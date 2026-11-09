from datetime import date, datetime, timedelta, timezone as utc
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from scheduling import school_workspace as workspace
from scheduling.models import Location, Session
from scheduling.tests.test_lessons import music, book
from scheduling.tests.test_school_workspace import school_workspace, session, camp, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def calendar(school_workspace):
    s = school_workspace
    s.profile.calendar_enabled = True
    s.profile.save(update_fields=["calendar_enabled"])
    return s


def page(s, **query):
    return s.client.get(url(s, "school_schedule"), {"view": "calendar", **query})


def test_calendar_uses_existing_lessons_classes_and_programs(calendar):
    s = calendar
    book(vars(s))
    session(s)
    camp(s)
    response = page(s, date="2030-01-01", range="month")
    assert response.status_code == 200
    data = response.context["schedule_calendar_config"]
    assert {item["extendedProps"]["kind"] for item in data["events"]} == {"Lesson", "Class", "Program session"}
    assert len({item["id"] for item in data["events"]}) == len(data["events"])
    for item in data["events"]:
        assert s.client.get(item["extendedProps"]["url"]).status_code == 200
        assert parse_qs(urlsplit(item["extendedProps"]["url"]).query)["schedule"][0].startswith(url(s, "school_schedule"))
    assert b'vendor/event-calendar/event-calendar.min.js' in response.content
    assert "no-store" in response.headers["Cache-Control"]


def test_calendar_and_list_preserve_filters_and_show_same_week(calendar):
    s = calendar
    book(vars(s))
    session(s)
    response = page(s, date="2030-01-07", range="week", teacher=s.teacher.pk, page="9")
    events = response.context["schedule_calendar_config"]["events"]
    assert events and all(item["extendedProps"]["kind"] == "Lesson" for item in events)
    target = response.context["schedule_list_url"]
    query = parse_qs(urlsplit(target).query)
    assert query["teacher"] == [str(s.teacher.pk)] and query["date"] == ["2030-01-07"]
    assert "page" not in query
    listed = s.client.get(url(s, "school_schedule") + target)
    assert {row["id"] for row in listed.context["agenda"]} == {event["id"] for event in events}
    assert listed.context["schedule_range"] == "week"


def test_list_toggle_does_not_change_upcoming_scope(calendar):
    target = url(calendar, "school_schedule")
    response = calendar.client.get(target)
    assert response.context["schedule_list_url"] == target
    assert response.context["schedule_range"] == ""


def test_reset_filters_preserves_calendar_view(calendar):
    response = page(calendar, teacher=calendar.teacher.pk)
    target = url(calendar, "school_schedule") + '?location=&amp;view=calendar'
    assert f'href="{target}" aria-label="Reset filters"'.encode() in response.content


@pytest.mark.parametrize("range_name", ["day", "week", "month"])
def test_calendar_navigation_stays_in_supported_bounds(calendar, range_name):
    s = calendar
    response = page(s, date="2030-01-01", range=range_name)
    for target in ("schedule_previous", "schedule_next", "schedule_today"):
        assert s.client.get(url(s, "school_schedule") + response.context[target]).status_code == 200
    for day in ("1901-01-01", "2199-12-31"):
        response = page(s, date=day, range=range_name)
        assert response.status_code == 200
    assert response.context["schedule_next"] is None


@pytest.mark.parametrize("query", [{"date": "invalid"}, {"date": "9999-12-31"},
    {"date": "1900-01-01"}, {"range": "year"}, {"teacher": "999999"}, {"location": "999999"}])
def test_invalid_calendar_filters_do_not_leak_events(calendar, query):
    session(calendar, name="Private class")
    response = page(calendar, **query)
    assert response.status_code == 400
    assert "schedule_calendar_config" not in response.context
    assert b'Private class' not in response.content


def test_calendar_is_opt_in_and_feature_scoped(calendar):
    s = calendar
    s.profile.calendar_enabled = False
    s.profile.save(update_fields=["calendar_enabled"])
    assert page(s).context["schedule_view"] == "list"
    s.profile.calendar_enabled = True
    s.profile.save(update_fields=["calendar_enabled"])
    s.policy["features"] = []
    assert not page(s).context["calendar_available"]
    s.policy["features"] = ["lessons"]
    book(vars(s))
    session(s)
    response = page(s, date="2030-01-07")
    assert {item["extendedProps"]["kind"] for item in response.context["schedule_calendar_config"]["events"]} == {"Lesson"}


def test_calendar_includes_past_and_overnight_events_in_location_time(calendar):
    s = calendar
    s.location.timezone_name = "America/Los_Angeles"
    s.location.save(update_fields=["timezone_name"])
    item = session(s, start=datetime(2029, 11, 4, 6, 30, tzinfo=utc.utc))
    Session.objects.filter(pk=item.pk).update(ends_at=item.starts_at + timedelta(hours=5))
    response = page(s, date="2029-11-04", range="day")
    events = response.context["schedule_calendar_config"]["events"]
    assert len(events) == 1
    assert events[0]["start"] == "2029-11-03T23:30:00"
    assert events[0]["end"] == "2029-11-04T03:30:00"
    assert s.client.get(url(s, "school_schedule"), {"date": "2029-11-03"}).context["agenda"]


def test_calendar_rejects_overlarge_result_instead_of_silent_truncation(calendar, monkeypatch):
    session(calendar)
    session(calendar)
    monkeypatch.setattr(workspace, "MAX_EVENTS", 1)
    response = page(calendar)
    assert response.status_code == 400
    assert b'Too many events' in response.content
    assert "schedule_calendar_config" not in response.context


def test_calendar_queries_do_not_scale_with_rows(calendar):
    s = calendar
    item = session(s)
    with CaptureQueriesContext(connection) as queries:
        assert page(s).status_code == 200
    first = len(queries)
    Session.objects.bulk_create([Session(offering=item.offering, starts_at=item.starts_at + timedelta(minutes=i),
        ends_at=item.ends_at + timedelta(minutes=i), capacity=10) for i in range(10)])
    with CaptureQueriesContext(connection) as queries:
        response = page(s)
    assert len(response.context["schedule_calendar_config"]["events"]) == 11
    assert len(queries) <= first + 1


def test_calendar_location_scope_and_disabled_school(calendar):
    s = calendar
    visible = session(s)
    other = Location.objects.create(profile=s.profile, name="Restricted")
    session(s, name="Hidden", location=other)
    s.member.all_locations = False
    s.member.save(update_fields=["all_locations"])
    s.member.locations.add(s.location)
    response = page(s)
    assert [item["id"] for item in response.context["schedule_calendar_config"]["events"]] == [f"session-{visible.pk}"]
    assert page(s, location=other.pk).status_code == 400
    s.policy["simplified"] = False
    assert page(s).status_code == 404


def test_month_window_covers_all_six_calendar_rows():
    assert workspace._schedule_window(date(2030, 9, 1), "month") == (date(2030, 8, 26), date(2030, 10, 7))


@pytest.mark.parametrize("enabled", [True, False])
def test_school_calendar_settings_support_lesson_only_schools(calendar, enabled):
    from scheduling.calendar_settings import CalendarSettingsForm
    s = calendar
    s.policy["features"] = ["lessons"]
    s.profile.capabilities = ["lessons"]
    s.profile.lessons_enabled = enabled
    form = CalendarSettingsForm({"calendar_enabled": "on"}, instance=s.profile)
    assert "default_dashboard_view" not in form.fields
    assert form.is_valid() is enabled


@pytest.mark.parametrize("target", ["https://evil.example/bookings/music/school/schedule/", "//evil.example/",
    "/bookings/another-school/school/schedule/", "http://[invalid", "x" * 2049])
def test_schedule_back_link_rejects_external_or_other_school(calendar, target):
    item = session(calendar)
    response = calendar.client.get(url(calendar, "workshop_detail", pk=item.offering_id), {"schedule": target})
    assert response.status_code == 200
    assert not response.context["school_schedule_return"]


def test_back_link_returns_to_same_calendar_and_filters(calendar):
    s = calendar
    item = session(s)
    target = url(s, "school_schedule") + "?view=calendar&range=month&date=2030-01-01"
    response = s.client.get(url(s, "workshop_detail", pk=item.offering_id), {"schedule": target})
    assert response.context["school_schedule_return"] == target
    assert target.replace("&", "&amp;").encode() in response.content
