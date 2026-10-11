"""Transactional outbox with fenced claims and a deliberately local-only transport."""
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.mail.backends.locmem import EmailBackend
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.services.notifications import get_school_email_connection
from .access import require_access
from .lessons import audit, digest
from .message_models import BookingMessage, BookingMessageAttempt
from .models import SchedulingProfile

PENDING = {"draft", "queued", "retry", "failed"}
LOCAL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


def preview_enabled():
    return (settings.DEBUG and getattr(settings, "BOOKINGS_MESSAGE_PREVIEW", False)
            and settings.EMAIL_BACKEND == LOCAL_BACKEND)


def require_preview():
    if not preview_enabled():
        raise Http404


@transaction.atomic
def capture(profile, *, event_key, group_key, recipient, subject, body):
    profile = SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    payload = dict(group_key=group_key, recipient=recipient, subject=subject, body=body)
    fingerprint = digest(payload)
    existing = BookingMessage.objects.filter(profile=profile, event_key=event_key).first()
    if existing:
        if existing.payload_digest != fingerprint:
            raise ValidationError("This message event already exists with different contents.")
        return existing
    message = BookingMessage(profile=profile, event_key=event_key, **payload, payload_digest=fingerprint,
        state="queued" if profile.message_preview_enabled and preview_enabled() else "draft")
    message.full_clean()
    message.save()
    # Never recall an in-flight or accepted message. Preserve ordering until it is resolved.
    BookingMessage.objects.filter(profile=profile, group_key=group_key, state__in=PENDING).exclude(pk=message.pk).update(
        state="superseded", result_code="newer_update")
    return message


def locked_access(user, slug):
    require_preview()
    profile, role = require_access(user, slug, "setup.edit")
    profile = SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    require_access(user, slug, "setup.edit")
    return profile, role


@transaction.atomic
def change_message(user, slug, public_id, action):
    profile, _ = locked_access(user, slug)
    message = get_object_or_404(BookingMessage, profile=profile, public_id=public_id)
    if action == "queue" and message.can_queue:
        if not profile.message_preview_enabled:
            raise ValidationError("Enable local message processing first.")
        message.state, message.result_code = "queued", "manual_queue"
        message.next_attempt_at = timezone.now()
    elif action == "suppress" and message.can_suppress:
        message.state, message.result_code = "suppressed", "staff_suppressed"
    else:
        raise ValidationError("This message changed or this action is unavailable. Refresh the queue.")
    message.save()
    audit(user, profile, message, "change", {"message_action": action})
    return message


def recover_stalled(profile):
    for message in BookingMessage.objects.filter(profile=profile, state="sending",
            lease_expires_at__lte=timezone.now()).order_by("pk")[:20]:
        BookingMessageAttempt.objects.filter(token=message.lease_token, finished_at__isnull=True).update(
            finished_at=timezone.now(), outcome="uncertain", result_code="worker_interrupted")
        message.state, message.result_code = "uncertain", "worker_interrupted"
        message.lease_token = message.lease_expires_at = None
        message.save()


def unresolved_messages(profile, group_key):
    return BookingMessage.objects.filter(
        profile=profile, group_key=group_key, state__in=["sending", "uncertain"]
    ).order_by("pk")


@transaction.atomic
def claim(profile_id):
    require_preview()
    profile = SchedulingProfile.objects.select_for_update().get(pk=profile_id)
    recover_stalled(profile)
    if not profile.enabled or not profile.message_preview_enabled:
        return None
    blocked = unresolved_messages(profile, OuterRef("group_key"))
    message = BookingMessage.objects.filter(profile=profile, state__in=["queued", "retry"],
        next_attempt_at__lte=timezone.now(), attempts__lt=5).annotate(blocked=Exists(blocked)).filter(blocked=False).order_by("pk").first()
    if not message:
        return None
    message.state, message.lease_token = "sending", uuid.uuid4()
    message.lease_expires_at = timezone.now() + timedelta(minutes=2)
    message.attempts += 1
    message.save()
    BookingMessageAttempt.objects.create(message=message, token=message.lease_token, number=message.attempts)
    return message


class NotAccepted(Exception):
    """Definitive rejection before transport acceptance; safe for a bounded retry."""


def local_transport(message, outcome):
    require_preview()
    if outcome == "retry":
        raise NotAccepted
    if outcome == "uncertain":
        raise TimeoutError
    # Reuse the application's mail factory, but never a school's SMTP secrets.
    connection = get_school_email_connection()
    if type(connection) is not EmailBackend:
        raise RuntimeError("Local backend required")
    sent = EmailMessage(subject=message.subject, body=message.body,
        from_email="Pontora Preview <booking-preview@example.invalid>", to=[message.recipient],
        headers={"Message-ID": f"<booking-{message.public_id}@preview.invalid>"}, connection=connection).send(fail_silently=False)
    if sent != 1:
        raise NotAccepted


def recipient_allowed(message):
    from .visit_access import can_deliver
    if not can_deliver(message):
        return False
    from .appointment_models import LessonBookingNotice
    from .family_access import authorized_students
    notice = LessonBookingNotice.objects.filter(delivery=message).select_related("booking__grant").first()
    if notice is None or notice.booking.grant_id is None:
        return True
    grant = notice.booking.grant
    return (not grant.revoked_at and grant.expires_at > timezone.now()
            and grant.recipient_email == message.recipient
            and authorized_students(grant).filter(pk=notice.booking.series.student_id).exists())


@transaction.atomic
def finish(message, result):
    profile = SchedulingProfile.objects.select_for_update().get(pk=message.profile_id)
    current = BookingMessage.objects.get(pk=message.pk)
    if current.state != "sending" or current.lease_token != message.lease_token:
        return False
    if result == "retry":
        newer = BookingMessage.objects.filter(profile=profile, group_key=current.group_key, pk__gt=current.pk).exists()
        current.state = "superseded" if newer else ("retry" if current.attempts < 3 else "failed")
        current.result_code = "newer_update" if newer else "not_accepted"
        current.next_attempt_at = timezone.now() + timedelta(minutes=2 ** current.attempts)
    else:
        current.state = result
        current.result_code = {"simulated": "local_backend_accepted", "suppressed": "recipient_access_ended"}.get(result, "acceptance_unknown")
    current.lease_token = current.lease_expires_at = None
    current.save()
    BookingMessageAttempt.objects.filter(token=message.lease_token).update(finished_at=timezone.now(),
        outcome=result, result_code=current.result_code)
    return True


def process_batch(profile_id, *, limit=20, outcome="success"):
    require_preview()
    if type(limit) is not int or not 1 <= limit <= 20 or outcome not in {"success", "retry", "uncertain"}:
        raise ValidationError("Choose a valid local processing limit and outcome.")
    processed = 0
    for _ in range(limit):
        message = claim(profile_id)
        if message is None:
            break
        try:
            if not recipient_allowed(message):
                processed += finish(message, "suppressed")
                continue
            local_transport(message, outcome)
        except NotAccepted:
            result = "retry"
        except Exception:
            # Exception text can contain recipient data or provider credentials.
            result = "uncertain"
        else:
            result = "simulated"
        processed += finish(message, result)
    return processed
