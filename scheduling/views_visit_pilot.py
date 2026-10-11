from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_http_methods
from django_ratelimit.decorators import ratelimit

from . import visits, visit_access, visit_operations, visit_schedule, messaging
from .editing import protect_edit
from .lesson_forms import ClosureForm
from .lesson_models import LessonClosure, LessonOccurrence
from .lessons import local_datetime
from .models import Location, Room, SchedulingProfile, VisitPilotSettings
from .setup import _audit
from .views import _context
from .views_family import private_page
from .visit_resources import apply_visit_closure


class PilotForm(forms.ModelForm):
    class Meta:
        model = VisitPilotSettings
        fields = ["contact_email", "family_changes", "notice_hours"]
        labels = {"contact_email": "School contact email", "family_changes": "Families can cancel or change their visit",
            "notice_hours": "Changes close this many hours before the visit"}


class RecoveryForm(forms.Form):
    email = forms.EmailField(max_length=254, label="Booking email")


@private_page
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="5/m", method="POST", block=True)
def recover(request, school_slug):
    profile = visits.public_profile(school_slug)
    get_object_or_404(VisitPilotSettings, profile=profile)
    form = RecoveryForm(request.POST if request.method == "POST" else None)
    done = request.method == "POST" and form.is_valid()
    if done:
        visit_access.request_access(school_slug, form.cleaned_data["email"])
    return render(request, "scheduling/visits/recover.html", {"business": profile.school, "form": form, "done": done},
        status=400 if request.method == "POST" and not done else 200)


@private_page
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("access_token")
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def entry(request, school_slug):
    profile = visits.public_profile(school_slug)
    error = ""
    if request.method == "POST":
        try:
            item = visit_access.redeem(school_slug, request.POST.get("access_token", ""), request.session)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            return redirect("scheduling:visit_receipt", school_slug=school_slug, public_id=item.public_id)
    return render(request, "scheduling/visits/entry.html", {"business": profile.school, "error": error}, status=400 if error else 200)


@private_page
@require_POST
def family_change(request, school_slug, public_id):
    profile = visits.public_profile(school_slug)
    item = visit_access.authorized(request, profile, public_id)
    if request.POST.get("action") == "signout":
        visit_access.revoke(profile, public_id, request.session)
        return redirect("scheduling:visit_recover", school_slug=school_slug)
    try:
        visit_operations.change(profile, item.public_id, version=request.POST.get("version"), action=request.POST.get("action"),
            target=request.POST.get("target"), family=True)
    except ValidationError as exc:
        from .views_visits import receipt_context
        return render(request, "scheduling/visits/receipt.html", {**receipt_context(request, profile, item), "error": " ".join(exc.messages)}, status=400)
    return redirect("scheduling:visit_receipt", school_slug=school_slug, public_id=public_id)


@private_page
@login_required
@require_POST
def staff_change(request, school_slug, public_id):
    profile, _ = visits.access(request.user, school_slug, "sessions.edit")
    try:
        if request.POST.get("action") == "revoke":
            visit_access.revoke(profile, public_id, user=request.user)
        else:
            visit_operations.change(profile, public_id, version=request.POST.get("version"), action=request.POST.get("action"),
                target=request.POST.get("target"), reason=request.POST.get("reason", "")[:500], user=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Visit updated.")
    return redirect("scheduling:visit_detail", school_slug=school_slug, public_id=public_id)


@private_page
@login_required
@require_http_methods(["GET", "POST"])
def setup(request, school_slug):
    messaging.require_preview()
    profile, role = visits.access(request.user, school_slug, "setup.edit")
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        profile, role = visits.access(request.user, school_slug, "setup.edit")
        instance = VisitPilotSettings.objects.filter(profile=profile).first() or VisitPilotSettings(profile=profile)
        before = model_to_dict(instance)
        form = protect_edit(PilotForm(request.POST if request.method == "POST" else None, instance=instance), profile)
        if request.method == "POST" and form.is_valid():
            form.save()
            _audit(request.user, profile, instance, before, "change")
            return redirect("scheduling:visit_setup", school_slug=school_slug)
    context = _context(request, profile, role, "visits")
    context.update(form=form, pilot_configured=bool(instance.pk),
        has_location=Room.objects.filter(location__profile=profile).exists(), has_programs=visits.programs(profile).exists(),
        has_slots=visits.slots(profile).filter(session__is_published=True, program__offering__is_published=True,
            host__is_active=True, host__user__is_active=True, host__role__in=["owner", "scheduler"], session__starts_at__gt=timezone.now()).exists(),
        closures=LessonClosure.objects.filter(location__profile=profile, teacher=None).select_related("location").order_by("-first_date")[:25])
    return render(request, "scheduling/visits/setup.html", context, status=400 if form.errors else 200)


@private_page
@login_required
@require_http_methods(["GET", "POST"])
def repeat(request, school_slug, program_pk):
    profile, role = visits.access(request.user, school_slug, "sessions.edit")
    program = get_object_or_404(visits.programs(profile), pk=program_pk)
    form, rows, token = visit_schedule.ScheduleForm(program=program), [], ""
    if request.method == "POST":
        try:
            form, rows, token, done = visit_schedule.schedule(request.user, school_slug, program_pk, request.POST,
                request.POST.get("preview_token") if request.POST.get("action") == "publish" else None)
            if done:
                messages.success(request, "Visit times published.")
                return redirect("scheduling:visits", school_slug=school_slug)
        except ValidationError as exc:
            form = visit_schedule.ScheduleForm(request.POST, program=program)
            form.is_valid()
            form.add_error(None, " ".join(exc.messages))
    context = _context(request, profile, role, "visits")
    context.update(form=form, rows=rows, preview_token=token, program=program)
    return render(request, "scheduling/visits/repeat.html", context, status=400 if form.errors else 200)


@private_page
@login_required
@require_http_methods(["GET", "POST"])
def closure(request, school_slug, pk=None):
    profile, role = visits.access(request.user, school_slug, "setup.edit")
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        visits.access(request.user, school_slug, "setup.edit")
        instance = get_object_or_404(LessonClosure, pk=pk, location__profile=profile, teacher=None) if pk else None
        before = model_to_dict(instance) if instance else {}
        form = ClosureForm(request.POST if request.method == "POST" else None, instance=instance)
        del form.fields["teacher"]
        form.fields["location"].queryset = Location.objects.filter(profile=profile)
        form = protect_edit(form, profile)
        if request.method == "POST" and form.is_valid():
            item = form.instance
            zone = ZoneInfo(item.location.timezone_name)
            try:
                start = local_datetime(item.first_date, datetime.min.time(), zone)
                end = local_datetime(item.last_date+timedelta(days=1), datetime.min.time(), zone)
                if LessonOccurrence.objects.filter(series__location=item.location, state="scheduled", starts_at__lt=end, ends_at__gt=start).exists():
                    raise ValidationError("Move or cancel scheduled lessons before adding this closure.")
                apply_visit_closure(item, start, end)
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                form.save()
                _audit(request.user, profile, item, before, "change" if pk else "add")
                return redirect("scheduling:visit_setup", school_slug=school_slug)
    context = _context(request, profile, role, "visits")
    context.update(form=form, heading="Edit school closure" if pk else "Close a school date")
    return render(request, "scheduling/visits/form.html", context, status=400 if form.errors else 200)
