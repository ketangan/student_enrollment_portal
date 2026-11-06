"""Square Terminal transport. Settlement still uses the shared Square payment ledger."""
import re
from datetime import timedelta
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ValidationError

from . import square

# Square's documented virtual devices; never accepted for a live seller.
SIMULATIONS = {
    "success": ("Successful payment (up to $25)", "9fa747a2-25ff-48ee-b078-04381f7c828f"),
    "canceled": ("Customer cancellation", "841100b9-ee60-4537-9bcf-e30b2ba5e215"),
    "timeout": ("Timeout", "0a956d49-619a-4530-8e5e-8eac603ffc5e"),
    "offline": ("Offline Terminal", "da40d603-c2ea-4a65-8cfd-f42e36dab0c7"),
}


def maximum_amount_cents(environment, device_id):
    # Square's success simulator leaves larger requests pending instead of rejecting them.
    if environment == "test" and device_id == SIMULATIONS["success"][1]:
        return 2500
    return None


def validate_amount(attempt):
    maximum = maximum_amount_cents(attempt.account.environment, attempt.device_reference)
    if maximum is not None and attempt.amount_cents > maximum:
        raise ValidationError(f"Square Sandbox supports up to ${maximum / 100:.2f} on this test Terminal. Enter a smaller amount.")


def status_message(attempt):
    amount = f"${attempt.amount_cents / 100:.2f}"
    if attempt.close_requested or attempt.terminal_status == "CANCEL_REQUESTED":
        return f"Canceling Terminal payment of {amount}. Waiting for Square to confirm."
    maximum = maximum_amount_cents(attempt.account.environment, attempt.device_reference)
    if maximum is not None and attempt.amount_cents > maximum:
        return f"Square Sandbox cannot approve {amount} on this test Terminal. Cancel this payment and try ${maximum / 100:.2f} or less."
    if attempt.terminal_status == "PENDING":
        return f"Waiting for Terminal to accept the {amount} payment."
    if attempt.terminal_status == "IN_PROGRESS":
        return f"Complete the {amount} payment on the Terminal."
    return f"Waiting for Square to confirm the {amount} payment. Do not collect it again."


def enabled(account):
    return account.provider == "square" and (account.environment == "test" or (
        settings.PAYMENTS_SQUARE_LIVE_ENABLED and settings.PAYMENTS_SQUARE_TERMINAL_LIVE_ENABLED))


def checkout_id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,255}", value) is not None


def validate(attempt, result):
    money = result.get("amount_money") if isinstance(result, dict) else None
    options = result.get("device_options") if isinstance(result, dict) else None
    if (attempt.collection_method != "terminal" or attempt.account.provider != "square"
            or not isinstance(money, dict) or not isinstance(options, dict)
            or not checkout_id(result.get("id"))
            or result.get("reference_id") != str(attempt.public_id)
            or type(money.get("amount")) is not int or money != {"amount": attempt.amount_cents, "currency": "USD"}
            or options.get("device_id") != attempt.device_reference
            or result.get("location_id") != attempt.account.location_reference
            or result.get("app_id") != square.environment_setting(attempt.account.environment, "APPLICATION_ID")
            or result.get("status") not in {"PENDING", "IN_PROGRESS", "CANCEL_REQUESTED", "CANCELED", "COMPLETED"}
            or (attempt.terminal_checkout_id and result["id"] != attempt.terminal_checkout_id)):
        raise ValidationError("Terminal checkout does not match this payment, device or location.")
    return result


def create(attempt):
    if not enabled(attempt.account):
        raise ValidationError("Live Terminal payments are not enabled.")
    validate_amount(attempt)
    result = square.account_request(attempt.account, "POST", "/terminals/checkouts", {
        "idempotency_key": str(attempt.public_id), "checkout": {
            "amount_money": {"amount": attempt.amount_cents, "currency": "USD"},
            "reference_id": str(attempt.public_id), "payment_type": "CARD_PRESENT",
            "device_options": {"device_id": attempt.device_reference, "skip_receipt_screen": False,
                "tip_settings": {"allow_tipping": False}},
        }}).get("checkout")
    return validate(attempt, result)


def retrieve(attempt, resource_id=None):
    identity = resource_id or attempt.terminal_checkout_id
    if identity:
        if not checkout_id(identity):
            raise ValidationError("Invalid Terminal checkout identity.")
        return validate(attempt, square.account_request(attempt.account, "GET",
            "/terminals/checkouts/" + quote(identity, safe="")).get("checkout"))
    # A lost create response is not proof of failure. Search, never create a new charge.
    payload = {"query": {"filter": {"device_id": attempt.device_reference,
        "created_at": {"start_at": (attempt.created_at - timedelta(minutes=1)).isoformat()}}}, "limit": 100}
    matches = []
    for _ in range(3):
        result = square.account_request(attempt.account, "POST", "/terminals/checkouts/search", payload)
        rows = result.get("checkouts", [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValidationError("Square returned invalid Terminal checkouts.")
        matches.extend(row for row in rows if row.get("reference_id") == str(attempt.public_id))
        cursor = result.get("cursor")
        if not cursor:
            break
        if not isinstance(cursor, str) or len(cursor) > 2000:
            raise ValidationError("Square returned an invalid search cursor.")
        payload["cursor"] = cursor
    else:
        raise ValidationError("Terminal payment needs verification. Do not collect another payment.")
    if len(matches) != 1:
        raise ValidationError("Terminal payment needs verification. Do not collect another payment.")
    return validate(attempt, matches[0])


def cancel(attempt):
    identity = attempt.terminal_checkout_id
    if not checkout_id(identity):
        raise ValidationError("Verify the Terminal checkout before canceling it.")
    return validate(attempt, square.account_request(attempt.account, "POST",
        "/terminals/checkouts/" + quote(identity, safe="") + "/cancel", {}).get("checkout"))
