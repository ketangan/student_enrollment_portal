from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.payments import checkout
from core.payments.models import PaymentAttempt, PaymentOrder
from scheduling import customer_edits
from scheduling.checkout import payment_token, prepare_booking_checkout
from scheduling.models import Reservation, PartyRequest, WorkshopRequest, InventoryCatalog
from scheduling.square_checkout import submit
from scheduling.tests.test_parties import party_offering
from scheduling.tests.test_party_packages import package, direct_party, package_data, send
from scheduling.tests.test_package_only_booking import policy
from scheduling.tests.test_experience_demo import local, demo, browser, form_data, url
from scheduling.tests.test_experience_stripe import stripe_demo

pytestmark = pytest.mark.django_db


def route(item, name="customer_edit_booking"):
    return reverse("scheduling:" + name, kwargs={"school_slug": item.profile.school.slug, "token": payment_token(item)})


def square_attempt(item, settings):
    settings.DEBUG = True
    settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID = "sandbox-sq0idb-test"
    settings.PAYMENTS_SQUARE_TEST_ACCESS_TOKEN = "test-only"
    settings.PAYMENTS_SQUARE_TEST_LOCATION_ID = "LTEST"
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
    return prepare_booking_checkout(item, "http://testserver" + route(item, "customer_payment"))


def party_booking(client, direct_party):
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, organizer_phone="555-123-4567", age_range="7-Adult")
    assert send(client, direct_party.package.offering, data).status_code == 302
    return Reservation.objects.get(), data


def edit_party_data(client, item, data):
    response = client.get(route(item))
    assert response.status_code == 200
    assert response.context["form"].initial["organizer_phone"] == "555-123-4567"
    return {**data, "request_token": response.context["request_token"],
            "edit_revision": response.context["edit_revision"], "participant_count_mode": "estimate",
            "participant_estimate": "4", "organizer_name": "Changed customer"}


def test_party_edit_retains_identity_closes_old_card_and_retries_once(client, direct_party, policy, settings):
    item, data = party_booking(client, direct_party)
    attempt = square_attempt(item, settings)
    direct_party.package.total = Decimal("500")
    direct_party.package.save()
    data = edit_party_data(client, item, data)
    original_order, original_source = item.order_id, item.party_request_id
    for _ in range(2):
        response = client.post(route(item), data)
        assert response.status_code == 302
    item.refresh_from_db()
    attempt.refresh_from_db()
    assert item.quantity == 4 and item.snapshot["name"] == "Changed customer"
    assert item.order_id == original_order and item.party_request_id == original_source
    assert item.order.total_cents == 55000
    assert attempt.state == "expired" and attempt.close_requested and attempt.amount_cents == 15000
    assert Reservation.objects.count() == PartyRequest.objects.count() == PaymentOrder.objects.count() == 1
    with pytest.raises(ValidationError):
        submit(attempt, "cnon:stale-card")
    new_attempt = prepare_booking_checkout(item, attempt.cancel_url)
    assert new_attempt.pk != attempt.pk
    assert item.snapshot["customer_edit_revision"] == 1


def test_party_edit_processing_payment_blocks_changes(client, direct_party, policy, settings, monkeypatch):
    item, data = party_booking(client, direct_party)
    attempt = square_attempt(item, settings)
    attempt.submitted_at, attempt.state = timezone.now(), "unknown"
    attempt.save()
    monkeypatch.setattr("scheduling.checkout.recover_attempt", lambda *args, **kwargs: False)
    data = edit_party_data(client, item, data)
    response = client.post(route(item), data)
    assert response.status_code == 400
    assert b"still being verified" in response.content
    item.refresh_from_db()
    assert item.quantity == 6 and item.snapshot["name"] != "Changed customer"


def test_party_edit_requires_revision_token(client, direct_party, policy):
    item, data = party_booking(client, direct_party)
    data = edit_party_data(client, item, data)
    data.pop("edit_revision")
    assert client.post(route(item), data).status_code == 400
    item.refresh_from_db()
    assert item.quantity == 6


def test_unpaid_party_with_catalog_has_edit_link(client, direct_party, policy):
    item, _ = party_booking(client, direct_party)
    offering = item.party_request.offering
    offering.inventory_catalog = InventoryCatalog.objects.create(profile=item.profile, name="Party designs")
    offering.save(update_fields=["inventory_catalog"])
    response = client.get(route(item, "customer_payment"))
    from django.urls import resolve
    from scheduling.checkout import customer_reservation
    target = resolve(response.context["customer_edit_url"])
    assert target.view_name == "scheduling:customer_edit_booking"
    assert customer_reservation(target.kwargs["school_slug"], target.kwargs["token"]).pk == item.pk
    assert client.get(response.context["customer_edit_url"]).status_code == 200
    assert b"Edit details" in response.content


def test_party_edit_continues_to_new_card_checkout(client, direct_party, policy, settings):
    item, data = party_booking(client, direct_party)
    old = square_attempt(item, settings)
    data = edit_party_data(client, item, data)
    response = client.post(route(item), {**data, "start_payment": "1"})
    assert response.status_code == 302
    assert "/square/pay/" in response.url
    old.refresh_from_db()
    assert old.state == "expired" and old.close_requested
    assert PaymentAttempt.objects.filter(order=item.order).count() == 2


def test_party_edit_rejects_stale_tab_and_other_school(client, direct_party, policy):
    item, data = party_booking(client, direct_party)
    data = edit_party_data(client, item, data)
    assert client.post(route(item), data).status_code == 302
    data["organizer_name"] = "Stale overwrite"
    assert client.post(route(item), data).status_code == 400
    assert client.get(route(item).replace(item.profile.school.slug, "other-school")).status_code == 404
    item.refresh_from_db()
    assert item.snapshot["name"] == "Changed customer"


def test_party_edit_capacity_failure_rolls_back_booking_and_quote(client, direct_party, policy):
    item, data = party_booking(client, direct_party)
    data = edit_party_data(client, item, data)
    room = item.room
    room.capacity = 3
    room.save()
    response = client.post(route(item), data)
    assert response.status_code == 400
    item.refresh_from_db()
    assert item.quantity == 6 and item.order.total_cents == 49500
    assert item.party_request.group_size == 6


@pytest.mark.parametrize("state", ["confirmed", "completed", "review", "canceled"])
def test_nonheld_booking_cannot_be_edited(client, direct_party, policy, state):
    item, data = party_booking(client, direct_party)
    item.state = state
    item.save(update_fields=["state"])
    response = client.post(route(item), {**data, "organizer_name": "Not allowed"})
    assert response.status_code == 302
    item.refresh_from_db()
    assert item.snapshot["name"] != "Not allowed"


def workshop_booking(browser, stripe_demo):
    session, data = form_data(browser, stripe_demo, count=2)
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    response = browser.post(url("choose", pk=session.pk), data)
    assert response.status_code == 302
    return Reservation.objects.get(), data


def test_workshop_back_edit_review_save_retains_people_and_reprices(browser, stripe_demo, settings):
    item, data = workshop_booking(browser, stripe_demo)
    attempt = square_attempt(item, settings)
    edit = browser.get(route(item))
    assert edit.status_code == 200
    assert edit.context["purchaser"].data["purchaser_name"] == "Jordan Demo"
    assert edit.context["participants"].total_form_count() == 2
    data.update(action="review", edit_revision=edit.context["edit_revision"],
                purchaser_name="Updated organizer", **{"participants-TOTAL_FORMS": "1", "participants-0-name": "Updated guest"})
    review = browser.post(route(item), data)
    assert review.status_code == 200
    assert review.context["step"] == 3
    data.update(action="checkout", review_token=review.context["review_token"])
    for _ in range(2):
        response = browser.post(route(item), data)
        assert response.status_code == 302
    item.refresh_from_db()
    attempt.refresh_from_db()
    assert item.quantity == 1 and item.snapshot["name"] == "Updated organizer"
    assert item.workshop_request.participants.get().name == "Updated guest"
    assert item.order.total_cents == 6500
    assert attempt.state == "expired" and attempt.close_requested
    assert Reservation.objects.count() == WorkshopRequest.objects.count() == PaymentOrder.objects.count() == 1
    saved_review = browser.get(response.url)
    assert saved_review.context["saved_review"] is True
    assert b"Edit details" in saved_review.content and b"Updated guest" in saved_review.content


def test_party_changed_package_quote_requires_fresh_review(client, direct_party, policy):
    item, data = party_booking(client, direct_party)
    data = edit_party_data(client, item, data)
    direct_party.package.total = Decimal("800")
    direct_party.package.save()
    response = client.post(route(item), data)
    assert response.status_code == 400
    assert b"package details changed" in response.content
    item.refresh_from_db()
    assert item.order.total_cents == 49500 and item.quantity == 6


def test_party_payment_wins_edit_race(client, direct_party, policy, settings, monkeypatch):
    item, data = party_booking(client, direct_party)
    attempt = square_attempt(item, settings)
    attempt.submitted_at, attempt.state = timezone.now(), "unknown"
    attempt.save()
    def payment_received(*args, **kwargs):
        Reservation.objects.filter(pk=item.pk).update(state="confirmed")
        PaymentAttempt.objects.filter(pk=attempt.pk).update(state="paid")
        return True
    monkeypatch.setattr("scheduling.checkout.recover_attempt", payment_received)
    data = edit_party_data(client, item, data)
    response = client.post(route(item), data)
    assert response.status_code == 400
    item.refresh_from_db()
    assert item.state == "confirmed" and item.quantity == 6


def test_workshop_change_session_retains_booking(browser, stripe_demo):
    from scheduling.experience_demo import service
    item, data = workshop_booking(browser, stripe_demo)
    other = service.sessions(stripe_demo, online=True).filter(offering=item.session.offering).exclude(pk=item.session_id).first()
    edit_url = route(item) + f"?session={other.pk}"
    edit = browser.get(edit_url)
    assert edit.status_code == 200 and edit.context["session"].pk == other.pk
    data.update(action="review", edit_revision=edit.context["edit_revision"])
    review = browser.post(edit_url, data)
    assert review.status_code == 200
    data.update(action="checkout", review_token=review.context["review_token"])
    assert browser.post(edit_url, data).status_code == 302
    item.refresh_from_db()
    assert item.session_id == item.workshop_request.session_id == other.pk
    assert item.starts_at == other.starts_at
    assert Reservation.objects.count() == 1


def test_workshop_edit_does_not_count_own_stock_twice(browser, stripe_demo):
    item, data = workshop_booking(browser, stripe_demo)
    allocations = list(item.stock_allocations.select_related("level"))
    if not allocations:
        pytest.skip("Fixture has no material recipes")
    for allocation in allocations:
        allocation.level.on_hand = allocation.quantity
        allocation.level.save(update_fields=["on_hand"])
    edit = browser.get(route(item))
    assert not edit.context["purchaser"].errors
    data.update(action="review", edit_revision=edit.context["edit_revision"])
    review = browser.post(route(item), data)
    assert review.status_code == 200 and review.context["step"] == 3
    data.update(action="checkout", review_token=review.context["review_token"])
    assert browser.post(route(item), data).status_code == 302
    item.refresh_from_db()
    assert item.stock_allocations.count() == len(allocations)


def test_party_edit_validation_is_read_only_and_rejects_stale_revision(client, direct_party, policy):
    item, data = party_booking(client, direct_party)
    data = edit_party_data(client, item, data)
    data["validate_only"] = "1"
    response = client.post(route(item), data)
    assert response.status_code == 200 and not response.json()["errors"]
    item.refresh_from_db()
    assert item.quantity == 6 and customer_edits.revision(item) == 0
    data["edit_revision"] = "stale"
    response = client.post(route(item), data)
    assert response.json()["errors"]["__all__"][0]["code"] == "stale"
    assert not item.order.attempts.exists()


def test_workshop_edit_picker_and_stale_review_preserve_saved_booking(browser, stripe_demo):
    item, data = workshop_booking(browser, stripe_demo)
    chooser = browser.get(route(item), {"change_workshop": "1"})
    assert chooser.status_code == 200 and chooser.context["step"] == 1
    edit = browser.get(route(item))
    data.update(action="review", edit_revision="stale")
    response = browser.post(route(item), data)
    assert response.status_code == 400 and b"changed in another tab" in response.content
    data["edit_revision"] = edit.context["edit_revision"]
    review = browser.post(route(item), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    data["purchaser_name"] = "Unreviewed change"
    response = browser.post(route(item), data)
    assert response.status_code == 400 and b"review expired or changed" in response.content
    item.refresh_from_db()
    assert item.snapshot["name"] == "Jordan Demo" and item.quantity == 2


def test_workshop_edit_payment_handoff_and_late_failure(browser, stripe_demo, settings, monkeypatch):
    item, data = workshop_booking(browser, stripe_demo)
    old = square_attempt(item, settings)
    edit = browser.get(route(item))
    data.update(action="review", edit_revision=edit.context["edit_revision"])
    review = browser.post(route(item), data)
    data.update(action="checkout", review_token=review.context["review_token"], start_payment="1")
    save = customer_edits.save_workshop
    def fail(*args, **kwargs):
        raise ValidationError("Capacity changed. Choose another time.")
    monkeypatch.setattr(customer_edits, "save_workshop", fail)
    response = browser.post(route(item), data)
    assert response.status_code == 400 and b"Capacity changed" in response.content
    monkeypatch.setattr(customer_edits, "save_workshop", save)
    response = browser.post(route(item), data)
    assert response.status_code == 302 and "/square/pay/" in response.url
    old.refresh_from_db()
    assert old.state == "expired" and PaymentAttempt.objects.filter(order=item.order).count() == 2


def test_edit_services_reject_cross_school_targets_before_closing_checkout(client, direct_party, policy):
    from types import SimpleNamespace
    item, data = party_booking(client, direct_party)
    other = SimpleNamespace(location=SimpleNamespace(profile_id=item.profile_id + 1))
    token = customer_edits.edit_token(item)
    with pytest.raises(ValidationError, match="studio for this business"):
        customer_edits.save_party(item, other, data, token)
    with pytest.raises(ValidationError, match="workshop for this business"):
        customer_edits.save_workshop(item, SimpleNamespace(offering=other), data, token, {}, review_token="", browser="")
    assert not item.order.attempts.exists()


def test_deferred_workshop_edit_preserves_choice_and_missing_details(browser, stripe_demo):
    item, _ = workshop_booking(browser, stripe_demo)
    item.workshop_request.participants.update(selections_deferred=True, selections=[])
    data = customer_edits.workshop_data(item, item.session)
    assert data["choose_when"] == "later"
    assert data["participants-0-selections_deferred"] == "on"
    assert data["participants-TOTAL_FORMS"] == "2"


def test_edit_finish_refuses_new_checkout_before_changing_quote(client, direct_party, policy, settings):
    item, _ = party_booking(client, direct_party)
    square_attempt(item, settings)
    with pytest.raises(ValidationError, match="another tab"):
        customer_edits.finish(item, 0, 0, "Changed", "token", "digest")
    item.refresh_from_db()
    assert item.state == "held" and item.order.total_cents == 49500
