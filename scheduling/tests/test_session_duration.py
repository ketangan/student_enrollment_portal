from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scheduling.forms import WorkshopSessionForm
from scheduling.models import Session
from scheduling.setup import build_form, save_setup
from scheduling.tests.test_inventory import stock, reserve, url
from scheduling.tests.test_workshop_workspace import inline_data

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("start,duration,end", [
    ("2030-07-01T10:00", 90, "2030-07-01T18:30:00+00:00"),
    ("2030-07-01T23:30", 120, "2030-07-02T08:30:00+00:00"),
    ("2030-03-10T01:30", 120, "2030-03-10T11:30:00+00:00"),
    ("2030-11-03T00:30", 120, "2030-11-03T09:30:00+00:00"),
])
def test_duration_derives_end_in_elapsed_minutes(stock, start, duration, end):
    s = stock
    data = inline_data(s, session_id="new", starts_at=start, duration_minutes=duration,
                       ends_at="1990-01-01T00:00")
    data.pop("edit_revision")
    page = s.client.post(url(s, "workshop_detail", pk=s.offering.pk), data)
    assert page.status_code == 302
    session = s.offering.sessions.exclude(pk=s.session.pk).get()
    assert session.ends_at == datetime.fromisoformat(end)
    assert session.ends_at - session.starts_at == timedelta(minutes=duration)
    form = build_form(s.profile, "sessions", pk=session.pk)
    assert form["duration_minutes"].value() == duration
    assert "ends_at" not in form.fields


@pytest.mark.parametrize("value", ["", "0", "-1", "1.5", "abc", "9" * 30])
def test_invalid_duration_never_changes_a_session(stock, value):
    s = stock
    previous = (s.session.starts_at, s.session.ends_at)
    page = s.client.post(url(s, "workshop_detail", pk=s.offering.pk), inline_data(s, duration_minutes=value))
    assert page.status_code == 400
    assert "duration_minutes" in page.context["session_form"].errors
    s.session.refresh_from_db()
    assert (s.session.starts_at, s.session.ends_at) == previous


def test_capacity_only_edit_preserves_legacy_precision_and_reserved_snapshot(stock):
    s = stock
    s.session.starts_at = s.session.starts_at.replace(second=17, microsecond=250)
    s.session.ends_at = s.session.starts_at + timedelta(minutes=120, seconds=3)
    s.session.save()
    reserve(s, 2)
    before = (s.session.starts_at, s.session.ends_at)
    target = url(s, "workshop_detail", pk=s.offering.pk)
    invalid = s.client.post(target, inline_data(s, duration_minutes=0))
    assert invalid.status_code == 400
    assert set(invalid.context["session_form"].errors) == {"duration_minutes"}
    assert s.client.post(target, inline_data(s, capacity=90)).status_code == 302
    s.session.refresh_from_db()
    assert (s.session.starts_at, s.session.ends_at) == before
    assert s.session.capacity == 90
    blocked = s.client.post(target, inline_data(s, duration_minutes=90))
    assert blocked.status_code == 400 and b"This session has reservations" in blocked.content
    s.session.refresh_from_db()
    assert (s.session.starts_at, s.session.ends_at) == before


def test_longer_duration_checks_room_conflicts(stock):
    s = stock
    Session.objects.create(offering=s.offering, room=s.room, starts_at=s.session.ends_at,
        ends_at=s.session.ends_at + timedelta(hours=1), capacity=4, is_published=True)
    old_end = s.session.ends_at
    page = s.client.post(url(s, "workshop_detail", pk=s.offering.pk), inline_data(s, duration_minutes=180))
    assert page.status_code == 400 and b"already has a published session" in page.content
    s.session.refresh_from_db()
    assert s.session.ends_at == old_end


def test_duration_uses_elapsed_time_for_unreloaded_model_instance(stock):
    zone = ZoneInfo("America/Los_Angeles")
    instance = Session(offering=stock.offering, starts_at=datetime(2030, 3, 10, 1, 30, tzinfo=zone),
        ends_at=datetime(2030, 3, 10, 4, 30, tzinfo=zone), capacity=2)
    assert WorkshopSessionForm(instance=instance, offering=stock.offering)["duration_minutes"].value() == 120


def test_duration_editor_is_consistent_on_legacy_route_and_inline(stock):
    s = stock
    for route, params in [
        (url(s, "session_edit", pk=s.session.pk), {}),
        (url(s, "workshop_detail", pk=s.offering.pk), {"edit_session": s.session.pk}),
    ]:
        page = s.client.get(route, params)
        assert page.status_code == 200
        assert b'name="duration_minutes"' in page.content
        assert b'name="ends_at"' not in page.content
        assert b"Duration (min)" in page.content
    data = inline_data(s, duration_minutes=45)
    saved = save_setup(s.user, s.school.slug, "sessions", data, pk=s.session.pk)
    assert saved.is_valid(), saved.errors
    s.session.refresh_from_db()
    assert s.session.ends_at - s.session.starts_at == timedelta(minutes=45)
