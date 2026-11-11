"""Contracts for the shared school session layout and in-admin booking flow."""
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from core.models import AdminAuditLog
from scheduling.appointment_models import LessonBooking
from scheduling.lesson_change_forms import RescheduleForm
from scheduling.tests.test_appointments import CONTACT, booked, offer
from scheduling.tests.test_lesson_changes import change_data
from scheduling.tests.test_lessons import music, book, url
from scheduling.tests.test_integrated_demo import integrated, demo_environment

pytestmark = pytest.mark.django_db


@pytest.fixture
def school_layout(monkeypatch):
    workflows = {"optional_features": [], "reschedule_reason_required": False, "lesson_changes": "single"}
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"navigation": {"layout": "school", "simplified": True}, "workflows": workflows}}))
    return workflows


def test_lesson_and_trial_share_session_grid(music, offer, school_layout, client):
    series = book(music, {**music["data"], "start_time": "11:00"})
    booking, _, _ = booked(music, offer)
    client.force_login(music["user"])
    for route in [url(music, "lesson_series", public_id=series.public_id),
                  url(music, "lesson_booking_detail", public_id=booking.public_id)]:
        response = client.get(route)
        assert response.status_code == 200
        assert "scheduling/school/lesson_detail.html" in [t.name for t in response.templates]
        html = response.content.decode()
        assert '<div class="workshop-heading">' in html
        assert 'aria-label="Lesson sessions"' in html
        assert '<th>Date</th><th>Time</th><th>Room</th>' in html
        assert 'data-lucide="calendar-clock"' in html and 'data-lucide="calendar-x"' in html


def test_add_teacher_only_on_teacher_tab_and_add_targets_grid(music, school_layout, client):
    client.force_login(music["user"])
    route = url(music, "lesson_setup")
    assert b'Add teacher' not in client.get(route + '?view=week').content
    assert b'data-inline-target="#teachers"' in client.get(route + '?view=list').content
    types = client.get(route + '?section=services').content
    assert b'Add teacher' not in types
    assert b'data-inline-target="#lesson-types"' in types


def test_optional_note_preview_and_confirm_preserve_conflict_checks(music, school_layout, client):
    series = book(music)
    item = series.occurrences.first()
    data = change_data(item, reason="")
    client.force_login(music["user"])
    route = url(music, "lesson_change", pk=item.pk)
    preview = client.post(route, {**data, "action": "preview"})
    assert preview.status_code == 200 and preview.context["can_confirm"]
    result = client.post(route, {**data, "action": "confirm", "preview_token": preview.context["token"]})
    assert result.status_code == 302
    item.refresh_from_db()
    assert item.state == "rescheduled" and item.reason == ""
    school_layout["reschedule_reason_required"] = True
    assert not RescheduleForm(data, occurrence=item).is_valid()


def test_staff_trial_stays_in_admin_and_preserves_quote_and_retry(music, offer, school_layout, client, mailoutbox):
    offer.fee_cents = 3500
    offer.save()
    client.force_login(music["user"])
    catalogue = client.get(url(music, "staff_lesson_trials"))
    assert catalogue.status_code == 200
    assert catalogue.context["booking_base"] == "scheduling/appointments/admin_base.html"
    assert b'class="dash-header' in catalogue.content
    assert b'Local booking preview' not in catalogue.content
    route = url(music, "staff_lesson_trial_book", public_id=offer.public_id)
    page = client.get(route)
    data = {**CONTACT, "slot": page.context["slots"][0]["token"],
            "date": page.context["selection"].cleaned_data["date"].isoformat()}
    response = client.post(route, data)
    booking = LessonBooking.objects.get()
    target = url(music, "lesson_booking_detail", public_id=booking.public_id)
    assert response.url == target and client.post(route, data).url == target
    assert LessonBooking.objects.count() == 1
    assert booking.order.total_cents == 3500 and not booking.order.entries.exists()
    assert AdminAuditLog.objects.filter(actor=music["user"]).exists()
    assert not mailoutbox
    public = client.get(url(music, "lesson_trials"))
    assert not public.context.get("staff_trial")


def test_staff_trial_requires_school_permission(music, offer, school_layout, client):
    routes = [url(music, "staff_lesson_trials"), url(music, "staff_lesson_trial_book", public_id=offer.public_id)]
    for route in routes:
        assert Client().get(route).status_code == 302
    client.force_login(User.objects.create_user(username="unrelated-staff"))
    for route in routes:
        assert client.get(route).status_code in (403, 404)
        assert client.post(route, CONTACT).status_code in (403, 404, 405)
    assert not LessonBooking.objects.exists()


@pytest.mark.parametrize("simplified", [True, False])
def test_record_headers_keep_established_navigation_in_both_layouts(integrated, client, monkeypatch, simplified):
    from core.tests.factories import LeadFactory, SubmissionFactory
    profile, user = integrated
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"navigation": {"layout": "school", "simplified": simplified}}}))
    client.force_login(user)
    lead = LeadFactory(school=profile.school)
    submission = SubmissionFactory(school=profile.school)
    for route, kwargs in [("school_lead_detail", {"lead_id": lead.pk}),
                          ("school_submission_detail", {"submission_id": submission.pk})]:
        page = client.get(reverse(route, kwargs={"school_slug": profile.school.slug, **kwargs}))
        assert page.status_code == 200
        assert (b'assets/lucide.js' in page.content) is simplified
        assert b'class="record-back-link"' in page.content
        assert (b'All Leads' if route == "school_lead_detail" else b'All Submissions') in page.content
        assert b'class="dash-card record-header' in page.content
        assert b'class="school-record-header' not in page.content
