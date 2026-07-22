import copy
import csv
import io
import json
import logging
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, Exists, IntegerField, OuterRef, Q, Value, When
from django.http import Http404, HttpResponse, FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.contrib import messages
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit
from django_ratelimit.exceptions import Ratelimited
from django.core.validators import validate_email as _validate_email
from django.core.exceptions import ValidationError as _ValidationError

from .models import (
    AdminAuditLog,
    AdminPreference,
    DraftSubmission,
    Lead,
    LEAD_SOURCE_CHOICES,
    LEAD_STATUS_CHOICES,
    LEAD_STATUS_CONTACTED,
    LEAD_STATUS_ENROLLED,
    LEAD_STATUS_LOST,
    LEAD_STATUS_NEW,
    LEAD_STATUS_TRIAL_SCHEDULED,
    School,
    Submission,
    SubmissionFile,
)
from .services import feature_flags as ff
from .services.admin_themes import (
    ADMIN_THEMES,
    DEFAULT_THEME_KEY,
    get_themes_for_api,
)
from .services.config_loader import (
    find_email_field_key,
    get_application_fee_config,
    get_forms,
    get_lead_form_config,
    get_program_options,
    load_school_config,
    PROGRAM_FIELD_KEYS,
)
from .services.billing_stripe import (
    create_application_fee_intent,
    retrieve_application_fee_intent,
)
from .services.form_utils import build_option_label_map
from .services.admin_submission_yaml import (
    build_yaml_sections,
    get_submission_status_choices,
    get_submission_workflow_filters,
    get_submission_workflow_transitions,
)
from .services.admin_lead_yaml import (
    get_lead_workflow_filters,
    get_lead_workflow_transitions,
)
from core.admin.audit import log_admin_audit
from .services.programs import inject_db_program_options, get_program_options, has_enrollment_options
from .services.validation import validate_submission
from .services.notifications import (
    send_applicant_confirmation_email,
    send_lead_admin_notification,
    send_lead_confirmation,
    send_resume_link_email,
    send_submission_notification_email,
    send_admin_message,
    _resolve_from_email,
)
from .services.lead_conversion import try_convert_lead
from .services.capacity import check_waitlist, get_capacity_config, get_waitlist_message
from .services.integrations import get_export_configs, normalize_csv_value, resolve_export_row
from .services.ai_summary import generate_ai_summary

_DRAFT_RESEND_COOLDOWN_MINUTES = 5


def _strip_file_fields(form_cfg: dict) -> dict:
    """Return form config with file-type fields removed.
    Admin create/edit forms do not support uploads; stripping avoids
    validate_submission() raising required-field errors for file inputs.
    NOTE: call site must also preserve existing file data during merge.
    """
    filtered = []
    for section in (form_cfg.get("sections") or []):
        fields = [f for f in (section.get("fields") or []) if f.get("type") != "file"]
        if fields:
            filtered.append({**section, "fields": fields})
    return {**form_cfg, "sections": filtered}


def _plain_post_values(post_data, form_cfg: dict) -> dict:
    """Extract submission field values from POST using plain keys (no DYN_PREFIX).

    Used for re-rendering the admin submission form after a validation error so
    that build_yaml_sections can populate field.value via existing_data= without
    falling into the DYN_PREFIX code path (which expects dyn__<key> names that
    the admin form never submits).
    """
    result = {}
    for section in (form_cfg.get("sections") or []):
        for f in (section.get("fields") or []):
            key = f.get("key")
            ftype = (f.get("type") or "text").strip().lower()
            if not key or ftype == "file":
                continue
            if ftype == "multiselect":
                result[key] = post_data.getlist(key)
            elif ftype in ("checkbox", "waiver"):
                result[key] = key in post_data
            else:
                result[key] = post_data.get(key, "")
    return result


# Phase 9: default branding (used when YAML has missing branding keys)
DEFAULT_BRANDING = {
    "logo_url": None,
    "theme": {
        "primary_color": "#111827",
        "accent_color": "#ea580c",
        "background": "#f7f7fb",
        "card": "#ffffff",
        "text": "#111827",
        "muted": "#6b7280",
        "border": "#e5e7eb",
        "radius": "16px",
    },
    "custom_css": None,
    "custom_js": None,
}


def merge_branding(branding_in: dict | None) -> dict:
    branding_in = branding_in or {}

    merged = {
        "logo_url": branding_in.get("logo_url", DEFAULT_BRANDING["logo_url"]),
        "custom_css": branding_in.get("custom_css", DEFAULT_BRANDING["custom_css"]),
        "custom_js": branding_in.get("custom_js", DEFAULT_BRANDING["custom_js"]),
        "theme": DEFAULT_BRANDING["theme"].copy(),
    }

    theme_in = branding_in.get("theme") or {}
    merged["theme"].update(theme_in)

    if not merged["theme"].get("accent_color"):
        merged["theme"]["accent_color"] = DEFAULT_BRANDING["theme"]["accent_color"]
    if not merged["theme"].get("primary_color"):
        merged["theme"]["primary_color"] = merged["theme"]["text"] or DEFAULT_BRANDING["theme"]["text"]

    return merged


# -----------------------------
# Rate-limiting error handler (used as handler429 in urls.py)
# -----------------------------

def ratelimited_error_view(request, _exception=None):
    """
    Shown when a public form endpoint is rate-limited (429 Too Many Requests).
    Registered as handler429 in config/urls.py.
    """
    return render(
        request,
        "429.html",
        {"retry_after": 60},
        status=429,
    )


# -----------------------------
# Apply flow helpers (single + multi form)
# -----------------------------

def _get_or_create_school_from_config(school_slug: str, config, branding: dict) -> School:
    school, _created = School.objects.get_or_create(
        slug=school_slug,
        defaults={
            "display_name": config.display_name,
            "website_url": config.raw.get("school", {}).get("website_url", ""),
            "source_url": config.raw.get("school", {}).get("source_url", ""),
            "logo_url": branding.get("logo_url") or "",
            "theme_primary_color": branding["theme"].get("primary_color") or "",
            "theme_accent_color": branding["theme"].get("accent_color") or "",
        },
    )
    return school


def _save_uploaded_files(submission: Submission, form_cfg: dict, files) -> None:
    """
    Creates SubmissionFile rows for any uploaded file fields in the current form_cfg.
    Keeps everything scoped to the same submission (multi-step safe).
    """
    for section in (form_cfg.get("sections") or []):
        for field in (section.get("fields") or []):
            if (field.get("type") or "").strip().lower() == "file":
                key = field.get("key")
                if not key:
                    continue
                uploaded = files.get(key)
                if uploaded:
                    SubmissionFile.objects.create(
                        submission=submission,
                        field_key=key,
                        file=uploaded,
                        original_name=getattr(uploaded, "name", "") or "",
                        content_type=getattr(uploaded, "content_type", "") or "",
                        size_bytes=getattr(uploaded, "size", 0) or 0,
                    )



def _get_client_ip(request) -> str:
    """Return client IP, preferring X-Forwarded-For (first entry) over REMOTE_ADDR."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or ""


def _strip_waiver_fields(form_cfg: dict) -> dict:
    """Return a deep copy of form_cfg with waiver-type fields removed."""
    cfg = copy.deepcopy(form_cfg)
    for section in (cfg.get("sections") or []):
        section["fields"] = [
            f for f in (section.get("fields") or [])
            if (f.get("type") or "").strip().lower() != "waiver"
        ]
    return cfg


def _inject_waiver_metadata(cleaned: dict, form_cfg: dict, request) -> None:
    """
    Inject audit metadata for agreed waiver fields in-place.
    Stores: __at (UTC-aware ISO 8601), __ip (XFF-aware), __text (wording snapshot),
    and __link_url (if present). Only written when agreed = True.
    """
    now_iso = timezone.now().isoformat()  # UTC-aware ISO 8601
    ip = _get_client_ip(request)
    for section in (form_cfg.get("sections") or []):
        for field in (section.get("fields") or []):
            if (field.get("type") or "").strip().lower() == "waiver":
                key = field.get("key")
                if key and cleaned.get(key):
                    cleaned[f"{key}__at"] = now_iso
                    cleaned[f"{key}__ip"] = ip
                    cleaned[f"{key}__text"] = field.get("text", "")
                    if field.get("link_url"):
                        cleaned[f"{key}__link_url"] = field.get("link_url", "")


def _draft_session_key(school_slug: str) -> str:
    return f"apply_draft_id:{school_slug}"


def _resolve_active_draft(request, school: School, school_slug: str, token: str | None = None):
    """
    Returns the active DraftSubmission for this request, or None.
    Token takes precedence over session. Session is updated if token wins.
    """
    session_key = _draft_session_key(school_slug)

    if token:
        draft = DraftSubmission.objects.filter(token=token, school=school).first()
        if draft and not draft.is_expired() and not draft.is_submitted():
            request.session[session_key] = draft.pk
            return draft
        return None  # expired or submitted — handled by caller

    draft_id = request.session.get(session_key)
    if draft_id:
        draft = DraftSubmission.objects.filter(pk=draft_id, school=school).first()
        if draft and not draft.is_expired() and not draft.is_submitted():
            return draft
        # Stale session reference — clear it silently
        request.session.pop(session_key, None)
    return None


def _save_draft(*, school, form_key, cleaned, config_raw, last_form_key="", draft=None):
    """
    Create or update a DraftSubmission with the given cleaned data.
    Returns the draft instance.
    """
    from .services.notifications import _find_applicant_email
    email = _find_applicant_email(cleaned, config_raw) or ""
    if draft is None:
        new_draft = DraftSubmission(
            school=school,
            form_key=form_key,
            data=dict(cleaned),
            email=email,
            last_form_key=last_form_key,
        )
        new_draft.extend_expiry()
        new_draft.save()
        return new_draft
    # Update existing draft
    merged_data = dict(draft.data or {})
    merged_data.update(cleaned)
    draft.data = merged_data
    draft.email = email or draft.email
    draft.last_form_key = last_form_key or draft.last_form_key
    draft.extend_expiry()
    draft.save()
    return draft


def _maybe_send_resume_email(draft, school):
    """Send resume link email, throttled to once per cooldown window."""
    if draft.last_email_sent_at:
        cooldown = timedelta(minutes=_DRAFT_RESEND_COOLDOWN_MINUTES)
        if timezone.now() - draft.last_email_sent_at < cooldown:
            return False
    sent = send_resume_link_email(draft=draft, school=school)
    if sent:
        draft.last_email_sent_at = timezone.now()
        draft.save(update_fields=["last_email_sent_at"])
    return sent


def _get_next_step_after(config, last_form_key: str) -> str | None:
    """Returns the step key after last_form_key for multi-form, or None if it's the last step."""
    forms = get_forms(config) or {}
    ordered_keys = list(forms.keys())
    if not ordered_keys:
        return None
    if not last_form_key or last_form_key not in ordered_keys:
        return ordered_keys[0]
    idx = ordered_keys.index(last_form_key)
    return ordered_keys[idx + 1] if idx + 1 < len(ordered_keys) else None


def _get_multi_form_context(config, form_key: str):
    """
    Returns: (form_cfg, ordered_keys, next_key)
    - If form_key == "default": caller should redirect to first configured form key.
    """
    forms = get_forms(config) or {}
    ordered_keys = list(forms.keys())

    if not ordered_keys:
        raise Http404("Multi-form config is empty")

    if form_key not in forms:
        raise Http404("Form not found")

    form_cfg = forms[form_key].get("form") or {}
    idx = ordered_keys.index(form_key)
    next_key = ordered_keys[idx + 1] if idx + 1 < len(ordered_keys) else None
    return form_cfg, ordered_keys, next_key


def _apply_form_context(
    *,
    school: School,
    branding: dict,
    form: dict,
    is_multi: bool,
    form_key: str,
    next_key: str | None,
    errors: dict,
    values,
) -> dict:
    # Keep context keys stable across branches (tests + templates rely on these).
    return {
        "school": school,
        "branding": branding,
        "form": form,
        "is_multi": is_multi,
        "form_key": form_key,
        "next_key": next_key,
        "errors": errors,
        "values": values,
    }


def _complete_submission_from_draft(
    *,
    request,
    school: School,
    school_slug: str,
    draft: DraftSubmission,
    raw_config: dict,
    config,
    form_cfg: dict,
    payment_intent_id: str = "",
    payment_status: str = "",
) -> "HttpResponse":
    """
    Finalise a DraftSubmission → Submission, run post-processing, and redirect to success.
    Used by both the normal (no-fee) submit path and the payment confirm path.
    """
    # Duplicate guard: lock the draft and verify it hasn't been submitted yet.
    # Two browser tabs submitting the same form concurrently must produce only one Submission.
    with transaction.atomic():
        _locked = (
            DraftSubmission.objects
            .select_for_update()
            .filter(pk=draft.pk, submitted_at__isnull=True)
            .first()
        )
        if not _locked:
            # Already submitted (race or payment double-confirm) — redirect silently.
            return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

        submission = Submission.objects.create(
            school=school,
            form_key=draft.form_key or "default",
            data=dict(draft.data or {}),
            payment_intent_id=payment_intent_id,
            payment_status=payment_status,
        )
        _locked.submitted_at = timezone.now()
        _locked.save(update_fields=["submitted_at"])

    request.session.pop(_draft_session_key(school_slug), None)

    try:
        try_convert_lead(school=school, submission=submission, config_raw=raw_config, lead=draft.lead)
    except Exception:
        logger.exception("Failed to convert lead for submission %s", submission.public_id)

    if school.features.file_uploads_enabled and request.FILES:
        _save_uploaded_files(submission, form_cfg, request.FILES)

    if school.features.email_notifications_enabled:
        try:
            send_submission_notification_email(
                request=request,
                config_raw=raw_config,
                school_name=config.display_name,
                submission_id=submission.id,
                submission_public_id=submission.public_id,
                student_name=submission.student_display_name(),
                submission_data=submission.data or {},
                school=school,
            )
        except Exception:
            logger.exception("Failed to send submission notification email")
        try:
            _status_url = ""
            if school.features.family_portal_enabled:
                from core.services.url_builder import app_reverse
                _status_url = app_reverse("family_status", kwargs={"school_slug": school_slug, "token": submission.status_token})
            send_applicant_confirmation_email(
                config_raw=raw_config,
                school_name=config.display_name,
                submission_public_id=submission.public_id,
                student_name=submission.student_display_name(),
                submission_data=submission.data or {},
                status_url=_status_url,
                school=school,
            )
        except Exception:
            logger.exception("Failed to send applicant confirmation email")

    # Resolve program/session FK + auto-enroll for DB-driven program schools.
    if school.program_field_key:
        from core.services.programs import resolve_submission_program_and_session, apply_auto_enrollment
        program, session = resolve_submission_program_and_session(school, submission.data or {})
        if program:
            update_fields = ["program"]
            submission.program = program
            if session is not None:
                submission.session = session
                update_fields.append("session")
            submission.save(update_fields=update_fields)
            apply_auto_enrollment(school, submission, program, session=session)
            submission.refresh_from_db(fields=["status"])
            if submission.status == "Waitlisted":
                request.session[_WAITLIST_SESSION_KEY] = True
        else:
            # No DB program match — fall through to YAML capacity check.
            _maybe_set_waitlist_flag(request, school, submission.data or {}, raw_config)
    else:
        _maybe_set_waitlist_flag(request, school, submission.data or {}, raw_config)

    request.session["_enrollify_last_form_key"] = draft.last_form_key or draft.form_key or "default"
    request.session["_enrollify_submission_public_id"] = submission.public_id
    return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))


# -----------------------------
# Apply view (dispatcher)
# -----------------------------

@xframe_options_exempt
@ratelimit(key="ip", rate="30/m", method="POST", block=True)
def apply_view(request, school_slug: str, form_key: str = "default"):
    try:
        config = load_school_config(school_slug)
    except Exception:
        logger.exception("Public config load failed for %r", school_slug)
        raise Http404("School configuration unavailable.")
    if config is None:
        raise Http404("School config not found")

    branding = merge_branding(getattr(config, "branding", None))
    school = _get_or_create_school_from_config(school_slug, config, branding)

    # Block inactive schools from accepting applications
    if not school.is_active:
        raise Http404("School not found")

    # Block expired-trial schools from accepting new applications (GET and POST)
    if school.is_trial_expired:
        return render(request, "trial_expired.html", {
            "school": school,
            "branding": branding,
            "billing_url": reverse("school_billing", kwargs={"school_slug": school.slug}),
        })

    # Strip custom branding assets if the feature is not enabled for this school.
    if not school.features.custom_branding_enabled:
        branding["custom_css"] = None
        branding["custom_js"] = None

    forms = get_forms(config) or {}
    is_multi = len(forms) > 1 and school.features.multi_form_enabled

    # ----------------------------
    # SINGLE-FORM SCHOOL (legacy)
    # ----------------------------
    if not is_multi:
        form_cfg = config.form
        if not school.features.waiver_enabled:
            form_cfg = _strip_waiver_fields(form_cfg)
        # Inject DB program options (replaces YAML options when school.program_field_key is set)
        form_cfg = inject_db_program_options(form_cfg, school, form_key="default")

        save_resume_enabled = school.features.save_resume_enabled
        raw_config = getattr(config, "raw", {}) or {}

        if request.method == "POST":
            # Save-draft action (secondary submit button)
            if request.POST.get("_action") == "save_draft" and save_resume_enabled:
                cleaned, _ = validate_submission(form_cfg, request.POST, request.FILES, partial=True)
                active_draft = _resolve_active_draft(request, school, school_slug)
                draft = _save_draft(
                    school=school, form_key="default", cleaned=cleaned,
                    config_raw=raw_config, draft=active_draft,
                )
                request.session[_draft_session_key(school_slug)] = draft.pk
                if draft.email:
                    sent = _maybe_send_resume_email(draft, school)
                    if sent:
                        messages.success(request, "We've emailed you a link to continue your application.")
                    else:
                        messages.success(request, "Draft saved. We recently sent you a resume link.")
                else:
                    messages.info(request, "Draft saved. Fill in your email to receive a resume link.")
                return redirect(request.path)

            # Normal full submit
            cleaned, errors = validate_submission(form_cfg, request.POST, request.FILES)

            # Block submission when program_field_key is set but no enrollment options exist
            if not errors and school.program_field_key:
                if not has_enrollment_options(school, form_key="default"):
                    errors = errors or {}
                    errors[school.program_field_key] = "No programs are currently available. Please contact the school."

            if errors:
                ctx = _apply_form_context(
                    school=school,
                    branding=branding,
                    form=form_cfg,
                    is_multi=False,
                    form_key="default",
                    next_key=None,
                    errors=errors,
                    values=request.POST,
                )
                ctx["save_resume_enabled"] = save_resume_enabled
                ctx["embed_mode"] = request.GET.get("embed") == "1"
                return render(request, "apply_form.html", ctx)

            _inject_waiver_metadata(cleaned, form_cfg, request)

            # --- Application fee gate ---
            # Use URL form_key for waiver check (single-form schools use "default"; multi-key
            # schools with multi_form_enabled=False still route each key through this branch).
            fee_cfg = get_application_fee_config(raw_config, form_key, form_data=cleaned)
            _stripe_ready = bool(school.app_fee_stripe_public_key) or settings.DEV_SKIP_PAYMENT
            if fee_cfg["enabled"] and not fee_cfg["waived"] and _stripe_ready:
                active_draft = _resolve_active_draft(request, school, school_slug)
                draft = _save_draft(
                    school=school, form_key=form_key, cleaned=cleaned,
                    config_raw=raw_config, last_form_key=form_key, draft=active_draft,
                )
                request.session[_draft_session_key(school_slug)] = draft.pk
                return redirect(reverse(
                    "apply_payment",
                    kwargs={"school_slug": school_slug, "draft_token": draft.token},
                ))

            # No fee (or fee not configured) — create submission immediately.
            # Duplicate-submission guard: two browser tabs opening the same resume URL
            # and submitting sequentially or concurrently must not produce two Submissions.
            _hidden_draft_token = request.POST.get("_draft_token", "").strip()
            active_draft = _resolve_active_draft(request, school, school_slug)

            with transaction.atomic():
                if active_draft:
                    # Re-fetch under a lock so a truly-concurrent second tab must wait.
                    # If it was submitted while we waited, the NULL filter drops it.
                    active_draft = (
                        DraftSubmission.objects
                        .select_for_update()
                        .filter(pk=active_draft.pk, school=school, submitted_at__isnull=True)
                        .first()
                    )
                    if not active_draft:
                        # Another tab won the race and already submitted.
                        return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))
                elif _hidden_draft_token:
                    # Session was cleared (a prior tab already submitted this draft).
                    # Use the hidden token to detect the duplicate.
                    _prior = (
                        DraftSubmission.objects
                        .select_for_update()
                        .filter(token=_hidden_draft_token, school=school)
                        .first()
                    )
                    if _prior and _prior.is_submitted():
                        return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

                submission = Submission.objects.create(
                    school=school,
                    form_key="default",
                    data=cleaned,
                    payment_status="waived" if (fee_cfg["enabled"] and fee_cfg["waived"]) else "",
                )

                # Mark draft submitted inside the same transaction so no second tab
                # can create a Submission between now and the committed mark.
                if active_draft:
                    active_draft.submitted_at = timezone.now()
                    active_draft.save(update_fields=["submitted_at"])

            request.session.pop(_draft_session_key(school_slug), None)

            # Resolve program/session FK + apply auto-enrollment for DB-driven program schools.
            if school.program_field_key:
                from core.services.programs import resolve_submission_program_and_session, apply_auto_enrollment
                program, session = resolve_submission_program_and_session(school, cleaned)
                if program:
                    update_fields = ["program"]
                    submission.program = program
                    if session is not None:
                        submission.session = session
                        update_fields.append("session")
                    submission.save(update_fields=update_fields)
                    apply_auto_enrollment(school, submission, program, session=session)
                    submission.refresh_from_db(fields=["status"])
                    if submission.status == "Waitlisted":
                        request.session[_WAITLIST_SESSION_KEY] = True

            try:
                draft_lead = active_draft.lead if active_draft else None
                try_convert_lead(school=school, submission=submission, config_raw=raw_config, lead=draft_lead)
            except Exception:
                logger.exception("Failed to convert lead for submission %s", submission.public_id)
            if school.features.file_uploads_enabled:
                _save_uploaded_files(submission, form_cfg, request.FILES)
            if school.features.email_notifications_enabled:
                try:
                    send_submission_notification_email(
                        request=request,
                        config_raw=raw_config,
                        school_name=config.display_name,
                        submission_id=submission.id,
                        submission_public_id=submission.public_id,
                        student_name=submission.student_display_name(),
                        submission_data=submission.data or {},
                        school=school,
                    )
                except Exception:
                    logger.exception("Failed to send submission notification email")
                try:
                    _status_url = ""
                    if school.features.family_portal_enabled:
                        from core.services.url_builder import app_reverse
                        _status_url = app_reverse("family_status", kwargs={"school_slug": school_slug, "token": submission.status_token})
                    send_applicant_confirmation_email(
                        config_raw=raw_config,
                        school_name=config.display_name,
                        submission_public_id=submission.public_id,
                        student_name=submission.student_display_name(),
                        submission_data=submission.data or {},
                        status_url=_status_url,
                        school=school,
                    )
                except Exception:
                    logger.exception("Failed to send applicant confirmation email")

            _maybe_set_waitlist_flag(request, school, submission.data or {}, raw_config)
            request.session["_enrollify_last_form_key"] = form_key
            request.session["_enrollify_submission_public_id"] = submission.public_id
            return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

        # GET: pre-populate from session draft
        active_draft = _resolve_active_draft(request, school, school_slug)
        _fee_cfg_get = get_application_fee_config(raw_config, form_key)
        _stripe_ready_get = bool(school.app_fee_stripe_public_key) or settings.DEV_SKIP_PAYMENT
        ctx = _apply_form_context(
            school=school,
            branding=branding,
            form=form_cfg,
            is_multi=False,
            form_key="default",
            next_key=None,
            errors={},
            values=active_draft.data if active_draft else {},
        )
        ctx["save_resume_enabled"] = save_resume_enabled
        ctx["embed_mode"] = request.GET.get("embed") == "1"
        ctx["fee_is_pending"] = _fee_cfg_get["enabled"] and not _fee_cfg_get["waived"] and _stripe_ready_get
        ctx["draft_token"] = active_draft.token if active_draft else ""
        return render(request, "apply_form.html", ctx)

    # ----------------------------
    # MULTI-FORM SCHOOL
    # ----------------------------

    raw_config = getattr(config, "raw", {}) or {}
    save_resume_enabled = school.features.save_resume_enabled

    # If user hits /apply (default), jump to first configured form key
    if is_multi and form_key == "default":
        first_key = next(iter(forms.keys()))
        return redirect(reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": first_key}))

    form_cfg, ordered_keys, next_key = _get_multi_form_context(config, form_key)
    if not school.features.waiver_enabled:
        form_cfg = _strip_waiver_fields(form_cfg)
    # Inject DB program options for multi-form schools
    form_cfg = inject_db_program_options(form_cfg, school, form_key=form_key)

    # GET: pre-populate from active draft (session or token)
    active_draft = _resolve_active_draft(request, school, school_slug)

    if request.method == "POST":
        # Save-draft action (secondary submit button) — mirrors single-form behavior
        if request.POST.get("_action") == "save_draft" and save_resume_enabled:
            cleaned, _ = validate_submission(form_cfg, request.POST, request.FILES, partial=True)
            draft = _save_draft(
                school=school, form_key="multi", cleaned=cleaned,
                config_raw=raw_config, last_form_key=form_key, draft=active_draft,
            )
            request.session[_draft_session_key(school_slug)] = draft.pk
            if draft.email:
                sent = _maybe_send_resume_email(draft, school)
                if sent:
                    messages.success(request, "We've emailed you a link to continue your application.")
                else:
                    messages.success(request, "Draft saved. We recently sent you a resume link.")
            else:
                messages.info(request, "Draft saved. Fill in your email to receive a resume link.")
            return redirect(request.path)

        cleaned, errors = validate_submission(form_cfg, request.POST, request.FILES)

        # Block submission when program_field_key is set but no enrollment options exist
        if not errors and school.program_field_key:
            if not has_enrollment_options(school, form_key=form_key):
                errors = errors or {}
                errors[school.program_field_key] = "No programs are currently available. Please contact the school."

        if errors:
            ctx = _apply_form_context(
                school=school,
                branding=branding,
                form=form_cfg,
                is_multi=True,
                form_key=form_key,
                next_key=next_key,
                errors=errors,
                values=request.POST,
            )
            ctx["save_resume_enabled"] = save_resume_enabled
            ctx["embed_mode"] = request.GET.get("embed") == "1"
            return render(request, "apply_form.html", ctx)

        _inject_waiver_metadata(cleaned, form_cfg, request)

        is_first_step = (ordered_keys[0] == form_key)
        if not is_first_step and active_draft is None:
            # Lost session + no token — restart from beginning
            return redirect(reverse("apply", kwargs={"school_slug": school_slug}))

        draft = _save_draft(
            school=school, form_key="multi", cleaned=cleaned,
            config_raw=raw_config, last_form_key=form_key, draft=active_draft,
        )
        request.session[_draft_session_key(school_slug)] = draft.pk

        # After step 1: email the magic link if feature enabled and email present
        if is_first_step and draft.email and save_resume_enabled:
            _maybe_send_resume_email(draft, school)

        if next_key:
            return redirect(reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": next_key}))

        # Final step — check application fee before creating Submission
        fee_cfg = get_application_fee_config(raw_config, form_key, form_data=draft.data or {})
        _stripe_ready = bool(school.app_fee_stripe_public_key) or settings.DEV_SKIP_PAYMENT
        if fee_cfg["enabled"] and not fee_cfg["waived"] and _stripe_ready:
            return redirect(reverse(
                "apply_payment",
                kwargs={"school_slug": school_slug, "draft_token": draft.token},
            ))

        payment_status = "waived" if (fee_cfg["enabled"] and fee_cfg["waived"]) else ""
        return _complete_submission_from_draft(
            request=request,
            school=school,
            school_slug=school_slug,
            draft=draft,
            raw_config=raw_config,
            config=config,
            form_cfg=form_cfg,
            payment_status=payment_status,
        )

    # GET render
    _stripe_ready_get = bool(school.app_fee_stripe_public_key) or settings.DEV_SKIP_PAYMENT
    if not next_key:
        _fee_cfg_get = get_application_fee_config(raw_config, form_key)
        _fee_is_pending = _fee_cfg_get["enabled"] and not _fee_cfg_get["waived"] and _stripe_ready_get
    else:
        _fee_is_pending = False
    ctx = _apply_form_context(
        school=school,
        branding=branding,
        form=form_cfg,
        is_multi=True,
        form_key=form_key,
        next_key=next_key,
        errors={},
        values=active_draft.data if active_draft else {},
    )
    ctx["save_resume_enabled"] = save_resume_enabled
    ctx["embed_mode"] = request.GET.get("embed") == "1"
    ctx["fee_is_pending"] = _fee_is_pending
    ctx["draft_token"] = active_draft.token if active_draft else ""
    return render(request, "apply_form.html", ctx)


@xframe_options_exempt
def apply_payment_view(request, school_slug: str, draft_token: str):
    """
    Payment page — shown after form completion when an application fee is required.
    Creates a Stripe PaymentIntent and renders the Stripe Elements form.
    """
    try:
        config = load_school_config(school_slug)
    except Exception:
        raise Http404("School configuration unavailable.")
    if config is None:
        raise Http404("School config not found")

    branding = merge_branding(getattr(config, "branding", None))
    school = _get_or_create_school_from_config(school_slug, config, branding)

    if not school.is_active:
        raise Http404("School not found")

    draft = get_object_or_404(DraftSubmission, token=draft_token, school=school)

    if draft.submitted_at:
        return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

    if draft.is_expired():
        return render(request, "apply_expired.html", {"school": school, "branding": branding})

    raw_config = getattr(config, "raw", {}) or {}
    # Use last_form_key (multi-form) or form_key (single-form) for fee lookup
    effective_form_key = draft.last_form_key or draft.form_key or "default"
    fee_cfg = get_application_fee_config(raw_config, effective_form_key, form_data=draft.data or {})

    _stripe_ready = bool(school.app_fee_stripe_public_key) or settings.DEV_SKIP_PAYMENT
    if not (fee_cfg["enabled"] and not fee_cfg["waived"] and _stripe_ready):
        # No fee applicable — complete directly
        form_cfg = config.form if hasattr(config, "form") else {}
        return _complete_submission_from_draft(
            request=request, school=school, school_slug=school_slug,
            draft=draft, raw_config=raw_config, config=config, form_cfg=form_cfg,
        )

    amount_cents = fee_cfg["amount"] * 100
    student_first = (draft.data or {}).get("student_first_name", "")
    student_last = (draft.data or {}).get("student_last_name", "")
    student_name = f"{student_first} {student_last}".strip() or draft.email or "Applicant"

    # Back URL: last form step the user came from.
    last_key = draft.last_form_key or draft.form_key or "default"
    if last_key and last_key not in ("default", "multi"):
        back_url = reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": last_key})
    else:
        back_url = reverse("apply", kwargs={"school_slug": school_slug})

    # Dev bypass: show the payment page UI without creating a Stripe PaymentIntent.
    if settings.DEV_SKIP_PAYMENT and not school.app_fee_stripe_public_key:
        bypass_url = reverse("apply_payment_bypass", kwargs={"school_slug": school_slug, "draft_token": draft_token})
        return render(request, "apply_payment.html", {
            "school": school,
            "school_slug": school_slug,
            "config": config,
            "branding": branding,
            "fee_cfg": fee_cfg,
            "stripe_public_key": "",
            "client_secret": None,
            "confirm_url": "",
            "student_name": student_name,
            "embed_mode": request.GET.get("embed") == "1",
            "dev_bypass_mode": True,
            "bypass_url": bypass_url,
            "back_url": back_url,
        })

    try:
        client_secret, _pi_id = create_application_fee_intent(
            school=school,
            amount_cents=amount_cents,
            metadata={
                "school_slug": school_slug,
                "draft_token": draft_token,
                "student_name": student_name,
            },
        )
    except Exception:
        logger.exception("Failed to create PaymentIntent for school %s", school_slug)
        # Graceful degradation: if Stripe is not reachable, let the submission through
        form_cfg = config.form if hasattr(config, "form") else {}
        return _complete_submission_from_draft(
            request=request, school=school, school_slug=school_slug,
            draft=draft, raw_config=raw_config, config=config, form_cfg=form_cfg,
        )

    from core.services.url_builder import app_reverse
    confirm_url = app_reverse("apply_payment_confirm", kwargs={"school_slug": school_slug, "draft_token": draft_token})

    return render(request, "apply_payment.html", {
        "school": school,
        "school_slug": school_slug,
        "config": config,
        "branding": branding,
        "fee_cfg": fee_cfg,
        "stripe_public_key": school.app_fee_stripe_public_key,
        "client_secret": client_secret,
        "confirm_url": confirm_url,
        "student_name": student_name,
        "embed_mode": request.GET.get("embed") == "1",
        "dev_bypass_mode": False,
        "bypass_url": "",
        "back_url": back_url,
    })


@xframe_options_exempt
def apply_payment_confirm_view(request, school_slug: str, draft_token: str):
    """
    Stripe redirects here after payment. Verifies the PaymentIntent and creates the Submission.
    URL params from Stripe: payment_intent, payment_intent_client_secret, redirect_status
    """
    try:
        config = load_school_config(school_slug)
    except Exception:
        raise Http404("School configuration unavailable.")
    if config is None:
        raise Http404("School config not found")

    branding = merge_branding(getattr(config, "branding", None))
    school = _get_or_create_school_from_config(school_slug, config, branding)

    draft = get_object_or_404(DraftSubmission, token=draft_token, school=school)

    if draft.submitted_at:
        return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

    if draft.is_expired():
        return render(request, "apply_expired.html", {"school": school, "branding": branding})

    payment_intent_id = request.GET.get("payment_intent", "").strip()
    redirect_status = request.GET.get("redirect_status", "")

    if not payment_intent_id:
        return redirect(reverse("apply_payment", kwargs={"school_slug": school_slug, "draft_token": draft_token}))

    # Verify the intent server-side
    try:
        intent = retrieve_application_fee_intent(school=school, payment_intent_id=payment_intent_id)
        intent_status = intent.status
    except Exception:
        logger.exception("Failed to retrieve PaymentIntent %s for school %s", payment_intent_id, school_slug)
        intent_status = ""

    if intent_status != "succeeded":
        raw_config = getattr(config, "raw", {}) or {}
        branding = merge_branding(getattr(config, "branding", None))
        from core.services.url_builder import app_reverse
        return render(request, "apply_payment.html", {
            "school": school,
            "school_slug": school_slug,
            "config": config,
            "branding": branding,
            "fee_cfg": get_application_fee_config(raw_config, draft.last_form_key or draft.form_key or "default", form_data=draft.data or {}),
            "stripe_public_key": school.app_fee_stripe_public_key,
            "client_secret": None,
            "confirm_url": app_reverse("apply_payment_confirm", kwargs={"school_slug": school_slug, "draft_token": draft_token}),
            "student_name": "",
            "payment_error": "Payment was not completed. Please try again.",
            "embed_mode": request.GET.get("embed") == "1",
        })

    raw_config = getattr(config, "raw", {}) or {}
    form_cfg = config.form if hasattr(config, "form") else {}
    return _complete_submission_from_draft(
        request=request,
        school=school,
        school_slug=school_slug,
        draft=draft,
        raw_config=raw_config,
        config=config,
        form_cfg=form_cfg,
        payment_intent_id=payment_intent_id,
        payment_status="paid",
    )


@xframe_options_exempt
@require_http_methods(["POST"])
def apply_payment_bypass_view(request, school_slug: str, draft_token: str):
    """
    Dev-only endpoint: completes a submission without Stripe when DEV_SKIP_PAYMENT=True.
    Hard 404 in production — the settings guard ensures IS_PROD=False before this is reachable.
    """
    if not settings.DEV_SKIP_PAYMENT:
        raise Http404("Not available")

    try:
        config = load_school_config(school_slug)
    except Exception:
        raise Http404("School configuration unavailable.")
    if config is None:
        raise Http404("School config not found")

    branding = merge_branding(getattr(config, "branding", None))
    school = _get_or_create_school_from_config(school_slug, config, branding)
    draft = get_object_or_404(DraftSubmission, token=draft_token, school=school)

    if draft.submitted_at:
        return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

    raw_config = getattr(config, "raw", {}) or {}
    form_cfg = config.form if hasattr(config, "form") else {}
    return _complete_submission_from_draft(
        request=request,
        school=school,
        school_slug=school_slug,
        draft=draft,
        raw_config=raw_config,
        config=config,
        form_cfg=form_cfg,
        payment_status="dev_bypass",
    )


_WAITLIST_SESSION_KEY = "apply_waitlist"


def _maybe_set_waitlist_flag(request, school, submission_data: dict, config_raw: dict) -> None:
    """Set a session flag when the submitted program is now at or over capacity."""
    try:
        if check_waitlist(school, submission_data, config_raw):
            request.session[_WAITLIST_SESSION_KEY] = True
    except Exception:
        logger.exception("Capacity check failed — ignoring")


@xframe_options_exempt
def apply_success_view(request, school_slug: str):
    try:
        config = load_school_config(school_slug)
    except Exception:
        logger.exception("Public config load failed for %r", school_slug)
        raise Http404("School configuration unavailable.")
    if config is None:
        raise Http404("School config not found")

    # Branding defaults (same as apply_view)
    branding = merge_branding(getattr(config, "branding", None))

    # Pull success config from YAML (safe defaults)
    success_cfg = (getattr(config, "raw", None) or {}).get("success", {}) or {}

    # Post-submit redirect: per-form key takes priority over top-level success.redirect_url
    _last_form_key = request.session.pop("_enrollify_last_form_key", "default")
    _forms_cfg = (getattr(config, "raw", None) or {}).get("forms", {}) or {}
    _redirect_url = ""
    if _last_form_key and _last_form_key != "default" and _last_form_key in _forms_cfg:
        _redirect_url = ((_forms_cfg[_last_form_key].get("success") or {}).get("redirect_url") or "").strip()
    if not _redirect_url:
        _redirect_url = (success_cfg.get("redirect_url") or "").strip()
    if _redirect_url:
        return redirect(_redirect_url)

    title = success_cfg.get("title") or "Submitted!"
    message = success_cfg.get("message") or f"Thanks — your application for {config.display_name} has been received."

    next_steps = success_cfg.get("next_steps") or []
    if isinstance(next_steps, str):
        next_steps = [next_steps]
    next_steps = [s for s in next_steps if isinstance(s, str) and s.strip()]

    contact = success_cfg.get("contact") or {}
    contact_name = contact.get("name") or ""
    contact_email = contact.get("email") or ""
    contact_phone = contact.get("phone") or ""

    hours = success_cfg.get("hours") or ""
    response_time = success_cfg.get("response_time") or ""
    hide_resubmit = bool(success_cfg.get("hide_resubmit", False))

    scheduling_cfg = (getattr(config, "raw", None) or {}).get("scheduling") or {}
    scheduling_url = (scheduling_cfg.get("url") or "").strip()
    scheduling_label = (scheduling_cfg.get("label") or "").strip() or "Book a time"

    # Waitlist flag — set by submit flow when program is at capacity.
    on_waitlist = request.session.pop(_WAITLIST_SESSION_KEY, False)
    submission_public_id = request.session.pop("_enrollify_submission_public_id", "")
    waitlist_message = ""
    if on_waitlist:
        raw_config = getattr(config, "raw", None) or {}
        cap_cfg = get_capacity_config(raw_config)
        waitlist_message = get_waitlist_message(cap_cfg) if cap_cfg else ""

    return render(
        request,
        "apply_success.html",
        {
            "school_slug": school_slug,
            "school_name": config.display_name,
            "branding": branding,
            "success_title": title,
            "success_message": message,
            "next_steps": next_steps,
            "contact_name": contact_name,
            "contact_email": contact_email,
            "contact_phone": contact_phone,
            "hours": hours,
            "response_time": response_time,
            "scheduling_url": scheduling_url,
            "scheduling_label": scheduling_label,
            "on_waitlist": on_waitlist,
            "waitlist_message": waitlist_message,
            "hide_resubmit": hide_resubmit,
            "submission_public_id": submission_public_id,
        },
    )


@xframe_options_exempt
def resume_draft_view(request, school_slug: str, token: str):
    try:
        config = load_school_config(school_slug)
    except Exception:
        logger.exception("Public config load failed for %r", school_slug)
        raise Http404("School configuration unavailable.")
    if config is None:
        raise Http404("School config not found")

    branding = merge_branding(getattr(config, "branding", None))
    school = _get_or_create_school_from_config(school_slug, config, branding)

    if not school.is_active:
        raise Http404

    draft = get_object_or_404(DraftSubmission, token=token, school=school)

    # Admin-initiated drafts (created via "Start Enrollment") have a lead FK set.
    # They always work regardless of save_resume_enabled — that flag is for the
    # family-facing save-and-resume feature, not for admin tooling.
    if not draft.lead_id and not school.features.save_resume_enabled:
        raise Http404

    if draft.is_submitted():
        return render(request, "apply_submitted_already.html", {"school": school, "branding": branding})

    if draft.is_expired():
        return render(request, "apply_expired.html", {"school": school, "branding": branding})

    # Token wins — update session so subsequent GETs use this draft
    request.session[_draft_session_key(school_slug)] = draft.pk

    if draft.form_key == "multi":
        next_step = _get_next_step_after(config, draft.last_form_key)
        forms = get_forms(config) or {}
        ordered_keys = list(forms.keys())
        target = next_step or draft.last_form_key or (ordered_keys[0] if ordered_keys else None)
        if not target:
            raise Http404
        return redirect(reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": target}))

    return redirect(reverse("apply", kwargs={"school_slug": school_slug}))


# ---------------------------------------------------------------------------
# Public lead / inquiry form  (/schools/<slug>/lead/)
# ---------------------------------------------------------------------------


def school_trial_page_view(request, school_slug):
    """Marketing-style trial page wrapper with the lead form embedded via iframe."""
    school = get_object_or_404(School, slug=school_slug)
    config = load_school_config(school_slug)
    branding = config.raw.get("branding", {})
    lead_cfg = config.raw.get("leads", {})
    lead_form_url = f"/schools/{school_slug}/lead/?embed=1"
    return render(request, "school_trial_page.html", {
        "school": school,
        "branding": branding,
        "lead_cfg": lead_cfg,
        "lead_form_url": lead_form_url,
    })


# ---------------------------------------------------------------------------

@xframe_options_exempt
@csrf_exempt
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def school_lead_form_view(request, school_slug, form_key=None):
    """
    Lightweight public inquiry form. Embeddable via ?embed=1.
    form_key=None  → legacy /lead/ route, reads from leads: YAML section.
    form_key="foo" → named variant at /lead/foo/, reads from lead_forms.foo.
    """
    from .services.lead_intake import create_or_update_lead

    try:
        config = load_school_config(school_slug)
    except Exception:
        logger.exception("Lead form: config load failed for %r", school_slug)
        raise Http404
    if not config:
        raise Http404

    school = _get_or_create_school_from_config(school_slug, config, merge_branding(config.branding))
    if not school.is_active:
        raise Http404

    branding = merge_branding(config.branding)
    if not school.features.custom_branding_enabled:
        branding["custom_css"] = None
        branding["custom_js"] = None
    embed = request.GET.get("embed") == "1" or request.POST.get("embed") == "1"

    if school.is_trial_expired:
        return render(request, "trial_expired.html", {
            "school": school,
            "branding": branding,
            "billing_url": reverse("school_billing", kwargs={"school_slug": school.slug}),
        })

    raw = config.raw
    lead_cfg = get_lead_form_config(raw, form_key)
    if lead_cfg is None:
        raise Http404  # named variant not defined in YAML
    if school.program_field_key:
        db_opts = get_program_options(school)
        if db_opts:
            for field in lead_cfg["fields"]:
                if field.get("key") == school.program_field_key:
                    field["options"] = db_opts
    program_options = [] if lead_cfg["hide_program_field"] else get_program_options(school)

    src_param = request.GET.get("src", "").strip()[:100]
    errors: dict = {}

    if request.method == "POST":
        if request.POST.get("trap_field"):
            # Honeypot triggered — silent success (don't reward bots with an error)
            return render(request, "lead_form.html", _lead_form_ctx(
                school, config, branding, lead_cfg, program_options, embed,
                success=True,
            ))

        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        interested_in_value = request.POST.get("interested_in_value", "").strip()
        interested_in_label = request.POST.get("interested_in_label", "").strip()
        message = request.POST.get("message", "").strip()
        src = request.POST.get("src", "").strip()[:100] or src_param
        utm_source = request.POST.get("utm_source", "").strip()
        utm_medium = request.POST.get("utm_medium", "").strip()
        utm_campaign = request.POST.get("utm_campaign", "").strip()

        if not lead_cfg.get("name_field_key") and not name:
            errors["name"] = "Name is required."
        if not email:
            errors["email"] = "Email is required."
        elif "@" not in email or "." not in email.split("@")[-1]:
            errors["email"] = "Enter a valid email address."
        if lead_cfg["phone_required"] and not phone:
            errors["phone"] = "Phone number is required."

        # Validate custom fields
        custom_field_values: dict = {}
        for field in lead_cfg["fields"]:
            key = field["key"]
            ftype = field.get("type", "text")
            required = bool(field.get("required", False))
            if ftype == "checkbox":
                val = request.POST.get(key) == "true"
                custom_field_values[key] = val
                if required and not val:
                    errors[key] = "You must check this box to continue."
            else:
                val = request.POST.get(key, "").strip()
                custom_field_values[key] = val
                if required and not val:
                    errors[key] = "This field is required."

        if not errors:
            # When name_field_key is set, use that custom field as the lead name
            name_field_key = lead_cfg.get("name_field_key", "")
            if name_field_key:
                name = custom_field_values.get(name_field_key, "").strip()

            # Auto-map redirect_url_field value → program when hide_program_field is set
            if not interested_in_value and lead_cfg.get("redirect_url_field"):
                ruf = lead_cfg["redirect_url_field"]
                field_val = custom_field_values.get(ruf, "")
                if field_val:
                    interested_in_value = field_val
                    for f in lead_cfg["fields"]:
                        if f["key"] == ruf:
                            for opt in f.get("options", []):
                                if opt.get("value") == field_val:
                                    interested_in_label = opt.get("label", field_val)
                                    break
                            break

            extra_data: dict = {}
            if message:
                extra_data["message"] = message
            if src:
                extra_data["src"] = src
            if custom_field_values:
                extra_data["form_fields"] = custom_field_values
            # Store classification on the data dict for audit/reporting
            if form_key:
                extra_data["form_key"] = form_key
                extra_data["category"] = lead_cfg["category"]
                extra_data["pipeline_visible"] = lead_cfg["pipeline_visible"]

            lead, created = create_or_update_lead(
                school=school,
                name=name,
                email=email,
                phone=phone,
                interested_in_label=interested_in_label,
                interested_in_value=interested_in_value,
                source="website_lead_form",
                utm_source=utm_source,
                utm_medium=utm_medium,
                utm_campaign=utm_campaign,
                data=extra_data,
                form_key=form_key or "",
            )

            log_admin_audit(
                request=request,
                action="add",
                obj=lead,
                changes={},
                extra={
                    "name": "lead_created_from_public_form",
                    "created": created,
                    "source": "website_lead_form",
                    "src": src or None,
                    "program": interested_in_label or None,
                    "form_key": form_key or None,
                    "category": lead_cfg["category"] if form_key else None,
                },
            )

            try:
                send_lead_admin_notification(school=school, lead=lead, config_raw=raw, lead_cfg=lead_cfg)
            except Exception:
                logger.exception("Lead admin notification failed silently, lead=%s", lead.pk)
            try:
                send_lead_confirmation(lead=lead, school_name=config.display_name, config_raw=raw, school=school, lead_cfg=lead_cfg)
            except Exception:
                logger.exception("Lead confirmation failed silently, lead=%s", lead.pk)

            redirect_url = lead_cfg.get("redirect_url", "")
            redirect_url_map = lead_cfg.get("redirect_url_map", {})
            redirect_url_field = lead_cfg.get("redirect_url_field", "")
            if redirect_url_map and redirect_url_field:
                field_val = custom_field_values.get(redirect_url_field, "")
                if field_val and field_val in redirect_url_map:
                    redirect_url = redirect_url_map[field_val]
            if redirect_url:
                if embed:
                    # Try JS top-frame navigation first. On some hosts (Wix, Squarespace) the
                    # browser blocks cross-origin top-frame navigation silently — the script
                    # throws a SecurityError and the iframe goes blank. Render a visible fallback
                    # so the user is never stranded: the JS fires first and wins if it can;
                    # if blocked, they see a success message + button that opens Fons in a new tab.
                    from django.http import HttpResponse
                    safe_url = redirect_url.replace('"', '%22').replace("'", '%27')
                    accent = (branding or {}).get("primary_color", "#0ea5e9")
                    safe_accent = accent.replace('"', '').replace("'", '') if accent else "#0ea5e9"
                    return HttpResponse(
                        f'''<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     display:flex;align-items:center;justify-content:center;
     min-height:100vh;background:#f9fafb;padding:24px;text-align:center;}}
.wrap{{max-width:360px;width:100%;}}
.check{{font-size:36px;margin-bottom:14px;color:{safe_accent};}}
.msg{{font-size:16px;color:#374151;line-height:1.6;margin-bottom:28px;}}
.btn{{display:inline-block;padding:14px 28px;background:{safe_accent};color:#fff;
      text-decoration:none;border-radius:8px;font-weight:600;font-size:15px;}}
</style>
</head>
<body>
<div class="wrap">
  <div class="check">&#10003;</div>
  <p class="msg">We received your information!<br>Click below to schedule your trial lesson.</p>
  <a href="{safe_url}" target="_top" class="btn">Continue to Scheduling &rarr;</a>
</div>
<script>try{{window.top.location.replace("{safe_url}");}}catch(e){{}}</script>
</body>
</html>''',
                        content_type="text/html",
                    )
                from django.http import HttpResponseRedirect
                return HttpResponseRedirect(redirect_url)

            return render(request, "lead_form.html", _lead_form_ctx(
                school, config, branding, lead_cfg, program_options, embed,
                success=True,
            ))

        # POST with validation errors — fall through to re-render with errors
        utm_source = request.POST.get("utm_source", "")
        utm_medium = request.POST.get("utm_medium", "")
        utm_campaign = request.POST.get("utm_campaign", "")
    else:
        utm_source = request.GET.get("utm_source", "")
        utm_medium = request.GET.get("utm_medium", "")
        utm_campaign = request.GET.get("utm_campaign", "")

    return render(request, "lead_form.html", {
        **_lead_form_ctx(school, config, branding, lead_cfg, program_options, embed),
        "errors": errors,
        "form_data": request.POST if errors else {},
        "src": src_param,
        "utm_source": utm_source,
        "utm_medium": utm_medium,
        "utm_campaign": utm_campaign,
    })


def _lead_form_ctx(school, config, branding, lead_cfg, program_options, embed, *, success=False):
    return {
        "school": school,
        "school_name": config.display_name,
        "branding": branding,
        "program_options": program_options,
        "form_title": lead_cfg["form_title"],
        "form_description": lead_cfg["form_description"],
        "cta_text": lead_cfg["cta_text"],
        "success_message": lead_cfg["success_message"],
        "custom_fields": lead_cfg.get("fields") or [],
        "name_field_key": lead_cfg.get("name_field_key", ""),
        "phone_required": lead_cfg.get("phone_required", False),
        "embed": embed,
        "success": success,
        "errors": {},
        "form_data": {},
        "src": "",
        "utm_source": "",
        "utm_medium": "",
        "utm_campaign": "",
    }


# ---------------------------------------------------------------------------
# Lead capture (public)  — legacy /interest/ URL, kept for backward-compat
# ---------------------------------------------------------------------------

@xframe_options_exempt
@csrf_exempt
@ratelimit(key="ip", rate="20/m", method="POST", block=True)
def lead_capture_view(request, school_slug):
    try:
        config = load_school_config(school_slug)
    except Exception:
        logger.exception("Public config load failed for %r", school_slug)
        raise Http404("School configuration unavailable.")
    if not config:
        raise Http404

    school = _get_or_create_school_from_config(school_slug, config, merge_branding(config.branding))
    if not school.is_active:
        raise Http404
    if not school.features.leads_enabled:
        raise Http404

    branding = merge_branding(config.branding)

    # Block expired-trial schools from capturing new leads (GET and POST)
    if school.is_trial_expired:
        return render(request, "trial_expired.html", {
            "school": school,
            "branding": branding,
            "billing_url": reverse("school_billing", kwargs={"school_slug": school.slug}),
        })

    leads_cfg = config.raw.get("leads") or {}
    program_options = get_program_options(school)

    errors: dict[str, str] = {}

    if request.method == "POST":
        # ── Honeypot — silent reject ──────────────────────────────────────
        if request.POST.get("trap_field"):
            return redirect(reverse("lead_capture_success", kwargs={"school_slug": school_slug}))

        name = request.POST.get("name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        interested_in_value = request.POST.get("interested_in_value", "").strip()
        interested_in_label = request.POST.get("interested_in_label", "").strip()
        source = request.POST.get("source", "").strip() or "website"
        utm_source = request.POST.get("utm_source", "").strip()
        utm_medium = request.POST.get("utm_medium", "").strip()
        utm_campaign = request.POST.get("utm_campaign", "").strip()

        # ── Validation ────────────────────────────────────────────────────
        if not name:
            errors["name"] = "Name is required."
        if not email:
            errors["email"] = "Email is required."
        elif "@" not in email or "." not in email.split("@")[-1]:
            errors["email"] = "Enter a valid email address."

        if not errors:
            normalized = email.lower().strip()

            # ── Race-condition-safe dedup ─────────────────────────────────
            # Dedup key: school + normalized_email (no time window).
            # Tradeoff: a parent using one email for two children appears as
            # a single lead. Acceptable for Feature 3.
            #
            # Strategy:
            #   1. Wrap in atomic() so select_for_update locks any found row,
            #      preventing two concurrent UPDATES from racing.
            #   2. If no row exists, attempt CREATE. The DB-level
            #      UniqueConstraint on (school, normalized_email) guarantees
            #      only one concurrent INSERT wins; the loser gets an
            #      IntegrityError, which we catch and handle as an update.
            def _apply_merge(lead):
                lead.name = name
                if phone:
                    lead.phone = phone
                if interested_in_label:
                    lead.interested_in_label = interested_in_label
                    lead.interested_in_value = interested_in_value
                # UTM: latest-touch attribution (intentional overwrite)
                lead.utm_source = utm_source or lead.utm_source
                lead.utm_medium = utm_medium or lead.utm_medium
                lead.utm_campaign = utm_campaign or lead.utm_campaign
                if lead.status == LEAD_STATUS_LOST:
                    lead.status = LEAD_STATUS_NEW
                lead.save()

            try:
                with transaction.atomic():
                    existing = Lead.objects.select_for_update().filter(
                        school=school,
                        normalized_email=normalized,
                    ).order_by("-created_at").first()

                    if existing:
                        _apply_merge(existing)
                    else:
                        Lead.objects.create(
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
                        )
            except IntegrityError:
                # Two concurrent requests both saw no existing row and both
                # tried to INSERT. We lost the race — fetch the winner's row
                # and apply our data on top of it.
                existing = Lead.objects.filter(
                    school=school, normalized_email=normalized
                ).order_by("-created_at").first()
                if existing:
                    _apply_merge(existing)

            return redirect(reverse("lead_capture_success", kwargs={"school_slug": school_slug}))

    # GET (or POST with errors)
    context = {
        "school": school,
        "school_name": config.display_name,
        "branding": branding,
        "program_options": program_options,
        "source_choices": LEAD_SOURCE_CHOICES,
        "cta_text": leads_cfg.get("cta_text") or "I'm interested",
        "errors": errors,
        # Preserve POST values on re-render
        "form_data": request.POST if errors else {},
        # UTM pass-through: GET params → hidden inputs
        "utm_source": request.GET.get("utm_source", "") if request.method == "GET" else request.POST.get("utm_source", ""),
        "utm_medium": request.GET.get("utm_medium", "") if request.method == "GET" else request.POST.get("utm_medium", ""),
        "utm_campaign": request.GET.get("utm_campaign", "") if request.method == "GET" else request.POST.get("utm_campaign", ""),
    }
    return render(request, "lead_form.html", context)


@xframe_options_exempt
def lead_capture_success_view(request, school_slug):
    try:
        config = load_school_config(school_slug)
    except Exception:
        logger.exception("Public config load failed for %r", school_slug)
        raise Http404("School configuration unavailable.")
    if not config:
        raise Http404

    branding = merge_branding(config.branding)
    leads_cfg = config.raw.get("leads") or {}
    success_message = leads_cfg.get("success_message") or "Thanks for your interest! We'll be in touch soon."
    apply_url = reverse("apply", kwargs={"school_slug": school_slug})

    scheduling_cfg = (config.raw or {}).get("scheduling") or {}
    scheduling_url = (scheduling_cfg.get("url") or "").strip()
    scheduling_label = (scheduling_cfg.get("label") or "").strip() or "Book a time"

    return render(request, "lead_success.html", {
        "school_name": config.display_name,
        "branding": branding,
        "success_message": success_message,
        "apply_url": apply_url,
        "scheduling_url": scheduling_url,
        "scheduling_label": scheduling_label,
    })


# ---------------------------------------------------------------------------
# Family status page — token-based, no login required
# ---------------------------------------------------------------------------

@xframe_options_exempt
def family_status_view(request, school_slug: str, token: str):
    """
    Public (no auth) status page for an applicant family.
    URL: /schools/<slug>/status/<token>/
    Returns 404 for unknown tokens — no enumeration vector.
    """
    school = get_object_or_404(School, slug=school_slug)

    # Check feature flag; if disabled fall back to a plain 404 so the URL
    # doesn't leak any information about the school's configuration.
    if not school.features.family_portal_enabled:
        raise Http404

    submission = get_object_or_404(Submission.objects.select_related("program"), school=school, status_token=token)

    config = None
    try:
        config = load_school_config(school_slug)
    except Exception:
        pass  # Treat missing config as no branding / default status labels

    branding = merge_branding(getattr(config, "branding", None) if config else None)
    school_name = (getattr(config, "display_name", None) if config else None) or school.display_name or school.slug

    # submission.status is already the human-readable string (matches YAML list entries).
    status_label = submission.status or "Pending"

    data = submission.data or {}
    sched_fields = _extract_sched_fields(data)
    student_info = _extract_student_info(data, submission)
    change_requested = submission.schedule_change_requested

    return render(request, "family_status.html", {
        "school": school,
        "school_name": school_name,
        "branding": branding,
        "submission": submission,
        "status_label": status_label,
        "public_notes": submission.public_notes or "",
        "sched_fields": sched_fields,
        "student_info": student_info,
        "change_requested": change_requested,
    })


def _extract_student_info(data: dict, submission) -> list[dict]:
    """Return key student fields for display on the family status page."""
    rows = []
    first = str(data.get("student_first_name") or "").strip()
    last = str(data.get("student_last_name") or "").strip()
    name = " ".join(filter(None, [first, last]))
    if not name:
        name = submission.student_display_name() or ""
    if name:
        rows.append({"label": "Student", "value": name})

    # Instrument: prefer DB program name, fall back to stripping internal prefix
    if submission.program_id and submission.program:
        rows.append({"label": "Instrument", "value": submission.program.name})
    else:
        raw = str(data.get("instrument") or "").strip()
        if raw:
            if raw.startswith("program:"):
                raw = raw[len("program:"):].replace("-", " ").title()
            rows.append({"label": "Instrument", "value": raw})

    for key, label in [("enrollment_type", "Enrollment Type")]:
        val = str(data.get(key) or "").strip()
        if val:
            rows.append({"label": label, "value": val})
    return rows


def _extract_sched_fields(data: dict) -> list[dict]:
    """Return a list of {label, value} dicts for sched_* keys that have a value."""
    _labels = {
        "sched_day_preference": "Day preference",
        "sched_preferred_timing": "Preferred time",
        "sched_days_unavailable": "Days unavailable",
        "sched_preferred_slot": "Ideal slot",
        "sched_preferred_start_week": "Start week",
    }
    result = []
    for key, label in _labels.items():
        raw = data.get(key)
        if raw:
            if isinstance(raw, list):
                value = ", ".join(str(v) for v in raw)
                result.append({"key": key, "label": label, "value": value, "items": [str(v) for v in raw]})
            else:
                result.append({"key": key, "label": label, "value": str(raw), "items": None})
    return result


# ---------------------------------------------------------------------------
# Family status login — last name + application ID → redirect to token URL
# ---------------------------------------------------------------------------

@ratelimit(key="ip", rate="10/m", method="POST", block=True)
@xframe_options_exempt
def school_status_login_view(request, school_slug: str):
    school = get_object_or_404(School, slug=school_slug)

    if not school.features.family_portal_enabled:
        raise Http404

    config = None
    try:
        config = load_school_config(school_slug)
    except Exception:
        pass
    branding = merge_branding(getattr(config, "branding", None) if config else None)
    school_name = (getattr(config, "display_name", None) if config else None) or school.display_name or school.slug

    error = None

    if request.method == "POST":
        raw_id = (request.POST.get("application_id") or "").strip().upper()
        raw_last = (request.POST.get("last_name") or "").strip().lower()

        if not raw_id or not raw_last:
            error = "Please enter both your Application ID and last name."
        else:
            try:
                submission = Submission.objects.get(school=school, public_id__iexact=raw_id)
                # Accept match against any *last_name* key in submission.data (case-insensitive).
                data = submission.data or {}
                last_name_values = [
                    str(v).strip().lower()
                    for k, v in data.items()
                    if "last_name" in k and v
                ]
                if raw_last in last_name_values:
                    return redirect(
                        reverse("family_status", kwargs={"school_slug": school_slug, "token": submission.status_token})
                    )
                else:
                    error = "We couldn't find a match. Please check your Application ID and last name."
            except Submission.DoesNotExist:
                error = "We couldn't find a match. Please check your Application ID and last name."

    return render(request, "school_status_login.html", {
        "school": school,
        "school_name": school_name,
        "branding": branding,
        "error": error,
    })


# ---------------------------------------------------------------------------
# Schedule change request — POST from family_status page
# ---------------------------------------------------------------------------

@require_http_methods(["POST"])
@xframe_options_exempt
def school_status_change_request_view(request, school_slug: str, token: str):
    school = get_object_or_404(School, slug=school_slug)

    if not school.features.family_portal_enabled:
        raise Http404

    submission = get_object_or_404(Submission, school=school, status_token=token)

    # Update sched_* fields in submission.data.
    sched_keys = [
        "sched_day_preference",
        "sched_preferred_timing",
        "sched_days_unavailable",
        "sched_preferred_slot",
        "sched_preferred_start_week",
    ]
    data = dict(submission.data or {})
    for key in sched_keys:
        val = request.POST.getlist(key)
        if len(val) == 1:
            data[key] = val[0]
        elif len(val) > 1:
            data[key] = val
        else:
            data[key] = ""

    submission.data = data
    submission.schedule_change_requested = True
    submission.schedule_change_requested_at = timezone.now()
    submission.save(update_fields=["data", "schedule_change_requested", "schedule_change_requested_at", "updated_at"])

    # Email Emily (to address from YAML notifications config).
    _notify_schedule_change(school, school_slug, submission)

    return redirect(
        reverse("family_status", kwargs={"school_slug": school_slug, "token": token})
        + "?change=requested"
    )


def _notify_schedule_change(school, school_slug: str, submission):
    """Send email to school's submission notification address about the schedule change request."""
    from core.services.notifications import send_admin_message

    try:
        config = load_school_config(school_slug)
    except Exception:
        return

    raw_to = ""
    try:
        raw_to = (
            config.raw.get("success", {})
            .get("notifications", {})
            .get("submission_email", {})
            .get("to", "")
        ) or ""
    except Exception:
        pass

    recipients = [r.strip() for r in raw_to.split(",") if r.strip()]
    if not recipients:
        return

    school_name = getattr(config, "display_name", None) or school.display_name or school.slug
    student = submission.student_display_name() or submission.public_id
    admin_url = ""
    try:
        from core.services.url_builder import app_reverse
        admin_url = app_reverse(
            "school_submission_detail",
            kwargs={"school_slug": school_slug, "submission_id": submission.id},
        )
    except Exception:
        pass

    sched_fields = _extract_sched_fields(submission.data or {})
    prefs_text = "\n".join(f"  • {f['label']}: {f['value']}" for f in sched_fields) if sched_fields else "  (no preferences provided)"

    message = (
        f"A family has submitted a scheduling change request for {student}.\n\n"
        f"Updated preferences:\n{prefs_text}\n\n"
        f"View submission: {admin_url}"
    )

    for recipient in recipients:
        send_admin_message(
            to_email=recipient,
            subject=f"Scheduling change request — {student}",
            message=message,
            school_name=school_name,
            school=school,
        )
