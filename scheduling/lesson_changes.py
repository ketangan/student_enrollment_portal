"""Staff lesson changes; preserves make-up entitlements without financial effects."""
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .lesson_change_forms import AttendanceForm, RescheduleForm
from .lesson_models import LessonOccurrence, LessonSeries
from .lessons import audit, digest, lesson_access, plan_dates, preview_revision

CHANGE_SALT = "scheduling.lesson-change.v1"
ATTENDANCE_SALT = "scheduling.lesson-attendance.v1"


def get_occurrence(profile, pk):
    return get_object_or_404(LessonOccurrence.objects.select_related("series__service", "series__student", "series__profile"),
                             pk=pk, series__profile=profile)


def change_payload(data):
    return {key: str(data.get(key, "")) for key in RescheduleForm.base_fields}


def plan_change(item, cleaned):
    from .credits import validate_makeup_change
    if item.state != "scheduled" or item.starts_at <= timezone.now():
        raise ValidationError("Only future scheduled lessons can be moved. Refresh the schedule.")
    source = item.series
    old_zone = ZoneInfo(source.snapshot["timezone"])
    new_zone = ZoneInfo(cleaned["location"].timezone_name)
    today = timezone.localdate(timezone=new_zone)
    if not today <= cleaned["first_date"] <= today + timedelta(days=180):
        raise ValidationError("Every new date must be in the future and within 180 days.")
    # Shift the selected scheduled dates, not canceled/closed dates or separate exceptions.
    delta = cleaned["first_date"] - item.starts_at.astimezone(old_zone).date()
    items = list(source.occurrences.filter(state="scheduled", starts_at__gte=item.starts_at).order_by("starts_at")) if cleaned["scope"] == "future" else [item]
    dates = [datetime.combine(old.starts_at.astimezone(old_zone).date() + delta, cleaned["start_time"]) for old in items]
    if dates[0].date() < today or dates[-1].date() > today + timedelta(days=180):
        raise ValidationError("Every new date must be in the future and within 180 days.")
    data = {**cleaned, "service": source.service, "student": source.student}
    rows = plan_dates(data, dates, duration_minutes=source.snapshot["duration_minutes"], exclude_ids=[old.pk for old in items])
    validate_makeup_change(item, rows)
    for old, row in zip(items, rows):
        row.update(original_id=old.pk, original_start=old.starts_at, version=old.version)
        if row["state"] == "skipped":
            row["state"] = "conflict"
    gap = timedelta(minutes=cleaned["teacher"].buffer_minutes)
    for previous, row in zip(rows, rows[1:]):
        if row["starts_at"] < previous["ends_at"] + gap:
            row.update(state="conflict", reason="Replacement lessons overlap or leave too little teacher gap.")
    if source.teacher_id == cleaned["teacher"].pk and source.location_id == cleaned["location"].pk and source.room_id == (cleaned["room"].pk if cleaned["room"] else None) and all(old.starts_at == row["starts_at"] for old, row in zip(items, rows)):
        raise ValidationError("Choose a different date, time, teacher or room.")
    revision = digest({"source": source.pk, "snapshot": source.snapshot, "plan": preview_revision(data, rows)})
    return rows, revision


def change_token(user, item, data, revision):
    return signing.dumps({"user": user.pk, "profile": item.series.profile_id, "occurrence": item.pk,
                          "key": str(uuid.uuid4()), "payload": digest(change_payload(data)), "revision": revision}, salt=CHANGE_SALT)


@transaction.atomic
def reschedule(user, slug, pk, data, token):
    from .credits import move_redemption
    profile, _ = lesson_access(user, slug, "sessions.edit", locked=True)
    item = get_occurrence(profile, pk)
    try:
        signed = signing.loads(token, salt=CHANGE_SALT, max_age=3600)
        key = uuid.UUID(signed["key"])
        expected_revision = signed["revision"]
        if (signed["user"], signed["profile"], signed["occurrence"], signed["payload"]) != (user.pk, profile.pk, pk, digest(change_payload(data))):
            raise ValueError
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        raise ValidationError("This change preview expired or changed. Preview again.")
    existing = LessonSeries.objects.filter(profile=profile, predecessor=item.series, submission_key=key, submission_digest=signed["payload"]).first()
    if existing:
        return existing
    form = RescheduleForm(data, occurrence=item)
    if not form.is_valid():
        raise ValidationError("The lesson setup changed. Review the fields and preview again.")
    cleaned = form.cleaned_data
    rows, revision = plan_change(item, cleaned)
    if any(row["state"] != "scheduled" for row in rows):
        raise ValidationError("A replacement date has a conflict. Nothing was moved.")
    if revision != expected_revision:
        raise ValidationError("The schedule or setup changed. Preview again before saving.")
    source = item.series
    teacher, location, room = (cleaned[key] for key in ["teacher", "location", "room"])
    zone = ZoneInfo(location.timezone_name)
    replacement = LessonSeries.objects.create(profile=profile, predecessor=source, service=source.service,
        student=source.student, teacher=teacher, location=location, room=room,
        first_date=rows[0]["starts_at"].astimezone(zone).date(), last_date=rows[-1]["starts_at"].astimezone(zone).date(),
        start_time=cleaned["start_time"], interval_weeks=source.interval_weeks,
        submission_key=key, submission_digest=signed["payload"],
        snapshot={**source.snapshot, "teacher": teacher.name, "location": location.name, "room": room.name if room else "",
                  "timezone": location.timezone_name, "buffer_minutes": teacher.buffer_minutes, "travel_minutes": teacher.travel_minutes})
    for row in rows:
        new = LessonOccurrence.objects.create(series=replacement, starts_at=row["starts_at"], ends_at=row["ends_at"], state="scheduled")
        LessonOccurrence.objects.filter(pk=row["original_id"]).update(state="rescheduled", replacement=new,
            reason=cleaned["reason"], version=row["version"] + 1)
        move_redemption(user, row["original_id"], new, cleaned["reason"])
    audit(user, profile, replacement, "add", {"action": "reschedule", "source_series": source.pk,
        "original_dates": [row["original_id"] for row in rows], "reason": cleaned["reason"]})
    from .appointments import save_change_notice
    save_change_notice(replacement, "Lesson rescheduled")
    return replacement


def attendance_token(user, item):
    return signing.dumps({"user": user.pk, "occurrence": item.pk, "version": item.version}, salt=ATTENDANCE_SALT)


@transaction.atomic
def record_attendance(user, slug, pk, data):
    profile, _ = lesson_access(user, slug, "sessions.edit", locked=True)
    item = get_occurrence(profile, pk)
    form = AttendanceForm(data)
    if not form.is_valid():
        raise ValidationError("Choose an attendance outcome and a short reason.")
    cleaned = form.cleaned_data
    try:
        signed = signing.loads(cleaned["revision"], salt=ATTENDANCE_SALT, max_age=3600)
        if signed["user"] != user.pk or signed["occurrence"] != item.pk:
            raise ValueError
        expected = signed["version"]
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        raise ValidationError("This attendance form expired or changed. Refresh and try again.")
    if item.state != "scheduled" or item.ends_at > timezone.now():
        raise ValidationError("Attendance can only be recorded after a scheduled lesson ends.")
    if (item.attendance, item.attendance_note) == (cleaned["attendance"], cleaned["reason"]):
        return item
    if item.version != expected:
        raise ValidationError("Attendance changed since you opened this form. Refresh before editing.")
    previous = {"attendance": item.attendance, "reason": item.attendance_note}
    item.attendance, item.attendance_note = cleaned["attendance"], cleaned["reason"]
    item.version += 1
    item.save(update_fields=["attendance", "attendance_note", "version"])
    audit(user, profile, item, "change", {"action": "attendance", "before": previous,
        "after": {"attendance": item.attendance, "reason": item.attendance_note}})
    return item
