"""Workshop confirmations reuse the reservation outbox and delivery worker."""
from zoneinfo import ZoneInfo

from django.utils import timezone

from core.services.url_builder import app_reverse, demo_reverse
from . import messaging, workshop_followup
from .capabilities import has_capability
from .lessons import digest
from .message_models import BookingMessage


def enabled(profile):
    return profile.enabled and profile.workshop_notifications_enabled and has_capability(profile, "workshops")


def signature(item):
    return digest({"start": item.starts_at.isoformat(), "end": item.ends_at.isoformat(),
        "recipient": item.workshop_request.purchaser_email, "quantity": item.quantity, "room": item.room_id})


def deliverable(message, item):
    return (enabled(item.profile) and item.profile_id == message.profile_id
        and item.state == "confirmed" and item.starts_at > timezone.now()
        and message.notice_kind == "confirmation" and message.recipient == item.workshop_request.purchaser_email
        and message.notice_signature == signature(item))


def capture(item):
    if not enabled(item.profile) or item.state != "confirmed" or item.starts_at <= timezone.now():
        return
    stamp = signature(item)
    key = f"workshop:{item.public_id}:confirmation:{stamp}"
    if BookingMessage.objects.filter(profile=item.profile, event_key=key).exists():
        return
    source, school = item.workshop_request, item.profile.school
    when = timezone.localtime(item.starts_at, ZoneInfo(source.session.offering.location.timezone_name))
    subject = f"{school.display_name or school.slug}: Your workshop is confirmed"[:200]
    body = [f"Hi {source.purchaser_name},", "", "Your workshop is confirmed.",
        source.session.offering.name, source.session.offering.location.name,
        f"{when:%B %d, %Y at %I:%M %p %Z}", f"{item.quantity} participants", ""]
    if source.session.offering.inventory_catalog_id:
        reverse = demo_reverse if school.is_demo else app_reverse
        body.extend(["Choose your designs:", reverse("scheduling:workshop_selections", kwargs={
            "school_slug": school.slug, "token": workshop_followup.token_for(item)}),
            "You can choose before your visit or at the studio, subject to availability.", ""])
    body.append("Thank you!")
    message = messaging.capture(item.profile, event_key=key,
        group_key=f"workshop:{item.public_id}:confirmation", recipient=source.purchaser_email,
        subject=subject, body="\n".join(body))
    message.reservation, message.notice_kind, message.notice_signature = item, "confirmation", stamp
    from .party_notifications import live_enabled
    if live_enabled(item.profile) and item.order.environment == "live":
        message.state = "queued"
    message.save()
    return message
