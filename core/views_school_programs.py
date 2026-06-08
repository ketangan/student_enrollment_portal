"""School admin views for managing DB-driven programs (SchoolProgram)."""
from __future__ import annotations

import re

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.contrib import messages

from core.admin.audit import log_admin_audit
from core.models import SchoolProgram, SchoolSession
from core.services.programs import _auto_session_code
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
    program = get_object_or_404(SchoolProgram, id=program_id, school=school, is_deleted=False)
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

    enrolled_count = program.submissions.filter(status=STATUS_ENROLLED).count()
    can_delete = not program.is_active and not program.has_submissions()

    sessions = SchoolSession.objects.filter(program=program, is_deleted=False).order_by("display_order", "name")

    ctx = _school_admin_base_context(request, school, "settings")
    ctx.update({
        "form_heading": f"Edit: {program.name}",
        "form_action": request.path,
        "back_url": settings_url,
        "errors": errors,
        "values": values,
        "is_edit": True,
        "program": program,
        "program_code": program.code,
        "is_active": program.is_active,
        "enrolled_count": enrolled_count,
        "can_delete": can_delete,
        "sessions": sessions,
        "add_session_url": reverse("school_session_create", kwargs={"school_slug": school_slug, "program_id": program.pk}),
        "deactivate_url": reverse("school_program_deactivate", kwargs={"school_slug": school_slug, "program_id": program.pk}),
        "activate_url": reverse("school_program_activate", kwargs={"school_slug": school_slug, "program_id": program.pk}),
        "delete_url": reverse("school_program_delete", kwargs={"school_slug": school_slug, "program_id": program.pk}),
    })
    return render(request, "school_admin/program_form.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_program_activate_view(request, school_slug: str, program_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school, is_deleted=False)

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
    program = get_object_or_404(SchoolProgram, id=program_id, school=school, is_deleted=False)

    if program.is_active:
        enrolled_now = program.submissions.filter(status=STATUS_ENROLLED).count()
        program.is_active = False
        program.save(update_fields=["is_active"])
        log_admin_audit(
            request=request,
            action="change",
            obj=program,
            changes={"is_active": {"old": True, "new": False}},
            extra={"name": "program_deactivated", "code": program.code, "program_name": program.name},
        )
        if enrolled_now > 0:
            messages.warning(
                request,
                f"Program '{program.name}' deactivated. "
                f"{enrolled_now} student{'' if enrolled_now == 1 else 's'} remain enrolled — their status is unchanged.",
            )
        else:
            messages.success(request, f"Program '{program.name}' deactivated.")

    return redirect(_settings_url(school_slug))


@login_required
@require_http_methods(["POST"])
def school_program_delete_view(request, school_slug: str, program_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school, is_deleted=False)

    if program.is_active:
        messages.error(request, f"Cannot delete '{program.name}' — deactivate it first.")
        return redirect(_settings_url(school_slug))

    if program.has_submissions():
        messages.error(request, f"Cannot delete '{program.name}' — it has associated submissions.")
        return redirect(_settings_url(school_slug))

    name = program.name
    code = program.code
    program.is_deleted = True
    program.save(update_fields=["is_deleted", "updated_at"])
    log_admin_audit(
        request=request,
        action="delete",
        obj=program,
        changes={},
        extra={"name": "program_deleted", "code": code, "program_name": name},
    )
    messages.success(request, f"Program '{name}' removed.")
    return redirect(_settings_url(school_slug))


# ---------------------------------------------------------------------------
# Session CRUD views
# ---------------------------------------------------------------------------

def _program_edit_url(school_slug: str, program_id: int) -> str:
    return reverse("school_program_edit", kwargs={"school_slug": school_slug, "program_id": program_id})


def _next_session_display_order(program) -> int:
    from django.db.models import Max
    result = SchoolSession.objects.filter(program=program).aggregate(Max("display_order"))
    return (result["display_order__max"] or 0) + 1


@login_required
@require_http_methods(["GET", "POST"])
def school_session_create_view(request, school_slug: str, program_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school, is_deleted=False)
    back_url = _program_edit_url(school_slug, program_id)

    errors = {}
    values = {
        "name": "", "code": "", "capacity": "",
        "start_date": "", "end_date": "",
        "auto_enroll": False, "waitlist_enabled": False,
    }

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        code_raw = request.POST.get("code", "").strip()
        capacity_raw = request.POST.get("capacity", "").strip()
        start_date_raw = request.POST.get("start_date", "").strip()
        end_date_raw = request.POST.get("end_date", "").strip()
        auto_enroll = request.POST.get("auto_enroll") == "1"
        waitlist_enabled = request.POST.get("waitlist_enabled") == "1"

        values = {
            "name": name, "code": code_raw, "capacity": capacity_raw,
            "start_date": start_date_raw, "end_date": end_date_raw,
            "auto_enroll": auto_enroll, "waitlist_enabled": waitlist_enabled,
        }

        if not name:
            errors["name"] = "Name is required."

        # Code: use custom or auto-generate; validate uniqueness within program.
        code = code_raw if code_raw else _auto_session_code(program, name) if name else ""
        if code:
            code = re.sub(r"[^a-z0-9_\-]", "", code.lower())[:64] or ""
        if not code and name:
            code = _auto_session_code(program, name)
        if code and SchoolSession.objects.filter(program=program, code=code).exists():
            errors["code"] = f"Code '{code}' is already used by another session in this program."

        capacity = None
        if capacity_raw:
            try:
                capacity = int(capacity_raw)
                if capacity <= 0:
                    errors["capacity"] = "Capacity must be a positive number."
            except ValueError:
                errors["capacity"] = "Capacity must be a whole number."

        from datetime import date as _date
        start_date = end_date = None
        if start_date_raw:
            try:
                start_date = _date.fromisoformat(start_date_raw)
            except ValueError:
                errors["start_date"] = "Enter a valid date (YYYY-MM-DD)."
        if end_date_raw:
            try:
                end_date = _date.fromisoformat(end_date_raw)
            except ValueError:
                errors["end_date"] = "Enter a valid date (YYYY-MM-DD)."
        if start_date and end_date and end_date < start_date:
            errors["end_date"] = "End date must be on or after start date."

        if not errors:
            session = SchoolSession.objects.create(
                program=program,
                name=name,
                code=code,
                capacity=capacity,
                start_date=start_date,
                end_date=end_date,
                auto_enroll=auto_enroll,
                waitlist_enabled=waitlist_enabled,
                display_order=_next_session_display_order(program),
            )
            log_admin_audit(
                request=request,
                action="add",
                obj=session,
                changes={},
                extra={
                    "name": "session_created",
                    "session_code": code,
                    "session_name": name,
                    "program_code": program.code,
                },
            )
            messages.success(request, f"Session '{name}' added to {program.name}.")
            return redirect(back_url)

    ctx = _school_admin_base_context(request, school, "settings")
    ctx.update({
        "form_heading": f"Add Session — {program.name}",
        "form_action": request.path,
        "back_url": back_url,
        "errors": errors,
        "values": values,
        "is_edit": False,
        "program": program,
    })
    return render(request, "school_admin/session_form.html", ctx)


@login_required
@require_http_methods(["GET", "POST"])
def school_session_edit_view(request, school_slug: str, program_id: int, session_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school, is_deleted=False)
    session = get_object_or_404(SchoolSession, id=session_id, program=program, is_deleted=False)
    back_url = _program_edit_url(school_slug, program_id)

    code_locked = session.has_submissions()

    errors = {}
    values = {
        "name": session.name,
        "code": session.code,
        "capacity": str(session.capacity) if session.capacity is not None else "",
        "start_date": session.start_date.isoformat() if session.start_date else "",
        "end_date": session.end_date.isoformat() if session.end_date else "",
        "auto_enroll": session.auto_enroll,
        "waitlist_enabled": session.waitlist_enabled,
    }

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        code_raw = request.POST.get("code", "").strip()
        capacity_raw = request.POST.get("capacity", "").strip()
        start_date_raw = request.POST.get("start_date", "").strip()
        end_date_raw = request.POST.get("end_date", "").strip()
        auto_enroll = request.POST.get("auto_enroll") == "1"
        waitlist_enabled = request.POST.get("waitlist_enabled") == "1"

        values = {
            "name": name, "code": code_raw, "capacity": capacity_raw,
            "start_date": start_date_raw, "end_date": end_date_raw,
            "auto_enroll": auto_enroll, "waitlist_enabled": waitlist_enabled,
        }

        if not name:
            errors["name"] = "Name is required."

        # Code is locked if submissions exist; otherwise validate+accept the edit.
        if code_locked:
            new_code = session.code
        else:
            new_code = re.sub(r"[^a-z0-9_\-]", "", code_raw.lower())[:64] if code_raw else ""
            if not new_code:
                new_code = _auto_session_code(program, name) if name else session.code
            if (
                new_code != session.code
                and SchoolSession.objects.filter(program=program, code=new_code).exists()
            ):
                errors["code"] = f"Code '{new_code}' is already used by another session in this program."

        capacity = None
        if capacity_raw:
            try:
                capacity = int(capacity_raw)
                if capacity <= 0:
                    errors["capacity"] = "Capacity must be a positive number."
            except ValueError:
                errors["capacity"] = "Capacity must be a whole number."

        from datetime import date as _date
        start_date = end_date = None
        if start_date_raw:
            try:
                start_date = _date.fromisoformat(start_date_raw)
            except ValueError:
                errors["start_date"] = "Enter a valid date (YYYY-MM-DD)."
        if end_date_raw:
            try:
                end_date = _date.fromisoformat(end_date_raw)
            except ValueError:
                errors["end_date"] = "Enter a valid date (YYYY-MM-DD)."
        if start_date and end_date and end_date < start_date:
            errors["end_date"] = "End date must be on or after start date."

        if not errors:
            old_capacity = session.capacity
            old_auto_enroll = session.auto_enroll
            changed = {}
            if session.name != name:
                changed["name"] = {"old": session.name, "new": name}
            if not code_locked and session.code != new_code:
                changed["code"] = {"old": session.code, "new": new_code}
            if session.capacity != capacity:
                changed["capacity"] = {"old": old_capacity, "new": capacity}
            if session.auto_enroll != auto_enroll:
                changed["auto_enroll"] = {"old": old_auto_enroll, "new": auto_enroll}
            if session.waitlist_enabled != waitlist_enabled:
                changed["waitlist_enabled"] = {"old": session.waitlist_enabled, "new": waitlist_enabled}

            session.name = name
            if not code_locked:
                session.code = new_code
            session.capacity = capacity
            session.start_date = start_date
            session.end_date = end_date
            session.auto_enroll = auto_enroll
            session.waitlist_enabled = waitlist_enabled
            session.save()

            if changed:
                log_admin_audit(
                    request=request,
                    action="change",
                    obj=session,
                    changes=changed,
                    extra={"name": "session_edited", "session_code": session.code},
                )

            if "capacity" in changed:
                enrolled_now = session.submissions.filter(status=STATUS_ENROLLED).count()
                log_admin_audit(
                    request=request,
                    action="action",
                    obj=session,
                    changes={},
                    extra={
                        "name": "session_capacity_changed",
                        "old_capacity": old_capacity,
                        "new_capacity": capacity,
                        "current_enrolled": enrolled_now,
                        "session_code": session.code,
                    },
                )
                if (
                    capacity is not None
                    and old_capacity is not None
                    and capacity < old_capacity
                ):
                    if enrolled_now > capacity:
                        messages.warning(
                            request,
                            f"Warning: {enrolled_now} students are enrolled in '{session.name}', "
                            f"which exceeds the new capacity of {capacity}.",
                        )

            if "auto_enroll" in changed:
                log_admin_audit(
                    request=request,
                    action="action",
                    obj=session,
                    changes={},
                    extra={
                        "name": "session_auto_enroll_changed",
                        "old": old_auto_enroll,
                        "new": auto_enroll,
                        "session_code": session.code,
                    },
                )

            messages.success(request, f"Session '{session.name}' updated.")
            return redirect(back_url)

    enrolled_count = session.submissions.filter(status=STATUS_ENROLLED).count()
    can_delete = not session.is_active and not session.has_submissions()

    ctx = _school_admin_base_context(request, school, "settings")
    ctx.update({
        "form_heading": f"Edit Session — {session.name}",
        "form_action": request.path,
        "back_url": back_url,
        "errors": errors,
        "values": values,
        "is_edit": True,
        "program": program,
        "session": session,
        "session_code": session.code,
        "code_locked": code_locked,
        "is_active": session.is_active,
        "enrolled_count": enrolled_count,
        "can_delete": can_delete,
        "activate_url": reverse("school_session_activate", kwargs={"school_slug": school_slug, "program_id": program_id, "session_id": session_id}),
        "deactivate_url": reverse("school_session_deactivate", kwargs={"school_slug": school_slug, "program_id": program_id, "session_id": session_id}),
        "delete_url": reverse("school_session_delete", kwargs={"school_slug": school_slug, "program_id": program_id, "session_id": session_id}),
    })
    return render(request, "school_admin/session_form.html", ctx)


@login_required
@require_http_methods(["POST"])
def school_session_activate_view(request, school_slug: str, program_id: int, session_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school, is_deleted=False)
    session = get_object_or_404(SchoolSession, id=session_id, program=program, is_deleted=False)

    if not session.is_active:
        session.is_active = True
        session.save(update_fields=["is_active"])
        log_admin_audit(
            request=request,
            action="change",
            obj=session,
            changes={"is_active": {"old": False, "new": True}},
            extra={"name": "session_activated", "session_code": session.code},
        )
        messages.success(request, f"Session '{session.name}' reactivated.")

    return redirect(_program_edit_url(school_slug, program_id))


@login_required
@require_http_methods(["POST"])
def school_session_deactivate_view(request, school_slug: str, program_id: int, session_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school, is_deleted=False)
    session = get_object_or_404(SchoolSession, id=session_id, program=program, is_deleted=False)

    if session.is_active:
        enrolled_now = session.submissions.filter(status=STATUS_ENROLLED).count()
        session.is_active = False
        session.save(update_fields=["is_active"])
        log_admin_audit(
            request=request,
            action="change",
            obj=session,
            changes={"is_active": {"old": True, "new": False}},
            extra={"name": "session_deactivated", "session_code": session.code},
        )
        if enrolled_now > 0:
            messages.warning(
                request,
                f"Session '{session.name}' deactivated. "
                f"{enrolled_now} student{'' if enrolled_now == 1 else 's'} remain enrolled — their status is unchanged.",
            )
        else:
            messages.success(request, f"Session '{session.name}' deactivated.")

    return redirect(_program_edit_url(school_slug, program_id))


@login_required
@require_http_methods(["POST"])
def school_session_delete_view(request, school_slug: str, program_id: int, session_id: int):
    school = _get_accessible_school_for_admin(request, school_slug)
    program = get_object_or_404(SchoolProgram, id=program_id, school=school, is_deleted=False)
    session = get_object_or_404(SchoolSession, id=session_id, program=program, is_deleted=False)

    if session.is_active:
        messages.error(request, f"Cannot remove '{session.name}' — deactivate it first.")
        return redirect(_program_edit_url(school_slug, program_id))

    if session.has_submissions():
        messages.error(request, f"Cannot remove '{session.name}' — it has associated submissions.")
        return redirect(_program_edit_url(school_slug, program_id))

    name = session.name
    session.is_deleted = True
    session.save(update_fields=["is_deleted", "updated_at"])
    log_admin_audit(
        request=request,
        action="delete",
        obj=session,
        changes={},
        extra={"name": "session_deleted", "session_code": session.code, "program_code": program.code},
    )
    messages.success(request, f"Session '{name}' removed.")
    return redirect(_program_edit_url(school_slug, program_id))
