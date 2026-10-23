"""Live routing uses mocked transport only; these tests never contact Square."""
import base64
import hashlib
import hmac
import json
import uuid
from io import StringIO
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.db import IntegrityError, transaction
from django.urls import reverse

from core.payments import checkout, refunds, square
from core.payments.models import PaymentAccount, ProviderEventReceipt
from core.payments.providers import ready
from core.payments.settings import save_provider
from core.payments.service import totals
from scheduling.checkout import approve_refund, prepare_booking_checkout, recover_attempt
from scheduling.integrations import vault
from scheduling.integrations.square_connection import update_locations
from scheduling.models import SquareConnection
from scheduling.square_checkout import submit
from scheduling.reservations import booking_account, cancel_reservation
from .test_reservations import inventory
from .test_square_locations import oauth_attempt
from .test_square_payments import attempt, square_account, payment_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def live_attempt(oauth_attempt, settings):
    settings.PAYMENTS_SQUARE_LIVE_APPLICATION_ID = "sq0idp-live-app"
    settings.PAYMENTS_SQUARE_LIVE_APPLICATION_SECRET = "live-app-secret"
    settings.PAYMENTS_SQUARE_LIVE_OAUTH_REDIRECT_URI = "https://testserver/bookings/square/oauth/callback/"
    settings.PAYMENTS_SQUARE_LIVE_WEBHOOK_SECRET = "live-hook-secret"
    settings.PAYMENTS_SQUARE_LIVE_WEBHOOK_URL = "https://testserver/bookings/square/live/webhook/"
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = True
    account = oauth_attempt.account
    account.school.is_demo = False
    account.school.save(update_fields=["is_demo"])
    account.environment = "live"
    account.save(update_fields=["environment"])
    oauth_attempt.order.environment = "live"
    oauth_attempt.order.save(update_fields=["environment"])
    connection = SquareConnection.objects.get(profile=oauth_attempt.order.reservation.profile)
    connection.environment = "live"
    connection.application_id = settings.PAYMENTS_SQUARE_LIVE_APPLICATION_ID
    connection.credential_ciphertext = vault.seal(connection, json.dumps({"access_token": "live-seller-token",
        "refresh_token": "live-refresh", "merchant_id": connection.merchant_id, "application_id": connection.application_id}))
    connection.save()
    return oauth_attempt


def live_payment(attempt):
    payment = payment_for(attempt)
    payment["application_details"]["application_id"] = "sq0idp-live-app"
    return payment


def pay(attempt, monkeypatch):
    call = Mock(return_value={"payment": live_payment(attempt)})
    monkeypatch.setattr(square, "request", call)
    submit(attempt, "cnon:mock-live-token")
    attempt.refresh_from_db()
    return call


def test_live_deposit_balance_and_sdk(live_attempt, monkeypatch, client):
    page = client.get(checkout.open_checkout(live_attempt))
    assert page.status_code == 200
    assert b'https://web.squarecdn.com/v1/square.js' in page.content
    assert b'sandbox.web.squarecdn.com' not in page.content
    assert b'sq0idp-live-app' in page.content and b'live-seller-token' not in page.content
    pay(live_attempt, monkeypatch)
    assert live_attempt.state == "paid" and live_attempt.entry.environment == "live"
    balance = prepare_booking_checkout(live_attempt.order.reservation, live_attempt.success_url)
    assert balance.amount_cents == 7500
    pay(balance, monkeypatch)
    assert totals(balance.order)["outstanding_cents"] == 0
    assert list(balance.order.entries.values_list("environment", flat=True)) == ["live", "live"]


@pytest.mark.parametrize("problem", ["gate", "webhook", "demo", "order_environment", "application"])
def test_live_checkout_fails_closed(live_attempt, monkeypatch, settings, problem):
    transport = Mock()
    monkeypatch.setattr(square, "request", transport)
    if problem == "gate":
        settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    elif problem == "webhook":
        settings.PAYMENTS_SQUARE_LIVE_WEBHOOK_SECRET = ""
    elif problem == "demo":
        live_attempt.account.school.is_demo = True
        live_attempt.account.school.save()
    elif problem == "order_environment":
        live_attempt.order.environment = "test"
        live_attempt.order.save()
    else:
        settings.PAYMENTS_SQUARE_LIVE_APPLICATION_ID = "sq0idp-other-app"
    with pytest.raises(ValidationError):
        submit(live_attempt, "cnon:test")
    assert not transport.called
    live_attempt.refresh_from_db()
    assert not live_attempt.submitted_at and not live_attempt.order.entries.exists()


def test_live_transport_never_uses_sandbox_token_or_host(live_attempt, monkeypatch, settings):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"payment": {}}'
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(square, "build_opener", Mock(return_value=opener))
    square.account_request(live_attempt.account, "GET", "/payments/P1")
    sent = opener.open.call_args.args[0]
    assert sent.full_url == "https://connect.squareup.com/v2/payments/P1"
    assert sent.get_header("Authorization") == "Bearer live-seller-token"
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    with pytest.raises(ValidationError, match="not enabled"):
        square.create_payment(live_attempt, "cnon:test")
    assert opener.open.call_count == 1


def test_recovery_remains_available_when_live_intake_disabled(live_attempt, monkeypatch, settings):
    monkeypatch.setattr(square, "request", Mock(side_effect=ValidationError("Timeout")))
    with pytest.raises(ValidationError):
        submit(live_attempt, "cnon:test")
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    read = Mock(return_value={"payments": [live_payment(live_attempt)]})
    monkeypatch.setattr(square, "request", read)
    assert recover_attempt(live_attempt, force=True)
    live_attempt.refresh_from_db()
    assert live_attempt.state == "paid"
    assert read.call_args.args[0] == "GET"
    assert live_attempt.order.entries.count() == 1
    recover_attempt(live_attempt, force=True)
    assert live_attempt.order.entries.count() == 1


def test_recovery_command_requires_explicit_live_environment(live_attempt, monkeypatch, settings):
    monkeypatch.setattr(square, "request", Mock(side_effect=ValidationError("Timeout")))
    with pytest.raises(ValidationError):
        submit(live_attempt, "cnon:test")
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    transport = Mock(return_value={"payments": [live_payment(live_attempt)]})
    monkeypatch.setattr(square, "request", transport)
    output = StringIO()
    call_command("reconcile_booking_payments", stdout=output)
    assert "Checked 0" in output.getvalue() and not transport.called
    call_command("reconcile_booking_payments", environment="live", school="another-school", stdout=StringIO())
    assert not transport.called
    call_command("reconcile_booking_payments", environment="live", stdout=StringIO())
    live_attempt.refresh_from_db()
    assert live_attempt.state == "paid" and live_attempt.entry.environment == "live"
    assert transport.call_args.args[0] == "GET"
    recover_attempt(live_attempt, force=True)
    assert live_attempt.order.entries.count() == 1


def test_refund_remains_live_when_intake_disabled(live_attempt, monkeypatch, settings, booking_member):
    pay(live_attempt, monkeypatch)
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    cancel_reservation(booking_member.user, booking_member.profile.school.slug, live_attempt.order.reservation.public_id)
    refund = approve_refund(booking_member.user, booking_member.profile.school.slug,
        live_attempt.order.reservation.public_id, live_attempt.public_id,
        key=uuid.uuid4(), amount_cents=1000, reason="Test refund")
    result = {"id": "LIVE_REFUND", "payment_id": live_attempt.provider_intent,
        "location_id": live_attempt.account.location_reference, "reason": square.refund_reason(refund),
        "amount_money": {"amount": 1000, "currency": "USD"}, "status": "COMPLETED"}
    monkeypatch.setattr(square, "request", Mock(return_value={"refund": result}))
    assert refunds.process(refund, force=True)
    refund.refresh_from_db()
    assert refund.state == "succeeded" and refund.entry.environment == "live"
    bad = square.refund_snapshot(refund, result)
    bad["livemode"] = False
    with pytest.raises(ValidationError):
        refunds.apply_snapshot(refund, bad)


def test_live_signed_webhook_isolated_and_replay_safe(live_attempt, monkeypatch, settings, client):
    pay(live_attempt, monkeypatch)
    event = {"event_id": "LIVE_EVENT", "merchant_id": live_attempt.account.reference,
        "type": "payment.updated", "data": {"id": live_attempt.provider_intent}}
    payload = json.dumps(event).encode()
    signature = base64.b64encode(hmac.new(settings.PAYMENTS_SQUARE_LIVE_WEBHOOK_SECRET.encode(),
        settings.PAYMENTS_SQUARE_LIVE_WEBHOOK_URL.encode() + payload, hashlib.sha256).digest()).decode()
    kwargs = dict(data=payload, content_type="application/json", HTTP_X_SQUARE_HMACSHA256_SIGNATURE=signature)
    assert client.post(reverse("scheduling:square_test_webhook"), **kwargs).status_code == 400
    for _ in range(2):
        assert client.post(reverse("scheduling:square_live_webhook"), **kwargs).status_code == 200
    assert ProviderEventReceipt.objects.get().environment == "live"
    assert live_attempt.order.entries.count() == 1


def test_live_snapshot_rejects_sandbox_evidence(live_attempt):
    with pytest.raises(ValidationError):
        square.snapshot(live_attempt, payment_for(live_attempt))
    snapshot = square.snapshot(live_attempt, live_payment(live_attempt))
    snapshot["livemode"] = False
    with pytest.raises(ValidationError):
        checkout.apply_snapshot(live_attempt, snapshot)


def test_live_mapping_can_be_prepared_before_activation(live_attempt, monkeypatch, settings, booking_member):
    profile = live_attempt.order.reservation.profile
    profile.payment_provider = "stripe"
    profile.save()
    connection = SquareConnection.objects.get(profile=profile)
    location = live_attempt.order.reservation.room.location
    actor = get_user_model().objects.create_superuser("live-operator", password="test")
    request = RequestFactory().post("/", secure=True)
    request.user = actor
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    monkeypatch.setattr(square, "list_locations", Mock(return_value=[{"id": "NEWLIVE", "name": "Live studio"}]))
    update_locations(request, profile.school.slug, connection.revision, mappings={f"location_{location.pk}": "NEWLIVE"})
    mapping = connection.mappings.get(location=location)
    assert mapping.account.environment == "live"
    assert not ready(mapping.account)
    with pytest.raises(ValidationError, match="not been activated"):
        save_provider(school=profile.school, provider="square", expected_provider="stripe", actor=actor)
    profile.refresh_from_db()
    assert profile.payment_provider == "stripe"
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = True
    save_provider(school=profile.school, provider="square", expected_provider="stripe", actor=actor)
    profile.refresh_from_db()
    assert booking_account(profile, True, location).pk == mapping.account.pk
    assert live_attempt.account.location_reference == "LTEST"


def test_demo_signature_cannot_reach_live_account(live_attempt, monkeypatch, settings, client):
    event = {"event_id": "CROSS_ENV", "merchant_id": live_attempt.account.reference,
        "type": "payment.updated", "data": {"id": "LIVE_PAYMENT"}}
    payload = json.dumps(event).encode()
    signature = base64.b64encode(hmac.new(settings.PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET.encode(),
        settings.PAYMENTS_SQUARE_TEST_WEBHOOK_URL.encode() + payload, hashlib.sha256).digest()).decode()
    transport = Mock()
    monkeypatch.setattr(square, "request", transport)
    response = client.post(reverse("scheduling:square_test_webhook"), payload, content_type="application/json",
        HTTP_X_SQUARE_HMACSHA256_SIGNATURE=signature)
    assert response.status_code == 503 and not transport.called
    assert not ProviderEventReceipt.objects.exists()


@pytest.mark.parametrize("provider,scope", [("stripe", "connected"), ("square", "platform_demo")])
def test_live_unsupported_account_cannot_be_created(live_attempt, provider, scope):
    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAccount.objects.create(school=live_attempt.account.school, provider=provider, scope=scope,
            environment="live", reference="OTHER", location_reference="LOC" if provider == "square" else "")
