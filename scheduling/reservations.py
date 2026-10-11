"""Native reservations. All inventory writers lock the tenant profile."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import AdminAuditLog
from core.payments import service as payments
from core.payments.models import PaymentAccount, PaymentAttempt
from .access import require_access
from .capabilities import require_capability
from .models import PartyRequest, Reservation, ReservationOccurrence, SchedulingProfile, Session, WorkshopRequest


def active_claims(now=None):
    pending_orders = PaymentAttempt.objects.filter(state__in=PaymentAttempt.ACTIVE).values("order_id")
    return Reservation.objects.filter(Q(state__in=["confirmed", "completed"]) | Q(state="held", hold_until__gt=now or timezone.now())
        | Q(order_id__in=pending_orders))


def peak_quantity(intervals):
    events = []
    for start, end, quantity in intervals:
        events.extend([(start, quantity), (end, -quantity)])
    current = peak = 0
    for _, delta in sorted(events):
        current += delta
        peak = max(peak, current)
    return peak


def validate_room_capacity(room):
    if not room.pk or room.capacity is None:
        return
    claims = active_claims().filter(room=room, party_request__isnull=False)
    if peak_quantity(claims.values_list("starts_at", "ends_at", "quantity")) > room.capacity:
        raise ValidationError({"capacity": "Capacity cannot be lower than the room's active reservations."})


def validate_session_edit(session):
    claims = active_claims().filter(Q(session=session.pk) | Q(occurrences__session=session.pk)).distinct()
    if session.pk and (claims.exists() or session.camp_occurrences.exists()):
        old = Session.objects.get(pk=session.pk)
        if (old.starts_at, old.ends_at, old.room_id) != (session.starts_at, session.ends_at, session.room_id):
            raise ValidationError({"starts_at": "This session has reservations. Camp dates stay fixed after registration; create a new session instead."})
        quantity = claims.aggregate(total=Sum("quantity"))["total"] or 0
        if session.capacity is not None and session.capacity < quantity:
            raise ValidationError({"capacity": "Capacity cannot be lower than active reserved places."})
        if not session.is_published and claims.filter(camp_registration__isnull=False).exists():
            raise ValidationError("Close camp registration instead. A reserved camp session must remain published.")
    if session.is_published and session.room_id and session.starts_at and session.ends_at:
        from .lessons import validate_lesson_room
        validate_lesson_room(session.room_id, session.starts_at, session.ends_at)
        if active_claims().filter(room_id=session.room_id, party_request__isnull=False,
            starts_at__lt=session.ends_at, ends_at__gt=session.starts_at).exists():
            raise ValidationError({"room": "This room has an active party reservation at that time."})


def session_places_taken(session, exclude=None):
    # OR + distinct avoids multiplying a reservation when it has several camp dates.
    claims = active_claims().exclude(pk=exclude).filter(Q(session=session) | Q(occurrences__session=session)).distinct()
    return claims.aggregate(total=Sum("quantity"))["total"] or 0


def ensure_session_capacity(item, session, starts_at, ends_at, room_id):
    if not session.is_published or not session.offering.is_published:
        raise ValidationError("This session is no longer published.")
    if (session.starts_at, session.ends_at, session.room_id) != (starts_at, ends_at, room_id):
        raise ValidationError("This session has changed since the quote was created.")
    if session_places_taken(session, item.pk) + item.quantity > session.capacity:
        raise ValidationError("There are not enough places left for this whole group.")


def ensure_capacity(item):
    from .inventory import ensure_stock
    ensure_stock(item)
    now = timezone.now()
    if item.starts_at <= now:
        raise ValidationError("This time has already started. No reservation can be made.")
    claims = active_claims(now).exclude(pk=item.pk)
    if item.camp_registration_id:
        occurrences = list(item.occurrences.select_related("session__offering"))
        expected = set(item.camp_registration.camp.sessions.values_list("pk", flat=True))
        if len(occurrences) < 2 or {o.session_id for o in occurrences} != expected:
            raise ValidationError("The camp schedule is incomplete. Contact the studio before paying.")
        for occurrence in occurrences:
            ensure_session_capacity(item, occurrence.session, occurrence.starts_at, occurrence.ends_at, occurrence.room_id)
        return
    if item.session_id:
        session = Session.objects.select_related("offering").get(pk=item.session_id)
        ensure_session_capacity(item, session, item.starts_at, item.ends_at, item.room_id)
        return
    room = item.room
    from .lessons import validate_lesson_room
    validate_lesson_room(item.room_id, item.starts_at, item.ends_at)
    overlaps = claims.filter(room=room, starts_at__lt=item.ends_at, ends_at__gt=item.starts_at)
    if Session.objects.filter(room=room, is_published=True, starts_at__lt=item.ends_at, ends_at__gt=item.starts_at).exists():
        raise ValidationError("This room has a published session at the requested time.")
    if ReservationOccurrence.objects.filter(reservation__in=claims, room=room,
            starts_at__lt=item.ends_at, ends_at__gt=item.starts_at).exists():
        raise ValidationError("This room has a camp reservation at the requested time.")
    if overlaps.exists() and (item.exclusive or overlaps.filter(Q(exclusive=True) | Q(session__isnull=False)).exists()):
        raise ValidationError("An exclusive reservation conflicts with this room and time.")
    intervals = list(overlaps.values_list("starts_at", "ends_at", "quantity"))
    intervals.append((item.starts_at, item.ends_at, item.quantity))
    if peak_quantity(intervals) > room.capacity:
        raise ValidationError("This room does not have enough shared capacity for the whole group.")


def _audit(user, item, action, **details):
    AdminAuditLog.objects.create(actor=user, action="change", model_label="scheduling.reservation",
        object_id=str(item.pk), object_repr=str(item.public_id), changes={"action": action, "state": item.state},
        extra={"scheduling_profile_id": item.profile_id, **details})


def locked_profile(user, slug):
    profile, _ = require_access(user, slug, "setup.edit", capability="reservations")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    return require_access(user, slug, "setup.edit", capability="reservations")[0]


@transaction.atomic
def create_reservation(user, slug, kind, public_id, total_cents=None, deposit_cents=None, source_revision=None, online=False):
    profile = locked_profile(user, slug)
    if not profile.native_reservations_enabled:
        raise ValidationError("Native reservations are not enabled for this workspace.")
    account = booking_account(profile, online)
    if kind == "workshop":
        require_capability(profile, "workshops")
        source = get_object_or_404(WorkshopRequest.objects.select_related("session__offering__location", "session__room"),
            public_id=public_id, session__offering__location__profile=profile)
        existing = Reservation.objects.filter(workshop_request=source).first()
        if existing:
            if (existing.order.total_cents, existing.order.deposit_cents, existing.order.account_id) != (total_cents, deposit_cents, account.pk if account else None):
                raise ValidationError("A reservation already exists with a different quote.")
            return existing
        if source.status != "pending":
            raise ValidationError("Only pending workshop requests can become reservations.")
        session = source.session
        item = Reservation(profile=profile, workshop_request=source, session=session, room=session.room,
            starts_at=session.starts_at, ends_at=session.ends_at, quantity=source.participants.count(),
            snapshot={"name": source.purchaser_name, "workshop": source.session_snapshot["workshop"],
                      "timezone": source.session_snapshot["timezone"]})
    elif kind == "party":
        require_capability(profile, "parties")
        source = get_object_or_404(PartyRequest.objects.select_related("offering__location", "room"),
            public_id=public_id, offering__location__profile=profile)
        existing = Reservation.objects.filter(party_request=source).first()
        if existing:
            if existing.order.account_id != (account.pk if account else None):
                raise ValidationError("This reservation already uses a different payment method.")
            return existing
        if source_revision is not None and source.revision != source_revision:
            raise ValidationError("The party proposal changed. Open a new reservation form before approving it.")
        if source.status != "reviewed" or not source.room_id or source.proposed_total is None:
            raise ValidationError("Review this party with a proposed room, total and deposit first.")
        total_cents, deposit_cents = int(source.proposed_total * 100), int(source.proposed_deposit * 100)
        item = Reservation(profile=profile, party_request=source, room=source.room, starts_at=source.starts_at,
            ends_at=source.ends_at, quantity=source.group_size, exclusive=source.space == "exclusive",
            snapshot={"name": source.organizer_name, "workshop": source.offering_snapshot["workshop"],
                      "timezone": source.offering_snapshot["timezone"]})
    else:
        raise ValidationError("Choose a workshop request or party inquiry.")
    ensure_capacity(item)
    item.order = payments.create_order(school=profile.school, purpose="booking", source_key=f"{kind}:{source.public_id}",
        total_cents=total_cents, deposit_cents=deposit_cents, description=item.snapshot["workshop"], account=account)
    item.hold_until = min(timezone.now() + timedelta(minutes=30), item.starts_at)
    if total_cents == 0:
        item.state = "confirmed"
    item.full_clean()
    item.save()
    from .inventory import allocate
    allocate(item)
    _audit(user, item, "created")
    return item


def booking_account(profile, online):
    if not online:
        return None
    from core.services.billing_stripe import merchant_test_checkout_ready
    account = PaymentAccount.objects.filter(school=profile.school, enabled=True, environment="test").first()
    if not account or not merchant_test_checkout_ready():
        raise ValidationError("Stripe sandbox is not connected. Use offline test records for now.")
    return account


@transaction.atomic
def settle_offline(user, slug, public_id, *, key, kind, amount_cents, reference):
    profile = locked_profile(user, slug)
    item = get_object_or_404(Reservation.objects.select_related("order", "room"), profile=profile, public_id=public_id, visit_booking__isnull=True)
    # Exact retries remain valid after cancellation. A different payload is rejected in the ledger.
    existing = item.order.entries.filter(key=key).exists()
    if not existing and ((kind == "refund" and item.state != "canceled") or (kind == "payment" and item.state == "canceled")):
        raise ValidationError("Cancel the reservation before recording a refund; canceled reservations cannot receive new payments.")
    payments.record_offline(order=item.order, actor=user, key=key, kind=kind, amount_cents=amount_cents, reference=reference)
    if existing or item.state == "canceled":
        return item
    required = item.order.deposit_cents or item.order.total_cents
    if item.state not in ("confirmed", "completed") and payments.totals(item.order)["net_cents"] >= required:
        try:
            ensure_capacity(item)
        except ValidationError:
            # Received money must not disappear just because an expired hold lost its capacity.
            item.state = "review"
        else:
            item.state = "confirmed"
        item.save(update_fields=["state"])
    elif item.state == "held" and item.hold_until <= timezone.now():
        item.state = "review"
        item.save(update_fields=["state"])
    _audit(user, item, "offline_settlement")
    return item


@transaction.atomic
def cancel_reservation(user, slug, public_id):
    profile = locked_profile(user, slug)
    item = get_object_or_404(Reservation, profile=profile, public_id=public_id, visit_booking__isnull=True)
    if item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists():
        raise ValidationError("A checkout may still accept payment. Verify its expiry before canceling this reservation.")
    if item.state == "completed":
        raise ValidationError("Completed reservations cannot be canceled. Review any payment adjustment separately.")
    if item.state != "canceled":
        item.state = "canceled"
        item.save(update_fields=["state"])
        _audit(user, item, "canceled")
    return item
