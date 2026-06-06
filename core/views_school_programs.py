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
from core.services.programs import get_programs_summary
from core.views_school_common import (
    STATUS_ENROLLED,
    _get_accessible_school_for_admin,
    _school_admin_base_context,
)

_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@login_required
@require_http_methods(["GET"])
def school_programs_list_view(request, school_slug: str):
    school = _get_accessible_school_for_admin(request, school_slug)
    summary = get_programs_summary(school)
    no_active_warning = (
        bool(school.program_field_key)
        and not SchoolProgram.objects.filter(school=school, is_active=True).exists()
    )
    ctx = _school_admin_base_context(request, school, "programs")
    ctx.update({
        "programs_summary": summary,
        "program_field_key": school.program_field_key,
        "no_active_warning": no_active_warning,
        "create_url": reverse("school_program_create", kwargs={"school_slug": school_slug}),
    })
    return render(request, "school_admin/programs.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
def school_program_create_view(request, school_slug: str):
    school = _get_accessible_school_for_admin(request, school_slug)
    list_url = reverse("school_programs_list", kwargs={"school_slug": school_slug})

    errors = {}
    values = {
        "name": "",
        "code": "",
        "capacity": "",
        "auto_enroll": False,
        "waitlist_enabled": False,
        "display_order": 0,
    }

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        code = request.POST.get("code", "").strip()
        capacity_raw = request.POST.get("capacity", "").strip()
        auto_enroll = request.POST.get("auto_enroll") == "1"
        waitlist_enabled = request.POST.get("waitlist_enabled") == "1"
        display_order_raw = request.POST.get("display_order", "0").strip()

        values = {
            "name": name,
            "code": code,
            "capacity": capacity_raw,
            "auto_enroll": auto_enroll,
            "waitlist_enabled": waitlist_enabled,
            "display_order": display_order_raw,
        }

        if not name:
            errors["name"] = "Name is required."
        if not code:
            errors["code"] = "Code is required."
        elif not _CODE_RE.match(code):
            errors["code"] = "Code must contain only lowercase letters, numbers, hyphens, and underscores."
        elif SchoolProgram.objects.filter(school=school, code=code).exists():
            errors["code"] = f"A program with code '{code}' already exists."

        capacity = None
        if capacity_raw:
            try:
                capacity = int(capacity_raw)
                if capacity <= 0:
                    errors["capacity"] = "Capacity must be a positive number."
            except ValueError:
                errors["capacity"] = "Capacity must be a whole number."

        display_order = 0
        try:
            display_order = int(display_order_raw or "0")
        except ValueError:
            display_order = 0

        if not errors:
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
            messages.success(request, f"Program '{name}' created.")
            return redirect(list_url)

    ctx = _school_admin_base_context(request, school, "programs")
    ctx.update({
        "form_heading": "Add Program",
        "form_action": request.path,
        "cancel_url": list_url,
        "errors": errors,
        "values": values,
        "is_edit": False,
        "code_locked": False,
    })
    return render(request, "school_admin/program_form.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
def school_program_edit_view(request, school_slug: str, program_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school)
    list_url = reverse("school_programs_list", kwargs={"school_slug": school_slug})
    code_locked = program.has_submissions()

    errors = {}
    values = {
        "name": program.name,
        "code": program.code,
        "capacity": str(program.capacity) if program.capacity is not None else "",
        "auto_enroll": program.auto_enroll,
        "waitlist_enabled": program.waitlist_enabled,
        "display_order": program.display_order,
    }

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        code = request.POST.get("code", "").strip() if not code_locked else program.code
        capacity_raw = request.POST.get("capacity", "").strip()
        auto_enroll = request.POST.get("auto_enroll") == "1"
        waitlist_enabled = request.POST.get("waitlist_enabled") == "1"
        display_order_raw = request.POST.get("display_order", "0").strip()

        values = {
            "name": name,
            "code": code,
            "capacity": capacity_raw,
            "auto_enroll": auto_enroll,
            "waitlist_enabled": waitlist_enabled,
            "display_order": display_order_raw,
        }

        if not name:
            errors["name"] = "Name is required."
        if not code:
            errors["code"] = "Code is required."
        elif not code_locked and not _CODE_RE.match(code):
            errors["code"] = "Code must contain only lowercase letters, numbers, hyphens, and underscores."
        elif not code_locked and code != program.code and SchoolProgram.objects.filter(school=school, code=code).exclude(pk=program.pk).exists():
            errors["code"] = f"A program with code '{code}' already exists."

        capacity = None
        if capacity_raw:
            try:
                capacity = int(capacity_raw)
                if capacity <= 0:
                    errors["capacity"] = "Capacity must be a positive number."
            except ValueError:
                errors["capacity"] = "Capacity must be a whole number."

        display_order = 0
        try:
            display_order = int(display_order_raw or "0")
        except ValueError:
            display_order = 0

        if not errors:
            old_capacity = program.capacity
            old_auto_enroll = program.auto_enroll
            changed_fields = {}

            if program.name != name:
                changed_fields["name"] = {"old": program.name, "new": name}
            if not code_locked and program.code != code:
                changed_fields["code"] = {"old": program.code, "new": code}
            if program.capacity != capacity:
                changed_fields["capacity"] = {"old": old_capacity, "new": capacity}
            if program.auto_enroll != auto_enroll:
                changed_fields["auto_enroll"] = {"old": old_auto_enroll, "new": auto_enroll}
            if program.waitlist_enabled != waitlist_enabled:
                changed_fields["waitlist_enabled"] = {"old": program.waitlist_enabled, "new": waitlist_enabled}
            if program.display_order != display_order:
                changed_fields["display_order"] = {"old": program.display_order, "new": display_order}

            program.name = name
            if not code_locked:
                program.code = code
            program.capacity = capacity
            program.auto_enroll = auto_enroll
            program.waitlist_enabled = waitlist_enabled
            program.display_order = display_order
            program.save()

            if changed_fields:
                extra = {"name": "program_edited", "changed_fields": changed_fields}
                if "capacity" in changed_fields:
                    enrolled = program.submissions.filter(status=STATUS_ENROLLED).count()
                    extra["current_enrolled"] = enrolled
                log_admin_audit(
                    request=request,
                    action="change",
                    obj=program,
                    changes=changed_fields,
                    extra=extra,
                )

            messages.success(request, f"Program '{name}' updated.")

            # Warn when capacity decrease puts current enrolled over the new cap
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

            return redirect(list_url)

    ctx = _school_admin_base_context(request, school, "programs")
    ctx.update({
        "form_heading": f"Edit Program: {program.name}",
        "form_action": request.path,
        "cancel_url": list_url,
        "errors": errors,
        "values": values,
        "is_edit": True,
        "code_locked": code_locked,
        "program": program,
    })
    return render(request, "school_admin/program_form.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_program_deactivate_view(request, school_slug: str, program_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school)
    list_url = reverse("school_programs_list", kwargs={"school_slug": school_slug})
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
                "name": "program_deactivated",
                "code": program.code,
                "program_name": name,
                "has_submissions": False,
            },
        )
        program.delete()
        messages.success(request, f"Program '{name}' deleted.")

    return redirect(list_url)
