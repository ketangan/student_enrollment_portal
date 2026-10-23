"""Provider selection for booking payments only. Subscription billing stays Stripe."""
from django.core.exceptions import ValidationError
from django.conf import settings

from core.services import billing_stripe
from . import square


def gateway(account):
    if account.provider == "stripe":
        return billing_stripe
    if account.provider == "square":
        return square
    raise ValidationError("Unsupported payment provider.")


def ready(account):
    if account.provider == "square":
        if account.environment == "live":
            return settings.PAYMENTS_SQUARE_LIVE_ENABLED and square.configured(account) and square.webhook_configured("live")
        return square.configured(account) and (settings.DEBUG or square.webhook_configured())
    return account.provider == "stripe" and billing_stripe.merchant_test_checkout_ready()
