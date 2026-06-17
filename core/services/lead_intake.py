"""
Shared lead creation/deduplication logic used by:
- school_lead_form_view (public /lead/ form)
- webhook_lead_intake_view (external webhook intake)
"""
from __future__ import annotations

import secrets

from django.db import IntegrityError, transaction

from core.models import Lead, LEAD_STATUS_LOST, LEAD_STATUS_NEW


def ensure_lead_webhook_token(school) -> str:
    """
    Return the school's webhook token, generating one if it doesn't exist yet.
    Call from admin/setup paths only — never called from School.save().
    """
    if school.lead_webhook_token:
        return school.lead_webhook_token
    token = secrets.token_urlsafe(32)
    school.lead_webhook_token = token
    school.save(update_fields=["lead_webhook_token"])
    return token


def create_or_update_lead(
    *,
    school,
    name: str,
    email: str,
    phone: str = "",
    interested_in_label: str = "",
    interested_in_value: str = "",
    source: str = "website_lead_form",
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
    data: dict | None = None,
) -> tuple[Lead, bool]:
    """
    Create a new Lead or merge into an existing one for the same school+email.

    Dedup key: school + normalized_email (DB UniqueConstraint).
    Uses select_for_update to prevent concurrent-update races.

    Returns (lead, created_bool).
    """
    data = data or {}
    normalized = email.lower().strip()

    def _apply(lead: Lead) -> None:
        lead.name = name
        if phone:
            lead.phone = phone
        if interested_in_label:
            lead.interested_in_label = interested_in_label
            lead.interested_in_value = interested_in_value
        lead.utm_source = utm_source or lead.utm_source
        lead.utm_medium = utm_medium or lead.utm_medium
        lead.utm_campaign = utm_campaign or lead.utm_campaign
        if data:
            merged = dict(lead.data or {})
            merged.update(data)
            lead.data = merged
        if lead.status == LEAD_STATUS_LOST:
            lead.status = LEAD_STATUS_NEW
        lead.save()

    try:
        with transaction.atomic():
            existing = (
                Lead.objects.select_for_update()
                .filter(school=school, normalized_email=normalized)
                .order_by("-created_at")
                .first()
            )
            if existing:
                _apply(existing)
                return existing, False
            lead = Lead.objects.create(
                school=school,
                name=name,
                email=email,
                phone=phone,
                interested_in_label=interested_in_label,
                interested_in_value=interested_in_value,
                source=source,
                utm_source=utm_source,
                utm_medium=utm_medium,
                utm_campaign=utm_campaign,
                data=data,
            )
            return lead, True
    except IntegrityError:
        # Two concurrent inserts; the loser catches the winner's row.
        existing = (
            Lead.objects.filter(school=school, normalized_email=normalized)
            .order_by("-created_at")
            .first()
        )
        if existing:
            _apply(existing)
            return existing, False
        raise
