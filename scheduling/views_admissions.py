from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from core.models import Lead
from . import admissions, visits
from .admission_forms import PolicyForm
from .models import AdmissionCase, AdmissionPolicy, SchedulingProfile
from .requests import browser_key
from .setup import _audit
from .views import _context
from .visit_forms import BookingForm


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def policy(request, school_slug):
    profile, role = visits.access(request.user, school_slug, "setup.edit")
    admissions.require_editor(request.user, profile, owner=True)
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        instance = AdmissionPolicy.objects.filter(profile=profile).first()
        form = PolicyForm(request.POST if request.method == "POST" else None, profile=profile, instance=instance)
        before = model_to_dict(instance) if instance else {}
        if request.method == "POST" and form.is_valid():
            result = form.save()
            _audit(request.user, profile, result, before, "change" if instance else "add")
            messages.success(request, "Admissions workflow saved.")
            return redirect("scheduling:visits", school_slug=school_slug)
    context = _context(request, profile, role, "visits")
    context.update(form=form, heading="Admissions workflow")
    return render(request, "scheduling/admissions/settings.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def case_detail(request, school_slug, lead_pk):
    profile, role = visits.access(request.user, school_slug)
    if not visits.enrollment_access(request.user, profile):
        raise Http404
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        policy = get_object_or_404(AdmissionPolicy, profile=profile, enabled=True)
        lead = get_object_or_404(Lead, school=profile.school, pk=lead_pk)
        try:
            case = admissions.case_for_lead(policy, lead, create=True)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
            return redirect("school_lead_detail", school_slug=school_slug, lead_id=lead_pk)
        if request.method == "POST":
            admissions.require_editor(request.user, profile)
            action = request.POST.get("action")
            field = {"release_application": "application_released_at", "release_playdate": "playdate_released_at"}.get(action)
            if not field:
                raise Http404
            if request.POST.get("revision") != (getattr(case, field).isoformat() if getattr(case, field) else ""):
                messages.error(request, "This approval changed. Reload before trying again.")
            elif (field == "application_released_at" and policy.require_tour and (not case.tour_id or case.tour.state != "attended")):
                messages.error(request, "Record tour attendance before releasing this application.")
            elif field == "playdate_released_at" and not case.submission_id:
                messages.error(request, "An application must be submitted before releasing playdate booking.")
            else:
                before = {field: getattr(case, field)}
                setattr(case, field, timezone.now())
                case.save(update_fields=[field])
                _audit(request.user, profile, case, before, "change")
                if case.tour_id:
                    from .visit_access import notice
                    notice(case.tour, "Application available" if field == "application_released_at" else "Playdate booking available")
                messages.success(request, "Approval saved.")
            return redirect("scheduling:admission_case", school_slug=school_slug, lead_pk=lead_pk)
    context = _context(request, profile, role, "visits")
    context.update(case=case, application_reason=admissions.application_reason(case), playdate_reason=admissions.playdate_reason(case),
        booking_admissions=admissions.family_context(case.submission) if case.submission_id else None,
        can_approve=request.user.is_superuser or bool(getattr(request.user, "is_staff", False) and
            admissions.get_school_membership(request.user, profile.school).has_role("editor")))
    return render(request, "scheduling/admissions/case.html", context)


@never_cache
@require_POST
def start_application(request, school_slug, public_id):
    profile = visits.public_profile(school_slug)
    from .visit_access import authorized
    item = authorized(request, profile, public_id)
    if not item.lead_id or item.slot.program.kind != "tour":
        raise Http404
    with transaction.atomic():
        SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
        try:
            policy = get_object_or_404(AdmissionPolicy, profile=profile, enabled=True)
            case = admissions.case_for_lead(policy, item.lead, create=True)
            if case.submission_id:
                return redirect("family_status", school_slug=school_slug, token=case.submission.status_token)
            draft = admissions.prepare_draft(case)
        except ValidationError as exc:
            return render(request, "scheduling/admissions/blocked.html", {"business": profile.school, "reason": " ".join(exc.messages)}, status=403)
    return redirect("apply_resume", school_slug=school_slug, token=draft.token)


def family_case(school_slug, token):
    profile = visits.public_profile(school_slug)
    if not profile.school.features.family_portal_enabled:
        raise Http404
    case = get_object_or_404(AdmissionCase.objects.select_related("policy__profile__school", "lead", "submission"),
        policy__profile=profile, submission__school=profile.school, lead__school=profile.school, submission__status_token=token)
    return profile, case


@never_cache
@require_GET
def playdates(request, school_slug, token):
    profile, case = family_case(school_slug, token)
    reason = admissions.playdate_reason(case)
    slots = []
    if not reason:
        for slot in visits.occupancy(visits.slots(profile)).filter(program__kind="playdate", session__is_published=True,
                program__offering__is_published=True, session__starts_at__gt=timezone.now(),
                host__is_active=True, host__user__is_active=True, host__role__in=["owner", "scheduler"])[:50]:
            slot.places_left = max(0, slot.session.capacity - slot.places_taken)
            slots.append(slot)
    return render(request, "scheduling/admissions/playdates.html", {"business": profile.school, "case": case, "slots": slots, "reason": reason})


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def book_playdate(request, school_slug, token, pk):
    profile, case = family_case(school_slug, token)
    slot = get_object_or_404(visits.slots(profile), pk=pk, program__kind="playdate")
    browser = browser_key(request, create=request.method == "GET")
    form = BookingForm(request.POST if request.method == "POST" else None, identity=admissions.playdate_identity(case))
    error = None
    if request.method == "POST":
        try:
            item, form = visits.book(school_slug, pk, request.POST, browser, case=case)
            if item:
                return redirect("scheduling:visit_receipt", school_slug=school_slug, public_id=item.public_id)
        except ValidationError as exc:
            error = " ".join(exc.messages)
    else:
        error = admissions.playdate_reason(case)
        if error or not visits.available(slot):
            return render(request, "scheduling/admissions/blocked.html", {"business": profile.school,
                "reason": error or "This playdate is no longer available."}, status=403)
    return render(request, "scheduling/visits/book.html", {"business": profile.school, "slot": slot,
        "form": form, "error": error, "places_left": visits.places_left(slot), "case": case,
        "booking_token": request.POST.get("booking_token", "") if request.method == "POST" else visits.booking_token(slot, browser, case=case)},
        status=400 if request.method == "POST" else 200)
