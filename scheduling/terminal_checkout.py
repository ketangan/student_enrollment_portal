"""Staff Terminal collection uses the same attempts, settlement and recovery as online cards."""
from datetime import timedelta
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from django.urls import reverse
from django.utils import timezone

from core.payments import checkout, recovery, square, square_terminal
from core.payments.service import totals
from core.payments.models import PaymentAttempt, ProviderEventReceipt, Refund
from .checkout import settle_booking_snapshot, validate_booking_checkout
from .models import Reservation, SquareTerminal
from .reservations import _audit
from .square_checkout import locked_attempt
from .terminal_payments import locked_reservation


class CollectionAmountForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal("0.01"), max_value=Decimal("999999.99"), decimal_places=2, max_digits=8)
    treatment = forms.ChoiceField(required=False, choices=[("partial", "Partial payment"),
        ("discount", "Discount on this booking"), ("credit", "Credit from an earlier booking")])
    reason = forms.CharField(required=False, max_length=240)


def location_id(item):
    if item.party_request_id:
        return item.party_request.offering.location_id
    if item.workshop_request_id:
        return item.workshop_request.session.offering.location_id
    return None


def devices_for(item):
    return SquareTerminal.objects.filter(location_id=location_id(item), account_id=item.order.account_id,
        status="paired").order_by("name")


@transaction.atomic
def prepare(user, slug, public_id, device_id, key, payment_option, expected_balance, expected_due=None, *, collection=None):
    item = locked_reservation(user, slug, public_id)
    amount, adjustment, kind, reason = None, 0, "", ""
    if collection is not None:
        form = CollectionAmountForm(collection)
        if not form.is_valid():
            raise ValidationError([message for errors in form.errors.values() for message in errors])
        data = form.cleaned_data
        data["treatment"] = data["treatment"] or "partial"
        amount = int(data["amount"] * 100)
        difference = amount - expected_balance
        if difference > 0:
            if data["treatment"] != "partial":
                raise ValidationError("A discount or credit must reduce the balance.")
            adjustment, kind = difference, "purchase"
        elif data["treatment"] != "partial":
            if difference >= 0:
                raise ValidationError("A discount or credit must reduce the balance.")
            adjustment, kind = difference, data["treatment"]
        reason = data["reason"]
    existing = PaymentAttempt.objects.filter(public_id=key).first()
    if existing:
        if (existing.order_id != item.order_id or existing.collection_method != "terminal"
                or not SquareTerminal.objects.filter(pk=device_id, account_id=existing.account_id,
                    location_id=location_id(item), device_id=existing.device_reference).exists()):
            raise ValidationError("This payment request has already been used.")
        if collection is not None and (existing.amount_cents, existing.adjustment_cents,
                existing.adjustment_kind, existing.adjustment_reason) != (amount, adjustment, kind, reason):
            raise ValidationError("This payment request was already used with different amounts or adjustments.")
        return existing
    validate_booking_checkout(item)
    amounts = totals(item.order)
    if amounts["outstanding_cents"] != expected_balance:
        raise ValidationError("The balance changed. Review the updated amount before collecting payment.")
    if not 0 <= amounts["total_cents"] + adjustment <= 99999999:
        raise ValidationError("The adjusted booking total is outside the supported range.")
    if payment_option == "due" and expected_due is not None and checkout.amount_due(item.order) != expected_due:
        raise ValidationError("The deposit changed. Review the updated amount before collecting payment.")
    if item.state not in {"confirmed", "completed"} or not square_terminal.enabled(item.order.account):
        raise ValidationError("Terminal collection is not available for this booking.")
    device = get_object_or_404(devices_for(item), pk=device_id)
    if device.simulation != (item.order.environment == "test"):
        raise ValidationError("Terminal environment does not match this booking.")
    if Refund.objects.filter(attempt__order=item.order, state__in=Refund.ACTIVE).exists():
        raise ValidationError("Resolve the pending refund before collecting another payment.")
    if PaymentAttempt.objects.filter(account=item.order.account, device_reference=device.device_id,
            state__in=PaymentAttempt.ACTIVE).exists():
        raise ValidationError("This Terminal already has a payment in progress.")
    route = reverse("scheduling:reservation_detail", kwargs={"school_slug": slug, "public_id": public_id})
    attempt = checkout.prepare_attempt(item.order, return_url=route, payment_option=payment_option,
        device_reference=device.device_id, terminal_amount=amount)
    square_terminal.validate_amount(attempt)
    attempt.public_id = key
    attempt.expires_at = timezone.now() + timedelta(minutes=5)
    attempt.adjustment_cents, attempt.adjustment_kind = adjustment, kind
    attempt.adjustment_reason, attempt.requested_by = reason, user
    attempt.save(update_fields=["public_id", "expires_at", "adjustment_cents", "adjustment_kind", "adjustment_reason", "requested_by"])
    _audit(user, item, "terminal_payment_requested", attempt=str(attempt.public_id), terminal=device.name,
        amount_cents=attempt.amount_cents, adjustment_cents=adjustment, adjustment_kind=kind, reason=reason)
    return attempt


def validate_collection(attempt):
    square_terminal.validate_amount(attempt)
    item = Reservation.objects.select_related("profile", "order__account").get(order=attempt.order)
    validate_booking_checkout(item)
    if item.state not in {"confirmed", "completed"} or not devices_for(item).filter(
            device_id=attempt.device_reference, simulation=item.order.environment == "test").exists():
        raise ValidationError("This Terminal or booking is no longer available for collection. Close this payment and review its settings.")


def submit(attempt):
    with transaction.atomic():
        current = locked_attempt(attempt)
        checkout.validate_account(current.order)
        if (current.collection_method != "terminal" or current.state not in PaymentAttempt.ACTIVE
                or current.close_requested or current.submitted_at or current.expires_at <= timezone.now()):
            return
        validate_collection(current)
        current.submitted_at, current.state = timezone.now(), "unknown"
        current.save(update_fields=["submitted_at", "state"])
    result = square_terminal.create(current)
    settle(current, result)


def retry(attempt):
    def operation(current):
        with transaction.atomic():
            current = locked_attempt(current)
            checkout.validate_account(current.order)
            if (current.collection_method != "terminal" or current.state != "unknown"
                    or current.terminal_checkout_id or current.close_requested or not current.submitted_at
                    or current.expires_at <= timezone.now()):
                raise ValidationError("Check this Terminal payment before trying again.")
            validate_collection(current)
        settle(current, square_terminal.create(current))
    return recovery.run(attempt, operation, force=True)


def settle(attempt, result):
    square_terminal.validate(attempt, result)
    with transaction.atomic():
        current = locked_attempt(attempt)
        checkout.validate_account(current.order, new_payment=False)
        if not current.submitted_at or current.account_id != current.order.account_id:
            raise ValidationError("Terminal payment was not submitted for this account.")
        square_terminal.validate(current, result)
        current.terminal_checkout_id = result["id"]
        current.save(update_fields=["terminal_checkout_id"])
    identities = result.get("payment_ids", [])
    if not isinstance(identities, list) or len(identities) > 1 or any(not square.identifier(pk) for pk in identities):
        raise ValidationError("Terminal payment requires provider review; no balance was changed.")
    payment = None
    if result["status"] == "COMPLETED":
        if len(identities) != 1:
            raise ValidationError("Square has not supplied the completed payment yet.")
        payment = square.account_request(attempt.account, "GET", "/payments/" + identities[0]).get("payment")
        if not isinstance(payment, dict) or payment.get("id") != identities[0] or payment.get("status") != "COMPLETED":
            raise ValidationError("Terminal payment is not yet verified.")
    elif identities:
        raise ValidationError("Terminal checkout contains an unresolved payment. Do not collect again.")
    with transaction.atomic():
        current = locked_attempt(attempt)
        square_terminal.validate(current, result)
        if current.state == "paid":
            return
        current.terminal_checkout_id = result["id"]
        current.terminal_status = result["status"]
        current.save(update_fields=["terminal_checkout_id", "terminal_status"])
        snapshot = square.snapshot(current, payment, expired=result["status"] == "CANCELED")
        settle_booking_snapshot(current, snapshot)


def recover(attempt):
    with transaction.atomic():
        current = locked_attempt(attempt)
        if current.state not in PaymentAttempt.ACTIVE:
            return
        if not current.submitted_at:
            if current.close_requested or current.expires_at <= timezone.now():
                settle_booking_snapshot(current, square.snapshot(current, expired=True))
            return
    result = square_terminal.retrieve(current)
    # Save the verified identity before a cancellation call can time out.
    with transaction.atomic():
        current = locked_attempt(current)
        square_terminal.validate(current, result)
        current.terminal_checkout_id = result["id"]
        current.save(update_fields=["terminal_checkout_id"])
    if current.close_requested and result["status"] in {"PENDING", "IN_PROGRESS"}:
        result = square_terminal.cancel(current)
    settle(current, result)


def settle_terminal_event(attempt, event, identity):
    if not attempt.submitted_at or event.get("merchant_id") != attempt.account.reference or not square_terminal.checkout_id(identity):
        raise ValidationError("Terminal payment was not initiated for this account.")
    if ProviderEventReceipt.objects.filter(account=attempt.account, environment=attempt.account.environment,
            event_id=event["event_id"]).exists():
        return
    result = square_terminal.retrieve(attempt, identity)
    settle(attempt, result)
    ProviderEventReceipt.objects.get_or_create(account=attempt.account, environment=attempt.account.environment,
        event_id=event["event_id"], defaults={"event_type": event["type"], "attempt": attempt})


def webhook(event, environment):
    try:
        data = event.get("data", {})
        obj = data.get("object", {}) if isinstance(data, dict) else {}
        result = obj.get("checkout", {}) if isinstance(obj, dict) else {}
        reference = result.get("reference_id") if isinstance(result, dict) else None
        identity = result.get("id") if isinstance(result, dict) else None
        if not square_terminal.checkout_id(identity):
            raise ValidationError("Invalid Terminal checkout.")
        try:
            attempt = PaymentAttempt.objects.select_related("order__account", "account__school").filter(
                public_id=reference, collection_method="terminal", account__provider="square",
                account__environment=environment, account__reference=event.get("merchant_id")).first() if reference else None
        except (ValueError, ValidationError):
            attempt = None
        if attempt:
            settle_terminal_event(attempt, event, identity)
    except ValidationError:
        return HttpResponse("Terminal verification is pending.", status=503)
    return HttpResponse(status=200)
