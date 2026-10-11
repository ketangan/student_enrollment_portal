"""Fixed-session registration. One tenant lock, one order, all dates or none."""
from datetime import timedelta
from decimal import Decimal
from collections import Counter
import hashlib
import json
import uuid

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare

from core.payments import service as payments
from core.models import AdminAuditLog
from .camp_forms import CampForm, validate_dates
from .editing import protect_edit
from .models import Camp, CampRegistration, Reservation, ReservationOccurrence, SchedulingProfile
from .request_forms import participant_forms
from .requests import catalog_digest, catalog_for, submission_digest
from .reservations import _audit, booking_account, ensure_capacity, locked_profile, session_places_taken
from .setup import records
from .capabilities import require_capability

TOKEN_SALT = "scheduling.camp-registration.v1"


def scoped_camps(profile):
    require_capability(profile, "camps")
    return Camp.objects.filter(offering__location__profile=profile).select_related("offering__location__profile__school")


def public_camp(slug, pk):
    camp = get_object_or_404(Camp.objects.select_related("offering__location__profile__school"), pk=pk,
        offering__location__profile__enabled=True, offering__location__profile__school__slug=slug)
    require_capability(camp.offering.location.profile, "camps")
    return camp


def camp_dates(camp):
    return list(camp.sessions.select_related("offering__location", "room"))


def check_registration(camp, sessions, catalog):
    require_capability(camp.offering.location.profile, "camps")
    if not camp.is_open or not camp.offering.location.profile.native_reservations_enabled:
        raise ValidationError("Camp registration is closed.")
    validate_dates(camp, sessions, opening=True)
    if camp.online and camp.price:
        from .checkout import CHECKOUT_LEAD_TIME
        if sessions[0].starts_at < timezone.now() + CHECKOUT_LEAD_TIME:
            raise ValidationError("Online camp registration closes 40 minutes before the first session.")
    if not all(g.active_options for g in catalog):
        raise ValidationError("Camp choices are unavailable. Please contact the studio.")
    booking_account(camp.offering.location.profile, camp.online)


def places_left(sessions):
    return max(0, min((s.capacity - session_places_taken(s) for s in sessions), default=0))


def preparation_totals(reservations):
    counts = Counter()
    for item in reservations:
        for participant in item.camp_registration.participants:
            for choice in participant["selections"]:
                counts[(choice["group_id"], choice["group"], choice["option_id"], choice["option"])] += 1
    return [{"group": group, "option": option, "count": count} for (_, group, _, option), count in sorted(counts.items())]


def camp_digest(camp, sessions, catalog):
    state = [camp.name, camp.description, format(Decimal(camp.price), ".2f"), format(Decimal(camp.deposit), ".2f"), camp.online,
             [(s.pk, catalog_digest(s, catalog)) for s in sessions]]
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()


def registration_token(camp, sessions, catalog, browser):
    return signing.dumps({"key": str(uuid.uuid4()), "camp": camp.pk, "browser": browser,
        "catalog": camp_digest(camp, sessions, catalog)}, salt=TOKEN_SALT)


def camp_form(profile, offering_pk, pk=None, data=None):
    require_capability(profile, "camps")
    offering = get_object_or_404(records(profile, "workshops"), pk=offering_pk)
    instance = get_object_or_404(scoped_camps(profile), pk=pk, offering=offering) if pk else Camp(offering=offering)
    return protect_edit(CampForm(data, instance=instance, offering=offering), profile)


@transaction.atomic
def save_camp(user, slug, offering_pk, data, pk=None):
    profile = locked_profile(user, slug)
    form = camp_form(profile, offering_pk, pk, data)
    if form.is_valid():
        try:
            form.instance.full_clean()
            if form.cleaned_data["is_open"]:
                check_registration(form.instance, list(form.cleaned_data["sessions"]), catalog_for(form.instance.offering))
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            camp = form.save()
            AdminAuditLog.objects.create(actor=user, action="change" if pk else "add", model_label="scheduling.camp",
                object_id=str(camp.pk), object_repr=camp.name,
                changes={"is_open": camp.is_open, "price": str(camp.price), "deposit": str(camp.deposit),
                         "online": camp.online, "sessions": list(camp.sessions.values_list("pk", flat=True))},
                extra={"scheduling_profile_id": profile.pk})
    return form


@transaction.atomic
def register_camp(slug, pk, data, browser):
    try:
        token = signing.loads(data.get("request_token", ""), salt=TOKEN_SALT, max_age=7200)
        key = uuid.UUID(str(token["key"]))
        if token["camp"] != pk or not browser or not constant_time_compare(token["browser"], browser):
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError("This form has expired or belongs to another browser. Reload before trying again.")
    camp = public_camp(slug, pk)
    SchedulingProfile.objects.select_for_update().get(pk=camp.offering.location.profile_id)
    camp = public_camp(slug, pk)
    digest = submission_digest(data)
    existing = CampRegistration.objects.filter(submission_key=key, camp=camp, browser_digest=browser).first()
    if existing:
        if existing.submission_digest != digest:
            raise ValidationError("This registration was already submitted with different details. Start a new registration.")
        return existing, None, None
    sessions, catalog = camp_dates(camp), catalog_for(camp.offering)
    check_registration(camp, sessions, catalog)
    if not constant_time_compare(token.get("catalog", ""), camp_digest(camp, sessions, catalog)):
        raise ValidationError("The camp dates, price or choices changed. Reload and review before registering.")
    purchaser, participants = participant_forms(min(s.capacity for s in sessions), catalog, data)
    valid_purchaser, valid_participants = purchaser.is_valid(), participants.is_valid()
    if not valid_purchaser or not valid_participants:
        return None, purchaser, participants
    from .inventory import check_selections, allocate
    check_selections(camp.offering, [{"selections": form.snapshot()} for form in participants])
    quantity = len(participants.forms)
    if places_left(sessions) < quantity:
        raise ValidationError("There are not enough places on every date for your whole group. Nothing was reserved or charged.")
    registration = CampRegistration.objects.create(camp=camp, **purchaser.cleaned_data,
        participants=[{"name": f.cleaned_data["name"], "selections": f.snapshot()} for f in participants],
        submission_key=key, submission_digest=digest, browser_digest=browser)
    profile = camp.offering.location.profile
    total, deposit = int(camp.price * 100) * quantity, int(camp.deposit * 100) * quantity
    order = payments.create_order(school=profile.school, purpose="booking", source_key=f"camp:{registration.public_id}",
        total_cents=total, deposit_cents=deposit, description=camp.name, account=booking_account(profile, camp.online))
    item = Reservation(profile=profile, camp_registration=registration, order=order,
        starts_at=sessions[0].starts_at, ends_at=sessions[-1].ends_at, quantity=quantity,
        snapshot={"name": registration.purchaser_name, "workshop": camp.name, "timezone": camp.offering.location.timezone_name,
                  "location": camp.offering.location.name, "unit_price": str(camp.price), "unit_deposit": str(camp.deposit)},
        hold_until=min(timezone.now() + timedelta(minutes=30), sessions[0].starts_at),
        state="confirmed" if total == 0 else "held")
    item.full_clean()
    item.save()
    allocate(item)
    # Allocate rounding cents deterministically. No occurrence is a separate charge.
    count = len(sessions)
    ReservationOccurrence.objects.bulk_create([ReservationOccurrence(reservation=item, session=session,
        starts_at=session.starts_at, ends_at=session.ends_at, room=session.room, room_name=session.room.name if session.room else "",
        total_cents=total // count + (index < total % count),
        deposit_cents=deposit // count + (index < deposit % count)) for index, session in enumerate(sessions)])
    ensure_capacity(item)
    _audit(None, item, "camp_registered", occurrences=count)
    return registration, purchaser, participants
