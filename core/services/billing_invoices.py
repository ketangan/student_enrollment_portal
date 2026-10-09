"""Verified platform invoices drive payment alerts and the seven-day access grace."""

from datetime import datetime, timedelta, timezone as utc_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Min

from core.models import BillingInvoice, School
from core.services import billing_stripe as gateway
from core.services.payment_notifications import queue_payment_notice

INVOICE_EVENTS = frozenset({
    "invoice.paid", "invoice.payment_failed", "invoice.payment_action_required",
    "invoice.finalization_failed", "invoice.voided", "invoice.marked_uncollectible",
})
FAILURE_EVENTS = {"invoice.payment_failed", "invoice.payment_action_required"}


def object_id(value):
    return value.get("id", "") if isinstance(value, dict) else (value or "")


def subscription_id(invoice):
    return object_id(invoice.get("subscription") or
        ((invoice.get("parent") or {}).get("subscription_details") or {}).get("subscription"))


def unique_school(**lookup):
    rows = list(School.objects.filter(**lookup)[:2])
    if len(rows) > 1:
        raise ValidationError("Ambiguous school billing identity; operator review required")
    return rows[0] if rows else None


def refresh_payment_grace(school):
    """Caller holds the school lock. No subscriptions or manual suspension flags change."""
    if school.is_demo:
        return
    first = BillingInvoice.objects.filter(
        school=school, subscription_id=school.stripe_subscription_id, livemode=True,
        status__in=["open", "uncollectible"], first_failed_at__isnull=False,
    ).aggregate(first=Min("first_failed_at"))["first"]
    school.payment_grace_ends_at = first + timedelta(days=7) if first else None
    school.save(update_fields=["payment_grace_ends_at"])


def handle_invoice_event(event):
    snapshot = event["data"]["object"]
    invoice_id = snapshot.get("id")
    if not invoice_id or not event.get("id") or type(event.get("livemode")) is not bool:
        raise ValidationError("Invalid invoice event identity")
    if event["livemode"] != (settings.STRIPE_MODE == "live") or event.get("account"):
        raise ValidationError("Invoice event belongs to a different billing environment")
    sub_id = subscription_id(snapshot)
    customer = object_id(snapshot.get("customer"))
    if not customer:
        raise ValidationError("Invoice customer is missing")
    school = unique_school(stripe_subscription_id=sub_id) if sub_id else None
    if school is None:
        school = unique_school(stripe_customer_id=customer)
    if school is None and sub_id:
        subscription = gateway.retrieve_billing_subscription(sub_id)
        if object_id(subscription.get("customer")) != customer:
            raise ValidationError("Subscription customer mismatch")
        slug = (subscription.get("metadata") or {}).get("school_slug")
        school = School.objects.filter(slug=slug).first() if slug else None

    with transaction.atomic():
        if school:
            school = School.objects.select_for_update().get(pk=school.pk)
            if school.stripe_customer_id and school.stripe_customer_id != customer:
                raise ValidationError("Invoice does not belong to this school")
        # Read after the school lock: an old delivery cannot overwrite a newer
        # invoice state. No Stripe writes or card charges occur in this handler.
        current = gateway.retrieve_billing_invoice(invoice_id)
        if (current.get("id") != invoice_id or current.get("livemode") != event["livemode"]
                or object_id(current.get("customer")) != customer
                or subscription_id(current) != sub_id):
            raise ValidationError("Invoice identity mismatch")
        status = current.get("status")
        if status not in {"draft", "open", "paid", "void", "uncollectible"}:
            raise ValidationError("Unrecognized invoice state")
        if school and sub_id:
            row, _ = BillingInvoice.objects.get_or_create(provider_id=invoice_id, defaults={
                "school": school, "subscription_id": sub_id, "livemode": event["livemode"], "status": status,
            })
            if (row.school_id, row.subscription_id, row.livemode) != (school.pk, sub_id, event["livemode"]):
                raise ValidationError("Invoice binding changed")
            if event["type"] in FAILURE_EVENTS:
                failed_at = datetime.fromtimestamp(event["created"], tz=utc_timezone.utc)
                row.first_failed_at = min(row.first_failed_at, failed_at) if row.first_failed_at else failed_at
            row.status = status
            row.save(update_fields=["first_failed_at", "status", "updated_at"])
            # Do not inherit a previous subscription's debt or let test payments
            # suspend real users. Trial/demo activation rules remain independent.
            if event["livemode"] and not school.is_demo and school.stripe_subscription_id in ("", sub_id):
                if not school.stripe_subscription_id:
                    school.stripe_subscription_id, school.stripe_customer_id = sub_id, customer
                    school.save(update_fields=["stripe_subscription_id", "stripe_customer_id"])
                refresh_payment_grace(school)

        kind = event["type"].removeprefix("invoice.").replace("_", " ")
        if event["type"] == "invoice.paid":
            kind = "Subscription payment received" if sub_id else "Invoice payment received"
            key = f"platform:{event['livemode']}:{invoice_id}:paid"
        elif event["type"] in FAILURE_EVENTS:
            kind = "Subscription payment failed" if sub_id else "Invoice payment failed"
            key = f"platform:{event['livemode']}:{invoice_id}:{event['type']}:{snapshot.get('attempt_count', 0)}"
        else:
            key = f"platform:{event['id']}"
        amount = snapshot.get("amount_paid", 0) if event["type"] == "invoice.paid" else snapshot.get("amount_due", 0)
        detail = f"Invoice state now: {status}. Reason: {snapshot.get('billing_reason', 'one-time invoice')}."
        if school and school.payment_grace_ends_at:
            detail += f"\nSchool access deadline: {school.payment_grace_ends_at.isoformat()}."
        queue_payment_notice(key=key, school=school, kind=kind, reference=invoice_id,
            amount=amount, currency=snapshot.get("currency", ""), livemode=event["livemode"], detail=detail)
