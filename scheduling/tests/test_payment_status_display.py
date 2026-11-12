from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.utils import timezone

from core.payments import square
from core.payments.models import PaymentAttempt
from scheduling.checkout import recover_attempt
from .test_reservations import inventory, path
from .test_square_payments import square_account, attempt

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("state,submitted,expired,closing,label", [
    ("pending", False, False, False, "Awaiting customer payment"),
    ("open", False, False, False, "Awaiting customer payment"),
    ("pending", False, True, False, "Unused checkout expired"),
    ("unknown", True, True, False, "Payment status needs verification"),
    ("pending", True, False, False, "Payment status needs verification"),
    ("unknown", True, False, True, "Checkout closure awaiting verification"),
])
def test_online_status_is_honest_and_does_not_poll_or_change_money(
        attempt, booking_member, client, monkeypatch, state, submitted, expired, closing, label):
    now = timezone.now()
    PaymentAttempt.objects.filter(pk=attempt.pk).update(state=state,
        submitted_at=now if submitted else None, expires_at=now + timedelta(minutes=-1 if expired else 10),
        close_requested=closing)
    before = PaymentAttempt.objects.filter(pk=attempt.pk).values().get()
    remote = Mock(side_effect=AssertionError("Rendering must not contact Square"))
    monkeypatch.setattr(square, "account_request", remote)
    client.force_login(booking_member.user)
    page = client.get(path(booking_member, "reservation_detail", public_id=attempt.order.reservation.public_id))
    assert page.status_code == 200 and label.encode() in page.content
    assert b'class="payment-spinner"' not in page.content
    assert b'data-terminal-poll' not in page.content
    assert b'is being verified' not in page.content
    assert page.context["checkout_pending"]
    assert PaymentAttempt.objects.filter(pk=attempt.pk).values().get() == before
    assert not remote.called and not attempt.order.entries.exists()


def test_expired_unsubmitted_checkout_recovery_is_idempotent_and_local(attempt, booking_member, client, monkeypatch):
    PaymentAttempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now()-timedelta(days=1))
    remote = Mock(side_effect=AssertionError("Unsubmitted checkout needs no provider call"))
    monkeypatch.setattr(square, "account_request", remote)
    for _ in range(2):
        recover_attempt(attempt, force=True)
    attempt.refresh_from_db()
    assert attempt.state == "expired" and not attempt.order.entries.exists() and not remote.called
    client.force_login(booking_member.user)
    page = client.get(path(booking_member, "reservation_detail", public_id=attempt.order.reservation.public_id))
    assert not page.context["checkout_pending"] and not page.context["attempts"]
    assert b'Unused checkout expired' not in page.content


def test_unresolved_submitted_checkout_is_not_assumed_unpaid(attempt, monkeypatch):
    PaymentAttempt.objects.filter(pk=attempt.pk).update(submitted_at=timezone.now(),
        expires_at=timezone.now()-timedelta(days=1), state="unknown")
    remote = Mock(side_effect=TimeoutError("Provider unavailable"))
    monkeypatch.setattr(square, "account_request", remote)
    recover_attempt(attempt, force=True)
    attempt.refresh_from_db()
    assert remote.called and attempt.state == "unknown"
    assert not attempt.order.entries.exists()
