"""Local experience adapter; all charging and settlement stay in the shared payment module."""
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.payments.checkout import open_checkout
from core.payments.models import PaymentAttempt
from scheduling.checkout import prepare_booking_checkout, recover_attempt
from .config import available


def start_checkout(request, profile, item):
    if not available(profile) or not item.reservation.order.account_id:
        raise ValidationError("Stripe test checkout is not enabled for this booking.")
    path = reverse("scheduling:experience_booking", kwargs={
        "school_slug": profile.school.slug, "public_id": item.public_id})
    return_url = request.build_absolute_uri(path + "?checkout_return=1")
    attempt = prepare_booking_checkout(item.reservation, return_url)
    return open_checkout(attempt) or return_url


def verify_return(item):
    """A return URL is only a prompt to check Stripe, never proof of payment."""
    attempt = item.reservation.order.attempts.order_by("pk").last()
    if attempt is None:
        return "No Stripe checkout has been started for this booking."
    checked = True
    if attempt.state in PaymentAttempt.ACTIVE:
        checked = recover_attempt(attempt, force=True)
    attempt.refresh_from_db()
    item.reservation.refresh_from_db()
    if item.reservation.state in {"confirmed", "completed"}:
        return ""
    if not checked or attempt.attention or attempt.lease_until:
        return "Payment verification is pending. Please wait 30 seconds and check again. Do not pay again."
    if attempt.state == "paid":
        return "Payment received in test mode. This reservation needs the studio's review before confirmation."
    if attempt.state == "expired":
        return "Checkout expired without payment. Continue to checkout to check availability again."
    return "No successful payment has been verified. You can return to Stripe Checkout and try again."
