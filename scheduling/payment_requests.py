"""Staff-requested payment emails reuse the reservation payment link and outbox."""
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.payments.checkout import amount_due, validate_account
from core.payments.service import totals
from . import messaging
from .access import get_staff_reservation
from .checkout import payment_request_url
from .lessons import digest
from .message_models import BookingMessage
from .reservations import locked_profile, _audit

SALT = "scheduling.payment-request.v1"


def latest(item):
    return BookingMessage.objects.filter(reservation=item, notice_kind="payment_request").first()


def eligible(item, amounts=None):
    return bool(item.party_request_id and item.snapshot.get("staff_managed")
        and item.state == "confirmed" and item.order.account_id
        and (amounts or totals(item.order))["outstanding_cents"] > 0)


def signature(item):
    return digest({"recipient": item.party_request.organizer_email, "start": item.starts_at.isoformat(),
        "end": item.ends_at.isoformat(), "room": item.room_id, "quantity": item.quantity,
        "total": item.order.total_cents, "deposit": item.order.deposit_cents,
        "outstanding": totals(item.order)["outstanding_cents"]})


def context_for(user, item, amounts):
    if not item.party_request_id or not item.snapshot.get("staff_managed"):
        return {}
    notice = latest(item)
    token = signing.dumps({"user": user.pk, "reservation": item.pk,
        "previous": notice.pk if notice else None, "key": str(uuid.uuid4())}, salt=SALT)
    return {"payment_request_notice": notice, "can_request_payment": eligible(item, amounts),
        "payment_request_pending": bool(notice and notice.state in {"queued", "retry", "sending", "uncertain"}),
        "payment_request_token": token}


def contents(item):
    school = item.profile.school
    when = timezone.localtime(item.starts_at, ZoneInfo(item.snapshot.get("timezone", "UTC")))
    end = timezone.localtime(item.ends_at, when.tzinfo)
    amounts = totals(item.order)
    due = amount_due(item.order)
    label = "Deposit due" if due < amounts["outstanding_cents"] else "Amount due"
    lines = [f"Hi {item.party_request.organizer_name},", "", "Your party space is reserved.",
        "Please complete your payment using the link below.", "", school.display_name or school.slug,
        item.snapshot.get("party_package", {}).get("name") or item.snapshot["workshop"],
        item.party_request.offering_snapshot.get("location", ""), item.room.name,
        f"{when:%B %d, %Y at %I:%M %p} - {end:%B %d, %Y at %I:%M %p %Z}",
        f"{item.quantity} participants", f"Reservation total: ${amounts['total_cents'] / 100:.2f}",
        f"{label}: ${due / 100:.2f}"]
    if due < amounts["outstanding_cents"]:
        lines.append(f"You can also pay the full remaining amount of ${amounts['outstanding_cents'] / 100:.2f}.")
    lines.extend(["", "Make payment:", payment_request_url(item), "", "Thank you!"])
    return f"{school.display_name or school.slug}: Payment requested"[:200], "\n".join(lines)


@transaction.atomic
def send(user, slug, public_id, token):
    from .party_notifications import live_enabled
    from .capabilities import has_capability
    profile = locked_profile(user, slug)
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    if not eligible(item):
        raise ValidationError("Payment can only be requested for a reserved party with an unpaid balance and online payments enabled.")
    try:
        data = signing.loads(token, salt=SALT, max_age=7200)
        key = uuid.UUID(data["key"])
        if data["user"] != user.pk or data["reservation"] != item.pk:
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError, KeyError) as exc:
        raise ValidationError("Refresh this reservation before requesting payment.") from exc
    event_key = f"payment-request:{item.public_id}:{key}"
    existing = BookingMessage.objects.filter(profile=profile, event_key=event_key).first()
    if existing:
        return existing
    previous = latest(item)
    if data.get("previous") != (previous.pk if previous else None):
        raise ValidationError("Another payment request was created. Refresh to see its status.")
    if previous and previous.state in {"queued", "retry", "sending", "uncertain"}:
        raise ValidationError("A payment request is pending or needs delivery review. Check its status before resending.")
    validate_account(item.order)
    if not profile.party_notifications_enabled or not has_capability(profile, "parties"):
        raise ValidationError("Enable party emails in Settings before requesting payment.")
    live = live_enabled(profile) and item.order.environment == "live"
    if not live and not (settings.DEBUG and item.order.environment == "test"):
        raise ValidationError("Customer email delivery is not enabled for this workspace.")
    subject, body = contents(item)
    message = messaging.capture(profile, event_key=event_key,
        group_key=f"payment-request:{item.public_id}", recipient=item.party_request.organizer_email,
        subject=subject, body=body)
    message.reservation, message.notice_kind, message.notice_signature = item, "payment_request", signature(item)
    if live:
        message.state = "queued"
    message.save()
    _audit(user, item, "payment_requested", message=str(message.public_id))
    return message


def deliverable(message, item):
    from .capabilities import has_capability
    try:
        validate_account(item.order)
    except ValidationError:
        return False
    return (eligible(item) and item.profile_id == message.profile_id and item.profile.enabled
        and item.profile.party_notifications_enabled and has_capability(item.profile, "parties")
        and message.recipient == item.party_request.organizer_email
        and message.notice_signature == signature(item))


def deposit_received(item):
    """Staff confirmation reserves space, but does not count as payment evidence."""
    return (not item.snapshot.get("staff_managed") or
        totals(item.order)["net_cents"] >= (item.order.deposit_cents or item.order.total_cents))
