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
    _apply_lead_filters,
    _build_lead_row,
    _build_lead_prefill_data,
    _build_lead_name_prefill,
    _find_program_field_key,
    _LEAD_STATUS_CSS,
    _SMART_FILTERS,
    _SMART_FILTER_KEYS,
)
from .views_public import _strip_file_fields, _plain_post_values, _draft_session_key


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

    _LEAD_FIELD_LABELS = {
        "name": "Name",
        "email": "Email",
        "phone": "Phone",
        "interested_in": "Program Interest",
        "notes": "Notes",
        "next_follow_up_at": "Follow-up Date",
    }
    _old_values = {
        "name": lead.name or "",
        "email": lead.email or "",
        "phone": lead.phone or "",
        "interested_in": lead.interested_in_value or "",
    }
    _new_values = {
        "name": new_name,
        "email": new_email,
        "phone": new_phone,
        "interested_in": new_interested_in_value,
    }
    changed_detail = []
    for field in changed_fields:
        label = _LEAD_FIELD_LABELS.get(field, field)
        if field in _old_values:
            changed_detail.append({"field": label, "from": _old_values[field], "to": _new_values[field]})
        else:
            changed_detail.append({"field": label})

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
            extra={"name": "lead_update", "changed": changed_detail},
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
                extra={"name": "lead_created", "source": lead.source or "manual", "email": lead.email or "", "lead_name": lead.name or ""},
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


# ── School admin: lead mark-contacted + follow-up quick actions ──────────────

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
            extra={"name": "mark_contacted", "follow_up_date": lead.next_follow_up_at.date().isoformat(), "status_changed": "status" in update_fields},
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
                extra={"name": "bulk_mark_contacted", "follow_up_date": (now + timedelta(days=2)).date().isoformat(), "status_changed": "status" in update_fields},
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
                extra={"name": "bulk_follow_up_set", "date": follow_up_dt.date().isoformat()},
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


# ---------------------------------------------------------------------------
# Phase 12: admin communication actions (leads)
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
def school_lead_resend_resume_link_view(request, school_slug: str, lead_id: int):
    """
    Resend the draft resume link to the lead's email address.
    POST /schools/<slug>/admin/leads/<id>/resend-resume-link/
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

    # Find or create the draft so the link is always sendable without a prior
    # "Start Enrollment" click.
    with transaction.atomic():
        draft = (
            DraftSubmission.objects
            .select_for_update()
            .filter(school=school, lead=lead, submitted_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if not draft:
            config = _safe_load_school_config(school_slug)
            config_raw = getattr(config, "raw", {}) or {}
            prefill = _build_lead_prefill_data(lead, config_raw)
            draft = DraftSubmission.objects.create(
                school=school,
                lead=lead,
                data=prefill,
                email=lead.email,
            )
            log_admin_audit(
                request=request,
                action="action",
                obj=lead,
                changes={},
                extra={"name": "start_enrollment", "draft_id": draft.pk},
            )

    sent = send_resume_link_email(draft=draft, school=school)
    if sent:
        messages.success(request, f"Resume link sent to {draft.email}.")
        log_admin_audit(
            request=request,
            action="action",
            obj=lead,
            changes={},
            extra={"name": "resend_resume_link", "to": draft.email},
        )
    else:
        messages.error(request, "Failed to send email. Check email configuration.")

    return redirect(redirect_url)
