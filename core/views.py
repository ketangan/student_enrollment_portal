from collections import Counter
from datetime import timedelta
import csv

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone

from .models import School, Submission
from .services.config_loader import load_school_config
from .services.form_utils import build_option_label_map
from .services.validation import validate_submission


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


def apply_view(request, school_slug: str):
    config = load_school_config(school_slug)
    if config is None:
        raise Http404("School config not found")

    branding = merge_branding(getattr(config, "branding", None))

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

    if request.method == "POST":
        cleaned, errors = validate_submission(config.form, request.POST, request.FILES)
        if errors:
            return render(
                request,
                "apply_form.html",
                {
                    "school": school,
                    "branding": branding,
                    "form": config.form,
                    "errors": errors,
                    "values": request.POST,
                },
            )

        Submission.objects.create(school=school, data=cleaned)
        return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

    return render(
        request,
        "apply_form.html",
        {
            "school": school,
            "branding": branding,
            "form": config.form,
            "errors": {},
            "values": {},
        },
    )


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
        },
    )


def _can_view_school_admin_page(request, school: School) -> bool:
    user = request.user
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    membership = getattr(user, "school_membership", None)
    return bool(user.is_staff and membership and membership.school_id == school.id)


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
    if export:
        all_keys = set()
        for s in rows_for_reporting:
            all_keys.update((s.data or {}).keys())

        ordered_keys = ["created_at", "student_name", "program"] + sorted(all_keys)

        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = f'attachment; filename="{school.slug}-reports-last{range_days}d.csv"'

        writer = csv.writer(resp)
        writer.writerow(ordered_keys)

        for s in rows_for_reporting:
            data = s.data or {}
            created = timezone.localtime(s.created_at).isoformat()
            student = s.student_display_name()
            program = (s.program_display_name(label_map=label_map) or "").strip() or NONE_LABEL

            writer.writerow([created, student, program] + [data.get(k, "") for k in sorted(all_keys)])

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
            }
        )

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
        },
    )
