import pytest
from django.core.management import call_command

from scheduling import reservation_email as email, party_notifications, messaging
from scheduling.message_models import BookingMessage
from scheduling.models import SchedulingMembership
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking
from scheduling.tests.test_reservation_email import data, reserve

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo(stock, settings):
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = False
    settings.BOOKING_HOSTED_DEMO_SLUGS = [stock.school.slug]
    settings.BOOKING_HOSTED_DEMO_HOSTS = ["demo.example.invalid"]
    stock.school.is_demo = True
    stock.school.feature_flags = {"booking_experience_demo": True}
    stock.school.save()
    stock.profile.party_notifications_enabled = False
    stock.profile.workshop_notifications_enabled = False
    stock.profile.save()
    return stock


@pytest.mark.parametrize("recipient", ["tester@example.invalid", "another@example.invalid"])
@pytest.mark.parametrize("custom_smtp", [False, True])
def test_demo_manual_delivery_uses_existing_school_transport_without_recipient_list(demo, booking, monkeypatch, recipient, custom_smtp):
    sent = []
    demo.school.smtp_host = "smtp.example.invalid" if custom_smtp else ""
    demo.school.save()
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    def connection(school):
        assert school.pk == demo.school.pk and school.smtp_host == demo.school.smtp_host
        return Transport()
    monkeypatch.setattr(party_notifications, "get_school_email_connection", connection)
    payload = data(demo, booking, recipient=recipient)
    form, message = email.send(demo.user, demo.school.slug, booking.public_id, payload)
    assert not form.errors and message.state == "queued"
    for _ in range(2):
        call_command("process_party_notifications", all_schools=True)
    message.refresh_from_db()
    assert message.state == "sent" and len(sent) == 1
    assert sent[0].to == [recipient]
    assert BookingMessage.objects.count() == 1  # No automatic demo confirmations captured.
    form, duplicate = email.send(demo.user, demo.school.slug, booking.public_id, payload)
    assert not form.errors and duplicate.pk == message.pk and duplicate.state == "sent"


@pytest.mark.parametrize("change", ["school", "host", "debug", "active", "flag", "order", "capability", "demo"])
def test_demo_delivery_fails_closed(demo, booking, settings, change):
    recipient = "tester@example.invalid"
    if change == "school":
        settings.BOOKING_HOSTED_DEMO_SLUGS = ["another-school"]
    elif change == "host":
        settings.BOOKING_HOSTED_DEMO_HOSTS = []
    elif change == "debug":
        settings.DEBUG = True
    elif change == "active":
        demo.school.is_active = False
        demo.school.save()
    elif change == "flag":
        demo.school.feature_flags = {}
        demo.school.save()
    elif change == "order":
        booking.order.environment = "live"
        booking.order.save()
    elif change == "capability":
        demo.profile.capabilities.remove("parties")
        demo.profile.save()
    else:
        demo.school.is_demo = False
        demo.school.save()
    form, message = email.send(demo.user, demo.school.slug, booking.public_id, data(demo, booking, recipient=recipient))
    if change == "debug":
        assert message.state == "draft"  # Local capture, never external delivery.
    else:
        assert form.errors and message is None
    assert messaging.claim(demo.profile.pk, party_delivery=True) is None


@pytest.mark.parametrize("live_enabled", [False, True])
def test_revoked_staff_and_automatic_demo_messages_never_send(demo, booking, settings, monkeypatch, live_enabled):
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = live_enabled
    demo.profile.party_notifications_enabled = True
    demo.profile.save()
    _, message = email.send(demo.user, demo.school.slug, booking.public_id,
        data(demo, booking, recipient="tester@example.invalid"))
    automatic = BookingMessage.objects.create(profile=demo.profile, reservation=booking, notice_kind="confirmation",
        event_key="auto", group_key="auto", recipient="tester@example.invalid", subject="Auto", body="Auto", state="queued")
    SchedulingMembership.objects.filter(user=demo.user).update(is_active=False)
    def forbidden(*args):
        pytest.fail("Revoked staff access must not reach SMTP")
    monkeypatch.setattr(party_notifications, "get_school_email_connection", forbidden)
    assert party_notifications.process(demo.profile.pk) == 1
    message.refresh_from_db()
    automatic.refresh_from_db()
    assert message.state == "suppressed" and automatic.state == "queued"


def test_workshop_manual_demo_email_does_not_require_automatic_confirmations(demo, monkeypatch):
    item = reserve(demo)
    sent = []
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    monkeypatch.setattr(party_notifications, "get_school_email_connection", lambda school: Transport())
    form, message = email.send(demo.user, demo.school.slug, item.public_id,
        data(demo, item, recipient="workshop@example.invalid"))
    assert not form.errors and message.state == "queued"
    call_command("process_party_notifications", all_schools=True)
    message.refresh_from_db()
    assert message.state == "sent" and sent[0].to == ["workshop@example.invalid"]
    assert BookingMessage.objects.count() == 1
