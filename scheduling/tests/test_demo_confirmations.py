from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from scheduling import party_notifications, reservation_email
from scheduling.message_models import BookingMessage
from scheduling.models import Reservation
from .test_demo_manual_email import demo
from .test_guest_lists import booking
from .test_inventory import stock, reserve, confirm

pytestmark = pytest.mark.django_db


@pytest.fixture
def delivery(monkeypatch):
    sent = []
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    monkeypatch.setattr(party_notifications, "get_school_email_connection", lambda school: Transport())
    return sent


@pytest.mark.parametrize("kind", ["workshop", "party"])
def test_new_demo_confirmation_to_actual_recipient_once(demo, booking, delivery, kind, settings):
    settings.DEMO_BASE_URL = "https://demo.example.invalid"
    item = booking if kind == "party" else reserve(demo)
    if kind == "workshop":
        confirm(demo, item)
    demo.profile.party_notifications_enabled = demo.profile.workshop_notifications_enabled = True
    demo.profile.save()
    for _ in range(2):
        call_command("process_party_notifications", all_schools=True)
    message = BookingMessage.objects.get(reservation=item, notice_kind="confirmation")
    assert message.state == "sent" and message.attempts == 1
    assert len([email for email in delivery if email.to == [message.recipient]]) == 1
    assert message.recipient == (item.party_request.organizer_email if kind == "party" else item.workshop_request.purchaser_email)
    assert "https://demo.example.invalid/bookings/" in message.body
    assert message.subject.startswith("[Demo] ")
    assert "not a real reservation" in message.body


@pytest.mark.parametrize("change", ["old", "off", "live", "canceled", "past"])
def test_demo_cannot_backfill_or_send_ineligible_confirmation(demo, booking, delivery, change):
    demo.profile.party_notifications_enabled = change != "off"
    demo.profile.save()
    if change == "old":
        Reservation.objects.filter(pk=booking.pk).update(created_at=demo.profile.demo_notifications_since - timedelta(seconds=1))
    elif change == "live":
        booking.order.environment = "live"; booking.order.save()
    elif change == "canceled":
        booking.state = "canceled"; booking.save()
    elif change == "past":
        Reservation.objects.filter(pk=booking.pk).update(starts_at=timezone.now()-timedelta(hours=2), ends_at=timezone.now()-timedelta(hours=1))
    call_command("process_party_notifications", all_schools=True)
    assert not delivery
    assert not BookingMessage.objects.filter(state="sent").exists()


def test_disable_after_queue_suppresses_without_transport(demo, booking, delivery):
    demo.profile.party_notifications_enabled = True; demo.profile.save()
    party_notifications.capture_for_reservation(booking.pk)
    message = BookingMessage.objects.get()
    assert message.state == "queued"
    demo.profile.party_notifications_enabled = False; demo.profile.save()
    party_notifications.process(demo.profile.pk)
    message.refresh_from_db()
    assert message.state == "suppressed" and not delivery


def test_old_queued_messages_and_reminders_are_not_replayed(demo, booking, delivery):
    demo.profile.party_notifications_enabled = True; demo.profile.save()
    Reservation.objects.filter(pk=booking.pk).update(created_at=demo.profile.demo_notifications_since - timedelta(days=1))
    for kind in ("confirmation", "reminder"):
        BookingMessage.objects.create(profile=demo.profile, reservation=booking, notice_kind=kind,
            event_key=kind, group_key=kind, recipient=booking.party_request.organizer_email, subject="Old", body="Old", state="queued")
    call_command("process_party_notifications", all_schools=True)
    assert not delivery and BookingMessage.objects.filter(state="queued").count() == 2


def test_production_is_never_enabled_by_demo_policy(demo, booking):
    demo.profile.party_notifications_enabled = True; demo.profile.save()
    demo.school.is_demo = False; demo.school.save()
    booking.refresh_from_db()
    assert not reservation_email.demo_confirmation_allowed(booking)


@pytest.mark.django_db(transaction=True)
def test_demo_notification_migration_keeps_old_records_and_switches():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0058_lesson_booking_enrollment_submission")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="migration-demo-email", is_demo=True)
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(
            school=school, enabled=True, party_notifications_enabled=False, workshop_notifications_enabled=True)
        before = timezone.now()
        MigrationExecutor(connection).migrate(latest)
        from scheduling.models import SchedulingProfile
        updated = SchedulingProfile.objects.get(pk=profile.pk)
        assert before <= updated.demo_notifications_since <= timezone.now()
        assert not updated.party_notifications_enabled and updated.workshop_notifications_enabled
        assert updated.created_at == profile.created_at < updated.demo_notifications_since
    finally:
        MigrationExecutor(connection).migrate(latest)
