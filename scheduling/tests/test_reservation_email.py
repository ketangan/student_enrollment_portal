import uuid
from unittest.mock import patch

import pytest
from django.core import signing
from django.urls import reverse
from django.utils import timezone

from core.models import School, SchoolEmailTemplate
from core.services.email_content import clean_html, validate_body, render_tokens
from django.core.exceptions import ValidationError
from scheduling import reservation_email as email, messaging, party_notifications
from scheduling.message_models import BookingMessage, BookingMessageAttempt
from scheduling.models import SchedulingMembership
from scheduling.reservation_audit import audit_page
from scheduling.tests.test_inventory import stock, reserve
from scheduling.tests.test_guest_lists import booking

pytestmark = pytest.mark.django_db


def url(stock, booking):
    return reverse("scheduling:reservation_detail", kwargs={"school_slug": stock.school.slug, "public_id": booking.public_id})


def data(stock, booking, **changes):
    return {"action": "send_email", "recipient": "synthetic@example.invalid", "subject": "Your visit",
        "body": "<p>Hello <b>there</b></p>",
        "email_token": email.context_for(stock.user, booking)["email_form"].initial["email_token"], **changes}


def template(stock, **changes):
    return SchoolEmailTemplate.objects.create(school=stock.school, purpose="bookings", name="Visit details",
        subject="Hello {{first_name}}", body="<p>{{full_name}}</p>", **changes)


@pytest.fixture(autouse=True)
def local(settings):
    settings.DEBUG = True


def test_composer_scope_and_safe_personalization(stock, booking):
    item = template(stock)
    template(stock, booking_kind="workshop")
    template(stock, is_active=False)
    SchoolEmailTemplate.objects.create(school=stock.school, name="Enrollment secret", subject="Secret", body="Secret")
    other = School.objects.create(slug="email-other")
    SchoolEmailTemplate.objects.create(school=other, purpose="bookings", name="Foreign secret", subject="Secret", body="Secret")
    booking.party_request.organizer_name = '<img src=x onerror=alert(1)> Host'
    booking.party_request.save()
    context = email.context_for(stock.user, booking)
    assert set(context["email_options"]) == {"", str(item.pk)}
    assert "<img" not in context["email_options"][str(item.pk)]["body"]
    assert "&lt;img" in context["email_options"][str(item.pk)]["body"]
    response = stock.client.get(url(stock, booking))
    assert response.status_code == 200
    assert b"Send email" in response.content
    assert b'data-email-open' in response.content
    assert b'<dialog id="reservation-email-dialog"' in response.content
    assert b'data-email-shortcut' not in response.content
    assert b' value="preview_email"' not in response.content
    assert b"Write message" not in response.content
    assert b"Enrollment secret" not in response.content
    assert b"Foreign secret" not in response.content


def test_local_send_is_capture_only_and_idempotent(stock, booking, mailoutbox):
    payload = data(stock, booking, body='<p>Hello</p><script>bad()</script><a href="javascript:evil()">Bad</a>')
    path = url(stock, booking)
    for _ in range(2):
        assert stock.client.post(path, payload).status_code == 302
    message = BookingMessage.objects.get(notice_kind="manual")
    assert message.state == "draft" and message.is_html
    assert message.created_by == stock.user and message.reservation == booking
    assert "script" not in message.body and "javascript" not in message.body
    assert not mailoutbox
    row = next(row for row in audit_page(booking, None)["rows"] if row["key"] == f"email-{message.pk}")
    assert row["actor"] == stock.user.username and row["title"] == "Email not sent"
    assert row["email_body"] == "<p>Hello</p>Bad"
    page = stock.client.get(path)
    assert not list(page.context["messages"])
    assert b"Message saved for local preview" not in page.content
    response = stock.client.post(path, {**payload, "subject": "Changed replay"})
    assert response.status_code == 400
    assert b"different contents" in response.content
    assert BookingMessage.objects.count() == 1


@pytest.mark.parametrize("changes", [{"recipient": "bad"}, {"subject": "bad\r\nBcc: hidden@example.invalid"},
    {"subject": "x" * 201}, {"body": "<p> </p>"}, {"body": "x" * 20001}, {"body": "Hello {{unknown}}"},
    {"email_token": "bad"}])
def test_invalid_message_never_captured(stock, booking, changes):
    response = stock.client.post(url(stock, booking), data(stock, booking, **changes))
    assert response.status_code == 400
    assert BookingMessage.objects.count() == 0


def test_token_bound_to_user_and_reservation(stock, booking):
    token = signing.dumps({"user": stock.user.pk + 1, "reservation": booking.pk, "key": str(uuid.uuid4())}, salt=email.SALT)
    assert stock.client.post(url(stock, booking), data(stock, booking, email_token=token)).status_code == 400
    assert not BookingMessage.objects.exists()


def test_template_id_rechecked_and_removed_preview_rejected(stock, booking, mailoutbox):
    item = template(stock)
    payload = data(stock, booking, action="preview_email", template=item.pk)
    response = stock.client.post(url(stock, booking), payload)
    assert response.status_code == 400
    assert not BookingMessage.objects.exists() and not mailoutbox
    payload["action"] = "send_email"
    item.is_active = False
    item.save()
    assert stock.client.post(url(stock, booking), {**payload, "action": "send_email"}).status_code == 400
    item.school = School.objects.create(slug="moved-email-school")
    item.is_active = True
    item.save()
    assert stock.client.post(url(stock, booking), payload).status_code == 400


@pytest.mark.parametrize("role,all_locations", [("viewer", True), ("owner", False)])
def test_unauthorized_staff_cannot_compose_or_send(stock, booking, role, all_locations):
    payload = data(stock, booking)
    SchedulingMembership.objects.filter(user=stock.user).update(role=role, all_locations=all_locations)
    response = stock.client.get(url(stock, booking))
    assert response.status_code == (200 if all_locations else 404)
    assert b'id="reservation-email-dialog"' not in response.content
    assert b'data-email-open' not in response.content
    assert stock.client.post(url(stock, booking), payload).status_code == 404
    assert not BookingMessage.objects.exists()


@pytest.mark.parametrize("state", ["confirmed", "canceled", "completed"])
def test_live_delivery_uses_shared_transport_and_audits_actual_acceptance(stock, booking, settings, monkeypatch, state):
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    stock.profile.party_notifications_enabled = True
    stock.profile.save()
    booking.order.environment = "live"
    booking.order.save()
    booking.state = state
    booking.save()
    sent = []
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    connection = []
    def factory(school):
        connection.append(school.pk)
        return Transport()
    monkeypatch.setattr(party_notifications, "get_school_email_connection", factory)
    payload = data(stock, booking)
    assert stock.client.post(url(stock, booking), payload).status_code == 302
    message = BookingMessage.objects.get()
    assert message.state == "queued"
    assert party_notifications.process(stock.profile.pk) == 1
    assert party_notifications.process(stock.profile.pk) == 0
    assert len(sent) == 1 and connection == [stock.school.pk]
    assert sent[0].content_subtype == "html"
    assert sent[0].to == [payload["recipient"]]
    assert sent[0].body == payload["body"]
    assert stock.client.post(url(stock, booking), payload).status_code == 302
    message.refresh_from_db()
    assert message.state == "sent"
    rows = audit_page(booking, 1)["rows"]
    assert any("Email attempt 1 - Accepted by email provider" == row["title"] for row in rows)
    assert not any("Delivered" in row["title"] for row in rows)


@pytest.mark.parametrize("failure,state", [(0, "retry"), (TimeoutError, "uncertain")])
def test_failed_or_uncertain_sends_not_reported_as_sent(stock, booking, settings, monkeypatch, failure, state):
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    stock.profile.party_notifications_enabled = True
    stock.profile.save()
    booking.order.environment = "live"
    booking.order.save()
    class Transport:
        def send_messages(self, messages):
            if failure:
                raise failure
            return 0
    monkeypatch.setattr(party_notifications, "get_school_email_connection", lambda school: Transport())
    assert stock.client.post(url(stock, booking), data(stock, booking)).status_code == 302
    assert party_notifications.process(stock.profile.pk) == 1
    assert party_notifications.process(stock.profile.pk) == 0
    assert BookingMessage.objects.get().state == state
    assert not any("Accepted by email provider" in row["title"] for row in audit_page(booking, None)["rows"])


def test_live_disabled_and_demo_never_queue(stock, booking, settings):
    settings.DEBUG = False
    booking.order.environment = "live"
    booking.order.save()
    assert stock.client.post(url(stock, booking), data(stock, booking)).status_code == 400
    stock.school.is_demo = True
    stock.school.save()
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    stock.profile.party_notifications_enabled = True
    stock.profile.save()
    form, message = email.send(stock.user, stock.school.slug, booking.public_id, data(stock, booking))
    assert form.errors and not message and not BookingMessage.objects.exists()


def test_revoke_permission_or_notification_setting_before_delivery(stock, booking):
    stock.profile.party_notifications_enabled = True
    stock.profile.save()
    form, message = email.send(stock.user, stock.school.slug, booking.public_id, data(stock, booking))
    assert not form.errors
    assert party_notifications.deliverable(message)
    SchedulingMembership.objects.filter(user=stock.user).update(is_active=False)
    assert not party_notifications.deliverable(message)
    SchedulingMembership.objects.filter(user=stock.user).update(is_active=True)
    stock.profile.party_notifications_enabled = False
    stock.profile.save()
    assert not party_notifications.deliverable(message)


def test_workshop_composer_confirmation_and_simulator(stock, settings, mailoutbox):
    item = reserve(stock)
    item.state = "confirmed"
    item.save()
    stock.profile.workshop_notifications_enabled = True
    stock.profile.message_preview_enabled = True
    stock.profile.save()
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.EMAIL_BACKEND = messaging.LOCAL_BACKEND
    saved = template(stock, booking_kind="workshop")
    context = email.context_for(stock.user, item)
    assert set(context["email_options"]) == {"", str(saved.pk)}
    assert "Parent" in context["email_options"][str(saved.pk)]["body"]
    assert stock.client.post(url(stock, item), data(stock, item)).status_code == 302
    message = BookingMessage.objects.get(notice_kind="manual")
    message.state = "queued"
    message.save()
    assert messaging.process_batch(stock.profile.pk) == 1
    assert mailoutbox[0].content_subtype == "html"
    assert "Simulated, not sent" in str(audit_page(item, None)["rows"])


def test_sanitizer_and_template_substitution():
    raw = '<svg onload="bad()"></svg><script>bad()</script><img src="https://tracker.invalid"><p style="color:red" onclick="bad()">Hi <b>there</b><a href="javascript:bad()">link</a></p>'
    cleaned = clean_html(raw)
    assert "bad()" not in cleaned and "tracker" not in cleaned and "onclick" not in cleaned and "style" not in cleaned
    assert "<b>there</b>" in cleaned
    assert render_tokens("{{full_name}} / {{unknown}}", {"full_name": "<b>"}, html=True) == "&lt;b&gt; / {{unknown}}"
    with pytest.raises(ValidationError):
        validate_body("x" * 20001)
    with pytest.raises(ValidationError):
        validate_body("<script>bad()</script>")


def template_url(stock, name="email_template_new", **kwargs):
    return reverse("scheduling:" + name, kwargs={"school_slug": stock.school.slug, **kwargs})


def test_create_edit_deactivate_and_isolate_templates(stock):
    path = template_url(stock)
    response = stock.client.get(path)
    assert response.status_code == 200 and b'>Preview</button>' not in response.content
    payload = {"name": "Party update", "booking_kind": "party", "subject": "Hi {{first_name}}",
        "body": '<p>Hello {{full_name}}</p><script>bad()</script>', "is_active": "on"}
    preview = stock.client.post(path, {**payload, "action": "preview"})
    assert preview.status_code == 400
    assert not SchoolEmailTemplate.objects.exists()
    assert stock.client.post(path, payload).status_code == 302
    item = SchoolEmailTemplate.objects.get()
    assert item.school == stock.school and item.purpose == "bookings" and "script" not in item.body
    edit = template_url(stock, "email_template_edit", pk=item.pk)
    assert stock.client.get(edit).status_code == 200
    assert stock.client.post(edit, {**payload, "action": "preview", "subject": "Must not save"}).status_code == 400
    item.refresh_from_db()
    assert item.subject == payload["subject"]
    assert stock.client.post(edit, {**payload, "is_active": ""}).status_code == 302
    item.refresh_from_db()
    assert not item.is_active
    item.purpose = "enrollment"
    item.save()
    assert stock.client.get(edit).status_code == 404
    assert stock.client.post(edit, payload).status_code == 404
    item.purpose = "bookings"
    item.school = School.objects.create(slug="foreign-template")
    item.save()
    assert stock.client.get(edit).status_code == 404
    assert stock.client.post(edit, payload).status_code == 404


@pytest.mark.parametrize("changes", [{"body": "<p> </p>"}, {"body": "{{unknown}}"}, {"subject": "{{unknown}}"},
    {"subject": "x" * 201}, {"booking_kind": "invalid"}, {"name": ""}])
def test_invalid_templates(stock, changes):
    response = stock.client.post(template_url(stock), {"name": "Test", "booking_kind": "both",
        "subject": "Hello", "body": "<p>Hi</p>", **changes})
    assert response.status_code == 400 and not SchoolEmailTemplate.objects.exists()


def test_enrollment_routes_cannot_edit_booking_templates(client):
    from core.tests.factories import SchoolFactory, SchoolAdminMembershipFactory
    school = SchoolFactory()
    membership = SchoolAdminMembershipFactory(school=school, role="owner")
    client.force_login(membership.user)
    item = SchoolEmailTemplate.objects.create(school=school, purpose="bookings", name="Booking-only secret",
        subject="Booking subject", body="Booking body")
    enrollment = SchoolEmailTemplate.objects.create(school=school, name="Enrollment-only template", subject="Enrollment", body="Hello")
    settings_page = client.get(reverse("school_settings", kwargs={"school_slug": school.slug}))
    assert settings_page.status_code == 200
    assert b"Booking-only secret" not in settings_page.content
    assert b"Enrollment-only template" in settings_page.content
    for name in ("school_email_template_edit", "school_email_template_delete", "school_email_template_reactivate"):
        path = reverse(name, kwargs={"school_slug": school.slug, "template_id": item.pk})
        assert client.post(path, {"name": "bad", "subject": "bad", "body": "bad"}).status_code == 404
    assert client.get(reverse("school_email_template_edit", kwargs={"school_slug": school.slug, "template_id": enrollment.pk})).status_code == 200
    item.refresh_from_db()
    assert item.name == "Booking-only secret"


def test_audit_merge_uses_delivery_time_and_has_bounded_queries(stock, booking, django_assert_max_num_queries):
    _, message = email.send(stock.user, stock.school.slug, booking.public_id, data(stock, booking))
    BookingMessageAttempt.objects.create(message=message, token=uuid.uuid4(), number=1, outcome="sent", finished_at=timezone.now())
    with django_assert_max_num_queries(12):
        rows = audit_page(booking, 1)["rows"]
    assert rows[0]["title"] == "Email attempt 1 - Accepted by email provider"


def test_payment_shortcut_and_template_permissions(stock, booking, settings):
    from core.payments.models import PaymentAccount
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_local_tests"
    account = PaymentAccount.objects.create(school=stock.school, reference="acct_local_test", enabled=True)
    booking.order.account = account
    booking.order.merchant_reference = account.reference
    booking.order.save()
    saved = template(stock, booking_link="payment")
    context = email.context_for(stock.user, booking)
    link = signing.loads(context["email_options"][str(saved.pk)]["link_token"], salt=email.LINK_SALT)
    assert "/pay/" in link["url"]
    SchedulingMembership.objects.filter(user=stock.user).update(role="scheduler")
    assert stock.client.get(template_url(stock)).status_code == 404
    assert stock.client.post(template_url(stock), {}).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_send_creates_only_one_message(stock, booking):
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections
    payload = data(stock, booking)
    def submit():
        close_old_connections()
        try:
            form, message = email.send(stock.user, stock.school.slug, booking.public_id, payload)
            assert not form.errors
            return message.pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(), range(2)))
    assert len(set(ids)) == 1
    assert BookingMessage.objects.filter(reservation=booking).count() == 1
