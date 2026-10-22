from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from core.payments import checkout, service
from core.services import billing_stripe
from .access import accessible_profiles
from .capabilities import has_capability
from .checkout import customer_reservation, payment_choices, prepare_booking_checkout, settle_booking_event, validate_booking_checkout


@never_cache
@require_http_methods(["GET", "POST"])
def customer_payment(request, school_slug, token):
    item = customer_reservation(school_slug, token)
    error = ""
    if request.method == "POST":
        return_url = request.build_absolute_uri(reverse("scheduling:customer_payment", kwargs={"school_slug": school_slug, "token": token}))
        try:
            attempt = prepare_booking_checkout(item, return_url, payment_option=request.POST.get("payment_option", "due"))
            url = checkout.open_checkout(attempt)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            return redirect(url or return_url)
    item.refresh_from_db()
    due = checkout.amount_due(item.order)
    try:
        validate_booking_checkout(item)
    except ValidationError:
        ready = False
    else:
        ready = due >= 50
    amounts = service.totals(item.order)
    can_view_reservation = (not item.visit_booking_id
        and has_capability(item.profile, "reservations")
        and accessible_profiles(request.user).filter(pk=item.profile_id).exists())
    context = {"item": item, "business": item.profile.school, "due": Decimal(due) / 100,
               "amounts": amounts, "ready": ready, "error": error, **payment_choices(item.order),
               "can_view_reservation": can_view_reservation,
               "balance_due_today": timezone.localdate(timezone=ZoneInfo(item.snapshot["timezone"])) >= timezone.localdate(item.starts_at, ZoneInfo(item.snapshot["timezone"])),
               "deposit_due": bool(item.order.deposit_cents and amounts["net_cents"] < item.order.deposit_cents),
               "confirmed": item.state in ("confirmed", "completed")}
    if item.party_request_id and item.party_request.offering.inventory_catalog_id:
        from .guest_lists import editable, token_for
        if editable(item):
            context["guest_list_url"] = reverse("scheduling:guest_list", kwargs={
                "school_slug": school_slug, "token": token_for(item)})
    context["hold_expired"] = item.state == "held" and item.hold_until <= timezone.now()
    response = render(request, "scheduling/customer_payment.html", context, status=400 if error else 200)
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
