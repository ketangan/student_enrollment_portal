from types import SimpleNamespace

import pytest
from django.http import Http404

from scheduling import lesson_changes, lessons
from scheduling.family_access import family_grant, issue_invitation, redeem_invitation
from scheduling.tests.test_appointments import booked, offer
from scheduling.tests.test_lessons import book, music, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def minimal(monkeypatch):
    workflow = {"optional_features": [], "lesson_changes": "single"}
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(
        raw={"scheduling": {"navigation": {"layout": "school", "simplified": True}, "workflows": workflow}}))
    return workflow


def test_series_keeps_inline_actions_without_family_attendance_or_test_billing(music, client, monkeypatch):
    series = book(music)
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(
        raw={"scheduling": {"navigation": {"layout": "school", "simplified": True},
                            "workflows": {"optional_features": []}}}))
    client.force_login(music["user"])
    response = client.get(url(music, "lesson_series", public_id=series.public_id))
    html = response.content.decode()
    assert response.status_code == 200
    assert '<table class="dash-table">' in html
    assert "data-inline-edit" in html and "calendar-clock" in html and "calendar-x" in html
    assert all(text not in html for text in ["Attendance", "<dt>Family", "<dt>Billing", "Test quote"])


def test_trial_hides_application_link_and_uses_inline_existing_endpoints(music, offer, minimal, client):
    booking, _, _ = booked(music, offer)
    client.force_login(music["user"])
    response = client.get(url(music, "lesson_booking_detail", public_id=booking.public_id))
    assert response.status_code == 200
    assert b"Connect application" not in response.content
    assert b"Saved, not sent" not in response.content
    assert b'data-inline-edit' in response.content
    item = booking.series.occurrences.get()
    for route in ["lesson_change", "lesson_cancel"]:
        result = client.get(url(music, route, pk=item.pk))
        assert result.status_code == 200 and b"data-inline-editor" in result.content
    for method in [client.get, client.post]:
        assert method(url(music, "lesson_attendance", pk=item.pk)).status_code == 404
    with pytest.raises(Http404):
        lesson_changes.record_attendance(music["user"], music["slug"], item.pk, {})
    item.refresh_from_db()
    assert item.attendance == "unmarked"


def test_family_management_routes_and_writers_closed_without_blocking_trials(music, minimal, client):
    client.force_login(music["user"])
    for route in ["family_access", "family_entry", "family_schedule"]:
        assert client.get(url(music, route)).status_code == 404
    assert client.get(url(music, "lesson_setup") + "?section=students").status_code == 404
    for kind in ["students", "households"]:
        target = url(music, "lesson_setup_new", kind=kind)
        assert client.get(target).status_code == 404
        assert client.post(target, {}).status_code == 404
        with pytest.raises(Http404):
            lessons.save_lesson_setup(music["user"], music["slug"], kind, {})
    with pytest.raises(Http404):
        issue_invitation(music["user"], music["slug"], 0, {})
    with pytest.raises(Http404):
        redeem_invitation(music["slug"], "", {})
    with pytest.raises(Http404):
        family_grant(music["slug"], {})
    from scheduling.family_access import public_profile
    assert public_profile(music["slug"]).pk == music["profile"].pk


def test_optional_controls_can_be_reenabled_without_changing_layout(music, minimal, client):
    minimal["optional_features"] = ["family_management", "attendance"]
    client.force_login(music["user"])
    assert client.get(url(music, "family_access")).status_code == 200
    assert client.get(url(music, "lesson_setup") + "?section=students").status_code == 200
    assert client.get(url(music, "lesson_setup_new", kind="households")).status_code == 200


@pytest.mark.parametrize("query", ["?view=list", "?section=services"])
def test_teacher_grids_reuse_inline_editors(music, minimal, client, query):
    client.force_login(music["user"])
    response = client.get(url(music, "lesson_setup") + query)
    assert response.status_code == 200 and b"data-inline-edit" in response.content
    for kind in ["teachers", "services", "availability", "closures"]:
        response = client.get(url(music, "lesson_setup_new", kind=kind))
        assert response.status_code == 200 and b"data-inline-editor" in response.content
