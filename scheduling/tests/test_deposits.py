from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.payments import checkout, service as ledger, square
from core.payments.models import PaymentAttempt
from core.services import billing_stripe
from scheduling import reservations
from scheduling.checkout import payment_token, prepare_booking_checkout, settle_booking_event, validate_booking_checkout
from scheduling.forms import OfferingForm
from scheduling.models import Offering, Reservation
from scheduling.party_forms import PartyReviewForm
from .test_checkout import merchant, online, prepare, event_for, session_for
from .test_experience_demo import browser, demo, local, form_data, url
from .test_experience_stripe import stripe_demo
from .test_reservations import inventory, workshop_request, party_request
from .test_square_payments import square_account, pay

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("mode,deposit,valid", [
    ("full", "", True), ("deposit", "25", True), ("deposit", "", False),
    ("deposit", "0", False), ("deposit", "-1", False), ("deposit", "0.49", False),
    ("deposit", "65", False), ("deposit", "64.75", False), ("deposit", "64.50", True),
])
def test_workshop_payment_setting(inventory, mode, deposit, valid):
    profile, offering, _, _ = inventory
    form = OfferingForm({"name": offering.name, "location": offering.location_id,
        "payment_at_booking": mode, "deposit_per_participant": deposit},
        instance=offering, profile=profile, unit_price_cents=6500)
    assert form.is_valid() == valid, form.errors
    if valid:
        saved = form.save()
        assert saved.deposit_per_participant == (Decimal(deposit) if mode == "deposit" else 0)


def test_workshop_quote_and_full_payment_default(inventory):
    offering = inventory[1]
    assert reservations.workshop_quote(offering, 6500, 2) == (13000, 0)
    offering.deposit_per_participant = Decimal("25")
    assert reservations.workshop_quote(offering, 6500, 2) == (13000, 5000)
    with pytest.raises(ValidationError):
        reservations.workshop_quote(offering, 2000, 2)


@pytest.mark.parametrize("mode,deposit,valid", [
    ("full", "", True), ("deposit", "100", True), ("deposit", "0", False),
    ("deposit", "", False), ("deposit", "400", False), ("deposit", "399.75", False),
])
def test_party_deposit_is_per_reservation(inventory, mode, deposit, valid):
    party = party_request(inventory)
    form = PartyReviewForm({"revision": party.revision, "status": "reviewed", "room": party.room_id,
        "proposed_total": "400", "payment_at_booking": mode, "proposed_deposit": deposit}, instance=party)
    assert form.is_valid() == valid, form.errors
    if valid:
        saved = form.save()
        assert saved.proposed_deposit == (Decimal(deposit) if mode == "deposit" else 0)
        assert saved.proposed_balance == (300 if mode == "deposit" else 0)


def test_public_review_snapshots_deposit_and_rejects_stale_settings(stripe_demo, browser):
    session, data = form_data(browser, stripe_demo, count=2)
    Offering.objects.filter(pk=session.offering_id).update(deposit_per_participant=25)
    review = browser.post(url("choose", pk=session.pk), data)
    assert review.status_code == 200
    assert review.context["due_now"] == Decimal("50")
    assert review.context["due_later"] == Decimal("80")
    assert b"Pay in full or pay a $50.00 deposit" in review.content
    assert b"Continue to checkout <" in review.content
    for removed_copy in (b"Deposit option", b"checkout demo", b"test checkout", b"No real charge"):
        assert removed_copy not in review.content
    data.update(action="checkout", review_token=review.context["review_token"])
    Offering.objects.filter(pk=session.offering_id).update(deposit_per_participant=30)
    assert browser.post(url("choose", pk=session.pk), data).status_code == 400
    assert not Reservation.objects.exists()
    data["action"] = "review"
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"], deposit="0", total="1")
    result = browser.post(url("choose", pk=session.pk), data)
    assert result.status_code == 302
    item = Reservation.objects.get()
    assert (item.order.total_cents, item.order.deposit_cents) == (13000, 6000)
    Offering.objects.filter(pk=session.offering_id).update(deposit_per_participant=0)
    page = browser.get(result.url)
    assert page.context["due_now"] == Decimal("60")
    assert page.context["due_later"] == Decimal("70")
    deposit = prepare(item)
    settle_booking_event(event_for(deposit))
    page = browser.get(result.url)
    assert b"Pay remaining balance: $70.00" in page.content
    assert b"Booking confirmed" in page.content
    assert b"Demo booking confirmed" not in page.content
    settle_booking_event(event_for(prepare(item)))
    assert b"Paid in full" in browser.get(result.url).content
    assert b"Pay remaining balance" not in browser.get(result.url).content
    assert item.order.entries.count() == 2


@pytest.mark.parametrize("kind", ["workshop", "party"])
@pytest.mark.parametrize("minutes", [-120, -5, 5, 39])
def test_stripe_balance_payable_on_event_day(inventory, merchant, booking_member, kind, minutes):
    source = party_request(inventory) if kind == "party" else workshop_request(inventory)
    item = online(booking_member, source)
    deposit = prepare(item)
    settle_booking_event(event_for(deposit))
    Reservation.objects.filter(pk=item.pk).update(starts_at=timezone.now() + timedelta(minutes=minutes))
    balance = prepare(item)
    assert balance.amount_cents == item.order.total_cents - item.order.deposit_cents
    settle_booking_event(event_for(balance))
    settle_booking_event(event_for(balance))
    assert item.order.entries.count() == 2
    assert ledger.totals(item.order)["outstanding_cents"] == 0
    with pytest.raises(ValidationError, match="No payable balance"):
        prepare(item)


def test_new_booking_still_has_cutoff(inventory, merchant, booking_member):
    item = online(booking_member, workshop_request(inventory))
    item.starts_at = timezone.now() + timedelta(minutes=39)
    with pytest.raises(ValidationError, match="40 minutes"):
        validate_booking_checkout(item)


@pytest.mark.parametrize("kind", ["workshop", "party"])
def test_square_balance_decline_retry_and_duplicate(inventory, square_account, booking_member, monkeypatch, client, kind):
    source = party_request(inventory) if kind == "party" else workshop_request(inventory)
    item = online(booking_member, source)
    deposit = prepare(item)
    pay(deposit, monkeypatch)
    Reservation.objects.filter(pk=item.pk).update(starts_at=timezone.now() - timedelta(minutes=10))
    balance = prepare(item)
    expected = item.order.total_cents - item.order.deposit_cents
    assert balance.amount_cents == expected
    def decline(*args, **kwargs):
        raise square.Declined("Card declined.")
    monkeypatch.setattr(square, "request", decline)
    from scheduling.square_checkout import submit
    with pytest.raises(ValidationError):
        submit(balance, "cnon:decline")
    item.refresh_from_db()
    assert item.state == "confirmed"
    assert ledger.totals(item.order)["outstanding_cents"] == expected
    replacement = prepare(item)
    assert replacement.pk != balance.pk and replacement.amount_cents == expected
    call = pay(replacement, monkeypatch)
    with pytest.raises(ValidationError):
        submit(replacement, "cnon:duplicate")
    assert call.call_count == 1
    assert item.order.entries.count() == 2
    assert ledger.totals(item.order)["outstanding_cents"] == 0


def test_admin_balance_action_requires_access(inventory, merchant, booking_member, client):
    item = online(booking_member, workshop_request(inventory))
    settle_booking_event(event_for(prepare(item)))
    route = reverse("scheduling:reservation_detail", args=[inventory[0].school.slug, item.public_id])
    assert client.get(route).status_code == 302
    client.force_login(booking_member.user)
    assert b"Take online payment" in client.get(route).content


@pytest.mark.parametrize("provider_fixture", ["merchant", "square_account"])
@pytest.mark.parametrize("kind", ["workshop", "party"])
@pytest.mark.parametrize("option", ["due", "full"])
def test_customer_can_choose_deposit_or_full_payment(inventory, booking_member, client, monkeypatch,
                                                    request, provider_fixture, kind, option):
    account = request.getfixturevalue(provider_fixture)
    source = party_request(inventory) if kind == "party" else workshop_request(inventory)
    item = online(booking_member, source)
    total, deposit = item.order.total_cents, item.order.deposit_cents
    amount = deposit if option == "due" else total
    route = reverse("scheduling:customer_payment", args=[item.profile.school.slug, payment_token(item)])
    page = client.get(route)
    assert b'How would you like to pay?' in page.content
    assert f'Pay deposit: ${deposit / 100:.2f}'.encode() in page.content
    assert f'Pay in full: ${total / 100:.2f}'.encode() in page.content
    gateway_call = Mock(side_effect=session_for)
    monkeypatch.setattr(billing_stripe, "create_order_checkout", gateway_call)
    data = {"payment_option": option, "amount_cents": "1", "total": "1"}
    assert client.post(route, data).status_code == 302
    attempt = item.order.attempts.get()
    assert attempt.amount_cents == amount
    assert client.post(route, data).status_code == 302
    assert item.order.attempts.count() == 1
    assert b'How would you like to pay?' not in client.get(route).content
    other = "due" if option == "full" else "full"
    assert client.post(route, {"payment_option": other}).status_code == 400
    assert item.order.attempts.count() == 1
    if account.provider == "square":
        pay(attempt, monkeypatch)
    else:
        settle_booking_event(event_for(attempt))
        settle_booking_event(event_for(attempt))
        assert gateway_call.call_count == 1
    item.refresh_from_db()
    assert item.state == "confirmed"
    assert item.order.deposit_cents == deposit
    assert ledger.totals(item.order)["outstanding_cents"] == total - amount
    assert item.order.entries.count() == 1
    page = client.get(route)
    assert b'How would you like to pay?' not in page.content
    assert (b'Paid in full' in page.content) == (option == "full")


@pytest.mark.parametrize("option", ["", "partial", "0", "-1", "10000"])
def test_invalid_payment_choice_creates_no_attempt(inventory, merchant, booking_member, client, option):
    item = online(booking_member, workshop_request(inventory))
    route = reverse("scheduling:customer_payment", args=[item.profile.school.slug, payment_token(item)])
    assert client.post(route, {"payment_option": option}).status_code == 400
    assert not item.order.attempts.exists()


def test_experience_full_payment_reaches_provider(stripe_demo, browser, monkeypatch):
    session, data = form_data(browser, stripe_demo, count=1)
    Offering.objects.filter(pk=session.offering_id).update(deposit_per_participant=25)
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    result = browser.post(url("choose", pk=session.pk), data)
    page = browser.get(result.url)
    assert b'Pay in full: $65.00' in page.content
    monkeypatch.setattr(billing_stripe, "create_order_checkout", Mock(side_effect=session_for))
    response = browser.post(result.url, {"action": "payment_checkout", "payment_option": "full"})
    assert response.status_code == 302
    attempt = PaymentAttempt.objects.get()
    assert attempt.amount_cents == 6500 and attempt.order.deposit_cents == 2500
    settle_booking_event(event_for(attempt))
    assert b'Paid in full' in browser.get(result.url).content


def test_square_full_payment_retry_keeps_selected_amount(inventory, square_account, booking_member, monkeypatch, client):
    item = online(booking_member, workshop_request(inventory))
    attempt = prepare_booking_checkout(item, "http://localhost/return/", payment_option="full")
    route = checkout.open_checkout(attempt)
    from scheduling.square_checkout import submit
    monkeypatch.setattr(square, "request", Mock(side_effect=square.Declined("Card declined.")))
    with pytest.raises(ValidationError):
        submit(attempt, "cnon:decline")
    response = client.post(route, {"action": "retry"})
    assert response.status_code == 302
    replacement = item.order.attempts.latest("pk")
    assert replacement.pk != attempt.pk and replacement.amount_cents == 10000
    pay(replacement, monkeypatch)
    assert ledger.totals(item.order)["outstanding_cents"] == 0
