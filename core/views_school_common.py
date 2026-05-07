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

# Import public helpers needed by admin views
from .views_public import (
    _strip_file_fields,
    _plain_post_values,
    merge_branding,
    _get_or_create_school_from_config,
    _draft_session_key,
)


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


# ── Reports view ─────────────────────────────────────────────────────────────

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
