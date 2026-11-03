from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.http import Http404

from core.models import AdminAuditLog
from scheduling import reservations
from scheduling.experience_demo import config, service
from scheduling.forms import OfferingForm
from scheduling.models import Offering, Reservation
from scheduling.setup import build_form
from .test_setup import catalog, other_catalog, save, url as setup_url
from .test_experience_demo import browser, demo, form_data, local, url
from .test_experience_stripe import stripe_demo
from .test_customer_edits import route

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("price,deposit,valid", [
    ("65", "25", True), ("65", "64.50", True), ("65", "64.51", False),
    ("20", "25", False), ("0", "0", True), ("", "0", True),
    ("-1", "0", False), ("1.001", "0", False), ("NaN", "0", False),
])
def test_price_and_deposit_validation(booking_member, catalog, price, deposit, valid):
    offering = catalog[2]
    form = OfferingForm({"name": offering.name, "location": offering.location_id,
        "price_per_participant": price, "payment_at_booking": "deposit" if deposit != "0" else "full",
        "deposit_per_participant": deposit}, instance=offering, profile=booking_member.profile)
    assert form.is_valid() == valid, form.errors
    if valid:
        saved = form.save()
        assert saved.price_per_participant == (Decimal(price) if price else None)
        assert saved.deposit_per_participant == Decimal(deposit)


def test_setup_is_school_scoped_and_does_not_load_demo_configuration(booking_member, catalog, other_catalog, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Workshop setup must not load demo pricing")
    monkeypatch.setattr(config, "configuration", forbidden)
    offering = catalog[2]
    form = save(booking_member, "workshops", {"name": offering.name, "location": offering.location_id,
        "price_per_participant": "72.50", "payment_at_booking": "full"}, pk=offering.pk)
    assert form.is_valid(), form.errors
    offering.refresh_from_db()
    assert offering.price_per_participant == Decimal("72.50")
    assert reservations.workshop_unit_price(offering) == 7250
    audit = AdminAuditLog.objects.get(object_id=str(offering.pk), model_label="scheduling.offering")
    assert audit.changes["price_per_participant"] == {"from": None, "to": "72.50"}
    with pytest.raises(Http404):
        build_form(booking_member.profile, "workshops", pk=other_catalog[3].pk)
    other_catalog[3].refresh_from_db()
    assert other_catalog[3].price_per_participant is None


def test_old_forms_preserve_price_but_explicit_blank_clears_it(booking_member, catalog):
    offering = catalog[2]
    Offering.objects.filter(pk=offering.pk).update(price_per_participant="72.50")
    for change, expected in [({}, Decimal("72.50")), ({"price_per_participant": ""}, None)]:
        form = save(booking_member, "workshops", {"name": offering.name,
            "location": offering.location_id, **change}, pk=offering.pk)
        assert form.is_valid(), form.errors
        offering.refresh_from_db()
        assert offering.price_per_participant == expected


@pytest.mark.parametrize("price,deposit", [("-1", "0"), ("10", "10"), ("10", "9.51")])
def test_database_rejects_invalid_commercial_values(catalog, price, deposit):
    with pytest.raises(IntegrityError), transaction.atomic():
        Offering.objects.filter(pk=catalog[2].pk).update(price_per_participant=price, deposit_per_participant=deposit)


def test_blank_price_is_not_free(catalog):
    with pytest.raises(ValidationError, match="does not have a price"):
        reservations.workshop_unit_price(catalog[2])


def test_setup_renders_price_in_payment_section(client, booking_member, catalog):
    client.force_login(booking_member.user)
    page = client.get(setup_url("workshop_edit", booking_member.profile, pk=catalog[2].pk))
    html = page.content.decode()
    assert html.index("Booking payments") < html.index('id="id_price_per_participant"') < html.index('id="id_payment_at_booking"')
    assert f'<a class="button" href="{setup_url("workshop_detail", booking_member.profile, pk=catalog[2].pk)}">Cancel</a>' in html
    detail = client.get(setup_url("workshop_detail", booking_member.profile, pk=catalog[2].pk))
    assert b"Not set" in detail.content
    Offering.objects.filter(pk=catalog[2].pk).update(price_per_participant=0)
    assert b"$0.00" in client.get(setup_url("workshop_detail", booking_member.profile, pk=catalog[2].pk)).content


def test_review_uses_workshop_price_and_preserves_saved_quote(demo, browser, monkeypatch):
    session, data = form_data(browser, demo, count=2)
    Offering.objects.filter(pk=session.offering_id).update(price_per_participant="72.50", deposit_per_participant=25)
    location = session.offering.location
    location.default_tax_rate = Decimal("8.25")
    location.save(update_fields=["default_tax_rate"])
    original = config.configuration(demo.school.slug)
    monkeypatch.setattr(config, "configuration", lambda slug: {**original, "price_cents": 1})
    review = browser.post(url("choose", pk=session.pk), data)
    assert review.status_code == 200
    assert review.context["unit_price"] == Decimal("72.50")
    assert review.context["subtotal"] == Decimal("145.00")
    assert review.context["tax"] == Decimal("11.96")
    assert review.context["total"] == Decimal("156.96")
    assert review.context["due_now"] == Decimal("50")
    data.update(action="checkout", review_token=review.context["review_token"], total="1", price_cents="1")
    response = browser.post(url("choose", pk=session.pk), data)
    assert response.status_code == 302
    item = Reservation.objects.get()
    assert (item.order.total_cents, item.order.deposit_cents) == (15696, 5000)
    assert item.workshop_request.session_snapshot["experience_demo"]["price_cents"] == 7250
    Offering.objects.filter(pk=session.offering_id).update(price_per_participant=90)
    assert browser.get(response.url).context["total"] == Decimal("156.96")
    item.order.refresh_from_db()
    assert item.order.total_cents == 15696 and not item.order.entries.exists()


def test_unpriced_workshop_cannot_be_booked_but_existing_confirmation_survives(demo, browser):
    session, data = form_data(browser, demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    response = browser.post(url("choose", pk=session.pk), data)
    assert response.status_code == 302
    assert browser.post(response.url, {"action": "complete"}).status_code == 302
    item = Reservation.objects.get()
    evidence = list(item.order.entries.values())
    Offering.objects.filter(pk=session.offering_id).update(price_per_participant=None)
    assert session.pk not in {row["id"] for row in service.session_rows(demo)}
    assert browser.get(url("choose", pk=session.pk)).status_code == 404
    assert browser.post(url("choose", pk=session.pk), data).status_code == 404
    assert browser.get(response.url).status_code == 200
    assert list(item.order.entries.values()) == evidence
    item.refresh_from_db()
    assert item.state == "confirmed" and item.order.total_cents == 6500


def test_price_removed_while_waiting_for_lock_is_rejected(demo, browser, monkeypatch):
    session, data = form_data(browser, demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    begin = service.begin
    def remove_price(*args, **kwargs):
        Offering.objects.filter(pk=session.offering_id).update(price_per_participant=None)
        return begin(*args, **kwargs)
    monkeypatch.setattr(service, "begin", remove_price)
    assert browser.post(url("choose", pk=session.pk), data).status_code == 404
    assert not Reservation.objects.exists()


def test_session_prices_are_per_offering(demo):
    offerings = list(Offering.objects.filter(location__profile=demo, accepts_requests=True).order_by("pk"))
    Offering.objects.filter(pk=offerings[0].pk).update(price_per_participant="82.25")
    rows = service.session_rows(demo)
    assert {r["price_cents"] for r in rows if r["workshop_id"] == offerings[0].pk} == {8225}
    assert {r["price_cents"] for r in rows if r["workshop_id"] != offerings[0].pk} == {6500}


def test_demo_seed_initializes_missing_price_without_overwriting_edits(demo):
    offerings = list(Offering.objects.filter(location__profile=demo, accepts_requests=True).order_by("pk"))
    Offering.objects.filter(pk=offerings[0].pk).update(price_per_participant=None)
    Offering.objects.filter(pk=offerings[1].pk).update(price_per_participant="72.50")
    call_command("seed_booking_experience_demo", demo.school.slug, password="admin")
    offerings[0].refresh_from_db()
    offerings[1].refresh_from_db()
    assert offerings[0].price_per_participant == Decimal("65")
    assert offerings[1].price_per_participant == Decimal("72.50")


def test_unpaid_edit_rejects_changed_price_and_rereviews(stripe_demo, browser):
    session, data = form_data(browser, stripe_demo, count=1)
    review = browser.post(url("choose", pk=session.pk), data)
    data.update(action="checkout", review_token=review.context["review_token"])
    assert browser.post(url("choose", pk=session.pk), data).status_code == 302
    item = Reservation.objects.get()
    edit = browser.get(route(item))
    data.update(action="review", edit_revision=edit.context["edit_revision"])
    review = browser.post(route(item), data)
    assert review.status_code == 200
    data.update(action="checkout", review_token=review.context["review_token"])
    Offering.objects.filter(pk=session.offering_id).update(price_per_participant="75.25")
    assert browser.post(route(item), data).status_code == 400
    item.order.refresh_from_db()
    assert item.order.total_cents == 6500
    data["action"] = "review"
    review = browser.post(route(item), data)
    assert review.context["unit_price"] == Decimal("75.25")
    data.update(action="checkout", review_token=review.context["review_token"])
    assert browser.post(route(item), data).status_code == 302
    item.order.refresh_from_db()
    assert item.order.total_cents == 7525 and not item.order.entries.exists()


@pytest.mark.django_db(transaction=True)
def test_price_migration_preserves_existing_workshop(catalog):
    pk = catalog[2].pk
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("scheduling", "0050_booking_staff_assignments")])
        MigrationExecutor(connection).migrate(latest)
        offering = Offering.objects.get(pk=pk)
        assert offering.name == catalog[2].name and offering.price_per_participant is None
        assert offering.deposit_per_participant == 0
    finally:
        MigrationExecutor(connection).migrate(latest)
