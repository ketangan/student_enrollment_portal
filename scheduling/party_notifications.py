"""Party confirmations and one deadline reminder, using the Bookings outbox."""
import hashlib
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from core.services.notifications import get_school_email_connection
from core.services.url_builder import app_reverse, demo_reverse
from . import guest_lists, messaging
from .capabilities import has_capability
from .guides import MAX_GUIDE_BYTES
from .lessons import digest
from .message_models import BookingMessage
from .models import DesignGuide, Reservation, SchedulingProfile


def attach_guide(email, message):
    """Use the school's current immutable upload at send time, including retries."""
    if not message.reservation_id or message.notice_kind not in {"confirmation", "reminder"}:
        return
    try:
        guide = DesignGuide.objects.filter(profile_id=message.profile_id).first()
        if guide is None:
            return
        with guide.file.open("rb") as source:
            data = source.read(MAX_GUIDE_BYTES + 1)
        if not 0 < len(data) <= MAX_GUIDE_BYTES or hashlib.sha256(data).hexdigest() != guide.checksum:
            raise ValueError("Invalid stored guide")
        email.attach(guide.original_name, data, "application/pdf")
    except Exception as exc:
        # No transport call has happened: storage failures are safe to retry.
        raise messaging.NotAccepted from exc


def live_enabled(profile):
    from .workshop_notifications import enabled
    return (not settings.DEBUG and settings.BOOKINGS_PARTY_EMAIL_ENABLED
            and not profile.school.is_demo and (enabled(profile) or
            profile.enabled and profile.party_notifications_enabled and has_capability(profile, "parties")))


def signature(item):
    return digest({"start": item.starts_at.isoformat(), "end": item.ends_at.isoformat(),
        "recipient": item.party_request.organizer_email, "quantity": item.quantity,
        "room": item.room_id, "due": str(guest_lists.due_at(item))})


def needs_guests(item):
    return not item.prepared_at and sum(row["quantity"] for row in item.party_request.materials) != item.quantity


def deliverable(message):
    item = Reservation.objects.select_related("profile__school", "party_request").get(pk=message.reservation_id)
    if item.workshop_request_id:
        from .workshop_notifications import deliverable as workshop_deliverable
        return workshop_deliverable(message, item)
    return (item.profile_id == message.profile_id and item.profile.enabled
        and item.profile.party_notifications_enabled and has_capability(item.profile, "parties")
        and item.state == "confirmed" and item.starts_at > timezone.now()
        and item.party_request.organizer_email == message.recipient
        and message.notice_signature == signature(item)
        and (message.notice_kind == "confirmation" or (message.notice_kind == "reminder" and needs_guests(item))))


def contents(item, kind):
    school = item.profile.school
    reverse = demo_reverse if school.is_demo else app_reverse
    kwargs = {"school_slug": school.slug}
    title = "Your party is confirmed" if kind == "confirmation" else "Your party guest selections are due"
    when = timezone.localtime(item.starts_at, ZoneInfo(item.snapshot.get("timezone", "UTC")))
    lines = [f"Hi {item.party_request.organizer_name},", "", title + ".",
             f"{school.display_name}", f"{when:%B %d, %Y at %I:%M %p %Z}",
             f"{item.quantity} participants", ""]
    location = item.party_request.offering_snapshot.get("location")
    party_name = item.snapshot.get("party_package", {}).get("name") or item.snapshot.get("workshop")
    if location:
        lines.insert(4, location)
    if party_name:
        lines.insert(4, party_name)
    if item.party_request.offering.inventory_catalog_id:
        due = guest_lists.due_at(item)
        if needs_guests(item):
            if due and due > timezone.now():
                local_due = timezone.localtime(due, when.tzinfo)
                lines.append(f"Please submit your guest selections by {local_due:%B %d, %Y}.")
            else:
                lines.append("Please submit your guest selections now so the team can prepare.")
        lines.extend(["Your private guest-list link:", reverse("scheduling:guest_list", kwargs={
            **kwargs, "token": guest_lists.token_for(item)}), ""])
    if item.profile.design_guides.exists():
        lines.extend(["Design guide:", reverse("scheduling:design_guide", kwargs=kwargs), ""])
    lines.append("Thank you!")
    return f"{school.display_name or school.slug}: {title}"[:200], "\n".join(lines)


@transaction.atomic
def capture_for_reservation(pk):
    original = Reservation.objects.only("profile_id").get(pk=pk)
    profile = SchedulingProfile.objects.select_for_update().select_related("school").get(pk=original.profile_id)
    item = Reservation.objects.select_related("profile__school", "party_request__offering", "order",
        "workshop_request__session__offering__location").get(pk=pk)
    if item.workshop_request_id:
        from .workshop_notifications import capture
        return capture(item)
    if not profile.enabled or not profile.party_notifications_enabled or not has_capability(profile, "parties"):
        return
    if not item.party_request_id or item.state != "confirmed" or item.starts_at <= timezone.now():
        return
    stamp = signature(item)
    def capture(kind):
        event_key = f"party:{item.public_id}:{kind}:{stamp}"
        existing = BookingMessage.objects.filter(profile=profile, event_key=event_key).first()
        if existing:
            return existing
        subject, body = contents(item, kind)
        message = messaging.capture(profile, event_key=event_key, group_key=f"party:{item.public_id}:{kind}",
            recipient=item.party_request.organizer_email, subject=subject, body=body)
        message.reservation, message.notice_kind, message.notice_signature = item, kind, stamp
        if live_enabled(profile) and item.order.environment == "live":
            message.state = "queued"
        message.save()
        return message
    confirmation = capture("confirmation")
    due = guest_lists.due_at(item)
    # A short-notice confirmation already asks for selections now; do not send two emails.
    if (due and confirmation.created_at < due <= timezone.now() and needs_guests(item)
            and item.party_request.offering.inventory_catalog_id):
        capture("reminder")


def process(profile_id, limit=20):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Limit must be between 1 and 20.")
    count = 0
    for _ in range(limit):
        message = messaging.claim(profile_id, party_delivery=True)
        if message is None:
            break
        try:
            if not deliverable(message):
                count += messaging.finish(message, "suppressed")
                continue
            profile = SchedulingProfile.objects.select_related("school").get(pk=profile_id)
            if not live_enabled(profile):
                count += messaging.finish(message, "suppressed")
                continue
            connection = get_school_email_connection(profile.school)
            if connection.__class__.__module__ in {"django.core.mail.backends.console", "django.core.mail.backends.locmem",
                                                   "django.core.mail.backends.dummy", "django.core.mail.backends.filebased"}:
                raise RuntimeError("External mail backend required")
            email = EmailMessage(subject=message.subject, body=message.body,
                from_email=profile.school.smtp_from_email or settings.DEFAULT_FROM_EMAIL,
                to=[message.recipient], headers={"Message-ID": f"<booking-{message.public_id}@mypontora.com>"},
                connection=connection)
            attach_guide(email, message)
            accepted = email.send(fail_silently=False)
            result = "sent" if accepted == 1 else "retry"
        except messaging.NotAccepted:
            result = "retry"
        except Exception:
            result = "uncertain"
        count += messaging.finish(message, result)
    return count
