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
    from .school_navigation import navigation_policy
    if navigation_policy(profile.school)["simplified"]:
        return redirect("scheduling:school_schedule", school_slug=school_slug)
    items = LessonOccurrence.objects.filter(series__profile=profile).exclude(state="rescheduled").select_related("series__student__household", "series__location")
    family = request.GET.get("family", "")
    if family:
        from .school_navigation import require_optional_feature
        require_optional_feature(profile, "family_management")
        household = get_object_or_404(lesson_records(profile, "households"), pk=family if family.isdigit() else 0)
        items = items.filter(series__student__household=household)
    context = _context(request, profile, role, "lessons")
    items = context["location_filter"].filter(items, "series__location")
    period = request.GET.get("period", "upcoming") if context["school_layout"] else "all"
    if period not in {"upcoming", "past", "all"}:
        period = "upcoming"
    if period == "upcoming":
        items = items.filter(ends_at__gte=timezone.now())
    elif period == "past":
        items = items.filter(ends_at__lt=timezone.now()).order_by("-starts_at", "pk")
    context.update(items=Paginator(items, 50).get_page(request.GET.get("page")),
                   households=lesson_records(profile, "households"), family=family, period=period)
    return render(request, "scheduling/lessons.html", context)


@never_cache
@login_required
@require_GET
def setup(request, school_slug):
    profile, role = lesson_access(request.user, school_slug)
    context = _context(request, profile, role, "lessons")
    section = request.GET.get("section", "teachers")
    if section == "students":
        from .school_navigation import require_optional_feature
        require_optional_feature(profile, "family_management")
    sections = {"teachers", "students", "services"} if context["simplified_school"] else {"teachers", "students"}
    context["setup_section"] = section if section in sections else "teachers"
    for kind in LABELS:
        context[kind] = lesson_records(profile, kind)
    context["teachers"] = context["teachers"].prefetch_related("services")
    context["availability"] = context["availability"].select_related("teacher", "location")
    context["closures"] = context["closures"].select_related("teacher", "location")
    context["students"] = context["students"].select_related("household")
    if context["simplified_school"] and context["setup_section"] in {"teachers", "services"}:
        teacher_id = request.GET.get("teacher", "")
        teacher = get_object_or_404(context["teachers"], pk=teacher_id if teacher_id.isdigit() else 0) if teacher_id else None
        context["selected_teacher"] = teacher
        if teacher:
            context["availability"] = context["availability"].filter(teacher=teacher)
            from django.db.models import Q
            context["closures"] = context["closures"].filter(Q(teacher=teacher) | Q(teacher=None))
        elif context["setup_section"] == "teachers" and request.GET.get("view", "week") != "list":
            from .teacher_overview import weekly_overview
            context.update(weekly_overview(request, profile, context))
        return render(request, "scheduling/school_teacher_setup.html", context)
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
            from django.urls import reverse
            from .school_navigation import navigation_policy
            url = reverse("scheduling:lesson_setup", kwargs={"school_slug": school_slug})
            policy = navigation_policy(profile.school)
            if policy["layout"] == "school":
                url += "?section=" + ("students" if kind in {"students", "households"} else "services" if kind == "services" and policy["simplified"] else "teachers")
                if kind == "teachers" and policy["simplified"]:
                    url += "&view=list"
                if kind in {"availability", "closures"} and form.instance.teacher_id:
                    url += "&teacher=" + str(form.instance.teacher_id)
            return redirect(url)
    else:
        form = setup_form(profile, kind, pk=pk)
        teacher_id = request.GET.get("teacher", "")
        if not pk and kind in {"availability", "closures"} and teacher_id:
            teacher = get_object_or_404(lesson_records(profile, "teachers"), pk=teacher_id if teacher_id.isdigit() else 0)
            form.initial["teacher"] = teacher.pk
    context = _context(request, profile, role, "lessons")
    context.update(form=form, title=f'{"Edit" if pk else "Add"} {LABELS[kind]}', submit_label="Save",
        setup_section="students" if kind in {"students", "households"} else "teachers")
    if context["simplified_school"] and kind in {"services", "teachers"}:
        from django.urls import reverse
        context["setup_kind"] = kind
        context["setup_return_url"] = reverse("scheduling:lesson_setup", kwargs={"school_slug": school_slug}) + (
            "?section=services" if kind == "services" else "?view=list")
        context["setup_return_label"] = "Lesson types" if kind == "services" else "Teachers & availability"
        if kind == "services":
            context["title"] = f'{"Edit" if pk else "Add"} lesson type'
    if context["simplified_school"] and kind in {"availability", "closures"}:
        from django.urls import reverse
        label = "hours" if kind == "availability" else "time off"
        context["title"] = f'{"Edit" if pk else "Add"} {label}'
        teacher_id = form.instance.teacher_id or form.initial.get("teacher")
        if teacher_id:
            context["teacher_return_url"] = reverse("scheduling:lesson_setup", kwargs={"school_slug": school_slug}) + f"?teacher={teacher_id}"
    return render(request, "scheduling/lesson_form.html", context, status=400 if form.errors else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def series_new(request, school_slug):
    profile, role = lesson_access(request.user, school_slug, "sessions.edit")
    from .school_navigation import workflow_policy
    from django.http import Http404
    if "recurring_lessons" not in workflow_policy(profile.school)["optional_features"]:
        raise Http404
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
    booking = source_booking(series)
    if context["simplified_school"] and booking:
        response = redirect("scheduling:lesson_booking_detail", school_slug=school_slug, public_id=booking.public_id)
        if context["school_schedule_return"]:
            from urllib.parse import urlencode
            response["Location"] += "?" + urlencode({"schedule": context["school_schedule_return"]})
        return response
    context.update(series=series, items=series.occurrences.select_related("replacement__series", "replaces__series", "cancellation", "issued_credit", "redeemed_credit"), now=timezone.now(), booking=booking)
    with timezone.override(ZoneInfo(series.snapshot["timezone"])):
        return render(request, "scheduling/lesson_series.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def cancel(request, school_slug, pk):
    profile, role = lesson_access(request.user, school_slug, "sessions.edit")
    item = get_object_or_404(LessonOccurrence.objects.select_related("series__profile__school", "redeemed_credit"), pk=pk, series__profile=profile)
    form = CancelLessonForm(request.POST if request.method == "POST" else None,
        occurrence=item, initial={"origin": "family", "scope": "one"})
    context = _context(request, profile, role, "lessons")
    if request.method == "POST" and form.is_valid():
        try:
            data = form.cleaned_data
            if request.POST.get("action") == "confirm":
                series = cancel_lessons(request.user, school_slug, pk, data["scope"], data["reason"],
                                        data["origin"], request.POST.get("preview_token", ""))
                messages.success(request, "Booking canceled. Billing is unchanged." if context["simplified_school"]
                    else "Lessons canceled. Make-up outcomes are recorded; billing is unchanged.")
                return redirect("scheduling:lesson_series", school_slug=school_slug, public_id=series.public_id)
            rows, revision = cancellation_plan(item, data["scope"], data["origin"])
            context.update(rows=rows, token=cancellation_token(request.user, item, data, revision))
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
    from .appointments import source_booking
    context.update(form=form, title="Cancel booking" if context["simplified_school"] else "Cancel lessons",
        submit_label="Cancel booking", occurrence=item,
        booking=source_booking(item.series) if context["simplified_school"] else None,
        show_credit_outcomes=not form.fields["origin"].widget.is_hidden)
    with timezone.override(ZoneInfo(item.series.snapshot["timezone"])):
        return render(request, "scheduling/lesson_cancel.html", context, status=400 if form.errors else 200)
