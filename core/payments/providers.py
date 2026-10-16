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
        return square.configured() and (settings.DEBUG or square.webhook_configured())
    return account.provider == "stripe" and billing_stripe.merchant_test_checkout_ready()
