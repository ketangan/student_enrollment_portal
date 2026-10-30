import pytest
from django.core.management import call_command

from scheduling import reservation_email as email, party_notifications, messaging
from scheduling.message_models import BookingMessage
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking
from scheduling.tests.test_reservation_email import data

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo(stock, settings):
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = False
    settings.BOOKING_HOSTED_DEMO_SLUGS = [stock.school.slug]
    settings.BOOKING_HOSTED_DEMO_HOSTS = ["demo.example.invalid"]
    settings.BOOKINGS_DEMO_EMAIL_RECIPIENTS = {stock.school.slug: ["tester@example.invalid"]}
    stock.school.is_demo = True
    stock.school.feature_flags = {"booking_experience_demo": True}
    stock.school.save()
    stock.profile.party_notifications_enabled = True
    stock.profile.save()
    return stock


def test_demo_manual_delivery_is_allowlisted_and_uses_existing_school_transport(demo, booking, monkeypatch):
    sent = []
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    monkeypatch.setattr(party_notifications, "get_school_email_connection", lambda school: Transport())
    payload = data(demo, booking, recipient="tester@example.invalid")
    form, message = email.send(demo.user, demo.school.slug, booking.public_id, payload)
    assert not form.errors and message.state == "queued"
    for _ in range(2):
        call_command("process_party_notifications", all_schools=True)
    message.refresh_from_db()
    assert message.state == "sent" and len(sent) == 1
    assert sent[0].to == ["tester@example.invalid"]
    assert BookingMessage.objects.count() == 1  # No automatic demo confirmations captured.
    form, duplicate = email.send(demo.user, demo.school.slug, booking.public_id, payload)
    assert not form.errors and duplicate.pk == message.pk and duplicate.state == "sent"


@pytest.mark.parametrize("change", ["recipient", "school", "host", "debug", "active", "flag", "order", "notification"])
def test_demo_delivery_fails_closed(demo, booking, settings, change):
    recipient = "tester@example.invalid"
    if change == "recipient":
        recipient = "customer@example.invalid"
    elif change == "school":
        settings.BOOKINGS_DEMO_EMAIL_RECIPIENTS = {"another-school": [recipient]}
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
    else:
        demo.profile.party_notifications_enabled = False
        demo.profile.save()
    form, message = email.send(demo.user, demo.school.slug, booking.public_id, data(demo, booking, recipient=recipient))
    if change == "debug":
        assert message.state == "draft"  # Local capture, never external delivery.
    else:
        assert form.errors and message is None
    assert messaging.claim(demo.profile.pk, party_delivery=True) is None


@pytest.mark.parametrize("config", [[], {"bad": []}, {"inventory-demo": "bad"},
    {"inventory-demo": [7]}, {"inventory-demo": ["invalid"]}, {"inventory-demo": ["a@example.invalid"] * 21}])
def test_invalid_demo_allowlists_do_not_enable_delivery(demo, config, settings):
    settings.BOOKINGS_DEMO_EMAIL_RECIPIENTS = config
    assert not email.demo_recipients(demo.profile)


def test_revoked_recipient_and_automatic_demo_messages_never_send(demo, booking, settings, monkeypatch):
    _, message = email.send(demo.user, demo.school.slug, booking.public_id,
        data(demo, booking, recipient="tester@example.invalid"))
    automatic = BookingMessage.objects.create(profile=demo.profile, reservation=booking, notice_kind="confirmation",
        event_key="auto", group_key="auto", recipient="tester@example.invalid", subject="Auto", body="Auto", state="queued")
    settings.BOOKINGS_DEMO_EMAIL_RECIPIENTS = {demo.school.slug: ["another@example.invalid"]}
    def forbidden(*args):
        pytest.fail("Revoked recipient must not reach SMTP")
    monkeypatch.setattr(party_notifications, "get_school_email_connection", forbidden)
    assert party_notifications.process(demo.profile.pk) == 1
    message.refresh_from_db()
    automatic.refresh_from_db()
    assert message.state == "suppressed" and automatic.state == "queued"
