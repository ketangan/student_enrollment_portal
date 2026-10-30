from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from core.payments import checkout, service
from core.services import billing_stripe
from .checkout import customer_reservation, payment_choices, prepare_booking_checkout, settle_booking_event, validate_booking_checkout


@never_cache
@require_http_methods(["GET", "POST"])
def customer_payment(request, school_slug, token):
    item = customer_reservation(school_slug, token)
    error = ""
    payment_requested = request.GET.get("action") == "payment"
    if request.method == "POST":
        return_url = request.build_absolute_uri(reverse("scheduling:customer_payment", kwargs={"school_slug": school_slug, "token": token}))
        try:
            attempt = prepare_booking_checkout(item, return_url, payment_option=request.POST.get("payment_option", "due"),
                cancel_url=return_url + "?action=payment" if payment_requested else None)
            url = checkout.open_checkout(attempt)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            return redirect(url or return_url)
    item.refresh_from_db()
    from .customer_edits import editable
    due = checkout.amount_due(item.order)
    try:
        validate_booking_checkout(item)
    except ValidationError:
        ready = False
    else:
        ready = due >= 50
    amounts = service.totals(item.order)
    from .payment_requests import deposit_received
    payment_requirement_met = deposit_received(item)
    context = {"item": item, "business": item.profile.school, "due": Decimal(due) / 100,
               "payment_form_url": reverse("scheduling:customer_payment", kwargs={"school_slug": school_slug, "token": token}),
               "amounts": amounts, "ready": ready, "error": error, **payment_choices(item.order),
               "balance_due_today": timezone.localdate(timezone=ZoneInfo(item.snapshot["timezone"])) >= timezone.localdate(item.starts_at, ZoneInfo(item.snapshot["timezone"])),
               "deposit_due": bool(item.order.deposit_cents and amounts["net_cents"] < item.order.deposit_cents),
               "confirmed": item.state in ("confirmed", "completed"),
               "payment_requirement_met": payment_requirement_met}
    context["payment_requested"] = payment_requested
    if context["payment_requested"]:
        context["payment_form_url"] += "?action=payment"
    if item.party_request_id and item.party_request.offering.inventory_catalog_id:
        from .guest_lists import editable as guest_list_editable, token_for
        if guest_list_editable(item) and payment_requirement_met:
            context["guest_list_url"] = reverse("scheduling:guest_list", kwargs={
                "school_slug": school_slug, "token": token_for(item)})
    context["hold_expired"] = item.state == "held" and item.hold_until <= timezone.now()
    from .customer_experience import checkout_presentation
    context.update(checkout_presentation(item, context["payment_form_url"]))
    if editable(item):
        context["customer_edit_url"] = reverse("scheduling:customer_edit_booking", kwargs={"school_slug": school_slug, "token": token})
    context["review_due"] = context["payment_active_amount"] or context["payment_minimum"]
    context["review_later"] = max(Decimal(0), amounts["outstanding"] - context["review_due"])
    template = "scheduling/party_payment.html" if context.get("party_experience") else "scheduling/customer_payment.html"
    if context.get("workshop_experience") and editable(item):
        from .experience_demo.config import context as experience_context
        from .experience_demo import service as experience_service
        _, config = experience_context(request, school_slug)
        source = item.workshop_request
        rows = [{"name": person.name, "selections": person.selections,
                 "selections_deferred": person.selections_deferred} for person in source.participants.all()]
        experience_service.selection_previews(rows, source.session.offering, config)
        breakdown = source.session_snapshot.get("price_breakdown", {})
        context.update(saved_review=True, demo=config, rows=rows, session=source.session,
            purchaser={"purchaser_name": source.purchaser_name, "purchaser_email": source.purchaser_email,
                       "purchaser_phone": source.purchaser_phone},
            unit_price=Decimal(config["price_cents"])/100, total=amounts["total"],
            subtotal=Decimal(breakdown.get("subtotal_cents", item.order.total_cents))/100,
            tax=Decimal(breakdown.get("tax_cents", 0))/100, stripe_checkout=True,
            due_now=context["review_due"], due_later=context["review_later"])
        template = "scheduling/experience/review.html"
    response = render(request, template, context, status=400 if error else 200)
    response["Referrer-Policy"] = "same-origin"
    return response


@csrf_exempt
@require_POST
def stripe_test_webhook(request):
    if len(request.body) > 262144:
        return HttpResponseBadRequest("Payload too large.")
    try:
        event = billing_stripe.construct_merchant_test_event(request.body, request.headers.get("Stripe-Signature", ""))
        from core.payments.notifications import notify_booking_failure
        if not notify_booking_failure(event):
            settle_booking_event(event)
    except ValidationError:
        return HttpResponseBadRequest("Event could not be verified for this payment.")
    return HttpResponse(status=200)
