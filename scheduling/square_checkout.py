"""Square card checkout using the existing booking locks, ledger and recovery."""
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST, require_http_methods
from django_ratelimit.decorators import ratelimit

from core.models import School
from core.payments import checkout, square, refunds
from core.payments.models import PaymentAccount, PaymentAttempt, PaymentOrder, ProviderEventReceipt, Refund
from core.services.payment_notifications import queue_payment_notice
from .checkout import prepare_booking_checkout, recover_attempt, settle_booking_snapshot, validate_booking_checkout
from .models import Reservation, SchedulingProfile


def locked_attempt(attempt):
    item = Reservation.objects.get(order_id=attempt.order_id)
    SchedulingProfile.objects.select_for_update().get(pk=item.profile_id)
    School.objects.select_for_update().get(pk=attempt.order.school_id)
    PaymentOrder.objects.select_for_update().get(pk=attempt.order_id)
    return PaymentAttempt.objects.select_for_update(of=("self",)).select_related("order__account", "account__school").get(pk=attempt.pk)


def notify_failure(attempt):
    queue_payment_notice(key=f"square:{attempt.public_id}:failed", school=attempt.order.school,
        kind="Booking payment failed", reference=attempt.provider_intent or str(attempt.public_id),
        amount=attempt.amount_cents, currency=attempt.order.currency, livemode=attempt.order.environment == "live",
        detail="Square card payment failed. The customer can try another card. School access is unchanged.")


def submit(attempt, source):
    if not isinstance(source, str) or not source.startswith("cnon:") or len(source) > 1000:
        raise ValidationError("Enter payment details using the secure card form.")
    with transaction.atomic():
        current = locked_attempt(attempt)
        validate_booking_checkout(Reservation.objects.select_related("profile", "order__account").get(order=current.order))
        if (current.account.provider != "square" or current.state not in PaymentAttempt.ACTIVE
                or current.submitted_at or current.close_requested or current.expires_at <= timezone.now()):
            raise ValidationError("Check this payment's status before making another payment.")
        current.submitted_at, current.state = timezone.now(), "unknown"
        current.save(update_fields=["submitted_at", "state"])
    # No card token is saved. The durable submitted marker blocks double-clicks and retries.
    try:
        result = square.create_payment(current, source)
    except square.Declined:
        with transaction.atomic():
            current = locked_attempt(current)
            settle_booking_snapshot(current, square.snapshot(current, expired=True))
            notify_failure(current)
        raise
    settle_booking_snapshot(current, result)
    if result.get("square_payment", {}).get("status") in {"FAILED", "CANCELED"}:
        notify_failure(current)
        raise square.Declined("The card was declined. Try another card.")


def recover_square_attempt(attempt):
    # An unsubmitted local form can close atomically; once submitted, only Square can settle it.
    with transaction.atomic():
        current = locked_attempt(attempt)
        if current.state not in PaymentAttempt.ACTIVE:
            return
        if not current.submitted_at:
            if current.close_requested or current.expires_at <= timezone.now():
                settle_booking_snapshot(current, square.snapshot(current, expired=True))
            return
    snapshot = square.retrieve_order_checkout(current)
    settle_booking_snapshot(current, snapshot)
    if snapshot.get("square_payment", {}).get("status") in {"FAILED", "CANCELED"}:
        notify_failure(current)


def resolve_attempt(school_slug, token):
    try:
        public_id = signing.loads(token, salt="square-booking-checkout", max_age=86400)
        attempt = get_object_or_404(PaymentAttempt.objects.select_related("order__school", "order__account", "account__school"),
            public_id=public_id, order__school__slug=school_slug, order__purpose="booking", account__provider="square")
        checkout.validate_account(attempt.order, new_payment=False)
    except (signing.BadSignature, ValueError, ValidationError):
        raise Http404("Payment link is no longer available.") from None
    return attempt


@never_cache
@sensitive_post_parameters("source_id")
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="30/m", method="POST", block=True)
def square_checkout(request, school_slug, token):
    attempt = resolve_attempt(school_slug, token)
    item = get_object_or_404(Reservation, order=attempt.order)
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") == "retry":
                if attempt.close_requested:
                    return redirect(attempt.cancel_url)
                if attempt.state != "expired":
                    raise ValidationError("Check payment status before trying again.")
                option = "full" if attempt.amount_cents > checkout.amount_due(item.order) else "due"
                next_attempt = prepare_booking_checkout(item, attempt.success_url, payment_option=option)
                return redirect(checkout.open_checkout(next_attempt))
            if request.POST.get("action") == "check":
                recover_attempt(attempt, force=True)
            else:
                submit(attempt, request.POST.get("source_id", ""))
        except ValidationError as exc:
            error = exc.messages[0]
        attempt.refresh_from_db()
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"state": attempt.state, "error": error,
                "return_url": attempt.success_url if attempt.state == "paid" else ""}, status=400 if error else 200)
    if attempt.state == "paid":
        return redirect(attempt.success_url)
    if attempt.state in PaymentAttempt.ACTIVE and not attempt.submitted_at and (attempt.expires_at <= timezone.now() or attempt.close_requested):
        recover_square_attempt(attempt)
        attempt.refresh_from_db()
    ready = False
    try:
        validate_booking_checkout(item)
        ready = (not attempt.submitted_at and not attempt.close_requested
                 and attempt.state in PaymentAttempt.ACTIVE and attempt.expires_at > timezone.now())
    except ValidationError as exc:
        error = error or exc.messages[0]
    from .customer_experience import checkout_presentation
    from .checkout import payment_token
    from django.urls import reverse
    back_url = reverse("scheduling:customer_payment", kwargs={"school_slug": school_slug, "token": payment_token(item)}) if item.workshop_request_id and "experience_demo" in item.workshop_request.session_snapshot else attempt.cancel_url
    response = render(request, "scheduling/square_checkout.html", {
        **checkout_presentation(item, back_url), "checkout_back_url": back_url,
        "business": attempt.order.school, "item": item, "attempt": attempt, "ready": ready, "error": error,
        "square_options": {"applicationId": square.environment_setting(attempt.account.environment, "APPLICATION_ID"),
                           "locationId": attempt.account.location_reference, "amount": str(attempt.amount)},
    })
    response["Referrer-Policy"] = "same-origin"
    return response


@transaction.atomic
def settle_event(attempt, event, payment):
    attempt = locked_attempt(attempt)
    if ProviderEventReceipt.objects.filter(account=attempt.account, environment=attempt.order.environment, event_id=event["event_id"]).exists():
        return
    if not attempt.submitted_at:
        raise ValidationError("Square payment was not initiated by this checkout.")
    settle_booking_snapshot(attempt, square.snapshot(attempt, payment))
    if payment["status"] in {"FAILED", "CANCELED"}:
        notify_failure(attempt)
    ProviderEventReceipt.objects.create(account=attempt.account, environment=attempt.order.environment, event_id=event["event_id"], event_type=event["type"], attempt=attempt)


@transaction.atomic
def settle_refund_event(refund, event, result):
    attempt = locked_attempt(refund.attempt)
    if ProviderEventReceipt.objects.filter(account=attempt.account, environment=attempt.order.environment, event_id=event["event_id"]).exists():
        return
    if not refund.submitted_at or event.get("merchant_id") != attempt.account.reference:
        raise ValidationError("Square refund was not submitted for this account.")
    refunds.apply_snapshot(refund, square.refund_snapshot(refund, result))
    ProviderEventReceipt.objects.create(account=attempt.account, environment=attempt.order.environment, event_id=event["event_id"], event_type=event["type"], attempt=attempt)


@csrf_exempt
@require_POST
def square_test_webhook(request):
    return square_webhook(request, "test")


@csrf_exempt
@require_POST
def square_live_webhook(request):
    return square_webhook(request, "live")


def square_webhook(request, environment):
    if len(request.body) > 262144:
        return HttpResponseBadRequest("Payload too large.")
    try:
        event = square.construct_event(request.body, request.headers.get("X-Square-Hmacsha256-Signature", ""), environment)
        if event.get("type") not in {"payment.created", "payment.updated", "refund.created", "refund.updated"}:
            return HttpResponse(status=200)
        data = event.get("data", {})
        payment_id = data.get("id") if isinstance(data, dict) else None
        if not square.identifier(payment_id):
            raise ValidationError("Invalid payment identity.")
    except ValidationError:
        return HttpResponseBadRequest("Event signature or payload could not be verified.")
    try:
        merchant = event.get("merchant_id")
        if not square.identifier(merchant):
            raise ValidationError("Invalid Square merchant identity.")
        account = PaymentAccount.objects.select_related("school").filter(
            provider="square", environment=environment, reference=merchant).order_by("scope", "pk").first()
        if account is None:
            raise ValidationError("Square merchant is not configured.")
        if event["type"].startswith("refund."):
            result = square.account_request(account, "GET", "/refunds/" + payment_id).get("refund", {})
            if not isinstance(result, dict) or result.get("id") != payment_id:
                raise ValidationError("Square refund identity mismatch.")
            candidates = Refund.objects.select_related("attempt__account", "attempt__order").filter(
                attempt__account__provider="square", attempt__account__environment=environment,
                attempt__account__reference=merchant,
                attempt__provider_intent=result.get("payment_id"))
            matching = [refund for refund in candidates if square.refund_reason(refund) == result.get("reason")]
            if len(matching) == 1:
                settle_refund_event(matching[0], event, result)
            elif matching:
                raise ValidationError("Ambiguous refund identity.")
            return HttpResponse(status=200)
        # Read the current payment; never trust an old or out-of-order webhook snapshot.
        payment = square.account_request(account, "GET", "/payments/" + payment_id).get("payment", {})
        if not isinstance(payment, dict):
            raise ValidationError("Square returned an invalid payment.")
        reference = payment.get("reference_id")
        try:
            attempt = PaymentAttempt.objects.select_related("order__account", "account__school").filter(
                public_id=reference, account__provider="square", account__environment=environment).first() if reference else None
        except (ValueError, ValidationError):
            attempt = None
        if attempt is None:
            return HttpResponse(status=200)
        if event.get("merchant_id") != attempt.account.reference or payment.get("id") != payment_id:
            raise ValidationError("Square account mismatch.")
        settle_event(attempt, event, payment)
    except ValidationError:
        # A signed event can arrive during a transient API outage. Ask Square to retry.
        return HttpResponse("Payment verification is pending.", status=503)
    return HttpResponse(status=200)
