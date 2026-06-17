"""
Webhook endpoints for external lead intake.

POST /webhooks/leads/<school_slug>/<token>/

Accepts JSON or form-encoded payloads from Zapier, Make, WordPress,
Wix, Squarespace automations, or custom HTML forms.
"""
from __future__ import annotations

import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from core.models import School
from core.services.lead_intake import create_or_update_lead
from core.admin.audit import log_admin_audit

logger = logging.getLogger(__name__)

# Flexible field-name aliases (first non-empty match wins)
_NAME_ALIASES    = ("name", "parent_name", "contact_name", "guardian_name", "full_name")
_EMAIL_ALIASES   = ("email", "parent_email", "contact_email", "guardian_email")
_PHONE_ALIASES   = ("phone", "parent_phone", "contact_phone", "guardian_phone")
_STUDENT_ALIASES = ("student_name", "child_name", "student_first_name")
_PROGRAM_ALIASES = ("program", "program_interest", "interested_in", "program_name", "class_interest")
_MESSAGE_ALIASES = ("message", "notes", "comments", "inquiry", "note")
_SOURCE_ALIASES  = ("source", "lead_source", "referral_source")

_ALL_MAPPED = frozenset(
    _NAME_ALIASES + _EMAIL_ALIASES + _PHONE_ALIASES + _STUDENT_ALIASES
    + _PROGRAM_ALIASES + _MESSAGE_ALIASES + _SOURCE_ALIASES
)

MAX_PAYLOAD_BYTES = 50 * 1024  # 50 KB hard cap on raw body


def _pick(data: dict, aliases: tuple) -> str:
    for key in aliases:
        val = data.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return ""


@csrf_exempt
@require_http_methods(["POST"])
def webhook_lead_intake_view(request, school_slug, token):
    # ── Size guard (before DB) ────────────────────────────────────────────────
    content_length = request.META.get("CONTENT_LENGTH")
    try:
        if content_length and int(content_length) > MAX_PAYLOAD_BYTES:
            return JsonResponse({"ok": False, "error": "Payload too large."}, status=400)
    except (ValueError, TypeError):
        pass

    # ── Token auth — 404 on bad token (no info leakage) ──────────────────────
    try:
        school = School.objects.get(
            slug=school_slug,
            lead_webhook_token=token,
            is_active=True,
        )
    except School.DoesNotExist:
        return JsonResponse({"ok": False, "error": "Not found."}, status=404)

    # Extra guard: reject schools whose token is the empty string
    if not school.lead_webhook_token:
        return JsonResponse({"ok": False, "error": "Not found."}, status=404)

    # ── Parse payload: JSON or form-encoded ──────────────────────────────────
    content_type = (request.META.get("CONTENT_TYPE") or "").lower()
    if "application/json" in content_type:
        try:
            body = request.body[:MAX_PAYLOAD_BYTES]
            payload = json.loads(body)
            if not isinstance(payload, dict):
                return JsonResponse(
                    {"ok": False, "error": "JSON payload must be an object."}, status=400
                )
        except (json.JSONDecodeError, ValueError) as exc:
            return JsonResponse({"ok": False, "error": f"Invalid JSON: {exc}"}, status=400)
    else:
        # form-encoded: QueryDict → plain dict (flatten single-element lists)
        payload = {
            k: (v[0] if isinstance(v, list) and len(v) == 1 else v)
            for k, v in request.POST.lists()
        }

    # ── Map known fields ──────────────────────────────────────────────────────
    name            = _pick(payload, _NAME_ALIASES)
    email           = _pick(payload, _EMAIL_ALIASES)
    phone           = _pick(payload, _PHONE_ALIASES)
    student_name    = _pick(payload, _STUDENT_ALIASES)
    program_interest = _pick(payload, _PROGRAM_ALIASES)
    message         = _pick(payload, _MESSAGE_ALIASES)
    source_detail   = _pick(payload, _SOURCE_ALIASES) or "external_form"

    # ── Validation ────────────────────────────────────────────────────────────
    if not name:
        return JsonResponse({"ok": False, "error": "Name is required."}, status=400)
    if not email and not phone:
        return JsonResponse(
            {"ok": False, "error": "At least one of email or phone is required."}, status=400
        )
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        return JsonResponse({"ok": False, "error": "Invalid email address."}, status=400)

    # ── Build lead.data ───────────────────────────────────────────────────────
    extra_fields = {k: v for k, v in payload.items() if k not in _ALL_MAPPED}
    lead_data: dict = {"source_detail": source_detail}
    if student_name:
        lead_data["student_name"] = student_name
    if message:
        lead_data["message"] = message
    if extra_fields:
        lead_data["extra"] = extra_fields

    # ── Create / update lead ──────────────────────────────────────────────────
    lead, created = create_or_update_lead(
        school=school,
        name=name,
        email=email or "",
        phone=phone,
        interested_in_label=program_interest,
        interested_in_value=program_interest,
        source="webhook",
        data=lead_data,
    )

    log_admin_audit(
        request=None,
        action="add",
        obj=lead,
        changes={},
        extra={
            "name": "lead_created_from_webhook",
            "created": created,
            "source_detail": source_detail,
            "program": program_interest or None,
        },
    )

    return JsonResponse({"ok": True, "lead_id": lead.public_id})
