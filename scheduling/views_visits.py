from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from . import visits
from .requests import browser_key
from .views import _context
from .visit_forms import BookingForm
from .models import VisitPilotSettings
from .views_family import private_page


@never_cache
@login_required
@require_GET
def index(request, school_slug):
    profile, role = visits.access(request.user, school_slug)
    context = _context(request, profile, role, "visits")
    form = context["location_filter"]
    context.update(location_filter=form, selected_location=form.selected)
    times = list(form.filter(visits.occupancy(visits.slots(profile)), "program__offering__location")
        .filter(session__ends_at__gt=timezone.now())[:50])
    for slot in times:
        slot.booking_status = "Closed" if not visits.available(slot) else ("Full" if slot.places_taken >= slot.session.capacity else "Open")
    context.update(programs=form.filter(visits.programs(profile), "offering__location"), slots=times,
        can_configure_admissions=visits.enrollment_access(request.user, profile, owner=True),
        bookings=Paginator(form.filter(visits.bookings(profile), "slot__program__offering__location"), 25).get_page(request.GET.get("page")))
    return render(request, "scheduling/visits/index.html", context, status=200 if form.is_valid() else 400)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def program_edit(request, school_slug, pk=None):
    profile, role = visits.access(request.user, school_slug, "setup.edit")
    form = visits.save_program(request.user, school_slug, request.POST, pk) if request.method == "POST" else visits.program_form(profile, pk=pk)
    if request.method == "POST" and form.is_valid():
        return redirect("scheduling:visits", school_slug=school_slug)
    context = _context(request, profile, role, "visits")
    context.update(form=form, heading="Edit tour" if pk else "Add tour")
    return render(request, "scheduling/visits/form.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def slot_edit(request, school_slug, program_pk, pk=None):
    profile, role = visits.access(request.user, school_slug, "sessions.edit")
    form = visits.save_slot(request.user, school_slug, program_pk, request.POST, pk) if request.method == "POST" else visits.slot_form(profile, program_pk, pk=pk)
    if request.method == "POST" and form.is_valid():
        return redirect("scheduling:visits", school_slug=school_slug)
    context = _context(request, profile, role, "visits")
    context.update(form=form, heading="Edit tour time" if pk else "Add tour time")
    return render(request, "scheduling/visits/form.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@require_GET
def catalogue(request, school_slug):
    profile = visits.public_profile(school_slug)
    items = []
    for slot in visits.occupancy(visits.slots(profile)).filter(program__kind="tour", session__is_published=True, program__offering__is_published=True,
            session__starts_at__gt=timezone.now(), host__is_active=True, host__user__is_active=True,
            host__role__in=["owner", "scheduler"])[:50]:
        slot.places_left = max(0, slot.session.capacity - slot.places_taken)
        items.append(slot)
    return render(request, "scheduling/visits/catalogue.html", {"business": profile.school, "slots": items,
        "pilot": VisitPilotSettings.objects.filter(profile=profile).first()})


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def book(request, school_slug, pk):
    profile = visits.public_profile(school_slug)
    slot = get_object_or_404(visits.slots(profile), pk=pk, program__kind="tour")
    browser = browser_key(request, create=request.method == "GET")
    form = BookingForm(request.POST if request.method == "POST" else None)
    error = None
    if request.method == "POST":
        try:
            item, form = visits.book(school_slug, pk, request.POST, browser)
            if item:
                return redirect("scheduling:visit_receipt", school_slug=school_slug, public_id=item.public_id)
        except ValidationError as exc:
            error = " ".join(exc.messages)
    elif not visits.available(slot):
        raise Http404("This tour is not open for bookings.")
    context = {"business": profile.school, "slot": slot, "form": form, "error": error, "places_left": visits.places_left(slot),
        "booking_token": request.POST.get("booking_token", "") if request.method == "POST" else visits.booking_token(slot, browser)}
    return render(request, "scheduling/visits/book.html", context, status=400 if request.method == "POST" else 200)


def change_slots(profile, item):
    return visits.occupancy(visits.slots(profile)).filter(program=item.slot.program, session__is_published=True,
        program__offering__is_published=True, session__starts_at__gt=timezone.now(), host__is_active=True,
        host__user__is_active=True, host__role__in=["owner", "scheduler"]).exclude(pk=item.slot_id)[:50]


def receipt_context(request, profile, item):
    from .admissions import lead_context
    from .visit_operations import family_may_change
    return {"business": profile.school, "item": item,
        "admissions": lead_context(item.lead) if item.lead_id else None,
        "pilot": VisitPilotSettings.objects.filter(profile=profile).first(), "can_change": family_may_change(item, profile),
        "alternatives": change_slots(profile, item)}


@private_page
@require_GET
def receipt(request, school_slug, public_id):
    profile = visits.public_profile(school_slug)
    from .visit_access import authorized
    item = authorized(request, profile, public_id)
    return render(request, "scheduling/visits/receipt.html", receipt_context(request, profile, item))


@never_cache
@login_required
@require_GET
def detail(request, school_slug, public_id):
    profile, role = visits.access(request.user, school_slug)
    item = get_object_or_404(visits.bookings(profile), public_id=public_id)
    context = _context(request, profile, role, "visits")
    context.update(item=item, can_view_lead=visits.enrollment_access(request.user, profile),
        attendance_available=item.slot.session.starts_at <= timezone.now(), alternatives=change_slots(profile, item))
    return render(request, "scheduling/visits/detail.html", context)


@never_cache
@login_required
@require_POST
def update(request, school_slug, public_id):
    try:
        item = visits.change_state(request.user, school_slug, public_id, request.POST.get("state"), request.POST.get("previous"), request.POST.get("version"))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"{item.slot.program.get_kind_display()} booking updated.")
    return redirect("scheduling:visit_detail", school_slug=school_slug, public_id=public_id)
