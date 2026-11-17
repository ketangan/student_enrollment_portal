import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from scheduling.models import Offering, Session
from scheduling.message_models import BookingMessage
from scheduling.setup import save_setup
from scheduling.tests.test_inventory import stock, url
from scheduling.tests.test_session_changes import notifications, booked, HEADER
from scheduling.tests.test_workshop_schedule import daytime, second_session, data_for, path

pytestmark = pytest.mark.django_db


def workshop_data(s, mode="repeat"):
    values = data_for(s, "create")
    return {"name": "New scheduled workshop", "location": s.location.pk,
        "inventory_catalog": s.catalog.pk, "price_per_participant": "35.00", "payment_at_booking": "full",
        "schedule-mode": mode, **{f"schedule-{key}": value for key, value in values.items()}}


@pytest.mark.parametrize("mode,count", [("", 0), ("once", 1), ("repeat", 2)])
def test_create_workshop_with_optional_schedule(notifications, mode, count):
    s = notifications
    form = save_setup(s.user, s.school.slug, "workshops", workshop_data(s, mode))
    assert form.is_valid(), (form.errors, form.schedule_form.errors)
    sessions = list(form.instance.sessions.all())
    assert len(sessions) == count
    assert all(row.is_published for row in sessions)
    if mode == "repeat":
        assert sessions[0].series_id and sessions[0].series_id == sessions[1].series_id
    elif mode == "once":
        assert sessions[0].series_id is None


@pytest.mark.parametrize("change", ["conflict", "missing", "room", "dates"])
def test_invalid_schedule_never_leaves_a_workshop(notifications, change):
    s = notifications
    data = workshop_data(s)
    if change == "conflict":
        data["schedule-start_time"] = timezone.localtime(s.session.starts_at).strftime("%H:%M")
    elif change == "missing":
        data["schedule-capacity"] = ""
    elif change == "dates":
        data["schedule-last_date"] = timezone.localdate() - timedelta(days=1)
    else:
        data["schedule-room"] = 999999
    form = save_setup(s.user, s.school.slug, "workshops", data)
    assert not form.is_valid()
    assert not Offering.objects.filter(name=data["name"]).exists()


def test_following_scope_only_changes_linked_sessions_and_preserves_bookings(notifications):
    s = notifications
    first = s.session
    second = second_session(s)
    key = uuid.uuid4()
    Session.objects.filter(pk__in=[first.pk, second.pk]).update(series_id=key)
    unrelated = second_session(s)
    s.session = second
    reservation = booked(s)
    route = path(s, "edit") + f"?session={second.pk}"
    response = s.client.get(route, **HEADER)
    assert set(response.context["form"].fields) == {"start_time", "preview_token", "scope", "duration_minutes", "capacity", "notify_customers"}
    data = {**data_for(s), "start_time": "14:00", "scope": "following", "notify_customers": "1"}
    response = s.client.post(route, data, HTTP_X_SESSION_PREFLIGHT="1")
    assert response.status_code == 200, response.content.decode()
    data["preview_token"] = response.json()["review_token"]
    assert s.client.post(route, data, **HEADER).json() == {"saved": True}
    second.refresh_from_db()
    first.refresh_from_db()
    unrelated.refresh_from_db()
    reservation.refresh_from_db()
    assert first.starts_at == unrelated.starts_at - timedelta(days=7)
    assert second.starts_at != unrelated.starts_at
    assert reservation.starts_at == second.starts_at
    assert BookingMessage.objects.filter(notice_kind="rescheduled").count() == 1


def test_legacy_sessions_do_not_offer_series_scope(notifications):
    s = notifications
    page = s.client.get(url(s, "session_edit", pk=s.session.pk), **HEADER)
    assert b"This and following sessions" not in page.content


def test_schedule_is_not_added_to_disabled_workflows(notifications):
    s = notifications
    s.profile.native_reservations_enabled = False
    s.profile.save()
    page = s.client.get(url(s, "workshop_new"))
    assert b'data-workshop-schedule' not in page.content


def test_series_cancellation_is_reviewed_and_retry_safe(notifications):
    s = notifications
    second = second_session(s)
    Session.objects.filter(pk__in=[s.session.pk, second.pk]).update(series_id=uuid.uuid4())
    booked(s)
    route = path(s, "cancel") + f"?session={s.session.pk}"
    data = {"resolution": "without_refund"}
    response = s.client.post(route, data, **HEADER)
    assert response.status_code == 200
    assert len(response.context["rows"]) == 2
    data["preview_token"] = response.context["form"]["preview_token"].value()
    for _ in range(2):
        assert s.client.post(route, data, **HEADER).json() == {"saved": True}
    assert Session.objects.filter(pk__in=[s.session.pk, second.pk], canceled_at__isnull=False).count() == 2


def test_changed_series_membership_invalidates_review(notifications):
    s = notifications
    s.session.series_id = uuid.uuid4()
    s.session.save()
    route = path(s, "edit") + f"?session={s.session.pk}"
    original_start = s.session.starts_at
    data = {**data_for(s), "start_time": "14:00", "scope": "following", "notify_customers": "1"}
    response = s.client.post(route, data, HTTP_X_SESSION_PREFLIGHT="1")
    assert response.status_code == 200
    data["preview_token"] = response.json()["review_token"]
    s.session.series_id = uuid.uuid4()
    s.session.save()
    rejected = s.client.post(route, data, **HEADER)
    assert rejected.status_code == 200
    assert rejected.context["conflict_count"] == 1
    assert not rejected.context["form"]["preview_token"].value()
    assert b'class="primary" disabled' in rejected.content
    s.session.refresh_from_db()
    assert s.session.starts_at == original_start
    assert not BookingMessage.objects.exists()


@pytest.mark.parametrize("seed", ["999999", "bad", "legacy"])
def test_invalid_series_never_falls_back_to_broad_matching(notifications, seed):
    s = notifications
    route = path(s, "edit") + f"?session={s.session.pk if seed == 'legacy' else seed}"
    assert s.client.get(route, **HEADER).status_code == 404


def test_recurring_interval_is_not_limited_to_two_weeks(notifications):
    s = notifications
    data = workshop_data(s)
    data["schedule-interval"] = 3
    data["schedule-last_date"] = data["schedule-first_date"] + timedelta(days=21)
    form = save_setup(s.user, s.school.slug, "workshops", data)
    assert form.is_valid(), form.errors
    one, two = form.instance.sessions.all()
    assert two.starts_at - one.starts_at == timedelta(days=21)


@pytest.mark.parametrize("field", ["name", "location"])
def test_invalid_workshop_keeps_schedule_without_error_page(notifications, field):
    s = notifications
    data = workshop_data(s)
    data[field] = ""
    response = s.client.post(url(s, "workshop_new"), data)
    assert response.status_code == 200
    assert response.context["form"].errors
    assert b"Repeating sessions" in response.content
    assert not Offering.objects.filter(name="New scheduled workshop").exists()
