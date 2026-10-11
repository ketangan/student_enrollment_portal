import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from django.views.decorators.debug import sensitive_post_parameters
from django_ratelimit.decorators import ratelimit

from core.payments import service as payments
from . import appointments
from .appointment_forms import AvailabilityForm, OfferForm, TrialContactForm, OfflineLessonPaymentForm
from .appointment_models import LessonBookingOffer, LessonBooking
from .family_access import authorized_students
from .lessons import lesson_access
from .views import _context
from .views_family import private_page


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
    if request.method == "POST":
        form = appointments.save_offer(request.user, school_slug, request.POST, pk)
        if form.is_valid():
            messages.success(request, "Booking offer saved.")
            return redirect("scheduling:lesson_booking_setup", school_slug=school_slug)
    else:
        form = OfferForm(instance=instance, profile=profile)
    context = _context(request, profile, role, "lessons")
    context.update(form=form, title="Edit booking offer" if pk else "Add booking offer", submit_label="Save offer")
    return render(request, "scheduling/lesson_form.html", context, status=400 if form.errors else 200)


@private_page
@require_GET
@ratelimit(key="ip", rate="60/m", block=True)
def catalogue(request, school_slug, family=False):
    profile, grant = appointments.booking_scope(school_slug, request.session, family)
    return render(request, "scheduling/booking_catalogue.html", {"business": profile.school, "grant": grant, "family": family,
        "offers": appointments.offers(profile, family)})


@private_page
@require_http_methods(["GET", "POST"])
@sensitive_post_parameters("contact_name", "contact_email", "student_name", "slot")
@ratelimit(key="ip", rate="60/m", block=True)
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def select_slot(request, school_slug, public_id, family=False):
    profile, grant = appointments.booking_scope(school_slug, request.session, family)
    offer = appointments.get_offer(profile, public_id, family)
    students = authorized_students(grant) if grant else None
    first_day = (timezone.now() + timedelta(hours=offer.notice_hours)).astimezone(ZoneInfo(offer.location.timezone_name)).date()
    initial = {"date": first_day, "student": students.first().pk if students is not None else None}
    selection = AvailabilityForm(request.GET or initial, students=students)
    contact = TrialContactForm(request.POST if request.method == "POST" else None) if not family else None
    error = ""
    if request.method == "POST" and (family or contact.is_valid()):
        try:
            booking = appointments.confirm_booking(school_slug, request.session, request.POST.get("slot", ""), request.POST, family, public_id)
        except ValidationError as exc:
            error = "; ".join(exc.messages)
        else:
            return redirect("scheduling:lesson_booking_receipt", school_slug=school_slug, public_id=booking.public_id)
    slots = []
    if selection.is_valid():
        try:
            student = selection.cleaned_data.get("student")
            rows = appointments.day_slots(offer, selection.cleaned_data["date"], student)
            browser = appointments.browser_secret(request.session)
            slots = [{**row, "token": appointments.quote(offer, row, student, grant, browser)} for row in rows]
        except ValidationError as exc:
            selection.add_error(None, "; ".join(exc.messages))
    context = {"business": profile.school, "grant": grant, "family": family, "offer": offer, "selection": selection,
        "form": contact, "slots": slots, "error": error}
    return render(request, "scheduling/booking_select.html", context,
        status=400 if error or selection.errors or (contact and contact.errors) else 200)


@private_page
@require_GET
def receipt(request, school_slug, public_id):
    booking = appointments.customer_booking(school_slug, request.session, public_id)
    series = appointments.current_series(booking.series)
    return render(request, "scheduling/booking_receipt.html", {"business": booking.offer.profile.school,
        "booking": booking, "series": series, "item": series.occurrences.first()})


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
    booking = get_object_or_404(LessonBooking.objects.select_related("order", "series", "offer"), series__profile=profile, public_id=public_id)
    series = appointments.current_series(booking.series)
    item = series.occurrences.first()
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
    context.update(booking=booking, series=series, item=item, form=form,
        amounts=payments.totals(booking.order) if booking.order_id else None,
        notices=booking.notices.select_related("delivery"), entries=booking.order.entries.all() if booking.order_id else [])
    return render(request, "scheduling/lesson_booking_detail.html", context, status=400 if form.errors else 200)
