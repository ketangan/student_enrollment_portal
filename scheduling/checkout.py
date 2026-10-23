"""Booking-specific capacity and settlement; payment transport stays in core."""

from datetime import timedelta
from decimal import Decimal

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.payments import checkout, recovery, refunds, service
from core.payments.providers import gateway
from core.models import School
from core.payments.models import PaymentAttempt, PaymentOrder, Refund
from .models import Reservation, SchedulingProfile
from .access import get_staff_reservation
from .reservations import _audit, ensure_capacity, locked_profile

CHECKOUT_LEAD_TIME = timedelta(minutes=40)


def payment_token(item):
    return signing.dumps({"reservation": str(item.public_id), "school": item.profile.school.slug}, salt="booking-payment")


def customer_reservation(slug, token):
    try:
        data = signing.loads(token, salt="booking-payment", max_age=86400)
        if data["school"] != slug:
            raise signing.BadSignature()
    except (signing.BadSignature, KeyError, TypeError) as exc:
        raise Http404("Payment link expired or invalid.") from exc
    return get_object_or_404(Reservation.objects.select_related("order__account", "profile__school"),
        public_id=data["reservation"], profile__school__slug=slug, order__account__isnull=False)


def validate_booking_checkout(item):
    profile = item.profile
    # Intake can stop without preventing an existing confirmed booking's balance payment.
    if (not profile.enabled or item.state not in ("held", "confirmed", "completed")
            or (not profile.native_reservations_enabled and item.state not in ("confirmed", "completed"))):
        raise ValidationError("This reservation is not available for checkout. Please contact the studio.")
    if item.state == "held" and item.starts_at < timezone.now() + CHECKOUT_LEAD_TIME:
        raise ValidationError("Online checkout closes 40 minutes before the reservation starts.")
    checkout.validate_account(item.order)


@transaction.atomic
def prepare_booking_checkout(item, return_url, *, payment_option="due"):
    SchedulingProfile.objects.select_for_update().get(pk=item.profile_id)
    item = Reservation.objects.select_related("order__account", "profile").get(pk=item.pk)
    validate_booking_checkout(item)
    if item.state not in ("confirmed", "completed"):
        ensure_capacity(item)
    attempt = checkout.prepare_attempt(item.order, return_url=return_url, payment_option=payment_option)
    if item.state == "held":
        item.hold_until = max(item.hold_until, attempt.expires_at)
        item.save(update_fields=["hold_until"])
    return attempt


def payment_choices(order):
    minimum = checkout.amount_due(order)
    outstanding = service.totals(order)["outstanding_cents"]
    active = order.attempts.filter(state__in=PaymentAttempt.ACTIVE).first()
    return {
        "payment_has_choice": 0 < minimum < outstanding and active is None,
        "payment_minimum": Decimal(minimum) / 100,
        "payment_full": Decimal(outstanding) / 100,
        "payment_later": Decimal(outstanding - minimum) / 100,
        "payment_active_amount": Decimal(active.amount_cents) / 100 if active else None,
        "payment_option": "full" if active and active.amount_cents == outstanding else "due",
    }


@transaction.atomic
def settle_booking_event(event):
    attempt = checkout.event_attempt(event)
    if attempt is None:
        return
    if attempt.order.purpose != "booking":
        raise ValidationError("This endpoint only settles booking orders.")
    item = get_object_or_404(Reservation, order=attempt.order)
    SchedulingProfile.objects.select_for_update().get(pk=item.profile_id)
    item.refresh_from_db()
    previous = PaymentAttempt.objects.get(pk=attempt.pk).state
    attempt = checkout.apply_event(event)
    _settle(item, attempt, previous)


def _settle(item, attempt, previous):
    if previous == attempt.state:
        return
    if attempt.state == "paid" and item.state != "canceled":
        amounts = service.totals(item.order)
        if item.state not in ("confirmed", "completed") and (amounts["net_cents"] > amounts["total_cents"] or item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists()):
            item.state = "review"
        elif item.state not in ("confirmed", "completed"):
            try:
                ensure_capacity(item)
            except ValidationError:
                item.state = "review"
            else:
                item.state = "confirmed"
        item.save(update_fields=["state"])
        _audit(None, item, "verified_test_payment" if item.order.environment == "test" else "verified_payment")
    elif attempt.state == "expired" and item.state == "held":
        # Only verified provider expiry releases an unpaid online hold.
        item.hold_until = timezone.now()
        item.save(update_fields=["hold_until"])
    _audit(None, item, "checkout_status", attempt=str(attempt.public_id), status=attempt.get_state_display())


@transaction.atomic
def settle_booking_snapshot(attempt, snapshot):
    item = Reservation.objects.get(order=attempt.order)
    SchedulingProfile.objects.select_for_update().get(pk=item.profile_id)
    item.refresh_from_db()
    previous = PaymentAttempt.objects.get(pk=attempt.pk).state
    attempt = checkout.apply_snapshot(attempt, snapshot)
    _settle(item, attempt, previous)


def recover_attempt(attempt, *, force=False):
    def operation(current):
        checkout.validate_account(current.order, new_payment=False)
        if current.account_id != current.order.account_id or current.order.purpose != "booking":
            raise ValidationError("Invalid booking payment binding.")
        if current.state not in PaymentAttempt.ACTIVE:
            return
        if current.account.provider == "square":
            from .square_checkout import recover_square_attempt
            return recover_square_attempt(current)
        snapshot = gateway(current.account).retrieve_order_checkout(current)
        checkout._validate_session(current, snapshot)
        if snapshot.get("status") == "open" and (current.close_requested or current.expires_at <= timezone.now()):
            snapshot = gateway(current.account).expire_order_checkout(current, snapshot["id"])
        settle_booking_snapshot(current, snapshot)
    return recovery.run(attempt, operation, force=force)


@transaction.atomic
def request_close(user, slug, public_id, attempt_id):
    profile = locked_profile(user, slug)
    item = get_staff_reservation(profile, public_id)
    attempt = get_object_or_404(PaymentAttempt, order=item.order, public_id=attempt_id)
    attempt.close_requested = True
    attempt.save(update_fields=["close_requested"])
    _audit(user, item, "close_checkout_requested")
    return attempt


@transaction.atomic
def approve_refund(user, slug, public_id, attempt_id, **kwargs):
    profile = locked_profile(user, slug)
    item = get_staff_reservation(profile, public_id)
    School.objects.select_for_update().get(pk=profile.school_id)
    PaymentOrder.objects.select_for_update().get(pk=item.order_id)
    if item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists():
        raise ValidationError("Close outstanding checkouts and cancel the reservation before approving a refund.")
    attempt = get_object_or_404(PaymentAttempt, order=item.order, public_id=attempt_id)
    existing = Refund.objects.filter(key=kwargs["key"], attempt=attempt).exists()
    if item.state != "canceled" and not existing:
        pending = Refund.objects.filter(attempt__order=item.order, state__in=Refund.ACTIVE).aggregate(
            value=Sum("amount_cents"))["value"] or 0
        amounts = service.totals(item.order)
        excess = amounts["net_cents"] - amounts["total_cents"] - pending
        if item.state == "completed" and (type(kwargs["amount_cents"]) is not int or not 0 < kwargs["amount_cents"] <= excess):
            raise ValidationError("Only excess payment can be refunded here after completion. A service refund requires a separate billing adjustment.")
        if item.state not in ("confirmed", "completed") or type(kwargs["amount_cents"]) is not int or not 0 < kwargs["amount_cents"] <= excess:
            raise ValidationError("Only excess payment can be returned while keeping a confirmed booking. Otherwise cancel it first.")
    refund = refunds.approve(attempt, actor=user, **kwargs)
    _audit(user, item, "refund_approved", refund=str(refund.public_id), amount_cents=refund.amount_cents)
    return refund


@transaction.atomic
def resolve_review(user, slug, public_id):
    profile = locked_profile(user, slug)
    item = get_staff_reservation(profile, public_id)
    amounts = service.totals(item.order)
    if (item.state != "review" or item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists()
            or not (item.order.deposit_cents or item.order.total_cents) <= amounts["net_cents"] <= amounts["total_cents"]):
        raise ValidationError("Confirmation requires verified payment, no excess money and no outstanding checkout.")
    ensure_capacity(item)
    item.state = "confirmed"
    item.save(update_fields=["state"])
    _audit(user, item, "payment_review_confirmed")
