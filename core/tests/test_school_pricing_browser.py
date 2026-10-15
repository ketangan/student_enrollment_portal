"""Opt-in real-browser regression, isolated database and mocked Stripe only.

Run with PONTORA_BROWSER_QA=1. Never uses a running developer/production server.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from core.tests.factories import SchoolFactory, UserFactory, SchoolAdminMembershipFactory
from core.tests.test_school_pricing import stripe, school, offer, location_update  # noqa: F401 - shared isolated fixtures

pytestmark = [pytest.mark.django_db(transaction=True),
              pytest.mark.skipif(os.environ.get("PONTORA_BROWSER_QA") != "1", reason="Opt-in browser verification")]


@pytest.mark.parametrize("change_base", [False, True])
def test_location_renewal_operator_and_customer_screens(live_server, settings, stripe, school, location_update, change_base):
    from playwright.sync_api import sync_playwright, expect
    settings.DEBUG = True
    settings.APP_BASE_URL = live_server.url
    admin = UserFactory(username="location_operator", is_superuser=True, is_staff=True, password="admin")
    output = Path("/tmp/pontora-pricing-qa")
    output.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in {"localhost", "127.0.0.1"} else route.abort())
        page.goto(f"{live_server.url}/login/?next=/ops/schools/{school.slug}/#subscription-pricing")
        page.get_by_label("Email or username").fill(admin.username)
        page.get_by_label("Password", exact=True).fill("admin")
        page.get_by_role("button", name="Sign in", exact=True).click()
        if change_base:
            page.get_by_label("Base subscription Price ID").fill("price_upgrade")
            page.get_by_role("button", name="Validate prices").click()
            page.get_by_role("button", name="Save agreement", exact=True).click()
            expect(page.get_by_text("Current subscription rate:", exact=False)).to_contain_text("$89.00 / month")
            expect(page.get_by_text("New subscription rate:", exact=False)).to_contain_text("$139.00 / month")
            for width in (1280, 390, 320):
                page.set_viewport_size({"width": width, "height": 900})
                box = page.get_by_role("button", name="Apply billing changes at next renewal").bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= width
                page.get_by_text("Review billing change", exact=True).scroll_into_view_if_needed()
                page.screenshot(path=str(output / f"base-change-review-{width}.png"))
        expect(page.get_by_role("button", name="Apply billing changes at next renewal")).to_be_visible()
        page.get_by_role("button", name="Apply billing changes at next renewal").click()
        expect(page.get_by_text("Confirmed with Stripe:", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Apply billing changes at next renewal")).to_have_count(0)
        page.goto(f"{live_server.url}/schools/{school.slug}/admin/billing/")
        expect(page.get_by_text("No charge today.", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Continue to secure checkout")).to_have_count(0)
        for width in (1280, 390, 320):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(output / f"{'base' if change_base else 'location'}-renewal-{width}.png"), full_page=True)
        page.get_by_role("link", name="Back to settings", exact=False).click()
        expect(page).to_have_url(f"{live_server.url}/schools/{school.slug}/admin/settings/?tab=billing")
        browser.close()


def test_pricing_desktop_mobile_flow(live_server, settings, stripe):
    from django.utils import timezone
    from playwright.sync_api import sync_playwright, expect
    settings.DEBUG = True
    settings.APP_BASE_URL = live_server.url
    school = SchoolFactory(slug="pricing-browser", display_name="Workshop Pricing Review", is_demo=False,
                           plan="trial", trial_started_at=timezone.now(), trial_end_date=None)
    operator = UserFactory(username="pricing_operator", is_superuser=True, is_staff=True, password="admin")
    owner = UserFactory(username="pricing_owner", password="admin")
    SchoolAdminMembershipFactory(school=school, user=owner, role="owner")
    legacy = SchoolFactory(slug="pricing-legacy", display_name="Existing School", plan="starter",
                           stripe_customer_id="cus_legacy", stripe_subscription_id="sub_legacy", stripe_subscription_status="active")
    SchoolAdminMembershipFactory(school=legacy, user=owner, role="owner")
    output = Path("/tmp/pontora-pricing-qa")
    output.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        # No browser request may leave loopback during a payment UI test.
        page.route("**/*", lambda route: route.continue_() if urlsplit(route.request.url).hostname in {"localhost", "127.0.0.1"} else route.abort())
        page.goto(f"{live_server.url}/login/?next=/ops/schools/{school.slug}/#subscription-pricing")
        page.get_by_label("Email or username").fill(operator.username)
        page.get_by_label("Password", exact=True).fill("admin")
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_role("heading", name="Subscription pricing", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Save agreement", exact=True)).to_be_disabled()
        page.get_by_label("Agreement name").fill("Workshop founding agreement")
        page.get_by_label("Base subscription Price ID").fill("prod_wrong")
        page.get_by_role("button", name="Validate prices").click()
        expect(page.get_by_text("Enter a Stripe Price ID", exact=False)).to_be_visible()
        page.get_by_label("Base subscription Price ID").fill("price_base")
        page.get_by_label("Additional location Price ID").fill("price_location")
        page.get_by_label("Additional locations to bill").fill("2")
        expect(page.get_by_label("Future upgrade Price ID")).to_have_count(0)
        page.get_by_role("button", name="Validate prices").click()
        expect(page.get_by_text("$89.00 / month", exact=True)).to_be_visible()
        for width in (1440, 1280, 390, 320):
            page.set_viewport_size({"width": width, "height": 1000})
            for field in page.locator("#subscription-pricing input:not([type=hidden])").all():
                box = field.bounding_box()
                assert box and box["width"] >= 100 and box["x"] >= 0 and box["x"] + box["width"] <= width
            page.locator("#subscription-pricing").screenshot(path=str(output / f"operator-{width}.png"))
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="Save agreement").click()
        expect(page.get_by_role("heading", name="Saved agreement")).to_be_visible()
        page.reload()
        expect(page.get_by_label("Base subscription Price ID")).to_have_value("price_base")
        from core.models import SchoolBillingCheckout
        from core.services import school_pricing
        from core.tests.test_school_pricing import recovery_session
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connections
        def prepare_checkout():
            try:
                offer = school_pricing.current_offer(school)
                school_pricing.start_checkout(school=school, offer_id=offer.pk,
                                              success_url=live_server.url, cancel_url=live_server.url)
                return SchoolBillingCheckout.objects.get(offer=offer)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as executor:
            attempt = executor.submit(prepare_checkout).result()
        stripe.checkout.Session.retrieve.return_value = recovery_session(attempt)
        page.reload()
        page.get_by_role("button", name="Check status", exact=True).click()
        expect(page.get_by_text("Checkout is still open.", exact=False)).to_be_visible()
        for width in (1280, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            page.locator("#subscription-pricing").screenshot(path=str(output / f"recovery-{width}.png"))
        stripe.checkout.Session.retrieve.side_effect = [recovery_session(attempt), recovery_session(attempt, status="expired")]
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Expire unpaid checkout", exact=True).click()
        expect(page.get_by_text("Checkout expired.", exact=False)).to_be_visible()
        expect(page.get_by_role("button", name="Expire unpaid checkout", exact=True)).to_have_count(0)
        stripe.checkout.Session.retrieve.side_effect = None
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.context.clear_cookies()
        page.goto(f"{live_server.url}/login/?next=/schools/{school.slug}/admin/billing/")
        page.get_by_label("Email or username").fill(owner.username)
        page.get_by_label("Password", exact=True).fill("admin")
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_role("heading", name="Your agreed subscription")).to_be_visible()
        expect(page.get_by_text("Agreed future upgrade", exact=False)).to_have_count(0)
        expect(page.get_by_text("days remaining", exact=False)).to_be_visible()
        expect(page.get_by_text("$89.00 / month", exact=True)).to_be_visible()
        expect(page.get_by_role("heading", name="Starter", exact=True)).to_have_count(0)
        expect(page.locator(".billing-plan-card")).to_have_count(1)
        expect(page.locator(".billing-plan-card--current")).to_have_count(1)
        expect(page.get_by_role("link", name="Contact Pontora", exact=True)).to_have_count(0)
        expect(page.locator(".billing-plan-card .billing-price-btn")).to_have_count(1)
        expect(page.locator(".billing-agreement-table")).to_have_count(0)
        for width in (1440, 390, 320):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(output / f"owner-{width}.png"), full_page=True)
        # Intercept only the external redirect; do not simulate a successful charge.
        page.route("https://checkout.stripe.com/**", lambda route: route.fulfill(status=200, body="Stripe redirect intercepted for local verification"))
        page.get_by_role("button", name="Continue to secure checkout").click()
        expect(page).to_have_url("https://checkout.stripe.com/c/pay/test_local")
        assert stripe.checkout.Session.create.call_args.kwargs["line_items"] == [
            {"price": "price_base", "quantity": 1}, {"price": "price_location", "quantity": 2},
        ]
        page.goto(f"{live_server.url}/schools/{legacy.slug}/admin/billing/")
        expect(page.get_by_role("button", name="Manage Billing", exact=False)).to_be_visible()
        expect(page.get_by_role("heading", name="Your agreed subscription")).to_have_count(0)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.screenshot(path=str(output / "legacy-billing-1440.png"), full_page=True)
        page.goto(f"{live_server.url}/ops/schools/{school.slug}/pricing/")
        expect(page).to_have_url(f"{live_server.url}/schools/{school.slug}/admin/")
        expect(page.get_by_role("heading", name="Subscription pricing", exact=True)).to_have_count(0)
        browser.close()
