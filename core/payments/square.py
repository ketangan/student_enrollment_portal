"""Square sandbox transport and verified snapshots; no live-token fallback.

Card details stay in Square's Web Payments SDK. A submitted attempt is never
automatically submitted again after uncertainty; recovery only reads Square.
"""
import base64
import hashlib
import hmac
import json
import re
from datetime import timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils import timezone

API_ROOT = "https://connect.squareupsandbox.com/v2"
API_VERSION = "2026-09-16"
DECLINES = {"CARD_DECLINED", "GENERIC_DECLINE", "INSUFFICIENT_FUNDS", "CARD_EXPIRED",
            "CVV_FAILURE", "ADDRESS_VERIFICATION_FAILURE"}


class Declined(ValidationError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValidationError("Square returned an unexpected redirect.")


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is not None


def configured():
    return bool(settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID.startswith("sandbox-sq0idb-")
                and settings.PAYMENTS_SQUARE_TEST_ACCESS_TOKEN
                and identifier(settings.PAYMENTS_SQUARE_TEST_LOCATION_ID))


def webhook_configured():
    url = urlsplit(settings.PAYMENTS_SQUARE_TEST_WEBHOOK_URL)
    return bool(settings.PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET and url.scheme == "https" and url.netloc)


def request(method, path, payload=None):
    if not configured() or not path.startswith("/") or ".." in path:
        raise ValidationError("Square sandbox is not configured.")
    req = Request(API_ROOT + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + settings.PAYMENTS_SQUARE_TEST_ACCESS_TOKEN,
                 "Square-Version": API_VERSION, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect).open(req, timeout=10) as response:
            result = json.loads(response.read(524289))
    except HTTPError as exc:
        try:
            errors = json.loads(exc.read(524289)).get("errors", [])
            codes = {error.get("code") for error in errors}
        except (ValueError, TypeError, AttributeError):
            codes = set()
        if method == "POST" and path == "/payments" and exc.code in {400, 402} and codes and codes <= DECLINES:
            raise Declined("The card was declined. Try another card.") from None
        raise ValidationError("Square could not verify this request. Check payment status before trying again.") from None
    except (OSError, ValueError) as exc:
        raise ValidationError("Square could not be reached. Check payment status before trying again.") from None
    if not isinstance(result, dict) or result.get("errors"):
        raise ValidationError("Square returned an unexpected response.")
    return result


def verified_location():
    location = request("GET", "/locations/" + settings.PAYMENTS_SQUARE_TEST_LOCATION_ID).get("location", {})
    if (not isinstance(location, dict) or location.get("id") != settings.PAYMENTS_SQUARE_TEST_LOCATION_ID
            or not identifier(location.get("merchant_id")) or location.get("status") != "ACTIVE"
            or location.get("currency") != "USD" or location.get("country") != "US"
            or not isinstance(location.get("capabilities"), list)
            or "CREDIT_CARD_PROCESSING" not in location["capabilities"]):
        raise ValidationError("Square needs an active US sandbox location that accepts card payments.")
    return location


def validate_account(account):
    if (account.provider != "square" or account.environment != "test" or account.scope != "platform_demo"
            or not account.school.is_demo or not identifier(account.reference)
            or account.location_reference != settings.PAYMENTS_SQUARE_TEST_LOCATION_ID or not configured()):
        raise ValidationError("Square sandbox account does not match this demo business.")


def validate_payment(attempt, payment):
    if not isinstance(payment, dict):
        raise ValidationError("Square returned an invalid payment.")
    money = payment.get("amount_money", {})
    application = payment.get("application_details")
    if (not isinstance(money, dict) or not isinstance(application, dict)
            or not identifier(payment.get("id")) or payment.get("location_id") != attempt.account.location_reference
            or payment.get("reference_id") != str(attempt.public_id)
            or type(money.get("amount")) is not int or money["amount"] != attempt.amount_cents
            or money.get("currency") != attempt.order.currency.upper()
            or payment.get("source_type") != "CARD"
            or application.get("application_id") != settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID
            or payment.get("status") not in {"COMPLETED", "APPROVED", "PENDING", "FAILED", "CANCELED"}
            or (attempt.provider_intent and payment["id"] != attempt.provider_intent)):
        raise ValidationError("Square payment does not match its reservation, account or amount.")
    if payment["status"] == "COMPLETED" and payment.get("total_money") != money:
        raise ValidationError("Square charged an unexpected total.")


def snapshot(attempt, payment=None, *, expired=False):
    if payment is not None:
        validate_payment(attempt, payment)
    paid = bool(payment and payment["status"] == "COMPLETED")
    expired = expired or bool(payment and payment["status"] in {"FAILED", "CANCELED"})
    return {"id": "sq_checkout_" + attempt.public_id.hex, "mode": "payment", "livemode": False,
        "payment_method_types": ["card"], "currency": attempt.order.currency, "amount_total": attempt.amount_cents,
        "status": "complete" if paid else "expired" if expired else "open", "payment_status": "paid" if paid else "unpaid",
        "payment_intent": payment["id"] if paid else None, "square_payment": payment,
        "url": attempt.checkout_url, "expires_at": int(attempt.expires_at.timestamp()),
        "metadata": {"payment_order": str(attempt.order.public_id), "payment_attempt": str(attempt.public_id), "purpose": attempt.order.purpose}}


def create_payment(attempt, source):
    validate_account(attempt.account)
    payment = request("POST", "/payments", {
        "idempotency_key": str(attempt.public_id), "source_id": source,
        "amount_money": {"amount": attempt.amount_cents, "currency": attempt.order.currency.upper()},
        "location_id": attempt.account.location_reference, "reference_id": str(attempt.public_id),
        "autocomplete": True, "note": f"Pontora reservation {attempt.order.public_id}",
    }).get("payment", {})
    return snapshot(attempt, payment)


def retrieve_payment(attempt):
    validate_account(attempt.account)
    if attempt.provider_intent:
        if not identifier(attempt.provider_intent):
            raise ValidationError("Invalid Square payment reference.")
        return request("GET", "/payments/" + attempt.provider_intent).get("payment", {})
    params = {"location_id": attempt.account.location_reference, "sort_order": "ASC", "limit": 100,
              "begin_time": (attempt.created_at - timedelta(minutes=1)).isoformat(), "end_time": timezone.now().isoformat()}
    matches = []
    for _ in range(3):
        result = request("GET", "/payments?" + urlencode(params))
        payments = result.get("payments", [])
        if not isinstance(payments, list) or not all(isinstance(p, dict) for p in payments):
            raise ValidationError("Square returned an invalid payment list.")
        matches.extend(p for p in payments if p.get("reference_id") == str(attempt.public_id))
        if not result.get("cursor"):
            if len(matches) == 1:
                return matches[0]
            break
        params["cursor"] = result["cursor"]
    raise ValidationError("No unique Square payment found. Do not pay again until the studio verifies it.")


def retrieve_order_checkout(attempt):
    if not attempt.submitted_at:
        return snapshot(attempt)
    return snapshot(attempt, retrieve_payment(attempt))


def refund_reason(refund):
    return f"Pontora {refund.public_id}: {refund.reason}"[:192]


def refund_snapshot(refund, result):
    attempt = refund.attempt
    if (not isinstance(result, dict) or not isinstance(result.get("amount_money"), dict)
            or not identifier(result.get("id")) or result.get("payment_id") != attempt.provider_intent
            or result.get("location_id") != attempt.account.location_reference
            or result.get("reason") != refund_reason(refund)
            or type(result.get("amount_money", {}).get("amount")) is not int
            or result.get("amount_money") != {"amount": refund.amount_cents, "currency": attempt.order.currency.upper()}
            or result.get("status") not in {"PENDING", "COMPLETED", "FAILED", "REJECTED"}):
        raise ValidationError("Square refund does not match the approved payment and amount.")
    return {"id": result["id"], "livemode": False, "currency": attempt.order.currency,
        "amount": refund.amount_cents, "payment_intent": attempt.provider_intent,
        "status": {"PENDING": "pending", "COMPLETED": "succeeded", "FAILED": "failed", "REJECTED": "failed"}[result["status"]],
        "metadata": {"payment_refund": str(refund.public_id), "payment_order": str(attempt.order.public_id)}}


def create_order_refund(refund):
    validate_account(refund.attempt.account)
    return refund_snapshot(refund, request("POST", "/refunds", {
        "idempotency_key": str(refund.public_id), "payment_id": refund.attempt.provider_intent,
        "amount_money": {"amount": refund.amount_cents, "currency": refund.attempt.order.currency.upper()},
        "reason": refund_reason(refund),
    }).get("refund", {}))


def retrieve_order_refund(refund):
    validate_account(refund.attempt.account)
    if refund.provider_refund:
        if not identifier(refund.provider_refund):
            raise ValidationError("Invalid Square refund reference.")
        return refund_snapshot(refund, request("GET", "/refunds/" + refund.provider_refund).get("refund", {}))
    payment = retrieve_payment(refund.attempt)
    validate_payment(refund.attempt, payment)
    ids = payment.get("refund_ids", [])
    if not isinstance(ids, list) or len(ids) > 20 or not all(identifier(value) for value in ids):
        raise ValidationError("Square refund lookup needs operator review.")
    matches = []
    for value in ids:
        result = request("GET", "/refunds/" + value).get("refund", {})
        if not isinstance(result, dict):
            raise ValidationError("Square returned an invalid refund.")
        if result.get("reason") == refund_reason(refund):
            matches.append(result)
    if len(matches) != 1:
        raise ValidationError("No unique Square refund found. Do not submit another refund.")
    return refund_snapshot(refund, matches[0])


def construct_event(payload, signature):
    if not webhook_configured():
        raise ValidationError("Square webhook is not configured.")
    expected = base64.b64encode(hmac.new(settings.PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET.encode(),
        settings.PAYMENTS_SQUARE_TEST_WEBHOOK_URL.encode() + payload, hashlib.sha256).digest()).decode()
    if not isinstance(signature, str) or not signature.isascii() or not hmac.compare_digest(expected, signature):
        raise ValidationError("Invalid Square webhook signature.")
    try:
        event = json.loads(payload)
        if not isinstance(event, dict) or not identifier(event.get("event_id")):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValidationError("Invalid Square webhook payload.") from None
    return event
