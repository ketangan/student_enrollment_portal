from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import uuid

import pytest
from django.http import Http404
from django.core.exceptions import ValidationError
from django.test import Client
from pytest_django.asserts import assertContains

from core.models import AdminAuditLog, School
from scheduling import inventory, preparation, reservations
from scheduling.models import (SchedulingMembership, SchedulingProfile, Location,
    CatalogVariant, StockAdjustment, Reservation, Camp, CampRegistration, PartyRequest)
from scheduling.tests.test_inventory import stock, reserve, confirm, url, selections

pytestmark = pytest.mark.django_db


def command(s, booking, action):
    booking.refresh_from_db()
    return {"action": action, "edit_revision": preparation.form_for(booking, s.profile)["edit_revision"].value()}


def test_readiness_is_reversible_audited_and_independent_of_money_and_stock(stock):
    s = stock
    booking = reserve(s, 2)
    confirm(s, booking)
    entries = list(booking.order.entries.values())
    available = inventory.available(s.level)
    first = command(s, booking, "prepare")
    preparation.update(s.user, s.school.slug, booking.public_id, first)
    booking.refresh_from_db()
    assert booking.prepared_at and booking.prepared_by == s.user and booking.preparation_version == 1
    assert AdminAuditLog.objects.filter(model_label="scheduling.reservation", changes__has_key="prepared_at").exists()
    same = command(s, booking, "prepare")
    preparation.update(s.user, s.school.slug, booking.public_id, same)
    assert Reservation.objects.get(pk=booking.pk).preparation_version == 1
    with pytest.raises(ValidationError, match="changed"):
        preparation.update(s.user, s.school.slug, booking.public_id, first)
    preparation.update(s.user, s.school.slug, booking.public_id, command(s, booking, "reopen"))
    booking.refresh_from_db()
    assert not booking.prepared_at and not booking.prepared_by and booking.preparation_version == 2
    with pytest.raises(ValidationError, match="changed"):
        preparation.update(s.user, s.school.slug, booking.public_id, first)
    s.level.refresh_from_db()
    assert s.level.on_hand == 5 and inventory.available(s.level) == available
    assert not StockAdjustment.objects.exists() and list(booking.order.entries.values()) == entries


@pytest.mark.parametrize("action", ["prepare", "reopen"])
def test_preparation_blocks_unconfirmed_and_consumed(stock, action):
    s = stock
    booking = reserve(s)
    with pytest.raises(ValidationError, match="confirmed"):
        preparation.update(s.user, s.school.slug, booking.public_id, command(s, booking, action))
    confirm(s, booking)
    inventory.consume(s.user, s.school.slug, booking.public_id)
    with pytest.raises(ValidationError, match="already been used"):
        preparation.update(s.user, s.school.slug, booking.public_id, command(s, booking, action))
    reservations.cancel_reservation(s.user, s.school.slug, booking.public_id)
    with pytest.raises(ValidationError, match="confirmed"):
        preparation.update(s.user, s.school.slug, booking.public_id, command(s, booking, action))


def test_missing_materials_and_invalid_command_cannot_mark_ready(stock):
    s = stock
    booking = reserve(s); confirm(s, booking)
    with pytest.raises(ValidationError):
        preparation.update(s.user, s.school.slug, booking.public_id, {"action": "wrong", "edit_revision": "forged"})
    booking.stock_allocations.all().delete()
    with pytest.raises(ValidationError, match="do not match"):
        preparation.update(s.user, s.school.slug, booking.public_id, command(s, booking, "prepare"))
    s.offering.inventory_catalog = None; s.offering.save()
    with pytest.raises(ValidationError, match="do not match"):
        preparation.update(s.user, s.school.slug, booking.public_id, command(s, booking, "prepare"))
    assert not Reservation.objects.get(pk=booking.pk).prepared_at


@pytest.mark.parametrize("role,allowed", [("owner", True), ("scheduler", True), ("viewer", False)])
def test_preparation_route_permissions_csrf_and_safe_return(stock, role, allowed):
    s = stock
    booking = reserve(s); confirm(s, booking)
    SchedulingMembership.objects.filter(user=s.user).update(role=role)
    target = url(s, "preparation_update", public_id=booking.public_id)
    detail = url(s, "reservation_preparation", public_id=booking.public_id)
    assert s.client.get(target).status_code == 405
    assert (b"Mark prepared" in s.client.get(detail).content) == allowed
    data = {**command(s, booking, "prepare"), "return_to": "https://evil.invalid/"}
    csrf_client = Client(enforce_csrf_checks=True); csrf_client.force_login(s.user)
    assert csrf_client.post(target, data).status_code == 403
    response = s.client.post(target, data)
    if allowed:
        assert response.status_code == 302 and response.url == detail
        assertContains(s.client.get(detail), "Reopen preparation")
        rejected = s.client.post(target, data, follow=True)
        assertContains(rejected, "Reload this page")
    else:
        assert response.status_code == 404
    alien = SchedulingProfile.objects.create(school=School.objects.create(slug="private-other"), enabled=True)
    with pytest.raises(Http404):
        preparation.update(s.user, alien.school.slug, booking.public_id, data)
    s.profile.capabilities = []; s.profile.save()
    assert s.client.post(target, data).status_code == 404


def test_readiness_works_for_party_and_camp_reservations(stock):
    s = stock
    workshop = reserve(s)
    camp = Camp.objects.create(offering=s.offering, name="Camp", price=10)
    registration = CampRegistration.objects.create(camp=camp, purchaser_name="Parent", purchaser_email="p@example.invalid",
        submission_key=uuid.uuid4(), submission_digest="d", browser_digest="b",
        participants=[{"name": "Child", "selections": selections(s)}])
    workshop.workshop_request = None; workshop.session = None; workshop.room = None
    workshop.camp_registration = registration; workshop.state = "confirmed"; workshop.save()
    preparation.update(s.user, s.school.slug, workshop.public_id, command(s, workshop, "prepare"))
    party = PartyRequest.objects.create(offering=s.offering, organizer_name="Party", organizer_email="p@example.invalid",
        group_size=1, starts_at=s.session.starts_at, ends_at=s.session.ends_at, space="shared",
        materials=[{"item_id": s.item.pk, "name": s.item.name, "quantity": 1}],
        submission_key=uuid.uuid4(), submission_digest="d", browser_digest="b")
    workshop.camp_registration = None; workshop.party_request = party; workshop.room = s.room
    workshop.prepared_at = None; workshop.prepared_by = None; workshop.save()
    preparation.update(s.user, s.school.slug, workshop.public_id, command(s, workshop, "prepare"))
    assert Reservation.objects.get(pk=workshop.pk).prepared_at


def test_reservation_filters_search_dates_status_and_studio_are_scoped(stock):
    s = stock
    booking = reserve(s)
    confirm(s, booking)
    s.location.timezone_name = "America/Los_Angeles"; s.location.save()
    booking.refresh_from_db()
    booking.starts_at = datetime(2026, 11, 2, 7, 30, tzinfo=ZoneInfo("UTC"))  # Nov 1 after DST ends.
    booking.ends_at = booking.starts_at + timedelta(hours=1); booking.save()
    target = url(s, "reservations")
    for query in [{"q": "parent"}, {"q": "p@example.invalid"}, {"q": "workshop"},
                  {"state": "confirmed"}, {"location": s.location.pk},
                  {"date_from": "2026-11-01", "date_to": "2026-11-01"}]:
        response = s.client.get(target, query)
        assert list(response.context["items"]) == [booking], query
        assert response.context["items"][0].location_name == "West studio"
    for query in [{"q": "missing"}, {"state": "held"}, {"date_from": "2026-11-02"}]:
        response = s.client.get(target, query)
        assert not list(response.context["items"])
        assertContains(response, "No reservations match these filters.")
    east = Location.objects.create(profile=s.profile, name="East")
    assert not list(s.client.get(target, {"location": east.pk}).context["items"])
    for invalid in [{"location": 999999}, {"date_from": "bad"}, {"state": "forged"},
                    {"date_from": "2026-11-02", "date_to": "2026-11-01"}, {"date_to": "9999-12-31"}]:
        response = s.client.get(target, invalid)
        assert response.context["filter_form"].errors and not list(response.context["items"])
    response = s.client.get(target, {"q": "Parent", "state": "confirmed", "attention": 1})
    assertContains(response, "q=Parent&amp;state=confirmed")
    from django.utils.html import escape
    assertContains(response, f'href="{escape(response.context["attention_url"])}">Clear filters')


def test_catalog_search_pagination_and_grouped_navigation(stock):
    s = stock
    for index in range(30):
        from scheduling.models import ChoiceOption
        ChoiceOption.objects.create(group=s.design, label=f"Test design {index:02}")
    target = url(s, "inventory_catalog", catalog_pk=s.catalog.pk)
    page = s.client.get(target, {"q": "test", "page": 2})
    assert len(page.context["designs"]) == 12
    assertContains(page, "q=test&amp;page=1")
    empty = s.client.get(target, {"q": "not found"})
    assertContains(empty, "No designs match your search.")
    assertContains(page, 'aria-label="Setup sections"')
    assertContains(page, 'class="dash-nav-link dash-nav-link--active" aria-current="page">Setup')
    s.profile.capabilities = []; s.profile.save()
    page = s.client.get(url(s, "settings"))
    assertContains(page, 'aria-label="Setup sections"')
    assert b'>Requests</a>' not in page.content
    assert f'href="{url(s, "workshops")}"'.encode() not in page.content


@pytest.mark.django_db(transaction=True)
def test_simultaneous_preparation_edits_cannot_overwrite(stock):
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections
    s = stock
    booking = reserve(s); confirm(s, booking)
    data = command(s, booking, "prepare")
    def save(_):
        close_old_connections()
        try:
            preparation.update(s.user, s.school.slug, booking.public_id, data)
            return "saved"
        except ValidationError:
            return "stale"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == ["saved", "stale"]
    assert Reservation.objects.get(pk=booking.pk).preparation_version == 1
