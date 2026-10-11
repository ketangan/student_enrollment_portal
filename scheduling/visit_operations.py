"""Atomic booking moves and audited corrections, preserving enrollment identity."""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.formats import date_format
from zoneinfo import ZoneInfo

from core.models import Lead, Submission
from . import visits
from .models import SchedulingProfile, VisitPilotSettings
from .reservations import ensure_capacity
from .setup import _audit


def family_may_change(item, profile):
    policy = VisitPilotSettings.objects.filter(profile=profile, family_changes=True).first()
    return bool(policy and item.state == "booked" and
        item.slot.session.starts_at > timezone.now()+timedelta(hours=policy.notice_hours))


def sync_identity(item, previous_state, user):
    if item.admission_case_id:
        case = item.admission_case
        sub = Submission.objects.select_for_update().get(pk=case.submission_id)
        statuses = item.reservation.snapshot["admission_statuses"]
        expected = statuses["completed"] if previous_state == "attended" else (
            statuses["scheduled"] if previous_state == "booked" else item.prior_submission_status)
        if sub.status == expected:
            before = {"status": sub.status}
            sub.status = statuses["completed"] if item.state == "attended" else (
                statuses["scheduled"] if item.state == "booked" else item.prior_submission_status)
            sub.save(update_fields=["status"])
            _audit(user, case.policy.profile, sub, before, "change")
    elif item.lead_id and item.slot.program.capture_leads:
        lead = Lead.objects.select_for_update().get(pk=item.lead_id)
        if lead.converted_submission_id or lead.data.get("scheduled_visit") != str(item.public_id):
            return
        expected = {"attended": "trial_completed", "booked": "trial_scheduled"}.get(previous_state, "new")
        if lead.status != expected:
            return
        before = {"status": lead.status, "data": lead.data}
        lead.status = {"attended": "trial_completed", "booked": "trial_scheduled"}.get(item.state, "new")
        fields = dict(lead.data.get("form_fields", {}))
        if item.state in {"booked", "attended"}:
            local = timezone.localtime(item.slot.session.starts_at, ZoneInfo(item.slot.program.offering.location.timezone_name))
            fields["tour_slot"] = date_format(local, "D, M j, Y, g:i A")
        else:
            fields.pop("tour_slot", None)
        lead.data = {**lead.data, "form_fields": fields}
        lead.save(update_fields=["status", "data", "updated_at"])
        _audit(user, item.slot.host.profile, lead, before, "change")


@transaction.atomic
def change(profile, public_id, *, version, action, target=None, reason="", user=None, family=False):
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile = visits.public_profile(profile.school.slug)
    if not family:
        visits.access(user, profile.school.slug, "sessions.edit")
    item = get_object_or_404(visits.bookings(profile), public_id=public_id)
    if str(item.version) != str(version):
        raise ValidationError("This booking changed. Reload before updating it.")
    if family and not family_may_change(item, profile):
        raise ValidationError("Please contact the school to change this booking.")
    before = {"state": item.state, "slot": item.slot_id, "version": item.version, "reason": reason}
    reservation = item.reservation
    if action == "move":
        if item.state != "booked" or item.slot.session.starts_at <= timezone.now():
            raise ValidationError("Only an upcoming scheduled visit can be moved.")
        try:
            target = int(target)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Choose an available time.") from exc
        slot = get_object_or_404(visits.slots(profile), pk=target, program=item.slot.program)
        if not visits.available(slot) or slot.pk == item.slot_id:
            raise ValidationError("Choose a different available time for this visit.")
        from .visit_resources import validate_host
        validate_host(profile, slot.host, slot.program.offering.location, slot.session.starts_at, slot.session.ends_at, exclude=slot.pk)
        reservation.session, reservation.room = slot.session, slot.session.room
        reservation.starts_at, reservation.ends_at = slot.session.starts_at, slot.session.ends_at
        reservation.hold_until = slot.session.starts_at
        ensure_capacity(reservation)
        item.slot = slot
    elif action == "cancel":
        if item.state != "booked":
            raise ValidationError("Only a scheduled visit can be canceled.")
        item.state, reservation.state = "canceled", "canceled"
    elif action in {"booked", "attended", "no_show"} and not family:
        if not reason.strip():
            raise ValidationError("Enter a reason for this correction.")
        if action != "booked" and item.slot.session.starts_at > timezone.now():
            raise ValidationError("Record attendance only after the visit starts.")
        from .models import AdmissionCase
        if item.state == "attended" and action != "attended" and AdmissionCase.objects.filter(tour=item, submission__isnull=False).exists():
            raise ValidationError("This tour already released a submitted application. Contact the owner to review its history.")
        if action in {"booked", "attended"} and item.admission_case_id and item.admission_case.playdates.exclude(pk=item.pk).filter(state__in=["booked", "attended"]).exists():
            raise ValidationError("This application already has another playdate.")
        if item.state == "canceled":
            if action != "booked":
                raise ValidationError("Reopen a canceled visit before recording attendance.")
            if not visits.available(item.slot):
                raise ValidationError("This time is no longer available. Make a new booking.")
            from .visit_resources import validate_host
            validate_host(profile, item.slot.host, item.slot.program.offering.location,
                item.slot.session.starts_at, item.slot.session.ends_at, exclude=item.slot_id)
            ensure_capacity(reservation)
        item.state, reservation.state = action, "confirmed"
    else:
        raise ValidationError("Choose a valid booking action.")
    item.version += 1
    item.save(update_fields=["slot", "state", "version"])
    reservation.save()
    sync_identity(item, before["state"], user)
    _audit(user, profile, item, before, "change")
    from .visit_access import notice
    notice(item, "Booking updated")
    return item
