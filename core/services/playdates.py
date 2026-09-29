"""Opt-in, one-hour demo playdates on weekdays during school hours."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone


EDITABLE_STATUSES = frozenset({
    "New", "In Review", "Playdate Scheduled",
    "Contacted", "Needs Follow Up", "Tour Scheduled", "Tour Completed",
})


def get_playdate_config(raw):
    portal = (raw or {}).get("family_portal", {})
    config = portal.get("playdate", {}) if isinstance(portal, dict) else {}
    return config if isinstance(config, dict) and config.get("enabled") is True else None


def _slot_start(value, config):
    start = datetime.fromisoformat(value)
    if timezone.is_naive(start):
        raise ValueError("Playdate slots need an explicit timezone")
    return start.astimezone(ZoneInfo(config.get("timezone", "America/Los_Angeles")))


def slot_label(value, config):
    try:
        start = _slot_start(value, config)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return str(value)
    end = start + timedelta(hours=1)
    start_time = start.strftime("%I:%M").lstrip("0").removesuffix(":00")
    end_time = end.strftime("%I:%M %p").lstrip("0").replace(":00", "")
    if start.strftime("%p") != end.strftime("%p"):
        start_time += start.strftime(" %p")
    return f"{start:%a, %b} {start.day}, {start.year}: {start_time}-{end_time}"


def available_slots(config):
    slots = []
    for value in config.get("slots", []):
        try:
            start = _slot_start(value, config)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            continue
        end = start + timedelta(hours=1)
        if (start <= timezone.now() or start.weekday() >= 5
                or start.time() < time(8) or end.time() > time(17)
                or end.date() != start.date()):
            continue
        slots.append({"value": value, "label": slot_label(value, config)})
    return slots


def playdate_context(config, submission):
    value = (submission.data or {}).get("playdate_slot", "")
    return {
        "value": value,
        "label": slot_label(value, config) if value else "",
        "options": available_slots(config),
        "can_schedule": submission.status in EDITABLE_STATUSES,
        "status_message": {
            "New": "Your application has been received. Enrollment is not yet confirmed.",
            "In Review": "The school is reviewing your application and availability.",
            "Playdate Scheduled": "Your child's playdate is scheduled. Enrollment is not yet confirmed.",
            "Playdate Completed": "The school will follow up about availability, fees, and remaining paperwork.",
            "Fee Pending": "The school will contact you with payment instructions and any remaining paperwork.",
            "Waitlisted": "Your application is on the waitlist. The school will contact you when a place is available.",
            "Enrolled": "The school has confirmed your child's enrollment.",
        }.get(submission.status, ""),
    }


def save_playdate(*, request, school, submission_id, config, value):
    """Use the same validation and status rules for parent and admin bookings."""
    from django.db import transaction
    from core.admin.audit import log_admin_audit
    from core.models import Submission

    if value not in {slot["value"] for slot in available_slots(config)}:
        return "invalid"
    with transaction.atomic():
        submission = Submission.objects.select_for_update().get(pk=submission_id, school=school)
        if submission.status not in EDITABLE_STATUSES:
            return "locked"
        data = dict(submission.data or {})
        if data.get("playdate_slot") == value and submission.status == "Playdate Scheduled":
            return "unchanged"
        old_status = submission.status
        old_slot = data.get("playdate_slot", "")
        data["playdate_slot"] = value
        submission.data = data
        submission.status = "Playdate Scheduled"
        submission.schedule_change_requested = False
        submission.schedule_change_requested_at = timezone.now()
        submission.save(update_fields=["data", "status", "schedule_change_requested", "schedule_change_requested_at", "updated_at"])
        log_admin_audit(request=request, action="action", obj=submission, changes={}, extra={
            "name": "playdate_scheduled", "from": old_status, "to": submission.status,
            "old_slot": old_slot, "playdate_slot": value, "slot_label": slot_label(value, config),
        })
    return "saved"
