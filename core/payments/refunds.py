"""Shared, account-bound test refunds. Domain code authorizes eligibility first."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from core.models import School
from .providers import gateway
from .checkout import validate_account
from .models import LedgerEntry, PaymentAttempt, PaymentOrder, Refund
from . import recovery


@transaction.atomic
def approve(attempt, *, key, amount_cents, reason, actor):
    School.objects.select_for_update().get(pk=attempt.order.school_id)
    PaymentOrder.objects.select_for_update().get(pk=attempt.order_id)
    attempt = PaymentAttempt.objects.select_related("order__account").get(pk=attempt.pk)
    validate_account(attempt.order, new_payment=False)
    if (attempt.state != "paid" or not attempt.entry_id or not attempt.provider_intent
            or attempt.account_id != attempt.order.account_id):
        raise ValidationError("Only verified payments can be refunded.")
    if type(amount_cents) is not int or amount_cents < 1 or not reason.strip() or len(reason.strip()) > 240:
        raise ValidationError("Enter a valid refund amount and reason.")
    existing = Refund.objects.filter(key=key).first()
    if existing:
        if (existing.attempt_id, existing.amount_cents, existing.reason, existing.actor_id) != (
                attempt.pk, amount_cents, reason.strip(), actor.pk):
            raise ValidationError("This refund request was already used with different details.")
        return existing
    allocated = attempt.refunds.exclude(state__in=["failed", "canceled"]).aggregate(value=Sum("amount_cents"))["value"] or 0
    if amount_cents > attempt.amount_cents - allocated:
        raise ValidationError("Refund exceeds the payment available after completed and pending refunds.")
    return Refund.objects.create(attempt=attempt, key=key, amount_cents=amount_cents, reason=reason.strip(), actor=actor)


@transaction.atomic
def apply_snapshot(refund, snapshot):
    School.objects.select_for_update().get(pk=refund.attempt.order.school_id)
    PaymentOrder.objects.select_for_update().get(pk=refund.attempt.order_id)
    refund = Refund.objects.select_for_update(of=("self",)).select_related("attempt__order__account").get(pk=refund.pk)
    attempt, order = refund.attempt, refund.attempt.order
    validate_account(order, new_payment=False)
    metadata = snapshot.get("metadata") or {}
    if (attempt.account_id != order.account_id or snapshot.get("livemode") is True or snapshot.get("currency") != order.currency
            or type(snapshot.get("amount")) is not int or snapshot["amount"] != refund.amount_cents
            or snapshot.get("payment_intent") != attempt.provider_intent
            or metadata.get("payment_refund") != str(refund.public_id)
            or metadata.get("payment_order") != str(order.public_id)
            or snapshot.get("status") not in {"pending", "requires_action", "succeeded", "failed", "canceled"}
            or not isinstance(snapshot.get("id"), str) or not snapshot["id"] or len(snapshot["id"]) > 100
            or (order.account.provider == "stripe" and not snapshot["id"].startswith("re_"))
            or (refund.provider_refund and refund.provider_refund != snapshot["id"])):
        raise ValidationError("Refund does not match its approved payment, amount or identity.")
    if Refund.objects.filter(provider_refund=snapshot["id"]).exclude(pk=refund.pk).exists():
        raise ValidationError("Provider refund is already bound to another request.")
    if refund.state in {"succeeded", "failed", "canceled"}:
        if refund.state != snapshot["status"]:
            raise ValidationError("Terminal refund state changed; operator investigation is required.")
        return refund
    refund.provider_refund, refund.state = snapshot["id"], snapshot["status"]
    if refund.state == "succeeded" and not refund.entry_id:
        refund.entry = LedgerEntry.objects.create(order=order, school_id=order.school_id, environment="test",
            key=refund.public_id, kind="refund", channel=order.account.provider, amount_cents=refund.amount_cents, reference=snapshot["id"])
    refund.save(update_fields=["provider_refund", "state", "entry"])
    return refund


def process(refund, *, force=False):
    def operation(current):
        validate_account(current.attempt.order, new_payment=False)
        if current.attempt.account_id != current.attempt.order.account_id:
            raise ValidationError("Payment account binding changed.")
        if current.state not in Refund.ACTIVE:
            return
        if not current.submitted_at:
            # Commit uncertainty BEFORE contacting the provider. Never create twice after a crash.
            Refund.objects.filter(pk=current.pk).update(submitted_at=timezone.now(), state="unknown")
            snapshot = gateway(current.attempt.account).create_order_refund(current)
        else:
            snapshot = gateway(current.attempt.account).retrieve_order_refund(current)
        apply_snapshot(current, snapshot)
    return recovery.run(refund, operation, force=force)
