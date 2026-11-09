from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import admissions, enrollment_links
from .views import _context
from .views_family import private_page


@private_page
@login_required
@require_http_methods(["GET", "POST"])
def link(request, school_slug, public_id):
    profile, role = enrollment_links.access(request, school_slug)
    booking = enrollment_links.booking_for(profile, public_id)
    form = enrollment_links.LinkForm()
    if request.method == "POST":
        booking, form = enrollment_links.link_existing(request, school_slug, public_id, request.POST)
        if form.is_valid():
            messages.success(request, "Application linked. No family records or payments were changed.")
            return redirect(enrollment_links.submission_url(booking.enrollment_submission))
    context = _context(request, profile, role, "lessons")
    context.update(booking=booking, form=form,
        application_url=enrollment_links.submission_url(booking.enrollment_submission)
            if booking.enrollment_submission_id else None,
        can_create=admissions.direct_enrollment(profile.school)
            or admissions.policy_for(profile.school_id) is None)
    return render(request, "scheduling/enrollment_link.html", context,
        status=400 if form.errors else 200)


@private_page
@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def create_application(request, school_slug, public_id):
    from core.views_school_submissions import school_submission_create_view

    profile, _ = enrollment_links.access(request, school_slug, locked=True)
    booking = enrollment_links.booking_for(profile, public_id)
    if booking.enrollment_submission_id:
        return redirect(enrollment_links.submission_url(booking.enrollment_submission))
    if not admissions.direct_enrollment(profile.school) and admissions.policy_for(profile.school_id):
        raise Http404
    request.lesson_enrollment_booking = booking
    request.lesson_enrollment_profile = profile
    return school_submission_create_view(request, school_slug)
