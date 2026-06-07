"""School admin views for managing DB-driven programs (SchoolProgram)."""
from __future__ import annotations

import re

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.contrib import messages

from core.admin.audit import log_admin_audit
from core.models import SchoolProgram
from core.views_school_common import (
    STATUS_ENROLLED,
    _get_accessible_school_for_admin,
    _school_admin_base_context,
)


def _auto_code(school, name: str) -> str:
    """Generate a unique slug-like code from name. 'Ballet Beginner' → 'ballet_beginner'."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower().strip()).strip("_")[:40] or "program"
    code = base
    n = 2
    while SchoolProgram.objects.filter(school=school, code=code).exists():
        code = f"{base}_{n}"
        n += 1
    return code


def _next_display_order(school) -> int:
    """Return max existing display_order + 1 (appends to end of list)."""
    from django.db.models import Max
    result = SchoolProgram.objects.filter(school=school).aggregate(Max("display_order"))
    return (result["display_order__max"] or 0) + 1


def _settings_url(school_slug: str) -> str:
    return reverse("school_settings", kwargs={"school_slug": school_slug})


@login_required
@require_http_methods(["GET"])
def school_programs_list_view(request, school_slug: str):
    # Programs are now embedded in the Settings page.
    _get_accessible_school_for_admin(request, school_slug)
    return redirect(_settings_url(school_slug))


@login_required
@require_http_methods(["GET", "POST"])
def school_program_create_view(request, school_slug: str):
    school = _get_accessible_school_for_admin(request, school_slug)
    settings_url = _settings_url(school_slug)

    errors = {}
    values = {"name": "", "capacity": "", "auto_enroll": False, "waitlist_enabled": False}

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        capacity_raw = request.POST.get("capacity", "").strip()
        auto_enroll = request.POST.get("auto_enroll") == "1"
        waitlist_enabled = request.POST.get("waitlist_enabled") == "1"

        values = {
            "name": name,
            "capacity": capacity_raw,
            "auto_enroll": auto_enroll,
            "waitlist_enabled": waitlist_enabled,
        }

        if not name:
            errors["name"] = "Name is required."

        capacity = None
        if capacity_raw:
            try:
                capacity = int(capacity_raw)
                if capacity <= 0:
                    errors["capacity"] = "Capacity must be a positive number."
            except ValueError:
                errors["capacity"] = "Capacity must be a whole number."

        if not errors:
            code = _auto_code(school, name)
            display_order = _next_display_order(school)
            program = SchoolProgram.objects.create(
                school=school,
                name=name,
                code=code,
                capacity=capacity,
                auto_enroll=auto_enroll,
                waitlist_enabled=waitlist_enabled,
                display_order=display_order,
            )
            log_admin_audit(
                request=request,
                action="add",
                obj=program,
                changes={},
                extra={
                    "name": "program_created",
                    "code": code,
                    "program_name": name,
                    "capacity": capacity,
                    "auto_enroll": auto_enroll,
                    "waitlist_enabled": waitlist_enabled,
                },
            )
            messages.success(request, f"Program '{name}' added.")
            return redirect(settings_url)

    ctx = _school_admin_base_context(request, school, "settings")
    ctx.update({
        "form_heading": "Add Program",
        "form_action": request.path,
        "back_url": settings_url,
        "errors": errors,
        "values": values,
        "is_edit": False,
    })
    return render(request, "school_admin/program_form.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
def school_program_edit_view(request, school_slug: str, program_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school)
    settings_url = _settings_url(school_slug)

    errors = {}
    values = {
        "name": program.name,
        "capacity": str(program.capacity) if program.capacity is not None else "",
        "auto_enroll": program.auto_enroll,
        "waitlist_enabled": program.waitlist_enabled,
    }

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        capacity_raw = request.POST.get("capacity", "").strip()
        auto_enroll = request.POST.get("auto_enroll") == "1"
        waitlist_enabled = request.POST.get("waitlist_enabled") == "1"

        values = {
            "name": name,
            "capacity": capacity_raw,
            "auto_enroll": auto_enroll,
            "waitlist_enabled": waitlist_enabled,
        }

        if not name:
            errors["name"] = "Name is required."

        capacity = None
        if capacity_raw:
            try:
                capacity = int(capacity_raw)
                if capacity <= 0:
                    errors["capacity"] = "Capacity must be a positive number."
            except ValueError:
                errors["capacity"] = "Capacity must be a whole number."

        if not errors:
            old_capacity = program.capacity
            old_auto_enroll = program.auto_enroll
            changed_fields = {}

            if program.name != name:
                changed_fields["name"] = {"old": program.name, "new": name}
            if program.capacity != capacity:
                changed_fields["capacity"] = {"old": old_capacity, "new": capacity}
            if program.auto_enroll != auto_enroll:
                changed_fields["auto_enroll"] = {"old": old_auto_enroll, "new": auto_enroll}
            if program.waitlist_enabled != waitlist_enabled:
                changed_fields["waitlist_enabled"] = {"old": program.waitlist_enabled, "new": waitlist_enabled}

            program.name = name
            program.capacity = capacity
            program.auto_enroll = auto_enroll
            program.waitlist_enabled = waitlist_enabled
            program.save()

            if changed_fields:
                extra = {"name": "program_edited", "changed_fields": changed_fields}
                if "capacity" in changed_fields:
                    extra["current_enrolled"] = program.submissions.filter(status=STATUS_ENROLLED).count()
                log_admin_audit(
                    request=request,
                    action="change",
                    obj=program,
                    changes=changed_fields,
                    extra=extra,
                )

            if "capacity" in changed_fields:
                enrolled_now = program.submissions.filter(status=STATUS_ENROLLED).count()
                log_admin_audit(
                    request=request,
                    action="action",
                    obj=program,
                    changes={},
                    extra={
                        "name": "program_capacity_changed",
                        "old_capacity": old_capacity,
                        "new_capacity": capacity,
                        "current_enrolled": enrolled_now,
                    },
                )

            if "auto_enroll" in changed_fields:
                log_admin_audit(
                    request=request,
                    action="action",
                    obj=program,
                    changes={},
                    extra={
                        "name": "program_auto_enroll_changed",
                        "old": old_auto_enroll,
                        "new": auto_enroll,
                    },
                )

            messages.success(request, f"Program '{name}' updated.")

            if (
                capacity is not None
                and old_capacity is not None
                and capacity < old_capacity
            ):
                enrolled_now = program.submissions.filter(status=STATUS_ENROLLED).count()
                if enrolled_now > capacity:
                    messages.warning(
                        request,
                        f"Warning: {enrolled_now} students are currently enrolled in '{name}', "
                        f"which exceeds the new capacity of {capacity}.",
                    )

            return redirect(settings_url)

    ctx = _school_admin_base_context(request, school, "settings")
    ctx.update({
        "form_heading": f"Edit: {program.name}",
        "form_action": request.path,
        "back_url": settings_url,
        "errors": errors,
        "values": values,
        "is_edit": True,
        "program_code": program.code,
    })
    return render(request, "school_admin/program_form.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_program_activate_view(request, school_slug: str, program_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school)

    if not program.is_active:
        program.is_active = True
        program.save(update_fields=["is_active"])
        log_admin_audit(
            request=request,
            action="change",
            obj=program,
            changes={"is_active": {"old": False, "new": True}},
            extra={"name": "program_activated", "code": program.code, "program_name": program.name},
        )
        messages.success(request, f"Program '{program.name}' reactivated.")

    return redirect(_settings_url(school_slug))


@login_required
@require_http_methods(["POST"])
def school_program_deactivate_view(request, school_slug: str, program_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school)
    has_subs = program.has_submissions()

    if has_subs:
        program.is_active = False
        program.save(update_fields=["is_active"])
        log_admin_audit(
            request=request,
            action="change",
            obj=program,
            changes={"is_active": {"old": True, "new": False}},
            extra={
                "name": "program_deactivated",
                "code": program.code,
                "program_name": program.name,
                "has_submissions": True,
            },
        )
        messages.success(request, f"Program '{program.name}' deactivated (submissions preserved).")
    else:
        name = program.name
        log_admin_audit(
            request=request,
            action="delete",
            obj=program,
            changes={},
            extra={
                "name": "program_deleted",
                "code": program.code,
                "program_name": name,
                "has_submissions": False,
            },
        )
        program.delete()
        messages.success(request, f"Program '{name}' deleted.")

    return redirect(_settings_url(school_slug))
