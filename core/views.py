import logging
from collections import Counter
from datetime import timedelta
import csv

logger = logging.getLogger(__name__)

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, FileResponse
from django.shortcuts import get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone

from .models import School, Submission, SubmissionFile
from .services.config_loader import get_forms, load_school_config
from .services.form_utils import build_option_label_map
from .services.validation import validate_submission
from .services.notifications import send_submission_notification_email


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


def _multi_session_key(school_slug: str) -> str:
    return f"apply_submission_id:{school_slug}"


def _get_multi_submission(request, school: School, school_slug: str) -> Submission | None:
    submission_id = request.session.get(_multi_session_key(school_slug))
    if not submission_id:
        return None
    return Submission.objects.filter(id=submission_id, school=school).first()


def _ensure_multi_submission(request, school: School, school_slug: str) -> Submission:
    """
    Create the multi-form Submission only when needed (POST first step),
    then persist id in session.
    """
    existing = _get_multi_submission(request, school, school_slug)
    if existing:
        return existing

    submission = Submission.objects.create(
        school=school,
        form_key="multi",
        data={},
    )
    request.session[_multi_session_key(school_slug)] = submission.id
    return submission


def _merge_submission_data(submission: Submission, cleaned: dict) -> None:
    merged = dict(submission.data or {})
    merged.update(cleaned or {})
    Submission.objects.filter(pk=submission.pk).update(data=merged)
    submission.data = merged  # keep in-memory object consistent


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

def apply_view(request, school_slug: str, form_key: str = "default"):
    config = load_school_config(school_slug)
    if config is None:
        raise Http404("School config not found")

    branding = merge_branding(getattr(config, "branding", None))
    school = _get_or_create_school_from_config(school_slug, config, branding)

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

        if request.method == "POST":
            cleaned, errors = validate_submission(form_cfg, request.POST, request.FILES)
            if errors:
                return render(
                    request,
                    "apply_form.html",
                    _apply_form_context(
                        school=school,
                        branding=branding,
                        form=form_cfg,
                        is_multi=False,
                        form_key="default",
                        next_key=None,
                        errors=errors,
                        values=request.POST,
                    ),
                )

            submission = Submission.objects.create(school=school, form_key="default", data=cleaned)
            if school.features.file_uploads_enabled:
                _save_uploaded_files(submission, form_cfg, request.FILES)
            if school.features.email_notifications_enabled:
                try:
                    send_submission_notification_email(
                        request=request,
                        config_raw=getattr(config, "raw", {}) or {},
                        school_name=config.display_name,
                        submission_id=submission.id,
                        submission_public_id=submission.public_id,
                        student_name=submission.student_display_name(),
                        submission_data=submission.data or {},
                    )
                except Exception:
                    logger.exception("Failed to send submission notification email")

            return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

        return render(
            request,
            "apply_form.html",
            _apply_form_context(
                school=school,
                branding=branding,
                form=form_cfg,
                is_multi=False,
                form_key="default",
                next_key=None,
                errors={},
                values={},
            ),
        )

    # ----------------------------
    # MULTI-FORM SCHOOL
    # ----------------------------

    # If user hits /apply (default), jump to first configured form key
    if is_multi and form_key == "default":
        first_key = next(iter(forms.keys()))
        return redirect(reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": first_key}))

    form_cfg, ordered_keys, next_key = _get_multi_form_context(config, form_key)

    # GET: do NOT create Submission yet. Only load existing (if any) to prefill values.
    submission = _get_multi_submission(request, school, school_slug)

    if request.method == "POST":
        cleaned, errors = validate_submission(form_cfg, request.POST, request.FILES)
        if errors:
            return render(
                request,
                "apply_form.html",
                _apply_form_context(
                    school=school,
                    branding=branding,
                    form=form_cfg,
                    is_multi=True,
                    form_key=form_key,
                    next_key=next_key,
                    errors=errors,
                    values=request.POST,
                ),
            )

        # Create or reuse ONE submission for the whole flow
        submission = _ensure_multi_submission(request, school, school_slug)

        _merge_submission_data(submission, cleaned)
        if school.features.file_uploads_enabled:
            _save_uploaded_files(submission, form_cfg, request.FILES)

        # Next step or finish
        if next_key:
            return redirect(reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": next_key}))

        # Done: clear session key and go success
        request.session.pop(_multi_session_key(school_slug), None)
        if school.features.email_notifications_enabled:
            try:
                send_submission_notification_email(
                    request=request,
                    config_raw=getattr(config, "raw", {}) or {},
                    school_name=config.display_name,
                    submission_id=submission.id,
                    submission_public_id=submission.public_id,
                    student_name=submission.student_display_name(),
                    submission_data=submission.data or {},
                )
            except Exception:
                logger.exception("Failed to send submission notification email")

        return redirect(reverse("apply_success", kwargs={"school_slug": school_slug}))

    # GET render
    return render(
        request,
        "apply_form.html",
        _apply_form_context(
            school=school,
            branding=branding,
            form=form_cfg,
            is_multi=True,
            form_key=form_key,
            next_key=next_key,
            errors={},
            values=submission.data if submission else {},
        ),
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

@staff_member_required
def admin_download_submission_file(request, file_id: int):
    sf = get_object_or_404(SubmissionFile, id=file_id)

    # Superuser OK, otherwise enforce same-school access
    user = request.user
    if not user.is_superuser:
        membership = getattr(user, "school_membership", None)
        if not (membership and membership.school_id == sf.submission.school_id):
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
        },
    )
