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
    _resolve_from_email,
)
from .services.lead_conversion import try_convert_lead
from .services.integrations import get_export_configs, normalize_csv_value, resolve_export_row
from .services.ai_summary import generate_ai_summary
from .services.school_permissions import require_school_role, get_school_membership

from .views_school_common import *  # noqa: F401,F403
from .views_school_common import (  # noqa: F401 — private names not exported by *
    _get_accessible_school_for_admin,
    _log_page_view,
    _safe_load_school_config,
    _school_admin_base_context,
    _TERMINAL_SUBMISSION_STATUSES,
    _submission_initials,
)


@login_required
def school_dashboard_view(request, school_slug: str):
    """
    Modern inbox-style dashboard for school admins.
    URL: /schools/<slug>/admin/
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    _log_page_view(request, school, "dashboard")

    config = _safe_load_school_config(school_slug)
    label_map = build_option_label_map(config.form) if config else {}

    # select_related school+program avoids N+1 from program_display_name().
    all_submissions = Submission.objects.filter(school=school).select_related("school", "program")

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
    from core.services.url_builder import app_reverse
    apply_url = app_reverse("apply", kwargs={"school_slug": school_slug})
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

    # Demo session tracking
    from core.views_login import DEMO_SESSION_TOKEN_KEY, DEMO_SESSION_PAGES_KEY
    is_demo_session = False
    demo_token_id = request.session.get(DEMO_SESSION_TOKEN_KEY)
    if demo_token_id:
        is_demo_session = True
        visited = request.session.get(DEMO_SESSION_PAGES_KEY, [])
        if "dashboard" not in visited:
            visited = visited + ["dashboard"]
            request.session[DEMO_SESSION_PAGES_KEY] = visited
            try:
                from core.models import DemoAccessToken
                DemoAccessToken.objects.filter(pk=demo_token_id).update(pages_visited=visited)
            except Exception:
                pass

    _dash_membership = (
        None if request.user.is_superuser
        else get_school_membership(request.user, school)
    )
    _dash_role = _dash_membership.role if _dash_membership else ("owner" if request.user.is_superuser else None)

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
            "is_demo_session": is_demo_session,
            "current_role": _dash_role,
            "is_owner": _dash_role == "owner",
            "is_editor": _dash_role in ("owner", "editor"),
            "can_mutate": _dash_role in ("owner", "editor"),
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
    ff.PLAN_CUSTOM:  "Custom",
}


@login_required
def school_settings_view(request, school_slug: str):
    """
    School admin settings page.
    GET  /schools/<slug>/admin/settings/
    POST /schools/<slug>/admin/settings/  (action=update_display_name)
    """
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "editor")

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

    if request.method == "POST" and request.POST.get("action") == "update_smtp":
        require_school_role(request, school, "owner")
        smtp_host = request.POST.get("smtp_host", "").strip()
        smtp_port_raw = request.POST.get("smtp_port", "").strip()
        smtp_username = request.POST.get("smtp_username", "").strip()
        smtp_password = request.POST.get("smtp_password", "")
        smtp_from_email = request.POST.get("smtp_from_email", "").strip()
        smtp_use_tls = request.POST.get("smtp_use_tls") == "1"

        smtp_port = None
        if smtp_port_raw:
            try:
                smtp_port = int(smtp_port_raw)
                if smtp_port < 1 or smtp_port > 65535:
                    raise ValueError
            except ValueError:
                messages.error(request, "SMTP port must be a valid number (1–65535).")
                return redirect("school_settings", school_slug=school_slug)

        school.smtp_host = smtp_host
        school.smtp_port = smtp_port
        school.smtp_username = smtp_username
        if smtp_password:
            school.smtp_password = smtp_password
        school.smtp_from_email = smtp_from_email
        school.smtp_use_tls = smtp_use_tls
        school.save(update_fields=[
            "smtp_host", "smtp_port", "smtp_username",
            "smtp_from_email", "smtp_use_tls",
            *( ["smtp_password"] if smtp_password else [] ),
        ])
        log_admin_audit(
            request=request, action="action", obj=school, changes={},
            extra={"name": "update_smtp", "host": smtp_host or "(cleared)"},
        )
        if smtp_host:
            messages.success(request, f"SMTP settings saved — emails will route via {smtp_host}.")
        else:
            messages.success(request, "SMTP settings cleared — using default email service.")
        return redirect("school_settings", school_slug=school_slug)

    if request.method == "POST" and request.POST.get("action") == "clear_smtp":
        require_school_role(request, school, "owner")
        school.smtp_host = ""
        school.smtp_port = None
        school.smtp_username = ""
        school.smtp_password = ""
        school.smtp_from_email = ""
        school.smtp_use_tls = True
        school.save(update_fields=["smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from_email", "smtp_use_tls"])
        log_admin_audit(
            request=request, action="action", obj=school, changes={},
            extra={"name": "clear_smtp"},
        )
        messages.success(request, "SMTP settings cleared — using default email service.")
        return redirect("school_settings", school_slug=school_slug)

    if request.method == "POST" and request.POST.get("action") == "update_display_name":
        require_school_role(request, school, "owner")
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

    if request.method == "POST" and request.POST.get("action") == "update_follow_up_days":
        require_school_role(request, school, "owner")
        try:
            days = int(request.POST.get("follow_up_days", ""))
            if not (1 <= days <= 30):
                raise ValueError
        except (ValueError, TypeError):
            messages.error(request, "Follow-up window must be between 1 and 30 days.")
        else:
            old_days = school.default_follow_up_days
            if days == old_days:
                messages.info(request, "No change — already set to that.")
            else:
                school.default_follow_up_days = days
                school.save(update_fields=["default_follow_up_days"])
                log_admin_audit(
                    request=request,
                    action="action",
                    obj=school,
                    changes={},
                    extra={"name": "update_follow_up_days", "old": old_days, "new": days},
                )
                messages.success(request, f"Follow-up window updated to {days} day{'s' if days != 1 else ''}.")
        return redirect("school_settings", school_slug=school_slug)

    from core.services.url_builder import app_reverse
    apply_url = app_reverse("apply", kwargs={"school_slug": school_slug})
    embed_snippet = (
        f'<iframe src="{apply_url}" width="100%" height="700" '
        f'frameborder="0" style="border:none;" title="Application Form"></iframe>'
    )

    # All lead/intake form links: default leads: form first, then named lead_forms: variants.
    lead_form_variants = []
    try:
        from core.services.config_loader import load_school_config as _load_cfg
        _cfg = _load_cfg(school_slug)
        _raw = (_cfg.raw or {}) if _cfg else {}

        # Default lead form (/lead/) — shown if the school has a leads: section
        _leads_cfg = _raw.get("leads") or {}
        if _leads_cfg:
            _default_title = _leads_cfg.get("form_title") or "Lead Form"
            _default_url = app_reverse("school_lead_form", kwargs={"school_slug": school_slug})
            lead_form_variants.append({"key": "_default", "title": _default_title, "url": _default_url})

        # Named variants (/lead/<form_key>/) from lead_forms:
        for _key, _form_cfg in (_raw.get("lead_forms") or {}).items():
            _url = app_reverse("school_lead_form_variant", kwargs={"school_slug": school_slug, "form_key": _key})
            lead_form_variants.append({
                "key": _key,
                "title": (_form_cfg or {}).get("form_title") or _key,
                "url": _url,
            })
    except Exception:
        pass

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

    # Programs section (only when school has program_field_key configured)
    programs_summary = {}
    programs_create_url = ""
    programs_no_active_warning = False
    if school.program_field_key:
        from core.services.programs import get_programs_summary as _get_programs_summary
        from core.models import SchoolProgram as _SchoolProgram
        programs_summary = _get_programs_summary(school)
        programs_create_url = reverse("school_program_create", kwargs={"school_slug": school_slug})
        programs_no_active_warning = not _SchoolProgram.objects.filter(school=school, is_active=True).exists()

    from core.models import SchoolCustomToken, SchoolEmailTemplate
    _all_templates = SchoolEmailTemplate.objects.filter(school=school).order_by("name")
    email_templates          = [t for t in _all_templates if t.is_active]
    email_templates_inactive = [t for t in _all_templates if not t.is_active]
    custom_tokens = list(SchoolCustomToken.objects.filter(school=school))

    ctx = _school_admin_base_context(request, school, "settings")
    ctx.update({
        "apply_url": apply_url,
        "embed_snippet": embed_snippet,
        "lead_form_variants": lead_form_variants,
        "features": features,
        "programs_summary": programs_summary,
        "programs_create_url": programs_create_url,
        "programs_no_active_warning": programs_no_active_warning,
        "program_field_key": school.program_field_key,
        "email_templates": email_templates,
        "email_templates_inactive": email_templates_inactive,
        "email_template_create_url": reverse("school_email_template_create", kwargs={"school_slug": school_slug}),
        "custom_tokens": custom_tokens,
        "custom_token_create_url": reverse("school_custom_token_create", kwargs={"school_slug": school_slug}),
    })
    return render(request, "school_admin/settings.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_smtp_test_view(request, school_slug: str):
    """POST /schools/<slug>/admin/settings/smtp-test/ — test SMTP credentials, return JSON."""
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "owner")

    smtp_host = request.POST.get("smtp_host", "").strip()
    smtp_port_raw = request.POST.get("smtp_port", "").strip()
    smtp_username = request.POST.get("smtp_username", "").strip()
    smtp_password = request.POST.get("smtp_password", "") or school.smtp_password
    smtp_from_email = (request.POST.get("smtp_from_email", "").strip()
                       or smtp_username
                       or school.smtp_from_email)
    smtp_use_tls = request.POST.get("smtp_use_tls") == "1"

    if not smtp_host:
        return JsonResponse({"success": False, "message": "Enter an SMTP host before testing."})

    to_email = request.user.email
    if not to_email:
        return JsonResponse({"success": False, "message": "No email address on your account — add one to receive the test."})

    try:
        port = int(smtp_port_raw) if smtp_port_raw else 587
    except ValueError:
        return JsonResponse({"success": False, "message": "Invalid port number."})

    try:
        from django.core.mail.backends.smtp import EmailBackend as SMTPBackend
        from django.core.mail import EmailMessage as _EmailMessage
        conn = SMTPBackend(
            host=smtp_host,
            port=port,
            username=smtp_username,
            password=smtp_password,
            use_tls=smtp_use_tls,
            timeout=10,
            fail_silently=False,
        )
        _EmailMessage(
            subject=f"Enrollify SMTP test — {school.display_name}",
            body=(
                f"This is a test email from Enrollify.\n\n"
                f"Your custom SMTP settings for {school.display_name} are working correctly.\n\n"
                f"Host: {smtp_host}:{port}\nFrom: {smtp_from_email}"
            ),
            from_email=smtp_from_email,
            to=[to_email],
            connection=conn,
        ).send(fail_silently=False)
        return JsonResponse({"success": True, "message": f"Test email sent to {to_email}."})
    except Exception as exc:
        return JsonResponse({"success": False, "message": str(exc)})


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
    require_school_role(request, school, "owner")
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
    elif school.plan == ff.PLAN_CUSTOM and school.is_active:
        billing_state = "custom"
    elif not has_subscription and school.plan == "trial" and school.is_active:
        billing_state = "trial"
    elif has_subscription and status in ("active", "trialing", "past_due", "unpaid"):
        billing_state = "scheduled_cancel" if scheduled_cancel else "active"
    else:
        billing_state = "trial"

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
    require_school_role(request, school, "owner")
    from core.services.url_builder import app_reverse
    billing_url = app_reverse("school_billing", kwargs={"school_slug": school_slug})

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
    require_school_role(request, school, "owner")
    from core.services.url_builder import app_reverse
    billing_url = app_reverse("school_billing", kwargs={"school_slug": school_slug})
    portal_url = create_portal_session(school=school, return_url=billing_url)
    if not portal_url:
        messages.error(request, "Could not open billing portal. Please try again.")
        return redirect(billing_url)
    return redirect(portal_url)


# ── Team management ───────────────────────────────────────────────────────────

from .models import SchoolAdminMembership as _Membership


@login_required
@require_http_methods(["POST"])
def school_team_add_view(request, school_slug: str):
    """POST /schools/<slug>/admin/team/add/ — create a new user account and add to team."""
    from django.contrib.auth.models import User as _User
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "owner")
    settings_url = reverse("school_settings", kwargs={"school_slug": school_slug})

    username   = request.POST.get("username", "").strip()
    first_name = request.POST.get("first_name", "").strip()
    last_name  = request.POST.get("last_name", "").strip()
    password   = request.POST.get("password", "").strip()
    role       = request.POST.get("role", "").strip()

    if role not in _Membership.Role.values:
        messages.error(request, "Invalid role.")
        return redirect(settings_url)
    if not username:
        messages.error(request, "Username is required.")
        return redirect(settings_url)
    if not password:
        messages.error(request, "Password is required.")
        return redirect(settings_url)
    if len(password) < 8:
        messages.error(request, "Password must be at least 8 characters.")
        return redirect(settings_url)
    if _User.objects.filter(username__iexact=username).exists():
        messages.error(request, f'Username "{username}" is already taken.')
        return redirect(settings_url)

    with transaction.atomic():
        user = _User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            is_staff=True,
        )
        _Membership.objects.create(
            school=school, user=user, role=role, created_by=request.user,
        )
        log_admin_audit(
            request=request, action="action", obj=school, changes={},
            extra={"name": "member_created", "username": username, "role": role},
        )
    display = user.get_full_name() or username
    messages.success(request, f"{display} added as {role}.")
    return redirect(settings_url)


@login_required
@require_http_methods(["POST"])
def school_team_role_view(request, school_slug: str, membership_id: int):
    """POST /schools/<slug>/admin/team/<id>/role/ — change a member's role."""
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "owner")
    settings_url = reverse("school_settings", kwargs={"school_slug": school_slug})

    membership = get_object_or_404(_Membership, id=membership_id, school=school, is_active=True)
    new_role = request.POST.get("role", "").strip()

    if new_role not in _Membership.Role.values:
        messages.error(request, "Invalid role.")
        return redirect(settings_url)

    # Prevent demoting the last owner.
    if membership.role == "owner" and new_role != "owner":
        owner_count = _Membership.objects.filter(
            school=school, role="owner", is_active=True
        ).count()
        if owner_count <= 1:
            messages.error(request, "Cannot demote the last owner. Promote another member first.")
            return redirect(settings_url)

    old_role = membership.role
    membership.role = new_role
    with transaction.atomic():
        membership.save(update_fields=["role"])
        log_admin_audit(
            request=request, action="action", obj=school, changes={},
            extra={
                "name": "role_changed",
                "email": membership.user.email,
                "old_role": old_role,
                "new_role": new_role,
            },
        )
    messages.success(
        request,
        f"{membership.user.get_full_name() or membership.user.email} role changed to {new_role}.",
    )
    return redirect(settings_url)


@login_required
@require_http_methods(["POST"])
def school_team_remove_view(request, school_slug: str, membership_id: int):
    """POST /schools/<slug>/admin/team/<id>/remove/ — deactivate a team member."""
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "owner")
    settings_url = reverse("school_settings", kwargs={"school_slug": school_slug})

    membership = get_object_or_404(_Membership, id=membership_id, school=school, is_active=True)

    # Prevent removing the last owner.
    if membership.role == "owner":
        owner_count = _Membership.objects.filter(
            school=school, role="owner", is_active=True
        ).count()
        if owner_count <= 1:
            messages.error(request, "Cannot remove the last owner.")
            return redirect(settings_url)

    username = membership.user.username
    with transaction.atomic():
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        log_admin_audit(
            request=request, action="action", obj=school, changes={},
            extra={"name": "member_removed", "username": username},
        )
    messages.success(request, f"{membership.user.get_full_name() or username} has been removed from the team.")
    return redirect(settings_url)


@login_required
@require_http_methods(["POST"])
def school_team_update_name_view(request, school_slug: str, membership_id: int):
    """POST /schools/<slug>/admin/team/<id>/name/ — owner edits a member's display name."""
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "owner")
    settings_url = reverse("school_settings", kwargs={"school_slug": school_slug})

    membership = get_object_or_404(_Membership, id=membership_id, school=school, is_active=True)
    first_name = request.POST.get("first_name", "").strip()
    last_name  = request.POST.get("last_name", "").strip()

    user = membership.user
    old_first, old_last = user.first_name, user.last_name
    user.first_name = first_name
    user.last_name  = last_name
    with transaction.atomic():
        user.save(update_fields=["first_name", "last_name"])
        log_admin_audit(
            request=request, action="action", obj=school, changes={},
            extra={
                "name": "member_name_updated",
                "username": user.username,
                "old": f"{old_first} {old_last}".strip(),
                "new": f"{first_name} {last_name}".strip(),
            },
        )
    messages.success(request, f"Name updated for {user.username}.")
    return redirect(settings_url)
