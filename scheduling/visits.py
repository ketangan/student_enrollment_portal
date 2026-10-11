"""Native visits, serialized with the same tenant lock as all other room inventory."""

import uuid
from datetime import UTC
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import IntegerField, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.formats import date_format

from core.models import AdminAuditLog, Lead
from core.payments import service as payments
from core.services.lead_intake import create_or_update_lead
from core.services.school_permissions import get_school_membership
from .access import require_access
from .capabilities import require_capability
from .models import Reservation, SchedulingProfile, VisitBooking, VisitProgram, VisitSlot
from .requests import submission_digest
from .reservations import active_claims, ensure_capacity, session_places_taken
from .setup import _audit
from .visit_forms import BookingForm, ProgramForm, SlotForm

SALT = "scheduling.visit.v1"


def access(user, slug, permission="workspace.view"):
    return require_access(user, slug, permission, capability="visits")


def enrollment_access(user, profile, owner=False):
    if not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    school = profile.school
    if not user.is_staff or not school.is_active or (not school.is_demo and school.is_trial_expired):
        return False
    membership = get_school_membership(user, school)
    return membership is not None and (not owner or membership.role == "owner")


def programs(profile):
    return VisitProgram.objects.filter(offering__location__profile=profile).select_related("offering__location")


def slots(profile):
    return VisitSlot.objects.filter(program__offering__location__profile=profile).select_related(
        "program__offering__location", "session__room", "host__user")


def occupancy(queryset):
    claims = active_claims().filter(session_id=OuterRef("session_id")).order_by().values("session_id").annotate(total=Sum("quantity"))
    return queryset.annotate(places_taken=Coalesce(Subquery(claims.values("total"), output_field=IntegerField()), 0))


def bookings(profile):
    return VisitBooking.objects.filter(slot__program__offering__location__profile=profile).select_related(
        "slot__session__room", "slot__program__offering__location", "reservation", "lead")


def public_profile(slug):
    profile = get_object_or_404(SchedulingProfile.objects.select_related("school"), school__slug=slug, enabled=True)
    require_capability(profile, "visits")
    return profile


def available(slot):
    return (slot.session.is_published and slot.program.offering.is_published
        and slot.session.starts_at > timezone.now() and slot.host.is_active and slot.host.user.is_active
        and slot.host.role in {"owner", "scheduler"})


def places_left(slot):
    return max(0, slot.session.capacity - session_places_taken(slot.session))


def program_form(profile, data=None, pk=None):
    return ProgramForm(data, profile=profile, program=get_object_or_404(programs(profile), pk=pk) if pk else None)


def slot_form(profile, program_pk, data=None, pk=None):
    program = get_object_or_404(programs(profile), pk=program_pk)
    slot = get_object_or_404(slots(profile), pk=pk, program=program) if pk else None
    with timezone.override(ZoneInfo(program.offering.location.timezone_name)):
        return SlotForm(data, profile=profile, program=program, slot=slot)


@transaction.atomic
def save_program(user, slug, data, pk=None):
    profile, _ = access(user, slug, "setup.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = access(user, slug, "setup.edit")
    form = program_form(profile, data, pk)
    before = model_to_dict(form.instance) if pk else {}
    if form.is_valid():
        if (form.cleaned_data["capture_leads"] or form.cleaned_data["kind"] == "playdate" or (form.program and form.program.capture_leads)) and not enrollment_access(user, profile, owner=True):
            form.add_error("capture_leads", "Only an enrollment owner can enable this connection.")
        else:
            offering = form.save()
            program = form.program or VisitProgram(offering=offering)
            program.kind = form.cleaned_data["kind"]
            prior = program.capture_leads
            program.capture_leads = form.cleaned_data["capture_leads"]
            program.save()
            _audit(user, profile, offering, before, "change" if pk else "add")
            _audit(user, profile, program, {"capture_leads": prior} if pk else {}, "change" if pk else "add")
            form.program = program
    return form


@transaction.atomic
def save_slot(user, slug, program_pk, data, pk=None):
    profile, _ = access(user, slug, "sessions.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = access(user, slug, "sessions.edit")
    form = slot_form(profile, program_pk, data, pk)
    before = model_to_dict(form.instance) if pk else {}
    with timezone.override(ZoneInfo(form.instance.offering.location.timezone_name)):
        if form.is_valid():
            session = form.instance
            session.is_published = form.cleaned_data["is_published"]
            host = form.cleaned_data["host"]
            try:
                session.full_clean()
                if session.is_published:
                    from .visit_resources import validate_host
                    validate_host(profile, host, form.instance.offering.location, session.starts_at, session.ends_at, exclude=pk)
                if form.slot and form.slot.bookings.exists() and (host.pk != form.slot.host_id or any(
                        before[field] != getattr(session, field) for field in ("starts_at", "ends_at"))
                        or before["room"] != session.room_id):
                    raise ValidationError("This tour has booking history. Keep its time, room and host; create a new time instead.")
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                session.save()
                slot = form.slot or VisitSlot(program=get_object_or_404(programs(profile), pk=program_pk), session=session)
                host_before = slot.host_id
                slot.host = host
                slot.save()
                _audit(user, profile, session, before, "change" if pk else "add")
                _audit(user, profile, slot, {"host": host_before} if pk else {}, "change" if pk else "add")
    return form


def snapshot(slot):
    session, offering = slot.session, slot.program.offering
    return {"start": session.starts_at.astimezone(UTC).isoformat(), "end": session.ends_at.astimezone(UTC).isoformat(),
        "room": session.room_id, "host": slot.host_id, "capacity": session.capacity,
        "name": offering.name, "location": offering.location.name,
        "address": offering.location.address, "capture_leads": slot.program.capture_leads}


def booking_token(slot, browser, *, case=None):
    return signing.dumps({"key": str(uuid.uuid4()), "slot": slot.pk, "browser": browser, "snapshot": snapshot(slot),
        "case": case.pk if case else None}, salt=SALT)


def _lead_for(item, profile):
    # Email alone cannot identify a child. Follow enrollment's multi-child intake policy;
    # the signed booking key, not an email match, prevents duplicate retry records.
    local_start = timezone.localtime(item.slot.session.starts_at, ZoneInfo(item.slot.program.offering.location.timezone_name))
    lead, _ = create_or_update_lead(school=profile.school, name=item.contact_name, email=item.contact_email,
        source="website", data={"student_name": item.child_name, "scheduled_visit": str(item.public_id),
        "form_fields": {"student_name": item.child_name, "guardian_name": item.contact_name,
            "tour_slot": date_format(local_start, "D, M j, Y, g:i A")}})
    lead.status = "trial_scheduled"
    lead.save(update_fields=["status", "updated_at"])
    _audit(None, profile, lead, {}, "add")
    return lead


@transaction.atomic
def book(slug, pk, data, browser, *, case=None):
    profile = public_profile(slug)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile = public_profile(slug)
    slot = get_object_or_404(slots(profile), pk=pk)
    if (slot.program.kind == "playdate") != bool(case):
        from django.http import Http404
        raise Http404("Use the application's playdate booking link.")
    try:
        token = signing.loads(data.get("booking_token", ""), salt=SALT, max_age=7200)
        key = uuid.UUID(token["key"])
        if token["slot"] != pk or not browser or not constant_time_compare(token["browser"], browser):
            raise ValueError
        if token.get("case") != (case.pk if case else None):
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError("This booking form has expired. Open the tour again before booking.")
    digest = submission_digest(data)
    existing = bookings(profile).filter(submission_key=key).first()
    if existing:
        if existing.submission_digest != digest or existing.browser_digest != browser:
            raise ValidationError("This form was already used with different details. Open a new booking form.")
        return existing, None
    if case:
        from . import admissions
        case = admissions.require_playdate(case, profile)
    form = BookingForm(data, identity=admissions.playdate_identity(case) if case else None)
    if not form.is_valid():
        return None, form
    if not available(slot) or token.get("snapshot") != snapshot(slot):
        raise ValidationError("This tour changed or is no longer available. Choose a time again.")
    details = {key: value for key, value in form.cleaned_data.items() if key != "authority"}
    details["contact_email"] = details["contact_email"].lower()
    if not case and bookings(profile).filter(slot=slot, contact_email=details["contact_email"]).exclude(state="canceled").exists():
        raise ValidationError("A booking for this email already exists at this time. Contact the school to change it.")
    session = slot.session
    from .visit_resources import validate_host
    validate_host(profile, slot.host, slot.program.offering.location, session.starts_at, session.ends_at, exclude=slot.pk)
    reservation = Reservation(profile=profile, session=session, room=session.room, starts_at=session.starts_at,
        ends_at=session.ends_at, quantity=details["adults"] + details["children"], state="confirmed",
        hold_until=session.starts_at, snapshot={"name": details["contact_name"], "workshop": slot.program.offering.name,
            "timezone": slot.program.offering.location.timezone_name})
    ensure_capacity(reservation)
    item = VisitBooking.objects.create(slot=slot, **details, submission_key=key, submission_digest=digest, browser_digest=browser,
        admission_case=case, prior_submission_status=case.submission.status if case else "")
    reservation.visit_booking = item
    reservation.order = payments.create_order(school=profile.school, purpose="booking", source_key=f"visit:{item.public_id}",
        total_cents=0, deposit_cents=0, description=slot.program.offering.name, account=None)
    reservation.full_clean()
    reservation.save()
    if case:
        reservation.snapshot["admission_statuses"] = {"scheduled": case.policy.scheduled_status, "completed": case.policy.completed_status}
        reservation.save(update_fields=["snapshot"])
        before_status = {"status": case.submission.status}
        case.submission.status = case.policy.scheduled_status
        case.submission.save(update_fields=["status"])
        _audit(None, profile, case.submission, before_status, "change")
    if slot.program.capture_leads and not case:
        item.lead = _lead_for(item, profile)
        item.save(update_fields=["lead"])
    _audit(None, profile, item, {}, "add")
    from .visit_access import notice
    notice(item, "Visit confirmed")
    return item, form


@transaction.atomic
def change_state(user, slug, public_id, state, previous, version=None):
    profile, _ = access(user, slug, "sessions.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = access(user, slug, "sessions.edit")
    item = get_object_or_404(bookings(profile), public_id=public_id)
    if version is not None and str(item.version) != str(version):
        raise ValidationError("This booking changed. Reload before updating it.")
    if state not in {"attended", "no_show", "canceled"}:
        raise ValidationError("Choose an attendance or cancellation action.")
    if item.state == state:
        return item
    if item.state != previous or item.state != "booked":
        raise ValidationError("This booking changed. Reload before updating it.")
    if state != "canceled" and item.slot.session.starts_at > timezone.now():
        raise ValidationError("Record attendance once the tour has started.")
    before = model_to_dict(item)
    item.state = state
    item.version += 1
    item.save(update_fields=["state", "version"])
    if state == "canceled":
        Reservation.objects.filter(visit_booking=item).update(state="canceled")
    if item.admission_case_id:
        from .admissions import playdate_state
        playdate_state(item, user)
    elif item.lead_id and item.slot.program.capture_leads:
        lead = Lead.objects.select_for_update().get(pk=item.lead_id)
        if (not lead.converted_submission_id and lead.data.get("scheduled_visit") == str(item.public_id)
                and lead.status == "trial_scheduled"):
            lead.status = "trial_completed" if state == "attended" else "new"
            if state != "attended":
                lead.data = {**lead.data, "form_fields": {key: value for key, value in lead.data.get("form_fields", {}).items() if key != "tour_slot"}}
            lead.save(update_fields=["status", "data", "updated_at"])
            AdminAuditLog.objects.create(actor=user, action="change", model_label="core.lead", object_id=str(lead.pk),
                changes={"status": {"from": "trial_scheduled", "to": lead.status}},
                extra={"school_slug": slug, "visit_booking": str(item.public_id)})
    _audit(user, profile, item, before, "change")
    from .visit_access import notice
    notice(item, "Visit updated")
    return item
