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
from django.db.models import Avg, Case, Count, DurationField, Exists, ExpressionWrapper, F, IntegerField, OuterRef, Q, Value, When
from django.db.models.functions import TruncDay, TruncWeek
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
    from core.views_login import DEMO_SESSION_TOKEN_KEY, DEMO_SESSION_PAGES_KEY

    leads_enabled = school.features.leads_enabled
    user_initial = (request.user.get_full_name() or request.user.username)[0].upper()

    is_demo_session = False
    demo_token_id = request.session.get(DEMO_SESSION_TOKEN_KEY)
    if demo_token_id:
        is_demo_session = True
        visited = request.session.get(DEMO_SESSION_PAGES_KEY, [])
        if active_nav not in visited:
            visited = visited + [active_nav]
            request.session[DEMO_SESSION_PAGES_KEY] = visited
            try:
                from core.models import DemoAccessToken
                DemoAccessToken.objects.filter(pk=demo_token_id).update(pages_visited=visited)
            except Exception:
                pass

    return {
        "school": school,
        "school_slug": school.slug,
        "leads_enabled": leads_enabled,
        "user_initial": user_initial,
        "now": timezone.localtime(timezone.now()),
        "active_nav": active_nav,
        "is_demo_session": is_demo_session,
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
    Enrollment Analytics Reports.
    URL: /schools/<slug>/admin/reports/

    Sections:
    4.1  KPI row — rates and throughput only (App→Enrolled %, Lead→App %, Apps/Week, Avg Days)
    4.2  This-period vs previous comparison table
    4.3  Applications & Enrollments over time (inline SVG line chart)
    4.4  Enrollment Funnel (all-time) + Program Mix (scoped)
    4.5  Lead Source Effectiveness (all-time; leads module only)

    Data integrity rules enforced:
    - No Lead query executes when leads_enabled=False (§2)
    - Every rate shows its basis and scope (§3 R1, R3, R6)
    - Funnel is all-time and uses "of which" connectors, not a bare % (§3 R1)
    - Avg Days to Enroll uses updated_at as proxy for enrolled submissions (last-save ≈ enrollment date)
    - All time-series buckets gap-filled so the chart never has holes
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

    # ── Date range ────────────────────────────────────────────────────────────
    range_raw = (request.GET.get("range") or "30").strip()
    if range_raw not in {"7", "30", "90"}:
        range_raw = "30"
    range_days = int(range_raw)
    now = timezone.now()
    period_start = now - timedelta(days=range_days)
    prev_start = now - timedelta(days=range_days * 2)
    range_label = {"7": "Last 7 days", "30": "Last 30 days", "90": "Last 90 days"}[range_raw]

    # ── Feature flags — resolved once; no Lead query runs when False ──────────
    leads_enabled = school.features.leads_enabled
    csv_enabled = school.features.csv_export_enabled or request.user.is_superuser

    # ── Inline helpers ────────────────────────────────────────────────────────
    def _rate(num, den):
        """(rate_float, 'N of M') or (None, None) when den == 0."""
        if not den:
            return None, None
        return round(num / den * 100, 1), f"{num} of {den}"

    def _count_delta(a, b):
        """Signed integer delta with display string."""
        d = a - b
        return d, f"{'+' if d >= 0 else ''}{d}", d >= 0

    def _rate_delta(r1, r2):
        """Percentage-point delta between two rates."""
        if r1 is None or r2 is None:
            return None, None, None
        d = round(r1 - r2, 1)
        return d, f"{'+' if d >= 0 else ''}{d} pts", d >= 0

    # ── Scoped application querysets ──────────────────────────────────────────
    apps_this_qs = Submission.objects.filter(school=school, created_at__gte=period_start)
    apps_prev_qs = Submission.objects.filter(school=school, created_at__range=(prev_start, period_start))

    # ── CSV export ────────────────────────────────────────────────────────────
    export = request.GET.get("export", "").lower() in {"1", "true", "csv"}
    if export and csv_enabled:
        rows = list(apps_this_qs.order_by("-created_at")[:5000])
        all_keys: set = set()
        for s in rows:
            all_keys.update((s.data or {}).keys())
        ordered_keys = ["application_id", "created_at", "status", "student_name", "program"] + sorted(all_keys)
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{school.slug}-reports-last{range_days}d.csv"'
        writer = csv.writer(resp)
        writer.writerow(ordered_keys)
        for s in rows:
            data = s.data or {}
            writer.writerow(
                [s.public_id, timezone.localtime(s.created_at).isoformat(),
                 s.status or "", s.student_display_name(),
                 s.program_display_name(label_map=label_map) or ""]
                + [data.get(k, "") for k in sorted(all_keys)]
            )
        return resp

    # ── §4.1 + §4.2: Aggregate counts — one query per queryset ───────────────
    this_agg = apps_this_qs.aggregate(
        total=Count("id"),
        enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
    )
    prev_agg = apps_prev_qs.aggregate(
        total=Count("id"),
        enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
    )
    apps_t, apps_e = this_agg["total"], this_agg["enrolled"]
    p_apps_t, p_apps_e = prev_agg["total"], prev_agg["enrolled"]

    app_rate, app_rate_basis = _rate(apps_e, apps_t)
    p_app_rate, _ = _rate(p_apps_e, p_apps_t)
    _, app_rate_delta_str, app_rate_up = _rate_delta(app_rate, p_app_rate)
    _, apps_delta_str, apps_up = _count_delta(apps_t, p_apps_t)
    _, enrolled_delta_str, enrolled_up = _count_delta(apps_e, p_apps_e)

    apps_per_week = round(apps_t / (range_days / 7), 1) if apps_t else None
    p_apps_per_week = round(p_apps_t / (range_days / 7), 1) if p_apps_t else None
    _, apw_delta_str, apw_up = _rate_delta(apps_per_week or 0.0, p_apps_per_week or 0.0)

    # Lead KPI aggregates — branch gates all Lead queries
    leads_t = leads_c = p_leads_t = p_leads_c = 0
    lead_rate = lead_rate_basis = p_lead_rate = None
    lead_rate_delta_str = lead_rate_up = None
    leads_delta_str = leads_up = None
    if leads_enabled:
        la = Lead.objects.filter(school=school, created_at__gte=period_start).aggregate(
            total=Count("id"),
            converted=Count("id", filter=Q(converted_submission__isnull=False)),
        )
        pla = Lead.objects.filter(school=school, created_at__range=(prev_start, period_start)).aggregate(
            total=Count("id"),
            converted=Count("id", filter=Q(converted_submission__isnull=False)),
        )
        leads_t, leads_c = la["total"], la["converted"]
        p_leads_t, p_leads_c = pla["total"], pla["converted"]
        lead_rate, lead_rate_basis = _rate(leads_c, leads_t)
        p_lead_rate, _ = _rate(p_leads_c, p_leads_t)
        _, lead_rate_delta_str, lead_rate_up = _rate_delta(lead_rate, p_lead_rate)
        _, leads_delta_str, leads_up = _count_delta(leads_t, p_leads_t)

    # ── Avg days to enroll (updated_at proxy for enrolled submissions) ──────────
    def _avg_days(qs):
        result = (
            qs.filter(status=STATUS_ENROLLED)
            .annotate(dur=ExpressionWrapper(F("updated_at") - F("created_at"), output_field=DurationField()))
            .aggregate(avg=Avg("dur"))["avg"]
        )
        if result is None:
            return None
        return round(result.total_seconds() / 86400, 1)

    avg_days_enroll = _avg_days(apps_this_qs)
    p_avg_days_enroll = _avg_days(apps_prev_qs)

    def _days_delta(a, b):
        if a is None or b is None:
            return None, None, None
        d = round(a - b, 1)
        # lower is better for days
        return d, f"{'+' if d >= 0 else ''}{d}d", d <= 0

    _, adelta_str, adelta_up = _days_delta(avg_days_enroll, p_avg_days_enroll)

    # ── §4.1: KPI tiles ───────────────────────────────────────────────────────
    kpi_tiles = [
        {
            "label": "App → Enrolled",
            "value": f"{app_rate}%" if app_rate is not None else None,
            "basis": app_rate_basis,
            "delta_str": app_rate_delta_str,
            "delta_up": app_rate_up,
        },
    ]
    if leads_enabled:
        kpi_tiles.append({
            "label": "Lead → Application",
            "value": f"{lead_rate}%" if lead_rate is not None else None,
            "basis": lead_rate_basis,
            "delta_str": lead_rate_delta_str,
            "delta_up": lead_rate_up,
        })
    kpi_tiles.extend([
        {
            "label": "Applications / Week",
            "value": str(apps_per_week) if apps_per_week is not None else None,
            "basis": None,
            "delta_str": apw_delta_str,
            "delta_up": apw_up,
        },
        {
            "label": "Avg Days to Enroll",
            "value": f"{avg_days_enroll}d" if avg_days_enroll is not None else None,
            "basis": "submission → enrollment" if avg_days_enroll is not None else None,
            "delta_str": adelta_str,
            "delta_up": adelta_up,
        },
    ])

    # ── §4.2: Comparison table rows ───────────────────────────────────────────
    comparison_rows = [
        {
            "label": "Applications received",
            "this_val": apps_t,
            "prev_val": p_apps_t,
            "delta_str": apps_delta_str,
            "delta_up": apps_up,
        },
    ]
    if leads_enabled:
        comparison_rows.append({
            "label": "Leads captured",
            "this_val": leads_t,
            "prev_val": p_leads_t,
            "delta_str": leads_delta_str,
            "delta_up": leads_up,
        })
        comparison_rows.append({
            "label": "Leads → Applications",
            "this_val": f"{leads_c} of {leads_t}" if leads_t else "—",
            "prev_val": f"{p_leads_c} of {p_leads_t}" if p_leads_t else "—",
            "delta_str": None,
            "delta_up": None,
            "is_basis_row": True,
        })
    comparison_rows.extend([
        {
            "label": "Enrolled",
            "this_val": apps_e,
            "prev_val": p_apps_e,
            "delta_str": enrolled_delta_str,
            "delta_up": enrolled_up,
        },
        {
            "label": "App → Enrolled Rate",
            "this_val": f"{app_rate}%" if app_rate is not None else "—",
            "prev_val": f"{p_app_rate}%" if p_app_rate is not None else "—",
            "delta_str": app_rate_delta_str,
            "delta_up": app_rate_up,
            "is_rate_row": True,
        },
        {
            "label": "Avg Days to Enroll",
            "this_val": f"{avg_days_enroll}d" if avg_days_enroll is not None else "—",
            "prev_val": f"{p_avg_days_enroll}d" if p_avg_days_enroll is not None else "—",
            "delta_str": adelta_str,
            "delta_up": adelta_up,
        },
    ])

    # ── §4.3: Time series — DB-side bucketing + Python gap-fill ──────────────
    if range_days <= 30:
        trunc_fn, ts_step, ts_bucket_label = TruncDay, timedelta(days=1), "day"
    else:
        trunc_fn, ts_step, ts_bucket_label = TruncWeek, timedelta(weeks=1), "week"

    apps_ts_db = list(
        apps_this_qs
        .annotate(bucket=trunc_fn("created_at"))
        .values("bucket")
        .annotate(
            apps=Count("id"),
            enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
        )
        .order_by("bucket")
    )
    apps_ts_map = {}
    for row in apps_ts_db:
        b = row["bucket"]
        apps_ts_map[b.date() if hasattr(b, "date") else b] = row

    leads_ts_map: dict = {}
    if leads_enabled:
        for row in (
            Lead.objects.filter(school=school, created_at__gte=period_start)
            .annotate(bucket=trunc_fn("created_at"))
            .values("bucket")
            .annotate(leads=Count("id"))
            .order_by("bucket")
        ):
            b = row["bucket"]
            leads_ts_map[b.date() if hasattr(b, "date") else b] = row["leads"]

    # Gap-fill from period_start to today; for weekly buckets align to Monday
    cur_date = period_start.date()
    if ts_step == timedelta(weeks=1):
        cur_date -= timedelta(days=cur_date.weekday())  # back to Monday
    ts_data = []
    while cur_date <= now.date():
        row = apps_ts_map.get(cur_date, {})
        ts_data.append({
            "date": f"{cur_date.month}/{cur_date.day}",
            "apps": row.get("apps", 0),
            "enrolled": row.get("enrolled", 0),
            "leads": leads_ts_map.get(cur_date, 0),
        })
        cur_date += ts_step

    ts_max_y = max((r["apps"] for r in ts_data), default=0)
    if leads_enabled:
        ts_max_y = max(ts_max_y, max((r["leads"] for r in ts_data), default=0))
    ts_max_y = max(ts_max_y, 1)

    ts_json = json.dumps({
        "data": ts_data,
        "max_y": ts_max_y,
        "leads_enabled": leads_enabled,
    })

    # ── §4.4: Enrollment Funnel (all-time) ────────────────────────────────────
    all_apps_agg = Submission.objects.filter(school=school).aggregate(
        total=Count("id"),
        enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
    )
    funnel_apps = all_apps_agg["total"]
    funnel_enrolled = all_apps_agg["enrolled"]
    funnel_leads = funnel_converted = None
    if leads_enabled:
        all_leads_agg = Lead.objects.filter(school=school).aggregate(
            total=Count("id"),
            converted=Count("id", filter=Q(converted_submission__isnull=False)),
        )
        funnel_leads = all_leads_agg["total"]
        funnel_converted = all_leads_agg["converted"]

    funnel_max = max(funnel_leads or 0, funnel_apps, 1)
    funnel_l2a_rate, funnel_l2a_basis = (
        _rate(funnel_converted, funnel_leads) if funnel_leads else (None, None)
    )
    funnel_a2e_rate, funnel_a2e_basis = _rate(funnel_enrolled, funnel_apps)
    funnel = {
        "leads": funnel_leads,
        "leads_w": round((funnel_leads or 0) / funnel_max * 100),
        "converted": funnel_converted,
        "l2a_rate": funnel_l2a_rate,
        "l2a_basis": funnel_l2a_basis,
        "apps": funnel_apps,
        "apps_w": round(funnel_apps / funnel_max * 100),
        "a2e_rate": funnel_a2e_rate,
        "a2e_basis": funnel_a2e_basis,
        "enrolled": funnel_enrolled,
        "enrolled_w": round(funnel_enrolled / funnel_max * 100) if funnel_apps else 0,
    }

    # ── §4.4: Program Mix (scoped to date range) ──────────────────────────────
    prog_rows = list(apps_this_qs[:5000])
    prog_strings = [
        (s.program_display_name(label_map=label_map) or "").strip() or "(none)"
        for s in prog_rows
    ]
    mix_counts = Counter(prog_strings)
    mix_total = len(prog_strings)
    program_mix = []
    other_count = 0
    for i, (prog_key, c) in enumerate(mix_counts.most_common()):
        pct = round(c / mix_total * 100, 1) if mix_total else 0.0
        lbl = "Unspecified" if prog_key == "(none)" else prog_key
        if i < 4:
            program_mix.append({"label": lbl, "count": c, "pct": pct, "bar_w": int(pct)})
        else:
            other_count += c
    if other_count:
        other_pct = round(other_count / mix_total * 100, 1) if mix_total else 0.0
        program_mix.append({"label": "Other", "count": other_count, "pct": other_pct, "bar_w": int(other_pct)})

    # ── §4.5: Lead Source Effectiveness (all-time, leads only) ───────────────
    source_rows = None
    if leads_enabled:
        src_label_map = dict(LEAD_SOURCE_CHOICES)
        source_rows = []
        for row in (
            Lead.objects.filter(school=school)
            .values("source")
            .annotate(
                total=Count("id"),
                converted=Count("id", filter=Q(converted_submission__isnull=False)),
            )
            .order_by("-total")
        ):
            rate, basis = _rate(row["converted"], row["total"])
            source_rows.append({
                "label": src_label_map.get(row["source"], row["source"].replace("_", " ").title()),
                "total": row["total"],
                "converted": row["converted"],
                "rate": rate,
                "basis": basis,
                "bar_w": int(rate) if rate is not None else 0,
            })

    # ── §4.6: Program Enrollment Funnel (scoped to date range) ───────────────
    program_funnel = None
    if school.program_field_key:
        prog_data = list(
            apps_this_qs.filter(program__isnull=False)
            .values(
                "program__id",
                "program__name",
                "program__code",
                "program__is_active",
                "program__capacity",
                "program__display_order",
            )
            .annotate(
                total=Count("id"),
                enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
                waitlisted=Count("id", filter=Q(status=STATUS_WAITLISTED)),
                declined=Count("id", filter=Q(status=STATUS_DECLINED)),
            )
            .order_by("program__display_order", "program__name")
        )
        if prog_data:
            max_total = max(r["total"] for r in prog_data)
            active_rows = []
            inactive_rows = []

            # Build session-level breakdown per program.
            session_data = {}
            sess_qs = list(
                apps_this_qs.filter(session__isnull=False)
                .values(
                    "session__id",
                    "session__name",
                    "session__code",
                    "session__is_active",
                    "session__capacity",
                    "program__id",
                )
                .annotate(
                    total=Count("id"),
                    enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
                    waitlisted=Count("id", filter=Q(status=STATUS_WAITLISTED)),
                    declined=Count("id", filter=Q(status=STATUS_DECLINED)),
                )
                .order_by("session__display_order", "session__name")
            )
            for sr in sess_qs:
                pid = sr["program__id"]
                if pid not in session_data:
                    session_data[pid] = []
                stotal = sr["total"]
                senrolled = sr["enrolled"]
                swaitlisted = sr["waitlisted"]
                sdeclined = sr["declined"]
                spending = stotal - senrolled - swaitlisted - sdeclined
                session_data[pid].append({
                    "name": sr["session__name"],
                    "code": sr["session__code"],
                    "is_active": sr["session__is_active"],
                    "capacity": sr["session__capacity"],
                    "total": stotal,
                    "enrolled": senrolled,
                    "waitlisted": swaitlisted,
                    "declined": sdeclined,
                    "pending": spending,
                    "conv_rate": round(senrolled / stotal * 100, 1) if stotal else 0.0,
                    "enrolled_pct": round(senrolled / stotal * 100) if stotal else 0,
                    "waitlisted_pct": round(swaitlisted / stotal * 100) if stotal else 0,
                    "declined_pct": round(sdeclined / stotal * 100) if stotal else 0,
                    "pending_pct": round(spending / stotal * 100) if stotal else 0,
                    "is_synthetic": False,
                })

            # Synthetic "Program-level enrollment" rows — session=NULL submissions that
            # belong to a program which *also* has session submissions.  Only needed for
            # programs that have both kinds; programs without any session data show
            # everything in the program row directly.
            null_sess_data = {}
            null_qs = list(
                apps_this_qs.filter(program__isnull=False, session__isnull=True)
                .values("program__id")
                .annotate(
                    total=Count("id"),
                    enrolled=Count("id", filter=Q(status=STATUS_ENROLLED)),
                    waitlisted=Count("id", filter=Q(status=STATUS_WAITLISTED)),
                    declined=Count("id", filter=Q(status=STATUS_DECLINED)),
                )
            )
            for nr in null_qs:
                null_sess_data[nr["program__id"]] = nr

            for r in prog_data:
                total = r["total"]
                enrolled = r["enrolled"]
                waitlisted = r["waitlisted"]
                declined = r["declined"]
                pending = total - enrolled - waitlisted - declined
                conv_rate = round(enrolled / total * 100, 1) if total else 0.0
                pid = r["program__id"]
                sessions = list(session_data.get(pid, []))

                # Inject synthetic "Program-level enrollment" row when this program
                # has *both* session submissions and null-session submissions.
                nl = null_sess_data.get(pid)
                if sessions and nl and nl["total"]:
                    ntotal = nl["total"]
                    nenrolled = nl["enrolled"]
                    nwaitlisted = nl["waitlisted"]
                    ndeclined = nl["declined"]
                    npending = ntotal - nenrolled - nwaitlisted - ndeclined
                    sessions.append({
                        "name": "Program-level enrollment",
                        "code": "",
                        "is_active": True,
                        "capacity": None,
                        "total": ntotal,
                        "enrolled": nenrolled,
                        "waitlisted": nwaitlisted,
                        "declined": ndeclined,
                        "pending": npending,
                        "conv_rate": round(nenrolled / ntotal * 100, 1) if ntotal else 0.0,
                        "enrolled_pct": round(nenrolled / ntotal * 100) if ntotal else 0,
                        "waitlisted_pct": round(nwaitlisted / ntotal * 100) if ntotal else 0,
                        "declined_pct": round(ndeclined / ntotal * 100) if ntotal else 0,
                        "pending_pct": round(npending / ntotal * 100) if ntotal else 0,
                        "is_synthetic": True,
                    })

                row = {
                    "name": r["program__name"],
                    "code": r["program__code"],
                    "is_active": r["program__is_active"],
                    "capacity": r["program__capacity"],
                    "total": total,
                    "enrolled": enrolled,
                    "waitlisted": waitlisted,
                    "declined": declined,
                    "pending": pending,
                    "conv_rate": conv_rate,
                    "bar_w": round(total / max_total * 100),
                    "enrolled_pct": round(enrolled / total * 100) if total else 0,
                    "waitlisted_pct": round(waitlisted / total * 100) if total else 0,
                    "declined_pct": round(declined / total * 100) if total else 0,
                    "pending_pct": round(pending / total * 100) if total else 0,
                    "sessions": sessions,
                }
                if r["program__is_active"]:
                    active_rows.append(row)
                else:
                    inactive_rows.append(row)
            program_funnel = {
                "active": active_rows,
                "inactive": inactive_rows,
                "max_total": max_total,
            }

    base_ctx = _school_admin_base_context(request, school, "reports")
    base_ctx.update({
        "school_slug": school_slug,
        "range_days": range_days,
        "range_label": range_label,
        "range_options": [(7, "7d"), (30, "30d"), (90, "90d")],
        "period_start": timezone.localtime(period_start),
        "period_end": timezone.localtime(now),
        "kpi_tiles": kpi_tiles,
        "comparison_rows": comparison_rows,
        "ts_json": ts_json,
        "ts_bucket_label": ts_bucket_label,
        "funnel": funnel,
        "program_mix": program_mix,
        "program_mix_total": mix_total,
        "source_rows": source_rows,
        "program_funnel": program_funnel,
        "leads_enabled": leads_enabled,
        "csv_export_enabled": csv_enabled,
        "billing_url": reverse("school_billing", kwargs={"school_slug": school_slug}),
    })
    return render(request, "reports.html", base_ctx)
