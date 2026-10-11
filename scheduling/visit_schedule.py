"""Finite, reviewed visit schedules. All writes use the shared inventory lock."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import WEEKLY, rrule
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from . import visits
from .lessons import digest, local_datetime
from .models import Room, SchedulingMembership, SchedulingProfile, Session, VisitSlot, VisitScheduleBatch
from .setup import _audit
from .visit_resources import closure_reason, validate_host

SALT = "scheduling.visit-schedule.v1"


class ScheduleForm(forms.Form):
    first_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    last_date = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    start_time = forms.TimeField(widget=forms.TimeInput(attrs={"type": "time"}))
    duration = forms.IntegerField(min_value=10, max_value=180, initial=30, label="Duration (minutes)")
    interval = forms.TypedChoiceField(coerce=int, choices=[(1, "Every week"), (2, "Every two weeks")], label="Repeats")
    room = forms.ModelChoiceField(queryset=Room.objects.none())
    host = forms.ModelChoiceField(queryset=SchedulingMembership.objects.none())
    capacity = forms.IntegerField(min_value=1, max_value=1000, initial=6, label="Total places (adults and children)")

    def __init__(self, *args, program, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = program
        self.fields["room"].queryset = Room.objects.filter(location=program.offering.location)
        self.fields["host"].queryset = SchedulingMembership.objects.filter(profile=program.offering.location.profile,
            is_active=True, user__is_active=True, role__in=["owner", "scheduler"]).select_related("user")
        self.fields["host"].label_from_instance = lambda member: member.user.get_full_name() or member.user.username

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        today = timezone.localdate(timezone=ZoneInfo(self.program.offering.location.timezone_name))
        if not today <= data["first_date"] <= data["last_date"] <= today + timedelta(days=180):
            raise ValidationError("Choose dates from today through the next 180 days, with the last date after the first.")
        if data["capacity"] > data["room"].capacity:
            self.add_error("capacity", "The number of places exceeds the room capacity.")
        return data


def plan(program, data):
    rows = []
    location = program.offering.location
    zone = ZoneInfo(location.timezone_name)
    for wall in rrule(WEEKLY, interval=data["interval"], dtstart=datetime.combine(data["first_date"], data["start_time"]),
            until=datetime.combine(data["last_date"], datetime.max.time())):
        end_wall = wall + timedelta(minutes=data["duration"])
        start, end = local_datetime(wall.date(), wall.time(), zone), local_datetime(end_wall.date(), end_wall.time(), zone)
        if start <= timezone.now() or end-start != timedelta(minutes=data["duration"]) or end_wall.date() != wall.date():
            raise ValidationError("Times must be in the future, on the same day, without crossing a clock change.")
        reason = closure_reason(location, data["host"], start, end)
        state = "Skipped" if reason else "Open"
        if not reason:
            try:
                candidate = Session(offering=program.offering, room=data["room"], capacity=data["capacity"],
                    starts_at=start, ends_at=end, is_published=True)
                candidate.full_clean()
                validate_host(location.profile, data["host"], location, start, end)
            except ValidationError as exc:
                state, reason = "Conflict", " ".join(exc.messages)
        rows.append({"start": start, "end": end, "state": state, "reason": reason})
    return rows


def values_digest(data):
    return digest({key: str(getattr(value, "pk", value)) for key, value in data.items()})


@transaction.atomic
def schedule(user, slug, program_pk, data, token=None):
    profile, _ = visits.access(user, slug, "sessions.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = visits.access(user, slug, "sessions.edit")
    program = get_object_or_404(visits.programs(profile), pk=program_pk)
    form = ScheduleForm(data, program=program)
    if not form.is_valid():
        return form, [], "", False
    values = form.cleaned_data
    if token:
        try:
            saved = signing.loads(token, salt=SALT, max_age=1800)
            key = uuid.UUID(saved["key"])
            if saved["actor"] != user.pk or saved["program"] != program.pk:
                raise ValueError
        except (signing.BadSignature, KeyError, ValueError, TypeError) as exc:
            raise ValidationError("This preview expired. Preview the times again.") from exc
        previous = VisitScheduleBatch.objects.filter(key=key, program=program).first()
        if previous:
            if previous.digest != values_digest(values):
                raise ValidationError("This preview was already used with different values.")
            return form, [], "", True
    rows = plan(program, values)
    fingerprint = digest({"program": program.pk, "values": values_digest(values), "rows": rows})
    ready = any(row["state"] == "Open" for row in rows) and not any(row["state"] == "Conflict" for row in rows)
    if token:
        if saved["digest"] != fingerprint or not ready:
            raise ValidationError("Availability changed. Preview again; no times were saved.")
        ids = []
        for row in rows:
            if row["state"] != "Open":
                continue
            session = Session.objects.create(offering=program.offering, room=values["room"], capacity=values["capacity"],
                starts_at=row["start"], ends_at=row["end"], is_published=True)
            slot = VisitSlot.objects.create(program=program, session=session, host=values["host"])
            ids.append(slot.pk)
            _audit(user, profile, slot, {}, "add")
        VisitScheduleBatch.objects.create(program=program, key=key, slot_ids=ids, digest=values_digest(values))
        return form, rows, "", True
    preview = signing.dumps({"key": str(uuid.uuid4()), "program": program.pk, "actor": user.pk, "digest": fingerprint}, salt=SALT) if ready else ""
    return form, rows, preview, False
