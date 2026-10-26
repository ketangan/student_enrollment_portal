from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from scheduling.access import require_location_access
from scheduling.models import Camp, Reservation, SchedulingMembership, SchedulingProfile, Session
from scheduling.studio_preparation import summary
from .test_inventory import stock, reserve, confirm, url
from .test_camps import camp, register
from .test_reservations import inventory, party_request, reserve as reserve_party, path
from .test_location_access import two_locations

pytestmark = pytest.mark.django_db


def query(item, **changes):
    reservation = item if isinstance(item, Reservation) else item.reservation
    source = reservation.workshop_request or reservation.party_request or reservation.camp_registration
    offering = (source.session.offering if reservation.workshop_request_id else
        source.camp.offering if reservation.camp_registration_id else source.offering)
    day = timezone.localtime(item.starts_at, ZoneInfo(offering.location.timezone_name)).date()
    return {"date": day.isoformat(), "period": "week", **changes}


def test_read_only_roster_material_totals_and_constant_queries(stock):
    s = stock
    item = reserve(s); confirm(s, item)
    endpoint = url(s, "studio_preparation")
    response = s.client.get(endpoint, query(item))
    assert response.status_code == 200
    assert b"Child" in response.content and b"Cat" in response.content and b"Blue" in response.content
    assert response.context["studios"][0]["totals"][0]["quantity"] == 1
    assert response.context["location_action"] == endpoint
    profile, _ = require_location_access(s.user, s.school.slug, capability="reservations")
    with CaptureQueriesContext(connection) as first:
        summary(profile, [s.location], item.starts_at.date() - timedelta(days=1), 7)
    other = reserve(s); confirm(s, other)
    with CaptureQueriesContext(connection) as second:
        result = summary(profile, [s.location], item.starts_at.date() - timedelta(days=1), 7)
    assert len(first) == len(second) <= 6
    assert len(result[0]["entries"]) == 2 and result[0]["totals"][0]["quantity"] == 2
    item.refresh_from_db()
    item.prepared_at = timezone.now(); item.save(update_fields=["prepared_at"])
    result = s.client.get(endpoint, query(item)).context["studios"]
    assert result[0]["totals"][0]["quantity"] == 1
    assert len(result[0]["entries"]) == 2
    assert s.client.post(endpoint, {}).status_code == 405
    s.level.refresh_from_db()
    assert s.level.on_hand == 5


@pytest.mark.parametrize("state", ["held", "canceled", "review", "completed"])
def test_only_confirmed_bookings(stock, state):
    item = reserve(stock)
    Reservation.objects.filter(pk=item.pk).update(state=state)
    assert not stock.client.get(url(stock, "studio_preparation"), query(item)).context["studios"]


@pytest.mark.parametrize("params", [{"date": "bad"}, {"date": "9999-12-31"}, {"period": "month"}, {"location": "9999999"}])
def test_invalid_filters_never_broaden_results(stock, params):
    item = reserve(stock); confirm(stock, item)
    response = stock.client.get(url(stock, "studio_preparation"), query(item, **params))
    assert response.status_code == 200 and not response.context["studios"]


def test_default_dates_empty_and_feature_guard(stock):
    endpoint = url(stock, "studio_preparation")
    assert b"No confirmed bookings" in stock.client.get(endpoint).content
    stock.profile.capabilities = []; stock.profile.save()
    assert stock.client.get(endpoint).status_code == 404
    stock.client.logout()
    assert stock.client.get(endpoint).status_code == 302


def test_other_school_cannot_read_roster(stock):
    from core.models import School
    s = stock
    item = reserve(s); confirm(s, item)
    school = School.objects.create(slug="separate-studio")
    profile = SchedulingProfile.objects.create(school=school, enabled=True, capabilities=["reservations"])
    target = url(s, "studio_preparation").replace(s.school.slug, school.slug)
    assert s.client.get(target).status_code == 404
    SchedulingMembership.objects.create(profile=profile, user=s.user, role="viewer")
    assert b"Child" not in s.client.get(target, query(item)).content


def test_used_materials_and_saved_units(stock):
    s = stock
    item = reserve(s); confirm(s, item)
    item.snapshot["materials"] = [{"material_id": s.material.pk, "name": "Saved fabric", "unit": "pieces"}]
    item.save(update_fields=["snapshot"])
    response = s.client.get(url(s, "studio_preparation"), query(item))
    assert response.context["studios"][0]["totals"][0]["name"] == "Saved fabric"
    item.stock_allocations.update(consumed_at=timezone.now())
    response = s.client.get(url(s, "studio_preparation"), query(item))
    assert b"Materials used" in response.content and not response.context["studios"][0]["totals"]


@pytest.mark.parametrize("role", ["owner", "scheduler", "viewer"])
def test_location_permissions(two_locations, booking_member, client, role):
    mine, other, item, forbidden, party = two_locations
    Reservation.objects.filter(pk__in=[item.pk, forbidden.pk, party.pk]).update(state="confirmed")
    booking_member.role = role; booking_member.save()
    client.force_login(booking_member.user)
    endpoint = path(booking_member, "studio_preparation")
    response = client.get(endpoint, query(item))
    assert len(response.context["studios"]) == 1
    assert b"Private other" not in response.content
    assert not client.get(endpoint, query(item, location=other[1].location_id)).context["studios"]
    booking_member.is_active = False; booking_member.save()
    assert client.get(endpoint).status_code == 404


def test_local_midnight_and_day_week_boundaries(stock):
    s = stock
    s.location.timezone_name = "America/Los_Angeles"; s.location.save()
    item = reserve(s); confirm(s, item)
    local = datetime(2026, 11, 1, 23, 30, tzinfo=ZoneInfo(s.location.timezone_name))
    Reservation.objects.filter(pk=item.pk).update(starts_at=local, ends_at=local + timedelta(hours=1))
    endpoint = url(s, "studio_preparation")
    params = {"date": "2026-11-01", "period": "day", "location": s.location.pk}
    assert len(s.client.get(endpoint, params).context["studios"]) == 1
    assert not s.client.get(endpoint, {**params, "date": "2026-11-02"}).context["studios"]
    assert not s.client.get(endpoint, {**params, "date": "2026-10-25", "period": "week"}).context["studios"]
    assert len(s.client.get(endpoint, {**params, "date": "2026-10-26", "period": "week"}).context["studios"]) == 1


def test_camp_middle_date_and_party_details(camp, inventory, booking_member, client):
    registration = register(camp)
    item = registration.reservation
    Reservation.objects.filter(pk=item.pk).update(state="confirmed")
    dates = list(item.occurrences.all())
    client.force_login(booking_member.user)
    endpoint = path(booking_member, "studio_preparation")
    response = client.get(endpoint, query(dates[1], period="day"))
    entries = response.context["studios"][0]["entries"]
    assert len(entries) == 1 and entries[0]["starts_at"] == dates[1].starts_at
    assert b"Camper 0" in response.content
    response = client.get(endpoint, query(item))
    assert len(response.context["studios"][0]["entries"]) == 3
    party = reserve_party(booking_member, party_request(inventory, start=item.ends_at + timedelta(days=1)))
    Reservation.objects.filter(pk=party.pk).update(state="confirmed")
    response = client.get(endpoint, query(party, period="day"))
    assert response.context["studios"][0]["entries"][0]["item"].pk == party.pk
    assert b"Participant details not provided" in response.content


def test_camp_materials_are_counted_once_across_the_week(stock):
    s = stock
    second = Session.objects.create(offering=s.offering, room=s.room,
        starts_at=s.session.starts_at + timedelta(days=1), ends_at=s.session.ends_at + timedelta(days=1),
        capacity=10, is_published=True)
    camp = Camp.objects.create(offering=s.offering, name="Two-day camp", price=10, is_open=True)
    camp.sessions.set([s.session, second])
    item = register(camp, count=1).reservation
    confirm(s, item)
    response = s.client.get(url(s, "studio_preparation"), query(item))
    studio = response.context["studios"][0]
    assert len(studio["entries"]) == 2
    assert studio["totals"][0]["quantity"] == item.stock_allocations.get().quantity == 1


def test_reservation_returns_to_same_preparation_dates(stock):
    from urllib.parse import urlencode
    s = stock
    item = reserve(s); confirm(s, item)
    back = url(s, "studio_preparation") + "?" + urlencode(query(item, location=s.location.pk))
    endpoint = url(s, "reservation_preparation", public_id=item.public_id)
    response = s.client.get(endpoint, {"prep": back})
    assert response.context["preparation_return"] == back
    assert b"Back to Studio Prep" in response.content
    assert all("prep=" in tab["url"] for tab in response.context["booking_navigation"]["section"]["tabs"])
    for unsafe in ("https://outside.invalid/", "//outside.invalid/", "/bookings/another-school/preparation/", "x" * 2049):
        response = s.client.get(endpoint, {"prep": unsafe})
        assert "preparation_return" not in response.context
        assert b"Back to Studio Prep" not in response.content
