"""Explicit staff-attested application links; never infer family identity by email."""
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse

from core.models import Submission
from core.services.school_permissions import require_school_role
from core.views_school_common import TrialExpiredError, _get_accessible_school_for_admin
from .appointment_models import LessonBooking
from .lessons import audit, lesson_access
from .school_navigation import workflow_policy


class VerificationForm(forms.Form):
    verified = forms.BooleanField(label="I have verified this application is for the student in this booking.")


class LinkForm(VerificationForm):
    application = forms.CharField(max_length=16, label="Application reference")


def access(request, school_slug, *, locked=False):
    school = _get_accessible_school_for_admin(request, school_slug)
    require_school_role(request, school, "editor")
    profile, role = lesson_access(request.user, school_slug, "sessions.edit", locked=locked)
    if locked:
        # Enrollment access may have been revoked while waiting for the tenant lock.
        school = _get_accessible_school_for_admin(request, school_slug)
        require_school_role(request, school, "editor")
    if not workflow_policy(school)["enrollment_linking"]:
        raise Http404
    return profile, role


def booking_for(profile, public_id):
    return get_object_or_404(LessonBooking.objects.select_related(
        "series__profile__school", "series__student", "enrollment_submission"),
        series__profile=profile, offer__profile=profile, public_id=public_id)


def submission_url(submission):
    return reverse("school_submission_detail", kwargs={
        "school_slug": submission.school.slug, "submission_id": submission.pk})


def attach(request, profile, booking, submission):
    """Caller holds the profile lock; record and audit commit together."""
    if submission.school_id != profile.school_id or booking.series.profile_id != profile.pk:
        raise Http404
    if booking.enrollment_submission_id:
        if booking.enrollment_submission_id != submission.pk:
            raise ValidationError("This booking already has an application. Its link cannot be replaced here.")
        return booking
    booking.enrollment_submission = submission
    booking.save(update_fields=["enrollment_submission"])
    audit(request.user, profile, booking, "change", {
        "enrollment_submission": {"from": None, "to": submission.pk}, "identity_verified": True})
    return booking


@transaction.atomic
def link_existing(request, school_slug, public_id, data):
    profile, _ = access(request, school_slug, locked=True)
    booking = booking_for(profile, public_id)
    form = LinkForm(data)
    if not form.is_valid():
        return booking, form
    submission = Submission.objects.filter(school_id=profile.school_id,
        public_id=form.cleaned_data["application"]).first()
    if submission is None:
        form.add_error("application", "Application not found in this school.")
        return booking, form
    try:
        attach(request, profile, booking, submission)
    except ValidationError as exc:
        form.add_error(None, exc)
    return booking, form


def detail_context(request, booking):
    """Optional include context: scheduling-only users must not see application data."""
    try:
        profile, _ = access(request, booking.series.profile.school.slug)
    except (Http404, TrialExpiredError):
        return None
    if booking.series.profile_id != profile.pk:
        return None
    return {"url": reverse("scheduling:lesson_enrollment_link", kwargs={
        "school_slug": profile.school.slug, "public_id": booking.public_id}),
        "linked": bool(booking.enrollment_submission_id)}


def related_trials(request, submission):
    """Read-only backlinks, restricted to the application's authorized lesson tenant."""
    try:
        school = _get_accessible_school_for_admin(request, submission.school.slug)
        if not workflow_policy(school)["enrollment_linking"]:
            return []
        profile, _ = lesson_access(request.user, school.slug)
    except (Http404, TrialExpiredError):
        return []
    bookings = LessonBooking.objects.filter(
        enrollment_submission=submission, series__profile=profile, offer__profile=profile,
    ).select_related("series").order_by("pk")
    return [{
        "url": reverse("scheduling:lesson_booking_detail", kwargs={
            "school_slug": school.slug, "public_id": booking.public_id}),
        "reference": booking.public_id,
        "student": booking.series.snapshot.get("student", ""),
        "service": booking.series.snapshot.get("service", ""),
    } for booking in bookings]
