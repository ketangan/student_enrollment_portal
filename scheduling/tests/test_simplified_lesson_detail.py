from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from scheduling import lesson_changes, lessons
from scheduling.lesson_change_forms import RescheduleForm
from scheduling.lesson_forms import CancelLessonForm
from scheduling.tests.test_appointments import booked, offer
from scheduling.tests.test_lesson_changes import change_data, move
from scheduling.tests.test_lessons import book, music, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def simplified(monkeypatch):
    policy = {"layout": "school", "simplified": True, "features": ["lessons"], "optional_features": []}
    monkeypatch.setattr("scheduling.school_navigation.load_school_config",
        lambda slug: SimpleNamespace(raw={"scheduling": {"navigation": policy}}))
    return policy


def test_trial_series_opens_coherent_detail_without_loop(music, offer, simplified, client):
    booking, _, _ = booked(music, offer)
    item = booking.series.occurrences.get()
    client.force_login(music["user"])
    response = client.get(url(music, "lesson_series", public_id=booking.series.public_id), follow=True)
    target = url(music, "lesson_booking_detail", public_id=booking.public_id)
    assert response.redirect_chain == [(target, 302)]
    assert response.status_code == 200
    html = response.content.decode()
    assert "Booking history" in html and "America/Los_Angeles" in html
    assert booking.series.snapshot["teacher"] in html
    assert url(music, "lesson_change", pk=item.pk) in html
    assert url(music, "lesson_cancel", pk=item.pk) in html
    assert "Schedule, attendance &amp; changes" not in html
    assert "Make-up credit history" not in html


def test_rescheduled_trial_old_and_current_urls_reach_current_booking(music, offer, simplified, client):
    booking, _, _ = booked(music, offer)
    replacement = move(music, booking.series.occurrences.get())
    client.force_login(music["user"])
    for series in [booking.series, replacement]:
        response = client.get(url(music, "lesson_series", public_id=series.public_id), follow=True)
        assert response.status_code == 200 and len(response.redirect_chain) == 1
        assert response.context["item"].series_id == replacement.pk
        assert response.context["booking"].pk == booking.pk


@pytest.mark.parametrize("scope", ["future", "invalid"])
def test_simplified_scope_rejected_in_forms_views_and_writers(music, offer, simplified, client, scope):
    booking, _, _ = booked(music, offer)
    item = booking.series.occurrences.get()
    data = change_data(item, scope=scope)
    assert not RescheduleForm(data, occurrence=item).is_valid()
    cancel = {"scope": scope, "origin": "family", "reason": "Requested"}
    assert not CancelLessonForm(cancel, occurrence=item).is_valid()
    client.force_login(music["user"])
    assert client.post(url(music, "lesson_change", pk=item.pk), data).status_code == 400
    assert client.post(url(music, "lesson_cancel", pk=item.pk), cancel).status_code == 400
    with pytest.raises(ValidationError, match="selected lesson"):
        lesson_changes.reschedule(music["user"], music["slug"], item.pk, data, "")
    with pytest.raises(ValidationError, match="selected lesson"):
        lessons.cancel_lessons(music["user"], music["slug"], item.pk, scope, "Requested")
    item.refresh_from_db()
    assert item.state == "scheduled"


def test_cancel_single_trial_returns_to_detail_and_hides_new_credit_controls(music, offer, simplified, client):
    booking, _, _ = booked(music, offer)
    item = booking.series.occurrences.get()
    client.force_login(music["user"])
    target = url(music, "lesson_cancel", pk=item.pk)
    response = client.get(target)
    assert response.context["form"].fields["scope"].widget.is_hidden
    assert response.context["form"].fields["origin"].widget.is_hidden
    assert b"Back to booking" in response.content
    data = {"scope": "one", "origin": "family", "reason": "Requested", "action": "preview"}
    preview = client.post(target, data)
    assert preview.status_code == 200 and b"Make-up outcome" not in preview.content
    response = client.post(target, {**data, "action": "confirm", "preview_token": preview.context["token"]}, follow=True)
    assert response.status_code == 200 and len(response.redirect_chain) == 2
    assert response.context["item"].state == "canceled"
    assert b"Cancel booking</a>" not in response.content


def test_historical_credit_eligibility_retains_cancellation_controls(music, offer, simplified, client):
    offer.makeup_eligible = True
    offer.save()
    booking, _, _ = booked(music, offer)
    form = CancelLessonForm(occurrence=booking.series.occurrences.get())
    assert form.fields["scope"].widget.is_hidden
    assert not form.fields["origin"].widget.is_hidden


def test_unlinked_series_still_readable_and_default_workflow_unchanged(music, offer, client):
    series = book(music)
    form = RescheduleForm(occurrence=series.occurrences.first())
    assert ("future", "This and later scheduled lessons") in form.fields["scope"].choices
    client.force_login(music["user"])
    assert client.get(url(music, "lesson_series", public_id=series.public_id)).status_code == 200
