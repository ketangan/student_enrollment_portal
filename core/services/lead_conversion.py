# core/services/lead_conversion.py
from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from core.models import Lead
from core.services.notifications import _find_applicant_email

logger = logging.getLogger(__name__)


def try_convert_lead(*, school, submission, config_raw: dict) -> Lead | None:
    """
    Links the most recent unconverted Lead to a Submission when an email match is found.

    - Requires school.features.leads_conversion_enabled (Pro+)
    - Matches by normalized_email (case-insensitive, exact)
    - When multiple unconverted leads exist for the same email, converts the most
      recently created one (Option A: accept duplicates, pick most recent)
    - Idempotent: already-converted leads are never overwritten
    - Race-safe: uses select_for_update() inside transaction.atomic()
    - Returns the Lead if converted, else None
    """
    if not school.features.leads_conversion_enabled:
        return None

    applicant_email = _find_applicant_email(submission.data or {}, config_raw or {})
    if not applicant_email:
        logger.info("No applicant email found for submission %s", submission.public_id)
        return None

    normalized = applicant_email.lower().strip()
    if not normalized:
        return None

    with transaction.atomic():
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
        # updated_at is auto_now=True; must be listed explicitly in update_fields
        # because Django bypasses auto_now when update_fields is provided.
        lead.save(update_fields=["converted_submission", "converted_at", "updated_at"])

    logger.info("Lead %s converted by submission %s", lead.public_id, submission.public_id)
    return lead
