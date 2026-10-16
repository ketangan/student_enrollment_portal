import base64
import hashlib
import hmac
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError

from core.payments import square
from .test_square_payments import attempt, square_account, payment_for
from .test_reservations import inventory

pytestmark = pytest.mark.django_db


def test_transport_uses_only_sandbox_and_does_not_follow_redirects(square_account, monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b'{"ok":true}'
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(square, "build_opener", Mock(return_value=opener))
    assert square.request("POST", "/payments", {"amount": 100}) == {"ok": True}
    req = opener.open.call_args.args[0]
    assert req.full_url == "https://connect.squareupsandbox.com/v2/payments"
    assert opener.open.call_args.kwargs["timeout"] == 10
    assert json.loads(req.data) == {"amount": 100}
    with pytest.raises(ValidationError):
        square.NoRedirect().redirect_request(req, None, 302, "", {}, "https://evil.example")
    for path in ["https://evil.example", "/../live"]:
        with pytest.raises(ValidationError):
            square.request("GET", path)


@pytest.mark.parametrize("body,status,declined", [
    ({"errors": [{"code": "CARD_DECLINED"}]}, 400, True),
    ({"errors": [{"code": "CVV_FAILURE"}]}, 400, True),
    ({"errors": [{"code": "CARD_DECLINED"}, {"code": "INTERNAL_ERROR"}]}, 400, False),
    ({"errors": [{"code": "CARD_DECLINED"}]}, 500, False),
    ({"errors": [{"code": "INVALID_ACCESS_TOKEN", "detail": "secret should not appear"}]}, 401, False),
    ([], 502, False), ("not-json", 400, False),
])
def test_transport_errors_are_safe_and_only_definitive_declines_allow_retry(square_account, monkeypatch, body, status, declined):
    raw = json.dumps(body).encode() if not isinstance(body, str) else body.encode()
    error = HTTPError("https://sandbox", status, "error", {}, io.BytesIO(raw))
    monkeypatch.setattr(square, "build_opener", Mock(return_value=Mock(open=Mock(side_effect=error))))
    with pytest.raises(ValidationError) as caught:
        square.request("POST", "/payments", {})
    assert isinstance(caught.value, square.Declined) == declined
    assert "secret should not appear" not in str(caught.value)


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"errors":[{"code":"bad"}]}'])
def test_invalid_success_body_fails_closed(square_account, monkeypatch, raw):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = raw
    monkeypatch.setattr(square, "build_opener", Mock(return_value=Mock(open=Mock(return_value=response))))
    with pytest.raises(ValidationError):
        square.request("GET", "/locations")


def test_network_error_is_not_a_decline(square_account, monkeypatch):
    monkeypatch.setattr(square, "build_opener", Mock(return_value=Mock(open=Mock(side_effect=OSError("secret")))))
    with pytest.raises(ValidationError) as caught:
        square.request("POST", "/payments", {})
    assert not isinstance(caught.value, square.Declined) and "secret" not in str(caught.value)


@pytest.mark.parametrize("field,value", [("status", "INACTIVE"), ("currency", "CAD"),
    ("country", "CA"), ("capabilities", []), ("id", "OTHER"), ("merchant_id", "../bad")])
def test_location_must_be_active_and_card_capable(square_account, monkeypatch, field, value):
    location = {"id": "LTEST", "merchant_id": "MTEST", "status": "ACTIVE", "currency": "USD", "country": "US", "capabilities": ["CREDIT_CARD_PROCESSING"]}
    location[field] = value
    monkeypatch.setattr(square, "request", Mock(return_value={"location": location}))
    with pytest.raises(ValidationError):
        square.verified_location()


@pytest.mark.parametrize("field,value", [("provider", "stripe"), ("environment", "live"),
    ("scope", "connected"), ("reference", "../bad"), ("location_reference", "OTHER")])
def test_account_binding_fails_closed(square_account, field, value):
    setattr(square_account, field, value)
    with pytest.raises(ValidationError):
        square.validate_account(square_account)


def test_live_school_cannot_use_shared_sandbox(square_account):
    square_account.school.is_demo = False
    with pytest.raises(ValidationError):
        square.validate_account(square_account)


def test_missing_configuration_never_falls_back(square_account, settings):
    settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID = "sq0idp-live"
    settings.STRIPE_SECRET_KEY = "sk_live_must_not_use"
    assert not square.configured()
    with pytest.raises(ValidationError):
        square.request("GET", "/locations")


def test_recovery_lookup_is_bounded_and_requires_a_unique_payment(attempt, monkeypatch):
    payment = payment_for(attempt)
    mock = Mock(side_effect=[{"payments": [], "cursor": "page2"}, {"payments": [payment]}])
    monkeypatch.setattr(square, "request", mock)
    assert square.retrieve_payment(attempt) == payment
    assert "cursor=page2" in mock.call_args.args[1]
    for result in [{"payments": [payment, payment]}, {"payments": [], "cursor": "more"}]:
        mock = Mock(return_value=result)
        monkeypatch.setattr(square, "request", mock)
        with pytest.raises(ValidationError):
            square.retrieve_payment(attempt)
        assert mock.call_count <= 3
    attempt.provider_intent = payment["id"]
    monkeypatch.setattr(square, "request", Mock(return_value={"payment": payment}))
    assert square.retrieve_payment(attempt) == payment
    attempt.provider_intent = "../../other"
    with pytest.raises(ValidationError):
        square.retrieve_payment(attempt)


def test_unsubmitted_recovery_does_not_contact_square(attempt, monkeypatch):
    call = Mock(side_effect=AssertionError("No payment was submitted"))
    monkeypatch.setattr(square, "request", call)
    assert square.retrieve_order_checkout(attempt)["status"] == "open"
    assert not call.called


def test_webhook_uses_configured_url_and_constant_time_signature(square_account, settings):
    payload = json.dumps({"event_id": "event_1", "type": "payment.updated"}).encode()
    signature = base64.b64encode(hmac.new(settings.PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET.encode(),
        settings.PAYMENTS_SQUARE_TEST_WEBHOOK_URL.encode() + payload, hashlib.sha256).digest()).decode()
    assert square.construct_event(payload, signature)["event_id"] == "event_1"
    for wrong in ["invalid", "\u00e9", ""]:
        with pytest.raises(ValidationError):
            square.construct_event(payload, wrong)
    settings.PAYMENTS_SQUARE_TEST_WEBHOOK_URL += "wrong"
    with pytest.raises(ValidationError):
        square.construct_event(payload, signature)
    settings.PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET = ""
    with pytest.raises(ValidationError):
        square.construct_event(payload, signature)


def test_refund_binding_and_recovery(attempt, monkeypatch):
    import uuid
    attempt.provider_intent = "square_payment"
    refund = SimpleNamespace(attempt=attempt, public_id=uuid.uuid4(), amount_cents=500,
                             reason="Customer cancellation", provider_refund=None)
    result = {"id": "square_refund", "payment_id": "square_payment", "location_id": "LTEST",
        "reason": square.refund_reason(refund), "amount_money": {"amount": 500, "currency": "USD"}, "status": "PENDING"}
    assert square.refund_snapshot(refund, result)["status"] == "pending"
    payment = payment_for(attempt)
    payment.update(id=attempt.provider_intent, refund_ids=["square_refund"])
    mock = Mock(side_effect=[{"payment": payment}, {"refund": result}])
    monkeypatch.setattr(square, "request", mock)
    assert square.retrieve_order_refund(refund)["id"] == "square_refund"
    refund.provider_refund = "square_refund"
    monkeypatch.setattr(square, "request", Mock(return_value={"refund": result}))
    assert square.retrieve_order_refund(refund)["status"] == "pending"
    refund.provider_refund = "../../evil"
    with pytest.raises(ValidationError):
        square.retrieve_order_refund(refund)
    refund.provider_refund = None
    for ids in [[], ["id"] * 21, ["../../evil"]]:
        payment["refund_ids"] = ids
        monkeypatch.setattr(square, "request", Mock(return_value={"payment": payment}))
        with pytest.raises(ValidationError):
            square.retrieve_order_refund(refund)
    for field, value in [("payment_id", "other"), ("location_id", "other"), ("reason", "other"),
                         ("amount_money", {"amount": 999, "currency": "USD"}), ("status", "unknown")]:
        with pytest.raises(ValidationError):
            square.refund_snapshot(refund, {**result, field: value})
