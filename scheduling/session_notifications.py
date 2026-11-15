"""Workshop schedule notices use the existing booking outbox and mail worker."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.utils import timezone

from . import messaging, workshop_notifications
from .lessons import digest
from .message_models import BookingMessage

KINDS = {"rescheduled", "canceled"}


def delivery_enabled(item):
    from .party_notifications import live_enabled
    from .reservation_email import demo_confirmation_allowed
    return (workshop_notifications.enabled(item.profile)
        and ((live_enabled(item.profile) and item.order.environment == "live") or demo_confirmation_allowed(item)))


def signature(item):
    return digest({"booking": workshop_notifications.signature(item), "state": item.state})


def deliverable(message, item):
    return (message.notice_kind in KINDS and workshop_notifications.enabled(item.profile)
        and message.profile_id == item.profile_id and message.notice_signature == signature(item)
        and message.recipient == item.workshop_request.purchaser_email
        and (item.state == "canceled" if message.notice_kind == "canceled" else item.state == "confirmed"))


def capture(item, kind, before, key, *, refund_requested=False, customer_message=""):
    if not workshop_notifications.enabled(item.profile):
        return
    source, school = item.workshop_request, item.profile.school
    stamp = signature(item)
    event_key = f"session:{key}:{item.public_id}:{kind}"
    existing = BookingMessage.objects.filter(profile=item.profile, event_key=event_key).first()
    if existing:
        return existing
    zone = ZoneInfo(source.session.offering.location.timezone_name)
    def when(value):
        return timezone.localtime(value, zone).strftime("%B %d, %Y at %I:%M %p %Z")
    title = "Your workshop has been canceled" if kind == "canceled" else "Your workshop time has changed"
    lines = [f"Hi {source.purchaser_name},", "", title + ".", source.session.offering.name,
        source.session.offering.location.name, f"Previous time: {when(datetime.fromisoformat(before['start']))}"]
    if kind == "rescheduled":
        lines.extend([f"New time: {when(item.starts_at)}", f"Ends: {when(item.ends_at)}",
            f"Room: {item.room.name}" if item.room else "", f"Participants: {item.quantity}",
            "Your agreed price and payments have not changed."])
    elif refund_requested:
        lines.append("A refund has been requested to the original payment method. Processing time depends on the payment provider.")
    if customer_message:
        lines.extend(["", customer_message])
    lines.extend(["", "Please contact the studio with any questions."])
    if school.is_demo:
        lines.extend(["", "This is a test booking, not a real reservation."])
    subject = f"{'[Demo] ' if school.is_demo else ''}{school.display_name or school.slug}: {title}"[:200]
    message = messaging.capture(item.profile, event_key=event_key,
        group_key=f"workshop:{item.public_id}:confirmation", recipient=source.purchaser_email,
        subject=subject, body="\n".join(lines))
    message.reservation, message.notice_kind, message.notice_signature = item, kind, stamp
    if delivery_enabled(item):
        message.state = "queued"
    message.save()
    # The maintenance confirmation sweep must not add a second email for this move.
    item.snapshot["workshop_update_signature"] = workshop_notifications.signature(item)
    item.save(update_fields=["snapshot"])
    return message
