from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from zoneinfo import ZoneInfo

from .access import scope_locations
from .lesson_models import LessonAvailability, LessonClosure
from .teacher_availability import (availability_form, save_availability, save_time_off,
    teacher_access, teacher_occurrences, teacher_windows, time_off_form)


def _context(profile, teacher, active):
    return {"profile": profile, "business": profile.school, "teacher": teacher, "teacher_active": active}


@never_cache
@login_required
@require_GET
def schedule(request, school_slug):
    profile, teacher = teacher_access(request.user, school_slug, request=request)
    period = "past" if request.GET.get("period") == "past" else "upcoming"
    items = teacher_occurrences(profile, teacher).exclude(state="rescheduled")
    if period == "past":
        items = items.filter(ends_at__lt=timezone.now()).order_by("-starts_at", "pk")
    else:
        items = items.filter(ends_at__gte=timezone.now())
    context = _context(profile, teacher, "schedule")
    context.update(items=Paginator(items, 50).get_page(request.GET.get("page")), period=period)
    return render(request, "scheduling/teacher_schedule.html", context)


@never_cache
@login_required
@require_GET
def availability(request, school_slug):
    profile, teacher = teacher_access(request.user, school_slug, request=request)
    days = [{"label": label, "windows": []} for _, label in LessonAvailability._meta.get_field("weekday").choices]
    for window in teacher_windows(profile, teacher):
        today = timezone.localdate(timezone=ZoneInfo(window.location.timezone_name))
        if window.effective_until is None or window.effective_until >= today:
            days[window.weekday]["windows"].append(window)
    closures = scope_locations(LessonClosure.objects.filter(location__profile=profile).filter(
        Q(teacher=teacher) | Q(teacher=None)), profile, "location_id").select_related("location")
    context = _context(profile, teacher, "availability")
    context.update(days=days, closures=[item for item in closures
        if item.last_date >= timezone.localdate(timezone=ZoneInfo(item.location.timezone_name))])
    return render(request, "scheduling/teacher_availability.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def availability_edit(request, school_slug, pk=None):
    profile, teacher = teacher_access(request.user, school_slug, request=request)
    if request.method == "POST":
        form = save_availability(request.user, profile, request.POST, pk, teacher=teacher)
        if form.is_valid():
            messages.success(request, "Future availability saved. Existing lessons are unchanged.")
            return redirect("scheduling:teacher_availability", school_slug=school_slug)
    else:
        form = availability_form(profile, pk=pk, teacher=teacher)
    context = _context(profile, teacher, "availability")
    context.update(form=form, title="Edit future availability" if pk else "Add availability")
    return render(request, "scheduling/teacher_form.html", context, status=400 if form.errors else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def time_off(request, school_slug):
    profile, teacher = teacher_access(request.user, school_slug, request=request)
    if request.method == "POST":
        form = save_time_off(request.user, school_slug, request.POST)
        if form.is_valid():
            messages.success(request, "Time off saved.")
            return redirect("scheduling:teacher_availability", school_slug=school_slug)
    else:
        form = time_off_form(profile, teacher)
    context = _context(profile, teacher, "availability")
    context.update(form=form, title="Add time off")
    return render(request, "scheduling/teacher_form.html", context, status=400 if form.errors else 200)
