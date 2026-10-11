"""Several prospects can share a sandbox without sharing reservations or live billing."""
from types import SimpleNamespace
from unittest.mock import MagicMock
from io import StringIO

import pytest
import stripe
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction

from core.models import School
from core.payments import accounts, checkout, service
from core.payments.models import LedgerEntry, PaymentAccount, PaymentAttempt, ProviderEventReceipt
from core.services import billing_stripe as gateway
from .test_checkout import event_for, post_event, session_for
from .test_experience_demo import browser, demo, local
from .test_experience_stripe import booking, open_stripe, stripe_demo

pytestmark = pytest.mark.django_db


@pytest.fixture
def shared_demo(stripe_demo, settings):
    settings.PAYMENTS_STRIPE_TEST_ACCOUNT_ID = "acct_sharedSandbox"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_booking_sandbox"
    settings.PAYMENTS_STRIPE_TEST_METHOD_CONFIG = "pmc_shared_cards"
    settings.STRIPE_MODE = "live"
    settings.STRIPE_SECRET_KEY = "sk_live_never_use_for_demo"
    account = PaymentAccount.objects.get(school=stripe_demo.school)
    account.reference = settings.PAYMENTS_STRIPE_TEST_ACCOUNT_ID
    account.scope = PaymentAccount.Scope.PLATFORM_DEMO
    account.save()
    return stripe_demo


def test_shared_sandbox_signed_webhook_confirms_without_browser_return(shared_demo, browser, settings, monkeypatch):
    before = School.objects.filter(pk=shared_demo.school_id).values().get()
    route, item = booking(browser, shared_demo)
    attempt, _ = open_stripe(browser, route, monkeypatch)
    event = event_for(attempt)
    event.pop("account")
    assert post_event(browser, settings, event).status_code == 200
    assert post_event(browser, settings, event).status_code == 200
    item.reservation.refresh_from_db()
    assert item.reservation.state == "confirmed"
    assert LedgerEntry.objects.filter(order=attempt.order).count() == 1
    assert ProviderEventReceipt.objects.filter(attempt=attempt).count() == 1
    assert School.objects.filter(pk=shared_demo.school_id).values().get() == before
    assert settings.STRIPE_MODE == "live"


def test_test_transport_omits_connected_scope_and_never_uses_live_key(shared_demo, browser, monkeypatch):
    route, item = booking(browser, shared_demo)
    attempt = checkout.prepare_attempt(item.reservation.order, return_url="https://demo.example.com/return")
    sdk = MagicMock()
    sdk.v1.checkout.sessions.create.return_value = session_for(attempt)
    sdk.v1.checkout.sessions.retrieve.return_value = session_for(attempt)
    sdk.v1.checkout.sessions.expire.return_value = session_for(attempt, status="expired")
    sdk.v1.refunds.create.return_value = {"id": "re_test", "status": "succeeded"}
    sdk.v1.refunds.retrieve.return_value = {"id": "re_test", "status": "succeeded"}
    factory = MagicMock(return_value=sdk)
    monkeypatch.setattr(stripe, "StripeClient", factory)
    original_global_key = stripe.api_key
    assert gateway.create_order_checkout(attempt)["livemode"] is False
    params = sdk.v1.checkout.sessions.create.call_args.kwargs["params"]
    assert "payment_method_types" not in params
    assert params["payment_method_configuration"] == "pmc_shared_cards"
    attempt.provider_session = "cs_test_provider"
    assert gateway.retrieve_order_checkout(attempt)["livemode"] is False
    assert gateway.expire_order_checkout(attempt, attempt.provider_session)["status"] == "expired"
    refund = SimpleNamespace(attempt=attempt, public_id="refund-id", amount_cents=100, provider_refund="re_test")
    assert gateway.create_order_refund(refund)["status"] == "succeeded"
    assert gateway.retrieve_order_refund(refund)["status"] == "succeeded"
    assert len(factory.call_args_list) == 5
    for call in factory.call_args_list:
        assert call.args == ("sk_test_local_only",)
        assert "stripe_account" not in call.kwargs
    assert stripe.api_key == original_global_key


@pytest.mark.parametrize("methods", [["card", "us_bank_account"], ["link"], None])
def test_checkout_never_exposes_unsupported_deferred_methods(shared_demo, browser, settings, monkeypatch, methods):
    route, item = booking(browser, shared_demo)
    attempt = checkout.prepare_attempt(item.reservation.order, return_url="https://demo.example.com/return")
    sdk = MagicMock()
    sdk.v1.checkout.sessions.create.return_value = {**session_for(attempt), "payment_method_types": methods}
    monkeypatch.setattr(stripe, "StripeClient", MagicMock(return_value=sdk))
    with pytest.raises(ValidationError, match="could not be verified"):
        checkout.open_checkout(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "unknown" and not attempt.checkout_url
    with pytest.raises(ValidationError, match="does not match"):
        checkout.apply_snapshot(attempt, sdk.v1.checkout.sessions.create.return_value)
    settings.PAYMENTS_STRIPE_TEST_METHOD_CONFIG = ""
    sdk.reset_mock()
    with pytest.raises(ValidationError, match="not configured"):
        gateway.create_order_checkout(attempt)
    sdk.v1.checkout.sessions.create.assert_not_called()


@pytest.mark.parametrize("change", ["live_event", "live_session", "account_envelope", "amount", "order", "bad_secret"])
def test_wrong_event_cannot_settle_shared_demo(shared_demo, browser, settings, monkeypatch, change):
    route, item = booking(browser, shared_demo)
    attempt, _ = open_stripe(browser, route, monkeypatch)
    event = event_for(attempt)
    event.pop("account")
    session = event["data"]["object"]
    if change == "live_event": event["livemode"] = True
    if change == "live_session": session["livemode"] = True
    if change == "account_envelope": event["account"] = attempt.order.merchant_reference
    if change == "amount": session["amount_total"] += 1
    if change == "order": session["metadata"]["payment_order"] = "wrong-order"
    assert post_event(browser, settings, event, secret="whsec_wrong" if change == "bad_secret" else None).status_code == 400
    assert not LedgerEntry.objects.filter(order=attempt.order).exists()
    item.reservation.refresh_from_db()
    assert item.reservation.state == "held"


def test_same_account_two_businesses_still_rejects_cross_business_event(shared_demo, browser, settings, monkeypatch):
    route, item = booking(browser, shared_demo)
    attempt, _ = open_stripe(browser, route, monkeypatch)
    other = School.objects.create(slug="other-shared-demo", is_demo=True)
    account = PaymentAccount.objects.create(school=other, reference=attempt.account.reference, scope="platform_demo", enabled=True)
    order = service.create_order(school=other, purpose="booking", source_key="another-booking",
        total_cents=attempt.amount_cents, deposit_cents=0, description="Another prospect", account=account)
    second = checkout.prepare_attempt(order, return_url="https://demo.example.com/return")
    second.provider_session = "cs_test_other_business"
    second.save(update_fields=["provider_session"])
    event = event_for(attempt)
    event.pop("account")
    event["data"]["object"]["metadata"].update(payment_attempt=str(second.public_id), payment_order=str(order.public_id))
    assert post_event(browser, settings, event).status_code == 400
    assert not LedgerEntry.objects.filter(order__in=[order, attempt.order]).exists()


def test_failed_test_payment_is_not_enrollment_or_subscription_failure(shared_demo, browser, settings, monkeypatch):
    route, item = booking(browser, shared_demo)
    attempt, _ = open_stripe(browser, route, monkeypatch)
    notices = MagicMock()
    monkeypatch.setattr("core.payments.notifications.queue_payment_notice", notices)
    event = {"id": "evt_failure", "type": "payment_intent.payment_failed", "livemode": False,
        "data": {"object": {"id": "pi_failed", "livemode": False, "amount": attempt.amount_cents,
        "currency": "usd", "metadata": session_for(attempt)["metadata"]}}}
    assert post_event(browser, settings, event).status_code == 200
    assert notices.call_args.kwargs["livemode"] is False
    assert notices.call_args.kwargs["school"] == shared_demo.school
    assert notices.call_args.kwargs["key"] == "booking:acct_sharedSandbox:evt_failure"
    assert not LedgerEntry.objects.filter(order=attempt.order).exists()
    item.reservation.refresh_from_db()
    assert item.reservation.state == "held"


@pytest.mark.parametrize("scope,reference,is_demo", [
    ("platform_demo", "acct_wrong", True), ("platform_demo", "acct_expected", False),
    ("platform_demo", "invalid", True), ("invalid", "acct_expected", True),
])
def test_invalid_shared_account_scope_rejected(settings, scope, reference, is_demo):
    settings.PAYMENTS_STRIPE_TEST_ACCOUNT_ID = "acct_expected"
    with pytest.raises(ValidationError):
        accounts.validate_scope(SimpleNamespace(scope=scope, reference=reference, school=SimpleNamespace(is_demo=is_demo)))


def test_gateway_rejects_wrong_sandbox_account_and_unknown_scope(settings):
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_key"
    settings.PAYMENTS_STRIPE_TEST_ACCOUNT_ID = "acct_expected"
    for scope in ("platform_demo", "unknown"):
        with pytest.raises(ValidationError):
            gateway._merchant_test_client("acct_wrong", scope=scope)


def test_connected_account_reference_stays_unique():
    first = School.objects.create(slug="first-connected")
    second = School.objects.create(slug="second-connected")
    PaymentAccount.objects.create(school=first, reference="acct_unique")
    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAccount.objects.create(school=second, reference="acct_unique")


def test_manual_recovery_can_be_limited_to_one_demo_and_never_touches_live_orders(shared_demo, browser, settings, monkeypatch):
    route, item = booking(browser, shared_demo)
    attempt, _ = open_stripe(browser, route, monkeypatch)
    other = School.objects.create(slug="other-recovery-demo", is_demo=True)
    account = PaymentAccount.objects.create(school=other, reference=attempt.account.reference, scope="platform_demo", enabled=True)
    order = service.create_order(school=other, purpose="booking", source_key="other",
        total_cents=1000, deposit_cents=0, description="Other", account=account)
    second = checkout.prepare_attempt(order, return_url="https://example.com/return")
    recover = MagicMock(return_value=True)
    monkeypatch.setattr("scheduling.management.commands.reconcile_booking_payments.recover_attempt", recover)
    out = StringIO()
    call_command("reconcile_booking_payments", school=shared_demo.school.slug, stdout=out)
    assert "Checked 1; verified 1" in out.getvalue()
    assert recover.call_args.args[0].pk == attempt.pk
    recover.reset_mock()
    type(order).objects.filter(pk__in=[order.pk, attempt.order_id]).update(environment="live")
    call_command("reconcile_booking_payments", stdout=StringIO())
    recover.assert_not_called()
    second.refresh_from_db()
    assert second.checked_at is None and second.lease_until is None
