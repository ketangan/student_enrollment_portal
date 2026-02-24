# core/views_billing.py
"""
Billing / Upgrade views for school admins.

- billing_view: shows current plan, pricing options, Stripe checkout + portal
- stripe_webhook: handles inbound Stripe webhook events
- billing_create_checkout: initiates a Stripe Checkout session
- billing_portal: redirects to Stripe Customer Portal
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from core.admin.common import _is_superuser, _membership_school_id
from core.models import School
from core.services import feature_flags as ff
from core.services.billing_stripe import (
    construct_webhook_event,
    create_checkout_session,
    create_portal_session,
    get_pricing_options,
    handle_checkout_completed,
    handle_subscription_deleted,
    handle_subscription_updated,
    is_stripe_configured,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers — school scoping (same pattern as reports)
# ---------------------------------------------------------------------------
def _resolve_billing_school(request) -> School | None:
    """Resolve the school for the billing page.

    - Superuser: uses ?school=<slug> query param (default: first school)
    - School admin: uses SchoolAdminMembership (ignores query param)
    """
    user = request.user
    if not user or not user.is_authenticated or not user.is_staff:
        return None

    if _is_superuser(user):
        slug = request.GET.get("school") or request.POST.get("school")
        if slug:
            return School.objects.filter(slug=slug).first()
        return School.objects.order_by("display_name", "slug").first()

    school_id = _membership_school_id(user)
    if school_id:
        return School.objects.filter(id=school_id).first()

    return None


# ---------------------------------------------------------------------------
# Billing page
# ---------------------------------------------------------------------------
@require_http_methods(["GET"])
def billing_view(request):
    """Show current plan + upgrade options for a school."""
    user = request.user
    if not user or not user.is_authenticated or not user.is_staff:
        raise Http404

    school = _resolve_billing_school(request)
    if not school:
        raise Http404("No school found for this account")

    stripe_configured = is_stripe_configured()

    # Build features list from the school's effective flags
    flags = ff.merge_flags(plan=school.plan, overrides=school.feature_flags)
    features = [
        {"name": flag.replace("_", " ").replace(" enabled", "").title(), "enabled": val}
        for flag, val in sorted(flags.items())
    ]

    # Pricing options
    pricing = get_pricing_options() if stripe_configured else []

    # Determine if school has an active Stripe subscription
    has_active_subscription = bool(getattr(school, "has_active_stripe_subscription", False))

    # All schools for superuser school-switcher
    schools = None
    if _is_superuser(user):
        schools = School.objects.all().order_by("display_name", "slug")

    context = {
        "school": school,
        "schools": schools,
        "plan_display": dict(ff.PLAN_CHOICES).get(school.plan, school.plan),
        "features": features,
        "pricing": pricing,
        "has_active_subscription": has_active_subscription,
        "stripe_configured": stripe_configured,
        "subscription_status": school.stripe_subscription_status,
    }
    return render(request, "billing.html", context)


# ---------------------------------------------------------------------------
# Checkout (redirect to Stripe)
# ---------------------------------------------------------------------------
@require_POST
def billing_create_checkout(request):
    """Create a Stripe Checkout session and redirect."""
    user = request.user
    if not user or not user.is_authenticated or not user.is_staff:
        raise Http404

    school = _resolve_billing_school(request)
    if not school:
        raise Http404

    # Stripe config check (hardening)
    if not is_stripe_configured():
        messages.error(request, "Billing is not configured.")
        return redirect(_billing_url(request, school))

    price_id = request.POST.get("price_id", "").strip()
    if not price_id:
        messages.error(request, "Missing price selection.")
        return redirect(_billing_url(request, school))

    # Validate price_id against configured pricing options
    valid_price_ids = {opt["price_id"] for opt in get_pricing_options()}
    if price_id not in valid_price_ids:
        messages.error(request, "Invalid price selection.")
        return redirect(_billing_url(request, school))

    billing_url = request.build_absolute_uri(_billing_url(request, school))
    success_url = billing_url + ("&" if "?" in billing_url else "?") + "status=success"
    cancel_url = billing_url + ("&" if "?" in billing_url else "?") + "status=canceled"

    # Pass user email if present and school has no Stripe customer
    user_email = getattr(request.user, "email", None)
    checkout_url = create_checkout_session(
        school=school,
        price_id=price_id,
        success_url=success_url,
        cancel_url=cancel_url,
        customer_email=user_email if user_email else None,
    )

    if not checkout_url:
        messages.error(request, "Could not start checkout. Please try again or contact support.")
        return redirect(_billing_url(request, school))

    return redirect(checkout_url)


# ---------------------------------------------------------------------------
# Billing Portal (redirect to Stripe)
# ---------------------------------------------------------------------------
@require_POST
def billing_portal(request):
    """Redirect to Stripe Customer Portal."""
    user = request.user
    if not user or not user.is_authenticated or not user.is_staff:
        raise Http404

    school = _resolve_billing_school(request)
    if not school:
        raise Http404

    return_url = request.build_absolute_uri(_billing_url(request, school))
    portal_url = create_portal_session(school=school, return_url=return_url)

    if not portal_url:
        messages.error(request, "Could not open billing portal. Please try again.")
        return redirect(_billing_url(request, school))

    return redirect(portal_url)


# ---------------------------------------------------------------------------
# Stripe Webhook
# ---------------------------------------------------------------------------
@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Handle inbound Stripe webhook events."""
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    event = construct_webhook_event(payload, sig_header)
    if event is None:
        return HttpResponse("Webhook signature verification failed", status=400)

    event_type = event.get("type", "") if isinstance(event, dict) else getattr(event, "type", "")
    data_object = (
        event.get("data", {}).get("object", {})
        if isinstance(event, dict)
        else getattr(getattr(event, "data", None), "object", {})
    )

    logger.info("Stripe webhook received: %s", event_type)

    if event_type == "checkout.session.completed":
        handle_checkout_completed(data_object)
    elif event_type == "customer.subscription.updated":
        handle_subscription_updated(data_object)
    elif event_type == "customer.subscription.deleted":
        handle_subscription_deleted(data_object)
    else:
        logger.debug("Unhandled Stripe event type: %s", event_type)

    return HttpResponse("ok", status=200)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _billing_url(request, school: School) -> str:
    """Build the billing page URL for the given school."""
    base = reverse("admin:billing")
    if _is_superuser(request.user):
        return f"{base}?school={school.slug}"
    return base
