from datetime import timedelta

import pytest
from django.utils import timezone

from scheduling import messaging, party_notifications as notices
from scheduling.message_models import BookingMessage
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking

pytestmark = pytest.mark.django_db


def test_shared_job_disabled_does_not_capture(stock, booking, settings):
    from django.core.management import call_command
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = False
    call_command("process_party_notifications", all_schools=True)
    assert not BookingMessage.objects.exists()


@pytest.mark.parametrize("demo,environment,expected", [
    (True, "live", 0), (False, "test", 0), (False, "live", 1),
])
def test_shared_job_filters_and_is_idempotent(stock, booking, settings, monkeypatch,
                                             demo, environment, expected):
    from django.core.management import call_command
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    stock.school.is_demo = demo
    stock.school.save()
    booking.order.environment = environment
    booking.order.save()
    sent = []
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    monkeypatch.setattr(notices, "get_school_email_connection", lambda *args: Transport())
    call_command("process_party_notifications", all_schools=True)
    call_command("process_party_notifications", all_schools=True)
    assert len(sent) == expected
    assert BookingMessage.objects.count() == expected


def test_shared_job_continues_other_schools_after_failure(stock, settings, monkeypatch):
    from django.core.management import call_command, CommandError
    from core.models import School
    from scheduling.models import SchedulingProfile
    from scheduling.management.commands import process_party_notifications as command
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="second-school"),
        enabled=True, party_notifications_enabled=True, capabilities=["workshops", "parties"])
    visited = []
    def process(profile_id, limit):
        visited.append(profile_id)
        if profile_id == stock.profile.pk:
            raise RuntimeError("Synthetic processing failure")
        return 0
    monkeypatch.setattr(command, "process", process)
    with pytest.raises(CommandError, match="1 school"):
        call_command("process_party_notifications", all_schools=True)
    assert set(visited) == {stock.profile.pk, other.pk}


@pytest.fixture(autouse=True)
def enabled(stock, settings):
    stock.profile.party_notifications_enabled = True
    stock.profile.message_preview_enabled = True
    stock.profile.save()
    settings.DEBUG = True
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.EMAIL_BACKEND = messaging.LOCAL_BACKEND


def capture(booking):
    notices.capture_for_reservation(booking.pk)
    return BookingMessage.objects.filter(reservation=booking).first()


def test_confirmation_idempotent_and_locally_delivered(stock, booking, mailoutbox):
    message = capture(booking)
    capture(booking)
    assert BookingMessage.objects.count() == 1
    assert message.notice_kind == "confirmation"
    assert "/guests/" in message.body
    assert messaging.process_batch(stock.profile.pk) == 1
    assert len(mailoutbox) == 1
    message.refresh_from_db()
    assert message.state == "simulated"
    assert messaging.process_batch(stock.profile.pk) == 0


def test_reminder_once_at_deadline(stock, booking, mailoutbox):
    first = capture(booking)
    # Keep the schedule/signature unchanged and advance the worker clock.
    due = notices.guest_lists.due_at(booking)
    from unittest.mock import patch
    with patch("django.utils.timezone.now", return_value=due + timedelta(minutes=1)):
        capture(booking); capture(booking)
        assert BookingMessage.objects.filter(notice_kind="reminder").count() == 1
        assert messaging.process_batch(stock.profile.pk) == 2
    assert len(mailoutbox) == 2


def test_short_notice_only_confirmation(booking):
    booking.starts_at = timezone.now() + timedelta(days=1)
    booking.save()
    capture(booking); capture(booking)
    assert BookingMessage.objects.count() == 1
    assert "selections now" in BookingMessage.objects.get().body


@pytest.mark.parametrize("state", ["held", "canceled", "review", "completed"])
def test_unconfirmed_never_queued(booking, state):
    booking.state = state; booking.save()
    capture(booking)
    assert not BookingMessage.objects.exists()


def test_cancellation_suppresses_queued_message(stock, booking, mailoutbox):
    message = capture(booking)
    booking.state = "canceled"; booking.save()
    assert messaging.process_batch(stock.profile.pk) == 1
    assert not mailoutbox
    message.refresh_from_db(); assert message.state == "suppressed"


def test_reschedule_supersedes_old_confirmation(stock, booking):
    old = capture(booking)
    booking.starts_at += timedelta(days=1); booking.ends_at += timedelta(days=1); booking.save()
    new = capture(booking)
    old.refresh_from_db()
    assert old.state == "superseded"
    assert new.pk != old.pk
    assert not notices.deliverable(old)


def test_submitted_guests_suppress_reminder(stock, booking, mailoutbox):
    capture(booking)
    from unittest.mock import patch
    with patch("django.utils.timezone.now", return_value=notices.guest_lists.due_at(booking) + timedelta(minutes=1)):
        capture(booking)
        booking.party_request.materials = [{"quantity": booking.quantity}]
        booking.party_request.save()
        messaging.process_batch(stock.profile.pk)
    assert len(mailoutbox) == 1
    assert BookingMessage.objects.get(notice_kind="reminder").state == "suppressed"


def test_live_worker_never_sends_test_or_demo(stock, booking, settings, monkeypatch):
    message = capture(booking)
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    monkeypatch.setattr(notices, "get_school_email_connection", lambda *args: pytest.fail("External send attempted"))
    assert notices.process(stock.profile.pk) == 0
    booking.order.environment = "live"; booking.order.save()
    stock.school.is_demo = True; stock.school.save()
    assert notices.process(stock.profile.pk) == 0


def test_live_delivery_fenced_and_duplicate_safe(stock, booking, settings, monkeypatch):
    capture(booking)
    booking.order.environment = "live"; booking.order.save()
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    sent = []
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    monkeypatch.setattr(notices, "get_school_email_connection", lambda *args: Transport())
    assert notices.process(stock.profile.pk) == 1
    assert notices.process(stock.profile.pk) == 0
    assert len(sent) == 1
    assert BookingMessage.objects.get().state == "sent"


def test_unknown_delivery_does_not_retry(stock, booking, settings, monkeypatch):
    capture(booking)
    booking.order.environment = "live"; booking.order.save()
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    def failing(*args):
        raise TimeoutError
    monkeypatch.setattr(notices, "get_school_email_connection", failing)
    assert notices.process(stock.profile.pk) == 1
    assert notices.process(stock.profile.pk) == 0
    assert BookingMessage.objects.get().state == "uncertain"


def test_owner_message_screen_stays_in_settings_and_is_private(client, stock, booking, settings):
    from django.urls import reverse
    from scheduling.models import SchedulingMembership
    message = capture(booking)
    settings.BOOKINGS_MESSAGE_PREVIEW = False
    path = reverse("scheduling:message_detail", kwargs={"school_slug": stock.school.slug,
                                                       "public_id": message.public_id})
    client.force_login(stock.user)
    response = client.get(path)
    assert response.status_code == 200
    assert response.context["active_nav"] == "guest_communication"
    assert b"Queue notification" not in response.content
    assert b"Back to guest communication" in response.content
    SchedulingMembership.objects.filter(user=stock.user).update(role="scheduler")
    assert client.get(path).status_code == 404


def test_disabling_notifications_suppresses_pending(stock, booking, mailoutbox):
    message = capture(booking)
    stock.profile.party_notifications_enabled = False; stock.profile.save()
    assert messaging.process_batch(stock.profile.pk) == 1
    assert not mailoutbox
    message.refresh_from_db(); assert message.state == "suppressed"


@pytest.mark.django_db(transaction=True)
def test_concurrent_capture_creates_one_confirmation(stock, booking):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    barrier = Barrier(2)
    def run():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            notices.capture_for_reservation(booking.pk)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    assert BookingMessage.objects.filter(reservation=booking).count() == 1
