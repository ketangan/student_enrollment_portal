"""Explicit, audited starts for contracts whose trial begins at written go-live."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import AdminAuditLog, School


def start_customer_trial(*, school_id, actor, notice_sent_at, notice_reference):
    if not actor.is_active or not actor.is_superuser:
        raise PermissionDenied
    if (not notice_sent_at or timezone.is_naive(notice_sent_at)
            or notice_sent_at > timezone.now()):
        raise ValidationError("Enter when the written go-live notice was sent, not a future time.")
    notice_reference = (notice_reference or "").strip()
    if not notice_reference or len(notice_reference) > 500:
        raise ValidationError("Provide the sent notice's reference (up to 500 characters).")
    with transaction.atomic():
        school = School.objects.select_for_update().get(pk=school_id)
        if (school.is_demo or not school.is_active or not school.is_trial_plan
                or not school.trial_requires_activation or school.stripe_subscription_id):
            raise ValidationError("Only an active customer awaiting a go-live trial can be started.")
        if school.trial_started_at:
            return False  # A repeat submission must never restart or extend the clock.
        if not 1 <= school.trial_duration_days <= 365:
            raise ValidationError("Trial duration must be between 1 and 365 days.")
        if notice_sent_at + timedelta(days=school.trial_duration_days) <= timezone.now():
            raise ValidationError("That notice would create an already expired trial.")
        school.trial_started_at = notice_sent_at
        school.trial_end_date = None  # Never inherit an old demo expiry override.
        school.save(update_fields=["trial_started_at", "trial_end_date"])
        AdminAuditLog.objects.create(
            actor=actor, action="action", model_label="core.school",
            object_id=str(school.pk), object_repr=school.slug,
            extra={"name": "customer_trial_started", "notice_reference": notice_reference,
                   "notice_sent_at": notice_sent_at.isoformat(),
                   "duration_days": school.trial_duration_days,
                   "ends_at": school.trial_ends_at.isoformat()},
        )
    return True
