"""Negotiated platform prices: Stripe is mocked, tenant/access checks are real."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, SchoolBillingCheckout, SchoolBillingOffer, SchoolBillingLocationChange
from core.services import school_pricing as pricing
from core.services.billing_stripe import (
    create_checkout_session, create_portal_session, handle_checkout_completed,
    handle_subscription_deleted, handle_subscription_updated,
)
from core.services.feature_flags import merge_flags
from core.tests.factories import SchoolFactory, UserFactory, SchoolAdminMembershipFactory

pytestmark = pytest.mark.django_db


def price(price_id="price_base", amount=4900, **changes):
    return {"id": price_id, "unit_amount": amount, "currency": "usd", "active": True, "livemode": False,
            "product": {"id": "prod_platform", "active": True}, "type": "recurring",
            "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"},
            "billing_scheme": "per_unit", **changes}


@pytest.fixture
def stripe(settings):
    settings.STRIPE_MODE = "test"
    settings.STRIPE_SECRET_KEY = "sk_test_local_only"
    settings.STRIPE_PUBLISHABLE_KEY = "pk_test_local_only"
    settings.STRIPE_PRICE_STARTER_MONTHLY = "price_legacy"
    sdk = MagicMock()
    sdk.Account.retrieve.return_value = {"id": "acct_platform"}
    sdk.Price.retrieve.side_effect = lambda ident, **kw: price(ident, {"price_base": 4900, "price_location": 2000, "price_upgrade": 7900}.get(ident, 3900))
    sdk.checkout.Session.create.return_value = {"id": "cs_test_local", "url": "https://checkout.stripe.com/c/pay/test_local"}
    sdk.checkout.Session.retrieve.return_value = {"status": "open"}
    sdk.billing_portal.Configuration.create.return_value = {"id": "bpc_private"}
    sdk.billing_portal.Session.create.return_value = MagicMock(url="https://billing.stripe.com/test_local")
    with patch("core.services.billing_stripe._get_stripe", return_value=sdk):
        yield sdk


@pytest.fixture
def school():
    return SchoolFactory(plan="trial", is_demo=False, is_active=True, feature_flags={"ai_summary_enabled": False})


@pytest.fixture
def owner(school):
    user = UserFactory(is_staff=True)
    SchoolAdminMembershipFactory(school=school, user=user, role="owner")
    return user


def publish(school, **changes):
    return pricing.publish_offer(school=school, actor=None, label="Agreed subscription", included_locations=1,
                                 additional_locations=changes.pop("additional_locations", 2),
                                 verified=pricing.validate_prices("price_base", "price_location", "price_upgrade"),
                                 expected_offer_id=changes.pop("expected_offer_id", ""), **changes)


@pytest.fixture
def offer(stripe, school):
    return publish(school)


def checkout(school, offer, **changes):
    return pricing.start_checkout(school=school, offer_id=offer.pk, success_url="https://example.test/billing/?status=success",
                                  cancel_url="https://example.test/billing/?status=canceled", **changes)


def subscription(attempt, **changes):
    return {"id": "sub_local", "customer": "cus_local", "livemode": False, "status": "active",
            "metadata": {"school_slug": attempt.offer.school.slug, "billing_checkout": str(attempt.pk)},
            "items": {"data": [{"id": f"si_{i}", "price": {"id": line["price"]}, "quantity": line["quantity"]}
                               for i, line in enumerate(attempt.offer.line_items)]}, **changes}


def complete(school, offer, stripe):
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.get(offer=offer)
    sub = subscription(attempt)
    stripe.Subscription.retrieve.return_value = sub
    handle_checkout_completed({"id": attempt.session_id, "metadata": sub["metadata"], "livemode": False,
                               "mode": "subscription", "subscription": sub["id"], "customer": sub["customer"]})
    school.refresh_from_db()
    return attempt, sub


def test_publish_verified_prices_and_location_total(offer, school):
    assert offer.total_cents == 8900
    assert offer.total_display == "$89.00 / month"
    assert offer.line_items == [{"price": "price_base", "quantity": 1}, {"price": "price_location", "quantity": 2}]
    assert offer.upgrade_price["amount"] == 7900
    school.refresh_from_db()
    assert school.plan == "trial"
    assert school.feature_flags == {"ai_summary_enabled": False}


@pytest.mark.parametrize("change", [
    {"active": False}, {"livemode": True}, {"product": {"active": False}}, {"product": "prod_unexpanded"},
    {"type": "one_time"}, {"recurring": None}, {"recurring": {"interval": "week", "interval_count": 1, "usage_type": "licensed"}},
    {"recurring": {"interval": "month", "interval_count": 2, "usage_type": "licensed"}},
    {"recurring": {"interval": "month", "interval_count": 1, "usage_type": "metered"}},
    {"billing_scheme": "tiered"}, {"currency": "eur"}, {"unit_amount": None}, {"unit_amount": 0},
    {"unit_amount": 49.5}, {"transform_quantity": {"divide_by": 5}}, {"currency_options": {"cad": {}}}, {"id": "price_other"},
])
def test_reject_incompatible_prices(stripe, change):
    stripe.Price.retrieve.side_effect = None
    stripe.Price.retrieve.return_value = price(**change)
    with pytest.raises(ValidationError):
        pricing.validate_prices("price_base")


@pytest.mark.parametrize("value", ["prod_123", "https://buy.stripe.com/test", "", "price_x\n", "price_<script>"])
def test_reject_not_price_id(stripe, value):
    with pytest.raises(ValidationError):
        pricing.validate_prices(value)
    stripe.Price.retrieve.assert_not_called()


def test_unavailable_stripe_and_bad_credentials(stripe, settings):
    for key, mode in [("sk_live_wrong", "test"), ("sk_test_wrong", "live"), ("", "test"), ("sk_test_key", "typo")]:
        settings.STRIPE_SECRET_KEY, settings.STRIPE_MODE = key, mode
        with pytest.raises(ValidationError):
            pricing.validate_prices("price_base")


def test_read_failures_are_safe(stripe):
    stripe.Price.retrieve.side_effect = RuntimeError("sensitive provider error")
    with pytest.raises(ValidationError, match="Nothing was saved") as exc:
        pricing.validate_prices("price_base")
    assert "sensitive" not in str(exc.value)
    stripe.Account.retrieve.return_value = {}
    with pytest.raises(ValidationError, match="account"):
        pricing.validate_prices("price_base")


def test_mismatched_intervals_duplicate_location_and_lower_upgrade(stripe):
    original = stripe.Price.retrieve.side_effect
    stripe.Price.retrieve.side_effect = lambda ident, **kw: original(ident, **kw) if ident == "price_base" else price(ident, recurring={"interval": "year", "interval_count": 1, "usage_type": "licensed"})
    with pytest.raises(ValidationError, match="same billing period"):
        pricing.validate_prices("price_base", "price_location")
    stripe.Price.retrieve.side_effect = original
    with pytest.raises(ValidationError, match="separate price"):
        pricing.validate_prices("price_base", "price_base")
    with pytest.raises(ValidationError, match="higher"):
        pricing.validate_prices("price_base", upgrade_id="price_cheap")


def test_modes_fail_closed_and_never_revert_to_env(offer, school, settings):
    settings.STRIPE_MODE = "live"
    assert pricing.uses_school_pricing(school)
    assert pricing.current_offer(school) is None
    assert create_checkout_session(school=school, price_id="price_legacy", success_url="x", cancel_url="y") is None


def test_offer_versions_stale_edits_and_location_validation(stripe, school, offer):
    with pytest.raises(ValidationError, match="another window"):
        publish(school)
    with pytest.raises(ValidationError, match="require a location price"):
        pricing.publish_offer(school=school, actor=None, label="Bad", included_locations=1, additional_locations=1,
                              expected_offer_id=str(offer.pk), verified=pricing.validate_prices("price_base"))
    replacement = publish(school, expected_offer_id=str(offer.pk), additional_locations=0)
    offer.refresh_from_db()
    assert not offer.is_current
    assert replacement.line_items == [{"price": "price_base", "quantity": 1}]
    assert SchoolBillingOffer.objects.count() == 2


def test_checkout_ignores_client_prices_and_retries_identically(stripe, school, offer):
    url = checkout(school, offer, customer_email="owner@example.test")
    args = deepcopy(stripe.checkout.Session.create.call_args.kwargs)
    assert args["line_items"] == offer.line_items
    assert args["customer_email"] == "owner@example.test"
    assert "customer" not in args
    assert "trial_period_days" not in args["subscription_data"]
    assert args["allow_promotion_codes"] is False
    assert url.startswith("https://checkout.stripe.com/")
    assert checkout(school, offer, customer_email="changed@example.test") == url
    assert stripe.checkout.Session.create.call_args.kwargs == args
    assert SchoolBillingCheckout.objects.count() == 1
    assert publish(school, expected_offer_id=str(offer.pk)).pk == offer.pk
    assert SchoolBillingOffer.objects.filter(school=school).count() == 1
    with pytest.raises(ValidationError, match="Checkout .* scheduled expiry"):
        publish(school, expected_offer_id=str(offer.pk), additional_locations=3)


def test_timeout_preserves_retry_key(stripe, school, offer):
    stripe.checkout.Session.create.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        checkout(school, offer)
    original = stripe.checkout.Session.create.call_args.kwargs
    assert SchoolBillingCheckout.objects.count() == 1
    stripe.checkout.Session.create.side_effect = None
    checkout(school, offer)
    assert original == stripe.checkout.Session.create.call_args.kwargs


def test_expired_checkout_new_attempt_but_completed_waits(stripe, school, offer):
    checkout(school, offer)
    stripe.checkout.Session.retrieve.return_value = recovery_session(SchoolBillingCheckout.objects.get(offer=offer), status="complete")
    with pytest.raises(ValidationError, match="confirmation is still pending"):
        checkout(school, offer)
    stripe.checkout.Session.retrieve.return_value = {"status": "expired"}
    checkout(school, offer)
    assert SchoolBillingCheckout.objects.count() == 2
    assert SchoolBillingCheckout.objects.filter(state="expired").count() == 1


def recovery_session(attempt, **changes):
    return {"id": attempt.session_id, "livemode": False, "mode": "subscription",
            "metadata": {"billing_checkout": str(attempt.pk)}, "status": "open",
            "payment_status": "unpaid", "url": "https://checkout.stripe.com/c/pay/resume", **changes}


def test_recovery_resume_and_expire(stripe, school, offer):
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.get(offer=offer)
    stripe.checkout.Session.retrieve.return_value = recovery_session(attempt)
    stripe.checkout.Session.create.reset_mock()
    assert checkout(school, offer).endswith("/resume")
    stripe.checkout.Session.create.assert_not_called()
    assert "still open" in pricing.recover_checkout(school=school, checkout_id=attempt.pk)
    stripe.checkout.Session.expire.assert_not_called()
    stripe.checkout.Session.retrieve.side_effect = [recovery_session(attempt), recovery_session(attempt, status="expired")]
    assert "Checkout expired" in pricing.recover_checkout(school=school, checkout_id=attempt.pk, expire=True)
    stripe.checkout.Session.expire.assert_called_once_with(attempt.session_id, api_key="sk_test_local_only")
    attempt.refresh_from_db()
    assert attempt.state == "expired"
    assert "already been resolved" in pricing.recover_checkout(school=school, checkout_id=attempt.pk, expire=True)


@pytest.mark.parametrize("race", [False, True])
def test_recovery_completed_payment_and_expiration_race(stripe, school, offer, race):
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.get(offer=offer)
    sub = subscription(attempt)
    stripe.Subscription.retrieve.return_value = sub
    completed = recovery_session(attempt, status="complete", subscription=sub["id"], customer=sub["customer"])
    stripe.checkout.Session.retrieve.side_effect = [recovery_session(attempt), completed] if race else [completed]
    stripe.checkout.Session.expire.side_effect = RuntimeError("already completed")
    assert "Subscription confirmed" in pricing.recover_checkout(school=school, checkout_id=attempt.pk, expire=True)
    school.refresh_from_db()
    attempt.refresh_from_db()
    assert school.stripe_subscription_id == sub["id"]
    assert attempt.state == "complete"
    if not race:
        stripe.checkout.Session.expire.assert_not_called()


def test_customer_retry_reconciles_completed_checkout(stripe, school, offer):
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.get(offer=offer)
    sub = subscription(attempt)
    stripe.Subscription.retrieve.return_value = sub
    stripe.checkout.Session.retrieve.return_value = recovery_session(
        attempt, status="complete", subscription=sub["id"], customer=sub["customer"])
    stripe.checkout.Session.create.reset_mock()
    assert checkout(school, offer) == "https://example.test/billing/?status=success"
    stripe.checkout.Session.create.assert_not_called()
    attempt.refresh_from_db()
    assert attempt.state == "complete"


@pytest.mark.parametrize("problem", ["account", "mode", "metadata", "session", "network", "missing", "paid", "unknown", "expire_error"])
def test_recovery_failures_keep_pending(stripe, school, offer, problem):
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.get(offer=offer)
    session = recovery_session(attempt)
    if problem == "account":
        stripe.Account.retrieve.return_value = {"id": "acct_other"}
    elif problem == "mode":
        session["livemode"] = True
    elif problem == "metadata":
        session["metadata"] = {}
    elif problem == "session":
        session["id"] = "cs_other"
    elif problem == "network":
        stripe.checkout.Session.retrieve.side_effect = RuntimeError("network")
    elif problem == "missing":
        attempt.session_id = ""
        attempt.save()
    elif problem == "paid":
        session["payment_status"] = "paid"
    elif problem == "unknown":
        session["status"] = "unknown"
    elif problem == "expire_error":
        stripe.checkout.Session.expire.side_effect = RuntimeError("network")
    stripe.checkout.Session.retrieve.return_value = session
    with pytest.raises(ValidationError):
        pricing.recover_checkout(school=school, checkout_id=attempt.pk, expire=True)
    attempt.refresh_from_db()
    assert attempt.state == "pending"
    if problem != "expire_error":
        stripe.checkout.Session.expire.assert_not_called()


def test_recovery_operator_scope_permissions_and_csrf(client, stripe, school, offer, owner, settings):
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.get(offer=offer)
    stripe.checkout.Session.retrieve.return_value = recovery_session(attempt, status="expired")
    url = reverse("ops_school_detail", args=[school.slug])
    data = {"action": "check_checkout", "checkout_id": str(attempt.pk)}
    client.force_login(owner)
    assert client.post(url, data).status_code == 302
    stripe.checkout.Session.retrieve.assert_not_called()
    admin = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(admin)
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(admin)
    assert csrf_client.post(url, data).status_code == 403
    for other_id in ("invalid", str(attempt.pk)):
        with pytest.raises(ValidationError, match="not found"):
            pricing.recover_checkout(school=SchoolFactory(), checkout_id=other_id)
    settings.STRIPE_MODE = "live"
    with pytest.raises(ValidationError, match="not found"):
        pricing.recover_checkout(school=school, checkout_id=attempt.pk)
    settings.STRIPE_MODE = "test"
    original_name = school.display_name
    assert client.post(url, data).status_code == 302
    attempt.refresh_from_db()
    school.refresh_from_db()
    assert attempt.state == "expired"
    assert school.display_name == original_name
    assert AdminAuditLog.objects.filter(extra__action="check_checkout").exists()


def test_unknown_timeout_never_restarts_after_expiry(stripe, school, offer):
    attempt = SchoolBillingCheckout.objects.create(offer=offer, expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(ValidationError, match="reconcile"):
        checkout(school, offer)
    assert SchoolBillingCheckout.objects.get(pk=attempt.pk).state == "pending"
    stripe.checkout.Session.create.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"is_demo": True}, {"trial_requires_activation": True},
    {"stripe_subscription_id": "sub_existing", "stripe_subscription_status": "incomplete"},
    {"payment_grace_ends_at": timezone.now() + timedelta(days=3)},
])
def test_checkout_guards(stripe, school, offer, changes):
    for attr, value in changes.items():
        setattr(school, attr, value)
    school.save()
    with pytest.raises(ValidationError, match="existing subscription or trial"):
        checkout(school, offer)
    stripe.checkout.Session.create.assert_not_called()


def test_wrong_school_offer_missing_mode_and_changed_account(stripe, school, offer, settings):
    other = SchoolFactory()
    with pytest.raises(ValidationError, match="unavailable"):
        checkout(other, offer)
    stripe.Account.retrieve.return_value = {"id": "acct_wrong"}
    with pytest.raises(ValidationError, match="no longer matches"):
        checkout(school, offer)
    stripe.checkout.Session.create.assert_not_called()


def test_invalid_checkout_response(stripe, school, offer):
    stripe.checkout.Session.create.return_value = {"id": "cs_test", "url": "https://malicious.example/"}
    with pytest.raises(ValidationError, match="valid checkout"):
        checkout(school, offer)


def test_success_preserves_access_and_links_historical_offer(stripe, school, offer):
    flags = merge_flags(plan=school.plan, overrides=school.feature_flags)
    attempt, sub = complete(school, offer, stripe)
    assert school.plan == "custom"
    assert school.stripe_subscription_id == "sub_local"
    assert school.has_active_stripe_subscription
    assert merge_flags(plan=school.plan, overrides=school.feature_flags) == flags
    assert school.feature_flags["ai_summary_enabled"] is False
    assert SchoolBillingCheckout.objects.get(pk=attempt.pk).state == "complete"
    replacement = publish(school, expected_offer_id=str(offer.pk), additional_locations=0)
    handle_subscription_updated(sub)
    school.refresh_from_db()
    assert school.has_active_stripe_subscription
    assert attempt.offer_id != replacement.pk


@pytest.mark.parametrize("change", [
    {"livemode": True}, {"customer": ""}, {"id": ""}, {"status": "unknown"}, {"metadata": {}},
    {"items": {"data": []}}, {"items": {"data": [], "has_more": True}},
])
def test_invalid_custom_subscriptions_cannot_activate(stripe, school, offer, change):
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.get(offer=offer)
    assert not pricing.apply_subscription(school, subscription(attempt, **change))
    school.refresh_from_db()
    assert school.plan == "trial"


def test_other_school_metadata_and_second_subscription_rejected(stripe, school, offer):
    attempt, sub = complete(school, offer, stripe)
    assert not pricing.apply_subscription(SchoolFactory(), sub)
    sub["id"] = "sub_intruder"
    assert not pricing.apply_subscription(school, sub)


def test_order_independent_items_and_current_state_wins(stripe, school, offer):
    attempt, sub = complete(school, offer, stripe)
    current = deepcopy(sub)
    current["status"] = "canceled"
    current["items"]["data"].reverse()
    stripe.Subscription.retrieve.return_value = current
    handle_subscription_updated(sub)  # Late 'active' event must not reopen a canceled account.
    school.refresh_from_db()
    assert school.stripe_subscription_status == "canceled"
    assert not school.is_active


def test_stale_checkout_cannot_replace_active_subscription(stripe, school, offer):
    attempt, sub = complete(school, offer, stripe)
    school.stripe_subscription_id = "sub_newer"
    school.save()
    assert not pricing.apply_subscription(school, sub)
    for change in [{"livemode": True}, {"id": "cs_other"}, {"mode": "payment"}, {"subscription": ""}, {"metadata": {}}]:
        pricing.reconcile_checkout(school, {"id": attempt.session_id, "metadata": sub["metadata"], "livemode": False,
                                          "mode": "subscription", "subscription": sub["id"], "customer": sub["customer"], **change})
    school.refresh_from_db()
    assert school.stripe_subscription_id == "sub_newer"


def test_existing_subscription_unaffected_by_future_agreement(stripe, school, offer, settings):
    school.plan = "starter"
    school.stripe_subscription_id = "sub_legacy"
    school.stripe_customer_id = "cus_legacy"
    school.save()
    handle_subscription_updated({"id": "sub_legacy", "status": "active", "items": {"data": [{"price": {"id": "price_legacy"}}]}})
    school.refresh_from_db()
    assert school.plan == "starter"
    assert school.has_active_stripe_subscription
    handle_subscription_deleted({"id": "sub_legacy"})
    school.refresh_from_db()
    assert not school.is_active


def test_active_customer_keeps_accepted_locations_after_new_offer(client, stripe, school, offer, owner):
    complete(school, offer, stripe)
    publish(school, expected_offer_id=str(offer.pk), additional_locations=3)
    client.force_login(owner)
    response = client.get(reverse("school_billing", args=[school.slug]))
    assert response.status_code == 200
    assert response.context["agreed_offer"].pk == offer.pk
    assert b"$89.00 / month" in response.content
    assert b"$109.00 / month" not in response.content
    assert b"Continue to secure checkout" not in response.content
    assert (reverse("school_settings", args=[school.slug]) + "?tab=billing").encode() in response.content
    assert b"Back to settings" in response.content
    stripe.Subscription.modify.assert_not_called()


@pytest.fixture
def location_update(stripe, school, offer):
    attempt, sub = complete(school, offer, stripe)
    attempt.refresh_from_db()
    sub["collection_method"] = "charge_automatically"
    sub["billing_cycle_anchor"] = int(timezone.now().timestamp())
    sub["current_period_end"] = int((timezone.now() + timedelta(days=20)).timestamp())
    handle_subscription_updated(sub)
    school.refresh_from_db()
    new_offer = publish(school, expected_offer_id=str(offer.pk), additional_locations=3)
    def modify(sub_id, **params):
        assert sub_id == sub["id"]
        for update in params["items"]:
            if update.get("id"):
                item = next(item for item in sub["items"]["data"] if item["id"] == update["id"])
                item["quantity"] = update["quantity"]
                if "price" in update:
                    item["price"] = {"id": update["price"]}
            else:
                sub["items"]["data"].append({"id": "si_added", "price": {"id": update["price"]}, "quantity": update["quantity"]})
        sub["metadata"].update(params["metadata"])
        return sub
    stripe.Subscription.modify.side_effect = modify
    stripe.Subscription.retrieve.return_value = sub
    return new_offer, sub, attempt


def test_location_increase_no_proration_same_subscription_and_renewal(client, stripe, school, owner, location_update):
    new_offer, sub, attempt = location_update
    renewal = sub["current_period_end"]
    result = pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None)
    assert result.state == "applied"
    assert int(result.effective_at.timestamp()) == renewal
    args = stripe.Subscription.modify.call_args.kwargs
    assert args["items"] == [{"id": "si_1", "quantity": 3}]
    assert args["proration_behavior"] == "none"
    assert "billing_cycle_anchor" not in args and "trial_end" not in args
    stripe.Subscription.create.assert_not_called()
    stripe.Invoice.create.assert_not_called()
    assert pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None).pk == result.pk
    assert stripe.Subscription.modify.call_count == 1
    client.force_login(owner)
    response = client.get(reverse("school_billing", args=[school.slug]))
    assert b"$109.00 / month" in response.content and b"No charge today" in response.content
    assert b"Continue to secure checkout" not in response.content
    assert response.context["agreed_offer"] == new_offer
    # Renewal and failure events still accept the revised quantity through the existing handler.
    sub["status"] = "past_due"
    handle_subscription_updated(sub)
    school.refresh_from_db()
    assert school.stripe_subscription_status == "past_due"
    assert school.stripe_subscription_id == attempt.subscription_id


def test_first_extra_location_adds_item_without_replacing_base(stripe, school, offer, location_update):
    new_offer, sub, attempt = location_update
    offer.additional_locations = 0
    offer.save(update_fields=["additional_locations"])
    sub["items"]["data"] = sub["items"]["data"][:1]
    result = pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None)
    assert result.state == "applied"
    assert stripe.Subscription.modify.call_args.kwargs["items"] == [{"price": "price_location", "quantity": 3}]
    assert sub["items"]["data"][0]["quantity"] == 1


@pytest.mark.parametrize("problem", ["school", "mode", "account", "included", "decrease", "past_due", "cancel", "schedule", "pause", "pending", "threshold", "items", "renewal", "expired", "customer", "metadata", "collection"])
def test_location_changes_fail_closed(stripe, school, offer, location_update, problem, settings):
    new_offer, sub, attempt = location_update
    target = school
    if problem == "school":
        target = SchoolFactory()
    elif problem == "mode":
        sub["livemode"] = True
    elif problem == "account":
        stripe.Account.retrieve.return_value = {"id": "acct_other"}
    elif problem == "included":
        new_offer.included_locations = 2
        new_offer.save()
    elif problem == "decrease":
        new_offer.additional_locations = 1
        new_offer.save()
    elif problem == "past_due":
        sub["status"] = "past_due"
    elif problem == "cancel":
        sub["cancel_at_period_end"] = True
    elif problem == "schedule":
        sub["schedule"] = "sub_sched_external"
    elif problem == "pause":
        sub["pause_collection"] = {"behavior": "void"}
    elif problem == "pending":
        sub["pending_update"] = {"expires_at": 999}
    elif problem == "threshold":
        sub["items"]["data"][0]["billing_thresholds"] = {"usage_gte": 10}
    elif problem == "items":
        sub["items"]["data"][0]["quantity"] = 2
    elif problem == "renewal":
        sub.pop("current_period_end")
    elif problem == "expired":
        sub["current_period_end"] = 1
    elif problem == "customer":
        sub["customer"] = "cus_other"
    elif problem == "metadata":
        sub["metadata"] = {}
    elif problem == "collection":
        sub["collection_method"] = "send_invoice"
    with pytest.raises(ValidationError):
        pricing.apply_billing_at_renewal(school=target, offer_id=new_offer.pk, actor=None)
    stripe.Subscription.modify.assert_not_called()
    assert not SchoolBillingLocationChange.objects.exists()


@pytest.mark.parametrize("change_base", [False, True])
def test_location_timeout_retries_same_identity_and_blocks_offer_replacement(stripe, school, location_update, change_base):
    new_offer, sub, attempt = location_update
    if change_base:
        new_offer.base_price = pricing.validate_prices("price_upgrade")["base_price"]
        new_offer.save()
    original_modify = stripe.Subscription.modify.side_effect
    stripe.Subscription.modify.side_effect = RuntimeError("timeout")
    with pytest.raises(ValidationError, match="could not be confirmed"):
        pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None)
    change = SchoolBillingLocationChange.objects.get()
    original = stripe.Subscription.modify.call_args
    with pytest.raises(ValidationError, match="pending billing update"):
        publish(school, expected_offer_id=str(new_offer.pk), additional_locations=4)
    stripe.Subscription.modify.side_effect = original_modify
    assert pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None).pk == change.pk
    assert stripe.Subscription.modify.call_args == original
    assert SchoolBillingLocationChange.objects.count() == 1


@pytest.mark.parametrize("change_base", [False, True])
def test_location_timeout_after_stripe_applied_reconciles_without_rewrite(stripe, school, location_update, change_base):
    new_offer, sub, attempt = location_update
    if change_base:
        new_offer.base_price = pricing.validate_prices("price_upgrade")["base_price"]
        new_offer.save()
    original_modify = stripe.Subscription.modify.side_effect
    def timeout_after_update(*args, **kwargs):
        original_modify(*args, **kwargs)
        raise RuntimeError("timeout")
    stripe.Subscription.modify.side_effect = timeout_after_update
    with pytest.raises(ValidationError):
        pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None)
    assert pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None).state == "applied"
    assert stripe.Subscription.modify.call_count == 1


def test_location_update_operator_only_and_audited(client, stripe, school, owner, location_update):
    new_offer, sub, attempt = location_update
    url = reverse("ops_school_detail", args=[school.slug])
    data = {"action": "apply_billing", "offer_id": str(new_offer.pk)}
    client.force_login(owner)
    client.post(url, data)
    stripe.Subscription.modify.assert_not_called()
    admin = UserFactory(is_superuser=True, is_staff=True)
    client.force_login(admin)
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(admin)
    assert csrf.post(url, data).status_code == 403
    response = client.get(url)
    assert b"Apply billing changes at next renewal" in response.content
    data["billing_review"] = response.context["subscription_pricing"]["billing_review"]
    assert client.post(url, data).status_code == 302
    assert AdminAuditLog.objects.filter(extra__action="subscription_billing_updated").exists()
    assert b"Confirmed with Stripe" in client.get(url).content
    assert b"Apply billing changes at next renewal" not in client.get(url).content


@pytest.mark.parametrize("base_id,amount,locations", [("price_upgrade", 7900, 2), ("price_upgrade", 7900, 3), ("price_lower", 3900, 2)])
def test_base_price_replaces_existing_item_without_charging(client, stripe, school, offer, owner, location_update, base_id, amount, locations):
    new_offer, sub, attempt = location_update
    new_offer.base_price = pricing.validate_prices(base_id)["base_price"]
    new_offer.additional_locations = locations
    new_offer.save()
    original_anchor = sub["billing_cycle_anchor"]
    original_items = [item["id"] for item in sub["items"]["data"]]
    client.force_login(owner)
    assert client.get(reverse("school_billing", args=[school.slug])).context["agreed_offer"].pk == offer.pk
    stripe.Subscription.modify.assert_not_called()
    result = pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None,
                                             reviewed_offer_id=offer.pk, reviewed_renewal=sub["current_period_end"])
    assert result.state == "applied"
    params = stripe.Subscription.modify.call_args.kwargs
    assert params["items"][0] == {"id": "si_0", "price": base_id, "quantity": 1}
    assert len(params["items"]) == (2 if locations == 3 else 1)
    assert params["proration_behavior"] == "none"
    assert "billing_cycle_anchor" not in params and "trial_end" not in params
    assert sub["billing_cycle_anchor"] == original_anchor
    assert [item["id"] for item in sub["items"]["data"]] == original_items
    stripe.Subscription.create.assert_not_called()
    stripe.Invoice.create.assert_not_called()
    response = client.get(reverse("school_billing", args=[school.slug]))
    assert response.context["agreed_offer"].total_cents == amount + locations * 2000
    assert b"Updated rate starts at your next renewal" in response.content
    assert pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None).pk == result.pk
    assert stripe.Subscription.modify.call_count == 1
    sub["status"] = "past_due"
    handle_subscription_updated(sub)
    school.refresh_from_db()
    assert school.stripe_subscription_status == "past_due"


@pytest.mark.parametrize("interval", ["month", "year"])
def test_billing_frequency_cannot_change(stripe, school, offer, location_update, interval):
    new_offer, sub, attempt = location_update
    offer.base_price["interval"] = interval
    offer.save(update_fields=["base_price"])
    other = "year" if interval == "month" else "month"
    original = stripe.Price.retrieve.side_effect
    stripe.Price.retrieve.side_effect = lambda ident, **kw: {**original(ident, **kw), "recurring": {"interval": other, "interval_count": 1, "usage_type": "licensed"}}
    verified = pricing.validate_prices("price_upgrade", "price_location")
    new_offer.base_price, new_offer.location_price = verified["base_price"], verified["location_price"]
    new_offer.save()
    with pytest.raises(ValidationError, match="same billing frequency"):
        pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None)
    stripe.Subscription.modify.assert_not_called()


def test_annual_base_price_change_preserves_annual_billing(stripe, school, offer, location_update):
    new_offer, sub, attempt = location_update
    original = stripe.Price.retrieve.side_effect
    stripe.Price.retrieve.side_effect = lambda ident, **kw: {**original(ident, **kw), "recurring": {"interval": "year", "interval_count": 1, "usage_type": "licensed"}}
    verified = pricing.validate_prices("price_base", "price_location")
    offer.base_price, offer.location_price = verified["base_price"], verified["location_price"]
    offer.save(update_fields=["base_price", "location_price"])
    verified = pricing.validate_prices("price_upgrade", "price_location")
    new_offer.base_price, new_offer.location_price = verified["base_price"], verified["location_price"]
    new_offer.additional_locations = offer.additional_locations
    new_offer.save()
    sub["current_period_end"] = int((timezone.now() + timedelta(days=300)).timestamp())
    change = pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None)
    assert change.state == "applied"
    assert int(change.effective_at.timestamp()) == sub["current_period_end"]
    assert change.offer.total_display == "$119.00 / year"
    assert stripe.Subscription.modify.call_args.kwargs["items"] == [{"id": "si_0", "price": "price_upgrade", "quantity": 1}]


@pytest.mark.parametrize("problem", ["accepted", "renewal", "no_change", "location_rate"])
def test_billing_review_stale_or_unsupported_fails_closed(stripe, school, offer, location_update, problem):
    new_offer, sub, attempt = location_update
    reviewed_id, reviewed_renewal = offer.pk, sub["current_period_end"]
    if problem == "accepted":
        reviewed_id = -1
    elif problem == "renewal":
        reviewed_renewal -= 1
    elif problem == "no_change":
        new_offer.additional_locations = offer.additional_locations
        new_offer.save()
    else:
        new_offer.location_price = pricing.validate_prices("price_other")["base_price"]
        new_offer.save()
    with pytest.raises(ValidationError):
        pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None,
                                        reviewed_offer_id=reviewed_id, reviewed_renewal=reviewed_renewal)
    stripe.Subscription.modify.assert_not_called()
    assert not SchoolBillingLocationChange.objects.exists()


@pytest.mark.parametrize("problem", ["missing", "tampered", "school", "mode", "offer", "expired"])
def test_operator_billing_change_requires_valid_review(client, stripe, school, location_update, problem):
    from django.core import signing
    new_offer, sub, attempt = location_update
    client.force_login(UserFactory(is_superuser=True, is_staff=True))
    url = reverse("ops_school_detail", args=[school.slug])
    token = client.get(url).context["subscription_pricing"]["billing_review"]
    if problem == "missing":
        token = ""
    elif problem == "tampered":
        token += "x"
    elif problem in {"school", "mode", "offer"}:
        payload = signing.loads(token, salt="school-billing-change")
        payload[problem] = "other"
        token = signing.dumps(payload, salt="school-billing-change")
    elif problem == "expired":
        payload = signing.loads(token, salt="school-billing-change")
        with patch("django.core.signing.time.time", return_value=1):
            token = signing.dumps(payload, salt="school-billing-change")
    response = client.post(url, {"action": "apply_billing", "offer_id": new_offer.pk, "billing_review": token}, follow=True)
    assert response.status_code == 200
    stripe.Subscription.modify.assert_not_called()


@pytest.mark.parametrize("problem", ["interval", "renewal"])
def test_operator_unsupported_review_shows_reason(client, stripe, school, location_update, problem):
    new_offer, sub, attempt = location_update
    if problem == "interval":
        new_offer.base_price["interval"] = "year"
        new_offer.save()
    else:
        school.stripe_current_period_end = None
        school.save()
    client.force_login(UserFactory(is_superuser=True, is_staff=True))
    response = client.get(reverse("ops_school_detail", args=[school.slug]))
    assert response.context["subscription_pricing"]["change_error"]
    assert b"Apply billing changes at next renewal" not in response.content


def test_legacy_subscription_not_eligible_for_location_updates(stripe, school, offer):
    school.stripe_subscription_id = "sub_legacy"
    school.stripe_subscription_status = "active"
    school.save()
    with pytest.raises(ValidationError, match="Legacy subscriptions"):
        pricing.apply_billing_at_renewal(school=school, offer_id=offer.pk, actor=None)
    stripe.Subscription.modify.assert_not_called()


@pytest.mark.parametrize("problem", ["cancel", "items", "expired", "unconfirmed", "collection", "pending_update", "threshold", "renewal"])
def test_pending_location_change_does_not_overwrite_new_provider_state(stripe, school, location_update, problem):
    new_offer, sub, attempt = location_update
    stripe.Subscription.modify.side_effect = RuntimeError("timeout")
    with pytest.raises(ValidationError):
        pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None)
    stripe.Subscription.modify.reset_mock()
    if problem == "cancel":
        sub["cancel_at_period_end"] = True
    elif problem == "items":
        sub["items"]["data"][1]["quantity"] = 9
    elif problem == "expired":
        SchoolBillingLocationChange.objects.update(effective_at=timezone.now()-timedelta(seconds=1))
    elif problem == "collection":
        sub["collection_method"] = "send_invoice"
    elif problem == "pending_update":
        sub["pending_update"] = {"expires_at": 999}
    elif problem == "threshold":
        sub["items"]["data"][0]["billing_thresholds"] = {"usage_gte": 10}
    elif problem == "renewal":
        sub["current_period_end"] += 86400
    elif problem == "unconfirmed":
        stripe.Subscription.modify.side_effect = None
        stripe.Subscription.modify.return_value = sub
    with pytest.raises(ValidationError):
        pricing.apply_billing_at_renewal(school=school, offer_id=new_offer.pk, actor=None)
    assert SchoolBillingLocationChange.objects.get().state == "pending"
    if problem != "unconfirmed":
        stripe.Subscription.modify.assert_not_called()


def test_unknown_location_change_metadata_is_not_applied(stripe, school, location_update):
    new_offer, sub, attempt = location_update
    sub["metadata"]["billing_location_change"] = "unknown"
    assert not pricing.apply_subscription(school, sub)


def test_dedicated_portal_does_not_modify_default_configuration(stripe, school, offer):
    complete(school, offer, stripe)
    assert create_portal_session(school=school, return_url="https://example.test/")
    args = stripe.billing_portal.Session.create.call_args.kwargs
    assert args["configuration"] == "bpc_private"
    features = stripe.billing_portal.Configuration.create.call_args.kwargs["features"]
    assert features["subscription_update"]["enabled"] is False
    assert features["payment_method_update"]["enabled"] is True
    assert create_portal_session(school=school, return_url="https://example.test/")
    assert stripe.billing_portal.Configuration.create.call_count == 1
    stripe.Account.retrieve.return_value = {"id": "acct_wrong"}
    assert create_portal_session(school=school, return_url="https://example.test/") is None


def test_operator_permissions_validation_publish_and_no_school_edit(client, stripe, school, owner):
    url = reverse("ops_school_detail", args=[school.slug])
    assert client.get(url).status_code == 302
    client.force_login(owner)
    assert client.post(url, {"action": "publish"}).status_code == 302
    assert not SchoolBillingOffer.objects.exists()
    admin = UserFactory(is_superuser=True, is_staff=True)
    client.force_login(admin)
    assert client.get(url).status_code == 200
    stripe.Price.retrieve.assert_not_called()
    old_school = (school.plan, school.trial_end_date, school.feature_flags, school.display_name)
    legacy = client.get(reverse("ops_school_pricing", args=[school.slug]))
    assert legacy.url == url + "#subscription-pricing"
    data = {"label": "Negotiated", "base_price_id": "price_base", "included_locations": 1,
            "location_price_id": "price_location", "additional_locations": 2, "upgrade_price_id": "price_upgrade",
            "expected_offer_id": "", "action": "validate"}
    response = client.post(url, data)
    assert response.status_code == 200
    assert b"$89.00 / month" in response.content
    assert not SchoolBillingOffer.objects.exists()
    data["verification"] = response.context["subscription_pricing"]["form"]["verification"].value()
    data["action"] = "publish"
    assert client.post(url, data).status_code == 302
    offer = SchoolBillingOffer.objects.get()
    assert offer.created_by == admin
    assert offer.upgrade_price == {}
    school.refresh_from_db()
    assert (school.plan, school.trial_end_date, school.feature_flags, school.display_name) == old_school
    assert b"Future upgrade Price ID" not in client.get(url).content
    assert AdminAuditLog.objects.filter(extra__action="subscription_pricing_published").exists()
    assert client.get(url).status_code == 200
    assert b"another window" in client.post(url, data).content


def test_operator_form_errors_and_provider_errors(client, stripe, school):
    client.force_login(UserFactory(is_superuser=True, is_staff=True))
    url = reverse("ops_school_detail", args=[school.slug])
    data = {"label": "New", "base_price_id": "price_base", "included_locations": 1, "additional_locations": 2, "action": "validate"}
    assert b"Enter a price" in client.post(url, data).content
    data["additional_locations"] = 0
    stripe.Price.retrieve.side_effect = RuntimeError()
    assert b"Could not verify" in client.post(url, data).content
    assert not SchoolBillingOffer.objects.exists()


def test_school_view_no_global_prices_and_scoped_checkout(client, stripe, school, offer, owner):
    client.force_login(owner)
    url = reverse("school_billing", args=[school.slug])
    response = client.get(url)
    assert response.status_code == 200
    assert b"$89.00 / month" in response.content
    assert b"Your agreed subscription" in response.content
    assert b"Agreed future upgrade" not in response.content
    assert b"Contact Pontora to activate" not in response.content
    assert b"price_legacy" not in response.content
    assert b"Every current Pontora feature unlocked" not in response.content
    endpoint = reverse("school_billing_checkout", args=[school.slug])
    assert client.post(endpoint, {"price_id": "price_legacy"}).status_code == 302
    stripe.checkout.Session.create.assert_not_called()
    response = client.post(endpoint, {"offer_id": offer.pk, "quantity": 0, "price_id": "price_intruder"})
    assert response.url.startswith("https://checkout.stripe.com/")
    assert stripe.checkout.Session.create.call_args.kwargs["line_items"] == offer.line_items
    other = SchoolFactory()
    assert client.post(reverse("school_billing_checkout", args=[other.slug]), {"offer_id": offer.pk}).status_code == 404


def test_school_active_subscription_uses_original_offer(client, stripe, school, offer, owner):
    complete(school, offer, stripe)
    publish(school, expected_offer_id=str(offer.pk), additional_locations=0)
    client.force_login(owner)
    response = client.get(reverse("school_billing", args=[school.slug]))
    assert b"$89.00 / month" in response.content
    assert b"Continue to secure checkout" not in response.content


def test_legacy_billing_pages_cannot_bypass_negotiated_prices(client, stripe, school, offer, owner):
    client.force_login(owner)
    assert client.get(reverse("admin:billing")).url == reverse("school_billing", args=[school.slug])
    response = client.post(reverse("admin:billing_create_checkout"), {"price_id": "price_legacy"})
    assert response.url == reverse("school_billing", args=[school.slug])
    stripe.checkout.Session.create.assert_not_called()


def test_csrf_and_non_owner_cannot_purchase(stripe, school, offer, owner):
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner)
    assert client.post(reverse("school_billing_checkout", args=[school.slug]), {"offer_id": offer.pk}).status_code == 403
    viewer = UserFactory(is_staff=True)
    SchoolAdminMembershipFactory(school=school, user=viewer, role="viewer")
    client = Client()
    client.force_login(viewer)
    assert client.post(reverse("school_billing_checkout", args=[school.slug]), {"offer_id": offer.pk}).status_code == 404
    stripe.checkout.Session.create.assert_not_called()


def test_signed_validation_cannot_be_reused_after_edit(client, stripe, school):
    client.force_login(UserFactory(is_staff=True, is_superuser=True))
    url = reverse("ops_school_pricing", args=[school.slug])
    data = {"label": "Agreed rate", "base_price_id": "price_base", "included_locations": 1,
            "additional_locations": 0, "action": "validate"}
    response = client.post(url, data)
    data.update(verification=response.context["subscription_pricing"]["form"]["verification"].value(), action="publish", base_price_id="price_cheap")
    assert b"Validate prices again" in client.post(url, data).content
    assert not SchoolBillingOffer.objects.exists()
    data["verification"] = "forged"
    assert b"Validate prices again" in client.post(url, data).content


def test_pending_checkout_publication_recovery(stripe, school, offer):
    checkout(school, offer)
    stripe.checkout.Session.retrieve.side_effect = TimeoutError()
    with pytest.raises(ValidationError, match="No agreement was changed"):
        publish(school, expected_offer_id=str(offer.pk), additional_locations=3)
    stripe.checkout.Session.retrieve.side_effect = None
    stripe.checkout.Session.retrieve.return_value = {"status": "expired"}
    replacement = publish(school, expected_offer_id=str(offer.pk), additional_locations=3)
    assert replacement.pk != offer.pk
    assert SchoolBillingCheckout.objects.get(offer=offer).state == "expired"


def test_billing_view_safe_errors_and_missing_mode(client, stripe, school, offer, owner, settings):
    client.force_login(owner)
    stripe.checkout.Session.create.side_effect = RuntimeError("secret")
    url = reverse("school_billing_checkout", args=[school.slug])
    response = client.post(url, {"offer_id": offer.pk}, follow=True)
    assert b"Checkout could not be confirmed" in response.content
    assert b"secret" not in response.content
    settings.STRIPE_MODE = "live"
    response = client.get(reverse("school_billing", args=[school.slug]))
    assert b"Standard plan prices will not be substituted" in response.content
    assert b"price_legacy" not in response.content


def test_scheduling_only_owner_can_use_negotiated_billing(client, stripe, school, offer):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    user = UserFactory(is_staff=False)
    SchedulingMembership.objects.create(profile=profile, user=user, role="owner")
    client.force_login(user)
    response = client.get(reverse("school_billing", args=[school.slug]))
    assert response.status_code == 200
    assert b"$89.00 / month" in response.content
    response = client.post(reverse("school_billing_checkout", args=[school.slug]), {"offer_id": offer.pk})
    assert response.url.startswith("https://checkout.stripe.com/")


def test_resubscribe_after_cancellation_does_not_accept_old_events(stripe, school, offer):
    old_attempt, old = complete(school, offer, stripe)
    handle_subscription_deleted(old)
    school.refresh_from_db()
    stripe.checkout.Session.create.return_value = {"id": "cs_rejoin", "url": "https://checkout.stripe.com/c/pay/test_rejoin"}
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.filter(state="pending").get()
    new = subscription(attempt, id="sub_rejoin", customer="cus_rejoin")
    stripe.Subscription.retrieve.return_value = new
    handle_subscription_updated(new)
    school.refresh_from_db()
    assert school.stripe_subscription_id == "sub_rejoin"
    assert school.is_active
    stripe.Subscription.retrieve.return_value = old
    handle_subscription_updated(old)
    handle_subscription_deleted(old)
    school.refresh_from_db()
    assert school.stripe_subscription_id == "sub_rejoin"
    assert school.is_active


def test_unused_price_paths_and_portal_configuration_fail_closed(stripe, school, offer, settings):
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.get(offer=offer)
    sub = subscription(attempt)
    stripe.Subscription.retrieve.return_value = {**sub, "customer": "cus_wrong"}
    pricing.reconcile_checkout(school, {"id": attempt.session_id, "metadata": sub["metadata"], "livemode": False,
                                       "mode": "subscription", "subscription": sub["id"], "customer": sub["customer"]})
    school.refresh_from_db()
    assert school.plan == "trial"
    stripe.billing_portal.Configuration.create.return_value = {}
    with pytest.raises(ValidationError, match="could not be created"):
        pricing.portal_configuration(school)
    settings.STRIPE_MODE = "live"
    with pytest.raises(ValidationError, match="No agreement"):
        pricing.portal_configuration(school)


def test_future_price_does_not_misrepresent_legacy_subscription(client, stripe, school, offer, owner):
    school.plan = "starter"
    school.stripe_customer_id, school.stripe_subscription_id, school.stripe_subscription_status = "cus_old", "sub_old", "active"
    school.save()
    client.force_login(owner)
    response = client.get(reverse("school_billing", args=[school.slug]))
    assert b"Future subscription agreement" in response.content
    assert b"existing subscription has not changed" in response.content
    assert b"Continue to secure checkout" not in response.content


@pytest.mark.django_db(transaction=True)
def test_concurrent_checkout_requests_share_one_stripe_identity(stripe, school, offer):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connections
    barrier = Barrier(2)

    def remote(**kwargs):
        barrier.wait(timeout=10)
        return {"id": "cs_concurrent", "url": "https://checkout.stripe.com/c/pay/test_concurrent"}

    stripe.checkout.Session.create.side_effect = remote

    def worker():
        try:
            return checkout(school, offer)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker) for _ in range(2)]
        assert all(f.result(timeout=15).endswith("test_concurrent") for f in futures)
    assert SchoolBillingCheckout.objects.count() == 1
    args = [call.kwargs for call in stripe.checkout.Session.create.call_args_list]
    assert args[0] == args[1]


def test_negotiated_renewal_reuses_alerts_grace_and_recovery(stripe, school, settings):
    from core.models import PaymentNotification
    from core.services.billing_invoices import handle_invoice_event
    settings.STRIPE_MODE = "live"
    settings.STRIPE_SECRET_KEY = "sk_live_simulation_only"
    settings.PAYMENT_ALERT_RECIPIENTS = ["ketan@mypontora.com"]
    settings.PAYMENT_ALERT_TEST_EVENTS = False
    original = stripe.Price.retrieve.side_effect
    stripe.Price.retrieve.side_effect = lambda ident, **kw: {**original(ident, **kw), "livemode": True}
    offer = publish(school)
    checkout(school, offer)
    attempt = SchoolBillingCheckout.objects.get(offer=offer)
    sub = subscription(attempt, livemode=True)
    stripe.Subscription.retrieve.return_value = sub
    invoice = {"id": "in_negotiated", "customer": "cus_local", "subscription": "sub_local", "livemode": True,
               "status": "open", "currency": "usd", "amount_due": 8900, "amount_paid": 0,
               "attempt_count": 1, "billing_reason": "subscription_cycle"}
    stripe.Invoice.retrieve.return_value = invoice
    failure = {"id": "evt_failure", "type": "invoice.payment_failed", "livemode": True,
               "created": int((timezone.now() - timedelta(days=8)).timestamp()), "data": {"object": invoice}}
    # Invoice can arrive before Checkout/subscription events.
    handle_invoice_event(failure)
    handle_subscription_updated(sub)
    school.refresh_from_db()
    assert school.is_payment_access_expired
    assert school.plan == "custom"
    assert school.stripe_subscription_id == "sub_local"
    handle_invoice_event(failure)
    assert PaymentNotification.objects.count() == 1
    paid = {**invoice, "status": "paid", "amount_paid": 8900}
    stripe.Invoice.retrieve.return_value = paid
    success = {**failure, "id": "evt_paid", "type": "invoice.paid", "data": {"object": paid}}
    handle_invoice_event(success)
    handle_invoice_event(success)
    school.refresh_from_db()
    assert school.payment_grace_ends_at is None
    assert school.is_active
    assert PaymentNotification.objects.count() == 2
    assert all(n.recipients == ["ketan@mypontora.com"] for n in PaymentNotification.objects.all())


def test_new_pricing_endpoint_respects_existing_portal_boundaries(client, stripe, school, settings):
    settings.APP_BASE_URL = "https://app.example.com"
    settings.DEMO_BASE_URL = "https://demo.example.com"
    settings.ALLOWED_HOSTS = ["app.example.com", "demo.example.com", "testserver"]
    client.force_login(UserFactory(is_superuser=True, is_staff=True))
    path = reverse("ops_school_pricing", args=[school.slug])
    assert client.get(path, HTTP_HOST="demo.example.com").url == "https://app.example.com" + path
    assert client.post(path, {"action": "publish"}, HTTP_HOST="demo.example.com").status_code == 403
    school.is_demo = True
    school.save()
    assert client.get(path, HTTP_HOST="app.example.com").url == "https://demo.example.com" + path
    assert client.post(path, {"action": "publish"}, HTTP_HOST="app.example.com").status_code == 403
    stripe.Price.retrieve.assert_not_called()
