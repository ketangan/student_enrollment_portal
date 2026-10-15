import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core import signing
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from core.forms_school_pricing import SchoolPricingForm
from core.models import SchoolBillingCheckout
from core.services.school_pricing import accepted_location_change, apply_billing_at_renewal, current_offer, publish_offer, recover_checkout, start_checkout, validate_prices, validate_renewal_change
from core.views_ops import _log, ops_required

logger = logging.getLogger(__name__)


@ops_required
@require_http_methods(["GET", "POST"])
def school_pricing_settings(request, slug):
    if request.method == "GET":
        return redirect(reverse("ops_school_detail", kwargs={"slug": slug}) + "#subscription-pricing")
    from core.views_ops import ops_school_detail_view
    return ops_school_detail_view(request, slug)


def school_pricing_context(request, school):
    if request.method == "POST" and request.POST.get("action") == "apply_billing":
        try:
            try:
                review = signing.loads(request.POST.get("billing_review", ""), salt="school-billing-change", max_age=600)
            except signing.BadSignature:
                raise ValidationError("Reload and review the billing change again before applying it.")
            if (review["school"] != school.pk or review["mode"] != settings.STRIPE_MODE
                    or str(review["offer"]) != request.POST.get("offer_id")):
                raise ValidationError("This billing review does not match the school and saved agreement.")
            change = apply_billing_at_renewal(
                school=school, offer_id=review["offer"], actor=request.user,
                reviewed_offer_id=review["accepted_offer"], reviewed_renewal=review["renewal"])
            _log(request, "change", "core.school", school.pk, str(school),
                 {"action": "subscription_billing_updated", "change_id": str(change.pk), "offer_id": change.offer_id,
                  "effective_at": change.effective_at.isoformat(), "mode": settings.STRIPE_MODE})
            messages.success(request, "Billing updated for the next renewal. No immediate charge or proration; the billing date is unchanged.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(reverse("ops_school_detail", kwargs={"slug": school.slug}) + "#subscription-pricing")
    if request.method == "POST" and request.POST.get("action") in {"check_checkout", "expire_checkout"}:
        try:
            result = recover_checkout(school=school, checkout_id=request.POST.get("checkout_id", ""),
                                      expire=request.POST["action"] == "expire_checkout")
            _log(request, "action", "core.school", school.pk, str(school),
                 {"action": request.POST["action"], "checkout_id": request.POST.get("checkout_id"), "mode": settings.STRIPE_MODE})
            messages.success(request, result)
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(reverse("ops_school_detail", kwargs={"slug": school.slug}) + "#subscription-pricing")
    offer = current_offer(school)
    initial = {"expected_offer_id": str(offer.pk) if offer else ""}
    if offer:
        initial.update(label=offer.label, base_price_id=offer.base_price["id"],
                       location_price_id=offer.location_price.get("id", ""),
                       included_locations=offer.included_locations, additional_locations=offer.additional_locations)
    is_pricing_post = request.method == "POST" and request.POST.get("action") in {"validate", "publish"}
    form = SchoolPricingForm(request.POST if is_pricing_post else None, initial=initial)
    verified = None
    total = None
    if is_pricing_post and form.is_valid():
        data = form.cleaned_data
        try:
            verified = validate_prices(data["base_price_id"], data["location_price_id"])
            total = (verified["base_price"]["amount"] + verified["location_price"].get("amount", 0) * data["additional_locations"]) / 100
            confirmation = {"school": school.pk, "mode": settings.STRIPE_MODE,
                            "fields": {key: value for key, value in data.items() if key != "verification"},
                            "prices": verified}
            if request.POST.get("action") == "publish":
                try:
                    previous_confirmation = signing.loads(data["verification"], salt="school-pricing", max_age=600)
                except signing.BadSignature:
                    previous_confirmation = None
                if previous_confirmation != confirmation:
                    raise ValidationError("Validate prices again before saving; the fields or verified prices have changed.")
                offer = publish_offer(school=school, actor=request.user, label=data["label"],
                                      included_locations=data["included_locations"], additional_locations=data["additional_locations"],
                                      expected_offer_id=data["expected_offer_id"], verified=verified)
                _log(request, "change", "core.school", school.pk, str(school),
                     {"action": "subscription_pricing_published", "offer_id": offer.pk, "mode": offer.mode})
                messages.success(request, "Agreement saved. Existing subscriptions and feature access were not changed.")
                return redirect(reverse("ops_school_detail", kwargs={"slug": school.slug}) + "#subscription-pricing")
            form.data = form.data.copy()
            form.data["verification"] = signing.dumps(confirmation, salt="school-pricing")
        except ValidationError as exc:
            form.add_error(None, exc)
            verified = None
    checkout = SchoolBillingCheckout.objects.select_related("offer").filter(
        offer__school=school, offer__mode=settings.STRIPE_MODE, state="complete",
        subscription_id=school.stripe_subscription_id).first() if school.stripe_subscription_id else None
    location_change = accepted_location_change(checkout) if checkout else None
    accepted_offer = location_change.offer if location_change else checkout.offer if checkout else None
    pending_location_change = checkout.location_changes.filter(state="pending").first() if checkout else None
    renewal = pending_location_change.effective_at if pending_location_change else school.stripe_current_period_end
    review_token = ""
    change_error = ""
    if offer and accepted_offer and offer.pk != accepted_offer.pk:
        try:
            validate_renewal_change(accepted_offer, offer)
            if not renewal:
                raise ValidationError("A confirmed renewal date is required. Check the subscription in Stripe before applying changes.")
            review_token = signing.dumps({"school": school.pk, "mode": settings.STRIPE_MODE,
                                          "offer": offer.pk, "accepted_offer": accepted_offer.pk,
                                          "renewal": int(renewal.timestamp())}, salt="school-billing-change")
        except ValidationError as exc:
            change_error = " ".join(exc.messages)
    return {
        "active_nav": "schools", "school": school, "form": form, "offer": offer,
        "verified": verified, "total": total, "billing_mode": settings.STRIPE_MODE,
        "location_change": location_change, "pending_location_change": pending_location_change,
        "accepted_offer": accepted_offer, "billing_review": review_token,
        "renewal": renewal, "change_error": change_error,
        "history": school.billing_offers.filter(mode=settings.STRIPE_MODE).order_by("-created_at")[:10],
        "pending_checkouts": SchoolBillingCheckout.objects.filter(offer__school=school, offer__mode=settings.STRIPE_MODE, state="pending").select_related("offer").order_by("created_at"),
    }


def negotiated_checkout_response(request, school, billing_url):
    try:
        url = start_checkout(school=school, offer_id=request.POST.get("offer_id", ""),
                             success_url=billing_url + "?status=success", cancel_url=billing_url + "?status=canceled",
                             customer_email=request.user.email or None)
        return redirect(url)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    except Exception as exc:
        logger.warning("Negotiated checkout failed for school %s (%s)", school.pk, type(exc).__name__)
        messages.error(request, "Checkout could not be confirmed. Please retry or contact Pontora. Your agreed price has not been replaced.")
    return redirect(billing_url)
