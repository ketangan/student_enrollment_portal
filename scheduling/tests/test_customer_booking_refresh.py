from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock
from types import SimpleNamespace
from html.parser import HTMLParser

import pytest
from django.urls import reverse
from django.utils import timezone

from scheduling import requests, reservations
from scheduling.models import WorkshopRequest
from scheduling.request_forms import request_forms
from scheduling.tests.test_experience_demo import browser, demo, form_data, local, url
from scheduling.tests.test_experience_stripe import stripe_demo
from scheduling.tests.test_package_only_booking import policy
from scheduling.tests.test_parties import party_offering
from scheduling.tests.test_party_packages import package, direct_party, package_data, send

pytestmark = pytest.mark.django_db


def assert_review_actions(content):
    class Actions(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.controls, self.bars = [], [], 0
            self.action_depth = None

        def handle_starttag(self, tag, attributes):
            attributes = dict(attributes)
            if "data-booking-review-actions" in attributes:
                assert "aside" not in self.stack, "Checkout actions must not move into the price sidebar"
                self.action_depth = len(self.stack)
                self.bars += 1
            if self.action_depth is not None and tag in ("button", "a"):
                self.controls.append(attributes)
            if tag not in {"input", "img", "br", "hr", "meta", "link", "source", "wbr"}:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in self.stack:
                del self.stack[len(self.stack) - 1 - self.stack[::-1].index(tag):]
            if self.action_depth is not None and len(self.stack) == self.action_depth:
                self.action_depth = None

    parser = Actions()
    parser.feed(content.decode())
    assert parser.bars == 1
    assert len(parser.controls) == 2
    assert "quiet" in parser.controls[0]["class"].split()
    assert "button" in parser.controls[1]["class"].split()


@pytest.mark.parametrize("payment_option", ["due", "full"])
def test_party_card_back_returns_branded_review_and_reuses_booking(client, direct_party, policy, settings, payment_option):
    from core.payments.models import PaymentAttempt, PaymentOrder
    from scheduling.models import Reservation, PartyRequest
    from scheduling.checkout import prepare_booking_checkout
    from core.payments import checkout
    settings.DEBUG = True
    settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID = "sandbox-sq0idb-test"
    settings.PAYMENTS_SQUARE_TEST_ACCESS_TOKEN = "test-only"
    settings.PAYMENTS_SQUARE_TEST_LOCATION_ID = "LTEST"
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, age_range="7-Adult", organizer_phone="555-0100")
    response = send(client, direct_party.package.offering, data)
    assert response.status_code == 302
    review_url = response.url
    item = Reservation.objects.get()
    account = item.order.account
    account.provider, account.scope, account.reference, account.location_reference = "square", "platform_demo", "MTEST", "LTEST"
    account.save()
    school = item.profile.school
    school.is_demo = True
    school.save(update_fields=["is_demo"])
    item.profile.payment_provider = "square"
    item.profile.save(update_fields=["payment_provider"])
    item.order.merchant_reference = "MTEST"
    item.order.save(update_fields=["merchant_reference"])
    attempt = prepare_booking_checkout(item, "http://testserver" + review_url, payment_option=payment_option)
    card_url = checkout.open_checkout(attempt)
    card = client.get(card_url)
    assert card.status_code == 200
    assert b"Back to review" in card.content and b'class="brandbar"' in card.content
    assert b"Back to parties" not in card.content
    for _ in range(2):
        review = client.get(attempt.cancel_url)
        assert review.status_code == 200
        assert review.context["review_due"] == Decimal(attempt.amount_cents) / 100
        assert b"One last look before the party." in review.content
        assert b"7-Adult" in review.content and b"555-0100" in review.content
        assert b"Back to parties" not in review.content and b"Choose a studio" not in review.content
        assert b"Reservation payment" not in review.content
        continued = client.post(review_url, {"payment_option": payment_option})
        assert continued.status_code == 302
        assert client.get(continued.url).context["attempt"].pk == attempt.pk
    assert Reservation.objects.count() == PartyRequest.objects.count() == PaymentOrder.objects.count() == PaymentAttempt.objects.count() == 1
    assert client.get(review_url.replace(school.slug, "other-school")).status_code == 404


@pytest.mark.parametrize("state,heading", [("canceled", "Reservation canceled"), ("review", "Reservation needs review"), ("confirmed", "Your party is confirmed.")])
def test_party_return_respects_reservation_state_and_saved_quote(client, direct_party, policy, state, heading):
    from scheduling.models import Reservation
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk)
    response = send(client, direct_party.package.offering, data)
    item = Reservation.objects.get()
    item.state = state
    item.save(update_fields=["state"])
    original_name = item.snapshot["party_package"]["name"]
    direct_party.package.name = "New package name"
    direct_party.package.total = Decimal("900")
    direct_party.package.save()
    review = client.get(response.url)
    assert review.status_code == 200
    assert heading.encode() in review.content
    assert original_name.encode() in review.content and b"New package name" not in review.content
    assert b"$495.00" in review.content
    if state in {"canceled", "review"}:
        assert b"Continue to payment" not in review.content


@pytest.fixture
def party_consents(monkeypatch):
    texts = {"revision": "test-v1", "waiver": "I acknowledge a signed waiver is required.",
             "attendance": "Each maker has a seat; up to {observers} chaperones are included.",
             "policy": "I agree to the booking and cancellation policy."}
    monkeypatch.setattr("scheduling.booking_policies.load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"booking_policies": {"source_url": "https://example.com/policy", "party_acknowledgments": texts}}}))
    return texts


@pytest.mark.parametrize("missing", ["waiver", "attendance", "policy"])
def test_party_requires_each_acknowledgment_at_booking(client, direct_party, policy, party_consents, missing):
    from scheduling.models import PartyRequest
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, **{f"{key}_acknowledged": "on" for key in ("waiver", "attendance", "policy")})
    data.pop(f"{missing}_acknowledged")
    result = send(client, direct_party.package.offering, data)
    assert result.status_code == 400
    assert f"{missing}_acknowledged" in result.context["form"].errors
    assert not PartyRequest.objects.exists()
    data["validate_only"] = "1"
    assert send(client, direct_party.package.offering, data).json() == {"errors": {}}


def test_party_acknowledgments_are_snapshotted_once(client, direct_party, policy, party_consents):
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, **{f"{key}_acknowledged": "on" for key in ("waiver", "attendance", "policy")})
    assert send(client, direct_party.package.offering, data).status_code == 302
    item = direct_party.package.offering.party_requests.get()
    record = item.offering_snapshot["acknowledgments"]
    assert record["revision"] == "test-v1"
    assert record["source_url"] == "https://example.com/policy"
    assert record["accepted_at"]
    assert all(value["accepted"] for value in record["items"].values())
    assert str(direct_party.package.included_observers) in record["items"]["attendance"]["text"]
    assert send(client, direct_party.package.offering, data).status_code == 302
    item.refresh_from_db()
    assert item.offering_snapshot["acknowledgments"] == record


def test_changed_acknowledgments_reject_old_form(client, direct_party, policy, party_consents):
    from scheduling.models import PartyRequest
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, **{f"{key}_acknowledged": "on" for key in ("waiver", "attendance", "policy")})
    party_consents["revision"] = "test-v2"
    assert send(client, direct_party.package.offering, data).status_code == 400
    assert not PartyRequest.objects.exists()


def test_package_label_and_capacity_error_explain_included_and_maximum(direct_party, policy):
    from django.core.exceptions import ValidationError
    from scheduling.party_forms import PartyInquiryForm
    package = direct_party.package
    package.additional_participant_limit = 2
    package.save()
    form = PartyInquiryForm(offering=package.offering)
    label = form.fields["package"].label_from_instance(package)
    assert "up to 6" in label and "$450.00" in label
    with pytest.raises(ValidationError, match="maximum of 8 participants"):
        package.quote(9)


@pytest.mark.parametrize("days,allowed", [(6, False), (7, False), (8, True)])
def test_choose_later_deadline_is_enforced_on_server(demo, monkeypatch, days, allowed):
    session = demo.locations.first().offerings.first().sessions.first()
    session.offering.allow_deferred_selections = True
    now = timezone.now()
    session.starts_at = now + timedelta(days=days)
    monkeypatch.setattr(timezone, "now", lambda: now)
    catalog = requests.catalog_for(session.offering)
    _, forms = request_forms(session, catalog)
    assert ("selections_deferred" in forms.empty_form.fields) is allowed
    body = {"participants-TOTAL_FORMS": "1", "participants-INITIAL_FORMS": "0",
            "participants-0-name": "Maker", "choose_when": "later"}
    for group in catalog:
        if getattr(group, "is_participant_age", False):
            body[f"participants-0-choice_{group.pk}"] = str(group.active_options[0].pk)
    _, submitted = request_forms(session, catalog, body)
    assert submitted.is_valid() is allowed


def test_review_phone_tax_and_three_step_simulated_booking(demo, browser):
    session, data = form_data(browser, demo, count=1)
    location = session.offering.location
    location.default_tax_rate = Decimal("8.875")
    location.save()
    data["purchaser_phone"] = "+1 (626) 555-0123"
    review = browser.post(url("choose", pk=session.pk), data)
    assert review.context["tax"] == Decimal("5.77")
    assert review.context["total"] == Decimal("70.77")
    assert b'form="review-payment"' in review.content
    assert_review_actions(review.content)
    data.update(action="checkout", review_token=review.context["review_token"], simulate_checkout="1")
    result = browser.post(url("choose", pk=session.pk), data)
    assert result.status_code == 302
    item = WorkshopRequest.objects.get()
    assert item.reservation.state == "confirmed"
    assert item.purchaser_phone == "+1 (626) 555-0123"
    assert item.reservation.order.total_cents == 7077
    assert item.session_snapshot["price_breakdown"]["tax_cents"] == 577
    assert browser.post(url("choose", pk=session.pk), data).status_code == 302
    assert item.reservation.order.entries.count() == 1
    location.default_tax_rate = Decimal("11")
    location.save()
    assert browser.post(url("choose", pk=session.pk), data).status_code == 400
    item.reservation.order.refresh_from_db()
    assert item.reservation.order.total_cents == 7077


@pytest.mark.parametrize("field,value", [
    ("organizer_phone", "abc"), ("participant_estimate", "many guests"),
    ("age_range", "4-6"), ("organizer_email", "not-an-email"),
])
def test_party_review_validation_has_no_booking_side_effects(client, direct_party, policy, monkeypatch, field, value):
    from scheduling.models import PartyRequest, Reservation
    monkeypatch.setattr("scheduling.party_forms.age_policy", lambda school: {"minimum": 7})
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, validate_only="1")
    data[field] = value
    response = send(client, direct_party.package.offering, data)
    assert response.status_code == 200
    assert field in response.json()["errors"]
    assert not PartyRequest.objects.exists()
    assert not Reservation.objects.exists()


def test_party_valid_review_does_not_book_or_repeat_tax_copy(client, direct_party, policy):
    from scheduling.models import PartyRequest
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, validate_only="1")
    assert send(client, direct_party.package.offering, data).json() == {"errors": {}}
    assert not PartyRequest.objects.exists()
    from scheduling.party_forms import PartyInquiryForm
    details = PartyInquiryForm(offering=direct_party.package.offering).package_details
    assert "tax" not in details[str(direct_party.package_id)]["text"].lower()


def test_party_range_reserves_and_prices_upper_bound_not_forged_count(client, direct_party, policy):
    package = direct_party.package
    package.additional_participant_limit = 3
    package.additional_participant_price = Decimal("65")
    package.save()
    data = package_data(client, package)
    data.update(party_slot=direct_party.pk, participant_count_mode="estimate", participant_estimate="7-9 guests", group_size="1")
    result = send(client, package.offering, data)
    assert result.status_code == 302
    item = package.offering.party_requests.get()
    assert item.group_size == 9
    assert item.participant_estimate == "7-9 guests"
    assert item.reservation.quantity == 9
    assert item.reservation.order.total_cents == 70950  # (450 + 3 * 65) + 10% tax
    assert send(client, package.offering, data).status_code == 302
    assert package.offering.party_requests.count() == 1


@pytest.mark.parametrize("estimate", ["", "many", "9-7", "7-10001", "7-999"])
def test_invalid_or_over_capacity_range_cannot_advance(client, direct_party, policy, estimate):
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, validate_only="1", participant_count_mode="estimate", participant_estimate=estimate)
    assert "participant_estimate" in send(client, direct_party.package.offering, data).json()["errors"]


def test_exact_mode_ignores_stale_estimate(client, direct_party, policy):
    from scheduling.party_forms import PartyInquiryForm
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, participant_count_mode="exact", participant_estimate="7-9 guests", group_size="4")
    form = PartyInquiryForm(data, offering=direct_party.package.offering)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["participant_estimate"] == ""
    assert form.cleaned_data["group_size"] == 4


def test_pay_from_review_skips_extra_checkout_screen(stripe_demo, browser, monkeypatch):
    from scheduling.experience_demo import payments
    session, data = form_data(browser, stripe_demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    assert_review_actions(review.content)
    assert b"Continue to payment" in review.content
    start = Mock(return_value="https://checkout.stripe.com/test-only")
    monkeypatch.setattr(payments, "start_checkout", start)
    data.update(action="checkout", review_token=review.context["review_token"], start_payment="1", payment_option="full")
    assert browser.post(url("choose", pk=session.pk), data).url == "https://checkout.stripe.com/test-only"
    assert start.call_args.args[0].POST["payment_option"] == "full"


def test_party_uses_shared_customer_shell_and_server_validated_totals(client, direct_party, policy, monkeypatch):
    offering = direct_party.package.offering
    route = reverse("scheduling:party_start", kwargs={"school_slug": offering.location.profile.school.slug})
    page = client.get(route, {"offering": offering.pk})
    assert_review_actions(page.content)
    assert b'data-party-panel="3"' in page.content
    assert b'scheduling/experience.css' in page.content
    assert b'id="party-tax"' in page.content
    content = page.content.decode()
    first, group = content.split('data-party-panel="2"', 1)
    assert 'id="id_group_size"' not in first
    assert 'id="id_group_size"' in group
    assert 'type="radio"' not in group
    assert 'placeholder="e.g. 8 or 7-9"' in group
    assert 'list="party-occasions"' not in content
    assert 'id="id_birthday_name"' not in content
    assert 'id="id_birthday_age"' not in content
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, customer_flow="three-step", proposed_total="1")
    from scheduling import views_checkout
    from django.http import HttpResponseRedirect
    handoff = Mock(return_value=HttpResponseRedirect("https://checkout.example.invalid/test"))
    monkeypatch.setattr(views_checkout, "customer_payment", handoff)
    result = send(client, offering, data)
    assert result.url == "https://checkout.example.invalid/test"
    assert handoff.call_count == 1
    assert offering.party_requests.get().reservation.order.total_cents == 49500
