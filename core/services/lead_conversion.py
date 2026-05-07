# core/services/lead_conversion.py
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from core.models import Lead, LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST
from core.services.notifications import _find_applicant_email

logger = logging.getLogger(__name__)


def try_convert_lead(
    *, school, submission, config_raw: dict, lead: Lead | None = None
) -> Lead | None:
    """
    Links an unconverted Lead to a Submission.

    Two lookup paths:
    - Direct (admin-initiated): caller passes a ``lead`` instance when the draft
      was created via Start Enrollment.  The lead is re-fetched under a lock and
      verified (school scope + not yet converted) before linking.
    - Email-match (self-service): when no lead is supplied the submission data is
      inspected for an email field and matched against Lead.normalized_email.

    Shared guarantees:
    - Requires school.features.leads_conversion_enabled (Pro+)
    - Idempotent: already-converted leads are never overwritten
    - Race-safe: select_for_update() inside transaction.atomic()
    - Returns the Lead if converted, else None
    """
    if not school.features.leads_conversion_enabled:
        return None

    with transaction.atomic():
        if lead is not None:
            # Fast path: admin-initiated enrollment — lead FK known directly.
            # Re-fetch with lock; verify school scoping and unconverted status.
            lead = (
                Lead.objects
                .select_for_update()
                .filter(id=lead.id, school=school, converted_submission__isnull=True)
                .first()
            )
            if not lead:
                return None
        else:
            # Email-match path: self-service form submission.
            applicant_email = _find_applicant_email(submission.data or {}, config_raw or {})
            if not applicant_email:
                logger.info("No applicant email found for submission %s", submission.public_id)
                return None

            normalized = applicant_email.lower().strip()
            lead = (
                Lead.objects
                .select_for_update()
                .filter(
                    school=school,
                    normalized_email=normalized,
                    converted_submission__isnull=True,
                )
                .order_by("-created_at")
                .first()
            )
            if not lead:
                logger.info(
                    "No unconverted lead match for submission %s school=%s email=%s",
                    submission.public_id,
                    school.slug,
                    normalized,
                )
                return None

        lead.converted_submission = submission
        lead.converted_at = timezone.now()
        # Advance pipeline status to enrolled — but do not overwrite a lost lead.
        # A lead may have been marked lost intentionally; respect that designation
        # while still recording the conversion link.
        if lead.status not in (LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST):
            lead.status = LEAD_STATUS_ENROLLED
        # updated_at is auto_now=True; must be listed explicitly in update_fields
        # because Django bypasses auto_now when update_fields is provided.
        lead.save(update_fields=["converted_submission", "converted_at", "status", "updated_at"])

    logger.info("Lead %s converted by submission %s", lead.public_id, submission.public_id)
    return lead
