"""
Shared lead creation/deduplication logic used by:
- school_lead_form_view (public /lead/ form)
- webhook_lead_intake_view (external webhook intake)
"""
from __future__ import annotations

import secrets

from core.models import Lead


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
    form_key: str = "",
) -> tuple[Lead, bool]:
    """
    Always creates a new Lead record.

    Multiple leads per email per school are allowed — a guardian email can represent
    multiple students (different children) or the same student enrolling in multiple
    programs (e.g. piano + guitar). Deduplication is surfaced to admins via a hint
    on the lead detail page rather than enforced at the DB level.

    Returns (lead, True) always. The second value is kept for API compatibility.
    """
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
        data=data or {},
        form_key=form_key,
    )
    return lead, True
