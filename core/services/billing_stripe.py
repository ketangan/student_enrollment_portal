# core/services/billing_stripe.py
"""
Stripe integration service — thin wrapper around the Stripe SDK.

All Stripe API calls live here so views stay thin and testable.
Never import stripe directly in views; always go through this module.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stripe SDK — no module-level cache; reads settings on each call so that
# override_settings() in tests and STRIPE_MODE changes take effect immediately.
# ---------------------------------------------------------------------------

def _get_stripe():
    """Return the stripe module configured with the secret key, or None."""
    secret = getattr(settings, "STRIPE_SECRET_KEY", "").strip()
    if not secret:
        logger.warning("STRIPE_SECRET_KEY not set — Stripe features disabled")
        return None

    try:
        import stripe

        stripe.api_key = secret
        return stripe
    except ImportError:
        logger.error("stripe package not installed — pip install stripe")
        return None


def is_stripe_configured() -> bool:
    """Return True if Stripe keys are present and SDK is available."""
    return _get_stripe() is not None and bool(
        getattr(settings, "STRIPE_PUBLISHABLE_KEY", "").strip()
    )


# ---------------------------------------------------------------------------
# Price helpers — lazy: read from Django settings at call time so that
# override_settings() works correctly in tests and STRIPE_MODE is respected.
# ---------------------------------------------------------------------------

_PRICE_SETTINGS = [
    ("STRIPE_PRICE_STARTER_MONTHLY", "starter_monthly", "Starter Monthly", "$49.99 / month", "starter", "month"),
    ("STRIPE_PRICE_STARTER_ANNUAL",  "starter_annual",  "Starter Annual",  "$499 / year",   "starter", "year"),
    ("STRIPE_PRICE_PRO_MONTHLY",     "pro_monthly",     "Pro Monthly",     "$99 / month",   "pro",     "month"),
    ("STRIPE_PRICE_PRO_ANNUAL",      "pro_annual",      "Pro Annual",      "$990 / year",   "pro",     "year"),
    ("STRIPE_PRICE_GROWTH_MONTHLY",  "growth_monthly",  "Growth Monthly",  "$199 / month",  "growth",  "month"),
    ("STRIPE_PRICE_GROWTH_ANNUAL",   "growth_annual",   "Growth Annual",   "$1,990 / year", "growth",  "year"),
]


def _price(setting_name: str) -> str:
    return getattr(settings, setting_name, "").strip()


def get_pricing_options() -> list[dict]:
    """Return pricing cards for the billing page."""
    options = []
    for setting_name, option_id, name, amount, plan, interval in _PRICE_SETTINGS:
        price_id = _price(setting_name)
        if price_id:
            options.append({
                "id": option_id,
                "price_id": price_id,
                "name": name,
                "amount": amount,
                "plan": plan,
                "interval": interval,
            })
    return options


# ---------------------------------------------------------------------------
# Map Stripe price → plan name
# ---------------------------------------------------------------------------
def price_to_plan(price_id: str) -> str | None:
    """Map a Stripe Price ID to an internal plan name, or None if unknown."""
    plan_map = {"starter": ["STRIPE_PRICE_STARTER_MONTHLY", "STRIPE_PRICE_STARTER_ANNUAL"],
                "pro":     ["STRIPE_PRICE_PRO_MONTHLY",     "STRIPE_PRICE_PRO_ANNUAL"],
                "growth":  ["STRIPE_PRICE_GROWTH_MONTHLY",  "STRIPE_PRICE_GROWTH_ANNUAL"]}
    for plan, setting_names in plan_map.items():
        for sname in setting_names:
            pid = _price(sname)
            if pid and pid == price_id:
                return plan
    return None


# ---------------------------------------------------------------------------
# Checkout Session
# ---------------------------------------------------------------------------
def create_checkout_session(
    *,
    school,
    price_id: str,
    success_url: str,
    cancel_url: str,
    customer_email: str | None = None,
) -> str | None:
    """Create a Stripe Checkout Session. Returns the session URL or None on error."""
    stripe = _get_stripe()
    if not stripe:
        return None

    params: dict = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(school.id),  # For Stripe Dashboard lookup
        "metadata": {"school_slug": school.slug, "school_id": str(school.id)},
        "subscription_data": {
            "metadata": {"school_slug": school.slug, "school_id": str(school.id)},
        },
    }

    # CRITICAL: In subscription mode, never send both customer AND customer_email
    # Stripe Checkout rules:
    # - If customer exists: use customer (email will be pulled from customer record)
    # - If no customer: optionally set customer_email (Stripe creates new customer)
    if school.stripe_customer_id:
        params["customer"] = school.stripe_customer_id
    elif customer_email:
        params["customer_email"] = customer_email

    try:
        session = stripe.checkout.Session.create(**params)
        return session.url
    except Exception:
        logger.exception("Failed to create Stripe Checkout session for %s", school.slug)
        return None


# ---------------------------------------------------------------------------
# Customer Portal
# ---------------------------------------------------------------------------
def create_portal_session(*, school, return_url: str) -> str | None:
    """Create a Stripe Billing Portal session. Returns the URL or None."""
    stripe = _get_stripe()
    if not stripe or not school.stripe_customer_id:
        return None

    try:
        session = stripe.billing_portal.Session.create(
            customer=school.stripe_customer_id,
            return_url=return_url,
        )
        return session.url
    except Exception:
        logger.exception("Failed to create Stripe portal session for %s", school.slug)
        return None


# ---------------------------------------------------------------------------
# Webhook verification
# ---------------------------------------------------------------------------
def construct_webhook_event(payload: bytes, sig_header: str) -> object | None:
    """Verify + construct a Stripe webhook event. Returns event or None."""
    stripe = _get_stripe()
    if not stripe:
        return None

    secret = getattr(settings, "STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        logger.error("STRIPE_WEBHOOK_SECRET not set — cannot verify webhook")
        return None

    try:
        return stripe.Webhook.construct_event(payload, sig_header, secret)
    except (stripe.error.SignatureVerificationError, ValueError) as e:
        logger.warning("Stripe webhook signature failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Webhook handlers — keep idempotent
# ---------------------------------------------------------------------------
def handle_checkout_completed(session_data: dict) -> None:
    """Handle checkout.session.completed — link Stripe customer + subscription to school."""
    from core.models import School

    metadata = session_data.get("metadata") or {}
    school_slug = metadata.get("school_slug")
    if not school_slug:
        logger.warning(
            "Stripe webhook checkout.session.completed: missing school_slug in metadata"
        )
        return

    customer_id = session_data.get("customer", "")
    subscription_id = session_data.get("subscription", "")

    try:
        school = School.objects.get(slug=school_slug)
    except School.DoesNotExist:
        logger.warning(
            "Stripe webhook checkout.session.completed: school %s not found",
            school_slug
        )
        return

    school.stripe_customer_id = customer_id or school.stripe_customer_id
    school.stripe_subscription_id = subscription_id or school.stripe_subscription_id

    # Default to "active" — overwritten below if we successfully fetch the real status
    subscription_status = "active"

    # Determine plan from line items
    line_items = session_data.get("line_items", {}).get("data", [])
    if line_items:
        price_id = line_items[0].get("price", {}).get("id", "")
        plan = price_to_plan(price_id)
        if plan:
            school.plan = plan

    # If no line_items in webhook (common), try to fetch from subscription
    if not line_items and subscription_id:
        try:
            stripe = _get_stripe()
            if stripe:
                sub = stripe.Subscription.retrieve(subscription_id)
                if sub and sub.get("items", {}).get("data"):
                    price_id = sub["items"]["data"][0].get("price", {}).get("id", "")
                    plan = price_to_plan(price_id)
                    if plan:
                        school.plan = plan
                subscription_status = sub.get("status", "active")
        except Exception:
            logger.exception("Failed to fetch subscription %s", subscription_id)

    school.stripe_subscription_status = subscription_status
    school.is_active = True  # Reactivate on successful checkout
    school.stripe_cancel_at = None
    school.stripe_cancel_at_period_end = False
    school.stripe_current_period_end = None

    school.save(
        update_fields=[
            "stripe_customer_id",
            "stripe_subscription_id",
            "stripe_subscription_status",
            "plan",
            "is_active",
            "stripe_cancel_at",
            "stripe_cancel_at_period_end",
            "stripe_current_period_end",
        ]
    )
    logger.info(
        "Stripe webhook checkout.session.completed: school=%s customer=%s subscription=%s plan=%s is_active=True",
        school.slug,
        customer_id,
        subscription_id,
        school.plan,
    )


def handle_subscription_updated(subscription_data: dict) -> None:
    """Handle customer.subscription.updated — sync status + plan."""
    from core.models import School

    sub_id = subscription_data.get("id", "")
    status = subscription_data.get("status", "")

    school = School.objects.filter(stripe_subscription_id=sub_id).first()
    # Fallback: sometimes early events include metadata.school_slug
    if not school:
        meta_slug = (subscription_data.get("metadata") or {}).get("school_slug")
        if meta_slug:
            school = School.objects.filter(slug=meta_slug).first()

    if not school:
        logger.warning(
            "Stripe webhook customer.subscription.updated: no school found for subscription %s",
            sub_id
        )
        return

    school.stripe_subscription_status = status

    # Determine plan from the subscription's current price
    items = subscription_data.get("items", {}).get("data", [])
    if items:
        price_id = items[0].get("price", {}).get("id", "")
        plan = price_to_plan(price_id)
        if plan:
            school.plan = plan

    # Cancellation scheduling
    cancel_at = subscription_data.get("cancel_at")
    cancel_at_period_end = subscription_data.get("cancel_at_period_end", False)
    current_period_end = None
    try:
        current_period_end = (
            items[0].get("current_period_end") if items and items[0].get("current_period_end") else subscription_data.get("current_period_end")
        )
    except Exception:
        current_period_end = subscription_data.get("current_period_end")

    def _to_dt(value):
        if not value:
            return None
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except Exception:
            return None

    school.stripe_cancel_at = _to_dt(cancel_at)
    school.stripe_cancel_at_period_end = bool(cancel_at_period_end)
    school.stripe_current_period_end = _to_dt(current_period_end)

    # If status is canceled and no active period remains, lock the school
    if status == "canceled" and not (school.stripe_cancel_at or school.stripe_cancel_at_period_end):
        school.is_active = False

    school.save(update_fields=[
        "stripe_subscription_status", "plan", "stripe_cancel_at", "stripe_cancel_at_period_end", "stripe_current_period_end", "is_active"
    ])
    logger.info(
        "Stripe webhook customer.subscription.updated: school=%s status=%s plan=%s is_active=%s",
        school.slug,
        status,
        school.plan,
        school.is_active,
    )


def handle_subscription_deleted(subscription_data: dict) -> None:
    """Handle customer.subscription.deleted — lock school, keep plan."""
    from core.models import School

    sub_id = subscription_data.get("id", "")

    school = School.objects.filter(stripe_subscription_id=sub_id).first()
    if not school:
        logger.warning(
            "Stripe webhook customer.subscription.deleted: no school found for subscription %s",
            sub_id
        )
        return

    school.stripe_subscription_status = "canceled"
    school.is_active = False  # Lock the school (Option A: no revert to trial)
    # Keep school.plan unchanged — preserves what they had
    school.stripe_cancel_at = None
    school.stripe_cancel_at_period_end = False
    school.stripe_current_period_end = None
    school.save(update_fields=[
        "stripe_subscription_status", "is_active", "stripe_cancel_at", "stripe_cancel_at_period_end", "stripe_current_period_end"
    ])
    logger.info(
        "Stripe webhook customer.subscription.deleted: school=%s plan=%s locked (is_active=False)",
        school.slug,
        school.plan,
    )
