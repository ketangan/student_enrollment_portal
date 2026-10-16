from datetime import timedelta
from copy import deepcopy
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory
from django.utils import timezone

from core.payments.models import LedgerEntry, PaymentAccount, PaymentAttempt
from core.services import billing_stripe as gateway
from scheduling.experience_demo import payments, service
from .test_checkout import session_for
from .test_experience_demo import SLUG, browser, demo, form_data, local, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def stripe_demo(demo, settings):
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_only"
    PaymentAccount.objects.create(school=demo.school, reference="acct_test_demo", enabled=True)
    return demo


def booking(browser, demo):
    session, data = form_data(browser, demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    assert b"Continue to checkout <" in review.content
    assert b"Stripe test checkout" not in review.content
    data.update(action="checkout", review_token=review.context["review_token"])
    result = browser.post(url("choose", pk=session.pk), data)
    assert result.status_code == 302
    response = browser.get(result.url)
    return result.url, response.context["item"]


def open_stripe(browser, route, monkeypatch):
    create = Mock(side_effect=session_for)
    monkeypatch.setattr(gateway, "create_order_checkout", create)
    response = browser.post(route, {"action": "stripe_checkout", "amount": "1", "livemode": "true"})
    assert response.status_code == 302
    assert response.url.startswith("https://checkout.stripe.com/")
    return PaymentAttempt.objects.get(), create


def test_online_calendar_and_direct_selection_hide_checkout_cutoff(stripe_demo, browser):
    session, data = form_data(browser, stripe_demo, count=1)
    start = timezone.now() + timedelta(minutes=30)
    type(session).objects.filter(pk=session.pk).update(starts_at=start, ends_at=start + timedelta(hours=1))
    response = browser.get(url("start"))
    assert session.pk not in [row["id"] for row in response.context["sessions"]]
    assert browser.get(url("choose", pk=session.pk)).status_code == 404
    assert browser.post(url("choose", pk=session.pk), data).status_code == 404
    PaymentAccount.objects.filter(school=stripe_demo.school).update(enabled=False)
    assert session.pk in [row["id"] for row in browser.get(url("start")).context["sessions"]]
    assert browser.get(url("choose", pk=session.pk)).status_code == 200


def test_review_crossing_payment_cutoff_creates_no_reservation(stripe_demo, browser, monkeypatch):
    from scheduling.experience_demo.config import context
    from scheduling.requests import browser_key
    now = timezone.now()
    session, data = form_data(browser, stripe_demo, count=1)
    type(session).objects.filter(pk=session.pk).update(starts_at=now + timedelta(minutes=50),
        ends_at=now + timedelta(minutes=110))
    review = browser.post(url("choose", pk=session.pk), data)
    assert review.status_code == 200
    data.update(action="checkout", review_token=review.context["review_token"])
    request = review.wsgi_request
    profile, config = context(request, SLUG)
    session.refresh_from_db()
    monkeypatch.setattr(timezone, "now", lambda: now + timedelta(minutes=11))
    with pytest.raises(ValidationError, match="Choose another time"):
        service.begin(profile, session, data, browser_key(request), config)
    assert not stripe_demo.reservations.exists()
    assert not stripe_demo.school.paymentorder_set.exists()


def test_verified_payment_and_duplicate_returns(stripe_demo, browser, monkeypatch):
    route, item = booking(browser, stripe_demo)
    html = browser.get(route).content
    assert b'target="_top"' in html and b"Continue to Stripe Checkout" in html
    assert b"4242 4242 4242 4242" not in html
    assert b"Complete your test booking" not in html
    assert b"4000 0000 0000 0002" not in html and b"Simulate successful" not in html
    attempt, create = open_stripe(browser, route, monkeypatch)
    assert attempt.amount_cents == item.reservation.order.total_cents == 6500
    assert attempt.order.account.school == stripe_demo.school
    assert attempt.success_url == attempt.cancel_url == "http://127.0.0.1:8002" + route + "?checkout_return=1"
    assert browser.post(route, {"action": "stripe_checkout"}).status_code == 302
    assert create.call_count == 1
    assert browser.get(route + "?success=true").status_code == 200
    assert not LedgerEntry.objects.filter(order=attempt.order).exists()
    retrieve = Mock(return_value=session_for(attempt, status="complete"))
    monkeypatch.setattr(gateway, "retrieve_order_checkout", retrieve)
    result = browser.get(route + "?checkout_return=1")
    assert b"Booking confirmed" in result.content
    assert result.context["item"].reservation.state == "confirmed"
    assert b"Continue to Stripe Checkout" not in result.content
    assert browser.get(route + "?checkout_return=1").status_code == 200
    assert retrieve.call_count == 1
    entries = LedgerEntry.objects.filter(order=attempt.order)
    assert entries.count() == 1 and entries.get().channel == "stripe"
    assert entries.get().environment == "test"


@pytest.mark.parametrize("action", ["complete", "decline", "abandon", "bad"])
def test_simulation_cannot_confirm_or_cancel_stripe_booking(stripe_demo, browser, action):
    route, item = booking(browser, stripe_demo)
    assert browser.post(route, {"action": action}).status_code == 400
    with pytest.raises(ValidationError, match="verified Stripe"):
        service.simulate(stripe_demo, item, {}, action)
    assert not item.reservation.order.entries.exists()
    item.reservation.refresh_from_db()
    assert item.reservation.state == "held"


def test_unpaid_cancel_return_and_retry_reuse_same_checkout(stripe_demo, browser, monkeypatch):
    route, item = booking(browser, stripe_demo)
    attempt, create = open_stripe(browser, route, monkeypatch)
    retrieve = Mock(return_value=session_for(attempt))
    monkeypatch.setattr(gateway, "retrieve_order_checkout", retrieve)
    response = browser.get(route + "?checkout_return=1&payment_status=paid")
    assert b"No successful payment has been verified" in response.content
    assert response.context["item"].reservation.state == "held"
    assert browser.get(route + "?checkout_return=1").status_code == 200
    assert retrieve.call_count == 1  # bounded recovery, even if the return URL is forged repeatedly
    assert browser.post(route, {"action": "stripe_checkout"}).status_code == 302
    assert create.call_count == 1 and not item.reservation.order.entries.exists()


@pytest.mark.parametrize("failure", ["network", "live", "amount", "metadata"])
def test_failed_verification_never_confirms(stripe_demo, browser, monkeypatch, failure):
    route, item = booking(browser, stripe_demo)
    attempt, _ = open_stripe(browser, route, monkeypatch)
    snapshot = session_for(attempt, status="complete")
    if failure == "network":
        retrieve = Mock(side_effect=RuntimeError("offline"))
    else:
        if failure == "live": snapshot["livemode"] = True
        if failure == "amount": snapshot["amount_total"] = 1
        if failure == "metadata": snapshot["metadata"]["payment_order"] = "wrong"
        retrieve = Mock(return_value=snapshot)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", retrieve)
    response = browser.get(route + "?checkout_return=1")
    assert b"Payment verification is pending" in response.content
    assert response.context["item"].reservation.state == "held"
    assert not item.reservation.order.entries.exists()


def test_expired_and_missing_checkout(stripe_demo, browser, monkeypatch):
    route, item = booking(browser, stripe_demo)
    assert b"No Stripe checkout has been started" in browser.get(route + "?checkout_return=1").content
    attempt, _ = open_stripe(browser, route, monkeypatch)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", lambda a: session_for(a, status="expired"))
    assert b"Checkout expired without payment" in browser.get(route + "?checkout_return=1").content
    assert not item.reservation.order.entries.exists()


def test_browser_ownership_and_csrf_before_provider(stripe_demo, browser, monkeypatch):
    route, item = booking(browser, stripe_demo)
    create = Mock()
    monkeypatch.setattr(gateway, "create_order_checkout", create)
    other = Client(HTTP_HOST="127.0.0.1:8002")
    assert other.get(route + "?checkout_return=1").status_code == 404
    assert other.post(route, {"action": "stripe_checkout"}).status_code == 404
    strict = Client(enforce_csrf_checks=True, HTTP_HOST="127.0.0.1:8002")
    strict.cookies = browser.cookies
    assert strict.post(route, {"action": "stripe_checkout"}).status_code == 403
    create.assert_not_called()


@pytest.mark.parametrize("missing", ["key", "account", "live_key"])
def test_unavailable_stripe_fails_closed_at_booking(stripe_demo, browser, settings, missing):
    session, data = form_data(browser, stripe_demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    if missing == "account": PaymentAccount.objects.filter(school=stripe_demo.school).update(enabled=False)
    else: settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_live_forbidden" if missing == "live_key" else ""
    data.update(action="checkout", review_token=review.context["review_token"])
    response = browser.post(url("choose", pk=session.pk), data)
    assert response.status_code == 400
    assert not stripe_demo.reservations.exists()


def test_payment_mode_change_requires_new_review(stripe_demo, browser):
    session, data = form_data(browser, stripe_demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    PaymentAccount.objects.filter(school=stripe_demo.school).update(enabled=False)
    data.update(action="checkout", review_token=review.context["review_token"])
    assert browser.post(url("choose", pk=session.pk), data).status_code == 400
    assert not stripe_demo.reservations.exists()


def test_multiple_businesses_enable_test_checkout_independently(stripe_demo, settings, monkeypatch):
    from core.models import School
    from scheduling.experience_demo import config
    from scheduling.models import SchedulingProfile

    settings.STRIPE_MODE = "live"
    settings.STRIPE_SECRET_KEY = "sk_live_must_not_be_used"
    second = School.objects.create(slug="another-prospect", is_demo=True,
        feature_flags={"booking_experience_demo": True})
    SchedulingProfile.objects.create(school=second, enabled=True,
        capabilities=["workshops", "reservations"])
    # Brand content is supplied separately; payment selection must not depend on its slug.
    brand = config.configuration(SLUG)
    monkeypatch.setattr(config, "configuration", lambda slug: deepcopy(brand))
    request = RequestFactory().get("/", HTTP_HOST="127.0.0.1:8002")
    assert config.context(request, stripe_demo.school.slug)[1]["stripe_checkout"] is True
    assert config.context(request, second.slug)[1]["stripe_checkout"] is False
    other_account = PaymentAccount.objects.create(school=second, reference="acct_another", enabled=True)
    assert config.context(request, second.slug)[1]["stripe_checkout"] is True
    assert config.context(request, stripe_demo.school.slug)[1]["stripe_checkout"] is True
    other_account.enabled = False
    other_account.save(update_fields=["enabled"])
    assert config.context(request, second.slug)[1]["stripe_checkout"] is False
    assert config.context(request, stripe_demo.school.slug)[1]["stripe_checkout"] is True
    assert settings.STRIPE_MODE == "live"


def test_disabling_test_checkout_preserves_existing_payment_binding(stripe_demo, browser, monkeypatch):
    route, item = booking(browser, stripe_demo)
    attempt, _ = open_stripe(browser, route, monkeypatch)
    PaymentAccount.objects.filter(school=stripe_demo.school).update(enabled=False)
    assert browser.post(route, {"action": "complete"}).status_code == 400
    assert browser.post(route, {"action": "stripe_checkout"}).status_code == 400
    monkeypatch.setattr(gateway, "retrieve_order_checkout", lambda a: session_for(a, status="complete"))
    response = browser.get(route + "?checkout_return=1")
    assert response.context["item"].reservation.state == "confirmed"
    assert LedgerEntry.objects.get(order=attempt.order).channel == "stripe"


def test_unknown_creation_can_retry_without_false_confirmation(stripe_demo, browser, monkeypatch):
    route, item = booking(browser, stripe_demo)
    monkeypatch.setattr(gateway, "create_order_checkout", Mock(side_effect=RuntimeError("offline")))
    assert browser.post(route, {"action": "stripe_checkout"}).status_code == 400
    assert PaymentAttempt.objects.get().state == "unknown"
    assert not item.reservation.order.entries.exists()


def test_payment_review_and_lease_status(stripe_demo, browser, monkeypatch):
    route, item = booking(browser, stripe_demo)
    attempt, _ = open_stripe(browser, route, monkeypatch)
    attempt.lease_until = timezone.now() + timedelta(minutes=1)
    attempt.save(update_fields=["lease_until"])
    assert "verification is pending" in payments.verify_return(item)
    attempt.state, attempt.lease_until = "paid", None
    attempt.save(update_fields=["state", "lease_until"])
    assert "needs the studio's review" in payments.verify_return(item)


def test_adapter_refuses_unavailable_demo(stripe_demo, browser, monkeypatch):
    route, item = booking(browser, stripe_demo)
    monkeypatch.setattr(payments, "available", lambda profile: False)
    with pytest.raises(ValidationError):
        payments.start_checkout(RequestFactory().post(route), stripe_demo, item)


def test_disabled_mode_preserves_existing_simulation(demo, browser):
    session, data = form_data(browser, demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    assert b"Continue to checkout <" in review.content
    data.update(action="checkout", review_token=review.context["review_token"])
    response = browser.post(url("choose", pk=session.pk), data)
    assert b"Simulate successful checkout" in browser.get(response.url).content
    assert browser.post(response.url, {"action": "complete"}).status_code == 302
