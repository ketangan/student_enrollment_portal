"""Finite recurring workshop schedules using the single-session change service."""
from datetime import datetime, timedelta
import uuid
from zoneinfo import ZoneInfo

from dateutil.rrule import WEEKLY, rrule
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import AdminAuditLog, School
from . import session_changes, setup
from .access import require_location_access
from .capabilities import has_capability
from .lessons import digest, local_datetime
from .models import SchedulingProfile, Session, Room
from .staff_workshops import validate_new_session

SALT = "scheduling.workshop-schedule.v1"
WEEKDAYS = list(enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]))


def offering_for(user, slug, pk, action):
    profile, role = require_location_access(user, slug,
        "setup.edit" if action == "cancel" else "sessions.edit", capability="workshops")
    if (action not in {"create", "edit", "cancel"} or not profile.native_reservations_enabled
            or not has_capability(profile, "reservations")):
        from django.http import Http404
        raise Http404
    offering = get_object_or_404(setup.records(profile, "workshops"), pk=pk)
    return profile, role, offering


class ScheduleForm(forms.Form):
    first_date = forms.DateField(label="From", widget=forms.DateInput(attrs={"type": "date"}))
    last_date = forms.DateField(label="Through", widget=forms.DateInput(attrs={"type": "date"}))
    weekday = forms.TypedChoiceField(coerce=int, choices=WEEKDAYS, label="Day")
    current_time = forms.TimeField(label="Current start time", widget=forms.TimeInput(attrs={"type": "time"}))
    start_time = forms.TimeField(label="New start time", widget=forms.TimeInput(attrs={"type": "time"}))
    interval = forms.TypedChoiceField(coerce=int, choices=[(1, "Every week"), (2, "Every two weeks")], label="Repeat")
    duration_minutes = forms.IntegerField(min_value=1, max_value=1440, initial=120, label="Duration (min)")
    room = forms.ModelChoiceField(queryset=Room.objects.none(), required=False, label="Room (optional)")
    capacity = forms.IntegerField(min_value=1, max_value=10000, label="Capacity")
    resolution = forms.ChoiceField(label="Payment handling", widget=forms.RadioSelect,
        initial="without_refund", choices=[("without_refund", "Cancel without refund"),
            ("with_refund", "Cancel and refund payments")])
    email_customers = forms.BooleanField(required=False, label="Email customers")
    customer_message = forms.CharField(required=False, max_length=2000, label="Customer message (optional)",
        widget=forms.Textarea(attrs={"rows": 2}))
    preview_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, offering, action, **kwargs):
        super().__init__(*args, **kwargs)
        self.offering, self.action = offering, action
        self.fields["room"].queryset = Room.objects.filter(location=offering.location)
        keep = {"first_date", "last_date", "weekday", "preview_token"}
        if action == "create":
            keep.update(["start_time", "interval", "duration_minutes", "room", "capacity"])
            self.fields["start_time"].label = "Start time"
        elif action == "edit":
            keep.update(["current_time", "start_time"])
        else:
            keep.update(["current_time", "resolution", "email_customers", "customer_message"])
        self.fields = {name: field for name, field in self.fields.items() if name in keep}

    def clean(self):
        values = super().clean()
        if self.errors:
            return values
        today = timezone.localdate(timezone=ZoneInfo(self.offering.location.timezone_name))
        if not today <= values["first_date"] <= values["last_date"] <= today + timedelta(days=180):
            raise ValidationError("Choose a date range within the next 180 days.")
        if self.action == "edit" and values["current_time"] == values["start_time"]:
            self.add_error("start_time", "Choose a different start time.")
        return values


def matching(offering, values):
    zone = ZoneInfo(offering.location.timezone_name)
    start = local_datetime(values["first_date"], datetime.min.time(), zone)
    end = local_datetime(values["last_date"] + timedelta(days=1), datetime.min.time(), zone)
    result = []
    for session in offering.sessions.filter(starts_at__gte=start, starts_at__lt=end,
            starts_at__gt=timezone.now(), canceled_at__isnull=True, camps__isnull=True).select_related("room"):
        local = timezone.localtime(session.starts_at, zone)
        if local.weekday() == values["weekday"] and local.time().replace(second=0, microsecond=0) == values["current_time"]:
            result.append(session)
    return result


def creation_plan(offering, values):
    zone = ZoneInfo(offering.location.timezone_name)
    rows = []
    for wall in rrule(WEEKLY, byweekday=values["weekday"], interval=values["interval"],
            dtstart=datetime.combine(values["first_date"], values["start_time"]),
            until=datetime.combine(values["last_date"], datetime.max.time())):
        start = local_datetime(wall.date(), wall.time(), zone)
        session = Session(offering=offering, starts_at=start,
            ends_at=start.astimezone(ZoneInfo("UTC")) + timedelta(minutes=values["duration_minutes"]),
            room=values["room"], capacity=values["capacity"], is_published=True)
        try:
            validate_new_session(session)
        except ValidationError as exc:
            raise ValidationError(f"{wall:%b %d, %Y}: {' '.join(exc.messages)}") from exc
        rows.append(session)
    if not rows:
        raise ValidationError("No sessions match these dates and day.")
    return rows


def form_values(values):
    return digest({key: str(getattr(value, "pk", value)) for key, value in values.items() if key != "preview_token"})


@transaction.atomic
def save(user, slug, offering_pk, action, data):
    profile, _, offering = offering_for(user, slug, offering_pk, action)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    School.objects.select_for_update().get(pk=profile.school_id)
    profile, _, offering = offering_for(user, slug, offering_pk, action)
    form = ScheduleForm(data, offering=offering, action=action)
    if not form.is_valid():
        return form, [], False
    values, saved = form.cleaned_data, None
    stamp = form_values(values)
    if values["preview_token"]:
        try:
            saved = signing.loads(values["preview_token"], salt=SALT, max_age=1800)
            key = uuid.UUID(saved["key"])
            if (saved["actor"], saved["offering"], saved["action"], saved["values"]) != (user.pk, offering.pk, action, stamp):
                raise ValueError
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            raise ValidationError("The preview expired or the fields changed. Review the schedule again.") from None
        if AdminAuditLog.objects.filter(model_label="scheduling.offering", object_id=str(offering.pk),
                extra__schedule_batch=str(key), extra__scheduling_profile_id=profile.pk).exists():
            return form, [], True
    else:
        key = uuid.uuid4()
    rows, commands = [], []
    zone = ZoneInfo(offering.location.timezone_name)
    with timezone.override(zone):
        if action == "create":
            sessions = creation_plan(offering, values)
            rows = [{"after": session, "count": 0, "rows": []} for session in sessions]
        else:
            sessions = matching(offering, values)
            if not sessions:
                raise ValidationError("No upcoming sessions match this day and start time.")
            if saved and [s.pk for s in sessions] != saved["sessions"]:
                raise ValidationError("The matching sessions changed. Review the schedule again.")
            for index, session in enumerate(sessions):
                if saved:
                    command = QueryDict(mutable=True)
                    command.update(saved["commands"][index])
                else:
                    child = session_changes.build_form(user, profile, session, action)
                    command = QueryDict(mutable=True)
                    command.update({field.name: field.value() or "" for field in child.hidden_fields()})
                    if action == "edit":
                        local = timezone.localtime(session.starts_at, zone)
                        start = local_datetime(local.date(), values["start_time"], zone)
                        command.update(starts_at=start.isoformat(), duration_minutes=child.initial["duration_minutes"],
                            room=str(session.room_id or ""), capacity=session.capacity)
                    else:
                        command.update(resolution=values["resolution"], reason="",
                            email_customers="on" if values["email_customers"] else "",
                            customer_message=values["customer_message"])
                try:
                    complete, child, preview = session_changes.save(user, slug, session.pk, action, command, preview_only=True)
                    if complete:
                        raise ValidationError("This session was already changed. Review the schedule again.")
                    if child.errors:
                        raise ValidationError([str(error) for errors in child.errors.values() for error in errors])
                except ValidationError as exc:
                    date = timezone.localtime(session.starts_at, zone).strftime("%b %d, %Y")
                    raise ValidationError(f"{date}: {' '.join(exc.messages)}") from exc
                command["review_token"] = child.data["review_token"]
                commands.append(command.dict())
                rows.append(preview)
        if saved:
            for index, session in enumerate(sessions):
                if action == "create":
                    session.save()
                    setup._audit(user, profile, session, {}, "add")
                else:
                    command = QueryDict(mutable=True)
                    command.update(commands[index])
                    complete, _, _ = session_changes.save(user, slug, session.pk, action, command)
                    if not complete:
                        raise ValidationError("The schedule changed. Review it again; nothing was saved.")
            AdminAuditLog.objects.create(actor=user, action="change", model_label="scheduling.offering",
                object_id=str(offering.pk), object_repr=str(offering), changes={"schedule_action": action},
                extra={"scheduling_profile_id": profile.pk, "schedule_batch": str(key), "values": stamp,
                    "sessions": [s.pk for s in sessions]})
            return form, rows, True
    form.data = form.data.copy()
    form.data["preview_token"] = signing.dumps({"actor": user.pk, "offering": offering.pk,
        "action": action, "key": str(key), "values": stamp, "commands": commands,
        "sessions": [s.pk for s in sessions]}, salt=SALT, compress=True)
    return form, rows, False
