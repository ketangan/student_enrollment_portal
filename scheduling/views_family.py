from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from .credits import credit_query
from .family_access import (InviteForm, RevokeForm, authorized_students, close_family_access,
    family_grant, issue_invitation, public_profile, redeem_invitation, revoke_access)
from .family_models import FamilyScheduleGrant
from .lesson_models import LessonHousehold, LessonOccurrence
from .lessons import lesson_access
from .views import _context


def private_page(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except Http404:
            response = render(request, "scheduling/family_unavailable.html", status=404)
        # Same-origin POSTs need Origin/Referer for Django CSRF. Secrets only travel
        # in a stripped URL fragment and a protected POST, never in a request URL.
        response["Referrer-Policy"] = "same-origin"
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response["Content-Security-Policy"] = "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        return response
    return wrapped


@private_page
@login_required
@require_GET
def access_list(request, school_slug):
    profile, role = lesson_access(request.user, school_slug, "setup.edit")
    context = _context(request, profile, role, "lessons")
    context.update(households=LessonHousehold.objects.filter(profile=profile),
        grants=Paginator(FamilyScheduleGrant.objects.filter(household__profile=profile)
            .select_related("household").prefetch_related("students"), 25).get_page(request.GET.get("page")))
    return render(request, "scheduling/family_access_list.html", context)


@private_page
@login_required
@require_http_methods(["GET", "POST"])
@sensitive_variables()
def invite(request, school_slug, pk):
    profile, role = lesson_access(request.user, school_slug, "setup.edit")
    household = get_object_or_404(LessonHousehold, profile=profile, pk=pk)
    form = InviteForm(request.POST if request.method == "POST" else None, user=request.user, household=household,
        initial={"recipient_name": household.name, "recipient_email": household.email})
    context = _context(request, profile, role, "lessons")
    if request.method == "POST" and form.is_valid():
        try:
            grant, secret = issue_invitation(request.user, school_slug, pk, request.POST)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            path = reverse("scheduling:family_entry", kwargs={"school_slug": school_slug})
            context.update(grant=grant, invite_url=request.build_absolute_uri(path) + "#" + secret if secret else "")
            return render(request, "scheduling/family_invited.html", context)
    context.update(form=form, household=household)
    return render(request, "scheduling/family_invite.html", context, status=400 if form.errors else 200)


@private_page
@login_required
@require_http_methods(["GET", "POST"])
def revoke(request, school_slug, public_id):
    profile, role = lesson_access(request.user, school_slug, "setup.edit")
    grant = get_object_or_404(FamilyScheduleGrant, household__profile=profile, public_id=public_id)
    form = RevokeForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        revoke_access(request.user, school_slug, public_id, request.POST)
        messages.success(request, "Family access revoked.")
        return redirect("scheduling:family_access", school_slug=school_slug)
    context = _context(request, profile, role, "lessons")
    context.update(grant=grant, form=form)
    return render(request, "scheduling/family_revoke.html", context, status=400 if form.errors else 200)


@private_page
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("access_token")
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def entry(request, school_slug):
    try:
        profile = public_profile(school_slug)
    except Http404:
        return render(request, "scheduling/family_unavailable.html", status=404)
    error = ""
    if request.method == "POST":
        try:
            redeem_invitation(school_slug, request.POST.get("access_token", ""), request.session)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            return redirect("scheduling:family_schedule", school_slug=school_slug)
    return render(request, "scheduling/family_entry.html", {"business": profile.school, "error": error}, status=400 if error else 200)


@private_page
@require_GET
def schedule(request, school_slug):
    profile, grant = family_grant(school_slug, request.session)
    return render_schedule(request, profile, grant)


@private_page
@login_required
@require_GET
def preview(request, school_slug, public_id):
    profile, _ = lesson_access(request.user, school_slug, "setup.edit")
    grant = get_object_or_404(FamilyScheduleGrant, household__profile=profile, public_id=public_id)
    return render_schedule(request, profile, grant, preview=True)


def render_schedule(request, profile, grant, preview=False):
    from .appointments import preview_enabled
    students = authorized_students(grant)
    selected = request.GET.get("student", "")
    visible = students
    if selected:
        student = get_object_or_404(students, pk=selected if selected.isdigit() and len(selected) < 19 else 0)
        visible = students.filter(pk=student.pk)
    past = request.GET.get("period") == "past"
    lessons = LessonOccurrence.objects.filter(series__profile=profile, series__student__in=visible).exclude(state="rescheduled")
    lessons = lessons.filter(ends_at__lte=timezone.now()).order_by("-starts_at", "-pk") if past else lessons.filter(ends_at__gt=timezone.now())
    credits = credit_query(profile).filter(source__series__student__in=visible)
    context = {"business": profile.school, "grant": grant, "students": students, "selected": selected, "preview": preview,
        "booking_preview_enabled": preview_enabled(),
        "period": "past" if past else "upcoming", "now": timezone.now(),
        "items": Paginator(lessons.select_related("series"), 25).get_page(request.GET.get("page")),
        "credits": Paginator(credits, 10).get_page(request.GET.get("credits_page"))}
    return render(request, "scheduling/family_schedule.html", context)


@private_page
@require_POST
def sign_out(request, school_slug):
    close_family_access(school_slug, request.session)
    return render(request, "scheduling/family_unavailable.html", {"signed_out": True})
