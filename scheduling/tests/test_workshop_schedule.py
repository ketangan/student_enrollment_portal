from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest
from django.db import close_old_connections
from django.http import QueryDict
from django.test import Client
from django.utils import timezone

from core.models import AdminAuditLog
from core.payments.service import totals
from scheduling import workshop_schedule
from scheduling.message_models import BookingMessage
from scheduling.models import Session, SchedulingMembership
from scheduling.tests.test_inventory import stock, url
from scheduling.tests.test_session_changes import notifications, booked, HEADER, cancel_data, review, provider_paid

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def daytime(stock):
    # Use a deliberate wall time, not the test runner's current hour near midnight.
    local = timezone.localtime(stock.session.starts_at, ZoneInfo(stock.location.timezone_name)).replace(
        hour=10, minute=0, second=0, microsecond=0)
    stock.session.starts_at, stock.session.ends_at = local, local + timedelta(hours=1)
    stock.session.save()
    stock.start = local


def path(s, action):
    return url(s, "workshop_schedule", offering_pk=s.offering.pk, action=action)


def data_for(s, action="edit"):
    local = timezone.localtime(s.session.starts_at, ZoneInfo(s.location.timezone_name))
    return {"first_date": local.date(), "last_date": local.date() + timedelta(days=7), "weekday": local.weekday(),
        "current_time": local.strftime("%H:%M"), "start_time": (local + timedelta(hours=2)).strftime("%H:%M"),
        "interval": 1, "duration_minutes": 60, "room": s.room.pk, "capacity": 100,
        "resolution": "without_refund", "preview_token": ""}


def second_session(s):
    return Session.objects.create(offering=s.offering, room=s.room, capacity=100, is_published=True,
        starts_at=s.session.starts_at + timedelta(days=7), ends_at=s.session.ends_at + timedelta(days=7))


def preview(s, action, data):
    response = s.client.post(path(s, action), data, **HEADER)
    assert response.status_code == 200, response.content.decode()
    assert response.context["rows"]
    return {**data, "preview_token": response.context["form"]["preview_token"].value()}


def test_bulk_move_reviews_and_updates_bookings_once_without_repricing(notifications):
    s = notifications
    one = booked(s, 2)
    first = s.session
    s.session = second_session(s)
    two = booked(s)
    s.session = first
    money = [totals(i.order) for i in [one, two]]
    accepted = preview(s, "edit", data_for(s))
    one.refresh_from_db()
    assert one.starts_at == first.starts_at and not BookingMessage.objects.exists()
    for _ in range(2):
        assert s.client.post(path(s, "edit"), accepted, **HEADER).json() == {"saved": True}
    for item, before in zip([one, two], money):
        old_start = item.starts_at
        item.refresh_from_db()
        assert item.starts_at == old_start.replace(second=0, microsecond=0) + timedelta(hours=2)
        assert totals(item.order) == before
    assert BookingMessage.objects.filter(notice_kind="rescheduled").count() == 2
    assert AdminAuditLog.objects.filter(extra__schedule_batch__isnull=False).count() == 1


@pytest.mark.parametrize("change", ["new_session", "new_booking", "capacity", "fields"])
def test_stale_bulk_review_never_partially_updates(notifications, change):
    s = notifications
    item = booked(s)
    second = second_session(s)
    accepted = preview(s, "edit", data_for(s))
    if change == "new_session":
        second.delete()
    elif change == "new_booking":
        s.session = second
        booked(s)
    elif change == "capacity":
        second.capacity = 90
        second.save()
    else:
        accepted["start_time"] = "21:00"
    response = s.client.post(path(s, "edit"), accepted, **HEADER)
    assert response.status_code == 400
    item.refresh_from_db()
    assert item.starts_at == item.workshop_request.session.starts_at
    assert not BookingMessage.objects.exists()


def test_conflict_in_later_session_rolls_back_whole_batch(notifications):
    s = notifications
    booked(s)
    second = second_session(s)
    accepted = preview(s, "edit", data_for(s))
    Session.objects.create(offering=s.offering, room=s.room, capacity=10, is_published=True,
        starts_at=second.starts_at + timedelta(hours=2), ends_at=second.ends_at + timedelta(hours=2))
    assert s.client.post(path(s, "edit"), accepted, **HEADER).status_code == 400
    original = s.session.starts_at
    s.session.refresh_from_db()
    assert s.session.starts_at == original and not BookingMessage.objects.exists()


def test_recurring_creation_is_reviewed_published_and_retry_safe(notifications):
    s = notifications
    data = data_for(s, "create")
    accepted = preview(s, "create", data)
    assert s.offering.sessions.count() == 1
    for _ in range(2):
        assert s.client.post(path(s, "create"), accepted, **HEADER).json() == {"saved": True}
    created = s.offering.sessions.exclude(pk=s.session.pk)
    assert created.count() == 2 and all(i.is_published for i in created)
    assert not BookingMessage.objects.exists()


def test_recurring_conflict_after_preview_saves_nothing(notifications):
    s = notifications
    accepted = preview(s, "create", data_for(s))
    Session.objects.create(offering=s.offering, room=s.room, capacity=10, is_published=True,
        starts_at=s.session.starts_at + timedelta(days=7, hours=2), ends_at=s.session.ends_at + timedelta(days=7, hours=2))
    assert s.client.post(path(s, "create"), accepted, **HEADER).status_code == 400
    assert s.offering.sessions.count() == 2


@pytest.mark.parametrize("send", [False, True])
def test_bulk_cancel_email_is_deliberate_with_personal_message(notifications, send):
    s = notifications
    item = booked(s)
    second_session(s)
    data = data_for(s, "cancel")
    data.update(email_customers="on" if send else "", customer_message="Our studio is closed for repairs.")
    accepted = preview(s, "cancel", data)
    assert s.client.post(path(s, "cancel"), accepted, **HEADER).json() == {"saved": True}
    item.refresh_from_db()
    assert item.state == "canceled" and s.offering.sessions.filter(canceled_at__isnull=False).count() == 2
    assert BookingMessage.objects.count() == int(send)
    if send:
        assert "closed for repairs" in BookingMessage.objects.get().body


def test_single_cancel_does_not_send_email_without_opt_in(notifications):
    s = notifications
    booked(s)
    endpoint, accepted = review(s, "cancel", cancel_data(s, email_customers=""))
    assert s.client.post(endpoint, accepted, **HEADER).json() == {"saved": True}
    assert not BookingMessage.objects.exists()


def test_bulk_refunds_use_existing_approval_and_roll_back_on_late_failure(notifications, monkeypatch):
    from scheduling import cancellation
    from core.payments.models import Refund
    from django.core.exceptions import ValidationError
    s = notifications
    one = booked(s)
    first = s.session
    s.session = second_session(s)
    two = booked(s)
    s.session = first
    provider_paid(s, one, monkeypatch)
    provider_paid(s, two, monkeypatch)
    accepted = preview(s, "cancel", {**data_for(s), "resolution": "with_refund"})
    original = cancellation.approve_refund
    def approve(*args, **kwargs):
        if args[2] == two.public_id:
            raise ValidationError("Account needs review")
        return original(*args, **kwargs)
    monkeypatch.setattr(cancellation, "approve_refund", approve)
    assert s.client.post(path(s, "cancel"), accepted, **HEADER).status_code == 400
    one.refresh_from_db()
    assert one.state == "confirmed" and not Refund.objects.exists()
    monkeypatch.setattr(cancellation, "approve_refund", original)
    assert s.client.post(path(s, "cancel"), accepted, **HEADER).json() == {"saved": True}
    assert list(Refund.objects.values_list("amount_cents", flat=True)) == [100, 100]


def test_recurring_wall_time_survives_daylight_saving_transition(notifications):
    from datetime import datetime
    from unittest.mock import patch
    s = notifications
    s.location.timezone_name = "America/Los_Angeles"
    s.location.save()
    data = {**data_for(s), "first_date": "2027-03-07", "last_date": "2027-03-14", "weekday": 6,
        "start_time": "10:00", "room": ""}
    with patch("django.utils.timezone.now", return_value=datetime(2027, 2, 1, tzinfo=ZoneInfo("UTC"))):
        s.client.force_login(s.user)
        accepted = preview(s, "create", data)
        assert s.client.post(path(s, "create"), accepted, **HEADER).json() == {"saved": True}
        times = list(s.offering.sessions.exclude(pk=s.session.pk).values_list("starts_at", flat=True))
        assert [value.hour for value in times] == [18, 17]
        data.update(start_time="02:30")
        assert s.client.post(path(s, "create"), data, **HEADER).status_code == 400


def test_other_days_times_and_locations_are_excluded(notifications):
    from scheduling.models import Location, Offering
    s = notifications
    second = second_session(s)
    second.starts_at += timedelta(hours=1)
    second.ends_at += timedelta(hours=1)
    second.save()
    other = Location.objects.create(profile=s.profile, name="Other studio")
    offering = Offering.objects.create(location=other, name="Other")
    foreign = Session.objects.create(offering=offering, starts_at=s.session.starts_at,
        ends_at=s.session.ends_at, capacity=10)
    accepted = preview(s, "edit", data_for(s))
    assert s.client.post(path(s, "edit"), accepted, **HEADER).json() == {"saved": True}
    old = second.starts_at
    second.refresh_from_db()
    assert second.starts_at == old
    foreign.refresh_from_db()
    assert foreign.starts_at == s.session.starts_at


@pytest.mark.parametrize("has_booking", [False, True])
def test_empty_and_booked_session_editors_expose_same_batch_entry(notifications, has_booking):
    s = notifications
    if has_booking:
        booked(s)
    page = s.client.get(url(s, "session_edit", pk=s.session.pk), **HEADER)
    assert page.status_code == 200
    assert b"Change multiple sessions" in page.content and b"Repeat session" in page.content
    repeated = s.client.get(path(s, "create"), {"session": s.session.pk}, **HEADER)
    assert repeated.context["form"]["first_date"].value() == timezone.localtime(s.session.starts_at,
        ZoneInfo(s.location.timezone_name)).date() + timedelta(days=7)


def test_already_used_child_review_is_a_clear_error_not_a_partial_batch(notifications):
    from django.core import signing
    from scheduling import session_changes
    s = notifications
    booked(s)
    second_session(s)
    accepted = preview(s, "edit", data_for(s))
    signed = signing.loads(accepted["preview_token"], salt=workshop_schedule.SALT)
    command = QueryDict(mutable=True)
    command.update(signed["commands"][0])
    assert session_changes.save(s.user, s.school.slug, s.session.pk, "edit", command)[0]
    response = s.client.post(path(s, "edit"), accepted, **HEADER)
    assert response.status_code == 400
    assert not AdminAuditLog.objects.filter(extra__schedule_batch__isnull=False).exists()


@pytest.mark.parametrize("action", ["create", "edit", "cancel"])
def test_access_csrf_and_disabled_workflow(notifications, action):
    s = notifications
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(s.user)
    assert csrf.post(path(s, action), data_for(s)).status_code == 403
    SchedulingMembership.objects.filter(user=s.user).update(role="viewer")
    assert s.client.get(path(s, action)).status_code == 404
    SchedulingMembership.objects.filter(user=s.user).update(role="owner")
    s.profile.native_reservations_enabled = False
    s.profile.save()
    assert s.client.get(path(s, action)).status_code == 404


@pytest.mark.parametrize("change", [{"last_date": "2000-01-01"}, {"weekday": 8}, {"start_time": "bad"}, {"capacity": 0}])
def test_invalid_recurrence(notifications, change):
    s = notifications
    assert s.client.post(path(s, "create"), {**data_for(s), **change}, **HEADER).status_code == 400
    assert s.offering.sessions.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_recurrence_confirmation_does_not_duplicate(notifications):
    s = notifications
    accepted = preview(s, "create", data_for(s))
    barrier = Barrier(2)
    def run():
        close_old_connections()
        try:
            data = QueryDict(mutable=True)
            data.update(accepted)
            barrier.wait(timeout=10)
            return workshop_schedule.save(s.user, s.school.slug, s.offering.pk, "create", data)[2]
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: run(), range(2))) == [True, True]
    assert s.offering.sessions.count() == 3
