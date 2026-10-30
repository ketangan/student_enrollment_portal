from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlsplit
from html import unescape
import re

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.models import School
from scheduling.location_filters import LocationFilterForm
from scheduling.models import Location, Offering, Room, SchedulingProfile, Session
from scheduling.workspace import overview
from scheduling.tests.test_reservations import inventory, workshop_request, party_request, reserve, settle
from scheduling.tests.test_camps import camp, register
from scheduling.tests.test_lessons import music, book as book_lesson
from scheduling.tests.test_visits import tour, book as book_tour

pytestmark = pytest.mark.django_db


def url(profile, name="workspace"):
    return reverse("scheduling:" + name, kwargs={"school_slug": profile.school.slug})


@pytest.fixture
def second_studio(inventory):
    profile, _, _, first = inventory
    place = Location.objects.create(profile=profile, name="Second studio", timezone_name="America/New_York")
    room = Room.objects.create(location=place, name="Other room", capacity=6)
    offering = Offering.objects.create(location=place, name="Second workshop", is_published=True)
    session = Session.objects.create(offering=offering, room=room, starts_at=first.starts_at,
        ends_at=first.ends_at, capacity=4, is_published=True)
    return profile, offering, room, session


def counts(data):
    return {item["label"]: item["count"] for item in data["attention_items"]}


def test_dashboard_session_counts_and_labels(client, inventory, second_studio, booking_member):
    from scheduling.models import Reservation

    profile = inventory[0]
    session = inventory[3]
    session.capacity = 20
    session.save()
    for state in ("confirmed", "confirmed", "held", "review", "canceled", "completed"):
        reservation = reserve(booking_member, workshop_request(inventory))
        Reservation.objects.filter(pk=reservation.pk).update(state=state)
    # Two bookings with two participants each must count as two, not four.
    data = overview(profile)
    assert {item["label"]: item["reservation_count"] for item in data["agenda"]} == {"Workshop": 2, "Second workshop": 0}
    assert overview(profile, location=second_studio[1].location)["agenda"][0]["reservation_count"] == 0
    client.force_login(booking_member.user)
    page = client.get(url(profile), {"location": inventory[1].location_id})
    assert page.status_code == 200
    assert b"Upcoming Sessions" in page.content
    assert b"View pending &rarr;" in page.content
    assert b"View customer schedule" not in page.content
    assert b"View bookings" not in page.content
    assert b"<strong>2</strong> confirmed reservations" in page.content
    reservation = Reservation.objects.filter(session=session, state="confirmed").first()
    Reservation.objects.filter(pk=reservation.pk).update(state="canceled")
    assert b"<strong>1</strong> confirmed reservation</p>" in client.get(url(profile)).content
    empty = client.get(url(profile), {"location": second_studio[1].location_id})
    assert b"<strong>0</strong> confirmed reservations" in empty.content


def test_overview_filters_before_event_limit_and_keeps_setup_global(inventory, second_studio, django_assert_max_num_queries):
    profile, offering, _, first = inventory
    place = second_studio[1].location
    for offset in range(23):
        Session.objects.create(offering=offering, starts_at=timezone.now()+timedelta(hours=offset),
            ends_at=timezone.now()+timedelta(hours=offset+1), capacity=2, is_published=True)
    # Includes up to three bounded queries for persisted booking activity.
    with django_assert_max_num_queries(15):
        data = overview(profile, location=place)
    assert len(data["agenda"]) == 1 and not data["agenda_truncated"]
    assert data["agenda"][0]["location"] == place.name
    assert data["agenda"][0]["zone"] == "America/New_York"
    assert data["setup_steps"] == overview(profile)["setup_steps"]
    empty = Location.objects.create(profile=profile, name="Empty studio")
    assert overview(profile, location=empty)["agenda"] == []


def test_all_attention_counts_and_drilldowns_match_selected_location(client, inventory, second_studio, booking_member):
    for fixture in (inventory, second_studio):
        workshop_request(fixture)
        pending_party = party_request(fixture)
        pending_party.status = "pending"
        pending_party.save()
        payment = reserve(booking_member, workshop_request(fixture))
        payment.state = "review"
        payment.save()
        confirmed = reserve(booking_member, party_request(fixture))
        settle(booking_member, confirmed, 10000)
    profile = inventory[0]
    place = second_studio[1].location
    data = overview(profile, location=place)
    assert list(counts(data).values()) == [1, 1, 1, 0]
    assert list(counts(overview(profile)).values()) == [2, 2, 2, 0]
    assert [item["kind"] for item in data["agenda"]] == ["Published session", "Confirmed party"]
    assert {item["location"] for item in data["agenda"]} == {place.name}
    client.force_login(booking_member.user)
    for attention in data["attention_items"][:3]:
        assert parse_qs(urlsplit(attention["url"]).query)["location"] == [str(place.pk)]
        page = client.get(attention["url"])
        assert page.status_code == 200
        assert page.context["items"].paginator.count == attention["count"]
        assert len(page.context["items"]) == 1
        if page.context.get("location_filter"):
            assert page.context["location_filter"].selected == place
            assert f"location={place.pk}".encode() in page.content


def test_camp_payment_attention_is_scoped(camp, inventory, second_studio):
    reservation = register(camp).reservation
    reservation.state = "review"
    reservation.save()
    label = "Reservations needing payment review"
    assert counts(overview(inventory[0], location=inventory[1].location))[label] == 1
    assert counts(overview(inventory[0], location=second_studio[1].location))[label] == 0


def test_lessons_are_filtered_without_mutating_series(music):
    series = book_lesson(music)
    place = Location.objects.create(profile=music["profile"], name="Other studio")
    assert len(overview(music["profile"], location=music["location"])["agenda"]) == 1
    assert not overview(music["profile"], location=place)["agenda"]
    assert series.occurrences.filter(state="scheduled").count() == 3


def test_tour_times_and_attendance_are_scoped_in_dashboard_and_destination(client, tour):
    profile = tour.host.profile
    location = tour.program.offering.location
    other = Location.objects.create(profile=profile, name="Other preschool")
    client.force_login(tour.host.user)
    assert len(overview(profile, location=location)["agenda"]) == 1
    assert not overview(profile, location=other)["agenda"]
    assert len(client.get(url(profile, "visits"), {"location": location.pk}).context["slots"]) == 1
    empty = client.get(url(profile, "visits"), {"location": other.pk})
    assert not empty.context["slots"] and not empty.context["programs"]
    book_tour(tour)
    Session.objects.filter(pk=tour.session_id).update(starts_at=timezone.now()-timedelta(hours=2), ends_at=timezone.now()-timedelta(hours=1))
    attention = overview(profile, location=location)["attention_items"][0]
    assert attention["count"] == 1
    assert counts(overview(profile, location=other))["Tour attendance to record"] == 0
    page = client.get(attention["url"])
    assert page.context["bookings"].paginator.count == 1
    assert client.get(url(profile, "visits"), {"location": other.pk}).context["bookings"].paginator.count == 0


@pytest.mark.parametrize("name", ["workspace", "requests", "parties", "visits", "workshops"])
@pytest.mark.parametrize("value", ["foreign", "bad", "-1", "99999999999999999999999999999"])
def test_invalid_or_foreign_location_never_falls_back_to_all_data(client, inventory, booking_member, name, value):
    profile = inventory[0]
    foreign = SchedulingProfile.objects.create(school=School.objects.create(slug="unrelated"))
    place = Location.objects.create(profile=foreign, name="Private location")
    client.force_login(booking_member.user)
    page = client.get(url(profile, name), {"location": place.pk if value == "foreign" else value})
    assert page.status_code == 400
    assert b"Private location" not in page.content
    if name == "workspace":
        assert not page.context.get("agenda") and not page.context.get("attention_items")
    elif name == "visits":
        assert not page.context["slots"] and not page.context["bookings"]
    elif name == "workshops":
        assert not page.context["offerings"]
    else:
        assert not page.context["items"]
    with pytest.raises(ValidationError):
        overview(profile, location=place)


@pytest.mark.parametrize("role", ["owner", "scheduler", "viewer"])
def test_dashboard_picker_works_for_all_staff_roles_without_writes(client, inventory, second_studio, booking_member, role):
    profile = inventory[0]
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    page = client.get(url(profile), {"location": second_studio[1].location_id})
    assert page.status_code == 200
    assert page.context["selected_location"] == second_studio[1].location
    assert b'id="dashboard-location-filter"' in page.content
    assert b"Second workshop" in page.content
    assert b"All locations" in page.content
    assert (b"Setup checklist" in page.content) == (role == "owner")
    all_page = client.get(url(profile), {"location": ""})
    assert len(all_page.context["agenda"]) == 2
    assert client.post(url(profile), {"location": ""}).status_code == 405


def test_filter_form_is_tenant_scoped_and_empty_selection_means_all(inventory, second_studio):
    profile = inventory[0]
    form = LocationFilterForm({}, profile=profile)
    assert form.is_valid() and form.selected is None
    assert form.filter(profile.locations.all(), "pk").count() == 2
    assert list(form.fields["location"].queryset) == list(profile.locations.order_by("name", "pk"))


def test_workshop_drilldown_filters_offerings(client, inventory, second_studio, booking_member):
    client.force_login(booking_member.user)
    profile = inventory[0]
    place = second_studio[1].location
    page = client.get(url(profile, "workshops"), {"location": place.pk})
    assert list(page.context["offerings"]) == [second_studio[1]]
    dashboard = client.get(url(profile), {"location": place.pk})
    assert f'{url(profile, "workshops")}?location={place.pk}'.encode() in dashboard.content


@pytest.mark.parametrize("name,create", [("requests", workshop_request), ("parties", party_request)])
def test_location_survives_review_tabs_and_pagination(client, inventory, second_studio, booking_member, name, create):
    for _ in range(51):
        item = create(inventory)
        item.status = "pending"
        item.save(update_fields=["status"])
    create(second_studio)
    profile = inventory[0]
    place = inventory[1].location
    client.force_login(booking_member.user)
    first = client.get(url(profile, name), {"review": "1", "location": place.pk})
    assert first.context["items"].paginator.count == 51
    links = re.findall(r'href="([^"]+)"', first.content.decode())
    expected = {"location": [str(place.pk)], "page": ["2"], "review": ["1"]}
    assert any(all(parse_qs(urlsplit(unescape(link)).query).get(key) == value
                   for key, value in expected.items()) for link in links)
    second = client.get(url(profile, name), {"review": "1", "location": place.pk, "page": "2"})
    assert len(second.context["items"]) == 1
    assert second.context["selected_location"] == place
    assert second.context["review_only"]


@pytest.mark.parametrize("hour,duration,expected", [(16, 2, "4:00 PM &ndash; 6:00 PM"), (23, 2, "11:00 PM &ndash; Oct 11, 1:00 AM")])
def test_agenda_end_date_uses_location_day_not_utc_day(client, inventory, booking_member, monkeypatch, hour, duration, expected):
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 10, 9, tzinfo=dt_timezone.utc))
    session = inventory[3]
    session.starts_at = datetime(2026, 10, 10, hour, tzinfo=ZoneInfo("America/Los_Angeles"))
    session.ends_at = session.starts_at + timedelta(hours=duration)
    session.save()
    client.force_login(booking_member.user)
    assert expected.encode() in client.get(url(inventory[0])).content
