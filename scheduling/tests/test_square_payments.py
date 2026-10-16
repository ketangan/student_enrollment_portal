import base64
import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import PaymentNotification, School
from core.payments import checkout, refunds, square
from core.payments.models import LedgerEntry, PaymentAccount, PaymentAttempt, ProviderEventReceipt
from core.payments.settings import save_provider
from core.services import billing_stripe
from scheduling.checkout import approve_refund, recover_attempt
from scheduling.reservations import booking_account, cancel_reservation
from scheduling.square_checkout import recover_square_attempt, submit
from .test_checkout import online, prepare
from .test_reservations import inventory, workshop_request
from .test_experience_demo import browser, demo, local, form_data, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def square_account(inventory, settings):
    settings.DEBUG = True
    settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID = "sandbox-sq0idb-test"
    settings.PAYMENTS_SQUARE_TEST_ACCESS_TOKEN = "test-secret-do-not-expose"
    settings.PAYMENTS_SQUARE_TEST_LOCATION_ID = "LTEST"
    settings.PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET = "test-webhook-secret"
    settings.PAYMENTS_SQUARE_TEST_WEBHOOK_URL = "https://demo.example.com/bookings/square/test/webhook/"
    settings.PAYMENT_ALERT_TEST_EVENTS = True
    settings.PAYMENT_ALERT_RECIPIENTS = ["operator@example.com"]
    profile = inventory[0]
    profile.school.is_demo = True
    profile.school.save(update_fields=["is_demo"])
    profile.payment_provider = "square"
    profile.save(update_fields=["payment_provider"])
    return PaymentAccount.objects.create(school=profile.school, provider="square", scope="platform_demo",
        reference="MTEST", location_reference="LTEST", enabled=True)


@pytest.fixture
def attempt(inventory, booking_member, square_account):
    return prepare(online(booking_member, workshop_request(inventory)))


def payment_for(attempt, status="COMPLETED"):
    return {"id": "square_" + attempt.public_id.hex, "status": status,
        "amount_money": {"amount": attempt.amount_cents, "currency": "USD"},
        "total_money": {"amount": attempt.amount_cents, "currency": "USD"},
        "location_id": attempt.account.location_reference, "reference_id": str(attempt.public_id),
        "source_type": "CARD", "application_details": {"application_id": "sandbox-sq0idb-test"}}


def pay(attempt, monkeypatch, status="COMPLETED"):
    call = Mock(return_value={"payment": payment_for(attempt, status)})
    monkeypatch.setattr(square, "request", call)
    submit(attempt, "cnon:card-test")
    attempt.refresh_from_db()
    return call


def post_event(client, settings, attempt, *, event_id=None, merchant="MTEST", signature=True,
               event_type="payment.updated", resource_id=None):
    event = {"event_id": event_id or str(uuid.uuid4()), "merchant_id": merchant,
        "type": event_type, "data": {"id": resource_id or payment_for(attempt)["id"]}}
    payload = json.dumps(event).encode()
    digest = base64.b64encode(hmac.new(settings.PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET.encode(),
        settings.PAYMENTS_SQUARE_TEST_WEBHOOK_URL.encode() + payload, hashlib.sha256).digest()).decode()
    return client.post(reverse("scheduling:square_test_webhook"), payload, content_type="application/json",
                       HTTP_X_SQUARE_HMACSHA256_SIGNATURE=digest if signature else "bad")


def test_payment_deposit_is_verified_once_without_touching_school(attempt, monkeypatch, client):
    school = attempt.order.school
    before = School.objects.filter(pk=school.pk).values().get()
    page_url = checkout.open_checkout(attempt)
    response = client.get(page_url)
    assert response.status_code == 200 and b"Pay $25.00" in response.content
    assert b"test-secret" not in response.content
    call = pay(attempt, monkeypatch)
    assert call.call_count == 1
    assert call.call_args.args[2]["amount_money"]["amount"] == 2500
    assert call.call_args.args[2]["location_id"] == "LTEST"
    assert attempt.state == "paid" and attempt.order.reservation.state == "confirmed"
    assert LedgerEntry.objects.get().channel == "square"
    assert PaymentNotification.objects.filter(key__endswith=":paid").count() == 1
    with pytest.raises(ValidationError):
        submit(attempt, "cnon:another-token")
    assert call.call_count == 1
    assert client.get(page_url).url == attempt.success_url
    assert School.objects.filter(pk=school.pk).values().get() == before


def test_decline_can_retry_without_a_ledger_entry(attempt, monkeypatch, client):
    monkeypatch.setattr(square, "request", Mock(side_effect=square.Declined("Card declined.")))
    url = checkout.open_checkout(attempt)
    response = client.post(url, {"source_id": "cnon:decline"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert response.status_code == 400 and response.json()["state"] == "expired"
    assert not LedgerEntry.objects.exists()
    assert PaymentNotification.objects.filter(key__endswith=":failed").count() == 1
    assert b"Try another card" in client.get(url).content
    response = client.post(url, {"action": "retry"})
    assert response.status_code == 302
    replacement = PaymentAttempt.objects.exclude(pk=attempt.pk).get()
    assert replacement.amount_cents == 2500 and replacement.account_id == attempt.account_id
    pay(replacement, monkeypatch)
    assert LedgerEntry.objects.count() == 1


def test_timeout_blocks_resubmission_and_recovers_without_new_charge(attempt, monkeypatch):
    create = Mock(side_effect=ValidationError("Provider unavailable"))
    monkeypatch.setattr(square, "request", create)
    with pytest.raises(ValidationError):
        submit(attempt, "cnon:timeout")
    attempt.refresh_from_db()
    assert attempt.state == "unknown" and attempt.submitted_at
    with pytest.raises(ValidationError):
        submit(attempt, "cnon:retry")
    assert create.call_count == 1
    monkeypatch.setattr(square, "request", Mock(return_value={"payments": [payment_for(attempt)]}))
    assert recover_attempt(attempt, force=True)
    attempt.refresh_from_db()
    assert attempt.state == "paid" and LedgerEntry.objects.count() == 1


def test_unknown_absence_never_releases_inventory(attempt, monkeypatch):
    PaymentAttempt.objects.filter(pk=attempt.pk).update(state="unknown", submitted_at=timezone.now(), close_requested=True)
    monkeypatch.setattr(square, "request", Mock(return_value={"payments": []}))
    assert not recover_attempt(attempt, force=True)
    attempt.refresh_from_db()
    assert attempt.state == "unknown" and attempt.attention
    assert attempt.order.reservation.state == "held"


def test_unsubmitted_close_prevents_late_submit(attempt, monkeypatch):
    call = Mock()
    monkeypatch.setattr(square, "request", call)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(close_requested=True)
    recover_square_attempt(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "expired"
    with pytest.raises(ValidationError):
        submit(attempt, "cnon:too-late")
    assert not call.called


@pytest.mark.parametrize("field,value", [("location_id", "OTHER"), ("reference_id", str(uuid.uuid4())),
    ("source_type", "BANK_ACCOUNT"), ("amount_money", {"amount": 1, "currency": "USD"}),
    ("amount_money", {"amount": 2500, "currency": "CAD"}), ("total_money", {"amount": 2501, "currency": "USD"}),
    ("application_details", {"application_id": "live-app"}), ("status", "INVALID"), ("id", "../evil")])
def test_payment_mismatch_cannot_settle(attempt, monkeypatch, field, value):
    payment = payment_for(attempt)
    payment[field] = value
    monkeypatch.setattr(square, "request", Mock(return_value={"payment": payment}))
    with pytest.raises(ValidationError):
        submit(attempt, "cnon:card")
    attempt.refresh_from_db()
    assert attempt.state == "unknown" and not LedgerEntry.objects.exists()


def test_webhook_signature_account_and_replay(attempt, monkeypatch, settings, client):
    PaymentAttempt.objects.filter(pk=attempt.pk).update(submitted_at=timezone.now(), state="unknown")
    call = Mock(return_value={"payment": payment_for(attempt)})
    monkeypatch.setattr(square, "request", call)
    assert post_event(client, settings, attempt, signature=False).status_code == 400
    assert not call.called
    assert post_event(client, settings, attempt, merchant="OTHER").status_code == 503
    assert not LedgerEntry.objects.exists()
    event_id = str(uuid.uuid4())
    assert post_event(client, settings, attempt, event_id=event_id).status_code == 200
    assert post_event(client, settings, attempt, event_id=event_id).status_code == 200
    assert LedgerEntry.objects.count() == ProviderEventReceipt.objects.count() == 1
    assert PaymentNotification.objects.count() == 1


def test_signed_link_cannot_cross_school_and_post_requires_csrf(attempt, client):
    url = checkout.open_checkout(attempt)
    assert client.get(url.replace(attempt.order.school.slug, "other-school")).status_code == 404
    assert client.get(url[:-3] + "bad/").status_code == 404
    assert Client(enforce_csrf_checks=True).post(url, {"source_id": "cnon:card"}).status_code == 403


def test_demo_cannot_collect_on_production_domain(attempt, client, settings):
    settings.APP_BASE_URL = "https://app.mypontora.com"
    settings.DEMO_BASE_URL = "https://demo.mypontora.com"
    settings.ALLOWED_HOSTS = ["app.mypontora.com", "demo.mypontora.com", "testserver"]
    url = checkout.open_checkout(attempt)
    assert client.post(url, {"source_id": "cnon:card"}, HTTP_HOST="app.mypontora.com", secure=True).status_code == 403


def test_provider_switch_keeps_original_checkout_and_refund(attempt, booking_member, inventory, monkeypatch, settings):
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_unused"
    stripe_account = PaymentAccount.objects.create(school=attempt.order.school, reference="acct_original", enabled=True)
    actor = get_user_model().objects.create_superuser("square-operator", "", "test")
    before = School.objects.filter(pk=attempt.order.school_id).values().get()
    save_provider(school=attempt.order.school, provider="stripe", expected_provider="square", actor=actor)
    profile = inventory[0]
    profile.refresh_from_db()
    assert booking_account(profile, True) == stripe_account
    pay(attempt, monkeypatch)
    item = attempt.order.reservation
    assert attempt.account.provider == "square"
    cancel_reservation(booking_member.user, profile.school.slug, item.public_id)
    refund = approve_refund(booking_member.user, profile.school.slug, item.public_id, attempt.public_id,
        key=uuid.uuid4(), amount_cents=2500, reason="Canceled")
    result = {"id": "sq_refund_1", "payment_id": attempt.provider_intent, "location_id": "LTEST",
        "reason": square.refund_reason(refund), "amount_money": {"amount": 2500, "currency": "USD"}, "status": "COMPLETED"}
    monkeypatch.setattr(square, "request", Mock(return_value={"refund": result}))
    stripe_call = Mock(side_effect=AssertionError("Must not use Stripe for a Square refund"))
    monkeypatch.setattr(billing_stripe, "create_order_refund", stripe_call)
    assert refunds.process(refund, force=True)
    refund.refresh_from_db()
    assert refund.state == "succeeded" and refund.entry.channel == "square"
    assert not stripe_call.called
    assert School.objects.filter(pk=attempt.order.school_id).values().get() == before


def test_settings_save_validates_square_without_charging_and_protects_permissions(inventory, square_account, monkeypatch, client):
    actor = get_user_model().objects.create_superuser("provider-operator", "", "test")
    school = inventory[0].school
    inventory[0].payment_provider = "stripe"
    inventory[0].save(update_fields=["payment_provider"])
    location = {"id": "LTEST", "merchant_id": "MTEST", "status": "ACTIVE", "currency": "USD", "country": "US", "capabilities": ["CREDIT_CARD_PROCESSING"]}
    call = Mock(return_value={"location": location})
    monkeypatch.setattr(square, "request", call)
    url = reverse("ops_school_detail", args=[school.slug])
    client.force_login(actor)
    assert b"Booking Payments" in client.get(url).content
    response = client.post(url, {"action": "save_booking_provider", "provider": "square", "expected_provider": "stripe"})
    assert response.status_code == 302 and response.url.endswith("#booking-payments")
    assert call.call_args.args == ("GET", "/locations/LTEST")
    inventory[0].refresh_from_db()
    assert inventory[0].payment_provider == "square"
    with pytest.raises(ValidationError):
        save_provider(school=school, provider="square", expected_provider="stripe", actor=actor)
    member = get_user_model().objects.create_user("not-operator")
    with pytest.raises(PermissionDenied):
        save_provider(school=school, provider="square", expected_provider="square", actor=member)
    client.force_login(member)
    assert client.post(url, {"action": "save_booking_provider", "provider": "stripe"}).status_code == 302


@pytest.mark.parametrize("field,value", [("amount_money", None), ("application_details", None)])
def test_malformed_payment_remains_unknown(attempt, monkeypatch, field, value):
    result = payment_for(attempt)
    result[field] = value
    monkeypatch.setattr(square, "request", Mock(return_value={"payment": result}))
    with pytest.raises(ValidationError):
        submit(attempt, "cnon:card")
    attempt.refresh_from_db()
    assert attempt.state == "unknown" and not LedgerEntry.objects.exists()


def test_refund_webhook_verifies_account_and_settles_once(attempt, booking_member, monkeypatch, client, settings):
    pay(attempt, monkeypatch)
    item = attempt.order.reservation
    cancel_reservation(booking_member.user, item.profile.school.slug, item.public_id)
    refund = approve_refund(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id,
        key=uuid.uuid4(), amount_cents=2500, reason="Canceled")
    result = {"id": "square_refund", "payment_id": attempt.provider_intent, "location_id": "LTEST",
        "reason": square.refund_reason(refund), "amount_money": {"amount": 2500, "currency": "USD"}, "status": "PENDING"}
    monkeypatch.setattr(square, "request", Mock(return_value={"refund": result}))
    assert refunds.process(refund, force=True)
    assert not LedgerEntry.objects.filter(kind="refund").exists()
    result["status"] = "COMPLETED"
    args = {"resource_id": "square_refund", "event_type": "refund.updated", "event_id": str(uuid.uuid4())}
    assert post_event(client, settings, attempt, merchant="OTHER", **args).status_code == 503
    assert not LedgerEntry.objects.filter(kind="refund").exists()
    assert post_event(client, settings, attempt, **args).status_code == 200
    assert post_event(client, settings, attempt, **args).status_code == 200
    refund.refresh_from_db()
    assert refund.state == "succeeded" and refund.entry.channel == "square"
    assert LedgerEntry.objects.filter(kind="refund").count() == 1


@pytest.mark.parametrize("status", ["FAILED", "CANCELED", "PENDING"])
def test_non_completed_recovery_never_records_money(attempt, monkeypatch, status):
    PaymentAttempt.objects.filter(pk=attempt.pk).update(state="unknown", submitted_at=timezone.now())
    monkeypatch.setattr(square, "request", Mock(return_value={"payments": [payment_for(attempt, status)]}))
    assert recover_attempt(attempt, force=True)
    attempt.refresh_from_db()
    assert attempt.state == ("unknown" if status == "PENDING" else "expired")
    assert not LedgerEntry.objects.exists()


def test_checkout_check_and_expiry_actions(attempt, monkeypatch, client):
    url = checkout.open_checkout(attempt)
    page = client.get(url)
    assert page.status_code == 200
    assert b"Complete your payment" in page.content
    assert b"Square sandbox" not in page.content and b"Test card details" not in page.content
    assert b"https://sandbox.web.squarecdn.com/v1/square.js" in page.content
    assert client.post(url, {"action": "retry"}).status_code == 200
    assert PaymentAttempt.objects.count() == 1
    PaymentAttempt.objects.filter(pk=attempt.pk).update(state="unknown", submitted_at=timezone.now())
    monkeypatch.setattr(square, "request", Mock(return_value={"payments": [payment_for(attempt)]}))
    response = client.post(url, {"action": "check"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert response.json()["state"] == "paid"
    assert response.json()["return_url"] == attempt.success_url


def test_expired_unsubmitted_page_cannot_charge(attempt, client):
    url = checkout.open_checkout(attempt)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    response = client.get(url)
    attempt.refresh_from_db()
    assert response.status_code == 200 and attempt.state == "expired"
    assert b"Try another card" in response.content


def test_webhook_ignores_foreign_payments_and_rejects_unsubmitted_ours(attempt, monkeypatch, client, settings):
    call = Mock(return_value={"payment": payment_for(attempt)})
    monkeypatch.setattr(square, "request", call)
    assert post_event(client, settings, attempt).status_code == 503
    result = payment_for(attempt)
    result["reference_id"] = "foreign-non-uuid"
    call.return_value = {"payment": result}
    assert post_event(client, settings, attempt).status_code == 200
    call.return_value = {"payment": None}
    assert post_event(client, settings, attempt).status_code == 503
    assert post_event(client, settings, attempt, resource_id="../bad").status_code == 400
    assert post_event(client, settings, attempt, event_type="ignored.event").status_code == 200
    assert client.post(reverse("scheduling:square_test_webhook"), b"x" * 262145,
                       content_type="application/json").status_code == 400
    assert not LedgerEntry.objects.exists()


@pytest.mark.parametrize("problem", ["live", "disabled", "missing_webhook", "account_changed", "account_disabled", "invalid_provider"])
def test_settings_fail_closed(inventory, square_account, monkeypatch, client, settings, problem):
    actor = get_user_model().objects.create_superuser("settings-operator", "", "test")
    school = inventory[0].school
    monkeypatch.setattr(square, "verified_location", Mock(return_value={"id": "LTEST", "merchant_id": "MTEST"}))
    if problem == "live":
        school.is_demo = False
        school.save(update_fields=["is_demo"])
    elif problem == "disabled":
        inventory[0].enabled = False
        inventory[0].save(update_fields=["enabled"])
    elif problem == "missing_webhook":
        settings.DEBUG = False
        settings.PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET = ""
    elif problem == "account_changed":
        square_account.reference = "OTHER"
        square_account.save(update_fields=["reference"])
    elif problem == "account_disabled":
        square_account.enabled = False
        square_account.save(update_fields=["enabled"])
    with pytest.raises(ValidationError):
        save_provider(school=school, provider="invalid" if problem == "invalid_provider" else "square",
                      expected_provider="square", actor=actor)
    client.force_login(actor)
    response = client.post(reverse("ops_school_detail", args=[school.slug]), {
        "action": "save_booking_provider", "provider": "square", "expected_provider": "square"})
    assert response.status_code == (403 if problem in {"live", "disabled"} else 302 if problem == "invalid_provider" else 200)


def test_settings_creates_only_verified_account(inventory, monkeypatch, settings):
    settings.DEBUG = True
    settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID = "sandbox-sq0idb-test"
    settings.PAYMENTS_SQUARE_TEST_ACCESS_TOKEN = "fake"
    settings.PAYMENTS_SQUARE_TEST_LOCATION_ID = "LTEST"
    school = inventory[0].school
    school.is_demo = True
    school.save(update_fields=["is_demo"])
    actor = get_user_model().objects.create_superuser("new-account-operator", "", "test")
    monkeypatch.setattr(square, "verified_location", Mock(return_value={"id": "LTEST", "merchant_id": "MTEST"}))
    save_provider(school=school, provider="square", expected_provider="stripe", actor=actor)
    account = PaymentAccount.objects.get(school=school, provider="square")
    assert account.reference == "MTEST" and account.location_reference == "LTEST" and account.enabled


@pytest.mark.django_db(transaction=True)
def test_concurrent_card_submissions_call_square_once(attempt, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from django.db import close_old_connections
    entered, release = Event(), Event()
    def payment_request(*args):
        entered.set()
        assert release.wait(10)
        return {"payment": payment_for(attempt)}
    call = Mock(side_effect=payment_request)
    monkeypatch.setattr(square, "request", call)
    def first_submission():
        close_old_connections()
        try:
            submit(PaymentAttempt.objects.select_related("order__account").get(pk=attempt.pk), "cnon:first")
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_submission)
        try:
            assert entered.wait(10)
            with pytest.raises(ValidationError):
                submit(attempt, "cnon:second")
        finally:
            release.set()
        future.result(timeout=10)
    assert call.call_count == 1 and LedgerEntry.objects.count() == 1


@pytest.mark.parametrize("switch_before_submit", [False, True])
def test_customer_experience_uses_square_and_rejects_stale_provider_review(demo, browser, square_account, monkeypatch, switch_before_submit):
    demo.payment_provider = "square"
    demo.save(update_fields=["payment_provider"])
    PaymentAccount.objects.create(school=demo.school, provider="square", scope="platform_demo",
        reference="MTEST", location_reference="LTEST", enabled=True)
    session, data = form_data(browser, demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    assert b"Continue to checkout <" in review.content
    assert b"Square test checkout" not in review.content and b"Stripe test checkout" not in review.content
    data.update(action="checkout", review_token=review.context["review_token"])
    if switch_before_submit:
        demo.payment_provider = "stripe"
        demo.save(update_fields=["payment_provider"])
        result = browser.post(url("choose", pk=session.pk), data)
        assert result.status_code == 400 and not demo.reservations.exists()
        return
    result = browser.post(url("choose", pk=session.pk), data)
    assert result.status_code == 302
    route = result.url
    item = browser.get(route).context["item"]
    checkout_response = browser.post(route, {"action": "payment_checkout"})
    assert checkout_response.status_code == 302 and "/square/pay/" in checkout_response.url
    attempt = item.reservation.order.attempts.get()
    pay(attempt, monkeypatch)
    response = browser.get(checkout_response.url, follow=True)
    assert response.status_code == 200 and b"Booking confirmed" in response.content
    assert item.reservation.order.entries.get().channel == "square"


def test_square_account_admin_cannot_rebind_historical_payments(square_account, client):
    actor = get_user_model().objects.create_superuser("square-account-operator", "", "test")
    client.force_login(actor)
    route = reverse("admin:payments_paymentaccount_change", args=[square_account.pk])
    form = client.get(route).context["adminform"].form
    assert form.fields["enabled"].label == "Enable Square test checkout"
    assert not {"provider", "location_reference", "reference", "school", "scope"} & form.fields.keys()
    assert client.post(route, {"enabled": "on", "provider": "stripe", "reference": "acct_other", "location_reference": "OTHER"}).status_code == 302
    square_account.refresh_from_db()
    assert square_account.provider == "square" and square_account.reference == "MTEST"
    assert square_account.location_reference == "LTEST"
    assert client.post(route, {}).status_code == 302
    square_account.refresh_from_db()
    assert not square_account.enabled and square_account.provider == "square"


def test_provider_dispatch_rejects_unknown_and_cross_provider_events(square_account):
    from core.payments import accounts, providers
    assert not accounts.matches_event_account(square_account, {"account": "MTEST"})
    square_account.provider = "unsupported"
    with pytest.raises(ValidationError):
        accounts.validate_scope(square_account)
    with pytest.raises(ValidationError):
        providers.gateway(square_account)
    assert not providers.ready(square_account)
