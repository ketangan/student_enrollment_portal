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

from .views_school_common import *  # noqa: F401,F403
from .views_school_common import (  # noqa: F401 — private names not exported by *
    _get_accessible_school_for_admin,
    _safe_load_school_config,
    _safe_redirect_url,
    _school_admin_base_context,
    _apply_submission_filters,
    _build_submission_row,
    _extract_contact_field,
    _PARENT_EMAIL_KEYS,
    _PARENT_PHONE_KEYS,
    _SMART_FILTERS,
    _SMART_FILTER_KEYS,
)
from .views_public import _strip_file_fields, _plain_post_values  # noqa: F401


def _get_display_form_dict(forms: dict, form_key: str) -> dict:
    """
    Return the form dict (with a 'sections' key) to use for admin display/edit.

    For multi-step submissions (form_key='multi'), all steps' sections are merged
    so the admin sees every field from every page in a single view.
    For single-form or named-form submissions, the matching form entry is returned.
    """
    if form_key == "multi" and forms:
        combined = []
        for step_entry in forms.values():
            step_form = step_entry.get("form", {}) if isinstance(step_entry, dict) else {}
            combined.extend(step_form.get("sections") or [])
        return {"sections": combined}
    form_entry = forms.get(form_key) or forms.get("default") or {}
    return form_entry.get("form", {}) if isinstance(form_entry, dict) else {}


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
    config_raw = getattr(load_school_config(school.slug), "raw", {}) or {}
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
    # Multi-step submissions (form_key='multi') get all steps merged into one view.
    forms = get_forms(config) if config else {}
    form_key = submission.form_key or "default"
    form_dict = _get_display_form_dict(forms, form_key)

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
    raw_form_cfg = _get_display_form_dict(available_forms, form_key)
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


# ── School admin: mark-contacted + follow-up quick actions (submissions) ──────

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


# ── School admin: bulk follow-up actions (submissions) ───────────────────────

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
# Phase 12: admin communication actions (submissions)
# ---------------------------------------------------------------------------

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
