"""Booking payment failure alerts using the shared operator outbox."""
from django.core.exceptions import ValidationError
from core.services.payment_notifications import queue_payment_notice


def notify_booking_failure(event):
    if event.get("type") != "payment_intent.payment_failed":
        return False
    from core.payments.models import PaymentAttempt
    intent = event["data"]["object"]
    metadata = intent.get("metadata") or {}
    if not metadata.get("payment_attempt"):
        return True
    try:
        attempt = PaymentAttempt.objects.select_related("order__school", "account").get(
            public_id=metadata["payment_attempt"])
    except (PaymentAttempt.DoesNotExist, ValueError) as exc:
        raise ValidationError("Unknown booking payment") from exc
    order = attempt.order
    if (event.get("account") != order.merchant_reference or event.get("livemode") is not False
            or intent.get("livemode") is not False or order.environment != "test"
            or attempt.account_id != order.account_id or attempt.account.school_id != order.school_id
            or metadata.get("payment_order") != str(order.public_id) or metadata.get("purpose") != order.purpose
            or intent.get("amount") != attempt.amount_cents or intent.get("currency") != order.currency
            or not intent.get("id") or not event.get("id")):
        raise ValidationError("Booking payment binding mismatch")
    queue_payment_notice(key=f"booking:{event['account']}:{event['id']}", school=order.school,
        kind="Booking payment failed", reference=intent["id"], amount=attempt.amount_cents,
        currency=order.currency, livemode=False,
        detail="Parent payment. Reservation and school access are unchanged; the customer may retry.")
    return True
