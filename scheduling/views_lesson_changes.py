from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .lesson_change_forms import AttendanceForm, RescheduleForm
from .lesson_changes import (attendance_token, change_token, get_occurrence, plan_change,
                             record_attendance, reschedule)
from .lessons import lesson_access
from .views import _context


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def change(request, school_slug, pk):
    profile, role = lesson_access(request.user, school_slug, "sessions.edit")
    item = get_occurrence(profile, pk)
    local = item.starts_at.astimezone(ZoneInfo(item.series.snapshot["timezone"]))
    form = RescheduleForm(request.POST if request.method == "POST" else None, occurrence=item,
        initial={"scope": "one", "first_date": local.date(), "start_time": local.time(), "teacher": item.series.teacher_id,
                 "location": item.series.location_id, "room": item.series.room_id})
    context = _context(request, profile, role, "lessons")
    context.update(form=form, occurrence=item)
    if request.method == "POST" and form.is_valid():
        try:
            if request.POST.get("action") == "confirm":
                result = reschedule(request.user, school_slug, pk, request.POST, request.POST.get("preview_token", ""))
                messages.success(request, "Lessons moved. Billing is unchanged; no notifications were sent.")
                return redirect("scheduling:lesson_series", school_slug=school_slug, public_id=result.public_id)
            rows, revision = plan_change(item, form.cleaned_data)
            context.update(rows=rows, token=change_token(request.user, item, request.POST, revision),
                can_confirm=all(row["state"] == "scheduled" for row in rows),
                zone=form.cleaned_data["location"].timezone_name)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
    return render(request, "scheduling/lesson_change.html", context, status=400 if form.errors else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def attendance(request, school_slug, pk):
    profile, role = lesson_access(request.user, school_slug, "sessions.edit")
    item = get_occurrence(profile, pk)
    form = AttendanceForm(request.POST if request.method == "POST" else None,
        initial={"attendance": item.attendance, "reason": item.attendance_note, "revision": attendance_token(request.user, item)})
    if request.method == "POST" and form.is_valid():
        try:
            record_attendance(request.user, school_slug, pk, request.POST)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, "Attendance saved. Billing and enrollment are unchanged.")
            return redirect("scheduling:lesson_series", school_slug=school_slug, public_id=item.series.public_id)
    context = _context(request, profile, role, "lessons")
    context.update(form=form, occurrence=item, title="Record attendance", submit_label="Save attendance")
    with timezone.override(ZoneInfo(item.series.snapshot["timezone"])):
        return render(request, "scheduling/lesson_form.html", context, status=400 if form.errors else 200)
