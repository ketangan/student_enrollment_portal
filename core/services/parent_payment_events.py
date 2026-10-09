"""School-owned payment alerts. Never changes enrollment billing or entitlement."""

from django.core.exceptions import ValidationError

from core.models import DraftSubmission
from core.services.payment_notifications import queue_payment_notice

INTENT_EVENTS = {"payment_intent.succeeded", "payment_intent.payment_failed"}


def notify_application_payment(school, event):
    if event.get("type") not in INTENT_EVENTS:
        return
    intent = event["data"]["object"]
    metadata = intent.get("metadata") or {}
    # School accounts may also receive unrelated payments: ignore those.
    if not metadata.get("draft_token") or metadata.get("school_slug") != school.slug:
        return
    if not DraftSubmission.objects.filter(school=school, token=metadata["draft_token"]).exists():
        raise ValidationError("Application payment has no matching draft")
    if (type(event.get("livemode")) is not bool or intent.get("livemode") != event["livemode"]
            or not intent.get("id") or not event.get("id") or event.get("account")):
        raise ValidationError("Invalid application payment identity")
    success = event["type"] == "payment_intent.succeeded"
    if success and intent.get("status") != "succeeded":
        raise ValidationError("Payment has not succeeded")
    key = f"application:{school.pk}:{event['livemode']}:{intent['id']}:paid" if success else f"application:{school.pk}:{event['id']}"
    queue_payment_notice(key=key, school=school,
        kind="Application payment received" if success else "Application payment failed",
        reference=intent["id"], amount=intent.get("amount_received" if success else "amount", 0),
        currency=intent.get("currency", ""), livemode=event["livemode"],
        detail="Parent payment. No change to school subscription access.")
