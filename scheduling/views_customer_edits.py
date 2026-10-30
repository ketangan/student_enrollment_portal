from zoneinfo import ZoneInfo

from django.core import signing
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from . import customer_edits, requests
from .checkout import customer_reservation
from .models import Offering
from .party_forms import PartyInquiryForm
from .customer_experience import checkout_presentation


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="30/m", method="POST", block=True)
def edit_booking(request, school_slug, token):
    item = customer_reservation(school_slug, token)
    review_url = reverse("scheduling:customer_payment", kwargs={"school_slug": school_slug, "token": token})
    if not customer_edits.editable(item):
        return redirect(review_url)
    edit_revision = request.POST.get("edit_revision", "") if request.method == "POST" else customer_edits.edit_token(item)
    if item.party_request_id:
        response = party(request, item, review_url, edit_revision)
    elif item.workshop_request_id:
        response = workshop(request, item, review_url, edit_revision)
    else:
        raise Http404
    response["Referrer-Policy"] = "same-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def party(request, item, review_url, edit_revision):
    from .parties import inquiry_token
    offerings = Offering.objects.filter(location__profile=item.profile, is_published=True,
        accepts_party_requests=True, party_packages__is_published=True,
        party_packages__booking_mode="direct", visit_program__isnull=True).distinct().select_related("location")
    offering = get_object_or_404(offerings, pk=request.GET.get("offering") or item.party_request.offering_id)
    presentation = checkout_presentation(item, review_url)
    if not presentation:
        raise Http404
    initial = customer_edits.party_initial(item)
    if offering.pk != item.party_request.offering_id:
        initial.update(package=None, party_slot=None)
    error = ""
    with timezone.override(ZoneInfo(offering.location.timezone_name)):
        form = PartyInquiryForm(request.POST if request.method == "POST" else None,
            initial=initial, offering=offering, exclude_reservation=item.pk)
        if request.method == "POST":
            if request.POST.get("validate_only") == "1":
                errors = form.errors.get_json_data()
                for name in form.acknowledgment_names:
                    errors.pop(name, None)
                try:
                    customer_edits.check_token(item, edit_revision)
                except ValidationError as exc:
                    errors["__all__"] = [{"message": message, "code": "stale"} for message in exc.messages]
                return JsonResponse({"errors": errors})
            if form.is_valid():
                try:
                    customer_edits.save_party(item, offering, request.POST, edit_revision)
                except ValidationError as exc:
                    error = " ".join(exc.messages)
                else:
                    if request.POST.get("start_payment") == "1":
                        from .views_checkout import customer_payment
                        from .checkout import payment_token
                        return customer_payment(request, item.profile.school.slug, payment_token(item))
                    return redirect(review_url)
        return render(request, "scheduling/party_experience.html", {**presentation,
            "business": item.profile.school, "offering": offering, "offerings": offerings,
            "form": form, "step": 1, "error": error, "editing_booking": True,
            "edit_revision": edit_revision, "party_location_url": request.path,
            "request_token": request.POST.get("request_token", "") if request.method == "POST" else inquiry_token(offering, requests.browser_key(request, create=True)),
            "party_form_url": request.path + f"?offering={offering.pk}"},
            status=400 if error or (request.method == "POST" and form.errors) else 200)


def workshop(request, item, review_url, edit_revision):
    from .experience_demo import service
    from .experience_demo.config import context
    from .experience_demo.views import page, choice_context
    profile, config = context(request, item.profile.school.slug)
    browser = requests.browser_key(request, create=True)
    shared = {"editing_booking": True, "edit_revision": edit_revision,
              "payment_review_url": review_url, "change_workshop_url": request.path + "?change_workshop=1"}
    if request.method == "GET" and request.GET.get("change_workshop") == "1":
        return page(request, "start", profile, config, {**shared, "step": 1,
            "sessions": service.session_rows(profile, online=True, exclude=item.pk),
            "edit_session_url": request.path})
    session = get_object_or_404(service.sessions(profile, online=True),
        pk=request.GET.get("session") or item.session_id)
    error = ""
    with timezone.override(ZoneInfo(session.offering.location.timezone_name)):
        body = service.payload(request.POST, requests.catalog_for(session.offering)) if request.method == "POST" else customer_edits.workshop_data(item, session)
        catalog, purchaser, participants, policy, valid = service.validate(session, body, exclude=item.pk)
        action = request.POST.get("action")
        if request.method == "POST" and valid and action == "checkout":
            try:
                expected = signing.loads(service.review_token(session, body, catalog, browser, config), salt=service.REVIEW_SALT)
                actual = signing.loads(request.POST.get("review_token", ""), salt=service.REVIEW_SALT, max_age=900)
                if actual != expected:
                    raise signing.BadSignature
                customer_edits.save_workshop(item, session, body, edit_revision, config,
                    review_token=request.POST.get("review_token", ""), browser=browser)
            except signing.BadSignature:
                error = "Your review expired or changed. Review the booking again before continuing."
            except ValidationError as exc:
                error = " ".join(exc.messages)
            else:
                if request.POST.get("start_payment") == "1":
                    from .views_checkout import customer_payment
                    from .checkout import payment_token
                    return customer_payment(request, item.profile.school.slug, payment_token(item))
                return redirect(review_url)
        if request.method == "POST" and valid and action == "review":
            try:
                customer_edits.check_token(item, edit_revision)
                total, deposit = service.reservations.workshop_quote(session.offering, config["price_cents"], len(participants))
            except ValidationError as exc:
                error = " ".join(exc.messages)
            else:
                rows = [{"name": form.cleaned_data["name"], **form.participant_snapshot()} for form in participants]
                service.selection_previews(rows, session.offering, config)
                breakdown = service.reservations.workshop_price_breakdown(session.offering, config["price_cents"], len(rows))
                return page(request, "review", profile, config, {**shared,
                    "session": session, "rows": rows, "purchaser": purchaser.cleaned_data,
                    "fields": [(key, value) for key, values in body.lists() for value in values],
                    "review_token": service.review_token(session, body, catalog, browser, config),
                    "subtotal": service.money(breakdown["subtotal_cents"]), "tax": service.money(breakdown["tax_cents"]),
                    "total": service.money(total), "due_now": service.money(deposit or total),
                    "due_later": service.money(total - deposit if deposit else 0), "step": 3})
        values = choice_context(request, session, config, purchaser, participants, policy, catalog, browser, exclude=item.pk)
        values.update(shared, error=error)
        return page(request, "choose", profile, config, values,
                    status=400 if error or (request.method == "POST" and action != "edit" and not valid) else 200)
