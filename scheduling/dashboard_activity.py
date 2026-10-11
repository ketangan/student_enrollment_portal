"""Read-only, bounded activity from existing booking records and audit events."""
from django.db.models import CharField, Q
from django.db.models.functions import Cast
from django.urls import reverse

from core.models import AdminAuditLog


EVENTS = {
    "offline_settlement": "Offline payment or refund recorded",
    "canceled": "Reservation canceled",
    "verified_test_payment": "Payment verified",
    "close_checkout_requested": "Checkout closure requested",
    "refund_approved": "Refund approved",
    "payment_review_confirmed": "Payment review completed",
    "completed": "Reservation completed",
}


def recent_activity(profile, reservations):
    def event(item, stamp, action, key):
        return {"name": item.snapshot.get("name") or "Customer", "action": action,
            "created_at": stamp, "key": key,
            "url": reverse("scheduling:reservation_detail", kwargs={
                "school_slug": profile.school.slug, "public_id": item.public_id})}

    rows = [event(item, item.created_at, "Reservation created", f"r{item.pk}")
            for item in reservations.order_by("-created_at", "-pk")[:8]]
    # Join to scoped live records, not just JSON metadata supplied to an audit.
    ids = reservations.order_by().annotate(text_id=Cast("pk", CharField())).values("text_id")
    logs = list(AdminAuditLog.objects.filter(model_label="scheduling.reservation", object_id__in=ids)
        .filter(Q(changes__action__in=list(EVENTS)) | Q(changes__has_key="prepared_at"))
        .order_by("-created_at", "-pk")[:8])
    records = {str(item.pk): item for item in reservations.filter(pk__in=[log.object_id for log in logs])}
    for log in logs:
        action = EVENTS.get(log.changes.get("action"))
        if not action:
            action = "Preparation completed" if log.changes["prepared_at"].get("to") else "Preparation reopened"
        rows.append(event(records[log.object_id], log.created_at, action, f"a{log.pk}"))
    return sorted(rows, key=lambda row: (row["created_at"], row["key"]), reverse=True)[:8]
