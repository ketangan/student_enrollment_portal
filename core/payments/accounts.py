"""Explicit test-merchant scopes, independent of platform subscription mode."""
from django.conf import settings
from django.core.exceptions import ValidationError

from .models import PaymentAccount


def validate_scope(account):
    if account.provider == "square":
        from .square import validate_account
        return validate_account(account)
    if account.provider != "stripe":
        raise ValidationError("Unsupported payment provider.")
    if account.scope == PaymentAccount.Scope.CONNECTED:
        return
    if (account.scope != PaymentAccount.Scope.PLATFORM_DEMO or not account.school.is_demo
            or not account.reference.startswith("acct_")
            or account.reference != settings.PAYMENTS_STRIPE_TEST_ACCOUNT_ID):
        raise ValidationError("Shared sandbox checkout requires a demo business and the configured test account.")


def matches_event_account(account, event):
    if account.provider != "stripe":
        return False
    validate_scope(account)
    if account.scope == PaymentAccount.Scope.PLATFORM_DEMO:
        # Platform events have no connected-account envelope. The dedicated signing
        # secret plus order/attempt metadata bind them to the configured sandbox.
        return event.get("account") is None
    return event.get("account") == account.reference
