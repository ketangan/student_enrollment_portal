import uuid
from datetime import timedelta

import pytest
from django.core import signing
from django.core.management import call_command
from django.utils import timezone

from core.models import SchoolEmailTemplate
from core.payments.models import LedgerEntry, PaymentAccount
from core.services.email_content import clean_html, transport_html
from scheduling import reservation_email as email, guest_lists, workshop_followup, party_notifications
from scheduling.message_models import BookingMessage
from scheduling.tests.test_inventory import stock, reserve
from scheduling.tests.test_guest_lists import booking
from scheduling.tests.test_reservation_email import data, url, template_url
from scheduling.tests.test_package_only_booking import policy
from scheduling.reservation_audit import audit_page

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def local(settings, stock):
    settings.DEBUG = True
    call_command("seed_booking_email_templates", school=stock.school.slug)


@pytest.fixture
def payable(stock, booking, settings):
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_local_tests"
    account = PaymentAccount.objects.create(school=stock.school, reference="acct_email_link_test", enabled=True)
    booking.order.account = account
    booking.order.merchant_reference = account.reference
    booking.order.deposit_cents = 2500
    booking.order.save()
    stock.profile.party_notifications_enabled = True
    stock.profile.save()
    return booking


def draft(stock, item, template):
    template = template_key(stock, template)
    option = email.context_for(stock.user, item)["email_options"][template]
    return data(stock, item, template=template, subject=option["subject"], body=option["body"], link_token=option["link_token"])


def template_key(stock, key):
    names = {"payment": "Payment reminder", "guests": "Guest details", "confirmation": "Confirmation"}
    return str(SchoolEmailTemplate.objects.get(school=stock.school, purpose="bookings", name=names[key]).pk) if key in names else key


def paid(stock, item, amount):
    return LedgerEntry.objects.create(order=item.order, school=stock.school, environment="test",
        actor=stock.user, kind="payment", amount_cents=amount, key=uuid.uuid4(), reference=str(uuid.uuid4()))


def test_payment_link_uses_deposit_then_balance_and_is_idempotent(stock, payable, mailoutbox):
    payload = draft(stock, payable, "payment")
    info = signing.loads(payload["link_token"], salt=email.LINK_SALT)
    assert info["label"] == "Pay $25.00"
    assert "$25.00" in info["hint"]
    for _ in range(2):
        assert stock.client.post(url(stock, payable), payload).status_code == 302
    message = BookingMessage.objects.get()
    assert message.notice_kind == "manual_payment" and message.state == "draft"
    assert message.body.count('class="booking-email-action"') == 1
    assert info["url"] in message.body and "Pay $25.00" in message.body
    assert email.deliverable(message, payable)
    paid(stock, payable, 2500)
    assert not email.deliverable(message, payable)
    updated = email.context_for(stock.user, payable)["email_options"][template_key(stock, "payment")]
    assert "$75.00" in updated["link_hint"]
    paid(stock, payable, 7500)
    assert email.context_for(stock.user, payable)["email_options"][template_key(stock, "payment")]["unavailable"]
    assert not mailoutbox


def test_balance_email_opens_payable_party_page_not_confirmation(stock, payable, policy, monkeypatch):
    from django.urls import reverse
    from core.payments import checkout
    from scheduling.checkout import payment_token, settle_booking_event
    from scheduling.tests.test_checkout import event_for

    paid(stock, payable, 2500)
    payload = draft(stock, payable, "payment")
    assert stock.client.post(url(stock, payable), payload).status_code == 302
    message = BookingMessage.objects.get(notice_kind="manual_payment")
    info = signing.loads(payload["link_token"], salt=email.LINK_SALT)
    assert info["url"] in message.body and "?action=payment" in info["url"]
    response = stock.client.get(info["url"])
    assert response.status_code == 200 and response.context["ready"]
    assert b"Your party payment" in response.content and b"Continue to payment" in response.content
    assert response.context["due"] == 75
    confirm_url = reverse("scheduling:customer_payment", args=[stock.school.slug, payment_token(payable)])
    assert b"Continue to payment" not in stock.client.get(confirm_url).content

    # A checkout failure must preserve the payment action and allow a retry.
    failed = stock.client.post(response.context["payment_form_url"], {"payment_option": "invalid"})
    assert failed.status_code == 400 and b"Continue to payment" in failed.content
    monkeypatch.setattr(checkout, "open_checkout", lambda attempt: "/synthetic-checkout/")
    for _ in range(2):
        started = stock.client.post(response.context["payment_form_url"], {"payment_option": "due"})
        assert started.status_code == 302 and started.url == "/synthetic-checkout/"
    attempt = payable.order.attempts.get()
    assert attempt.amount_cents == 7500
    assert "action=payment" in attempt.cancel_url and "action=payment" not in attempt.success_url
    assert b"Continue to payment" in stock.client.get(attempt.cancel_url).content
    attempt.provider_session_id = "cs_test_" + attempt.public_id.hex
    attempt.save()
    settle_booking_event(event_for(attempt))
    for path in (attempt.success_url, info["url"]):
        settled = stock.client.get(path)
        assert settled.status_code == 200 and b"Continue to payment" not in settled.content
        assert settled.context["amounts"]["outstanding_cents"] == 0
    assert stock.client.get(info["url"].replace(stock.school.slug, "other-school")).status_code == 404


def test_email_audit_is_read_only_but_transport_keeps_action_button(stock, payable):
    payload = draft(stock, payable, "payment")
    assert stock.client.post(url(stock, payable), payload).status_code == 302
    message = BookingMessage.objects.get()
    row = next(row for row in audit_page(payable, None)["rows"] if row["key"] == f"email-{message.pk}")
    assert "<a" not in row["email_body"] and "booking-email-action" not in row["email_body"]
    assert "Pay $25.00" in row["email_body"] and "Please complete" in row["email_body"]
    assert 'style="display:inline-block;' in transport_html(message.body)


def test_workshop_email_link_opens_remaining_payment_and_hides_when_paid(stock, settings):
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_local_tests"
    item = reserve(stock, total=10000)
    item.state = "confirmed"
    item.save()
    account = PaymentAccount.objects.create(school=stock.school, reference="acct_workshop_email_test", enabled=True)
    item.order.account = account
    item.order.merchant_reference = account.reference
    item.order.save()
    paid(stock, item, 2500)
    link = email.customer_link(item, "payment")
    response = stock.client.get(link["url"])
    assert response.status_code == 200 and response.context["ready"]
    assert b"Pay remaining balance: $75.00" in response.content
    paid(stock, item, 7500)
    response = stock.client.get(link["url"])
    assert response.status_code == 200 and not response.context["ready"]
    assert b"Paid in full" in response.content and b"Pay remaining balance" not in response.content


def test_changed_balance_keeps_draft_open_and_requires_review(stock, payable):
    payload = draft(stock, payable, "payment")
    paid(stock, payable, 1000)
    response = stock.client.post(url(stock, payable), payload)
    assert response.status_code == 400
    assert b'data-email-error' in response.content
    assert b'payment amount changed' in response.content
    assert b'$15.00' in response.content
    assert payload["subject"].encode() in response.content
    assert not BookingMessage.objects.exists()
    refreshed = email.context_for(stock.user, payable)["email_options"][template_key(stock, "payment")]
    assert stock.client.post(url(stock, payable), {**payload, "link_token": refreshed["link_token"]}).status_code == 302


@pytest.mark.parametrize("change", ["tampered", "foreign_user", "foreign_reservation", "wrong_kind", "missing"])
def test_customer_link_binding_is_enforced(stock, payable, change):
    payload = draft(stock, payable, "payment")
    link = signing.loads(payload["link_token"], salt=email.LINK_SALT)
    if change == "tampered":
        payload["link_token"] += "bad"
    elif change == "missing":
        payload["link_token"] = ""
    else:
        field = {"foreign_user": "user", "foreign_reservation": "reservation", "wrong_kind": "kind"}[change]
        link[field] = "guests" if field == "kind" else link[field] + 1
        payload["link_token"] = signing.dumps(link, salt=email.LINK_SALT)
    assert stock.client.post(url(stock, payable), payload).status_code == 400
    assert not BookingMessage.objects.exists()


def test_guest_link_resolves_to_this_party_and_closed_guest_access_is_not_offered(stock, booking):
    stock.profile.party_notifications_enabled = True
    stock.profile.save()
    payload = draft(stock, booking, "guests")
    info = signing.loads(payload["link_token"], salt=email.LINK_SALT)
    token = info["url"].rstrip("/").rsplit("/", 1)[-1]
    assert guest_lists.resolve(stock.school.slug, token).pk == booking.pk
    assert stock.client.post(url(stock, booking), payload).status_code == 302
    message = BookingMessage.objects.get()
    assert "Add guest details" in message.body and "/guests/" in message.body
    assert party_notifications.deliverable(message)
    booking.prepared_at = timezone.now()
    booking.save()
    assert not party_notifications.deliverable(message)
    options = email.context_for(stock.user, booking)["email_options"]
    assert options[template_key(stock, "guests")]["unavailable"]
    assert not options[template_key(stock, "confirmation")]["link_token"]


def test_workshop_selection_link_uses_workshop_customer_page(stock):
    item = reserve(stock)
    item.state = "confirmed"
    item.save()
    payload = draft(stock, item, "guests")
    info = signing.loads(payload["link_token"], salt=email.LINK_SALT)
    token = info["url"].rstrip("/").rsplit("/", 1)[-1]
    assert workshop_followup.resolve(stock.school.slug, token).pk == item.pk
    assert stock.client.post(url(stock, item), payload).status_code == 302
    assert "Update participant selections" in BookingMessage.objects.get().body


def test_confirmation_does_not_silently_drop_a_closed_guest_link(stock, booking):
    SchoolEmailTemplate.objects.filter(pk=template_key(stock, "confirmation")).update(booking_link="guests")
    payload = draft(stock, booking, "confirmation")
    booking.prepared_at = timezone.now()
    booking.save()
    response = stock.client.post(url(stock, booking), payload)
    assert response.status_code == 400 and b'button is unavailable' in response.content
    assert not BookingMessage.objects.exists()


@pytest.mark.parametrize("state", ["canceled", "review", "released"])
def test_closed_bookings_do_not_offer_links(stock, payable, state):
    payable.state = state
    assert email.customer_link(payable, "payment") is None
    assert email.customer_link(payable, "guests") is None


def test_account_and_short_notice_hold_must_be_payable(stock, payable):
    payable.order.account.enabled = False
    assert email.customer_link(payable, "payment") is None
    payable.order.account.enabled = True
    payable.state = "held"
    payable.starts_at = timezone.now() + timedelta(minutes=10)
    assert email.customer_link(payable, "payment") is None


def test_custom_template_explicit_link_setting_and_ineligible_filter(stock, payable):
    item = SchoolEmailTemplate.objects.create(school=stock.school, purpose="bookings", booking_link="payment",
        name="Balance note", subject="Your balance", body="<p>Please use the button below.</p>")
    payload = draft(stock, payable, str(item.pk))
    assert stock.client.post(url(stock, payable), payload).status_code == 302
    assert "Pay $25.00" in BookingMessage.objects.get().body
    paid(stock, payable, 10000)
    assert email.context_for(stock.user, payable)["email_options"][str(item.pk)]["unavailable"]
    response = stock.client.post(template_url(stock, "email_template_edit", pk=item.pk), {
        "name": item.name, "booking_kind": "both", "booking_link": "guests", "subject": item.subject,
        "body": item.body, "is_active": "on"})
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.booking_link == "guests"
    response = stock.client.post(template_url(stock, "email_template_edit", pk=item.pk), {
        "name": item.name, "booking_kind": "both", "booking_link": "malicious", "subject": item.subject, "body": item.body})
    assert response.status_code == 400


def test_only_fixed_action_button_style_reaches_email_transport():
    raw = '<p style="color:red">Hello</p><a class="booking-email-action" href="https://example.invalid/pay/" style="display:none" onclick="bad()">Pay</a><a class="other" href="javascript:bad()">Bad</a>'
    clean = clean_html(raw)
    assert 'class="booking-email-action"' in clean and "style=" not in clean
    rendered = transport_html(raw)
    assert 'style="display:inline-block;' in rendered
    assert 'display:none' not in rendered and 'onclick' not in rendered and 'javascript:' not in rendered
