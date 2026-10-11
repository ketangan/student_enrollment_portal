"""Provider recovery plumbing. No inventory decisions or web-process background threads."""

import uuid
import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

def due_records(model, *, active):
    now = timezone.now()
    return model.objects.filter(state__in=active, recovery_failures__lt=6).filter(
        Q(next_check_at__isnull=True) | Q(next_check_at__lte=now)
    ).filter(Q(lease_until__isnull=True) | Q(lease_until__lte=now)).order_by("next_check_at", "pk")


def run(record, operation, *, force=False):
    """Lease commits before I/O; failed/ambiguous work remains durable and visible."""
    model = type(record)
    now = timezone.now()
    token = uuid.uuid4()
    with transaction.atomic():
        current = model.objects.select_for_update().get(pk=record.pk)
        if current.lease_until and current.lease_until > now:
            return False
        if current.checked_at and current.checked_at > now - timedelta(seconds=30):
            return False
        if not force and (current.recovery_failures >= 6 or (current.next_check_at and current.next_check_at > now)):
            return False
        current.lease_token, current.lease_until = token, now + timedelta(minutes=5)
        current.save(update_fields=["lease_token", "lease_until"])
    record.refresh_from_db()
    try:
        operation(record)
    except Exception as exc:
        # No raw provider errors: they can contain personal data or credentials.
        logger.warning("Payment recovery failed: model=%s record=%s error_type=%s",
            model._meta.label, record.public_id, type(exc).__name__)
        failures = min(current.recovery_failures + 1, 6)
        changes = {"recovery_failures": failures, "attention": (
            "Verification failed. Funds and capacity remain unchanged; retry or investigate with the provider."
            if failures < 6 else "Automatic checks paused after 6 failures. Operator investigation is required."
        ), "next_check_at": timezone.now() + timedelta(minutes=min(2 ** failures, 60))}
        success = False
    else:
        changes = {"recovery_failures": 0, "attention": "", "next_check_at": timezone.now() + timedelta(minutes=5)}
        success = True
    model.objects.filter(pk=record.pk, lease_token=token).update(**changes,
        checked_at=timezone.now(), lease_until=None, lease_token=None)
    return success
