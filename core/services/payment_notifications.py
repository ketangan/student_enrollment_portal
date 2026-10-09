"""Commit payment facts before sending mail; retry delivery without replaying money."""

import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from core.models import PaymentNotification

logger = logging.getLogger(__name__)


def queue_payment_notice(*, key, school, kind, reference, amount, currency, livemode, detail=""):
    if not livemode and not settings.PAYMENT_ALERT_TEST_EVENTS:
        return None
    recipients = list(dict.fromkeys(settings.PAYMENT_ALERT_RECIPIENTS))
    name = (school.display_name or school.slug) if school else "Unmatched Stripe customer"
    name = " ".join(name.split())
    prefix = "" if livemode else "[TEST] "
    # Amounts stay in provider minor units, avoiding incorrect formatting for JPY/KWD.
    amount_label = f"${Decimal(amount) / 100:,.2f} USD" if currency.lower() == "usd" else f"{amount} {currency.upper()} (minor units)"
    body = (f"{kind}\nSchool: {name}\nReference: {reference}\n"
            f"Amount: {amount_label}\n{detail}")
    notice, _ = PaymentNotification.objects.get_or_create(
        key=key, defaults={"school": school, "recipients": recipients,
            "subject": f"{prefix}[Pontora payments] {kind}: {name}"[:255], "body": body},
    )
    transaction.on_commit(lambda: deliver_payment_notice(notice.pk), robust=True)
    return notice


def deliver_payment_notice(pk):
    # Serialize mail delivery per notice. A provider acceptance followed by a process
    # crash can still cause a duplicate: email delivery is at-least-once, not exactly-once.
    with transaction.atomic():
        notice = PaymentNotification.objects.select_for_update().get(pk=pk)
        if notice.sent_at or notice.next_attempt_at > timezone.now():
            return False
        notice.attempts += 1
        try:
            recipients = list(dict.fromkeys(settings.PAYMENT_ALERT_RECIPIENTS))
            if not recipients:
                raise ImproperlyConfigured("PAYMENT_ALERT_RECIPIENTS must not be empty")
            for recipient in recipients:
                validate_email(recipient)
            notice.recipients = recipients
            if settings.EMAIL_BACKEND.endswith(("console.EmailBackend", "dummy.EmailBackend")):
                raise ImproperlyConfigured("Configure a real email backend before delivery")
            result = EmailMessage(notice.subject, notice.body, settings.DEFAULT_FROM_EMAIL,
                                  notice.recipients).send(fail_silently=False)
            if result != 1:
                raise RuntimeError("Email provider did not accept the message")
        except Exception as exc:
            notice.last_error = type(exc).__name__
            notice.next_attempt_at = timezone.now() + timedelta(minutes=min(2 ** min(notice.attempts, 6), 60))
            logger.error("Payment notification %s failed: %s", notice.pk, notice.last_error)
        else:
            notice.sent_at = timezone.now()
            notice.last_error = ""
        notice.save(update_fields=["recipients", "attempts", "sent_at", "last_error", "next_attempt_at"])
        return bool(notice.sent_at)
