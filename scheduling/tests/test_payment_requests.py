import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404
from django.db import close_old_connections
from django.urls import reverse
from django.utils import timezone

from core.payments.models import PaymentAccount, LedgerEntry
from core.payments.service import totals
from scheduling import payment_requests as requests, messaging, party_notifications, checkout, reservations
from scheduling.message_models import BookingMessage
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def configured(stock, booking, settings):
    settings.DEBUG = True
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.EMAIL_BACKEND = messaging.LOCAL_BACKEND
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_local_tests"
    stock.profile.party_notifications_enabled = stock.profile.message_preview_enabled = True
    stock.profile.save()
    account = PaymentAccount.objects.create(school=stock.school, reference="acct_local_test", enabled=True)
    booking.order.account = account
    booking.order.merchant_reference = account.reference
    booking.order.deposit_cents = 2500
    booking.order.save()
    booking.snapshot["staff_managed"] = True
    booking.hold_until = timezone.now() - timedelta(days=3)
    booking.save()


def token(stock, booking):
    return requests.context_for(stock.user, booking, totals(booking.order))["payment_request_token"]


def send(stock, booking, value=None):
    return requests.send(stock.user, stock.school.slug, booking.public_id, value or token(stock, booking))


def paid(stock, booking, amount=2500):
    return LedgerEntry.objects.create(order=booking.order, school=booking.profile.school,
        environment="test", actor=stock.user, key=uuid.uuid4(), kind="payment", amount_cents=amount, reference=str(uuid.uuid4()))


def test_request_reuses_outbox_and_only_paid_confirmation_has_guests(stock, booking, mailoutbox):
    party_notifications.capture_for_reservation(booking.pk)
    assert not BookingMessage.objects.exists()
    notice = send(stock, booking)
    assert notice.state == "queued" and notice.notice_kind == "payment_request"
    assert "Deposit due: $25.00" in notice.body and "remaining amount of $100.00" in notice.body
    assert "/pay/" in notice.body and "/guests/" not in notice.body
    assert "Room" in notice.body and "2 participants" in notice.body
    assert messaging.process_batch(stock.profile.pk) == 1
    assert mailoutbox[0].to == [booking.party_request.organizer_email]
    assert not mailoutbox[0].attachments
    booking.refresh_from_db()
    assert booking.state == "confirmed" and booking.hold_until < timezone.now()
    paid(stock, booking)
    party_notifications.capture_for_reservation(booking.pk)
    assert "/guests/" in BookingMessage.objects.get(notice_kind="confirmation").body


def test_duplicates_pending_and_intentional_resend(stock, booking):
    first_token, stale_token = token(stock, booking), token(stock, booking)
    notice = send(stock, booking, first_token)
    assert send(stock, booking, first_token).pk == notice.pk
    with pytest.raises(ValidationError, match="Another payment request"):
        send(stock, booking, stale_token)
    with pytest.raises(ValidationError, match="pending"):
        send(stock, booking)
    messaging.process_batch(stock.profile.pk)
    assert send(stock, booking).pk != notice.pk
    assert BookingMessage.objects.count() == 2


@pytest.mark.parametrize("change", ["canceled", "paid", "recipient", "partial", "disabled"])
def test_obsolete_email_not_sent(stock, booking, mailoutbox, change):
    notice = send(stock, booking)
    if change == "canceled":
        reservations.cancel_reservation(stock.user, stock.school.slug, booking.public_id)
    elif change == "paid":
        paid(stock, booking, 10000)
    elif change == "partial":
        paid(stock, booking, 500)
    elif change == "recipient":
        booking.party_request.organizer_email = "changed@example.invalid"
        booking.party_request.save()
    else:
        stock.profile.party_notifications_enabled = False
        stock.profile.save()
    messaging.process_batch(stock.profile.pk)
    notice.refresh_from_db()
    assert notice.state == "suppressed" and not mailoutbox


@pytest.mark.parametrize("state", ["canceled", "held", "review", "completed"])
def test_wrong_state_cannot_request(stock, booking, state):
    booking.state = state
    booking.save()
    with pytest.raises(ValidationError, match="reserved party"):
        send(stock, booking)
    assert not BookingMessage.objects.exists()


def test_payment_link_survives_day_but_regular_checkout_link_does_not(stock, booking, monkeypatch):
    link = checkout.payment_token(booking)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now + 2 * 86400)
    assert checkout.customer_reservation(stock.school.slug, link).pk == booking.pk
    booking.snapshot.pop("staff_managed")
    booking.save()
    with pytest.raises(Http404):
        checkout.customer_reservation(stock.school.slug, link)


def test_space_claim_stays_until_manual_cancellation(stock, booking):
    assert reservations.active_claims().filter(pk=booking.pk).exists()
    reservations.cancel_reservation(stock.user, stock.school.slug, booking.public_id)
    assert not reservations.active_claims().filter(pk=booking.pk).exists()
    booking.refresh_from_db()
    with pytest.raises(ValidationError):
        checkout.validate_booking_checkout(booking)


def test_permission_and_tampered_token_rejected(stock, booking, client):
    path = reverse("scheduling:reservation_detail", kwargs={"school_slug": stock.school.slug, "public_id": booking.public_id})
    client.force_login(stock.user)
    response = client.get(path)
    assert b"Request payment" not in response.content and b"24 hours" not in response.content
    assert client.post(path, {"action": "request_payment", "request_token": "bad"}).status_code == 400
    from scheduling.models import SchedulingMembership
    SchedulingMembership.objects.filter(user=stock.user).update(role="viewer")
    assert client.post(path, {"action": "request_payment", "request_token": token(stock, booking)}).status_code == 404
    assert not BookingMessage.objects.exists()


def test_http_send_and_retry(stock, booking, client):
    client.force_login(stock.user)
    path = reverse("scheduling:reservation_detail", kwargs={"school_slug": stock.school.slug, "public_id": booking.public_id})
    data = {"action": "request_payment", "request_token": token(stock, booking)}
    assert client.post(path, data).status_code == 302
    assert client.post(path, data).status_code == 302
    assert BookingMessage.objects.count() == 1


def test_reservation_uses_clean_guest_actions_and_honest_email_status(stock, booking, client):
    notice = send(stock, booking)
    notice.state = "draft"
    notice.save()
    client.force_login(stock.user)
    path = reverse("scheduling:reservation_detail", kwargs={"school_slug": stock.school.slug, "public_id": booking.public_id})
    response = client.get(path)
    assert response.status_code == 200
    for text in (b"Copy guest selection link", b"Email preview only; not sent", b"reservation-guest-actions"):
        assert text not in response.content
    for text in (b"Copy host link", b"View design guide", b"Not queued", b"The space stays reserved", b"Materials have not been reserved", b"Resend payment request"):
        assert text not in response.content
    notice.state = "sent"
    notice.save()
    response = client.get(path)
    assert b"Payment email sent" not in response.content
    assert b"Resend payment request" not in response.content


def test_live_send_uses_existing_delivery_gates(stock, booking, settings):
    settings.DEBUG = False
    with pytest.raises(ValidationError, match="not enabled"):
        send(stock, booking)
    assert not BookingMessage.objects.exists()


@pytest.mark.parametrize("invalid", ["expired", "user", "reservation"])
def test_payment_request_token_scope_and_expiry(stock, booking, monkeypatch, invalid):
    from django.core import signing
    payload = signing.loads(token(stock, booking), salt=requests.SALT)
    if invalid == "expired":
        now = time.time()
        with monkeypatch.context() as patch:
            patch.setattr(signing.time, "time", lambda: now - 7201)
            value = signing.dumps(payload, salt=requests.SALT)
    else:
        payload[invalid] = -1
        value = signing.dumps(payload, salt=requests.SALT)
    with pytest.raises(ValidationError, match="Refresh"):
        send(stock, booking, value)
    assert not BookingMessage.objects.exists()


def test_notifications_disabled_blocks_request(stock, booking):
    stock.profile.party_notifications_enabled = False
    stock.profile.save()
    with pytest.raises(ValidationError, match="Enable party emails"):
        send(stock, booking)
    assert not BookingMessage.objects.exists()


def test_disconnected_payment_account_suppresses_queued_request(stock, booking, mailoutbox):
    notice = send(stock, booking)
    PaymentAccount.objects.filter(pk=booking.order.account_id).update(enabled=False)
    assert messaging.process_batch(stock.profile.pk) == 1
    notice.refresh_from_db()
    assert notice.state == "suppressed"
    assert not mailoutbox


def test_email_preview_and_actions_are_location_scoped(stock, booking, client):
    from scheduling.models import SchedulingMembership
    notice = send(stock, booking)
    member = SchedulingMembership.objects.get(user=stock.user, profile=stock.profile)
    member.all_locations = False
    member.save()
    member.locations.set([booking.room.location])
    client.force_login(stock.user)
    path = reverse("scheduling:message_detail", kwargs={"school_slug": stock.school.slug, "public_id": notice.public_id})
    response = client.get(path)
    assert response.status_code == 200
    assert b"Back to reservation" in response.content
    assert b"<title>Payment request" in response.content
    member.locations.clear()
    assert client.get(path).status_code == 404
    assert client.post(path, {"confirm": "yes", "action": "suppress"}).status_code == 404
    with pytest.raises(Http404):
        messaging.change_message(stock.user, stock.school.slug, notice.public_id, "suppress")
    member.locations.set([booking.room.location])
    assert client.post(path, {"confirm": "yes", "action": "suppress"}).status_code == 302
    notice.refresh_from_db()
    assert notice.state == "suppressed"


@pytest.mark.django_db(transaction=True)
def test_concurrent_requests_queue_one_email(stock, booking):
    value = token(stock, booking)
    barrier = Barrier(2)
    def submit():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return send(stock, booking, value).pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(), range(2)))
    assert ids[0] == ids[1] and BookingMessage.objects.count() == 1


def test_expired_checkout_does_not_release_staff_reservation(stock, booking):
    attempt = checkout.prepare_booking_checkout(booking, "https://example.invalid/return")
    attempt.state = "expired"
    attempt.save()
    checkout._settle(booking, attempt, "open")
    booking.refresh_from_db()
    assert booking.state == "confirmed"
    assert reservations.active_claims().filter(pk=booking.pk).exists()


def test_failed_delivery_retries_without_duplicate_email(stock, booking, mailoutbox):
    notice = send(stock, booking)
    messaging.process_batch(stock.profile.pk, outcome="retry")
    notice.refresh_from_db()
    assert notice.state == "retry" and not mailoutbox
    notice.next_attempt_at = timezone.now()
    notice.save()
    messaging.process_batch(stock.profile.pk)
    assert len(mailoutbox) == 1


def test_live_transport_accepts_request_once(stock, booking, settings, monkeypatch):
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    booking.order.environment = "live"
    booking.order.save()
    # Provider readiness is independently covered by the mapped Square integration tests.
    monkeypatch.setattr(requests, "validate_account", lambda order: None)
    sent = []
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    monkeypatch.setattr(party_notifications, "get_school_email_connection", lambda *args: Transport())
    notice = send(stock, booking)
    assert notice.state == "queued"
    assert party_notifications.process(stock.profile.pk) == 1
    assert party_notifications.process(stock.profile.pk) == 0
    assert len(sent) == 1
    notice.refresh_from_db()
    assert notice.state == "sent"
