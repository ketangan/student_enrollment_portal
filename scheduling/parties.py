"""Party intake and internal review using the shared reservation/payment services."""

import hashlib
import json
import uuid
from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from core.models import AdminAuditLog
from .access import require_location_access, staff_locations
from .capabilities import require_capability
from .models import Offering, PartyRequest, SchedulingProfile, Session
from .party_forms import PartyInquiryForm, PartyReviewForm
from .requests import submission_digest

TOKEN_SALT = "scheduling.party-inquiry.v1"
ACTIVE_STATUSES = [PartyRequest.Status.PENDING, PartyRequest.Status.REVIEWED]


def offering_for_inquiry(slug, pk):
    offering = get_object_or_404(Offering.objects.select_related("location__profile__school"),
        pk=pk, location__profile__school__slug=slug, location__profile__enabled=True)
    require_capability(offering.location.profile, "parties")
    return offering


def inquiry_digest(offering):
    from .requests import catalog_for
    catalog = [(g.pk, g.name, [(o.pk, o.label) for o in g.active_options]) for g in catalog_for(offering)] if offering.inventory_catalog_id else []
    packages = list(offering.party_packages.values("id", "is_published", "name", "participant_limit", "duration_minutes", "total", "deposit", "space",
        "room_id", "included_observers", "additional_participant_limit", "additional_participant_price", "tax_rate", "booking_mode"))
    return hashlib.sha256(json.dumps([offering.pk, offering.name, offering.location.name,
        offering.location.timezone_name, offering.is_published, offering.accepts_party_requests, catalog,
        packages, list(offering.party_packages.values("slots__id", "slots__starts_at", "slots__is_published"))], default=str).encode()).hexdigest()


def inquiry_token(offering, browser):
    return signing.dumps({"key": str(uuid.uuid4()), "offering": offering.pk, "browser": browser,
                          "configuration": inquiry_digest(offering)}, salt=TOKEN_SALT)


def audit_party(user, profile, item, changes):
    AdminAuditLog.objects.create(actor=user, action="add" if user is None else "change",
        model_label="scheduling.partyrequest", object_id=str(item.pk), object_repr=str(item.public_id),
        changes=changes, extra={"school_slug": profile.school.slug, "scheduling_profile_id": profile.pk})


@transaction.atomic
def submit_inquiry(slug, pk, data, browser):
    try:
        token = signing.loads(data.get("request_token", ""), salt=TOKEN_SALT, max_age=7200)
        key = uuid.UUID(str(token["key"]))
        if token["offering"] != pk or not browser or not constant_time_compare(token["browser"], browser):
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        raise ValidationError("This form has expired or belongs to another browser. Open a new inquiry before trying again.")
    offering = offering_for_inquiry(slug, pk)
    SchedulingProfile.objects.select_for_update().get(pk=offering.location.profile_id)
    offering = offering_for_inquiry(slug, pk)
    digest = submission_digest(data)
    existing = PartyRequest.objects.filter(submission_key=key, offering=offering, browser_digest=browser).first()
    if existing:
        if existing.submission_digest != digest:
            raise ValidationError("This inquiry was already submitted with different details. Start a new inquiry to make another request.")
        return existing, None
    if not offering.is_published or not offering.accepts_party_requests:
        raise ValidationError("This workshop is not accepting party inquiries.")
    if not constant_time_compare(token.get("configuration", ""), inquiry_digest(offering)):
        raise ValidationError("The workshop details changed. Open a new inquiry from All workshops.")
    with timezone.override(ZoneInfo(offering.location.timezone_name)):
        form = PartyInquiryForm(data, offering=offering)
        if not form.is_valid():
            return None, form
        item = PartyRequest.objects.create(offering=offering, **form.inquiry_values(),
            submission_key=key, submission_digest=digest, browser_digest=browser,
            offering_snapshot={"workshop": offering.name, "location": offering.location.name,
                               "timezone": offering.location.timezone_name})
    if item.package_id and item.package.booking_mode == "direct":
        from .reservations import booking_account, party_reservation_item, save_reservation
        profile = offering.location.profile
        require_capability(profile, "reservations")
        if not profile.native_reservations_enabled:
            raise ValidationError("Online party bookings are not available. Please contact the studio.")
        account = booking_account(profile, True)
        save_reservation(party_reservation_item(profile, item), item, "party",
            int(item.proposed_total * 100), int(item.proposed_deposit * 100), account, None)
        item.status = PartyRequest.Status.REVIEWED
        item.save(update_fields=["status"])
    audit_party(None, offering.location.profile, item, {"status": {"from": None, "to": item.status}})
    return item, form


def scoped_parties(profile):
    require_capability(profile, "parties")
    return PartyRequest.objects.filter(offering__location__in=staff_locations(profile)).select_related("offering__location", "room", "reservation")


@transaction.atomic
def review_inquiry(user, slug, public_id, data):
    profile, _ = require_location_access(user, slug, "sessions.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit")
    item = get_object_or_404(scoped_parties(profile), public_id=public_id)
    original = {field: getattr(item, field) for field in ("status", "room_id", "proposed_total", "proposed_deposit", "preparation_notes", "materials", "ends_at")}
    form = PartyReviewForm(data, instance=item)
    if form.is_valid():
        if hasattr(item, "reservation"):
            form.add_error(None, "This inquiry has a reservation. Its original proposal is locked; use the reservation page.")
        elif form.cleaned_data["revision"] != item.revision:
            form.add_error(None, "Someone changed this inquiry. Open the saved review before making further changes.")
        else:
            # Model validation is repeated with server-owned fields included.
            item.full_clean()
            changed = [field for field, before in original.items() if getattr(item, field) != before]
            if changed:
                item.revision += 1
                item.save(update_fields=[*original, "revision", "updated_at"])
                audit_party(user, profile, item, {"revision": {"from": item.revision - 1, "to": item.revision},
                    "status": {"from": original["status"], "to": item.status}, "fields_updated": changed})
    return form


def room_overlaps(item):
    """Warnings are informational; inquiries never claim a room or confirmed seats."""
    if not item.room_id or not item.ends_at:
        return [], []
    sessions = list(Session.objects.filter(room=item.room, is_published=True,
        starts_at__lt=item.ends_at, ends_at__gt=item.starts_at).select_related("offering"))
    parties = list(PartyRequest.objects.filter(offering__location=item.offering.location,
        status__in=ACTIVE_STATUSES, starts_at__lt=item.ends_at, ends_at__gt=item.starts_at
    ).filter(Q(room=item.room) | Q(room__isnull=True, space=PartyRequest.Space.EXCLUSIVE)).exclude(pk=item.pk))
    return sessions, parties
