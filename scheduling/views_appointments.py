import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.debug import sensitive_post_parameters
from django_ratelimit.decorators import ratelimit

from core.payments import service as payments
from . import appointments
from .appointment_forms import AvailabilityForm, OfferForm, TrialContactForm, OfflineLessonPaymentForm
from .appointment_models import LessonBookingOffer, LessonBooking
from .capabilities import has_capability
from .family_access import authorized_students
from .lessons import lesson_access
from .public_trial_availability import TrialAvailability
from .school_navigation import navigation_policy, workflow_policy
from .views import _context
from .views_family import private_page


def _public_trial_context(profile, family=False):
    policy = navigation_policy(profile.school) if not family else {}
    simplified = policy.get("layout") == "school" and policy.get("simplified", False)
    return {"simplified_trial": simplified,
        "booking_base": "scheduling/appointments/public_base.html" if simplified else "scheduling/family_base.html",
        "trial_classes_enabled": simplified and any(key in policy.get("features", []) and has_capability(profile, key)
            for key in ("workshops", "camps"))}


@private_page
@login_required
@require_GET
def setup(request, school_slug):
    profile, role = lesson_access(request.user, school_slug, "setup.edit")
    context = _context(request, profile, role, "lessons")
    context.update(offers=LessonBookingOffer.objects.filter(profile=profile).select_related("service", "teacher", "location", "room"),
        preview_enabled=appointments.preview_enabled())
    return render(request, "scheduling/booking_offers.html", context)


@private_page
@login_required
@require_http_methods(["GET", "POST"])
def edit_offer(request, school_slug, pk=None):
    profile, role = lesson_access(request.user, school_slug, "setup.edit")
    instance = get_object_or_404(LessonBookingOffer, profile=profile, pk=pk) if pk else None
    optional = workflow_policy(profile.school)["optional_features"]
    hidden_fields = [field for field, feature in (("family_booking", "family_booking"), ("makeup_eligible", "makeup_credits"))
        if feature not in optional]
    if request.method == "POST":
        data = request.POST.copy()
        for field in hidden_fields:
            data[field] = ""
        form = appointments.save_offer(request.user, school_slug, data, pk)
        if form.is_valid():
            messages.success(request, "Booking offer saved.")
            return redirect("scheduling:lesson_booking_setup", school_slug=school_slug)
    else:
        form = OfferForm(instance=instance, profile=profile)
    for field in hidden_fields:
        form.fields.pop(field, None)
    context = _context(request, profile, role, "lessons")
    context.update(form=form, title="Edit booking offer" if pk else "Add booking offer", submit_label="Save offer")
    return render(request, "scheduling/lesson_form.html", context, status=400 if form.errors else 200)


@private_page
@require_GET
@ratelimit(key="ip", rate="60/m", block=True)
def catalogue(request, school_slug, family=False, staff=False):
    staff_context = _staff_trial_context(request, school_slug) if staff else {}
    profile, grant = appointments.booking_scope(school_slug, request.session, family)
    return render(request, "scheduling/booking_catalogue.html", {"business": profile.school, "grant": grant, "family": family,
        "offers": appointments.offers(profile, family), **_public_trial_context(profile, family), **staff_context})


def _staff_trial_context(request, school_slug):
    profile, role = lesson_access(request.user, school_slug, "sessions.edit")
    context = _context(request, profile, role, "lessons")
    context.update(staff_trial=True, booking_base="scheduling/appointments/admin_base.html")
    return context


@login_required
def staff_catalogue(request, school_slug):
    return catalogue(request, school_slug, staff=True)


@login_required
def staff_select_slot(request, school_slug, public_id):
    return select_slot(request, school_slug, public_id, staff=True)


@private_page
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("contact_name", "contact_email", "student_name", "slot")
@ratelimit(key="ip", rate="60/m", block=True)
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def select_slot(request, school_slug, public_id, family=False, staff=False):
    staff_context = _staff_trial_context(request, school_slug) if staff else {}
    profile, grant = appointments.booking_scope(school_slug, request.session, family)
    offer = appointments.get_offer(profile, public_id, family)
    students = authorized_students(grant) if grant else None
    public_context = _public_trial_context(profile, family)
    availability = TrialAvailability(offer) if public_context["simplified_trial"] else None
    available_dates = availability.available_dates() if availability else []
    first_day = (timezone.now() + timedelta(hours=offer.notice_hours)).astimezone(ZoneInfo(offer.location.timezone_name)).date()
    if availability:
        first_day = available_dates[0] if available_dates else min(availability.first_day, availability.last_day)
    initial = {"date": first_day, "student": students.first().pk if students is not None else None}
    selection_data = request.GET or initial
    if availability:
        selection_data = {"date": request.GET.get("date", request.POST.get("date", first_day))}
    selection = AvailabilityForm(selection_data, students=students)
    if availability:
        selection.fields["date"].widget.attrs.update(min=timezone.localdate(timezone=availability.zone).isoformat(),
            max=availability.last_day.isoformat())
    contact = TrialContactForm(request.POST if request.method == "POST" else None) if not family else None
    if staff and contact:
        contact.fields["contact_name"].label = "Contact name"
        contact.fields["contact_email"].label = "Contact email"
        contact.fields["authority"].label = "I have permission to make this booking."
    error = ""
    if request.method == "POST" and (family or contact.is_valid()):
        try:
            booking = appointments.confirm_booking(school_slug, request.session, request.POST.get("slot", ""), request.POST, family, public_id,
                actor=request.user if staff else None)
        except ValidationError as exc:
            error = "; ".join(exc.messages)
        else:
            return redirect("scheduling:lesson_booking_detail" if staff else "scheduling:lesson_booking_receipt",
                school_slug=school_slug, public_id=booking.public_id)
    slots = []
    if selection.is_valid():
        try:
            student = selection.cleaned_data.get("student")
            rows = availability.slots(selection.cleaned_data["date"]) if availability else appointments.day_slots(offer, selection.cleaned_data["date"], student)
            browser = appointments.browser_secret(request.session)
            slots = [{**row, "token": appointments.quote(offer, row, student, grant, browser)} for row in rows]
        except ValidationError as exc:
            selection.add_error(None, "; ".join(exc.messages))
    context = {"business": profile.school, "grant": grant, "family": family, "offer": offer, "selection": selection,
        "form": contact, "slots": slots, "error": error, "available_dates": available_dates,
        "availability_end": availability.last_day if availability else None, **public_context, **staff_context}
    return render(request, "scheduling/booking_select.html", context,
        status=400 if error or selection.errors or (contact and contact.errors) else 200)


@private_page
@require_GET
def receipt(request, school_slug, public_id):
    booking = appointments.customer_booking(school_slug, request.session, public_id)
    series = appointments.current_series(booking.series)
    return render(request, "scheduling/booking_receipt.html", {"business": booking.offer.profile.school,
        "booking": booking, "series": series, "item": series.occurrences.first(),
        **_public_trial_context(booking.offer.profile, bool(booking.grant_id))})


@private_page
@login_required
@require_GET
def booking_list(request, school_slug):
    profile, role = lesson_access(request.user, school_slug)
    context = _context(request, profile, role, "lessons")
    context["bookings"] = Paginator(LessonBooking.objects.filter(series__profile=profile).select_related("series", "offer", "order").order_by("-created_at"), 25).get_page(request.GET.get("page"))
    return render(request, "scheduling/lesson_bookings.html", context)


@private_page
@login_required
@require_http_methods(["GET", "POST"])
def booking_detail(request, school_slug, public_id):
    profile, role = lesson_access(request.user, school_slug, "sessions.edit" if request.method == "POST" else "workspace.view")
    operator_tools_enabled = "operator_tools" in workflow_policy(profile.school)["optional_features"]
    if request.method == "POST" and not operator_tools_enabled:
        raise Http404
    booking = get_object_or_404(LessonBooking.objects.select_related("order", "series", "offer"), series__profile=profile, public_id=public_id)
    series = appointments.current_series(booking.series)
    item = series.occurrences.select_related("cancellation", "issued_credit__used_occurrence", "redeemed_credit").first()
    form = OfflineLessonPaymentForm(request.POST if request.method == "POST" else None, initial={"key": uuid.uuid4(), "kind": "payment"})
    if item.state == "canceled":
        form.fields["kind"].choices = [("refund", "Refund issued")]
        form.fields["kind"].initial = "refund"
    if request.method == "POST" and form.is_valid():
        try:
            appointments.record_payment(request.user, school_slug, public_id, request.POST)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, "Test ledger updated. No money was moved.")
            return redirect("scheduling:lesson_booking_detail", school_slug=school_slug, public_id=public_id)
    context = _context(request, profile, role, "lessons")
    from .enrollment_links import detail_context
    context.update(booking=booking, series=series, item=item, items=[item], now=timezone.now(), form=form, operator_tools_enabled=operator_tools_enabled,
        enrollment_link=detail_context(request, booking),
        amounts=payments.totals(booking.order) if booking.order_id else None,
        notices=booking.notices.select_related("delivery"), entries=booking.order.entries.all() if booking.order_id else [])
    return render(request, "scheduling/lesson_booking_detail.html", context, status=400 if form.errors else 200)
