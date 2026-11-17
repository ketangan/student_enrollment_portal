"""Calendar controls reuse scoped setup/cancellation, never alternate money writers."""
from datetime import timedelta
from zoneinfo import ZoneInfo
import uuid

import pytest
from django.test import Client, RequestFactory
from django.urls import reverse

from core.payments.service import totals
from scheduling.calendar import return_url
from scheduling.models import Location, Offering, SchedulingMembership, Session
from scheduling.tests.test_inventory import stock, reserve, confirm, url
from scheduling.tests.test_calendar import enable
from scheduling.tests.test_guest_lists import booking

pytestmark = pytest.mark.django_db
HEADER = {"HTTP_X_CALENDAR_EDITOR": "1"}


@pytest.mark.parametrize("state", ["held", "review", "confirmed", "completed"])
def test_party_calendar_edit_state_is_explicit_and_keeps_payment_guard(stock, booking, state):
    from django.utils import timezone
    from core.payments.models import PaymentAccount, PaymentAttempt
    from scheduling import booking_edits
    enable(stock)
    booking.state = state
    booking.hold_until = timezone.now() - timedelta(days=1)
    booking.save()
    if state in {"held", "review"}:
        account = PaymentAccount.objects.create(school=stock.school, reference="pending_test")
        PaymentAttempt.objects.create(order=booking.order, account=account, amount_cents=10000,
            state="pending", expires_at=booking.hold_until)
    path = url(stock, "calendar_detail", kind="party", pk=booking.pk)
    data = stock.client.get(path).json()
    assert data["availability"] == ("Reserved" if state == "confirmed" else booking.state_label)
    row = data["reservations"][0]
    assert ("edit_url" in row) == (state == "confirmed")
    assert bool(row["edit_unavailable"]) == (state != "confirmed")
    if state != "confirmed":
        edit_path = url(stock, "staff_booking_edit", public_id=booking.public_id)
        response = stock.client.get(edit_path, **HEADER)
        assert booking_edits.unavailable_reason(booking).encode() in response.content
        assert b"View reservation" in response.content and b"Save changes" not in response.content
        assert stock.client.post(edit_path, {}, **HEADER).status_code == 400
        booking.refresh_from_db()
        assert booking.state == state and booking.order.total_cents == 10000
    SchedulingMembership.objects.filter(user=stock.user).update(role="viewer")
    viewer_row = stock.client.get(path).json()["reservations"][0]
    assert "edit_url" not in viewer_row and "edit_unavailable" not in viewer_row


def session_data(s, response, **changes):
    form = response.context["form"]
    return {"starts_at": s.session.starts_at.astimezone(ZoneInfo(s.location.timezone_name)).strftime("%Y-%m-%dT%H:%M"),
        "duration_minutes": 60, "room": s.room.pk, "capacity": 50,
        "change_token": form["change_token"].value() if "change_token" in form.fields else "",
        "edit_revision": form["edit_revision"].value() if "edit_revision" in form.fields else "", **changes}


@pytest.mark.parametrize("start", ["2027-01-25T10:30", "2027-99-25T10:30", "not-a-date"])
def test_new_session_prefill_from_calendar_is_validated(stock, start):
    enable(stock)
    response = stock.client.get(url(stock, "session_new", offering_pk=stock.offering.pk), {"starts_at": start}, **HEADER)
    assert response.status_code == 200
    if start == "2027-01-25T10:30":
        assert str(response.context["form"].initial["first_date"]) == "2027-01-25"
        assert str(response.context["form"].initial["start_time"]) == "10:30:00"
    else:
        assert "start_time" not in response.context["form"].initial


@pytest.mark.parametrize("combined", [False, True])
def test_calendar_create_edit_publish_and_invalid_form(stock, combined):
    s = stock
    enable(s)
    path = url(s, "session_new", offering_pk=s.offering.pk)
    response = s.client.get(path, **HEADER)
    assert b"data-calendar-editor" in response.content
    assert b'type="datetime-local"' in response.content
    assert b'name="first_date"' not in response.content
    assert b'name="start_time"' not in response.content
    assert b"Customers can find and book it online" in response.content
    assert b"Only staff can see it; you can still add bookings" in response.content
    assert list(response.context["form"].fields)[:3] == ["starts_at", "duration_minutes", "capacity"]
    assert b'<button type="button" data-calendar-close>Cancel</button>' in response.content
    start = (s.session.starts_at + timedelta(days=2)).astimezone(ZoneInfo(s.location.timezone_name))
    data = {"first_date": start.date().isoformat(), "start_time": start.strftime("%H:%M"),
        "duration_minutes": 60, "capacity": 50, "room": s.room.pk, "mode": "once", "visibility": "draft",
        "creation_token": response.context["form"]["creation_token"].value()}
    if combined:
        data.pop("first_date")
        data.pop("start_time")
        data["starts_at"] = start.strftime("%Y-%m-%dT%H:%M")
        assert s.client.post(path, {**data, "starts_at": "invalid"}, **HEADER).status_code == 400
    assert s.client.post(path, {**data, "capacity": 0}, **HEADER).status_code == 400
    assert s.client.post(path, data, **HEADER).json() == {"saved": True, "session_created": True}
    created = Session.objects.latest("pk")
    assert created.starts_at.astimezone(ZoneInfo(s.location.timezone_name)).strftime("%Y-%m-%dT%H:%M") == start.strftime("%Y-%m-%dT%H:%M")
    count = Session.objects.count()
    assert s.client.post(path, data, **HEADER).json()["saved"]
    assert Session.objects.count() == count
    assert s.client.post(path, {**data, "capacity": 40}, **HEADER).status_code == 400
    assert s.client.post(path, {**data, "creation_token": "invalid"}, **HEADER).status_code == 400
    assert Session.objects.count() == count
    assert not created.is_published
    publish = url(s, "session_publish", pk=created.pk)
    assert s.client.post(publish, {"action": "publish"}, **HEADER).json() == {"saved": True}
    created.refresh_from_db()
    assert created.is_published
    edit = url(s, "session_edit", pk=created.pk)
    response = s.client.get(edit, **HEADER)
    data = session_data(s, response, starts_at=(s.session.starts_at + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"))
    assert s.client.post(edit, data, **HEADER).json() == {"saved": True}
    stale = s.client.post(edit, data, **HEADER)
    assert stale.json() == {"saved": True}  # Exact retry is idempotent.
    created.refresh_from_db()
    assert created.starts_at.astimezone(ZoneInfo(s.location.timezone_name)).strftime("%Y-%m-%dT%H:%M") == data["starts_at"]
    assert s.client.post(publish, {"action": "unpublish"}, **HEADER).json() == {"saved": True}


def test_booked_session_time_and_capacity_stay_protected(stock):
    s = stock
    enable(s)
    item = reserve(s, 2)
    confirm(s, item)
    before = totals(item.order)
    path = url(s, "session_edit", pk=s.session.pk)
    response = s.client.get(path, **HEADER)
    data = session_data(s, response, starts_at=(s.session.starts_at + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"))
    review = s.client.post(path, data, **HEADER)
    assert review.status_code == 200 and review.context["preview"]
    item.refresh_from_db()
    assert item.starts_at == s.session.starts_at
    data = session_data(s, response, capacity=1)
    assert s.client.post(path, data, **HEADER).status_code == 400
    updated = s.client.post(path, session_data(s, response), **HEADER)
    assert updated.status_code == 200, updated.context["form"].errors
    assert updated.json() == {"saved": True}
    item.refresh_from_db()
    assert item.starts_at == s.session.starts_at
    assert totals(item.order) == before


@pytest.mark.parametrize("role,editable,cancelable", [("owner", True, True), ("scheduler", True, False), ("viewer", False, False)])
def test_calendar_actions_follow_existing_roles(stock, role, editable, cancelable):
    s = stock
    enable(s)
    item = reserve(s)
    SchedulingMembership.objects.filter(user=s.user).update(role=role)
    data = s.client.get(url(s, "calendar_detail", kind="session", pk=s.session.pk)).json()
    assert ("edit_url" in data) == editable
    assert ("cancel_url" in data["reservations"][0]) == cancelable
    assert s.client.get(url(s, "session_edit", pk=s.session.pk), **HEADER).status_code == (200 if editable else 404)
    assert s.client.get(url(s, "reservation_cancel", public_id=item.public_id), **HEADER).status_code == (200 if cancelable else 404)


def test_cancellation_in_calendar_keeps_money_and_retries_idempotent(stock):
    s = stock
    enable(s)
    item = reserve(s)
    confirm(s, item)
    before = totals(item.order)
    path = url(s, "reservation_cancel", public_id=item.public_id)
    response = s.client.get(path, **HEADER)
    assert b"data-calendar-editor" in response.content
    assert b'<button type="button" data-calendar-close>Keep reservation</button>' in response.content
    data = {"key": str(uuid.uuid4()), "resolution": "without_refund", "reason": "Customer cannot attend"}
    assert s.client.post(path, {**data, "key": "not-a-key"}, **HEADER).status_code == 400
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    item.refresh_from_db()
    assert item.state == "canceled"
    assert totals(item.order) == before
    assert not item.order.attempts.exists()
    assert not s.client.get(url(s, "calendar_detail", kind="session", pk=s.session.pk)).json()["reservations"]


def test_unpaid_cancellation_defaults_only_an_omitted_payment_choice(stock):
    from scheduling.reservation_forms import CancellationForm
    s = stock
    enable(s)
    item = reserve(s)
    path = url(s, "reservation_cancel", public_id=item.public_id)
    data = {"key": str(uuid.uuid4())}
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    item.refresh_from_db()
    assert item.state == "canceled" and not item.order.attempts.exists()
    paid_form = CancellationForm(data, unpaid=False)
    assert not paid_form.is_valid() and "resolution" in paid_form.errors
    invalid_form = CancellationForm({**data, "resolution": "invalid"}, unpaid=True)
    assert not invalid_form.is_valid() and "resolution" in invalid_form.errors


def test_completed_reservation_has_no_calendar_cancel_action(stock):
    s = stock
    enable(s)
    item = reserve(s)
    item.state = "completed"
    item.save()
    rows = s.client.get(url(s, "calendar_detail", kind="session", pk=s.session.pk)).json()["reservations"]
    assert rows and all("cancel_url" not in row for row in rows)


def test_cancel_links_use_existing_button_styles_across_templates():
    import re
    from pathlib import Path
    from django.conf import settings
    missing = []
    for path in (Path(settings.BASE_DIR) / "templates").rglob("*.html"):
        for opening in re.findall(r'(<a\b[^>]*>)\s*Cancel\s*</a>', path.read_text()):
            if not re.search(r'class="[^"]*\b(?:button|dash-action-btn)\b', opening):
                missing.append(str(path.relative_to(settings.BASE_DIR)))
    assert not missing, f"Cancel links need shared button styling: {missing}"


def test_disabled_calendar_and_forged_location_cannot_write(stock):
    s = stock
    item = reserve(s)
    path = url(s, "session_edit", pk=s.session.pk)
    assert s.client.get(path, **HEADER).status_code == 404
    assert s.client.post(url(s, "session_publish", pk=s.session.pk), {"action": "unpublish"}, **HEADER).status_code == 404
    assert s.client.get(url(s, "reservation_cancel", public_id=item.public_id), **HEADER).status_code == 404
    enable(s)
    other = Location.objects.create(profile=s.profile, name="Other studio")
    member = SchedulingMembership.objects.get(user=s.user)
    member.all_locations = False
    member.save()
    member.locations.set([other])
    assert s.client.get(path, **HEADER).status_code == 404
    assert s.client.get(url(s, "session_new", offering_pk=s.offering.pk), **HEADER).status_code == 404
    assert s.client.get(url(s, "reservation_cancel", public_id=item.public_id), **HEADER).status_code == 404


def test_noncalendar_writers_unchanged_and_csrf_enforced(stock):
    s = stock
    path = url(s, "session_edit", pk=s.session.pk)
    response = s.client.get(path)
    assert s.client.post(path, session_data(s, response)).status_code == 302
    enable(s)
    assert s.client.get(url(s, "workshop_edit", pk=s.offering.pk), **HEADER).status_code == 404
    assert s.client.post(url(s, "workshop_publish", pk=s.offering.pk), {"action": "unpublish"}, **HEADER).status_code == 404
    client = Client(enforce_csrf_checks=True)
    client.force_login(s.user)
    assert client.post(path, session_data(s, response), **HEADER).status_code == 403


def test_publication_failure_stays_in_editor(stock):
    s = stock
    enable(s)
    overlap = Session.objects.create(offering=s.offering, room=s.room, starts_at=s.session.starts_at,
        ends_at=s.session.ends_at, capacity=3)
    path = url(s, "session_publish", pk=overlap.pk)
    response = s.client.post(path, {"action": "publish"}, **HEADER)
    assert response.status_code == 400 and response.json()["error"]
    assert s.client.post(path, {"action": "delete"}, **HEADER).status_code == 400


def test_calendar_return_cannot_redirect_outside_school(stock):
    s = stock
    enable(s)
    factory = RequestFactory()
    valid = url(s, "workspace") + "?view=calendar&date=2026-12-01&range=day"
    assert return_url(factory.get("/", {"calendar": valid}), s.profile) == valid
    for value in ["https://evil.invalid/", "//evil.invalid/", "/bookings/another/", "http://[", "x" * 2049]:
        assert return_url(factory.get("/", {"calendar": value}), s.profile) == ""
    s.profile.calendar_enabled = False
    assert return_url(factory.get("/", {"calendar": valid}), s.profile) == ""
