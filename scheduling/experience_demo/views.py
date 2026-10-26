from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django_ratelimit.decorators import ratelimit

from scheduling import requests
from scheduling.access import has_permission, require_access, require_location_access, location_restricted
from scheduling.capabilities import has_capability
from scheduling.models import Session, WorkshopRequest
from scheduling.request_forms import request_forms
from . import payments, service
from .config import context


def page(request, template, profile, config, values=None, status=200):
    staff = {}
    if template in {"preparation", "integration"}:
        from scheduling.views import _context
        profile, role = require_location_access(request.user, profile.school.slug)
        staff = _context(request, profile, role, "integrations" if template == "integration" else "preparation")
    response = render(request, f"scheduling/experience/{template}.html", {
        **staff,
        "profile": profile, "business": profile.school, "demo": config,
        "stripe_checkout": config.get("stripe_checkout", False),
        "payment_provider_name": profile.get_payment_provider_display(),
        "unit_price": service.money(config["price_cents"]), **(values or {}),
    }, status=status)
    response["Referrer-Policy"] = "same-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    if template in {"start", "choose", "review", "booking"}:
        # Only public demo screens may be framed, and only by this same origin.
        response["X-Frame-Options"] = "SAMEORIGIN"
        response["Content-Security-Policy"] = "frame-ancestors 'self'"
    return response


@never_cache
@require_GET
@ratelimit(key="ip", rate="60/m", block=True)
def start(request, school_slug):
    profile, config = context(request, school_slug)
    rows = service.session_rows(profile, online=config.get("stripe_checkout", False)) if profile.native_reservations_enabled else []
    order = {location["name"]: index for index, location in enumerate(config["locations"])}
    rows.sort(key=lambda row: order.get(row["location"], len(order)))
    return page(request, "start", profile, config, {"sessions": rows, "step": 1})


def choice_context(request, session, config, purchaser, participants, policy, catalog, browser):
    empty_participant = participants.empty_form
    for form in [*participants, empty_participant]:
        for field in form.fields.values():
            if field.label in (config["design_group"], config["color_group"], config["age_group"]):
                from django.forms import Select
                field.choices = [("", f"Choose {field.label.lower()}"), *field.choices]
                field.widget = Select(choices=field.choices)
    options = {group.name: [{"id": str(o.pk), "name": o.label, "swatch": o.swatch, "image_url": o.image_url} for o in group.active_options]
               for group in catalog}
    designs = []
    for option in options.get(config["design_group"], []):
        legacy = config.get("design_previews", {}).get(option["id"],
            next((row for row in config["designs"] if row["name"] == option["name"]), {}))
        designs.append({**legacy, "name": option["name"], "image_url": option["image_url"]})
    from scheduling.inventory import availability_payload
    stock = availability_payload(session.offering, starts_at=session.starts_at) if session.offering.inventory_catalog_id else {}
    return {"session": session, "purchaser": purchaser, "participants": participants, "policy": policy,
        "empty_participant": empty_participant,
        "request_token": request.POST.get("request_token") or requests.request_token(session, catalog, browser),
        "catalog_data": {"options": options, "designs": designs, "colors": config["colors"],
            "deferred_groups": [g.name for g in catalog if g.tracks_stock],
            "inventory": stock, "tracks_stock": bool(session.offering.inventory_catalog_id),
            "image": config["design_image"], "design_group": config["design_group"],
            "color_group": config["color_group"], "age_group": config["age_group"],
            "initial_count": request.GET.get("participants", "1") if request.method == "GET" else "1",
            "price": config["price_cents"], "limit": min(session.capacity, 8)}, "step": 2}


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def choose(request, school_slug, pk):
    profile, config = context(request, school_slug)
    if not profile.native_reservations_enabled:
        return page(request, "start", profile, config, {"sessions": [], "step": 1}, status=409 if request.method == "POST" else 200)
    session = get_object_or_404(Session.objects.select_related("offering__location__profile", "room"),
        pk=pk, offering__location__profile=profile,
        offering__visit_program__isnull=True)
    browser = requests.browser_key(request, create=True)
    catalog = requests.catalog_for(session.offering)
    if (not requests.accepts_reservations(session, catalog)
            or session.starts_at <= service.booking_cutoff(online=config.get("stripe_checkout", False))):
        raise Http404("This session is no longer available.")
    error, status = "", 200
    if request.method == "POST":
        data = service.payload(request.POST, catalog)
        action = request.POST.get("action")
        catalog, purchaser, participants, policy, valid = service.validate(session, data, check_stock=action != "checkout")
        if valid and action == "review":
            try:
                total, deposit = service.reservations.workshop_quote(session.offering, config["price_cents"], len(participants))
            except ValidationError as exc:
                purchaser.add_error(None, exc)
                valid = False
        if valid and action == "review":
            rows = [{"name": form.cleaned_data["name"], **form.participant_snapshot()} for form in participants]
            service.selection_previews(rows, session.offering, config)
            values = {"session": session, "rows": rows, "purchaser": purchaser.cleaned_data,
                "fields": [(key, value) for key, values in data.lists() for value in values],
                "review_token": service.review_token(session, data, catalog, browser, config),
                "total": service.money(total), "due_now": service.money(deposit or total),
                "due_later": service.money(total - deposit if deposit else 0), "step": 3}
            with timezone.override(ZoneInfo(session.offering.location.timezone_name)):
                return page(request, "review", profile, config, values)
        if valid and action == "checkout":
            try:
                item = service.begin(profile, session, request.POST, browser, config)
                return redirect("scheduling:experience_booking", school_slug=school_slug, public_id=item.public_id)
            except ValidationError as exc:
                error = " ".join(exc.messages)
        elif action not in {"review", "edit"}:
            error = "Review your selections before continuing."
        status = 200 if action == "edit" else 400
    else:
        purchaser, participants = request_forms(session, catalog)
        policy = service.ReviewForm()
    values = choice_context(request, session, config, purchaser, participants, policy, catalog, browser)
    values["error"] = error
    with timezone.override(ZoneInfo(session.offering.location.timezone_name)):
        return page(request, "choose", profile, config, values, status)


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="30/m", block=True)
def booking(request, school_slug, public_id):
    profile, config = context(request, school_slug)
    item = service.customer_booking(profile, public_id, requests.browser_key(request))
    error, status = "", 200
    stripe_checkout = bool(item.reservation.order.account_id)
    payment_message = ""
    if stripe_checkout:
        if request.method == "POST":
            try:
                if request.POST.get("action") not in {"stripe_checkout", "payment_checkout"}:
                    raise ValidationError("Use checkout to pay for this booking.")
                return redirect(payments.start_checkout(request, profile, item))
            except ValidationError as exc:
                error, status = " ".join(exc.messages), 400
        elif request.GET.get("checkout_return") == "1":
            payment_message = payments.verify_return(item)
    elif request.method == "POST":
        if request.POST.get("action") == "decline":
            if item.reservation.state in ("confirmed", "completed"):
                error, status = "This booking is already confirmed. No new payment was attempted.", 400
            else:
                error = "Simulated payment declined. No payment recorded; this booking is not confirmed."
        else:
            try:
                service.simulate(profile, item, config, request.POST.get("action"))
                return redirect("scheduling:experience_booking", school_slug=school_slug, public_id=public_id)
            except ValidationError as exc:
                error, status = " ".join(exc.messages), 400
    with timezone.override(ZoneInfo(item.session.offering.location.timezone_name)):
        from core.payments.checkout import amount_due
        from core.payments.service import totals
        from scheduling.checkout import payment_choices
        amounts = totals(item.reservation.order)
        from scheduling import workshop_followup, guest_lists
        followup_token = workshop_followup.token_for(item.reservation) if guest_lists.editable(item.reservation) and item.session.offering.inventory_catalog_id else ""
        return page(request, "booking", profile, config, {"item": item, "people": service.people(item, config),
            "followup_token": followup_token,
            "total": service.money(amounts["total_cents"]), "error": error, "step": 4,
            "amounts": amounts, "due_now": service.money(amount_due(item.reservation.order)),
            "due_later": service.money(max(0, amounts["outstanding_cents"] - amount_due(item.reservation.order))),
            "stripe_checkout": stripe_checkout, "payment_message": payment_message,
            **payment_choices(item.reservation.order),
            "payment_provider_name": item.reservation.order.account.get_provider_display() if stripe_checkout else ""}, status)


@never_cache
@require_GET
def calendar(request, school_slug, public_id):
    profile, config = context(request, school_slug)
    item = service.customer_booking(profile, public_id, requests.browser_key(request))
    if item.reservation.state not in ("confirmed", "completed"):
        raise Http404
    response = HttpResponse(service.calendar(item), content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="workshop-demo.ics"'
    return response


@never_cache
@login_required
@require_GET
def preparation(request, school_slug):
    profile, config = context(request, school_slug)
    profile, role = require_location_access(request.user, school_slug)
    raw = request.GET.get("session")
    if raw and not raw.isdecimal():
        raise Http404
    from scheduling.location_filters import staff_location
    scope = staff_location(request, profile)
    if not scope["location_filter"].is_valid():
        return page(request, "preparation", profile, config, status=400)
    values = service.roster(profile, config, raw, location=scope["selected_location"])
    values["audience"] = "Studio team"
    values["can_map"] = has_permission(role, "setup.edit") and not location_restricted(profile) and has_capability(profile, "integrations")
    for session in values["sessions"]:
        local = timezone.localtime(session.starts_at, ZoneInfo(session.offering.location.timezone_name))
        session.demo_label = f"{session.offering.name} / {session.offering.location.name} / {local:%b %d, %Y %I:%M %p}"
    if values["selected"]:
        zone = ZoneInfo(values["selected"].offering.location.timezone_name)
    else:
        zone = timezone.get_default_timezone()
    with timezone.override(zone):
        return page(request, "preparation", profile, config, values)


@never_cache
@login_required
@require_GET
def integration(request, school_slug, public_id):
    profile, config = context(request, school_slug)
    require_access(request.user, school_slug, "setup.edit", capability="integrations")
    item = get_object_or_404(WorkshopRequest.objects.select_related("reservation"), public_id=public_id,
        session__offering__location__profile=profile,
        reservation__state="confirmed", status="pending")
    try:
        projection, error = service.projection(item, config), ""
    except ValidationError as exc:
        projection, error = None, " ".join(exc.messages)
    return page(request, "integration", profile, config, {"audience": "Studio team", "item": item,
        "projection": projection, "error": error, "people": service.people(item, config), "mapping": config["mapping"].items()},
        status=400 if error else 200)
