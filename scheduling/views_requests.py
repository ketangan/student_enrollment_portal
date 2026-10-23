from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django_ratelimit.decorators import ratelimit

from .access import require_location_access
from .models import Reservation, WorkshopRequest
from .request_forms import request_forms
from .requests import (accepts_requests, browser_key, catalog_for, choice_form, preparation_totals,
                       request_token, save_choice, scoped_requests, session_for_request, submit_request, withdraw_request)
from .setup import records
from .views import _context


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def workshop_request(request, school_slug, pk):
    session = session_for_request(school_slug, pk)
    catalog = catalog_for(session.offering)
    browser = browser_key(request, create=request.method == "GET")
    error = None
    if request.method == "POST":
        try:
            result, purchaser, participants = submit_request(school_slug, pk, request.POST, browser)
        except ValidationError as exc:
            error = " ".join(exc.messages)
            purchaser, participants = request_forms(session, catalog, request.POST)
        else:
            if result:
                return redirect("scheduling:request_received", school_slug=school_slug, public_id=result.public_id)
    else:
        if not accepts_requests(session, catalog):
            raise Http404("This session is not accepting requests.")
        purchaser, participants = request_forms(session, catalog)
    context = {"business": session.offering.location.profile.school, "session": session, "catalog": catalog,
               "purchaser": purchaser, "participants": participants, "error": error,
               "request_token": request.POST.get("request_token", "") if request.method == "POST" else request_token(session, catalog, browser)}
    return render(request, "scheduling/workshop_request.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@require_GET
def request_received(request, school_slug, public_id):
    browser = browser_key(request)
    if not browser:
        raise Http404("Page not found")
    item = get_object_or_404(WorkshopRequest.objects.select_related("session__offering__location__profile__school"),
        public_id=public_id, browser_digest=browser, session__offering__location__profile__school__slug=school_slug,
        session__offering__location__profile__enabled=True)
    return render(request, "scheduling/request_received.html", {"business": item.session.offering.location.profile.school, "item": item})


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def choice_edit(request, school_slug, offering_pk, group_pk=None, option_pk=None, option=False):
    profile, role = require_location_access(request.user, school_slug, "setup.edit")
    arguments = {"group_pk": group_pk, "option_pk": option_pk, "option": option}
    if request.method == "POST":
        offering, form = save_choice(request.user, school_slug, offering_pk, request.POST, **arguments)
        if form.is_valid():
            messages.success(request, "Choice saved.")
            return redirect("scheduling:workshop_detail", school_slug=school_slug, pk=offering.pk)
    else:
        offering, form = choice_form(profile, offering_pk, **arguments)
    context = _context(request, profile, role, "workshops")
    context.update(offering=offering, form=form, option=option)
    return render(request, "scheduling/choice_form.html", context)


@never_cache
@login_required
@require_GET
def request_list(request, school_slug):
    profile, role = require_location_access(request.user, school_slug)
    context = _context(request, profile, role, "requests")
    form = context["location_filter"]
    context.update(location_filter=form, selected_location=form.selected)
    items = form.filter(scoped_requests(profile), "session__offering__location")
    context["review_only"] = request.GET.get("review") == "1"
    if context["review_only"]:
        items = items.filter(status="pending", reservation__isnull=True)
    context["items"] = Paginator(items, 50).get_page(request.GET.get("page"))
    return render(request, "scheduling/request_list.html", context, status=200 if form.is_valid() else 400)


@never_cache
@login_required
@require_GET
def request_detail(request, school_slug, public_id):
    profile, role = require_location_access(request.user, school_slug)
    context = _context(request, profile, role, "requests")
    context["item"] = get_object_or_404(scoped_requests(profile), public_id=public_id)
    return render(request, "scheduling/request_detail.html", context)


@never_cache
@login_required
@require_POST
def request_withdraw(request, school_slug, public_id):
    require_location_access(request.user, school_slug, "sessions.edit")
    if request.POST.get("confirm") != "yes":
        return HttpResponseBadRequest("Confirm withdrawal first.")
    try:
        withdraw_request(request.user, school_slug, public_id)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Request withdrawn. Removed from pending preparation totals.")
    return redirect("scheduling:request_detail", school_slug=school_slug, public_id=public_id)


@never_cache
@login_required
@require_GET
def preparation(request, school_slug, pk):
    profile, role = require_location_access(request.user, school_slug)
    session = get_object_or_404(records(profile, "sessions"), pk=pk)
    items = list(scoped_requests(profile).filter(session=session, status=WorkshopRequest.Status.PENDING).exclude(reservation__state__in=["canceled", "completed"]).order_by("created_at", "pk"))
    context = _context(request, profile, role, "requests")
    total, totals = preparation_totals(items)
    context.update(session=session, items=items, participant_total=total, totals=totals,
                   has_reservations=Reservation.objects.filter(session=session).exists())
    camp_reservations = list(Reservation.objects.filter(occurrences__session=session, state="confirmed").select_related("camp_registration"))
    context["camp_reservations"] = camp_reservations
    context["camp_participant_total"] = sum(item.quantity for item in camp_reservations)
    from .camps import preparation_totals as camp_preparation_totals
    context["camp_totals"] = camp_preparation_totals(camp_reservations)
    return render(request, "scheduling/preparation.html", context)
