from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django_ratelimit.decorators import ratelimit

from .access import require_location_access
from .camps import (camp_dates, camp_form, check_registration, places_left, public_camp,
                    register_camp, registration_token, save_camp, scoped_camps)
from .checkout import payment_token
from .models import CampRegistration
from .request_forms import participant_forms
from .requests import browser_key, catalog_for
from .setup import records
from .views import _context


@never_cache
@login_required
@require_GET
def camp_list(request, school_slug):
    profile, role = require_location_access(request.user, school_slug)
    context = _context(request, profile, role, "camps")
    form = context["location_filter"]
    context.update(camps=Paginator(form.filter(scoped_camps(profile), "offering__location").prefetch_related("sessions"), 50).get_page(request.GET.get("page")),
                   offerings=form.filter(records(profile, "workshops"), "location"))
    return render(request, "scheduling/camps.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def camp_edit(request, school_slug, offering_pk, pk=None):
    profile, role = require_location_access(request.user, school_slug, "setup.edit")
    if request.method == "POST":
        form = save_camp(request.user, school_slug, offering_pk, request.POST, pk)
        if form.is_valid():
            messages.success(request, "Camp saved.")
            return redirect("scheduling:camp_detail", school_slug=school_slug, pk=form.instance.pk)
    else:
        form = camp_form(profile, offering_pk, pk)
    context = _context(request, profile, role, "camps")
    context.update(form=form, offering=form.instance.offering)
    return render(request, "scheduling/camp_form.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_GET
def camp_detail(request, school_slug, pk):
    profile, role = require_location_access(request.user, school_slug)
    camp = get_object_or_404(scoped_camps(profile), pk=pk)
    sessions = camp_dates(camp)
    items = camp.registrations.select_related("reservation__order")
    context = _context(request, profile, role, "camps")
    context.update(camp=camp, dates=sessions, remaining=places_left(sessions),
                   items=Paginator(items, 50).get_page(request.GET.get("page")))
    try:
        check_registration(camp, sessions, catalog_for(camp.offering))
    except ValidationError as exc:
        context["registration_error"] = " ".join(exc.messages)
    return render(request, "scheduling/camp_detail.html", context)


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def camp_register(request, school_slug, pk):
    camp = public_camp(school_slug, pk)
    dates, catalog = camp_dates(camp), catalog_for(camp.offering)
    browser = browser_key(request, create=request.method == "GET")
    capacity = min((s.capacity for s in dates), default=1)
    error = ""
    if request.method == "POST":
        try:
            result, purchaser, participants = register_camp(school_slug, pk, request.POST, browser)
        except ValidationError as exc:
            error = " ".join(exc.messages)
            purchaser, participants = participant_forms(capacity, catalog, request.POST)
        else:
            if result:
                return redirect("scheduling:camp_received", school_slug=school_slug, public_id=result.public_id)
    else:
        try:
            check_registration(camp, dates, catalog)
        except ValidationError:
            raise Http404("Camp registration is closed.")
        purchaser, participants = participant_forms(capacity, catalog)
    context = {"camp": camp, "dates": dates, "catalog": catalog, "remaining": places_left(dates),
               "business": camp.offering.location.profile.school, "purchaser": purchaser,
               "participants": participants, "error": error,
               "request_token": request.POST.get("request_token", "") if request.method == "POST" else registration_token(camp, dates, catalog, browser)}
    return render(request, "scheduling/camp_register.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@require_GET
def camp_received(request, school_slug, public_id):
    browser = browser_key(request)
    if not browser:
        raise Http404("Page not found")
    registration = get_object_or_404(CampRegistration.objects.select_related("reservation__order", "camp__offering__location__profile__school"),
        public_id=public_id, browser_digest=browser, camp__offering__location__profile__school__slug=school_slug,
        camp__offering__location__profile__enabled=True)
    item = registration.reservation
    context = {"item": item, "registration": registration, "business": registration.camp.offering.location.profile.school}
    if item.order.account_id:
        context["payment_url"] = reverse("scheduling:customer_payment", kwargs={"school_slug": school_slug, "token": payment_token(item)})
    return render(request, "scheduling/camp_received.html", context)
