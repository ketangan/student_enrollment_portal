# core/tests/test_billing.py
"""
Tests for the Stripe billing feature:
- billing service module
- billing views (page, checkout, portal)
- webhook handler
- model fields
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory, override_settings
from django.urls import reverse

from core.models import School
from core.services.billing_stripe import (
    get_pricing_options,
    handle_checkout_completed,
    handle_subscription_deleted,
    handle_subscription_updated,
    is_stripe_configured,
    price_to_plan,
)
from core.tests.factories import SchoolFactory, SchoolAdminMembershipFactory, UserFactory


# ---------------------------------------------------------------------------
# Model field tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSchoolStripeFields:
    def test_stripe_fields_default_empty(self):
        school = SchoolFactory()
        assert school.stripe_customer_id == ""
        assert school.stripe_subscription_id == ""
        assert school.stripe_subscription_status == ""

    def test_stripe_fields_can_be_set(self):
        school = SchoolFactory()
        school.stripe_customer_id = "cus_123"
        school.stripe_subscription_id = "sub_456"
        school.stripe_subscription_status = "active"
        school.save()
        school.refresh_from_db()
        assert school.stripe_customer_id == "cus_123"
        assert school.stripe_subscription_id == "sub_456"
        assert school.stripe_subscription_status == "active"


# ---------------------------------------------------------------------------
# Service layer tests
# ---------------------------------------------------------------------------


class TestPriceToPlan:
    @patch.dict("os.environ", {"STRIPE_PRICE_STARTER_MONTHLY": "price_monthly_123"})
    def test_known_monthly_price(self):
        # Re-import to pick up env change
        from core.services import billing_stripe

        billing_stripe.PRICE_STARTER_MONTHLY_ID = "price_monthly_123"
        assert billing_stripe.price_to_plan("price_monthly_123") == "starter"

    def test_unknown_price_returns_none(self):
        assert price_to_plan("price_unknown_xyz") is None


class TestGetPricingOptions:
    @patch.dict(
        "os.environ",
        {
            "STRIPE_PRICE_STARTER_MONTHLY": "price_m",
            "STRIPE_PRICE_STARTER_ANNUAL": "price_a",
        },
    )
    def test_returns_two_options_when_env_set(self):
        from core.services import billing_stripe

        billing_stripe.PRICE_STARTER_MONTHLY_ID = "price_m"
        billing_stripe.PRICE_STARTER_ANNUAL_ID = "price_a"
        options = billing_stripe.get_pricing_options()
        assert len(options) == 2
        assert options[0]["id"] == "starter_monthly"
        assert options[1]["id"] == "starter_annual"

    def test_returns_empty_when_no_env(self):
        from core.services import billing_stripe

        original_m = billing_stripe.PRICE_STARTER_MONTHLY_ID
        original_a = billing_stripe.PRICE_STARTER_ANNUAL_ID
        billing_stripe.PRICE_STARTER_MONTHLY_ID = ""
        billing_stripe.PRICE_STARTER_ANNUAL_ID = ""
        try:
            options = billing_stripe.get_pricing_options()
            assert options == []
        finally:
            billing_stripe.PRICE_STARTER_MONTHLY_ID = original_m
            billing_stripe.PRICE_STARTER_ANNUAL_ID = original_a


class TestIsStripeConfigured:
    @patch.dict(
        "os.environ",
        {"STRIPE_SECRET_KEY": "", "STRIPE_PUBLISHABLE_KEY": ""},
    )
    def test_not_configured_without_keys(self):
        from core.services import billing_stripe

        billing_stripe._stripe = None  # reset cached module
        assert billing_stripe.is_stripe_configured() is False


# ---------------------------------------------------------------------------
# Webhook handler tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHandleCheckoutCompleted:
    def test_sets_customer_and_subscription(self):
        school = SchoolFactory(plan="trial")
        session_data = {
            "metadata": {"school_slug": school.slug},
            "customer": "cus_abc",
            "subscription": "sub_xyz",
            "line_items": {"data": []},
        }
        with patch("core.services.billing_stripe._get_stripe") as mock_stripe:
            # Mock subscription retrieval
            mock_sub = {
                "items": {"data": [{"price": {"id": "price_starter"}}]},
                "status": "active",
            }
            mock_stripe.return_value.Subscription.retrieve.return_value = mock_sub

            from core.services import billing_stripe

            original = billing_stripe.PRICE_STARTER_MONTHLY_ID
            billing_stripe.PRICE_STARTER_MONTHLY_ID = "price_starter"
            try:
                handle_checkout_completed(session_data)
            finally:
                billing_stripe.PRICE_STARTER_MONTHLY_ID = original

        school.refresh_from_db()
        assert school.stripe_customer_id == "cus_abc"
        assert school.stripe_subscription_id == "sub_xyz"
        assert school.plan == "starter"

    def test_ignores_missing_school_slug(self):
        """Should not crash when metadata is empty."""
        handle_checkout_completed({"metadata": {}, "customer": "cus_x"})


@pytest.mark.django_db
class TestHandleSubscriptionUpdated:
    def test_updates_status(self):
        school = SchoolFactory(
            stripe_subscription_id="sub_123",
            stripe_subscription_status="active",
            plan="starter",
        )
        handle_subscription_updated({
            "id": "sub_123",
            "status": "past_due",
            "items": {"data": []},
        })
        school.refresh_from_db()
        assert school.stripe_subscription_status == "past_due"

    def test_no_school_found(self):
        """Should not crash for unknown subscription."""
        handle_subscription_updated({"id": "sub_unknown", "status": "active", "items": {"data": []}})


@pytest.mark.django_db
class TestHandleSubscriptionDeleted:
    def test_reverts_to_trial(self):
        school = SchoolFactory(
            stripe_subscription_id="sub_del",
            stripe_subscription_status="active",
            plan="starter",
        )
        handle_subscription_deleted({"id": "sub_del"})
        school.refresh_from_db()
        assert school.plan == "trial"
        assert school.stripe_subscription_status == "canceled"


# ---------------------------------------------------------------------------
# View tests — billing page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBillingView:
    def _url(self):
        return reverse("admin:billing")

    def test_anonymous_redirects(self, client):
        resp = client.get(self._url())
        assert resp.status_code == 302  # admin_view redirects to login

    def test_non_staff_redirects(self, client):
        user = UserFactory()
        client.force_login(user)
        resp = client.get(self._url())
        assert resp.status_code == 302

    def test_school_admin_sees_billing_page(self, client):
        school = SchoolFactory(plan="trial")
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        resp = client.get(self._url())
        assert resp.status_code == 200
        assert b"Billing" in resp.content
        assert school.display_name.encode() in resp.content or school.slug.encode() in resp.content

    def test_superuser_sees_billing_with_school_switcher(self, client):
        school = SchoolFactory(plan="starter")
        user = UserFactory(is_staff=True, is_superuser=True)
        client.force_login(user)
        resp = client.get(self._url() + f"?school={school.slug}")
        assert resp.status_code == 200
        assert b"Billing" in resp.content

    def test_superuser_defaults_to_first_school(self, client):
        SchoolFactory(slug="aaa-school", display_name="AAA School")
        SchoolFactory(slug="zzz-school", display_name="ZZZ School")
        user = UserFactory(is_staff=True, is_superuser=True)
        client.force_login(user)
        resp = client.get(self._url())
        assert resp.status_code == 200
        assert b"AAA School" in resp.content

    def test_school_admin_ignores_school_param(self, client):
        school_a = SchoolFactory(slug="school-a", display_name="School A")
        school_b = SchoolFactory(slug="school-b", display_name="School B")
        membership = SchoolAdminMembershipFactory(school=school_a)
        client.force_login(membership.user)
        resp = client.get(self._url() + "?school=school-b")
        assert resp.status_code == 200
        # Should show school_a, not school_b
        assert b"School A" in resp.content

    def test_staff_without_membership_gets_404(self, client):
        user = UserFactory(is_staff=True)
        client.force_login(user)
        resp = client.get(self._url())
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# View tests — checkout & portal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBillingCheckout:
    def _url(self):
        return reverse("admin:billing_create_checkout")

    def _valid_pricing(self):
        """Return a mock pricing list that includes price_123."""
        return [{"price_id": "price_123", "id": "starter_monthly", "name": "Starter Monthly",
                 "amount": "$49.99/mo", "plan": "starter", "interval": "month"}]

    def test_anonymous_redirects(self, client):
        resp = client.post(self._url(), {"price_id": "price_123"})
        assert resp.status_code == 302  # admin_view redirects to login

    def test_non_staff_redirects(self, client):
        user = UserFactory()
        client.force_login(user)
        resp = client.post(self._url(), {"price_id": "price_123"})
        assert resp.status_code == 302

    def test_post_without_price_redirects_with_error(self, client):
        school = SchoolFactory()
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        resp = client.post(self._url(), {"price_id": ""})
        assert resp.status_code == 302

    @patch("core.views_billing.is_stripe_configured", return_value=True)
    @patch("core.views_billing.get_pricing_options")
    @patch("core.views_billing.create_checkout_session")
    def test_post_with_price_redirects_to_stripe(self, mock_checkout, mock_pricing, mock_is_configured, client):
        mock_pricing.return_value = self._valid_pricing()
        mock_checkout.return_value = "https://checkout.stripe.com/test"
        school = SchoolFactory()
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        resp = client.post(self._url(), {"price_id": "price_123"})
        assert resp.status_code == 302
        assert "checkout.stripe.com" in resp.url

    @patch("core.views_billing.is_stripe_configured", return_value=True)
    @patch("core.views_billing.get_pricing_options")
    @patch("core.views_billing.create_checkout_session")
    def test_post_stripe_error_redirects_back(self, mock_checkout, mock_pricing, mock_is_configured, client):
        mock_pricing.return_value = self._valid_pricing()
        mock_checkout.return_value = None
        school = SchoolFactory()
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        resp = client.post(self._url(), {"price_id": "price_123"})
        assert resp.status_code == 302
        assert "billing" in resp.url

    @patch("core.views_billing.is_stripe_configured", return_value=True)
    @patch("core.views_billing.get_pricing_options")
    @patch("core.views_billing.create_checkout_session")
    def test_superuser_checkout_with_school_param(self, mock_checkout, mock_pricing, mock_is_configured, client):
        mock_pricing.return_value = self._valid_pricing()
        mock_checkout.return_value = "https://checkout.stripe.com/test"
        school = SchoolFactory()
        user = UserFactory(is_staff=True, is_superuser=True)
        client.force_login(user)
        resp = client.post(self._url(), {"price_id": "price_123", "school": school.slug})
        assert resp.status_code == 302
        assert "checkout.stripe.com" in resp.url

    def test_invalid_price_id_redirects_with_error(self, client):
        """Submitting a price_id not in configured pricing returns an error."""
        school = SchoolFactory()
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        with patch("core.views_billing.get_pricing_options", return_value=[]), \
             patch("core.views_billing.create_checkout_session") as mock_checkout:
            resp = client.post(self._url(), {"price_id": "price_evil_injection"})
            mock_checkout.assert_not_called()
        assert resp.status_code == 302
        assert "billing" in resp.url

    def test_stripe_not_configured_redirects_with_error(self, client):
        school = SchoolFactory()
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        with patch("core.views_billing.is_stripe_configured", return_value=False), \
             patch("core.views_billing.create_checkout_session") as mock_checkout:
            resp = client.post(self._url(), {"price_id": "price_123"})
            mock_checkout.assert_not_called()
        assert resp.status_code == 302
        assert "billing" in resp.url

    @patch("core.views_billing.is_stripe_configured", return_value=True)
    @patch("core.views_billing.get_pricing_options")
    @patch("core.views_billing.create_checkout_session")
    def test_valid_price_id_passes_validation(self, mock_checkout, mock_pricing, mock_is_configured, client):
        """A known price_id passes validation and reaches Stripe."""
        mock_pricing.return_value = self._valid_pricing()
        mock_checkout.return_value = "https://checkout.stripe.com/ok"
        school = SchoolFactory()
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        resp = client.post(self._url(), {"price_id": "price_123"})
        assert resp.status_code == 302
        assert "checkout.stripe.com" in resp.url
        mock_checkout.assert_called_once()


@pytest.mark.django_db
class TestBillingPortal:
    def _url(self):
        return reverse("admin:billing_portal")

    def test_anonymous_redirects(self, client):
        resp = client.post(self._url())
        assert resp.status_code == 302

    def test_non_staff_redirects(self, client):
        user = UserFactory()
        client.force_login(user)
        resp = client.post(self._url())
        assert resp.status_code == 302

    @patch("core.views_billing.create_portal_session")
    def test_portal_redirects_to_stripe(self, mock_portal, client):
        mock_portal.return_value = "https://billing.stripe.com/portal"
        school = SchoolFactory(stripe_customer_id="cus_test")
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        resp = client.post(self._url())
        assert resp.status_code == 302
        assert "billing.stripe.com" in resp.url

    @patch("core.views_billing.create_portal_session")
    def test_portal_error_redirects_back(self, mock_portal, client):
        mock_portal.return_value = None
        school = SchoolFactory(stripe_customer_id="cus_test")
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        resp = client.post(self._url())
        assert resp.status_code == 302
        assert "billing" in resp.url


# ---------------------------------------------------------------------------
# Webhook endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStripeWebhook:
    def _url(self):
        return reverse("stripe_webhook")

    def test_missing_signature_returns_400(self, client):
        resp = client.post(
            self._url(),
            data=b'{}',
            content_type="application/json",
        )
        assert resp.status_code == 400

    @patch("core.views_billing.construct_webhook_event")
    def test_valid_checkout_completed(self, mock_construct, client):
        school = SchoolFactory(plan="trial")
        event = MagicMock()
        event.type = "checkout.session.completed"
        event.data.object = {
            "metadata": {"school_slug": school.slug},
            "customer": "cus_wh",
            "subscription": "sub_wh",
            "line_items": {"data": []},
        }
        mock_construct.return_value = event

        with patch("core.services.billing_stripe._get_stripe") as mock_stripe:
            mock_sub = {
                "items": {"data": []},
                "status": "active",
            }
            mock_stripe.return_value.Subscription.retrieve.return_value = mock_sub

            resp = client.post(
                self._url(),
                data=b'{}',
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE="test_sig",
            )

        assert resp.status_code == 200
        school.refresh_from_db()
        assert school.stripe_customer_id == "cus_wh"

    @patch("core.views_billing.construct_webhook_event")
    def test_unhandled_event_returns_200(self, mock_construct, client):
        event = MagicMock()
        event.type = "invoice.paid"
        event.data.object = {}
        mock_construct.return_value = event

        resp = client.post(
            self._url(),
            data=b'{}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test_sig",
        )
        assert resp.status_code == 200

    @patch("core.views_billing.construct_webhook_event")
    def test_subscription_updated_via_webhook(self, mock_construct, client):
        school = SchoolFactory(
            stripe_subscription_id="sub_wh_upd",
            stripe_subscription_status="active",
            plan="starter",
        )
        event = MagicMock()
        event.type = "customer.subscription.updated"
        event.data.object = {
            "id": "sub_wh_upd",
            "status": "past_due",
            "items": {"data": []},
        }
        mock_construct.return_value = event

        resp = client.post(
            self._url(),
            data=b'{}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test_sig",
        )
        assert resp.status_code == 200
        school.refresh_from_db()
        assert school.stripe_subscription_status == "past_due"

    @patch("core.views_billing.construct_webhook_event")
    def test_subscription_deleted_via_webhook(self, mock_construct, client):
        school = SchoolFactory(
            stripe_subscription_id="sub_wh_del",
            stripe_subscription_status="active",
            plan="starter",
        )
        event = MagicMock()
        event.type = "customer.subscription.deleted"
        event.data.object = {"id": "sub_wh_del"}
        mock_construct.return_value = event

        resp = client.post(
            self._url(),
            data=b'{}',
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="test_sig",
        )
        assert resp.status_code == 200
        school.refresh_from_db()
        assert school.plan == "trial"
        assert school.stripe_subscription_status == "canceled"


# ---------------------------------------------------------------------------
# Reports hub — billing link visibility
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestReportsHubBillingLink:
    def _url(self):
        return reverse("admin:reports_hub")

    def test_superuser_sees_billing_link(self, client):
        SchoolFactory()
        user = UserFactory(is_staff=True, is_superuser=True)
        client.force_login(user)
        resp = client.get(self._url())
        assert resp.status_code == 200
        assert b"Billing" in resp.content
        assert b"billing" in resp.content  # URL contains billing

    def test_school_admin_sees_billing_link(self, client):
        school = SchoolFactory()
        membership = SchoolAdminMembershipFactory(school=school)
        client.force_login(membership.user)
        resp = client.get(self._url())
        assert resp.status_code == 200
        assert b"Billing" in resp.content

    def test_staff_without_membership_gets_404(self, client):
        user = UserFactory(is_staff=True)
        client.force_login(user)
        resp = client.get(self._url())
        assert resp.status_code == 404

    def test_anonymous_redirects(self, client):
        resp = client.get(self._url())
        assert resp.status_code == 302
