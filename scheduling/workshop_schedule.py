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
    interval = forms.IntegerField(min_value=1, max_value=26, initial=1, label="Repeat every (weeks)")
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

    def __init__(self, *args, offering, action, seed=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.offering, self.action = offering, action
        self.seed = seed
        self.fields["room"].queryset = Room.objects.filter(location_id=offering.location_id)
        keep = {"first_date", "last_date", "weekday", "preview_token"}
        if action == "create":
            keep.update(["start_time", "interval", "duration_minutes", "room", "capacity"])
            self.fields["start_time"].label = "Start time"
        elif action == "edit":
            keep.update(["current_time", "start_time"])
        else:
            keep.update(["current_time", "resolution", "email_customers", "customer_message"])
        self.fields = {name: field for name, field in self.fields.items() if name in keep}
        if seed and action != "create":
            for name in ("first_date", "last_date", "weekday", "current_time"):
                self.fields.pop(name, None)
            if action == "edit":
                self.fields["scope"] = forms.ChoiceField(initial="following", choices=[
                    ("one", "Only this session"), ("following", "This and following sessions")], widget=forms.RadioSelect)
                self.fields["duration_minutes"] = forms.IntegerField(min_value=1, max_value=1440, label="Duration (min)")
                self.fields["capacity"] = forms.IntegerField(min_value=1, max_value=10000, label="Seats per session")
                self.fields["notify_customers"] = forms.ChoiceField(required=False, initial="1",
                    choices=[("1", "Notify customers"), ("0", "Do not notify customers")], widget=forms.HiddenInput)
                self.initial.update(scope="following", duration_minutes=round((seed.ends_at-seed.starts_at).total_seconds()/60), capacity=seed.capacity)

    def clean(self):
        values = super().clean()
        if self.errors:
            return values
        if self.seed and self.action != "create":
            return values
        today = timezone.localdate(timezone=ZoneInfo(self.offering.location.timezone_name))
        if not today <= values["first_date"] <= values["last_date"] <= today + timedelta(days=180):
            raise ValidationError("Choose a date range within the next 180 days.")
        if self.action == "edit" and values["current_time"] == values["start_time"]:
            self.add_error("start_time", "Choose a different start time.")
        return values


class WorkshopScheduleForm(ScheduleForm):
    """Optional initial schedule, validated by the existing recurrence form."""
    mode = forms.ChoiceField(required=False, initial="", label="Sessions",
        choices=[("", "Add times later"), ("once", "One session"), ("repeat", "Repeating sessions")])

    def __init__(self, *args, profile, **kwargs):
        from copy import deepcopy
        from .access import staff_locations
        from .models import Offering
        super().__init__(*args, offering=Offering(), action="create", **kwargs)
        self.fields["mode"] = deepcopy(self.base_fields["mode"])
        self.order_fields(["mode", "first_date", "last_date", "interval",
            "start_time", "duration_minutes", "room", "capacity"])
        self.fields.pop("preview_token")
        self.fields.pop("weekday")
        self.fields["first_date"].label = "First session date"
        self.fields["last_date"].label = "End date"
        self.fields["room"].queryset = Room.objects.filter(location__in=staff_locations(profile))
        for field in self.fields.values():
            field.required = False

    def clean(self):
        values = forms.Form.clean(self)
        if not values.get("mode"):
            return values
        required = ["first_date", "start_time", "duration_minutes", "capacity"]
        if values["mode"] == "repeat":
            required += ["last_date", "interval"]
        for name in required:
            if values.get(name) is None:
                self.add_error(name, "This field is required.")
        if self.errors:
            return values
        values["weekday"] = values["first_date"].weekday()
        if values["mode"] == "once":
            values.update(last_date=values["first_date"], weekday=values["first_date"].weekday(), interval=1)
        # Parent workshop validation can fail before a location is assigned.
        if not self.offering.location_id:
            return values
        if values.get("room") and values["room"].location_id != self.offering.location_id:
            self.add_error("room", "Choose a room at this workshop's location.")
            return values
        return super().clean()


class NewSessionForm(ScheduleForm):
    """A single/recurring editor over the existing schedule writer."""
    starts_at = forms.DateTimeField(label="Date and time",
        widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    mode = forms.ChoiceField(label="Repeats", initial="once",
        choices=[("once", "Does not repeat"), ("repeat", "Repeats weekly")])
    visibility = forms.ChoiceField(label="Visibility", initial="published", widget=forms.RadioSelect,
        choices=[("published", "Publish"), ("draft", "Save as draft")])
    creation_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        from copy import deepcopy
        super().__init__(*args, **kwargs)
        for name in ("mode", "visibility", "creation_token", "starts_at"):
            self.fields[name] = deepcopy(self.base_fields[name])
        self.fields.pop("weekday")
        self.fields.pop("first_date")
        self.fields.pop("start_time")
        if self.initial.get("first_date") and self.initial.get("start_time"):
            self.initial["starts_at"] = f'{self.initial["first_date"]}T{self.initial["start_time"]}'
        # Accept already-open forms using the previous date/time fields.
        if self.is_bound and "starts_at" not in self.data:
            self.data = self.data.copy()
            self.data["starts_at"] = f'{self.data.get("first_date", "")}T{self.data.get("start_time", "")}'
        self.fields["capacity"].label = "Seats"
        self.fields["last_date"].label = "Repeat through"
        self.fields["last_date"].required = False
        self.fields["interval"].required = False
        rooms = list(self.fields["room"].queryset[:2])
        if len(rooms) == 1:
            self.initial["room"] = rooms[0].pk
            self.fields["room"].disabled = True
            self.fields["room"].widget = forms.HiddenInput()
        self.order_fields(["starts_at", "duration_minutes", "capacity", "mode", "interval",
            "last_date", "room", "visibility", "preview_token"])

    def full_clean(self):
        with timezone.override(ZoneInfo(self.offering.location.timezone_name)):
            super().full_clean()

    def clean(self):
        values = forms.Form.clean(self)
        if values.get("starts_at"):
            local = timezone.localtime(values["starts_at"], ZoneInfo(self.offering.location.timezone_name))
            values.update(first_date=local.date(), start_time=local.time())
        if values.get("mode") == "once" and values.get("first_date"):
            values.update(last_date=values["first_date"], interval=1)
        elif values.get("mode") == "repeat":
            for name in ("last_date", "interval"):
                if not values.get(name):
                    self.add_error(name, "This field is required.")
        if values.get("first_date"):
            values["weekday"] = values["first_date"].weekday()
        return super().clean()


def matching(offering, values, seed=None):
    if seed:
        if values.get("scope") == "one":
            return [seed] if seed.starts_at > timezone.now() and not seed.canceled_at else []
        if not seed.series_id:
            raise ValidationError("This session is no longer part of a series. Reopen it before editing.")
        return list(offering.sessions.filter(series_id=seed.series_id,
            starts_at__gte=seed.starts_at, starts_at__gt=timezone.now(),
            canceled_at__isnull=True, camps__isnull=True).select_related("room"))
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
    series_id = uuid.uuid4()
    for wall in rrule(WEEKLY, byweekday=values["weekday"], interval=values["interval"],
            dtstart=datetime.combine(values["first_date"], values["start_time"]),
            until=datetime.combine(values["last_date"], datetime.max.time())):
        start = local_datetime(wall.date(), wall.time(), zone)
        session = Session(offering=offering, starts_at=start,
            ends_at=start.astimezone(ZoneInfo("UTC")) + timedelta(minutes=values["duration_minutes"]),
            room=values["room"], capacity=values["capacity"], is_published=True,
            series_id=series_id if values.get("mode") != "once" else None)
        try:
            validate_new_session(session)
        except ValidationError as exc:
            raise ValidationError(f"{wall:%b %d, %Y}: {' '.join(exc.messages)}") from exc
        session.is_published = values.get("visibility", "published") == "published"
        rows.append(session)
    if not rows:
        raise ValidationError("No sessions match these dates and day.")
    return rows


def form_values(values):
    return digest({key: str(getattr(value, "pk", value)) for key, value in values.items() if key not in {"preview_token", "creation_token"}})


def creation_token(user, offering):
    return signing.dumps({"actor": user.pk, "offering": offering.pk, "key": str(uuid.uuid4())}, salt=SALT + ".create")


@transaction.atomic
def save(user, slug, offering_pk, action, data, seed=None, *, session_editor=False, preview_only=False):
    profile, _, offering = offering_for(user, slug, offering_pk, action)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    School.objects.select_for_update().get(pk=profile.school_id)
    profile, _, offering = offering_for(user, slug, offering_pk, action)
    if seed:
        seed = get_object_or_404(offering.sessions, pk=seed.pk)
    form_class = NewSessionForm if session_editor and action == "create" else ScheduleForm
    form = form_class(data, offering=offering, action=action, seed=seed)
    if not form.is_valid():
        return form, [], False
    values, saved = form.cleaned_data, None
    stamp = form_values({**values, "series_seed": seed.pk if seed else None})
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
    direct_create = session_editor and action == "create" and values.get("creation_token")
    if direct_create:
        try:
            command = signing.loads(direct_create, salt=SALT + ".create", max_age=7200)
            key = uuid.UUID(command["key"])
            if (command["actor"], command["offering"]) != (user.pk, offering.pk):
                raise ValueError
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            raise ValidationError("Reopen the session form and try again.") from None
        previous = AdminAuditLog.objects.filter(model_label="scheduling.offering", object_id=str(offering.pk),
            extra__schedule_batch=str(key), extra__scheduling_profile_id=profile.pk).first()
        if previous:
            if previous.extra.get("values") != stamp:
                raise ValidationError("This session was already created. Reopen it to make changes.")
            return form, [], True
    rows, commands = [], []
    zone = ZoneInfo(offering.location.timezone_name)
    with timezone.override(zone):
        if action == "create":
            sessions = creation_plan(offering, values)
            rows = [{"after": session, "count": 0, "rows": []} for session in sessions]
        else:
            sessions = matching(offering, values, seed)
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
                        for name, value in {"starts_at": start.isoformat(), "duration_minutes": values.get("duration_minutes", child.initial["duration_minutes"]),
                                "room": str(session.room_id or ""), "capacity": values.get("capacity", session.capacity),
                                "notify_customers": values.get("notify_customers", "1")}.items():
                            command[name] = value
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
                    if seed and action == "edit":
                        items = session_changes.affected(profile, session)
                        rows.append({"before": session, "after": None, "count": len(items),
                            "rows": session_changes.preview_rows(items), "conflicts": exc.messages})
                        commands.append(None)
                        continue
                    date = timezone.localtime(session.starts_at, zone).strftime("%b %d, %Y")
                    raise ValidationError(f"{date}: {' '.join(exc.messages)}") from exc
                command["review_token"] = child.data["review_token"]
                commands.append(command.dict())
                rows.append(preview)
        if any(row.get("conflicts") for row in rows):
            form.data = form.data.copy()
            form.data["preview_token"] = ""
            return form, rows, False
        if (saved or direct_create) and not preview_only:
            for index, session in enumerate(sessions):
                if action == "create":
                    session.save()
                    setup._audit(user, profile, session, {}, "add")
                else:
                    command = QueryDict(mutable=True)
                    command.update(commands[index])
                    complete, _, _ = session_changes.save(user, slug, session.pk, action, command,
                        detach_from_series=bool(seed and values.get("scope") == "one"))
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
