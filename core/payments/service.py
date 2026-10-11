"""Financial records shared by domains. No Stripe or enrollment side effects."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from core.models import AdminAuditLog, School
from .models import LedgerEntry, PaymentOrder
from .accounts import validate_scope


def totals(order):
    amounts = {row["kind"]: row["amount"] for row in order.entries.values("kind").annotate(amount=Sum("amount_cents"))}
    paid, refunded = amounts.get("payment", 0), amounts.get("refund", 0)
    net = paid - refunded
    return {"paid_cents": paid, "refunded_cents": refunded, "net_cents": net,
            "outstanding_cents": max(0, order.total_cents - net),
            "paid": Decimal(paid) / 100, "refunded": Decimal(refunded) / 100,
            "net": Decimal(net) / 100, "outstanding": Decimal(max(0, order.total_cents - net)) / 100}


@transaction.atomic
def create_order(*, school, purpose, source_key, total_cents, deposit_cents, description, account=None):
    if type(total_cents) is not int or type(deposit_cents) is not int:
        raise ValidationError("Money must be expressed as whole cents.")
    School.objects.select_for_update().get(pk=school.pk)
    if account and (account.school_id != school.pk or account.environment != "test" or not account.enabled):
        raise ValidationError("A test payment account is not enabled for this business.")
    if account:
        validate_scope(account)
    values = dict(total_cents=total_cents, deposit_cents=deposit_cents, description=description,
                  merchant_reference=account.reference if account else f"offline:{school.pk}",
                  account=account, currency="usd")
    identity = dict(school=school, purpose=purpose, source_key=source_key, environment="test")
    existing = PaymentOrder.objects.filter(**identity).first()
    if existing:
        if any(getattr(existing, field) != value for field, value in values.items()):
            raise ValidationError("This order already exists with a different quote.")
        return existing
    order = PaymentOrder(**identity, **values)
    order.full_clean()
    order.save()
    return order


@transaction.atomic
def record_offline(*, order, actor, key, kind, amount_cents, reference):
    """Called after domain authorization. A reference may settle only one order."""
    if type(amount_cents) is not int:
        raise ValidationError("Money must be expressed as whole cents.")
    School.objects.select_for_update().get(pk=order.school_id)
    order = PaymentOrder.objects.select_for_update().get(pk=order.pk)
    if order.environment != "test" or order.merchant_reference != f"offline:{order.school_id}":
        raise ValidationError("Offline preview settlement is available only for test orders.")
    reference = reference.strip()
    existing = LedgerEntry.objects.filter(key=key).first()
    if existing:
        if (existing.order_id, existing.kind, existing.amount_cents, existing.reference) != (order.pk, kind, amount_cents, reference):
            raise ValidationError("This action was already used with different payment details.")
        return existing
    entry = LedgerEntry(order=order, school_id=order.school_id, environment=order.environment,
        key=key, kind=kind, amount_cents=amount_cents, reference=reference, actor=actor)
    entry.full_clean()
    amounts = totals(order)
    maximum = amounts["outstanding_cents"] if kind == "payment" else amounts["net_cents"]
    if amount_cents > maximum:
        raise ValidationError("The amount exceeds the outstanding balance or refundable amount.")
    entry.save()
    AdminAuditLog.objects.create(actor=actor, action="add", model_label="payments.ledgerentry",
        object_id=str(entry.pk), object_repr=str(order.public_id),
        changes={"kind": kind, "amount_cents": amount_cents},
        extra={"school_id": order.school_id, "environment": order.environment})
    return entry
