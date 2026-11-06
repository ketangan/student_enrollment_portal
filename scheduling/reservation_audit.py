"""Reservation-scoped history from durable audits, payments and stock movements."""
from decimal import Decimal
from django.db.models import Q
from core.models import AdminAuditLog
from core.payments.models import Refund
from .models import StockAdjustment
from .message_models import BookingMessage, BookingMessageAttempt
from core.services.email_content import clean_html


LABELS = {
    "materials_recalculated": "Materials recalculated; preparation reset",
    "participant_prepared": "Participant prepared",
    "participant_prep_reopened": "Participant preparation reopened",
    "participant_name_changed": "Participant name updated",
    "terminal_payment_corrected": "Payment record corrected",
    "workshop_selections_completed": "Participant selections saved",
    "workshop_selections_changed": "Participant selections changed",
    "workshop_participant_named": "Participant name updated",
    "internal_notes_updated": "Internal notes updated",
    "internal_note_added": "Internal note added",
    "created": "Reservation created", "checkout_status": "Checkout status updated",
    "canceled": "Reservation canceled", "verified_test_payment": "Payment verified",
    "close_checkout_requested": "Checkout closure requested", "payment_review_confirmed": "Reservation confirmed after payment review",
    "terminal_payment_requested": "Terminal payment requested",
    "refund_approved": "Refund approved", "cancellation_decision": "Cancellation instructions saved",
    "refund_status": "Refund status updated",
    "completed": "Reservation completed",
    "guest_list_submitted": "Guest list submitted",
    "guest_list_draft_saved": "Guest-list progress saved",
}

EMAIL_LABELS = {
    "draft": "Email not sent", "queued": "Email queued", "sending": "Email sending",
    "retry": "Email retry scheduled", "failed": "Email failed",
    "uncertain": "Email delivery needs review", "simulated": "Email simulated, not sent",
    "suppressed": "Email not sent", "superseded": "Email replaced by a newer update",
    "sent": "Email accepted by provider",
}


def group_refund_history(item, entries, refunds):
    """Present one staff request without merging or changing its ledger evidence."""
    entries = list(entries)
    by_refund = {str(refund.public_id): refund for refund in refunds}
    by_entry = {entry.pk: entry for entry in entries if entry.kind == "refund"}
    if not by_entry:
        return entries
    groups = {}
    batches = AdminAuditLog.objects.filter(model_label="scheduling.reservation", object_id=str(item.pk),
        extra__scheduling_profile_id=item.profile_id, changes__action="refund_approved",
        extra__has_key="booking_refund_key").order_by("created_at", "pk").values_list("extra", flat=True)
    for batch in batches:
        ids = batch.get("refunds", [])
        parts = [by_refund[key] for key in ids if key in by_refund]
        if not parts or len(parts) != len(set(ids)) or len(parts) != len(ids):
            continue
        requested = sum(part.amount_cents for part in parts)
        if requested != batch.get("amount_cents"):
            continue
        paid = [by_entry[part.entry_id] for part in parts if part.state == "succeeded" and part.entry_id in by_entry]
        if not paid:
            continue
        returned = sum(entry.amount_cents for entry in paid)
        complete = len(paid) == len(parts)
        active = any(part.state in Refund.ACTIVE for part in parts)
        latest = max(paid, key=lambda entry: entry.created_at)
        row = {"kind": "refund", "channel": latest.channel, "get_channel_display": latest.get_channel_display(),
            "amount": Decimal(returned) / 100, "created_at": latest.created_at, "note": batch.get("description", ""),
            "refund_label": "Refund completed" if complete else "Refund processing" if active else "Refund partially completed",
            "refund_detail": "" if complete else f"${returned / 100:.2f} of ${requested / 100:.2f} returned"}
        for entry in paid:
            groups[entry.pk] = (batch["booking_refund_key"], row)
    result, seen = [], set()
    for entry in entries:
        if entry.pk not in groups:
            result.append(entry)
            continue
        key, row = groups[entry.pk]
        if key not in seen:
            result.append(row)
            seen.add(key)
    return result


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
            elif action in {"workshop_selections_completed", "workshop_selections_changed", "workshop_participant_named",
                    "participant_prepared", "participant_prep_reopened", "participant_name_changed"}:
                detail = log.extra.get("participant_name", "")
            elif action in {"refund_status", "checkout_status"}:
                detail = log.extra.get("status", "")
            elif action == "terminal_payment_requested":
                detail = log.extra.get("terminal", "")
                if log.extra.get("amount_cents"):
                    detail += f' / Collect ${log.extra["amount_cents"] / 100:.2f}'
                if log.extra.get("adjustment_cents"):
                    detail += f' / {log.extra.get("adjustment_kind", "Adjustment").capitalize()}: ${abs(log.extra["adjustment_cents"]) / 100:.2f}'
                if log.extra.get("reason"):
                    detail += f' / {log.extra["reason"]}'
            elif not action:
                detail = "; ".join(f'{key.replace("_", " ").capitalize()}: {value.get("from")} -> {value.get("to")}'
                    for key, value in log.changes.items() if isinstance(value, dict))
        add(log.created_at, f"audit-{log.pk}", title, detail, log.actor)
    for entry in item.order.entries.select_related("actor").order_by("-created_at", "-pk")[:end + 1]:
        if entry.channel in {"terminal", "cash"}:
            method = "Terminal" if entry.channel == "terminal" else "Cash"
            title = f"{method} entry voided (no refund)" if entry.kind == "void" else f"{method} payment recorded by staff"
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
        detail = f'${refund.amount:.2f} / {refund.reason} / {refund.get_state_display()}'
        if refund.reduction_cents:
            detail += f' / Price reduction on successful refund: ${refund.reduction_cents / 100:.2f}'
        add(refund.created_at, f"refund-{refund.pk}", "Refund requested", detail, refund.actor)
    adjustments = StockAdjustment.objects.filter(level__allocations__reservation=item,
        reason=f"Materials used for reservation {item.public_id}").distinct().select_related("actor", "level__material").order_by("-created_at", "-pk")
    for adjustment in adjustments[:end + 1]:
        add(adjustment.created_at, f"stock-{adjustment.pk}", "Materials used",
            f'{-adjustment.delta.normalize():f} {adjustment.level.material.unit} {adjustment.level.material.name}', adjustment.actor)
    def email_event(message, stamp, key, title, actor=None):
        add(stamp, key, title, f"To: {message.recipient} / Subject: {message.subject}", actor)
        if message.document_snapshot:
            rows[-1]["detail"] += f' / Attachment: {message.document_snapshot["title"]} (PDF)'
        rows[-1].update(email_body=clean_html(message.body, links=False) if message.is_html else message.body,
            email_html=message.is_html)

    emails = BookingMessage.objects.filter(profile_id=item.profile_id, reservation=item).select_related("created_by")
    for message in emails.order_by("-created_at", "-pk")[:end + 1]:
        email_event(message, message.created_at, f"email-{message.pk}",
            EMAIL_LABELS[message.state], message.created_by)
    attempts = BookingMessageAttempt.objects.filter(message__reservation=item, message__profile_id=item.profile_id,
        finished_at__isnull=False).select_related("message").order_by("-finished_at", "-pk")
    for attempt in attempts[:end + 1]:
        email_event(attempt.message, attempt.finished_at, f"email-attempt-{attempt.pk}",
            f"Email attempt {attempt.number} - {attempt.outcome_label}")
    rows.sort(key=lambda row: (row["at"], row["key"]), reverse=True)
    return {"rows": rows[end - 25:end], "number": page, "previous": page - 1,
        "next": page + 1, "has_next": len(rows) > end, "has_previous": page > 1}
