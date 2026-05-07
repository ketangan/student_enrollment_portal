import copy
import csv
import io
import json
import logging
import zipfile
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

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
    get_forms,
    get_program_options,
    load_school_config,
    PROGRAM_FIELD_KEYS,
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
from .services.validation import validate_submission
from .services.notifications import (
    send_applicant_confirmation_email,
    send_resume_link_email,
    send_submission_notification_email,
    send_admin_message,
    send_workflow_notification,
    _resolve_from_email,
)
from .services.lead_conversion import try_convert_lead
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
            "billing_url": reverse("admin:billing"),
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
            submission = Submission.objects.create(school=school, form_key="default", data=cleaned)

            # Mark draft submitted (do NOT delete — magic link shows "already submitted" page)
            active_draft = _resolve_active_draft(request, school, school_slug)
            if active_draft:
                active_draft.submitted_at = timezone.now()
                active_draft.save(update_fields=["submitted_at"])
            request.session.pop(_draft_session_key(school_slug), None)

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
                    )
                except Exception:
                    logger.exception("Failed to send submission notification email")
                try:
                    send_applicant_confirmation_email(
                        config_raw=raw_config,
                        school_name=config.display_name,
                        submission_public_id=submission.public_id,
                        student_name=submission.student_display_name(),
                        submission_data=submission.data or {},
                    )
                except Exception:
                    logger.exception("Failed to send applicant confirmation email")

            return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

        # GET: pre-populate from session draft
        active_draft = _resolve_active_draft(request, school, school_slug)
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

        # Final step: convert draft → Submission
        submission = Submission.objects.create(
            school=school,
            form_key="multi",
            data=dict(draft.data),
        )
        draft.submitted_at = timezone.now()
        draft.save(update_fields=["submitted_at"])
        request.session.pop(_draft_session_key(school_slug), None)

        if school.features.file_uploads_enabled:
            _save_uploaded_files(submission, form_cfg, request.FILES)
        try:
            try_convert_lead(school=school, submission=submission, config_raw=raw_config, lead=draft.lead)
        except Exception:
            logger.exception("Failed to convert lead for submission %s", submission.public_id)
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
                )
            except Exception:
                logger.exception("Failed to send submission notification email")
            try:
                send_applicant_confirmation_email(
                    config_raw=raw_config,
                    school_name=config.display_name,
                    submission_public_id=submission.public_id,
                    student_name=submission.student_display_name(),
                    submission_data=submission.data or {},
                )
            except Exception:
                logger.exception("Failed to send applicant confirmation email")

        return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

    # GET render
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
    return render(request, "apply_form.html", ctx)


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

    scheduling_cfg = (getattr(config, "raw", None) or {}).get("scheduling") or {}
    scheduling_url = (scheduling_cfg.get("url") or "").strip()
    scheduling_label = (scheduling_cfg.get("label") or "").strip() or "Book a time"

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


def _can_view_school_admin_page(request, school: School) -> bool:
    user = request.user
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    membership = getattr(user, "school_membership", None)
    return bool(user.is_staff and membership and membership.school_id == school.id)

@staff_member_required
def admin_download_submission_file(request, file_id: int):
    sf = get_object_or_404(SubmissionFile, id=file_id)

    # Superuser OK, otherwise enforce same-school access
    user = request.user
    if not user.is_superuser:
        membership = getattr(user, "school_membership", None)
        if not (membership and membership.school_id == sf.submission.school_id):
            raise Http404("Not found")

        # Block inactive schools from downloading files
        if not sf.submission.school.is_active:
            raise Http404("Not found")

    if not sf.file:
        raise Http404("Not found")

    # streams from storage (works for local disk now, S3 later)
    stored = (sf.file.name or "").split("/")[-1]
    download_name = sf.original_name or (stored.split("__", 1)[-1] if "__" in stored else stored)

    as_attachment = request.GET.get("download") == "1"
    return FileResponse(sf.file.open("rb"), as_attachment=as_attachment, filename=download_name)

@login_required
def school_reports_view(request, school_slug: str):
    """
    Phase 10 Reports
    URL: /schools/<slug>/admin/reports

    Features:
    - date range filter: last 7/30/90 days (default 30)
    - optional program filter (exact match on display string)
    - export CSV of filtered rows
    - Program "(none)" displayed explicitly as "No program selected"
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    if not request.user.is_superuser and not school.features.reports_enabled:
        return render(
            request,
            "feature_disabled.html",
            {
                "school": school,
                "school_slug": school_slug,
                "feature_name": "Reports",
                "message": "Reports are currently disabled for this school.",
                "required_plan": "Starter",
                "billing_url": reverse("school_billing", kwargs={"school_slug": school_slug}),
            },
            status=403,
        )

    config = _safe_load_school_config(school_slug)
    label_map = build_option_label_map(config.form) if config else {}

    # Filters
    range_raw = (request.GET.get("range") or "30").strip()
    if range_raw not in {"7", "30", "90"}:
        range_raw = "30"
    range_days = int(range_raw)
    since = timezone.now() - timedelta(days=range_days)

    selected_program = (request.GET.get("program") or "").strip()
    export = (request.GET.get("export") or "").strip().lower() in {"1", "true", "yes", "csv"}

    qs = Submission.objects.filter(school=school, created_at__gte=since).order_by("-created_at")

    rows_for_reporting = list(qs[:5000])  # MVP cap

    # Program strings (using same logic as admin list)
    program_strings = []
    for s in rows_for_reporting:
        p = (s.program_display_name(label_map=label_map) or "").strip()
        program_strings.append(p if p else "(none)")

    # Apply program filter after computing strings
    if selected_program:
        filtered_rows = []
        filtered_program_strings = []
        for s, p in zip(rows_for_reporting, program_strings):
            if p == selected_program:
                filtered_rows.append(s)
                filtered_program_strings.append(p)
        rows_for_reporting = filtered_rows
        program_strings = filtered_program_strings

    NONE_LABEL = "No program selected"

    # Export CSV
    csv_enabled = school.features.csv_export_enabled or request.user.is_superuser
    if export and csv_enabled:
        all_keys = set()
        for s in rows_for_reporting:
            all_keys.update((s.data or {}).keys())

        ordered_keys = ["application_id", "created_at", "status", "student_name", "program"] + sorted(all_keys)

        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{school.slug}-reports-last{range_days}d.csv"'

        writer = csv.writer(resp)
        writer.writerow(ordered_keys)

        for s in rows_for_reporting:
            data = s.data or {}
            created = timezone.localtime(s.created_at).isoformat()
            student = s.student_display_name()
            program = (s.program_display_name(label_map=label_map) or "").strip() or NONE_LABEL

            writer.writerow(
                [s.public_id, created, (s.status or ""), student, program]
                + [data.get(k, "") for k in sorted(all_keys)]
            )
        return resp

    # Metrics
    total = len(rows_for_reporting)
    latest = rows_for_reporting[0].created_at if total else None

    counts = Counter(program_strings)
    program_rows = []
    for program_label, c in counts.most_common():
        display_label = NONE_LABEL if program_label == "(none)" else program_label
        pct = (c / total * 100.0) if total else 0.0
        program_rows.append({"label": display_label, "raw": program_label, "count": c, "pct": round(pct, 1)})

    # Schedule breakdown — preferred_time single-select, percent of responses that answered
    sched_label_map = label_map.get("preferred_time", {})
    sched_values = [
        (s.data or {}).get("preferred_time")
        for s in rows_for_reporting
        if (s.data or {}).get("preferred_time")
    ]
    sched_total = len(sched_values)
    sched_counts = Counter(sched_values)
    schedule_rows = []
    for val, c in sched_counts.most_common():
        lbl = sched_label_map.get(val, val)
        pct = (c / sched_total * 100.0) if sched_total else 0.0
        schedule_rows.append({"label": lbl, "count": c, "pct": round(pct, 1)})

    # Enrichment interests breakdown — multiselect, percent of total selections
    enrich_label_map = label_map.get("enrichment_interests", {})
    enrich_counter: Counter = Counter()
    for s in rows_for_reporting:
        interests = (s.data or {}).get("enrichment_interests", [])
        if isinstance(interests, list):
            for v in interests:
                if v:
                    enrich_counter[v] += 1
    enrich_total = sum(enrich_counter.values())
    enrichment_rows = []
    for val, c in enrich_counter.most_common():
        lbl = enrich_label_map.get(val, val)
        pct = (c / enrich_total * 100.0) if enrich_total else 0.0
        enrichment_rows.append({"label": lbl, "count": c, "pct": round(pct, 1)})

    recent = []
    for s in rows_for_reporting[:25]:
        program_label = (s.program_display_name(label_map=label_map) or "").strip() or NONE_LABEL
        recent.append(
            {
                "id": s.id,
                "admin_url": reverse("school_submission_detail", kwargs={"school_slug": school_slug, "submission_id": s.id}),
                "created_at": timezone.localtime(s.created_at),
                "student": s.student_display_name(),
                "program": program_label,
                "status": (s.status or "New"),
            }
        )

    # Lead analytics — only if leads feature is on
    lead_stats = None
    if school.features.leads_enabled:
        leads_qs = Lead.objects.filter(school=school)
        lead_total = leads_qs.count()
        lead_total_in_period = leads_qs.filter(created_at__gte=since).count()

        # Pipeline funnel (all-time current state)
        status_counts = {
            row["status"]: row["c"]
            for row in leads_qs.values("status").annotate(c=Count("id"))
        }
        lead_funnel = []
        for status_val, status_label in LEAD_STATUS_CHOICES:
            count = status_counts.get(status_val, 0)
            pct = round(count / lead_total * 100.0, 1) if lead_total else 0.0
            lead_funnel.append({"status": status_val, "label": status_label, "count": count, "pct": pct})

        # Source breakdown (all-time)
        conversion_enabled = school.features.leads_conversion_enabled or request.user.is_superuser
        source_label_map = dict(LEAD_SOURCE_CHOICES)
        source_data = (
            leads_qs
            .values("source")
            .annotate(count=Count("id"), converted=Count("id", filter=Q(converted_submission__isnull=False)))
            .order_by("-count")
        )
        source_rows = []
        for row in source_data:
            count = row["count"]
            converted = row["converted"]
            source_rows.append({
                "label": source_label_map.get(row["source"], row["source"].replace("_", " ").title()),
                "count": count,
                "converted": converted if conversion_enabled else None,
                "rate": round(converted / count * 100.0, 1) if (count and conversion_enabled) else None,
            })

        # Overall conversion rate
        total_converted = leads_qs.filter(converted_submission__isnull=False).count()
        lead_stats = {
            "total_in_period": lead_total_in_period,
            "total": lead_total,
            "funnel": lead_funnel,
            "sources": source_rows,
            "conversion_enabled": conversion_enabled,
            "total_converted": total_converted if conversion_enabled else None,
            "overall_rate": round(total_converted / lead_total * 100.0, 1) if (lead_total and conversion_enabled) else None,
        }

    # ── Phase 14 — Conversion Intelligence ────────────────────────────────────
    leads_enabled = school.features.leads_enabled
    submissions_url = reverse("school_submissions", kwargs={"school_slug": school_slug})
    leads_url = reverse("school_leads", kwargs={"school_slug": school_slug}) if leads_enabled else ""

    # funnel_metrics: always computed — submissions/enrolled visible to all schools.
    # Lead metrics are added only when leads_enabled (may be None if feature is off).
    _sub_funnel = Submission.objects.filter(school=school).aggregate(
        total=Count("id"),
        enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
    )
    _st = _sub_funnel["total"]
    _se = _sub_funnel["enrolled"]

    _lead_funnel_agg = None
    if leads_enabled:
        _lead_funnel_agg = Lead.objects.filter(school=school).aggregate(
            total=Count("id"),
            converted=Count("id", filter=Q(converted_submission__isnull=False)),
            active=Count(
                "id",
                filter=Q(converted_submission__isnull=True)
                    & ~Q(status__in=[LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST]),
            ),
        )
    _lt = _lead_funnel_agg["total"] if _lead_funnel_agg else None
    _lc = _lead_funnel_agg["converted"] if _lead_funnel_agg else None
    funnel_metrics = {
        "leads": _lt,
        "submissions": _st,
        "enrolled": _se,
        "lead_to_sub_rate": round(_lc / _lt * 100, 1) if (_lt and _lc is not None) else None,
        "sub_to_enrolled_rate": round(_se / _st * 100, 1) if _st else None,
    }

    # sub_status_breakdown: all-time submission counts grouped by status
    sub_status_breakdown = list(
        Submission.objects.filter(school=school)
        .values("status")
        .annotate(count=Count("id"))
        .order_by("-count")
    )

    # trend_stats: last 7 days vs previous 7 days (2–3 aggregate queries)
    _rpt_now = timezone.now()
    _7d_ago = _rpt_now - timedelta(days=7)
    _14d_ago = _rpt_now - timedelta(days=14)

    def _week_trend(this_val: int, last_val: int) -> dict:
        delta = this_val - last_val
        pct = round((delta / last_val) * 100) if last_val else None
        return {"this": this_val, "last": last_val, "delta": delta, "pct": pct, "up": delta >= 0}

    _this_sub = Submission.objects.filter(school=school, created_at__gte=_7d_ago).aggregate(
        subs=Count("id"),
        enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
    )
    _last_sub = Submission.objects.filter(
        school=school, created_at__range=(_14d_ago, _7d_ago)
    ).aggregate(
        subs=Count("id"),
        enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
    )
    _this_leads = (
        Lead.objects.filter(school=school, created_at__gte=_7d_ago).count()
        if leads_enabled else 0
    )
    _last_leads = (
        Lead.objects.filter(school=school, created_at__range=(_14d_ago, _7d_ago)).count()
        if leads_enabled else 0
    )
    trend_stats = {
        "leads": _week_trend(_this_leads, _last_leads) if leads_enabled else None,
        "submissions": _week_trend(_this_sub["subs"], _last_sub["subs"]),
        "enrolled": _week_trend(_this_sub["enrolled"], _last_sub["enrolled"]),
    }

    # pipeline_gaps: "where you're losing people"
    _lost_reasons = (
        list(
            Lead.objects.filter(school=school, status=LEAD_STATUS_LOST)
            .exclude(lost_reason="")
            .values("lost_reason")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        if leads_enabled else []
    )
    pipeline_gaps = {
        "leads_not_converted": _lead_funnel_agg["active"] if _lead_funnel_agg else None,
        "leads_not_converted_url": f"{leads_url}?filter=not_converted" if leads_enabled else None,
        "lost_leads": (
            Lead.objects.filter(school=school, status=LEAD_STATUS_LOST).count()
            if leads_enabled else None
        ),
        "lost_reasons": _lost_reasons,
        "subs_not_enrolled": Submission.objects.filter(school=school).exclude(
            status__in=_TERMINAL_SUBMISSION_STATUSES
        ).count(),
        "subs_not_enrolled_url": f"{submissions_url}?filter=not_enrolled",
    }

    # stale_counts: 5+ days no activity, excluding terminal statuses
    _5d_ago = _rpt_now - timedelta(days=5)
    stale_counts = {
        "submissions": Submission.objects.filter(school=school, updated_at__lte=_5d_ago).exclude(
            status__in=_TERMINAL_SUBMISSION_STATUSES
        ).count(),
        "submissions_url": f"{submissions_url}?filter=stale",
    }
    if leads_enabled:
        stale_counts["leads"] = Lead.objects.filter(
            school=school, updated_at__lte=_5d_ago
        ).exclude(status__in=[LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST]).count()
        stale_counts["leads_url"] = f"{leads_url}?filter=stale"

    base_ctx = _school_admin_base_context(request, school, "reports")
    base_ctx.update(
        {
            "school_slug": school_slug,
            "total": total,
            "latest": timezone.localtime(latest) if latest else None,
            "program_rows": program_rows,
            "recent": recent,
            "selected_program": selected_program,
            "range_days": range_days,
            "csv_export_enabled": csv_enabled,
            "lead_stats": lead_stats,
            "schedule_rows": schedule_rows,
            "enrichment_rows": enrichment_rows,
            "funnel_metrics": funnel_metrics,
            "sub_status_breakdown": sub_status_breakdown,
            "trend_stats": trend_stats,
            "pipeline_gaps": pipeline_gaps,
            "stale_counts": stale_counts,
            "leads_url": leads_url,
            "submissions_url": submissions_url,
            "billing_url": reverse("school_billing", kwargs={"school_slug": school_slug}),
            # True when leads source-conversion upgrade hint should appear
            "leads_conversion_upgrade": (
                leads_enabled
                and not (school.features.leads_conversion_enabled or request.user.is_superuser)
            ),
        }
    )
    return render(request, "reports.html", base_ctx)


# ── School dashboard ─────────────────────────────────────────────────────

_STATUS_CSS = {
    "new": "dash-badge--blue",
    "tour scheduled": "dash-badge--orange",
    "tour completed": "dash-badge--purple",
    "enrolled": "dash-badge--green",
    "waitlisted": "dash-badge--sky",
    "declined": "dash-badge--red",
    "archived": "dash-badge--gray",
}

# Status string constants — match DB values exactly; never inline these strings.
STATUS_NEW = "New"
STATUS_TOUR_SCHEDULED = "Tour Scheduled"
STATUS_TOUR_COMPLETED = "Tour Completed"
STATUS_WAITLISTED = "Waitlisted"
STATUS_ENROLLED = "Enrolled"
STATUS_DECLINED = "Declined"

# Submission statuses that represent a closed/terminal pipeline state.
# Used by stale detection and "not enrolled" filters to avoid over-counting.
# "Archived" and "Closed" are common admin-configured choices treated as
# terminal even though they are not model-level constants.
_TERMINAL_SUBMISSION_STATUSES = [STATUS_ENROLLED, STATUS_DECLINED, "Archived", "Closed"]

# Max rows shown per tab in the dashboard Work Queue. Change here to adjust everywhere.
DASHBOARD_WORK_QUEUE_LIMIT = 10


def get_submission_status_css(status: str) -> str:
    """Return the dash-badge CSS class for a submission status string."""
    return _STATUS_CSS.get((status or "").lower(), "dash-badge--gray")


def _submission_initials(student: str) -> str:
    parts = student.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return student[:2].upper() if student else "?"


def fetch_queryset_with_cap(qs, limit: int) -> tuple:
    """Fetch up to *limit* rows from a **queryset**, returning ``(rows, cap_hit)``.

    Fetches ``limit + 1`` rows to detect truncation without a separate
    ``count()`` call.  Use this for unmaterialised DB querysets.
    """
    rows = list(qs[:limit + 1])
    cap_hit = len(rows) > limit
    return rows[:limit], cap_hit


def slice_list_with_cap(rows: list, limit: int) -> tuple:
    """Slice an already-materialised **list** to *limit*, returning ``(rows, cap_hit)``.

    No DB hit.  Use this after Python-level filtering of a materialised queryset.
    """
    cap_hit = len(rows) > limit
    return rows[:limit], cap_hit


def _safe_redirect_url(request, next_url: str, fallback: str) -> str:
    """Returns next_url if it is safe to redirect to, else fallback.

    Uses Django's url_has_allowed_host_and_scheme — the same validator used by
    Django's built-in login view — to guard against open-redirect attacks.
    Accepts relative paths and same-host absolute URLs; rejects everything else.
    """
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


def _safe_load_school_config(school_slug: str):
    """
    Admin-only: wraps load_school_config() with exception handling.
    Returns config or None on YAML parse/IO failures.
    All admin call sites handle None via getattr(config, "raw", {}) or 'if config'.
    Public views must NOT use this — they must fail loudly on bad config.
    """
    try:
        return load_school_config(school_slug)
    except Exception:
        logger.exception("Failed to load school config for slug %r", school_slug)
        return None


def _get_accessible_school_for_admin(request, school_slug: str) -> School:
    """
    Shared access gate for all school-admin views.

    1. Fetch School by slug — Http404 if missing.
    2. Enforce _can_view_school_admin_page — Http404 if denied.
    3. Block inactive school unless caller is superuser — Http404 if blocked.

    Returns the School object on success.  Do not weaken these checks.
    """
    school = get_object_or_404(School, slug=school_slug)
    if not _can_view_school_admin_page(request, school):
        raise Http404("Page not found")
    if not school.is_active and not request.user.is_superuser:
        raise Http404("School not found")
    return school


@login_required
def school_dashboard_view(request, school_slug: str):
    """
    Modern inbox-style dashboard for school admins.
    URL: /schools/<slug>/admin/
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    config = _safe_load_school_config(school_slug)
    label_map = build_option_label_map(config.form) if config else {}

    # select_related("school") avoids N+1 from program_display_name() which
    # accesses self.school.slug for the TSCA school check.
    all_submissions = Submission.objects.filter(school=school).select_related("school")

    # Single aggregate query for all status counts (replaces 5 separate count() calls).
    counts = all_submissions.aggregate(
        total=Count("id"),
        new=Count("id", filter=Q(status=STATUS_NEW)),
        enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
        declined=Count("id", filter=Q(status=STATUS_DECLINED)),
    )
    total_submissions = counts["total"]
    new_count = counts["new"]
    approved_count = counts["enrolled"]
    declined_count = counts["declined"]

    # Inbox: New-first then by most recent. Evaluate to list so it can be
    # sliced cheaply for the activity feed without re-running SQL.
    recent_qs = list(
        all_submissions.annotate(
            _priority=Case(
                When(status=STATUS_NEW, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        ).order_by("_priority", "-created_at")[:DASHBOARD_WORK_QUEUE_LIMIT]
    )

    def _dash_row(s):
        student = s.student_display_name() or ""
        program_label = (s.program_display_name(label_map=label_map) or "").strip() or "—"
        status = s.status or STATUS_NEW
        return {
            "id": s.id,
            "admin_url": reverse("school_submission_detail", kwargs={"school_slug": school_slug, "submission_id": s.id}),
            "student": student,
            "initials": _submission_initials(student),
            "program": program_label,
            "status": status,
            "status_css": get_submission_status_css(status),
            "is_new": status == STATUS_NEW,
            "action_label": "Review" if status == STATUS_NEW else "Open",
            "action_primary": status == STATUS_NEW,
            "created_at": timezone.localtime(s.created_at),
        }

    inbox_submissions = []
    for s in recent_qs:
        student = s.student_display_name() or ""
        program_label = (s.program_display_name(label_map=label_map) or "").strip() or "—"
        status = s.status or STATUS_NEW
        inbox_submissions.append(
            {
                "id": s.id,
                "admin_url": reverse("school_submission_detail", kwargs={"school_slug": school_slug, "submission_id": s.id}),
                "student": student,
                "initials": _submission_initials(student),
                "program": program_label,
                "status": status,
                "status_css": get_submission_status_css(status),
                "is_new": status == STATUS_NEW,
                "action_label": "Review" if status == STATUS_NEW else "Open",
                "action_primary": status == STATUS_NEW,
                "created_at": timezone.localtime(s.created_at),
            }
        )

    # Insights — last 30 days
    # TODO: Loads up to 500 full Submission objects for in-process aggregation.
    #       Acceptable for MVP volumes. If submission counts or dashboard traffic grow,
    #       replace with DB-level aggregation or denormalised counters to avoid
    #       pulling large result sets into memory.
    since_30 = timezone.now() - timedelta(days=30)
    insight_qs = list(all_submissions.filter(created_at__gte=since_30).order_by("-created_at")[:500])

    # Top schedule
    sched_label_map = label_map.get("preferred_time", {})
    sched_values = [
        (s.data or {}).get("preferred_time")
        for s in insight_qs
        if (s.data or {}).get("preferred_time")
    ]
    schedule_total = len(sched_values)
    sched_counts = Counter(sched_values)
    top_schedule = None
    if sched_counts:
        top_val, top_c = sched_counts.most_common(1)[0]
        top_schedule = {
            "label": sched_label_map.get(top_val, top_val),
            "count": top_c,
            "pct": round(top_c / schedule_total * 100.0) if schedule_total else 0,
            "total": schedule_total,
        }

    # Top program
    NONE_LABEL = "No program"
    prog_strings = []
    for s in insight_qs:
        p = (s.program_display_name(label_map=label_map) or "").strip()
        prog_strings.append(p if p else "(none)")
    prog_counts = Counter(prog_strings)
    top_program = None
    if prog_counts:
        top_val, top_c = prog_counts.most_common(1)[0]
        prog_total = len(prog_strings)
        top_program = {
            "label": NONE_LABEL if top_val == "(none)" else top_val,
            "count": top_c,
            "pct": round(top_c / prog_total * 100.0) if prog_total else 0,
            "total": prog_total,
        }

    # Top enrichment interest
    enrich_label_map = label_map.get("enrichment_interests", {})
    enrich_counter: Counter = Counter()
    for s in insight_qs:
        interests = (s.data or {}).get("enrichment_interests", [])
        if isinstance(interests, list):
            for v in interests:
                if v:
                    enrich_counter[v] += 1
    enrich_total = sum(enrich_counter.values())
    top_enrichment = None
    # Only show enrichment insight if the school's YAML config defines the field
    if enrich_label_map and enrich_counter:
        top_val, top_c = enrich_counter.most_common(1)[0]
        top_enrichment = {
            "label": enrich_label_map.get(top_val, top_val),
            "count": top_c,
            "pct": round(top_c / enrich_total * 100.0) if enrich_total else 0,
            "total": enrich_total,
        }

    # Activity feed — last 5 submissions
    recent_activity = []
    for s in recent_qs[:5]:
        student = s.student_display_name() or ""
        recent_activity.append(
            {
                "initials": _submission_initials(student),
                "student": student,
                "action": "New submission received",
                "created_at": timezone.localtime(s.created_at),
                "status": s.status or STATUS_NEW,
                "admin_url": reverse("school_submission_detail", kwargs={"school_slug": school_slug, "submission_id": s.id}),
            }
        )

    # Leads
    leads_enabled = school.features.leads_enabled

    # Needs Attention + Follow-ups Today — computed in 1-2 aggregate() calls,
    # not 4 separate .count() queries.
    _dashboard_now = timezone.now()
    _dashboard_today = _dashboard_now.date()
    _sub_agg = Submission.objects.filter(school=school).aggregate(
        needs_attention=Count(
            "id",
            filter=(
                Q(next_follow_up_at__lte=_dashboard_now)
                | Q(status=STATUS_NEW, created_at__lte=_dashboard_now - timedelta(hours=24))
            ),
        ),
        followups_today=Count(
            "id", filter=Q(next_follow_up_at__date=_dashboard_today)
        ),
    )
    if leads_enabled:
        _lead_agg = (
            Lead.objects.filter(school=school)
            .exclude(status__in=[LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST])
            .aggregate(
                needs_attention=Count(
                    "id",
                    filter=(
                        Q(next_follow_up_at__lte=_dashboard_now)
                        | Q(
                            status=LEAD_STATUS_NEW,
                            created_at__lte=_dashboard_now - timedelta(hours=24),
                        )
                    ),
                ),
                followups_today=Count(
                    "id", filter=Q(next_follow_up_at__date=_dashboard_today)
                ),
            )
        )
        needs_attention_count = _sub_agg["needs_attention"] + _lead_agg["needs_attention"]
        followups_today_count = _sub_agg["followups_today"] + _lead_agg["followups_today"]
    else:
        needs_attention_count = _sub_agg["needs_attention"]
        followups_today_count = _sub_agg["followups_today"]

    # Work Queue tab counts — submissions only (leads not shown in queue table)
    sub_overdue_count = _sub_agg["needs_attention"]
    _5d_ago = _dashboard_now - timedelta(days=5)
    sub_stale_count = (
        Submission.objects.filter(school=school, updated_at__lte=_5d_ago)
        .exclude(status__in=_TERMINAL_SUBMISSION_STATUSES)
        .count()
    )

    # Overdue submissions list for Work Queue tab
    _overdue_qs = (
        Submission.objects.filter(school=school)
        .filter(
            Q(next_follow_up_at__lte=_dashboard_now)
            | Q(status=STATUS_NEW, created_at__lte=_dashboard_now - timedelta(hours=24))
        )
        .exclude(status__in=_TERMINAL_SUBMISSION_STATUSES)
        .select_related("school")
        .order_by("next_follow_up_at", "created_at")[:DASHBOARD_WORK_QUEUE_LIMIT]
    )
    overdue_submissions = [_dash_row(s) for s in _overdue_qs]

    # Stale submissions list for Work Queue tab
    _stale_qs = (
        Submission.objects.filter(school=school, updated_at__lte=_5d_ago)
        .exclude(status__in=_TERMINAL_SUBMISSION_STATUSES)
        .select_related("school")
        .order_by("updated_at")[:DASHBOARD_WORK_QUEUE_LIMIT]
    )
    stale_submissions = [_dash_row(s) for s in _stale_qs]

    # Active applications = total minus terminal-status items
    active_count = total_submissions - approved_count - declined_count

    # New leads count — for dashboard "New Leads" card
    new_leads_count = (
        Lead.objects.filter(school=school, status=LEAD_STATUS_NEW).count()
        if leads_enabled
        else 0
    )

    # conversion_metrics — dashboard summary card (leads_enabled schools only)
    conversion_metrics = None
    if leads_enabled:
        _d_lead = Lead.objects.filter(school=school).aggregate(
            total=Count("id"),
            converted=Count("id", filter=Q(converted_submission__isnull=False)),
            active=Count(
                "id",
                filter=Q(converted_submission__isnull=True)
                    & ~Q(status__in=[LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST]),
            ),
        )
        _d_sub = Submission.objects.filter(school=school).aggregate(
            total=Count("id"),
            enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
        )
        _dl, _dlc = _d_lead["total"], _d_lead["converted"]
        _ds, _dse = _d_sub["total"], _d_sub["enrolled"]
        # URLs computed below — define temp vars now, update after reverse() calls
        _d_leads_url = reverse("school_leads", kwargs={"school_slug": school_slug})
        _d_subs_url = reverse("school_submissions", kwargs={"school_slug": school_slug})
        conversion_metrics = {
            "lead_to_sub_rate": round(_dlc / _dl * 100) if _dl else 0,
            "sub_to_enrolled_rate": round(_dse / _ds * 100) if _ds else 0,
            "leads_not_converted": _d_lead["active"],
            "leads_not_converted_url": f"{_d_leads_url}?filter=not_converted",
            "subs_not_enrolled": Submission.objects.filter(school=school).exclude(
                status__in=_TERMINAL_SUBMISSION_STATUSES
            ).count(),
            "subs_not_enrolled_url": f"{_d_subs_url}?filter=not_enrolled",
        }

    # Apply URL and related links
    apply_url = request.build_absolute_uri(
        reverse("apply", kwargs={"school_slug": school_slug})
    )
    submissions_url = reverse("school_submissions", kwargs={"school_slug": school_slug})
    leads_url = reverse("school_leads", kwargs={"school_slug": school_slug}) if leads_enabled else ""

    # Time-based greeting
    local_hour = timezone.localtime(timezone.now()).hour
    if local_hour < 12:
        greeting = "Good morning"
    else:
        greeting = "Good evening"

    user_initial = (
        request.user.get_full_name() or request.user.username
    )[0].upper()

    return render(
        request,
        "dashboard.html",
        {
            "school": school,
            "school_slug": school_slug,
            "total_submissions": total_submissions,
            "new_count": new_count,
            "approved_count": approved_count,
            "declined_count": declined_count,
            "inbox_submissions": inbox_submissions,
            "top_schedule": top_schedule,
            "top_program": top_program,
            "top_enrichment": top_enrichment,
            "recent_activity": recent_activity,
            "leads_enabled": leads_enabled,
            "new_leads_count": new_leads_count,
            "needs_attention_count": needs_attention_count,
            "followups_today_count": followups_today_count,
            "sub_overdue_count": sub_overdue_count,
            "sub_stale_count": sub_stale_count,
            "overdue_submissions": overdue_submissions,
            "stale_submissions": stale_submissions,
            "active_count": active_count,
            "conversion_metrics": conversion_metrics,
            "apply_url": apply_url,
            "submissions_url": submissions_url,
            "leads_url": leads_url,
            "greeting": greeting,
            "user_initial": user_initial,
            "now": timezone.localtime(timezone.now()),
            "active_nav": "dashboard",
        },
    )


# ── School admin: submissions list ────────────────────────────────────────

# TODO: Future — make these key lists YAML-configurable per school so that schools
#       with custom contact field names (e.g. "primary_guardian_email") don't silently
#       show "—" for parent contact.  The current keys cover all known production forms
#       (dancemaker, kimberlas, tsca, young-minds).  Until the YAML mapping is built,
#       any new school with non-standard contact field names must add its keys here.
_PARENT_EMAIL_KEYS = ("contact_email", "guardian_email", "parent_email", "email", "applicant_email")
_PARENT_PHONE_KEYS = ("contact_phone", "guardian_phone", "parent_phone", "phone", "applicant_phone")


def _extract_contact_field(data: dict, keys: tuple) -> str:
    for k in keys:
        v = (data or {}).get(k, "")
        if v:
            return str(v)
    return ""


def _school_admin_base_context(request, school, active_nav: str) -> dict:
    """Shared context required by school_admin/base.html."""
    leads_enabled = school.features.leads_enabled
    user_initial = (request.user.get_full_name() or request.user.username)[0].upper()
    return {
        "school": school,
        "school_slug": school.slug,
        "leads_enabled": leads_enabled,
        "user_initial": user_initial,
        "now": timezone.localtime(timezone.now()),
        "active_nav": active_nav,
    }


# Smart filter keys (hardcoded, always available for all schools — Phase 11)
# not_converted / not_enrolled are routing-only (in _SMART_FILTER_KEYS but NOT _SMART_FILTERS)
# so they function as URL targets without showing as UI pills.
_SMART_FILTER_KEYS = {
    "needs_follow_up", "recent_activity", "stale",
    "not_converted",  # active leads that haven't become applications yet
    "not_enrolled",   # submissions not yet enrolled or declined
}

_SMART_FILTERS = {
    "needs_follow_up": {"label": "Needs Follow-Up"},
    "recent_activity":  {"label": "Recent"},
    # "stale" kept in _SMART_FILTER_KEYS for URL routing (dashboard links) but
    # intentionally NOT a display pill — the label is too confusing for admins.
}


def _apply_submission_filters(qs, active_filter, status_filter, workflow_filters):
    """Apply workflow/status/smart filter conditions to a submission queryset.

    Shared by school_submissions_view (list) and school_submission_export_view (CSV).
    The caller owns the base queryset, annotations, and ordering.
    """
    # Smart filters take highest priority
    if active_filter in _SMART_FILTER_KEYS:
        now = timezone.now()
        if active_filter == "needs_follow_up":
            return qs.filter(
                Q(next_follow_up_at__lte=now)
                | Q(status="New", created_at__lte=now - timedelta(hours=24))
            )
        if active_filter == "recent_activity":
            return qs.filter(updated_at__gte=now - timedelta(hours=48))
        if active_filter == "stale":
            return qs.filter(updated_at__lte=now - timedelta(days=5)).exclude(
                status__in=_TERMINAL_SUBMISSION_STATUSES
            )
        if active_filter == "not_enrolled":
            return qs.exclude(status__in=_TERMINAL_SUBMISSION_STATUSES)
    if active_filter and active_filter in workflow_filters:
        return qs.filter(status__in=workflow_filters[active_filter]["statuses"])
    if status_filter:
        return qs.filter(status=status_filter)
    return qs


def _apply_lead_filters(qs, active_filter, status_filter, search_q, workflow_filters):
    """Apply workflow/status/smart/search filter conditions to a lead queryset.

    Shared by school_leads_view (list) and school_lead_export_view (CSV).
    The caller owns the base queryset, annotations, and ordering.
    """
    # Smart filters take highest priority
    if active_filter in _SMART_FILTER_KEYS:
        now = timezone.now()
        if active_filter == "needs_follow_up":
            qs = qs.filter(
                Q(next_follow_up_at__lte=now)
                | Q(status=LEAD_STATUS_NEW, created_at__lte=now - timedelta(hours=24))
            ).exclude(status__in=[LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST])
        elif active_filter == "recent_activity":
            qs = qs.filter(updated_at__gte=now - timedelta(hours=48))
        elif active_filter == "stale":
            qs = qs.filter(updated_at__lte=now - timedelta(days=5)).exclude(
                status__in=[LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST]
            )
        elif active_filter == "not_converted":
            qs = qs.filter(converted_submission__isnull=True).exclude(
                status__in=[LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST]
            )
    elif active_filter and active_filter in workflow_filters:
        qs = qs.filter(status__in=workflow_filters[active_filter]["statuses"])
    elif status_filter:
        qs = qs.filter(status=status_filter)
    if search_q:
        qs = qs.filter(
            Q(name__icontains=search_q)
            | Q(email__icontains=search_q)
            | Q(phone__icontains=search_q)
        )
    return qs


_LEAD_STATUS_CSS: dict = {
    "new": "dash-badge--blue",
    "contacted": "dash-badge--orange",
    "trial_scheduled": "dash-badge--purple",
    "enrolled": "dash-badge--green",
    "lost": "dash-badge--gray",
}


def _build_submission_row(
    s: Submission,
    label_map: dict,
    workflow_transitions: dict | None = None,
    *,
    school_slug: str = "",
) -> dict:
    """Serialize one Submission into a display dict for school-admin list templates.

    workflow_transitions: {from_status: [{label, status}]} from YAML config.
    When provided, row["transitions"] is populated so the template can render
    inline action buttons. Empty list when no transitions are defined for this
    submission's current status (or when workflow_transitions is None/empty).

    school_slug: when provided, school_admin_url points to the school-admin detail
    page; otherwise falls back to the Django admin change page.
    """
    student = s.student_display_name() or ""
    status = s.status or STATUS_NEW
    django_admin_url = reverse("admin:core_submission_change", args=[s.id])
    school_admin_url = (
        reverse("school_submission_detail", kwargs={"school_slug": school_slug, "submission_id": s.id})
        if school_slug else django_admin_url
    )
    _now = timezone.now()
    is_overdue = bool(s.next_follow_up_at and s.next_follow_up_at < _now)
    return {
        "id": s.id,
        "admin_url": django_admin_url,
        "school_admin_url": school_admin_url,
        "student": student,
        "initials": _submission_initials(student),
        "program": (s.program_display_name(label_map=label_map) or "").strip() or "—",
        "status": status,
        "status_css": get_submission_status_css(status),
        "is_new": status == STATUS_NEW,
        "created_at": timezone.localtime(s.created_at),
        "parent_email": _extract_contact_field(s.data, _PARENT_EMAIL_KEYS),
        "parent_phone": _extract_contact_field(s.data, _PARENT_PHONE_KEYS),
        "transitions": (workflow_transitions or {}).get(status, []),
        "last_activity": timezone.localtime(s.created_at),
        "has_notes": bool(s.internal_notes),
        "has_files": getattr(s, "has_files", False),
        "next_follow_up_at": (
            timezone.localtime(s.next_follow_up_at) if s.next_follow_up_at else None
        ),
        "is_overdue": is_overdue,
    }


_NAME_GUARDIAN_KEYS = frozenset({"guardian_name", "parent_name", "contact_name"})
_NAME_FIRST_KEYS = frozenset({"student_first_name", "first_name", "child_first_name"})
_NAME_LAST_KEYS = frozenset({"student_last_name", "last_name", "child_last_name"})


def _find_program_field_key(config_raw: dict) -> str | None:
    """Return the first field key matching known program field names in the YAML form."""
    form = (config_raw or {}).get("form") or {}
    for section in form.get("sections", []):
        for f in section.get("fields", []):
            if f.get("key", "") in PROGRAM_FIELD_KEYS:
                return f["key"]
    return None


def _build_lead_name_prefill(lead_name: str, config_raw: dict) -> dict:
    """Map lead.name into the best available name field(s) in the YAML form.
    Priority: guardian_name (full name) > student_first/last (split on first space).
    """
    name = (lead_name or "").strip()
    if not name:
        return {}
    form = (config_raw or {}).get("form") or {}
    guardian_key = first_key = last_key = None
    for section in form.get("sections", []):
        for f in section.get("fields", []):
            key = f.get("key", "")
            if key in _NAME_GUARDIAN_KEYS and guardian_key is None:
                guardian_key = key
            elif key in _NAME_FIRST_KEYS and first_key is None:
                first_key = key
            elif key in _NAME_LAST_KEYS and last_key is None:
                last_key = key
    if guardian_key:
        return {guardian_key: name}
    if first_key and last_key:
        parts = name.split(" ", 1)
        return {first_key: parts[0], last_key: parts[1] if len(parts) > 1 else ""}
    return {}


def _build_lead_prefill_data(lead: Lead, config_raw: dict) -> dict:
    """Build DraftSubmission.data prefill dict from Lead fields."""
    prefill: dict = {}
    email_key = find_email_field_key(config_raw)
    if email_key and lead.email:
        prefill[email_key] = lead.email
    if lead.phone:
        prefill["contact_phone"] = lead.phone
    if lead.interested_in_value:
        prog_key = _find_program_field_key(config_raw)
        if prog_key:
            prefill[prog_key] = lead.interested_in_value
    prefill.update(_build_lead_name_prefill(lead.name, config_raw))
    return prefill


def _build_lead_row(
    lead: Lead,
    workflow_transitions: dict | None = None,
    *,
    school_slug: str = "",
) -> dict:
    """Serialize one Lead into a display dict for the leads list template.

    school_slug: when provided, school_admin_url and converted_submission_url
    point to school-admin pages; otherwise fall back to Django admin URLs.
    """
    transitions = list(workflow_transitions.get(lead.status, [])) if workflow_transitions else []
    django_admin_url = reverse("admin:core_lead_change", args=[lead.id])
    school_admin_url = (
        reverse("school_lead_detail", kwargs={"school_slug": school_slug, "lead_id": lead.id})
        if school_slug else django_admin_url
    )
    if school_slug and lead.converted_submission_id:
        converted_url = reverse(
            "school_submission_detail",
            kwargs={"school_slug": school_slug, "submission_id": lead.converted_submission_id},
        )
    elif lead.converted_submission_id:
        converted_url = reverse("admin:core_submission_change", args=[lead.converted_submission_id])
    else:
        converted_url = None
    _now = timezone.now()
    is_overdue = bool(lead.next_follow_up_at and lead.next_follow_up_at < _now)
    last_activity = timezone.localtime(lead.last_contacted_at or lead.created_at)
    quick_actions = [t for t in transitions if t["status"] in ("contacted", "lost")]
    return {
        "id": lead.id,
        "admin_url": django_admin_url,
        "school_admin_url": school_admin_url,
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "program": lead.interested_in_label or "—",
        "status": lead.get_status_display(),
        "status_raw": lead.status,
        "status_css": _LEAD_STATUS_CSS.get(lead.status, "dash-badge--gray"),
        "is_new": lead.status == LEAD_STATUS_NEW,
        "created_at": timezone.localtime(lead.created_at),
        "next_follow_up_at": (
            timezone.localtime(lead.next_follow_up_at) if lead.next_follow_up_at else None
        ),
        "is_overdue": is_overdue,
        "last_activity": last_activity,
        "quick_actions": quick_actions,
        "is_converted": lead.converted_submission_id is not None,
        "converted_submission_admin_url": converted_url,
        "transitions": transitions,
        "has_notes": bool(lead.notes),
    }


@login_required
def school_submissions_view(request, school_slug: str):
    """
    Modern submissions list for school admins.
    URL: /schools/<slug>/admin/submissions/
    Filters: ?filter=<workflow_key>  ?status=<exact>  ?q=<search>

    When the school YAML defines admin.submission_workflow.filters, the template
    renders named filter tabs (?filter=needs_review etc.) instead of the generic
    status dropdown. Both parameters co-exist: ?filter= takes precedence.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}
    label_map = build_option_label_map(config.form) if config else {}

    workflow_filters = get_submission_workflow_filters(config_raw)
    workflow_transitions = get_submission_workflow_transitions(config_raw)

    active_filter = (request.GET.get("filter") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
    search_q = (request.GET.get("q") or "").strip()

    # select_related("school") avoids N+1 from program_display_name() TSCA slug check.
    # Priority sort: overdue follow-up → upcoming follow-up → new (no follow-up) → rest.
    _now = timezone.now()
    qs = _apply_submission_filters(
        Submission.objects.filter(school=school).select_related("school").annotate(
            has_files=Exists(SubmissionFile.objects.filter(submission=OuterRef("pk"))),
            _inbox_priority=Case(
                When(next_follow_up_at__lte=_now, then=Value(0)),
                When(next_follow_up_at__isnull=False, then=Value(1)),
                When(status=STATUS_NEW, then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        ).order_by("_inbox_priority", "-created_at"),
        active_filter, status_filter, workflow_filters,
    )

    # Python-level search is unavoidable: student name and parent contact live in
    # the dynamic JSON `data` field, not indexed DB columns.
    # TODO: Replace with DB-level search once student name / contact fields are
    #       promoted to real indexed columns (e.g. generated column or separate
    #       denormalised table). As-is, searches on schools with >2000 submissions
    #       only cover the 2000 most recent — `search_cap_hit` warns users when
    #       this limit is reached.
    search_pool, search_cap_hit = fetch_queryset_with_cap(qs, 2000)

    if search_q:
        q_lower = search_q.lower()
        filtered = [
            s for s in search_pool
            if q_lower in (s.student_display_name() or "").lower()
            or q_lower in (s.program_display_name(label_map=label_map) or "").lower()
            or q_lower in _extract_contact_field(s.data, _PARENT_EMAIL_KEYS).lower()
            or q_lower in _extract_contact_field(s.data, _PARENT_PHONE_KEYS).lower()
            or q_lower in (s.status or "").lower()
        ]
    else:
        filtered = search_pool

    display_rows, display_cap_hit = slice_list_with_cap(filtered, 200)
    result_count = len(filtered)

    submissions = [_build_submission_row(s, label_map, workflow_transitions, school_slug=school_slug) for s in display_rows]

    # Distinct statuses for the fallback dropdown (only rendered when no workflow_filters).
    status_choices = list(
        Submission.objects.filter(school=school)
        .values_list("status", flat=True)
        .distinct()
        .order_by("status")
    )

    allowed_statuses, _ = get_submission_status_choices(config_raw)
    # Checkboxes and bulk bar are always shown — download/print work without YAML workflow.
    # The status-update dropdown is independently gated on having bulk_status_choices.
    workflow_actions_enabled = True

    # Metrics — school-wide status counts (not affected by active filter/search).
    sub_by_status = {
        row["status"]: row["n"]
        for row in Submission.objects.filter(school=school).values("status").annotate(n=Count("id"))
    }
    submissions_metrics = {
        "new": sub_by_status.get(STATUS_NEW, 0),
        "total": sum(sub_by_status.values()),
    }

    _export_params = {}
    if active_filter:
        _export_params["filter"] = active_filter
    elif status_filter:
        _export_params["status"] = status_filter
    if search_q:
        _export_params["q"] = search_q
    export_base = reverse("school_submission_export", kwargs={"school_slug": school_slug})
    export_url = export_base + ("?" + urlencode(_export_params) if _export_params else "")

    _qs_suffix = ("?" + urlencode(_export_params)) if _export_params else ""
    export_profiles = [
        {
            "name": profile_name,
            "url": reverse(
                "school_submission_profile_export",
                kwargs={"school_slug": school_slug, "profile_name": profile_name},
            ) + _qs_suffix,
        }
        for profile_name in get_export_configs(config_raw)
    ]

    apply_url = request.build_absolute_uri(
        reverse("apply", kwargs={"school_slug": school_slug})
    )

    ctx = _school_admin_base_context(request, school, "submissions")
    ctx.update(
        {
            "submissions": submissions,
            "total_count": len(submissions),
            "result_count": result_count,
            "display_cap_hit": display_cap_hit,
            "search_cap_hit": search_cap_hit,
            "active_filter": active_filter,
            "status_filter": status_filter,
            "search_q": search_q,
            "status_choices": status_choices,
            "workflow_filters": workflow_filters,
            "workflow_actions_enabled": workflow_actions_enabled,
            "submissions_url": reverse("school_submissions", kwargs={"school_slug": school_slug}),
            "status_update_url_name": "school_submission_status_update",
            "bulk_status_choices": allowed_statuses,
            "bulk_update_url": reverse(
                "school_submission_bulk_status_update",
                kwargs={"school_slug": school_slug},
            ),
            "submissions_metrics": submissions_metrics,
            "export_url": export_url,
            "export_profiles": export_profiles,
            "apply_url": apply_url,
            "smart_filters": _SMART_FILTERS,
        }
    )
    return render(request, "school_admin/submissions.html", ctx)


# ── School admin: submission CSV export ──────────────────────────────────────

@login_required
@require_http_methods(["GET"])
def school_submission_export_view(request, school_slug: str):
    """
    Export filtered submissions as CSV.
    GET /schools/<slug>/admin/submissions/export/

    Respects the same filter params as the list view:
      filter= (named workflow filter key)
      status= (exact status)
      q=      (search — applies Python-level search with no row cap)

    Columns: fixed metadata (ID, Status, Submitted At, Internal Notes)
    followed by all YAML form fields in schema order (label as header).

    Reuses:
      _apply_submission_filters   — identical filtering logic as the list view
      build_yaml_sections         — YAML field order and labels (schema-driven)
      normalize_csv_value         — type-safe value serialisation
      log_admin_audit             — audit trail for every export
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}
    label_map = build_option_label_map(config.form) if config else {}

    workflow_filters = get_submission_workflow_filters(config_raw)

    active_filter = (request.GET.get("filter") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
    search_q = (request.GET.get("q") or "").strip()

    qs = _apply_submission_filters(
        Submission.objects.filter(school=school).select_related("school").order_by("-created_at"),
        active_filter, status_filter, workflow_filters,
    )

    # No row cap for exports — iterate all matching rows.
    all_submissions = list(qs)
    if search_q:
        q_lower = search_q.lower()
        all_submissions = [
            s for s in all_submissions
            if q_lower in (s.student_display_name() or "").lower()
            or q_lower in (s.program_display_name(label_map=label_map) or "").lower()
            or q_lower in _extract_contact_field(s.data, _PARENT_EMAIL_KEYS).lower()
            or q_lower in _extract_contact_field(s.data, _PARENT_PHONE_KEYS).lower()
            or q_lower in (s.status or "").lower()
        ]

    # YAML-ordered field list for columns — same source as detail page rendering.
    # build_yaml_sections with empty data gives us (key, label) without values.
    yaml_sections = build_yaml_sections(config, existing_data={}) if config else []
    yaml_fields: list[tuple[str, str]] = [
        (f["key"], f["label"])
        for sec in yaml_sections
        for f in sec["fields"]
    ]

    fixed_headers = [
        "Submission ID", "Status", "Enrolled", "Submitted At", "Internal Notes", "Linked Lead ID",
    ]
    headers = fixed_headers + [label for _, label in yaml_fields]

    # Build submission_pk → lead_id lookup in one query to avoid N+1.
    sub_ids = [s.id for s in all_submissions]
    lead_by_sub: dict[int, int] = {
        row["converted_submission_id"]: row["id"]
        for row in Lead.objects.filter(
            school=school, converted_submission_id__in=sub_ids
        ).values("id", "converted_submission_id")
    } if sub_ids else {}

    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{school.slug}-submissions.csv"'

    writer = csv.writer(resp)
    writer.writerow(headers)

    for s in all_submissions:
        data = s.data or {}
        is_enrolled = (s.status or "") == STATUS_ENROLLED
        writer.writerow(
            [
                s.public_id,
                s.status or "",
                "Yes" if is_enrolled else "No",
                timezone.localtime(s.created_at).strftime("%Y-%m-%d %H:%M"),
                s.internal_notes or "",
                lead_by_sub.get(s.id, ""),
            ]
            + [normalize_csv_value(data.get(key)) for key, _ in yaml_fields]
        )

    log_admin_audit(
        request=request,
        action="action",
        obj=school,
        changes={},
        extra={"name": "export_csv", "model": "submission", "count": len(all_submissions)},
    )

    return resp


# ── School admin: submission profile (Brightwheel etc.) export ───────────

@login_required
@require_http_methods(["GET"])
def school_submission_profile_export_view(request, school_slug: str, profile_name: str):
    """
    Export filtered submissions using a YAML-configured export profile.
    GET /schools/<slug>/admin/submissions/export/<profile_name>/

    profile_name must match a key under exports: in the school's YAML.
    Respects the same filter params as the standard export view.
    Uses resolve_export_row() to map submission data → profile columns.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}

    export_configs = get_export_configs(config_raw)
    if profile_name not in export_configs:
        raise Http404(f"Export profile '{profile_name}' not found.")

    field_map = export_configs[profile_name]
    workflow_filters = get_submission_workflow_filters(config_raw)

    active_filter = (request.GET.get("filter") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
    search_q = (request.GET.get("q") or "").strip()

    label_map = build_option_label_map(config.form) if config else {}

    qs = _apply_submission_filters(
        Submission.objects.filter(school=school).order_by("-created_at"),
        active_filter, status_filter, workflow_filters,
    )
    all_submissions = list(qs)
    if search_q:
        q_lower = search_q.lower()
        all_submissions = [
            s for s in all_submissions
            if q_lower in (s.student_display_name() or "").lower()
            or q_lower in (s.program_display_name(label_map=label_map) or "").lower()
            or q_lower in _extract_contact_field(s.data, _PARENT_EMAIL_KEYS).lower()
            or q_lower in _extract_contact_field(s.data, _PARENT_PHONE_KEYS).lower()
            or q_lower in (s.status or "").lower()
        ]

    from .services.integrations import slugify_export_name
    filename = f"{school.slug}-{slugify_export_name(profile_name)}-export.csv"
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(resp)
    writer.writerow(list(field_map.keys()))

    all_warnings: list[str] = []
    for s in all_submissions:
        row, warnings = resolve_export_row(s.data or {}, field_map)
        writer.writerow([row.get(col, "") for col in field_map.keys()])
        all_warnings.extend(warnings)

    if all_warnings:
        logger.warning(
            "Profile export '%s' for school '%s' had %d mapping warning(s): %s",
            profile_name, school_slug, len(all_warnings), "; ".join(all_warnings[:10]),
        )

    log_admin_audit(
        request=request,
        action="action",
        obj=school,
        changes={},
        extra={"name": "export_csv", "model": "submission", "profile": profile_name, "count": len(all_submissions)},
    )

    return resp


# ── School admin: submission status update ────────────────────────────────

@login_required
@require_http_methods(["POST"])
def school_submission_status_update_view(request, school_slug: str, submission_id: int):
    """
    Inline status transition for a single submission.

    POST /schools/<slug>/admin/submissions/<id>/status/

    Required POST params:
      new_status  — target status string
      next        — (optional) full local path+query to redirect back to;
                    validated for safety; falls back to submissions list URL

    Validation (all must pass or request is rejected with an error message):
      1. Submission belongs to this school (school scoped get_object_or_404).
      2. new_status is in this school's configured submission_statuses.
      3. School YAML defines submission_workflow.transitions.
      4. The transition from submission.status → new_status is explicitly allowed.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    new_status = (request.POST.get("new_status") or "").strip()
    next_url   = (request.POST.get("next") or "").strip()

    fallback     = reverse("school_submissions", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    # School-scoped lookup: submission_id not belonging to this school → 404.
    submission = get_object_or_404(Submission, id=submission_id, school=school)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}

    # 1. Target status must be in this school's configured status list.
    allowed_statuses, _ = get_submission_status_choices(config_raw)
    if new_status not in allowed_statuses:
        messages.error(request, f"'{new_status}' is not a valid status for this school.")
        return redirect(redirect_url)

    # 2. If a workflow is configured, enforce the allowed transitions from current status.
    #    If no workflow is configured, any status in the allowed list is valid (free-for-all).
    transitions = get_submission_workflow_transitions(config_raw)
    if transitions:
        allowed_next = [t["status"] for t in transitions.get(submission.status, [])]
        if new_status not in allowed_next:
            messages.error(
                request,
                f"Cannot transition from \"{submission.status}\" to \"{new_status}\".",
            )
            return redirect(redirect_url)

    old_status = submission.status
    submission.status = new_status
    with transaction.atomic():
        submission.save(update_fields=["status", "updated_at"])
        log_admin_audit(
            request=request,
            action="action",
            obj=submission,
            changes={},
            extra={"name": "status_update", "from": old_status, "to": new_status},
        )

    messages.success(request, f"Status updated to \"{new_status}\".")
    return redirect(redirect_url)


# ── School admin: bulk submission status update ───────────────────────────

@login_required
@require_http_methods(["POST"])
def school_submission_bulk_status_update_view(request, school_slug: str):
    """
    Bulk inline status transition for multiple submissions.

    POST /schools/<slug>/admin/submissions/bulk-status/

    Required POST params:
      submission_ids  — repeated (one value per selected submission)
      new_status      — target status string
      next            — (optional) full local path+query to redirect back to;
                        validated for safety; falls back to submissions list URL

    Per-submission logic:
      - School-scoped lookup (cross-school IDs silently excluded).
      - Transition validated against YAML workflow per submission.
      - Eligible → update + audit log. Ineligible → skip + count.
      - Flash message reports updated/skipped counts.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    new_status = (request.POST.get("new_status") or "").strip()
    next_url   = (request.POST.get("next") or "").strip()
    raw_ids    = request.POST.getlist("submission_ids")

    fallback     = reverse("school_submissions", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    if not raw_ids:
        messages.error(request, "No submissions selected.")
        return redirect(redirect_url)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}

    # 1. Target status must be in school's configured statuses.
    allowed_statuses, _ = get_submission_status_choices(config_raw)
    if new_status not in allowed_statuses:
        messages.error(request, f"'{new_status}' is not a valid status for this school.")
        return redirect(redirect_url)

    # 2. If workflow configured, validate the transition. Otherwise allow any status freely.
    transitions = get_submission_workflow_transitions(config_raw)

    # 3. Parse IDs — ignore non-integer values silently.
    ids = []
    for sid in raw_ids:
        try:
            ids.append(int(sid))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.error(request, "No valid submissions selected.")
        return redirect(redirect_url)

    # School-scoped queryset — cross-school IDs silently excluded.
    submissions = list(Submission.objects.filter(id__in=ids, school=school))
    if not submissions:
        messages.error(request, "No matching submissions found.")
        return redirect(redirect_url)

    updated = 0
    skipped = 0
    for sub in submissions:
        if transitions:
            allowed_next = [t["status"] for t in transitions.get(sub.status, [])]
            if new_status not in allowed_next:
                skipped += 1
                continue
        old_status = sub.status
        sub.status = new_status
        sub.save(update_fields=["status", "updated_at"])
        log_admin_audit(
            request=request,
            action="action",
            obj=sub,
            changes={},
            extra={"name": "bulk_status_update", "from": old_status, "to": new_status},
        )
        updated += 1

    noun = "submission" if updated == 1 else "submissions"
    if updated and not skipped:
        messages.success(request, f"{updated} {noun} updated to \"{new_status}\".")
    elif updated and skipped:
        messages.success(
            request,
            f"{updated} {noun} updated to \"{new_status}\". "
            f"{skipped} skipped — current status does not allow this transition.",
        )
    else:
        messages.warning(
            request,
            f"No submissions updated. {skipped} skipped — "
            "current status does not allow this transition.",
        )

    return redirect(redirect_url)


# ── School admin: leads list ─────────────────────────────────────────────

@login_required
def school_leads_view(request, school_slug: str):
    """
    Modern leads list for school admins.
    URL: /schools/<slug>/admin/leads/
    Returns 404 if leads feature is disabled for the school.
    Filters: ?filter=<workflow_key>  ?status=<exact>  ?q=<search>

    When the school YAML defines admin.lead_workflow.filters, the template
    renders named filter tabs (?filter=new_leads etc.) instead of the generic
    status dropdown. Both parameters co-exist: ?filter= takes precedence.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    if not school.features.leads_enabled and not request.user.is_superuser:
        return render(
            request,
            "feature_disabled.html",
            {
                "school": school,
                "school_slug": school_slug,
                "feature_name": "Leads",
                "message": "The leads pipeline is not enabled for this school.",
                "required_plan": "Starter",
                "billing_url": reverse("school_billing", kwargs={"school_slug": school_slug}),
            },
            status=403,
        )

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}

    workflow_filters     = get_lead_workflow_filters(config_raw)
    workflow_transitions = get_lead_workflow_transitions(config_raw)
    # Filters and actions are independent: inline/bulk actions only require transitions.
    workflow_actions_enabled = bool(workflow_transitions)

    active_filter = (request.GET.get("filter") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
    search_q = (request.GET.get("q") or "").strip()

    # Priority sort: overdue follow-up → upcoming follow-up → new (no follow-up) → rest.
    _now = timezone.now()
    qs = _apply_lead_filters(
        Lead.objects.filter(school=school).select_related("school").annotate(
            _inbox_priority=Case(
                When(next_follow_up_at__lte=_now, then=Value(0)),
                When(next_follow_up_at__isnull=False, then=Value(1)),
                When(status=LEAD_STATUS_NEW, then=Value(2)),
                default=Value(3),
                output_field=IntegerField(),
            )
        ).order_by("_inbox_priority", "-created_at"),
        active_filter, status_filter, search_q, workflow_filters,
    )

    leads_raw, lead_display_cap_hit = fetch_queryset_with_cap(qs, 200)
    leads = [_build_lead_row(lead, workflow_transitions, school_slug=school_slug) for lead in leads_raw]

    # Metrics — school-wide status counts (not affected by active filter/search).
    leads_by_status = {
        row["status"]: row["n"]
        for row in Lead.objects.filter(school=school).values("status").annotate(n=Count("id"))
    }
    leads_metrics = {
        "new": leads_by_status.get(LEAD_STATUS_NEW, 0),
        "contacted": leads_by_status.get("contacted", 0),
        "enrolled": leads_by_status.get(LEAD_STATUS_ENROLLED, 0),
    }

    _export_params = {}
    if active_filter:
        _export_params["filter"] = active_filter
    elif status_filter:
        _export_params["status"] = status_filter
    if search_q:
        _export_params["q"] = search_q
    lead_export_base = reverse("school_lead_export", kwargs={"school_slug": school_slug})
    lead_export_url = lead_export_base + ("?" + urlencode(_export_params) if _export_params else "")

    lead_capture_url = request.build_absolute_uri(
        reverse("lead_capture", kwargs={"school_slug": school_slug})
    )

    ctx = _school_admin_base_context(request, school, "leads")
    ctx.update(
        {
            "leads": leads,
            "total_count": len(leads),
            "lead_display_cap_hit": lead_display_cap_hit,
            "active_filter": active_filter,
            "status_filter": status_filter,
            "search_q": search_q,
            "lead_status_choices": LEAD_STATUS_CHOICES,
            "workflow_filters": workflow_filters,
            "workflow_actions_enabled": workflow_actions_enabled,
            "leads_url": reverse("school_leads", kwargs={"school_slug": school_slug}),
            "leads_metrics": leads_metrics,
            "bulk_update_url": reverse(
                "school_lead_bulk_status_update",
                kwargs={"school_slug": school_slug},
            ),
            "export_url": lead_export_url,
            "lead_capture_url": lead_capture_url,
            "smart_filters": _SMART_FILTERS,
        }
    )
    return render(request, "school_admin/leads.html", ctx)


# ── School admin: lead CSV export ────────────────────────────────────────────

@login_required
@require_http_methods(["GET"])
def school_lead_export_view(request, school_slug: str):
    """
    Export filtered leads as CSV.
    GET /schools/<slug>/admin/leads/export/

    Respects the same filter params as the list view:
      filter= (named workflow filter key)
      status= (exact status)
      q=      (search — DB-level on name/email/phone)

    Columns: Lead ID, Name, Email, Phone, Program Interest, Status,
             Created At, Last Contacted At, Next Follow Up, Notes.

    Reuses:
      _apply_lead_filters   — identical filtering logic as the list view
      log_admin_audit       — audit trail for every export
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    if not school.features.leads_enabled and not request.user.is_superuser:
        return render(
            request,
            "feature_disabled.html",
            {
                "school": school,
                "school_slug": school_slug,
                "feature_name": "Leads",
                "message": "Lead export is not available — the leads pipeline is not enabled for this school.",
                "required_plan": "Starter",
                "billing_url": reverse("school_billing", kwargs={"school_slug": school_slug}),
            },
            status=403,
        )

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}
    workflow_filters = get_lead_workflow_filters(config_raw)

    active_filter = (request.GET.get("filter") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()
    search_q = (request.GET.get("q") or "").strip()

    qs = _apply_lead_filters(
        Lead.objects.filter(school=school).select_related("school").order_by("-created_at"),
        active_filter, status_filter, search_q, workflow_filters,
    )
    leads = list(qs)

    headers = [
        "Lead ID", "Name", "Email", "Phone", "Program Interest",
        "Status", "Created At", "Last Contacted At", "Next Follow Up", "Notes",
        "Converted", "Converted At",
    ]

    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{school.slug}-leads.csv"'

    writer = csv.writer(resp)
    writer.writerow(headers)

    for lead in leads:
        writer.writerow([
            lead.id,
            lead.name or "",
            lead.email or "",
            lead.phone or "",
            lead.interested_in_label or lead.interested_in_value or "",
            lead.status or "",
            timezone.localtime(lead.created_at).strftime("%Y-%m-%d %H:%M"),
            timezone.localtime(lead.last_contacted_at).strftime("%Y-%m-%d %H:%M") if lead.last_contacted_at else "",
            timezone.localtime(lead.next_follow_up_at).strftime("%Y-%m-%d %H:%M") if lead.next_follow_up_at else "",
            lead.notes or "",
            "Yes" if lead.converted_submission_id else "No",
            timezone.localtime(lead.converted_at).strftime("%Y-%m-%d %H:%M") if lead.converted_at else "",
        ])

    log_admin_audit(
        request=request,
        action="action",
        obj=school,
        changes={},
        extra={"name": "export_csv", "model": "lead", "count": len(leads)},
    )

    return resp


@login_required
@require_http_methods(["POST"])
def school_lead_status_update_view(request, school_slug: str, lead_id: int):
    """
    Inline status transition for a single lead.
    POST /schools/<slug>/admin/leads/<id>/status/

    POST params:
      new_status  — target status string (must be a valid Lead model status)
      next        — (optional) full local path+query for redirect; falls back to leads list

    Validation:
      1. new_status is a valid Lead model status (new/contacted/trial_scheduled/enrolled/lost).
      2. School YAML defines admin.lead_workflow.transitions.
      3. The transition from lead.status → new_status is explicitly allowed.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    new_status   = (request.POST.get("new_status") or "").strip()
    next_url     = (request.POST.get("next") or "").strip()
    fallback     = reverse("school_leads", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    lead = get_object_or_404(Lead, id=lead_id, school=school)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}

    # 1. Target status must be a valid Lead model status.
    if new_status not in {c[0] for c in LEAD_STATUS_CHOICES}:
        messages.error(request, f"'{new_status}' is not a valid lead status.")
        return redirect(redirect_url)

    # 2. If a lead workflow is configured, enforce allowed transitions.
    #    If no workflow configured, any valid status is allowed (free-for-all).
    transitions = get_lead_workflow_transitions(config_raw)
    if transitions:
        allowed_next = [t["status"] for t in transitions.get(lead.status, [])]
        if new_status not in allowed_next:
            messages.error(
                request,
                f'Cannot transition from "{lead.status}" to "{new_status}".',
            )
            return redirect(redirect_url)

    old_status = lead.status
    lead.status = new_status
    auto_fields: list[str] = []

    if new_status == "contacted":
        lead.last_contacted_at = timezone.now()
        auto_fields.append("last_contacted_at")
        # Clear a follow-up that's already overdue — it's been actioned.
        if lead.next_follow_up_at and lead.next_follow_up_at < timezone.now():
            lead.next_follow_up_at = None
            auto_fields.append("next_follow_up_at")

    with transaction.atomic():
        lead.save(update_fields=["status"] + auto_fields)
        log_admin_audit(
            request=request,
            action="action",
            obj=lead,
            changes={},
            extra={
                "name": "lead_status_update",
                "from": old_status,
                "to": new_status,
                **({"auto_fields": auto_fields} if auto_fields else {}),
            },
        )
    messages.success(request, f'Status updated to "{new_status}".')
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_lead_inline_status_view(request, school_slug: str, lead_id: int):
    """
    Unconstrained inline status override from the leads list.
    POST /schools/<slug>/admin/leads/<id>/inline-status/

    Unlike school_lead_status_update_view this does NOT enforce workflow
    transitions — it mirrors Django admin list_editable behaviour so admins
    can correct mistakes regardless of configured workflow.
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    new_status = (request.POST.get("new_status") or "").strip()
    next_url = request.POST.get("next", "").strip()
    fallback = reverse("school_leads", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    lead = get_object_or_404(Lead, id=lead_id, school=school)

    if new_status not in {c[0] for c in LEAD_STATUS_CHOICES}:
        messages.error(request, f"'{new_status}' is not a valid lead status.")
        return redirect(redirect_url)

    if new_status == lead.status:
        return redirect(redirect_url)

    old_status = lead.status
    lead.status = new_status
    with transaction.atomic():
        lead.save(update_fields=["status"])
        log_admin_audit(
            request=request,
            action="action",
            obj=lead,
            changes={"status": {"from": old_status, "to": new_status}},
            extra={"name": "inline_status_update"},
        )
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_submission_inline_status_view(request, school_slug: str, submission_id: int):
    """
    Unconstrained inline status override from the submissions list.
    POST /schools/<slug>/admin/submissions/<id>/inline-status/

    Does NOT enforce workflow transitions — mirrors Django admin list_editable
    behaviour so admins can correct status mistakes regardless of configured workflow.
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    new_status = (request.POST.get("new_status") or "").strip()
    next_url = request.POST.get("next", "").strip()
    fallback = reverse("school_submissions", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    submission = get_object_or_404(Submission, id=submission_id, school=school)
    config_raw = load_school_config(school.slug)
    allowed_statuses, _ = get_submission_status_choices(config_raw)

    if new_status not in allowed_statuses:
        messages.error(request, f"'{new_status}' is not a valid submission status.")
        return redirect(redirect_url)

    if new_status == submission.status:
        return redirect(redirect_url)

    old_status = submission.status
    submission.status = new_status
    with transaction.atomic():
        submission.save(update_fields=["status"])
        log_admin_audit(
            request=request,
            action="action",
            obj=submission,
            changes={"status": {"from": old_status, "to": new_status}},
            extra={"name": "inline_status_update"},
        )
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_lead_bulk_status_update_view(request, school_slug: str):
    """
    Bulk inline status transition for multiple leads.
    POST /schools/<slug>/admin/leads/bulk-status/

    POST params:
      lead_ids    — repeated (one value per selected lead)
      new_status  — target status string
      next        — (optional) full local path+query for redirect; falls back to leads list

    Per-lead logic: transition validated against YAML; eligible → update + audit;
    ineligible → skip + count. Flash message reports both counts.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    new_status = (request.POST.get("new_status") or "").strip()
    next_url   = (request.POST.get("next") or "").strip()
    raw_ids    = request.POST.getlist("lead_ids")

    fallback     = reverse("school_leads", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    if not raw_ids:
        messages.error(request, "No leads selected.")
        return redirect(redirect_url)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}

    # 1. Target status must be a valid Lead model status.
    if new_status not in {c[0] for c in LEAD_STATUS_CHOICES}:
        messages.error(request, f"'{new_status}' is not a valid lead status.")
        return redirect(redirect_url)

    # 2. If a lead workflow is configured, enforce allowed transitions per lead.
    #    If no workflow configured, any valid status is allowed (free-for-all).
    transitions = get_lead_workflow_transitions(config_raw)

    # 3. Parse IDs — ignore non-integer values silently.
    ids = []
    for sid in raw_ids:
        try:
            ids.append(int(sid))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.error(request, "No valid leads selected.")
        return redirect(redirect_url)

    # School-scoped queryset — cross-school IDs silently excluded.
    leads = list(Lead.objects.filter(id__in=ids, school=school))
    if not leads:
        messages.error(request, "No matching leads found.")
        return redirect(redirect_url)

    updated = 0
    skipped = 0
    _now = timezone.now()
    for lead in leads:
        if transitions:
            allowed_next = [t["status"] for t in transitions.get(lead.status, [])]
            if new_status not in allowed_next:
                skipped += 1
                continue
        old_status = lead.status
        lead.status = new_status
        update_fields = ["status"]
        auto_fields: list[str] = []
        if new_status == "contacted":
            lead.last_contacted_at = _now
            auto_fields.append("last_contacted_at")
            update_fields.append("last_contacted_at")
            if lead.next_follow_up_at and lead.next_follow_up_at < _now:
                lead.next_follow_up_at = None
                auto_fields.append("next_follow_up_at")
                update_fields.append("next_follow_up_at")
        lead.save(update_fields=update_fields)
        log_admin_audit(
            request=request,
            action="action",
            obj=lead,
            changes={},
            extra={
                "name": "bulk_lead_status_update",
                "from": old_status,
                "to": new_status,
                **({"auto_fields": auto_fields} if auto_fields else {}),
            },
        )
        updated += 1

    noun = "lead" if updated == 1 else "leads"
    if updated and not skipped:
        messages.success(request, f'{updated} {noun} updated to "{new_status}".')
    elif updated and skipped:
        messages.success(
            request,
            f'{updated} {noun} updated to "{new_status}". '
            f'{skipped} skipped — current status does not allow this transition.',
        )
    else:
        messages.warning(
            request,
            f'No leads updated. {skipped} skipped — '
            'current status does not allow this transition.',
        )
    return redirect(redirect_url)


# ── School admin: submission detail ──────────────────────────────────────────

@login_required
@require_http_methods(["GET"])
def school_submission_detail_view(request, school_slug: str, submission_id: int):
    """
    Read-only submission detail page for school admins.
    GET /schools/<slug>/admin/submissions/<id>/

    Shows all form fields with labels (from YAML), status, contact info,
    any linked lead (via lead.converted_submission), and audit log history.
    Status workflow actions POST to the existing status-update endpoint with
    next= pointing back to this page.
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    submission = get_object_or_404(Submission, id=submission_id, school=school)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}

    # Select the right form config for this submission's form_key.
    forms = get_forms(config) if config else {}
    form_key = submission.form_key or "default"
    form_entry = forms.get(form_key) or forms.get("default") or {}
    form_dict = form_entry.get("form", {}) if isinstance(form_entry, dict) else {}

    # build_yaml_sections populates value from submission.data for each field,
    # handling waiver, multiselect, checkbox, and unknown keys correctly.
    yaml_sections = build_yaml_sections(config, submission.data, form=form_dict) if config else []

    # Find a lead linked to this submission (reverse of lead.converted_submission).
    linked_lead = Lead.objects.filter(converted_submission=submission, school=school).first()

    # Audit log for this submission (most recent first, capped to avoid large pages).
    audit_log = (
        AdminAuditLog.objects
        .filter(model_label="core.submission", object_id=str(submission.pk))
        .select_related("actor")
        .order_by("-created_at")[:50]
    )

    # Workflow transitions for inline status buttons.
    transitions = get_submission_workflow_transitions(config_raw)
    status = submission.status or STATUS_NEW
    status_transitions = transitions.get(status, [])
    status_choices, _ = get_submission_status_choices(config_raw)

    # AI summary (Growth feature flag).
    ai_summary_enabled = school.features.ai_summary_enabled
    ai_summary_text = ""
    if ai_summary_enabled and isinstance(submission.ai_summary, dict):
        ai_summary_text = submission.ai_summary.get("summary", "")

    # Attached files for this submission.
    submission_files = list(submission.files.all().order_by("created_at"))

    # Follow-up overdue indicator.
    is_followup_overdue = bool(
        submission.next_follow_up_at and submission.next_follow_up_at < timezone.now()
    )

    # Program/class for the snapshot card.
    program_display = submission.program_display_name()

    submissions_url = reverse("school_submissions", kwargs={"school_slug": school_slug})
    detail_url = request.path

    # Prev/Next navigation — adjacent submissions by school_submission_number (or id fallback).
    def _sub_url(sub):
        return reverse("school_submission_detail", kwargs={"school_slug": school_slug, "submission_id": sub.id})

    if submission.school_submission_number:
        prev_sub = Submission.objects.filter(
            school=school,
            school_submission_number=submission.school_submission_number - 1,
        ).first()
        next_sub = Submission.objects.filter(
            school=school,
            school_submission_number=submission.school_submission_number + 1,
        ).first()
    else:
        prev_sub = Submission.objects.filter(school=school, id__lt=submission.id).order_by("-id").first()
        next_sub = Submission.objects.filter(school=school, id__gt=submission.id).order_by("id").first()

    ctx = _school_admin_base_context(request, school, "submissions")
    ctx.update({
        "submission": submission,
        "yaml_sections": yaml_sections,
        "linked_lead": linked_lead,
        "audit_log": audit_log,
        "status_transitions": status_transitions,
        "status_choices": status_choices,
        "status_css": get_submission_status_css(status),
        "parent_email": _extract_contact_field(submission.data, _PARENT_EMAIL_KEYS),
        "parent_phone": _extract_contact_field(submission.data, _PARENT_PHONE_KEYS),
        "submissions_url": submissions_url,
        "detail_url": detail_url,
        "django_admin_url": reverse("admin:core_submission_change", args=[submission.id]),
        "email_enabled": school.features.email_notifications_enabled,
        "ai_summary_enabled": ai_summary_enabled,
        "ai_summary_text": ai_summary_text,
        "submission_files": submission_files,
        "is_followup_overdue": is_followup_overdue,
        "program_display": program_display,
        "is_multi_form": len(forms) > 1,
        "prev_url": _sub_url(prev_sub) if prev_sub else None,
        "next_url": _sub_url(next_sub) if next_sub else None,
        "status_transition_keys": [t["status"] for t in status_transitions],
        "has_workflow_transitions": bool(status_transitions),
        "prev_label": f"Prev #{prev_sub.school_submission_number}" if prev_sub and prev_sub.school_submission_number else ("Prev" if prev_sub else None),
        "next_label": f"Next #{next_sub.school_submission_number}" if next_sub and next_sub.school_submission_number else ("Next" if next_sub else None),
    })
    return render(request, "school_admin/submission_detail.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_submission_update_view(request, school_slug: str, submission_id: int):
    """
    Update a submission's internal notes.
    POST /schools/<slug>/admin/submissions/<id>/update/

    Accepts:
      internal_notes — free-text string (blank clears the field)
      next           — redirect target after save (validated via _safe_redirect_url)
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    submission = get_object_or_404(Submission, id=submission_id, school=school)

    next_url = request.POST.get("next", "").strip()
    redirect_url = _safe_redirect_url(
        request, next_url,
        reverse("school_submissions", kwargs={"school_slug": school_slug}),
    )

    new_note = request.POST.get("new_note", "").strip()
    if new_note:
        ts = timezone.localtime(timezone.now()).strftime("%-m/%-d/%Y %-I:%M %p")
        notes_to_save = f"[{ts}] {new_note}"
        if submission.internal_notes:
            notes_to_save = f"[{ts}] {new_note}\n\n{submission.internal_notes}"
    else:
        notes_to_save = submission.internal_notes or ""

    if notes_to_save == (submission.internal_notes or ""):
        messages.info(request, "No changes made.")
        return redirect(redirect_url)

    submission.internal_notes = notes_to_save
    with transaction.atomic():
        submission.save(update_fields=["internal_notes", "updated_at"])
        log_admin_audit(
            request=request,
            action="action",
            obj=submission,
            changes={},
            extra={"name": "submission_update", "fields": ["internal_notes"]},
        )

    messages.success(request, "Submission updated successfully.")
    return redirect(redirect_url)


# ── School admin: lead detail ─────────────────────────────────────────────────

@login_required
@require_http_methods(["GET"])
def school_lead_detail_view(request, school_slug: str, lead_id: int):
    """
    Read-only lead detail page for school admins.
    GET /schools/<slug>/admin/leads/<id>/

    Pure read: no draft creation, no session writes, no audit logs.
    Enrollment buttons (Open Form / Start Enrollment) are determined by whether
    an active draft already exists — creation happens only via school_lead_start_enrollment_view.
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    lead = get_object_or_404(Lead, id=lead_id, school=school)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}

    # Workflow transitions for inline status buttons.
    workflow_transitions = get_lead_workflow_transitions(config_raw)
    status_transitions = workflow_transitions.get(lead.status, [])

    # Audit log for this lead.
    audit_log = (
        AdminAuditLog.objects
        .filter(model_label="core.lead", object_id=str(lead.pk))
        .select_related("actor")
        .order_by("-created_at")[:50]
    )

    leads_url = reverse("school_leads", kwargs={"school_slug": school_slug})
    detail_url = request.path

    # Enrollment section context — read-only, no side effects.
    converted_submission_url = None
    form_url = None
    resume_url = None
    start_enrollment_url = None

    if lead.converted_submission_id:
        converted_submission_url = reverse(
            "school_submission_detail",
            kwargs={"school_slug": school_slug, "submission_id": lead.converted_submission_id},
        )
    elif lead.status != LEAD_STATUS_LOST:
        start_enrollment_url = reverse(
            "school_lead_start_enrollment",
            kwargs={"school_slug": school_slug, "lead_id": lead_id},
        )
        # Read-only: check for an existing active draft (no creation, no session, no audit).
        existing_draft = (
            DraftSubmission.objects
            .filter(school=school, lead=lead, submitted_at__isnull=True)
            .exclude(token_expires_at__lt=timezone.now())
            .order_by("-created_at")
            .first()
        )
        if existing_draft:
            form_url = reverse("apply", kwargs={"school_slug": school_slug})
            resume_url = request.build_absolute_uri(
                reverse("apply_resume", kwargs={"school_slug": school_slug, "token": existing_draft.token})
            )

    is_followup_overdue = bool(
        lead.next_follow_up_at and lead.next_follow_up_at < timezone.now()
    )

    program_options = get_program_options(config) if config else []

    # Breadcrumb pipeline — ordered list of (value, label) pairs.
    lead_pipeline = [{"value": v, "label": l} for v, l in LEAD_STATUS_CHOICES]
    status_transition_keys = [t["status"] for t in status_transitions]
    has_workflow_transitions = bool(status_transitions)

    # Prev/Next navigation by lead id (leads have no sequential number field).
    def _lead_url(l):
        return reverse("school_lead_detail", kwargs={"school_slug": school_slug, "lead_id": l.id})

    prev_lead = Lead.objects.filter(school=school, id__lt=lead.id).order_by("-id").first()
    next_lead = Lead.objects.filter(school=school, id__gt=lead.id).order_by("id").first()

    ctx = _school_admin_base_context(request, school, "leads")
    ctx.update({
        "lead": lead,
        "status_transitions": status_transitions,
        "status_css": _LEAD_STATUS_CSS.get(lead.status, "dash-badge--gray"),
        "audit_log": audit_log,
        "leads_url": leads_url,
        "detail_url": detail_url,
        "form_url": form_url,
        "resume_url": resume_url,
        "start_enrollment_url": start_enrollment_url,
        "converted_submission_url": converted_submission_url,
        "is_followup_overdue": is_followup_overdue,
        "program_options": program_options,
        "django_admin_url": reverse("admin:core_lead_change", args=[lead.id]),
        "email_enabled": school.features.email_notifications_enabled,
        "lead_pipeline": lead_pipeline,
        "status_transition_keys": status_transition_keys,
        "has_workflow_transitions": has_workflow_transitions,
        "prev_url": _lead_url(prev_lead) if prev_lead else None,
        "next_url": _lead_url(next_lead) if next_lead else None,
        "prev_label": "Prev",
        "next_label": "Next",
    })
    return render(request, "school_admin/lead_detail.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_lead_start_enrollment_view(request, school_slug: str, lead_id: int):
    """
    Create or reuse a DraftSubmission for a lead, set the session, and redirect
    back to the lead detail page.
    POST /schools/<slug>/admin/leads/<id>/start-enrollment/

    This is the only entry point that creates drafts, writes the session key,
    and records the start_enrollment audit entry. The GET detail view never
    creates drafts — it only reads.
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    lead = get_object_or_404(Lead, id=lead_id, school=school)
    detail_url = reverse(
        "school_lead_detail",
        kwargs={"school_slug": school_slug, "lead_id": lead_id},
    )

    if lead.converted_submission_id:
        messages.info(request, f'"{lead.name}" has already been enrolled.')
        return redirect(detail_url)

    if lead.status == LEAD_STATUS_LOST:
        messages.warning(request, "Cannot start enrollment for a lost lead.")
        return redirect(detail_url)

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}
    prefill = _build_lead_prefill_data(lead, config_raw)

    # select_for_update() inside atomic() prevents two concurrent POSTs from
    # both seeing no draft and each inserting their own — the first acquires the
    # row lock, the second waits, then finds the already-created draft.
    with transaction.atomic():
        draft = (
            DraftSubmission.objects
            .select_for_update()
            .filter(school=school, lead=lead, submitted_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        is_new_draft = draft is None
        if is_new_draft:
            draft = DraftSubmission.objects.create(
                school=school,
                lead=lead,
                data=prefill,
                email=lead.email,
            )
        else:
            draft.data = prefill
            draft.extend_expiry()
            draft.save(update_fields=["data", "token_expires_at", "updated_at"])

    # Set session so "Open Form →" works immediately in this browser.
    request.session[_draft_session_key(school_slug)] = draft.pk

    if is_new_draft:
        log_admin_audit(
            request=request,
            action="action",
            obj=lead,
            changes={},
            extra={"name": "start_enrollment", "draft_id": draft.pk},
        )

    return redirect(detail_url)


@login_required
@require_http_methods(["POST"])
def school_lead_update_view(request, school_slug: str, lead_id: int):
    """
    Update a lead's core fields, notes, and/or follow-up date.
    POST /schools/<slug>/admin/leads/<id>/update/

    Accepts:
      name               — required; lead display name
      email              — required; validated with django.core.validators.validate_email
      phone              — optional
      interested_in_value — optional; program interest value key
      notes              — free-text string (blank clears the field)
      next_follow_up_at  — date string YYYY-MM-DD (blank clears the field)
      follow_up_delta    — int days from today (sent by quick "+N days" buttons;
                           takes priority over next_follow_up_at when present)
      next               — redirect target after save (validated via _safe_redirect_url)
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    lead = get_object_or_404(Lead, id=lead_id, school=school)

    next_url = request.POST.get("next", "").strip()
    redirect_url = _safe_redirect_url(
        request, next_url,
        reverse("school_leads", kwargs={"school_slug": school_slug}),
    )

    # --- Validate required fields ---
    new_name = request.POST.get("name", "").strip()
    new_email = request.POST.get("email", "").strip()

    if not new_name:
        messages.error(request, "Name is required.")
        return redirect(redirect_url)
    if not new_email:
        messages.error(request, "Email is required.")
        return redirect(redirect_url)
    try:
        _validate_email(new_email)
    except _ValidationError:
        messages.error(request, "Enter a valid email address.")
        return redirect(redirect_url)

    new_phone = request.POST.get("phone", "").strip()
    new_interested_in_value = request.POST.get("interested_in_value", "").strip()

    # Resolve label for interested_in from config program options.
    config = _safe_load_school_config(school_slug)
    program_options = get_program_options(config) if config else []
    label_map = {opt["value"]: opt["label"] for opt in program_options}
    new_interested_in_label = label_map.get(new_interested_in_value, new_interested_in_value)

    new_note = request.POST.get("new_note", "").strip()
    if new_note:
        ts = timezone.localtime(timezone.now()).strftime("%-m/%-d/%Y %-I:%M %p")
        notes_to_save = f"[{ts}] {new_note}"
        if lead.notes:
            notes_to_save = f"[{ts}] {new_note}\n\n{lead.notes}"
    else:
        notes_to_save = lead.notes or ""
    follow_up_delta_raw = request.POST.get("follow_up_delta", "").strip()
    follow_up_date_raw = request.POST.get("next_follow_up_at", "").strip()

    # Validate and parse follow-up — must succeed before any DB write.
    new_follow_up = None
    if follow_up_delta_raw:
        try:
            delta = int(follow_up_delta_raw)
            target_date = timezone.now().date() + timedelta(days=delta)
            new_follow_up = timezone.make_aware(
                datetime(target_date.year, target_date.month, target_date.day)
            )
        except (ValueError, TypeError):
            messages.error(request, "Invalid follow-up offset.")
            return redirect(redirect_url)
    elif follow_up_date_raw:
        try:
            parsed = date.fromisoformat(follow_up_date_raw)
            new_follow_up = timezone.make_aware(
                datetime(parsed.year, parsed.month, parsed.day)
            )
        except ValueError:
            messages.error(request, "Invalid follow-up date. Use YYYY-MM-DD format.")
            return redirect(redirect_url)

    # --- No-op detection ---
    changed_fields = []
    if lead.name != new_name:
        changed_fields.append("name")
    if (lead.email or "") != new_email:
        changed_fields.append("email")
    if (lead.phone or "") != new_phone:
        changed_fields.append("phone")
    if (lead.interested_in_value or "") != new_interested_in_value:
        changed_fields.append("interested_in")
    if (lead.notes or "") != notes_to_save:
        changed_fields.append("notes")
    # Compare follow-up dates by date portion only.
    existing_follow_up_date = lead.next_follow_up_at.date() if lead.next_follow_up_at else None
    new_follow_up_date = new_follow_up.date() if new_follow_up else None
    if existing_follow_up_date != new_follow_up_date:
        changed_fields.append("next_follow_up_at")

    if not changed_fields:
        messages.info(request, "No changes made.")
        return redirect(redirect_url)

    lead.name = new_name
    lead.email = new_email
    lead.phone = new_phone
    lead.interested_in_value = new_interested_in_value
    lead.interested_in_label = new_interested_in_label
    lead.notes = notes_to_save
    lead.next_follow_up_at = new_follow_up
    # normalized_email and normalized_phone are set by Lead.save() — include them so
    # generated columns stay in sync when contact fields change.
    with transaction.atomic():
        lead.save(update_fields=[
            "name", "email", "phone",
            "interested_in_value", "interested_in_label",
            "notes", "next_follow_up_at",
            "normalized_email", "normalized_phone",
            "updated_at",
        ])
        log_admin_audit(
            request=request,
            action="action",
            obj=lead,
            changes={},
            extra={"name": "lead_update", "fields": changed_fields},
        )

    messages.success(request, "Lead updated successfully.")
    return redirect(redirect_url)


@login_required
@require_http_methods(["GET", "POST"])
def school_lead_create_view(request, school_slug: str):
    """
    Create a new lead manually.
    GET  /schools/<slug>/admin/leads/new/  → render lead_form.html (blank)
    POST /schools/<slug>/admin/leads/new/  → validate → create → redirect to lead detail

    Feature-gated: requires leads_enabled or superuser.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    if not school.features.leads_enabled and not request.user.is_superuser:
        return render(
            request,
            "feature_disabled.html",
            {
                "school": school,
                "school_slug": school_slug,
                "feature_name": "Leads",
                "message": "Creating leads is not available — the leads pipeline is not enabled for this school.",
                "required_plan": "Starter",
                "billing_url": reverse("school_billing", kwargs={"school_slug": school_slug}),
            },
            status=403,
        )

    config = _safe_load_school_config(school_slug)
    program_options = get_program_options(config) if config else []

    leads_url = reverse("school_leads", kwargs={"school_slug": school_slug})

    if request.method == "GET":
        ctx = _school_admin_base_context(request, school, "leads")
        ctx.update({
            "form_heading": "New Lead",
            "form_action": request.path,
            "cancel_url": leads_url,
            "program_options": program_options,
            "values": {},
        })
        return render(request, "school_admin/lead_form.html", ctx)

    # POST: validate and create.
    new_name = request.POST.get("name", "").strip()
    new_email = request.POST.get("email", "").strip()
    new_phone = request.POST.get("phone", "").strip()
    new_interested_in_value = request.POST.get("interested_in_value", "").strip()
    notes = request.POST.get("notes", "")

    errors = {}
    if not new_name:
        errors["name"] = "Name is required."
    if not new_email:
        errors["email"] = "Email is required."
    else:
        try:
            _validate_email(new_email)
        except _ValidationError:
            errors["email"] = "Enter a valid email address."

    if errors:
        ctx = _school_admin_base_context(request, school, "leads")
        ctx.update({
            "form_heading": "New Lead",
            "form_action": request.path,
            "cancel_url": leads_url,
            "program_options": program_options,
            "values": request.POST,
            "errors": errors,
        })
        return render(request, "school_admin/lead_form.html", ctx)

    label_map = {opt["value"]: opt["label"] for opt in program_options}
    new_interested_in_label = label_map.get(new_interested_in_value, new_interested_in_value)

    try:
        with transaction.atomic():
            lead = Lead.objects.create(
                school=school,
                name=new_name,
                email=new_email,
                phone=new_phone,
                interested_in_value=new_interested_in_value,
                interested_in_label=new_interested_in_label,
                notes=notes,
                source="manual",
            )
            log_admin_audit(
                request=request,
                action="add",
                obj=lead,
                changes={},
                extra={"name": "lead_created"},
            )
    except IntegrityError:
        logger.warning("Duplicate lead email for school %r: %s", school_slug, new_email)
        ctx = _school_admin_base_context(request, school, "leads")
        ctx.update({
            "form_heading": "New Lead",
            "form_action": request.path,
            "cancel_url": leads_url,
            "program_options": program_options,
            "values": request.POST,
            "errors": {"email": "A lead with this email already exists for this school."},
        })
        return render(request, "school_admin/lead_form.html", ctx)

    messages.success(request, f'Lead "{lead.name}" created successfully.')
    return redirect(reverse("school_lead_detail", kwargs={"school_slug": school_slug, "lead_id": lead.id}))


@login_required
@require_http_methods(["GET", "POST"])
def school_submission_create_view(request, school_slug: str):
    """
    Create a new submission manually (admin).
    GET  /schools/<slug>/admin/submissions/new/  → render submission_form.html
    POST /schools/<slug>/admin/submissions/new/  → validate → create → redirect to detail
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    config = _safe_load_school_config(school_slug)
    available_forms = get_forms(config) if config else {}

    requested_key = (request.GET.get("form_key") or request.POST.get("_form_key") or "").strip()
    form_key = requested_key if requested_key in available_forms else (list(available_forms.keys()) or ["default"])[0]
    selected_form = available_forms.get(form_key, {})
    raw_form_cfg = selected_form.get("form") or {}
    form_cfg = _strip_file_fields(raw_form_cfg)

    submissions_url = reverse("school_submissions", kwargs={"school_slug": school_slug})
    is_multi = len(available_forms) > 1

    if request.method == "GET":
        yaml_sections = build_yaml_sections(config, existing_data={}, form=raw_form_cfg)
        for section in yaml_sections:
            for field in section.get("fields", []):
                field["error"] = ""
        ctx = _school_admin_base_context(request, school, "submissions")
        ctx.update({
            "form_heading": "New Submission",
            "form_action": request.path,
            "cancel_url": submissions_url,
            "yaml_sections": yaml_sections,
            "available_forms": available_forms,
            "form_key": form_key,
            "is_multi": is_multi,
            "show_notes": False,
            "has_file_fields": False,
        })
        return render(request, "school_admin/submission_form.html", ctx)

    # POST
    cleaned, errors = validate_submission(form_cfg, request.POST, files_data={})

    if errors:
        post_values = _plain_post_values(request.POST, raw_form_cfg)
        yaml_sections = build_yaml_sections(config, existing_data=post_values, form=raw_form_cfg)
        for section in yaml_sections:
            for field in section.get("fields", []):
                field["error"] = errors.get(field.get("key", ""), "")
        ctx = _school_admin_base_context(request, school, "submissions")
        ctx.update({
            "form_heading": "New Submission",
            "form_action": request.path,
            "cancel_url": submissions_url,
            "yaml_sections": yaml_sections,
            "available_forms": available_forms,
            "form_key": form_key,
            "is_multi": is_multi,
            "show_notes": False,
            "has_file_fields": False,
        })
        return render(request, "school_admin/submission_form.html", ctx)

    with transaction.atomic():
        submission = Submission.objects.create(
            school=school,
            form_key=form_key,
            data=cleaned,
        )
        log_admin_audit(
            request=request,
            action="add",
            obj=submission,
            changes={},
            extra={"name": "submission_created", "form_key": form_key},
        )

    messages.success(request, "Submission created successfully.")
    return redirect(reverse(
        "school_submission_detail",
        kwargs={"school_slug": school_slug, "submission_id": submission.id},
    ))


@login_required
@require_http_methods(["GET", "POST"])
def school_submission_edit_view(request, school_slug: str, submission_id: int):
    """
    Edit an existing submission's form data and internal notes.
    GET  /schools/<slug>/admin/submissions/<id>/edit/  → render submission_form.html (pre-filled)
    POST /schools/<slug>/admin/submissions/<id>/edit/  → validate → merge → save → redirect to detail
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    submission = get_object_or_404(Submission, id=submission_id, school=school)

    config = _safe_load_school_config(school_slug)
    available_forms = get_forms(config) if config else {}

    form_key = submission.form_key or "default"
    selected_form = available_forms.get(form_key, {})
    raw_form_cfg = selected_form.get("form") or {}
    form_cfg = _strip_file_fields(raw_form_cfg)

    # Check whether the original form config has any file fields (for template notice).
    original_sections = (raw_form_cfg.get("sections") or [])
    has_file_fields = any(
        f.get("type") == "file"
        for section in original_sections
        for f in (section.get("fields") or [])
    )

    detail_url = reverse(
        "school_submission_detail",
        kwargs={"school_slug": school_slug, "submission_id": submission_id},
    )

    if request.method == "GET":
        yaml_sections = build_yaml_sections(config, existing_data=submission.data, form=raw_form_cfg)
        for section in yaml_sections:
            for field in section.get("fields", []):
                field["error"] = ""
        ctx = _school_admin_base_context(request, school, "submissions")
        ctx.update({
            "form_heading": "Edit Submission",
            "form_action": request.path,
            "cancel_url": detail_url,
            "yaml_sections": yaml_sections,
            "available_forms": available_forms,
            "form_key": form_key,
            "is_multi": False,
            "show_notes": True,
            "notes_value": submission.internal_notes or "",
            "has_file_fields": has_file_fields,
        })
        return render(request, "school_admin/submission_form.html", ctx)

    # POST
    cleaned, errors = validate_submission(form_cfg, request.POST, files_data={})

    if errors:
        post_values = _plain_post_values(request.POST, raw_form_cfg)
        yaml_sections = build_yaml_sections(config, existing_data=post_values, form=raw_form_cfg)
        for section in yaml_sections:
            for field in section.get("fields", []):
                field["error"] = errors.get(field.get("key", ""), "")
        ctx = _school_admin_base_context(request, school, "submissions")
        ctx.update({
            "form_heading": "Edit Submission",
            "form_action": request.path,
            "cancel_url": detail_url,
            "yaml_sections": yaml_sections,
            "available_forms": available_forms,
            "form_key": form_key,
            "is_multi": False,
            "show_notes": True,
            "notes_value": request.POST.get("internal_notes", ""),
            "has_file_fields": has_file_fields,
        })
        return render(request, "school_admin/submission_form.html", ctx)

    # No-op detection.
    changed_fields = [k for k, v in cleaned.items() if submission.data.get(k) != v]
    new_notes = request.POST.get("internal_notes", "")
    if (submission.internal_notes or "") != new_notes:
        changed_fields.append("internal_notes")

    if not changed_fields:
        messages.info(request, "No changes made.")
        return redirect(detail_url)

    # File fields stripped from validation — their keys absent from cleaned.
    # Merge preserves all existing file-related data from submission.data.
    new_data = {**submission.data, **cleaned}
    submission.data = new_data
    submission.internal_notes = new_notes
    with transaction.atomic():
        submission.save(update_fields=["data", "internal_notes", "updated_at"])
        log_admin_audit(
            request=request,
            action="change",
            obj=submission,
            changes={},
            extra={"name": "submission_update", "fields": changed_fields},
        )

    messages.success(request, "Submission updated successfully.")
    return redirect(detail_url)


# ── Admin theme API ──────────────────────────────────────────────────────

@require_http_methods(["GET", "POST"])
def admin_theme_api(request):
    """GET: available themes + current selection.  POST: save preference."""
    if not request.user.is_authenticated or not request.user.is_staff:
        return JsonResponse({"error": "Not authorised"}, status=403)

    if request.method == "GET":
        current = DEFAULT_THEME_KEY
        try:
            current = request.user.admin_preference.theme
        except Exception:
            pass
        return JsonResponse({
            "themes": get_themes_for_api(),
            "current": current,
        })

    # POST — save preference
    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    theme_key = body.get("theme", "").strip()
    if theme_key not in ADMIN_THEMES:
        return JsonResponse({"error": f"Unknown theme: {theme_key}"}, status=400)

    old_theme = None
    pref, _created = AdminPreference.objects.get_or_create(
        user=request.user,
        defaults={"theme": theme_key},
    )
    if not _created:
        old_theme = pref.theme
        pref.theme = theme_key
        pref.save(update_fields=["theme"])

    if _created or old_theme != theme_key:
        log_admin_audit(
            request=request,
            action="action",
            obj=pref,
            changes={},
            extra={"name": "theme_change", "old": old_theme, "new": theme_key},
        )

    return JsonResponse({"ok": True, "theme": theme_key})


# ── School admin: mark-contacted + follow-up quick actions ──────────────────


@login_required
@require_http_methods(["POST"])
def school_lead_mark_contacted_view(request, school_slug: str, lead_id: int):
    """
    Mark a lead as contacted and schedule a 2-day follow-up.
    POST /schools/<slug>/admin/leads/<id>/mark-contacted/

    Sets last_contacted_at and next_follow_up_at = now + 2 days.
    Also advances status to "contacted" unless the lead is already
    contacted, enrolled, or lost.
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    lead = get_object_or_404(Lead, id=lead_id, school=school)

    next_url = request.POST.get("next", "").strip()
    redirect_url = _safe_redirect_url(
        request, next_url,
        reverse("school_leads", kwargs={"school_slug": school_slug}),
    )

    now = timezone.now()
    if (lead.last_contacted_at is not None
            and (now - lead.last_contacted_at).total_seconds() < 30):
        messages.success(request, "Lead marked as contacted.")
        return redirect(redirect_url)

    lead.last_contacted_at = now
    lead.next_follow_up_at = now + timedelta(days=2)
    update_fields = ["last_contacted_at", "next_follow_up_at"]

    if lead.status not in {"contacted", LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST}:
        lead.status = "contacted"
        update_fields.append("status")

    with transaction.atomic():
        lead.save(update_fields=update_fields)
        log_admin_audit(
            request=request,
            action="action",
            obj=lead,
            changes={},
            extra={"name": "mark_contacted"},
        )

    if (request.POST.get("send_email") == "1"
            and school.features.email_notifications_enabled
            and lead.email.strip()):
        try:
            _config = _safe_load_school_config(school_slug)
            _sent = send_workflow_notification(
                to_email=lead.email.strip(),
                student_name=lead.name,
                school_name=school.display_name,
                notification_type="contacted",
                config_raw=getattr(_config, "raw", {}),
                from_email=_resolve_from_email(getattr(_config, "raw", {})),
            )
            if _sent:
                log_admin_audit(
                    request=request,
                    action="action",
                    obj=lead,
                    changes={},
                    extra={"name": "workflow_email_sent", "type": "contacted", "to": lead.email.strip()},
                )
        except Exception:
            logger.exception("Non-blocking: workflow email failed for lead %s", lead.pk)

    messages.success(request, "Lead marked as contacted.")
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_submission_mark_contacted_view(request, school_slug: str, submission_id: int):
    """
    Mark a submission as contacted and schedule a 2-day follow-up.
    POST /schools/<slug>/admin/submissions/<id>/mark-contacted/
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    submission = get_object_or_404(Submission, id=submission_id, school=school)

    next_url = request.POST.get("next", "").strip()
    redirect_url = _safe_redirect_url(
        request, next_url,
        reverse("school_submissions", kwargs={"school_slug": school_slug}),
    )

    now = timezone.now()
    if (submission.last_contacted_at is not None
            and (now - submission.last_contacted_at).total_seconds() < 30):
        messages.success(request, "Submission marked as contacted.")
        return redirect(redirect_url)

    submission.last_contacted_at = now
    submission.next_follow_up_at = now + timedelta(days=2)
    update_fields = ["last_contacted_at", "next_follow_up_at", "updated_at"]

    # Advance status to the school's "Contacted" status if one exists and the
    # submission isn't already there or in a terminal state.
    _config_raw = getattr(_safe_load_school_config(school_slug), "raw", {}) or {}
    _allowed, _ = get_submission_status_choices(_config_raw)
    _contacted_status = next((s for s in _allowed if s.lower() == "contacted"), None)
    if _contacted_status:
        _terminal = {s for s in _allowed if s.lower() in {"enrolled", "closed", "archived"}}
        if submission.status != _contacted_status and submission.status not in _terminal:
            submission.status = _contacted_status
            update_fields.append("status")

    with transaction.atomic():
        submission.save(update_fields=update_fields)
        log_admin_audit(
            request=request,
            action="action",
            obj=submission,
            changes={},
            extra={"name": "mark_contacted"},
        )

    if request.POST.get("send_email") == "1" and school.features.email_notifications_enabled:
        _to = _extract_contact_field(submission.data, _PARENT_EMAIL_KEYS).strip()
        if _to:
            try:
                _config = _safe_load_school_config(school_slug)
                _sent = send_workflow_notification(
                    to_email=_to,
                    student_name=submission.student_display_name() or "",
                    school_name=school.display_name,
                    notification_type="contacted",
                    config_raw=getattr(_config, "raw", {}),
                    from_email=_resolve_from_email(getattr(_config, "raw", {})),
                )
                if _sent:
                    log_admin_audit(
                        request=request,
                        action="action",
                        obj=submission,
                        changes={},
                        extra={"name": "workflow_email_sent", "type": "contacted", "to": _to},
                    )
            except Exception:
                logger.exception(
                    "Non-blocking: workflow email failed for submission %s", submission.pk
                )

    messages.success(request, "Submission marked as contacted.")
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_submission_follow_up_set_view(request, school_slug: str, submission_id: int):
    """
    Set follow-up date for a submission.
    POST /schools/<slug>/admin/submissions/<id>/follow-up/

    Accepts:
      next_follow_up_at — date string YYYY-MM-DD
      follow_up_delta   — int days from today (takes priority)
      next              — redirect target
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    submission = get_object_or_404(Submission, id=submission_id, school=school)

    next_url = request.POST.get("next", "").strip()
    redirect_url = _safe_redirect_url(
        request, next_url,
        reverse("school_submissions", kwargs={"school_slug": school_slug}),
    )

    follow_up_delta_raw = request.POST.get("follow_up_delta", "").strip()
    follow_up_date_raw = request.POST.get("next_follow_up_at", "").strip()

    new_follow_up = None
    if follow_up_delta_raw:
        try:
            delta = int(follow_up_delta_raw)
            target_date = timezone.now().date() + timedelta(days=delta)
            new_follow_up = timezone.make_aware(
                datetime(target_date.year, target_date.month, target_date.day)
            )
        except (ValueError, TypeError):
            messages.error(request, "Invalid follow-up offset.")
            return redirect(redirect_url)
    elif follow_up_date_raw:
        try:
            parsed = date.fromisoformat(follow_up_date_raw)
            new_follow_up = timezone.make_aware(
                datetime(parsed.year, parsed.month, parsed.day)
            )
        except ValueError:
            messages.error(request, "Invalid follow-up date. Use YYYY-MM-DD format.")
            return redirect(redirect_url)
    else:
        messages.error(request, "No follow-up date provided.")
        return redirect(redirect_url)

    submission.next_follow_up_at = new_follow_up
    with transaction.atomic():
        submission.save(update_fields=["next_follow_up_at", "updated_at"])
        log_admin_audit(
            request=request,
            action="action",
            obj=submission,
            changes={},
            extra={"name": "follow_up_set"},
        )

    if request.POST.get("send_email") == "1" and school.features.email_notifications_enabled:
        _to = _extract_contact_field(submission.data, _PARENT_EMAIL_KEYS).strip()
        if _to:
            try:
                _config = _safe_load_school_config(school_slug)
                _sent = send_workflow_notification(
                    to_email=_to,
                    student_name=submission.student_display_name() or "",
                    school_name=school.display_name,
                    notification_type="follow_up",
                    config_raw=getattr(_config, "raw", {}),
                    from_email=_resolve_from_email(getattr(_config, "raw", {})),
                )
                if _sent:
                    log_admin_audit(
                        request=request,
                        action="action",
                        obj=submission,
                        changes={},
                        extra={"name": "workflow_email_sent", "type": "follow_up", "to": _to},
                    )
            except Exception:
                logger.exception(
                    "Non-blocking: workflow email failed for submission %s", submission.pk
                )

    messages.success(request, "Follow-up date set.")
    return redirect(redirect_url)


# ── School admin: bulk follow-up actions ─────────────────────────────────────


@login_required
@require_http_methods(["POST"])
def school_lead_bulk_mark_contacted_view(request, school_slug: str):
    """
    Bulk mark leads as contacted with 2-day follow-up scheduling.
    POST /schools/<slug>/admin/leads/bulk-mark-contacted/
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    next_url = request.POST.get("next", "").strip()
    raw_ids = request.POST.getlist("lead_ids")
    fallback = reverse("school_leads", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    ids = []
    for sid in raw_ids:
        try:
            ids.append(int(sid))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.error(request, "No leads selected.")
        return redirect(redirect_url)

    leads = list(Lead.objects.filter(id__in=ids, school=school))
    if not leads:
        messages.error(request, "No matching leads found.")
        return redirect(redirect_url)

    now = timezone.now()
    updated = 0
    with transaction.atomic():
        for lead in leads:
            lead.last_contacted_at = now
            lead.next_follow_up_at = now + timedelta(days=2)
            update_fields = ["last_contacted_at", "next_follow_up_at"]
            if lead.status not in {"contacted", LEAD_STATUS_ENROLLED, LEAD_STATUS_LOST}:
                lead.status = "contacted"
                update_fields.append("status")
            lead.save(update_fields=update_fields)
            log_admin_audit(
                request=request,
                action="action",
                obj=lead,
                changes={},
                extra={"name": "bulk_mark_contacted"},
            )
            updated += 1

    messages.success(request, f"{updated} lead{'s' if updated != 1 else ''} marked as contacted.")
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_lead_bulk_follow_up_view(request, school_slug: str):
    """
    Bulk set follow-up date for leads.
    POST /schools/<slug>/admin/leads/bulk-follow-up/

    Accepts: lead_ids (repeated), follow_up_date (YYYY-MM-DD), next.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    next_url = request.POST.get("next", "").strip()
    raw_ids = request.POST.getlist("lead_ids")
    follow_up_date_raw = request.POST.get("follow_up_date", "").strip()
    fallback = reverse("school_leads", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    ids = []
    for sid in raw_ids:
        try:
            ids.append(int(sid))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.error(request, "No leads selected.")
        return redirect(redirect_url)

    if not follow_up_date_raw:
        messages.error(request, "No follow-up date provided.")
        return redirect(redirect_url)

    try:
        parsed = date.fromisoformat(follow_up_date_raw)
        follow_up_dt = timezone.make_aware(datetime(parsed.year, parsed.month, parsed.day))
    except ValueError:
        messages.error(request, "Invalid follow-up date. Use YYYY-MM-DD format.")
        return redirect(redirect_url)

    leads = list(Lead.objects.filter(id__in=ids, school=school))
    if not leads:
        messages.error(request, "No matching leads found.")
        return redirect(redirect_url)

    updated = 0
    with transaction.atomic():
        for lead in leads:
            lead.next_follow_up_at = follow_up_dt
            lead.save(update_fields=["next_follow_up_at"])
            log_admin_audit(
                request=request,
                action="action",
                obj=lead,
                changes={},
                extra={"name": "bulk_follow_up_set"},
            )
            updated += 1

    messages.success(request, f"Follow-up date set for {updated} lead{'s' if updated != 1 else ''}.")
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_lead_bulk_clear_follow_up_view(request, school_slug: str):
    """
    Bulk clear follow-up date for leads.
    POST /schools/<slug>/admin/leads/bulk-clear-follow-up/
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    next_url = request.POST.get("next", "").strip()
    raw_ids = request.POST.getlist("lead_ids")
    fallback = reverse("school_leads", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    ids = []
    for sid in raw_ids:
        try:
            ids.append(int(sid))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.error(request, "No leads selected.")
        return redirect(redirect_url)

    leads = list(Lead.objects.filter(id__in=ids, school=school))
    if not leads:
        messages.error(request, "No matching leads found.")
        return redirect(redirect_url)

    updated = 0
    with transaction.atomic():
        for lead in leads:
            if lead.next_follow_up_at is not None:
                lead.next_follow_up_at = None
                lead.save(update_fields=["next_follow_up_at"])
                log_admin_audit(
                    request=request,
                    action="action",
                    obj=lead,
                    changes={},
                    extra={"name": "bulk_clear_follow_up"},
                )
                updated += 1

    if updated:
        messages.success(request, f"Follow-up cleared for {updated} lead{'s' if updated != 1 else ''}.")
    else:
        messages.info(request, "No leads had a follow-up date set.")
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_submission_bulk_mark_contacted_view(request, school_slug: str):
    """
    Bulk mark submissions as contacted with 2-day follow-up scheduling.
    POST /schools/<slug>/admin/submissions/bulk-mark-contacted/
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    next_url = request.POST.get("next", "").strip()
    raw_ids = request.POST.getlist("submission_ids")
    fallback = reverse("school_submissions", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    ids = []
    for sid in raw_ids:
        try:
            ids.append(int(sid))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.error(request, "No submissions selected.")
        return redirect(redirect_url)

    submissions = list(Submission.objects.filter(id__in=ids, school=school))
    if not submissions:
        messages.error(request, "No matching submissions found.")
        return redirect(redirect_url)

    _config_raw = getattr(_safe_load_school_config(school_slug), "raw", {}) or {}
    _allowed, _ = get_submission_status_choices(_config_raw)
    _contacted_status = next((s for s in _allowed if s.lower() == "contacted"), None)
    _terminal = {s for s in _allowed if s.lower() in {"enrolled", "closed", "archived"}}

    now = timezone.now()
    updated = 0
    with transaction.atomic():
        for submission in submissions:
            submission.last_contacted_at = now
            submission.next_follow_up_at = now + timedelta(days=2)
            _fields = ["last_contacted_at", "next_follow_up_at", "updated_at"]
            if (_contacted_status
                    and submission.status != _contacted_status
                    and submission.status not in _terminal):
                submission.status = _contacted_status
                _fields.append("status")
            submission.save(update_fields=_fields)
            log_admin_audit(
                request=request,
                action="action",
                obj=submission,
                changes={},
                extra={"name": "bulk_mark_contacted"},
            )
            updated += 1

    messages.success(request, f"{updated} submission{'s' if updated != 1 else ''} marked as contacted.")
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_submission_bulk_follow_up_view(request, school_slug: str):
    """
    Bulk set follow-up date for submissions.
    POST /schools/<slug>/admin/submissions/bulk-follow-up/

    Accepts: submission_ids (repeated), follow_up_date (YYYY-MM-DD), next.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    next_url = request.POST.get("next", "").strip()
    raw_ids = request.POST.getlist("submission_ids")
    follow_up_date_raw = request.POST.get("follow_up_date", "").strip()
    fallback = reverse("school_submissions", kwargs={"school_slug": school_slug})
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    ids = []
    for sid in raw_ids:
        try:
            ids.append(int(sid))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.error(request, "No submissions selected.")
        return redirect(redirect_url)

    if not follow_up_date_raw:
        messages.error(request, "No follow-up date provided.")
        return redirect(redirect_url)

    try:
        parsed = date.fromisoformat(follow_up_date_raw)
        follow_up_dt = timezone.make_aware(datetime(parsed.year, parsed.month, parsed.day))
    except ValueError:
        messages.error(request, "Invalid follow-up date. Use YYYY-MM-DD format.")
        return redirect(redirect_url)

    submissions = list(Submission.objects.filter(id__in=ids, school=school))
    if not submissions:
        messages.error(request, "No matching submissions found.")
        return redirect(redirect_url)

    updated = 0
    with transaction.atomic():
        for submission in submissions:
            submission.next_follow_up_at = follow_up_dt
            submission.save(update_fields=["next_follow_up_at", "updated_at"])
            log_admin_audit(
                request=request,
                action="action",
                obj=submission,
                changes={},
                extra={"name": "bulk_follow_up_set"},
            )
            updated += 1

    messages.success(request, f"Follow-up date set for {updated} submission{'s' if updated != 1 else ''}.")
    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_submission_bulk_download_view(request, school_slug: str):
    """
    Bulk download attached files for selected submissions as a ZIP.
    POST /schools/<slug>/admin/submissions/bulk-download/

    Accepts: submission_ids (repeated), next.
    Streams a ZIP containing all SubmissionFile rows for selected submissions.
    Submissions with no files are silently skipped.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    fallback = reverse("school_submissions", kwargs={"school_slug": school_slug})
    next_url = request.POST.get("next", "").strip()
    redirect_url = _safe_redirect_url(request, next_url, fallback)

    raw_ids = request.POST.getlist("submission_ids")
    ids = []
    for sid in raw_ids:
        try:
            ids.append(int(sid))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.error(request, "No submissions selected.")
        return redirect(redirect_url)

    # Enforce school scope — only files belonging to this school's submissions.
    files = list(
        SubmissionFile.objects
        .filter(submission__school=school, submission_id__in=ids)
        .select_related("submission")
        .order_by("submission_id", "id")
    )

    if not files:
        messages.error(request, "None of the selected submissions have attachments.")
        return redirect(redirect_url)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for sf in files:
            num = sf.submission.school_submission_number or sf.submission_id
            filename = sf.original_name or (sf.field_key + ".bin")
            arc_name = f"submission-{num}/{filename}"
            # Guard against duplicate names in the same submission folder.
            existing = {info.filename for info in zf.infolist()}
            base, ext = (arc_name.rsplit(".", 1) + [""])[:2] if "." in arc_name else (arc_name, "")
            counter = 1
            candidate = arc_name
            while candidate in existing:
                candidate = f"{base}_{counter}.{ext}" if ext else f"{base}_{counter}"
                counter += 1
            try:
                zf.writestr(candidate, sf.file.read())
            except Exception:
                pass  # skip unreadable files silently

    buf.seek(0)
    resp = HttpResponse(buf.read(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{school.slug}-attachments.zip"'
    return resp


_BULK_PRINT_LIMIT = 20


@login_required
@require_http_methods(["POST"])
def school_submission_bulk_print_view(request, school_slug: str):
    """
    Render a printable page for selected submissions, then auto-trigger window.print().
    POST /schools/<slug>/admin/submissions/bulk-print/

    Accepts: submission_ids (repeated).
    Returns an HTML page (no Django admin nav) that calls window.print() on load.
    Limit: 20 submissions max.
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    raw_ids = request.POST.getlist("submission_ids")
    ids = []
    for sid in raw_ids:
        try:
            ids.append(int(sid))
        except (ValueError, TypeError):
            pass

    if not ids:
        messages.error(request, "No submissions selected.")
        return redirect(reverse("school_submissions", kwargs={"school_slug": school_slug}))

    if len(ids) > _BULK_PRINT_LIMIT:
        messages.error(
            request,
            f"Please select 20 or fewer submissions to print. You selected {len(ids)}.",
        )
        return redirect(reverse("school_submissions", kwargs={"school_slug": school_slug}))

    submissions = list(
        Submission.objects
        .filter(id__in=ids, school=school)
        .prefetch_related("files")
        .order_by("school_submission_number")
    )

    if not submissions:
        messages.error(request, "No matching submissions found.")
        return redirect(reverse("school_submissions", kwargs={"school_slug": school_slug}))

    config = _safe_load_school_config(school_slug)
    config_raw = getattr(config, "raw", {}) or {}
    forms = get_forms(config) if config else {}

    rows = []
    for submission in submissions:
        form_key = submission.form_key or "default"
        form_entry = forms.get(form_key) or forms.get("default") or {}
        form_dict = form_entry.get("form", {}) if isinstance(form_entry, dict) else {}
        yaml_sections = build_yaml_sections(config, submission.data, form=form_dict) if config else []
        rows.append({
            "submission": submission,
            "yaml_sections": yaml_sections,
            "parent_email": _extract_contact_field(submission.data, _PARENT_EMAIL_KEYS),
            "parent_phone": _extract_contact_field(submission.data, _PARENT_PHONE_KEYS),
            "program_display": submission.program_display_name(),
            "files": list(submission.files.all()),
        })

    ctx = {
        "school": school,
        "rows": rows,
        "printed_at": timezone.localtime(timezone.now()),
    }
    return render(request, "school_admin/submission_bulk_print.html", ctx)


# ---------------------------------------------------------------------------
# Phase 12: admin communication actions
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["POST"])
def school_lead_send_message_view(request, school_slug: str, lead_id: int):
    """
    Send a one-off admin-composed message to a lead's email address.
    POST /schools/<slug>/admin/leads/<id>/send-message/
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    lead = get_object_or_404(Lead, id=lead_id, school=school)

    next_url = request.POST.get("next", "").strip()
    redirect_url = _safe_redirect_url(
        request, next_url,
        reverse("school_lead_detail", kwargs={"school_slug": school_slug, "lead_id": lead_id}),
    )

    if not school.features.email_notifications_enabled:
        messages.error(request, "Email is not enabled for this school.")
        return redirect(redirect_url)

    lead_email = lead.email.strip() if lead.email else ""
    if not lead_email or "@" not in lead_email:
        messages.error(request, "This lead has no valid email address on file.")
        return redirect(redirect_url)

    message = request.POST.get("message", "").strip()
    if not message:
        messages.error(request, "Message cannot be empty.")
        return redirect(redirect_url)

    config = _safe_load_school_config(school_slug)
    subject = request.POST.get("subject", "").strip() or f"Message from {school.display_name}"
    from_email = _resolve_from_email(getattr(config, "raw", {}))

    sent = send_admin_message(
        to_email=lead_email,
        subject=subject,
        message=message,
        school_name=school.display_name,
        from_email=from_email,
    )

    if sent:
        messages.success(request, f"Message sent to {lead_email}.")
        log_admin_audit(
            request=request,
            action="action",
            obj=lead,
            changes={},
            extra={"name": "manual_message_sent", "to": lead_email},
        )
    else:
        messages.error(request, "Message could not be sent. Please try again.")

    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_submission_send_message_view(request, school_slug: str, submission_id: int):
    """
    Send a one-off admin-composed message to a submission contact email.
    POST /schools/<slug>/admin/submissions/<id>/send-message/
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    submission = get_object_or_404(Submission, id=submission_id, school=school)

    next_url = request.POST.get("next", "").strip()
    redirect_url = _safe_redirect_url(
        request, next_url,
        reverse("school_submission_detail", kwargs={"school_slug": school_slug, "submission_id": submission_id}),
    )

    if not school.features.email_notifications_enabled:
        messages.error(request, "Email is not enabled for this school.")
        return redirect(redirect_url)

    post_to = request.POST.get("to_email", "").strip()
    to_email = post_to if (post_to and "@" in post_to) else _extract_contact_field(submission.data, _PARENT_EMAIL_KEYS).strip()
    if not to_email or "@" not in to_email:
        messages.error(request, "No valid email address found in this submission.")
        return redirect(redirect_url)

    message = request.POST.get("message", "").strip()
    if not message:
        messages.error(request, "Message cannot be empty.")
        return redirect(redirect_url)

    config = _safe_load_school_config(school_slug)
    subject = request.POST.get("subject", "").strip() or f"Message from {school.display_name}"
    from_email = _resolve_from_email(getattr(config, "raw", {}))

    sent = send_admin_message(
        to_email=to_email,
        subject=subject,
        message=message,
        school_name=school.display_name,
        from_email=from_email,
    )

    if sent:
        messages.success(request, f"Message sent to {to_email}.")
        log_admin_audit(
            request=request,
            action="action",
            obj=submission,
            changes={},
            extra={"name": "manual_message_sent", "to": to_email},
        )
    else:
        messages.error(request, "Message could not be sent. Please try again.")

    return redirect(redirect_url)


@login_required
@require_http_methods(["POST"])
def school_submission_resend_confirmation_view(request, school_slug: str, submission_id: int):
    """
    Resend the applicant confirmation email for a submission.
    POST /schools/<slug>/admin/submissions/<id>/resend-confirmation/
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    submission = get_object_or_404(Submission, id=submission_id, school=school)

    next_url = request.POST.get("next", "").strip()
    redirect_url = _safe_redirect_url(
        request, next_url,
        reverse("school_submission_detail", kwargs={"school_slug": school_slug, "submission_id": submission_id}),
    )

    if not school.features.email_notifications_enabled:
        messages.error(request, "Email is not enabled for this school.")
        return redirect(redirect_url)

    config = _safe_load_school_config(school_slug)

    sent = send_applicant_confirmation_email(
        config_raw=getattr(config, "raw", {}),
        school_name=school.display_name,
        submission_public_id=submission.public_id,
        student_name=submission.student_display_name() or "",
        submission_data=submission.data or {},
    )

    if sent:
        messages.success(request, "Confirmation email resent.")
        log_admin_audit(
            request=request,
            action="action",
            obj=submission,
            changes={},
            extra={"name": "resend_confirmation"},
        )
    else:
        messages.error(request, "Could not resend confirmation. Check email configuration.")

    return redirect(redirect_url)


@login_required
@staff_member_required
@require_http_methods(["POST"])
def school_submission_generate_summary_view(request, school_slug: str, submission_id: int):
    """
    Generate (or regenerate) the AI summary for a submission.
    POST /schools/<slug>/admin/submissions/<id>/generate-summary/
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    submission = get_object_or_404(Submission, id=submission_id, school=school)

    detail_url = reverse(
        "school_submission_detail",
        kwargs={"school_slug": school_slug, "submission_id": submission_id},
    )

    if not school.features.ai_summary_enabled:
        messages.error(request, "AI summary is not available for this school's plan.")
        return redirect(detail_url)

    config = _safe_load_school_config(school_slug)
    form_cfg: dict = {}
    criteria: list = []
    school_name = school.display_name or school_slug

    if config:
        raw_cfg = getattr(config, "raw", {}) or {}
        ai_cfg = raw_cfg.get("ai_summary") or {}
        criteria = list(ai_cfg.get("criteria") or [])
        forms = get_forms(config)
        form_key = submission.form_key or "default"
        matched = forms.get(form_key) or forms.get("default") or (list(forms.values())[0] if forms else None)
        if matched:
            form_cfg = matched if isinstance(matched, dict) else getattr(matched, "raw", {}) or {}

    was_regeneration = bool(submission.ai_summary)
    result, error = generate_ai_summary(
        submission_data=submission.data or {},
        school_name=school_name,
        form_cfg=form_cfg,
        criteria=criteria,
    )

    if result is not None:
        submission.ai_summary = result
        submission.ai_summary_at = timezone.now()
        submission.save(update_fields=["ai_summary", "ai_summary_at"])
        log_admin_audit(
            request=request,
            action="action",
            obj=submission,
            changes={},
            extra={"name": "regenerate_ai_summary" if was_regeneration else "generate_ai_summary"},
        )
        messages.success(request, "AI summary generated.")
    else:
        logger.warning("AI summary generation failed for submission %s: %s", submission_id, error)
        messages.error(request, f"Could not generate summary. {error}")

    return redirect(detail_url)


# ---------------------------------------------------------------------------
# Lead capture
# ---------------------------------------------------------------------------

@xframe_options_exempt
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
            "billing_url": reverse("admin:billing"),
        })

    leads_cfg = config.raw.get("leads") or {}
    program_options = get_program_options(config)

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


_FEATURE_LABELS = {
    "status_enabled":           "Application Status Tracking",
    "csv_export_enabled":       "CSV Export",
    "audit_log_enabled":        "Activity Log",
    "reports_enabled":          "Reports & Analytics",
    "email_notifications_enabled": "Email Notifications",
    "file_uploads_enabled":     "File Uploads",
    "leads_enabled":            "Leads Pipeline",
    "waiver_enabled":           "Digital Waivers",
    "custom_branding_enabled":  "Custom Branding",
    "multi_form_enabled":       "Multiple Forms",
    "custom_statuses_enabled":  "Custom Statuses",
    "leads_conversion_enabled": "Lead Conversion Tracking",
    "save_resume_enabled":      "Save & Resume Applications",
    "ai_summary_enabled":       "AI Application Summary",
}

_PLAN_DISPLAY = {
    ff.PLAN_TRIAL:   "Trial",
    ff.PLAN_STARTER: "Starter",
    ff.PLAN_PRO:     "Pro",
    ff.PLAN_GROWTH:  "Growth",
}


@login_required
def school_settings_view(request, school_slug: str):
    """
    School admin settings page.
    GET  /schools/<slug>/admin/settings/
    POST /schools/<slug>/admin/settings/  (action=update_display_name)
    """
    school = _get_accessible_school_for_admin(request, school_slug)

    if request.method == "POST" and request.POST.get("action") == "update_trial_end_date":
        if not request.user.is_superuser:
            messages.error(request, "Only superadmins can change the trial end date.")
            return redirect("school_settings", school_slug=school_slug)
        raw = request.POST.get("trial_end_date", "").strip()
        if not raw:
            school.trial_end_date = None
            school.save(update_fields=["trial_end_date"])
            log_admin_audit(
                request=request, action="action", obj=school, changes={},
                extra={"name": "clear_trial_end_date"},
            )
            messages.success(request, "Trial end date cleared — default length applies.")
        else:
            from datetime import date as _date
            try:
                new_end = _date.fromisoformat(raw)
            except ValueError:
                messages.error(request, "Invalid date format.")
                return redirect("school_settings", school_slug=school_slug)
            if new_end < _date.today():
                messages.error(request, "Trial end date must be today or in the future.")
                return redirect("school_settings", school_slug=school_slug)
            old_end = school.trial_end_date
            school.trial_end_date = new_end
            school.save(update_fields=["trial_end_date"])
            log_admin_audit(
                request=request, action="action", obj=school, changes={},
                extra={"name": "update_trial_end_date", "old": str(old_end), "new": str(new_end)},
            )
            messages.success(request, f"Trial extended to {new_end.strftime('%B %-d, %Y')}.")
        return redirect("school_settings", school_slug=school_slug)

    if request.method == "POST" and request.POST.get("action") == "update_display_name":
        new_name = request.POST.get("display_name", "").strip()
        if not new_name:
            messages.error(request, "Display name cannot be blank.")
        elif len(new_name) > 120:
            messages.error(request, "Display name must be 120 characters or fewer.")
        elif new_name == school.display_name:
            messages.info(request, "No change — display name is already set to that.")
        else:
            old_name = school.display_name
            school.display_name = new_name
            school.save(update_fields=["display_name"])
            log_admin_audit(
                request=request,
                action="action",
                obj=school,
                changes={},
                extra={"name": "update_display_name", "old": old_name, "new": new_name},
            )
            messages.success(request, f'Display name updated to "{new_name}".')
        return redirect("school_settings", school_slug=school_slug)

    apply_url = request.build_absolute_uri(
        reverse("apply", kwargs={"school_slug": school_slug})
    )
    embed_snippet = (
        f'<iframe src="{apply_url}" width="100%" height="700" '
        f'frameborder="0" style="border:none;" title="Application Form"></iframe>'
    )

    # Build feature list: enabled on current plan vs locked at a higher tier.
    current_flags = ff.merge_flags(plan=school.plan, overrides=school.feature_flags)
    school_plan_rank = ff.PLAN_RANK.get(school.plan, 0)
    features = []
    for flag, label in _FEATURE_LABELS.items():
        enabled = current_flags.get(flag, False)
        min_plan = ff._FEATURE_MIN_PLAN.get(flag, ff.PLAN_TRIAL)
        required_rank = ff.PLAN_RANK.get(min_plan, 0)
        features.append({
            "label": label,
            "enabled": enabled,
            "required_plan": _PLAN_DISPLAY.get(min_plan, min_plan),
            "is_upgrade": not enabled and required_rank > school_plan_rank,
        })

    ctx = _school_admin_base_context(request, school, "settings")
    ctx.update({
        "apply_url": apply_url,
        "embed_snippet": embed_snippet,
        "features": features,
    })
    return render(request, "school_admin/settings.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
def school_password_change_view(request, school_slug: str):
    """
    Custom password change page in the school admin UI.
    GET/POST /schools/<slug>/admin/password/
    """
    from django.contrib.auth.forms import PasswordChangeForm
    from django.contrib.auth import update_session_auth_hash

    school = _get_accessible_school_for_admin(request, school_slug)

    if request.method == "POST":
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)
            messages.success(request, "Password updated successfully.")
            return redirect("school_settings", school_slug=school_slug)
    else:
        form = PasswordChangeForm(user=request.user)

    ctx = _school_admin_base_context(request, school, "settings")
    ctx["form"] = form
    return render(request, "school_admin/password_change.html", ctx)


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


# ── School admin: billing ─────────────────────────────────────────────────

@login_required
@require_http_methods(["GET"])
def school_billing_view(request, school_slug: str):
    """Billing & plan page in the school admin UI.
    GET /schools/<slug>/admin/billing/
    """
    from core.services.billing_stripe import (
        get_pricing_options, is_stripe_configured,
    )
    school = _get_accessible_school_for_admin(request, school_slug)
    stripe_configured = is_stripe_configured()
    pricing_flat = get_pricing_options() if stripe_configured else []

    # Group flat list into {plan: {monthly, annual}} for clean template rendering
    pricing: dict = {}
    for opt in pricing_flat:
        plan = opt["plan"]
        interval = opt["interval"]
        if plan not in pricing:
            pricing[plan] = {}
        pricing[plan][interval] = opt

    has_subscription = bool(school.stripe_customer_id and school.stripe_subscription_id)
    status = school.stripe_subscription_status
    scheduled_cancel = bool(school.stripe_cancel_at or school.stripe_cancel_at_period_end)
    is_locked = not school.is_active

    from django.utils import timezone as djtz
    cancel_overdue = False
    if school.is_active and scheduled_cancel:
        end_date = school.stripe_cancel_at or school.stripe_current_period_end
        if end_date and end_date < djtz.now():
            cancel_overdue = True

    if is_locked:
        billing_state = "ended_locked"
    elif not has_subscription and school.plan == "trial" and school.is_active:
        billing_state = "trial"
    elif has_subscription and status in ("active", "trialing", "past_due", "unpaid"):
        billing_state = "scheduled_cancel" if scheduled_cancel else "active"
    else:
        billing_state = "trial"

    from core.services import feature_flags as ff
    plan_display = dict(ff.PLAN_CHOICES).get(school.plan, school.plan)

    from core.models import TRIAL_LENGTH_DAYS
    billing_url = reverse("school_billing", kwargs={"school_slug": school_slug})
    ctx = _school_admin_base_context(request, school, "billing")
    ctx.update({
        "plan_display": plan_display,
        "pricing": pricing,
        "billing_state": billing_state,
        "has_subscription": has_subscription,
        "cancel_at": school.stripe_cancel_at,
        "current_period_end": school.stripe_current_period_end,
        "scheduled_cancel": scheduled_cancel,
        "cancel_overdue": cancel_overdue,
        "stripe_configured": stripe_configured,
        "subscription_status": status,
        "billing_url": billing_url,
        "checkout_url": reverse("school_billing_checkout", kwargs={"school_slug": school_slug}),
        "portal_url": reverse("school_billing_portal", kwargs={"school_slug": school_slug}),
        "trial_length_days": TRIAL_LENGTH_DAYS,
    })
    return render(request, "school_admin/billing.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_billing_checkout_view(request, school_slug: str):
    """Initiate Stripe Checkout.
    POST /schools/<slug>/admin/billing/checkout/
    """
    from core.services.billing_stripe import (
        create_checkout_session, get_pricing_options, is_stripe_configured,
    )
    school = _get_accessible_school_for_admin(request, school_slug)
    billing_url = request.build_absolute_uri(
        reverse("school_billing", kwargs={"school_slug": school_slug})
    )

    if not is_stripe_configured():
        messages.error(request, "Billing is not configured.")
        return redirect(billing_url)

    price_id = request.POST.get("price_id", "").strip()
    if not price_id:
        messages.error(request, "Missing price selection.")
        return redirect(billing_url)

    valid_price_ids = {opt["price_id"] for opt in get_pricing_options()}
    if price_id not in valid_price_ids:
        messages.error(request, "Invalid price selection.")
        return redirect(billing_url)

    sep = "&" if "?" in billing_url else "?"
    success_url = billing_url + sep + "status=success"
    cancel_url = billing_url + sep + "status=canceled"

    user_email = getattr(request.user, "email", None) or None
    checkout_url = create_checkout_session(
        school=school,
        price_id=price_id,
        success_url=success_url,
        cancel_url=cancel_url,
        customer_email=user_email,
    )

    if not checkout_url:
        messages.error(request, "Could not start checkout. Please try again or contact support.")
        return redirect(billing_url)

    return redirect(checkout_url)


@login_required
@require_http_methods(["POST"])
def school_billing_portal_view(request, school_slug: str):
    """Redirect to Stripe Customer Portal.
    POST /schools/<slug>/admin/billing/portal/
    """
    from core.services.billing_stripe import create_portal_session
    school = _get_accessible_school_for_admin(request, school_slug)
    billing_url = request.build_absolute_uri(
        reverse("school_billing", kwargs={"school_slug": school_slug})
    )
    portal_url = create_portal_session(school=school, return_url=billing_url)
    if not portal_url:
        messages.error(request, "Could not open billing portal. Please try again.")
        return redirect(billing_url)
    return redirect(portal_url)
