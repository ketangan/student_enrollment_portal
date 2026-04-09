import copy
import json
import logging
from collections import Counter
from datetime import timedelta
import csv

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, FileResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.contrib import messages
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_http_methods

from .models import (
    AdminPreference,
    DraftSubmission,
    Lead,
    LEAD_SOURCE_CHOICES,
    LEAD_STATUS_CHOICES,
    LEAD_STATUS_LOST,
    LEAD_STATUS_NEW,
    School,
    Submission,
    SubmissionFile,
)
from .services.admin_themes import (
    ADMIN_THEMES,
    DEFAULT_THEME_KEY,
    get_themes_for_api,
)
from .services.config_loader import get_forms, get_program_options, load_school_config
from .services.form_utils import build_option_label_map
from .services.validation import validate_submission
from .services.notifications import (
    send_applicant_confirmation_email,
    send_resume_link_email,
    send_submission_notification_email,
)
from .services.lead_conversion import try_convert_lead

_DRAFT_RESEND_COOLDOWN_MINUTES = 5


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
def apply_view(request, school_slug: str, form_key: str = "default"):
    config = load_school_config(school_slug)
    if config is None:
        raise Http404("School config not found")

    branding = merge_branding(getattr(config, "branding", None))
    school = _get_or_create_school_from_config(school_slug, config, branding)

    # Block inactive schools from accepting applications
    if not school.is_active:
        raise Http404("School not found")

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
                try_convert_lead(school=school, submission=submission, config_raw=raw_config)
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
            try_convert_lead(school=school, submission=submission, config_raw=raw_config)
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
    return render(request, "apply_form.html", ctx)


@xframe_options_exempt
def apply_success_view(request, school_slug: str):
    config = load_school_config(school_slug)
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
    config = load_school_config(school_slug)
    if config is None:
        raise Http404("School config not found")

    branding = merge_branding(getattr(config, "branding", None))
    school = _get_or_create_school_from_config(school_slug, config, branding)

    if not school.is_active or not school.features.save_resume_enabled:
        raise Http404

    draft = get_object_or_404(DraftSubmission, token=token, school=school)

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

    return FileResponse(sf.file.open("rb"), as_attachment=False, filename=download_name)

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
    try:
        school = School.objects.get(slug=school_slug)
    except School.DoesNotExist:
        raise Http404("School not found")

    if not _can_view_school_admin_page(request, school):
        raise Http404("Page not found")

    # Block inactive schools (except for superusers)
    if not school.is_active and not request.user.is_superuser:
        raise Http404("School not found")
    
    if not request.user.is_superuser and not school.features.reports_enabled:
        return render(
            request,
            "feature_disabled.html",
            {
                "school": school,
                "school_slug": school_slug,
                "feature_name": "Reports",
                "message": "Reports are currently disabled for this school.",
            },
            status=403,
        )

    config = load_school_config(school_slug)
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

    recent = []
    for s in rows_for_reporting[:25]:
        program_label = (s.program_display_name(label_map=label_map) or "").strip() or NONE_LABEL
        admin_url = reverse("admin:core_submission_change", args=[s.id])

        recent.append(
            {
                "id": s.id,
                "admin_url": admin_url,
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

    return render(
        request,
        "reports.html",
        {
            "school": school,
            "school_slug": school_slug,
            "total": total,
            "latest": timezone.localtime(latest) if latest else None,
            "program_rows": program_rows,
            "recent": recent,
            "selected_program": selected_program,
            "range_days": range_days,
            "csv_export_enabled": csv_enabled,
            "lead_stats": lead_stats,
        },
    )


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

    pref, _created = AdminPreference.objects.get_or_create(
        user=request.user,
        defaults={"theme": theme_key},
    )
    if not _created:
        pref.theme = theme_key
        pref.save(update_fields=["theme"])

    return JsonResponse({"ok": True, "theme": theme_key})


# ---------------------------------------------------------------------------
# Lead capture
# ---------------------------------------------------------------------------

@xframe_options_exempt
def lead_capture_view(request, school_slug):
    config = load_school_config(school_slug)
    if not config:
        raise Http404

    school = _get_or_create_school_from_config(school_slug, config, merge_branding(config.branding))
    if not school.is_active:
        raise Http404
    if not school.features.leads_enabled:
        raise Http404

    branding = merge_branding(config.branding)
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


@xframe_options_exempt
def lead_capture_success_view(request, school_slug):
    config = load_school_config(school_slug)
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
