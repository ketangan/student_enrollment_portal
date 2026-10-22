"""Tenant-locked lesson planning with persisted dates, not inferred paid entitlements."""
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from dateutil.rrule import WEEKLY, rrule
from dateutil.tz import datetime_ambiguous, datetime_exists
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import AdminAuditLog
from .access import require_access
from .editing import protect_edit
from .lesson_forms import SETUP_FORMS, SeriesForm
from .lesson_models import (LessonAvailability, LessonClosure, LessonHousehold, LessonOccurrence,
                            LessonSeries, LessonService, LessonStudent, LessonTeacher)
from .models import Location, ReservationOccurrence, Room, SchedulingProfile, Session, SchedulingMembership, VisitSlot

TOKEN_SALT = "scheduling.lesson-preview.v1"


def lesson_access(user, slug, permission="workspace.view", locked=False):
    profile, role = require_access(user, slug, permission, capability="lessons")
    if locked:
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        profile, role = require_access(user, slug, permission, capability="lessons")
    if not profile.lessons_enabled:
        raise Http404("Page not found")
    return profile, role


def lesson_records(profile, kind):
    models = {"services": (LessonService, "profile"), "teachers": (LessonTeacher, "profile"),
              "households": (LessonHousehold, "profile"), "students": (LessonStudent, "household__profile"),
              "availability": (LessonAvailability, "location__profile"), "closures": (LessonClosure, "location__profile")}
    if kind not in models:
        raise Http404("Page not found")
    model, path = models[kind]
    return model.objects.filter(**{path: profile})


def setup_form(profile, kind, data=None, pk=None):
    objects = lesson_records(profile, kind)
    instance = get_object_or_404(objects, pk=pk) if pk else objects.model()
    if hasattr(instance, "profile_id"):
        instance.profile = profile
    form = SETUP_FORMS[kind](data, instance=instance)
    for field, model, path in [("services", LessonService, "profile"), ("teacher", LessonTeacher, "profile"),
                                ("location", Location, "profile"), ("household", LessonHousehold, "profile"),
                                ("booking_host", SchedulingMembership, "profile")]:
        if field in form.fields:
            form.fields[field].queryset = model.objects.filter(**{path: profile})
    frozen = []
    if pk and kind == "teachers" and LessonSeries.objects.filter(teacher=instance).exists():
        frozen = ["buffer_minutes", "travel_minutes"]
    if pk and kind == "students" and LessonSeries.objects.filter(student=instance).exists():
        frozen = ["household"]
    if pk and kind == "availability" and LessonSeries.objects.filter(teacher=instance.teacher, location=instance.location).exists():
        frozen = list(form.fields)
    for field in frozen:
        form.fields[field].disabled = True
    return protect_edit(form, profile)


def audit(user, profile, instance, action, details):
    AdminAuditLog.objects.create(actor=user, action=action, model_label=instance._meta.label_lower,
        object_id=str(instance.pk), object_repr=str(instance.pk), changes=details,
        extra={"scheduling_profile_id": profile.pk})


@transaction.atomic
def save_lesson_setup(user, slug, kind, data, pk=None):
    profile, _ = lesson_access(user, slug, "setup.edit", locked=True)
    form = setup_form(profile, kind, data, pk)
    if form.is_valid():
        try:
            form.instance.full_clean()
            if kind == "closures":
                closure = form.instance
                zone = ZoneInfo(closure.location.timezone_name)
                start = local_datetime(closure.first_date, datetime.min.time(), zone)
                end = local_datetime(closure.last_date + timedelta(days=1), datetime.min.time(), zone)
                affected = LessonOccurrence.objects.filter(state="scheduled", series__location=closure.location,
                    starts_at__lt=end, ends_at__gt=start)
                if closure.teacher_id:
                    affected = affected.filter(series__teacher=closure.teacher)
                if affected.exists():
                    raise ValidationError("Scheduled lessons fall within this closure. Cancel or move them explicitly first.")
                from .visit_resources import apply_visit_closure
                apply_visit_closure(closure, start, end)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            instance = form.save()
            audit(user, profile, instance, "change" if pk else "add", {"fields": sorted(form.changed_data)})
    return form


def local_datetime(day, clock, zone):
    value = datetime.combine(day, clock, tzinfo=zone)
    if not datetime_exists(value) or datetime_ambiguous(value):
        raise ValidationError(f"{day} at {clock:%H:%M} is ambiguous or does not exist because of a clock change. Choose another time.")
    return value.astimezone(dt_timezone.utc)


def scheduled_lessons():
    return LessonOccurrence.objects.filter(state="scheduled")


def validate_lesson_room(room_id, start, end):
    if room_id and scheduled_lessons().filter(series__room_id=room_id, starts_at__lt=end, ends_at__gt=start).exists():
        raise ValidationError("This room has a scheduled lesson at that time.")


class SlotCalendar:
    """Bounded conflict snapshot shared by staff checks and a day's public slots."""
    def __init__(self, data, start, end, exclude_ids=()):
        from .reservations import active_claims, studio_claims
        teacher, student, room = (data[k] for k in ["teacher", "student", "room"])
        self.windows = list(teacher.availability.filter(location=data["location"]))
        self.closures = list(LessonClosure.objects.filter(location=data["location"]).filter(Q(teacher=teacher) | Q(teacher=None)))
        # Include historical buffer snapshots, not only the teacher's current value.
        gap = timedelta(minutes=240)
        relevant = Q(series__teacher=teacher)
        if student is not None:
            relevant |= Q(series__student=student)
        if room:
            relevant |= Q(series__room=room)
        self.lessons = list(scheduled_lessons().filter(relevant, series__profile=teacher.profile,
            starts_at__lt=end+gap, ends_at__gt=start-gap).exclude(pk__in=exclude_ids).select_related("series"))
        self.sessions, self.claims = [], []
        studio_windows = list(studio_claims(data["location"].pk, start, end).values_list("starts_at", "ends_at"))
        self.visits = list(VisitSlot.objects.filter(host_id=teacher.booking_host_id, session__is_published=True,
            session__starts_at__lt=end+gap, session__ends_at__gt=start-gap).select_related("session", "program__offering")) if teacher.booking_host_id else []
        if room:
            filters = dict(room=room, starts_at__lt=end, ends_at__gt=start)
            self.sessions = list(Session.objects.filter(is_published=True, **filters).values_list("starts_at", "ends_at"))
            claims = active_claims()
            self.claims = list(claims.filter(**filters).values_list("starts_at", "ends_at"))
            self.claims += list(ReservationOccurrence.objects.filter(reservation__in=claims, **filters).values_list("starts_at", "ends_at"))
        self.claims += studio_windows

    def conflict(self, data, start, end):
        teacher, student, room, location = (data[k] for k in ["teacher", "student", "room", "location"])
        for visit in self.visits:
            gap = teacher.buffer_minutes
            if visit.program.offering.location_id != location.pk:
                gap = max(gap, teacher.travel_minutes)
            if visit.session.starts_at < end+timedelta(minutes=gap) and visit.session.ends_at+timedelta(minutes=gap) > start:
                return "Teacher is hosting a visit, including the required gap."
        for item in self.lessons:
            previous = item.series
            if previous.teacher_id == teacher.pk:
                minutes = max(teacher.buffer_minutes, previous.snapshot["buffer_minutes"])
                if previous.location_id != location.pk:
                    minutes = max(minutes, teacher.travel_minutes, previous.snapshot["travel_minutes"])
                if item.starts_at < end+timedelta(minutes=minutes) and item.ends_at+timedelta(minutes=minutes) > start:
                    return "Teacher already scheduled, including the required gap."
            if item.starts_at < end and item.ends_at > start:
                if student is not None and previous.student_id == student.pk:
                    return "Student already has a lesson at this time."
                if room and previous.room_id == room.pk:
                    return "Room already has a lesson."
        if any(a < end and b > start for a, b in self.sessions):
            return "Room already has a published workshop or camp session."
        if any(a < end and b > start for a, b in self.claims):
            return "Room already has a reservation."
        return ""


def slot_conflict(data, start, end, exclude_ids=()):
    return SlotCalendar(data, start, end, exclude_ids).conflict(data, start, end)


def plan_series(data):
    dates = rrule(WEEKLY, interval=data["interval_weeks"], dtstart=datetime.combine(data["first_date"], data["start_time"]),
                  until=datetime.combine(data["last_date"], datetime.max.time()))
    return plan_dates(data, dates)


def plan_dates(data, dates, duration_minutes=None, exclude_ids=(), calendar=None):
    service, teacher, location = (data[k] for k in ["service", "teacher", "location"])
    zone = ZoneInfo(location.timezone_name)
    windows = calendar.windows if calendar is not None else list(teacher.availability.filter(location=location))
    closures = calendar.closures if calendar is not None else list(LessonClosure.objects.filter(location=location).filter(Q(teacher=teacher) | Q(teacher=None)))
    rows = []
    duration = service.duration_minutes if duration_minutes is None else duration_minutes
    # Interpret local wall-clock dates explicitly; reject DST gaps/folds and changed durations.
    for wall in dates:
        start = local_datetime(wall.date(), wall.time(), zone)
        end_wall = wall + timedelta(minutes=duration)
        end = local_datetime(end_wall.date(), end_wall.time(), zone)
        if start <= timezone.now() or end - start != timedelta(minutes=duration):
            raise ValidationError("Lessons must start in the future and cannot cross a clock change.")
        closure = next((c for c in closures if c.first_date <= wall.date() <= c.last_date), None)
        state, reason = "scheduled", ""
        if closure:
            state, reason = "skipped", closure.reason
        elif end_wall.date() != wall.date() or not any(w.weekday == wall.weekday() and w.start_time <= wall.time() and w.end_time >= end_wall.time() for w in windows):
            state, reason = "conflict", "Outside teacher availability at this location."
        else:
            reason = calendar.conflict(data, start, end) if calendar is not None else slot_conflict(data, start, end, exclude_ids)
            if reason:
                state = "conflict"
        rows.append({"starts_at": start, "ends_at": end, "state": state, "reason": reason})
    return rows


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def payload(data):
    return {name: str(data.get(name, "")) for name in SeriesForm.base_fields}


def preview_revision(data, rows):
    return digest({"rows": rows, "service": str(data["service"]), "subject": data["service"].subject,
        "teacher": data["teacher"].name, "student": str(data["student"]), "location": data["location"].name,
        "timezone": data["location"].timezone_name, "room": str(data["room"]),
        "buffer": data["teacher"].buffer_minutes, "travel": data["teacher"].travel_minutes})


def preview_token(user, profile, data, cleaned, rows):
    return signing.dumps({"user": user.pk, "profile": profile.pk, "key": str(uuid.uuid4()),
        "payload": digest(payload(data)), "revision": preview_revision(cleaned, rows)}, salt=TOKEN_SALT)


@transaction.atomic
def create_series(user, slug, data, token):
    profile, _ = lesson_access(user, slug, "sessions.edit", locked=True)
    try:
        signed = signing.loads(token, salt=TOKEN_SALT, max_age=3600)
        key = uuid.UUID(signed["key"])
        if signed["user"] != user.pk or signed["profile"] != profile.pk or signed["payload"] != digest(payload(data)):
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError("This preview expired or changed. Preview the schedule again.")
    existing = LessonSeries.objects.filter(profile=profile, submission_key=key, submission_digest=signed["payload"]).first()
    if existing:
        return existing
    form = SeriesForm(data, profile=profile)
    if not form.is_valid():
        raise ValidationError("The lesson setup changed. Review the fields and preview again.")
    cleaned = form.cleaned_data
    rows = plan_series(cleaned)
    if any(row["state"] == "conflict" for row in rows) or not any(row["state"] == "scheduled" for row in rows):
        raise ValidationError("The schedule has conflicts or no available dates. Nothing was booked.")
    if signed["revision"] != preview_revision(cleaned, rows):
        raise ValidationError("Availability or setup changed. Preview again before saving.")
    service, teacher, student, location, room = (cleaned[k] for k in ["service", "teacher", "student", "location", "room"])
    series = LessonSeries.objects.create(profile=profile, **cleaned, submission_key=key, submission_digest=signed["payload"],
        snapshot={"service": service.name, "subject": service.subject, "duration_minutes": service.duration_minutes,
                  "teacher": teacher.name, "student": student.name, "household": student.household.name,
                  "location": location.name, "timezone": location.timezone_name, "room": room.name if room else "",
                  "buffer_minutes": teacher.buffer_minutes, "travel_minutes": teacher.travel_minutes})
    LessonOccurrence.objects.bulk_create([LessonOccurrence(series=series, **row) for row in rows])
    audit(user, profile, series, "add", {"dates": len(rows), "scheduled": sum(r["state"] == "scheduled" for r in rows)})
    return series


@transaction.atomic
def cancel_lessons(user, slug, pk, scope, reason, origin="family", token=None):
    from .credits import apply_cancellation, cancellation_plan, policy_for, validate_cancellation
    profile, _ = lesson_access(user, slug, "sessions.edit", locked=True)
    occurrence = get_object_or_404(LessonOccurrence, pk=pk, series__profile=profile)
    if scope not in {"one", "future"} or not reason.strip() or len(reason) > 200:
        raise ValidationError("Choose a cancellation scope and a short reason.")
    if occurrence.starts_at <= timezone.now():
        raise ValidationError("Past lessons cannot be canceled here.")
    if occurrence.state == "canceled":
        return occurrence.series
    if occurrence.state != "scheduled":
        raise ValidationError("This lesson is no longer scheduled. Refresh before canceling.")
    rows, revision = cancellation_plan(occurrence, scope, origin)
    policy = policy_for(profile)
    if token is not None or policy.enabled or any(row["credit"] for row in rows):
        validate_cancellation(user, occurrence, {"scope": scope, "origin": origin, "reason": reason}, revision, token)
    for row in rows:
        apply_cancellation(user, row, origin, reason.strip(), policy)
    count = LessonOccurrence.objects.filter(pk__in=[row["item"].pk for row in rows]).update(
        state="canceled", reason=reason.strip(), version=F("version") + 1)
    audit(user, profile, occurrence.series, "change", {"action": "cancel", "scope": scope, "from_occurrence": pk, "count": count, "reason": reason.strip()})
    from .appointments import save_change_notice
    save_change_notice(occurrence.series, "Lesson canceled")
    return occurrence.series
