from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from zoneinfo import ZoneInfo

from .access import require_access
from .credits import cancellation_plan, cancellation_token
from .credit_models import LessonCredit
from .lesson_forms import CancelLessonForm, SeriesForm
from .lesson_models import LessonOccurrence, LessonSeries
from .lessons import (audit, cancel_lessons, create_series, lesson_access, lesson_records,
                      plan_series, preview_token, save_lesson_setup, scheduled_lessons, setup_form)
from .models import SchedulingProfile
from .views import _context

LABELS = {"services": "service", "teachers": "teacher", "availability": "availability window",
          "closures": "lesson closure", "households": "family", "students": "student"}


class LessonSettingsForm(forms.Form):
    enabled = forms.BooleanField(required=False, label="Enable lesson scheduling")


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def settings(request, school_slug):
    profile, role = require_access(request.user, school_slug, "setup.edit", capability="lessons")
    form = LessonSettingsForm(request.POST if request.method == "POST" else None, initial={"enabled": profile.lessons_enabled})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            profile = SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
            profile, role = require_access(request.user, school_slug, "setup.edit", capability="lessons")
            if not form.cleaned_data["enabled"] and scheduled_lessons().filter(series__profile=profile, ends_at__gt=timezone.now()).exists():
                form.add_error(None, "Cancel future lessons before disabling this workspace's lesson scheduling.")
            elif not form.cleaned_data["enabled"] and LessonCredit.objects.filter(profile=profile, state="available", expires_at__gt=timezone.now()).exists():
                form.add_error(None, "Resolve unused make-up credits before disabling lesson scheduling.")
            else:
                profile.lessons_enabled = form.cleaned_data["enabled"]
                profile.save(update_fields=["lessons_enabled"])
                audit(request.user, profile, profile, "change", {"lessons_enabled": profile.lessons_enabled})
                return redirect("scheduling:lessons" if profile.lessons_enabled else "scheduling:workspace", school_slug=school_slug)
    context = _context(request, profile, role, "lessons")
    context.update(form=form, title="Lesson scheduling", submit_label="Save settings")
    return render(request, "scheduling/lesson_form.html", context, status=400 if form.errors else 200)


@never_cache
@login_required
@require_GET
def home(request, school_slug):
    profile, role = lesson_access(request.user, school_slug)
    items = LessonOccurrence.objects.filter(series__profile=profile).exclude(state="rescheduled").select_related("series__student__household", "series__location")
    family = request.GET.get("family", "")
    if family:
        household = get_object_or_404(lesson_records(profile, "households"), pk=family if family.isdigit() else 0)
        items = items.filter(series__student__household=household)
    context = _context(request, profile, role, "lessons")
    items = context["location_filter"].filter(items, "series__location")
    context.update(items=Paginator(items, 50).get_page(request.GET.get("page")),
                   households=lesson_records(profile, "households"), family=family)
    return render(request, "scheduling/lessons.html", context)


@never_cache
@login_required
@require_GET
def setup(request, school_slug):
    profile, role = lesson_access(request.user, school_slug)
    context = _context(request, profile, role, "lessons")
    for kind in LABELS:
        context[kind] = lesson_records(profile, kind)
    context["teachers"] = context["teachers"].prefetch_related("services")
    context["availability"] = context["availability"].select_related("teacher", "location")
    context["closures"] = context["closures"].select_related("teacher", "location")
    context["students"] = context["students"].select_related("household")
    return render(request, "scheduling/lesson_setup.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def edit_setup(request, school_slug, kind, pk=None):
    profile, role = lesson_access(request.user, school_slug, "setup.edit")
    if request.method == "POST":
        form = save_lesson_setup(request.user, school_slug, kind, request.POST, pk)
        if form.is_valid():
            messages.success(request, "Saved.")
            return redirect("scheduling:lesson_setup", school_slug=school_slug)
    else:
        form = setup_form(profile, kind, pk=pk)
    context = _context(request, profile, role, "lessons")
    context.update(form=form, title=f'{"Edit" if pk else "Add"} {LABELS[kind]}', submit_label="Save")
    return render(request, "scheduling/lesson_form.html", context, status=400 if form.errors else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def series_new(request, school_slug):
    profile, role = lesson_access(request.user, school_slug, "sessions.edit")
    form = SeriesForm(request.POST if request.method == "POST" else None, profile=profile)
    context = _context(request, profile, role, "lessons")
    context.update(form=form)
    if request.method == "POST" and form.is_valid():
        try:
            if request.POST.get("action") == "confirm":
                series = create_series(request.user, school_slug, request.POST, request.POST.get("preview_token", ""))
                messages.success(request, "Lesson schedule saved. No charges or notifications were sent.")
                return redirect("scheduling:lesson_series", school_slug=school_slug, public_id=series.public_id)
            rows = plan_series(form.cleaned_data)
            context.update(rows=rows, token=preview_token(request.user, profile, request.POST, form.cleaned_data, rows),
                can_confirm=any(r["state"] == "scheduled" for r in rows) and not any(r["state"] == "conflict" for r in rows),
                zone=form.cleaned_data["location"].timezone_name)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
    return render(request, "scheduling/lesson_series_form.html", context, status=400 if form.errors else 200)


@never_cache
@login_required
@require_GET
def series_detail(request, school_slug, public_id):
    from .appointments import source_booking
    profile, role = lesson_access(request.user, school_slug)
    series = get_object_or_404(LessonSeries.objects.select_related("student__household"), profile=profile, public_id=public_id)
    context = _context(request, profile, role, "lessons")
    context.update(series=series, items=series.occurrences.select_related("replacement__series", "replaces__series", "cancellation", "issued_credit", "redeemed_credit"), now=timezone.now(), booking=source_booking(series))
    with timezone.override(ZoneInfo(series.snapshot["timezone"])):
        return render(request, "scheduling/lesson_series.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def cancel(request, school_slug, pk):
    profile, role = lesson_access(request.user, school_slug, "sessions.edit")
    item = get_object_or_404(LessonOccurrence.objects.select_related("series"), pk=pk, series__profile=profile)
    form = CancelLessonForm(request.POST if request.method == "POST" else None, initial={"origin": "family"})
    context = _context(request, profile, role, "lessons")
    if request.method == "POST" and form.is_valid():
        try:
            data = form.cleaned_data
            if request.POST.get("action") == "confirm":
                series = cancel_lessons(request.user, school_slug, pk, data["scope"], data["reason"],
                                        data["origin"], request.POST.get("preview_token", ""))
                messages.success(request, "Lessons canceled. Make-up outcomes are recorded; billing is unchanged.")
                return redirect("scheduling:lesson_series", school_slug=school_slug, public_id=series.public_id)
            rows, revision = cancellation_plan(item, data["scope"], data["origin"])
            context.update(rows=rows, token=cancellation_token(request.user, item, data, revision))
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
    context.update(form=form, title="Cancel lessons", submit_label="Cancel lessons", occurrence=item)
    with timezone.override(ZoneInfo(item.series.snapshot["timezone"])):
        return render(request, "scheduling/lesson_cancel.html", context, status=400 if form.errors else 200)
