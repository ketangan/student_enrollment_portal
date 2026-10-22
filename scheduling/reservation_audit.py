"""Reservation-scoped history from durable audits, payments and stock movements."""
from django.db.models import Q
from core.models import AdminAuditLog
from core.payments.models import Refund
from .models import StockAdjustment


LABELS = {
    "created": "Reservation created", "checkout_status": "Checkout status updated",
    "canceled": "Reservation canceled", "verified_test_payment": "Payment verified",
    "close_checkout_requested": "Checkout closure requested", "payment_review_confirmed": "Reservation confirmed after payment review",
    "refund_approved": "Refund approved", "cancellation_decision": "Cancellation instructions saved",
    "refund_status": "Refund status updated",
    "completed": "Reservation completed",
    "guest_list_submitted": "Guest list submitted",
    "guest_list_draft_saved": "Guest-list progress saved",
}


def audit_page(item, raw_page):
    try:
        page = min(1000, max(1, int(raw_page or 1)))
    except (ValueError, TypeError):
        page = 1
    end = page * 25
    rows = []

    def add(stamp, key, title, detail="", actor=None):
        rows.append({"at": stamp, "key": key, "title": title, "detail": detail,
            "actor": actor.get_username() if actor else "System"})

    logs = AdminAuditLog.objects.filter(model_label="scheduling.reservation", object_id=str(item.pk)).exclude(
        Q(changes__has_key="action") & Q(changes__action__in=["offline_settlement", "terminal_payment_recorded", "terminal_payment_voided"])).select_related("actor").order_by("-created_at", "-pk")
    if not logs.filter(changes__action="created").exists():
        add(item.created_at, "created", "Reservation created")
    # Fetch enough of each source to merge one page, not the entire payment history.
    for log in logs[:end + 1]:
        action = log.changes.get("action", "")
        detail = ""
        if "prepared_at" in log.changes:
            title = "Preparation completed" if log.changes["prepared_at"].get("to") else "Preparation reopened"
        else:
            title = LABELS.get(action, "Reservation updated")
            if action == "cancellation_decision":
                handling = "With refund" if log.extra["resolution"] == "with_refund" else "Without refund"
                detail = f'{handling}: {log.extra["reason"]}'
            elif action in {"refund_status", "checkout_status"}:
                detail = log.extra.get("status", "")
            elif not action:
                detail = "; ".join(f'{key.replace("_", " ").capitalize()}: {value.get("from")} -> {value.get("to")}'
                    for key, value in log.changes.items() if isinstance(value, dict))
        add(log.created_at, f"audit-{log.pk}", title, detail, log.actor)
    for entry in item.order.entries.select_related("actor").order_by("-created_at", "-pk")[:end + 1]:
        if entry.channel == "terminal":
            title = "Terminal entry voided (no refund)" if entry.kind == "void" else "Terminal payment recorded by staff"
            add(entry.created_at, f"entry-{entry.pk}", title,
                f'${entry.amount:.2f}' + (f' / Receipt: {entry.external_reference}' if entry.external_reference else "")
                + (f' / Extra purchases: ${entry.extra_charge_cents / 100:.2f}' if entry.extra_charge_cents else "")
                + f' / Payment date: {entry.paid_on}' + (f' / {entry.note}' if entry.note else ""), entry.actor)
            continue
        title = "Payment received" if entry.kind == "payment" else "Refund recorded"
        add(entry.created_at, f"entry-{entry.pk}", title,
            f'${entry.amount:.2f} / {entry.get_channel_display()} / {entry.reference}', entry.actor)
    for attempt in item.order.attempts.order_by("-created_at", "-pk")[:end + 1]:
        add(attempt.created_at, f"checkout-{attempt.pk}", "Checkout created", f'${attempt.amount:.2f}')
    for refund in Refund.objects.filter(attempt__order=item.order).select_related("actor").order_by("-created_at", "-pk")[:end + 1]:
        add(refund.created_at, f"refund-{refund.pk}", "Refund requested", f'${refund.amount:.2f} / {refund.reason}', refund.actor)
    adjustments = StockAdjustment.objects.filter(level__allocations__reservation=item,
        reason=f"Materials used for reservation {item.public_id}").distinct().select_related("actor", "level__material").order_by("-created_at", "-pk")
    for adjustment in adjustments[:end + 1]:
        add(adjustment.created_at, f"stock-{adjustment.pk}", "Materials used",
            f'{-adjustment.delta.normalize():f} {adjustment.level.material.unit} {adjustment.level.material.name}', adjustment.actor)
    rows.sort(key=lambda row: (row["at"], row["key"]), reverse=True)
    return {"rows": rows[end - 25:end], "number": page, "previous": page - 1,
        "next": page + 1, "has_next": len(rows) > end, "has_previous": page > 1}
