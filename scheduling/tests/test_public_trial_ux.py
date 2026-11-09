"""Opt-in public trial UX; run with the appointment regressions after edits settle."""
import uuid
from datetime import date, datetime, timezone as utc
from unittest.mock import patch

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from scheduling import appointments
from scheduling.appointment_models import LessonBooking
from scheduling.lesson_models import LessonClosure
from scheduling.public_trial_availability import TrialAvailability
from scheduling.tests.test_appointments import CONTACT, booked, family, offer, offer_data
from scheduling.tests.test_lessons import music, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def simplified(monkeypatch):
    policy = {"layout": "school", "simplified": True, "features": ["lessons"], "optional_features": []}
    monkeypatch.setattr("scheduling.views_appointments.navigation_policy", lambda school: policy)
    monkeypatch.setattr("scheduling.views_appointments.workflow_policy", lambda school: policy)
    return policy


def trial_url(music, offer):
    return url(music, "lesson_trial_book", public_id=offer.public_id)


def close_days(music, first, last):
    return LessonClosure.objects.create(location=music["location"], teacher=music["teacher"],
        first_date=first, last_date=last, reason="Unavailable")


def test_first_visit_skips_closed_days_and_reuses_snapshot(music, offer, client, simplified):
    close_days(music, date(2030, 1, 2), date(2030, 1, 4))
    from scheduling.lessons import SlotCalendar
    with patch("scheduling.public_trial_availability.SlotCalendar", wraps=SlotCalendar) as calendar:
        response = client.get(trial_url(music, offer))
    assert response.status_code == 200
    assert calendar.call_count == 1
    assert response.context["selection"].cleaned_data["date"] == date(2030, 1, 5)
    assert response.context["slots"]
    assert len(response.context["available_dates"]) == 7
    assert b'aria-label="Next available trial dates"' in response.content
    assert b'aria-current="date"' in response.content
    assert b"America/Los_Angeles" in response.content


def test_explicit_date_is_not_silently_replaced(music, offer, client, simplified):
    close_days(music, date(2030, 1, 2), date(2030, 1, 4))
    response = client.get(trial_url(music, offer), {"date": "2030-01-03"})
    assert response.status_code == 200
    assert response.context["selection"].cleaned_data["date"] == date(2030, 1, 3)
    assert not response.context["slots"]
    assert response.context["available_dates"][0] == date(2030, 1, 5)


@pytest.mark.parametrize("day", ["invalid", "", "2029-12-31", "2031-01-01"])
def test_invalid_date_stays_an_error(music, offer, client, simplified, day):
    response = client.get(trial_url(music, offer), {"date": day})
    assert response.status_code == 400
    assert not response.context["slots"]
    assert not LessonBooking.objects.exists()


@pytest.mark.parametrize("notice", [24, 720])
def test_empty_horizon_has_honest_empty_state(music, offer, client, simplified, notice):
    offer.horizon_days = 2
    offer.notice_hours = notice
    offer.save()
    music["teacher"].availability.all().delete()
    response = client.get(trial_url(music, offer))
    assert response.status_code == 200
    assert not response.context["slots"]
    assert not response.context["available_dates"]
    assert b"No trial times are available through Jan 3, 2030" in response.content
    assert b'name="slot"' not in response.content


def test_queries_do_not_grow_with_search_horizon(music, offer):
    close_days(music, date(2030, 1, 1), date(2031, 1, 1))
    counts = []
    for horizon in (7, 180):
        fresh = appointments.get_offer(music["profile"], offer.public_id)
        fresh.horizon_days = horizon
        with CaptureQueriesContext(connection) as queries:
            availability = TrialAvailability(fresh)
            assert availability.available_dates() == []
        counts.append(len(queries))
    assert counts[0] == counts[1]
    assert counts[1] <= 12


def test_snapshot_matches_existing_planner_and_notice_boundary(music, offer):
    booked(music, offer)
    availability = TrialAvailability(offer)
    for day in (date(2030, 1, 2), date(2030, 1, 7)):
        assert availability.slots(day) == appointments.day_slots(offer, day)
        assert all(row["starts_at"] >= availability.threshold for row in availability.slots(day))
    with CaptureQueriesContext(connection) as queries:
        availability.slots(date(2030, 1, 7))
    assert len(queries) == 0


@pytest.mark.parametrize("now,day", [
    (datetime(2030, 3, 9, 12, tzinfo=utc.utc), date(2030, 3, 10)),
    (datetime(2030, 11, 2, 12, tzinfo=utc.utc), date(2030, 11, 3)),
])
def test_dst_slots_match_existing_planner(music, offer, monkeypatch, now, day):
    monkeypatch.setattr(timezone, "now", lambda: now)
    offer.notice_hours = 0
    assert TrialAvailability(offer).slots(day) == appointments.day_slots(offer, day)


def test_initial_date_uses_location_day_not_server_day(music, offer, client, simplified, monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: datetime(2030, 1, 2, 1, tzinfo=utc.utc))
    offer.notice_hours = 0
    offer.save()
    response = client.get(trial_url(music, offer))
    assert response.context["selection"].cleaned_data["date"] == date(2030, 1, 1)


def test_next_date_respects_future_effective_teacher_windows(music, offer, client, simplified):
    music["teacher"].availability.update(effective_from=date(2030, 1, 10))
    response = client.get(trial_url(music, offer))
    assert response.status_code == 200
    assert response.context["selection"].cleaned_data["date"] == date(2030, 1, 10)
    assert response.context["slots"]


@pytest.mark.parametrize("policy", [
    {"layout": "bookings", "simplified": False},
    {"layout": "bookings", "simplified": True},
    {"layout": "school", "simplified": False},
    {"layout": "school"},
])
def test_existing_layout_date_and_copy_unchanged(music, offer, client, monkeypatch, policy):
    monkeypatch.setattr("scheduling.views_appointments.navigation_policy", lambda school: policy)
    close_days(music, date(2030, 1, 2), date(2030, 1, 4))
    response = client.get(trial_url(music, offer))
    assert response.context["selection"].cleaned_data["date"] == date(2030, 1, 2)
    assert response.context["booking_base"] == "scheduling/family_base.html"
    assert not response.context["slots"]
    assert b"Local booking preview" not in response.content


def test_family_layout_does_not_opt_in(music, offer, client, simplified):
    grant, session = family(music)
    state = client.session
    state.update(dict(session))
    state.save()
    response = client.get(url(music, "family_lesson_book", public_id=offer.public_id))
    assert response.status_code == 200
    assert response.context["booking_base"] == "scheduling/family_base.html"
    assert response.context["grant"] == grant


@pytest.mark.parametrize("configured,granted,visible", [(True, True, True), (True, False, False), (False, True, False)])
def test_class_link_requires_configuration_and_capability(music, offer, client, simplified, configured, granted, visible):
    simplified["features"] = ["lessons", "workshops"] if configured else ["lessons"]
    music["profile"].capabilities = ["lessons", "workshops"] if granted else ["lessons"]
    music["profile"].save()
    response = client.get(url(music, "lesson_trials"))
    assert response.status_code == 200
    assert (b"Classes &amp; programs" in response.content) is visible


def test_invalid_contact_preserves_auto_selected_date(music, offer, client, simplified):
    close_days(music, date(2030, 1, 2), date(2030, 1, 4))
    route = trial_url(music, offer)
    page = client.get(route)
    response = client.post(route, {"date": "2030-01-05", "slot": page.context["slots"][0]["token"],
        "contact_name": "Still here"})
    assert response.status_code == 400
    assert response.context["selection"].cleaned_data["date"] == date(2030, 1, 5)
    assert response.context["form"]["contact_name"].value() == "Still here"
    assert not LessonBooking.objects.exists()


def test_school_branded_paid_booking_receipt_privacy_and_retry(music, offer, client, simplified, mailoutbox):
    school = music["profile"].school
    school.display_name = "Example School"
    school.save()
    offer.fee_cents = 2500
    offer.save()
    catalogue = client.get(url(music, "lesson_trials"))
    assert b"Example School" in catalogue.content
    assert url(music, "school_public").encode() in catalogue.content
    assert b"Pay at studio" in catalogue.content
    assert b"Classes &amp; programs" not in catalogue.content
    route = trial_url(music, offer)
    page = client.get(route)
    data = {**CONTACT, "date": page.context["selection"].cleaned_data["date"].isoformat(),
        "slot": page.context["slots"][0]["token"]}
    result = client.post(route, data)
    assert result.status_code == 302
    receipt = client.get(result.url)
    assert b"Example School" in receipt.content
    assert b"Local time: America/Los_Angeles" in receipt.content
    assert b"No confirmation email was sent" in receipt.content
    assert b"Continue to enrollment" in receipt.content
    assert b"$25.00" in receipt.content
    assert "no-store" in receipt["Cache-Control"]
    assert not mailoutbox
    booking = LessonBooking.objects.get()
    assert not booking.order.entries.exists()
    assert Client().get(result.url).status_code == 404
    assert client.post(route, data).url == result.url
    assert LessonBooking.objects.count() == 1


def test_changed_quote_still_rejected(music, offer, client, simplified):
    route = trial_url(music, offer)
    page = client.get(route)
    offer.fee_cents = 4500
    offer.save()
    response = client.post(route, {**CONTACT, "slot": page.context["slots"][0]["token"]})
    assert response.status_code == 400
    assert not LessonBooking.objects.exists()


def test_hidden_offer_features_cannot_be_enabled_by_post(music, offer, client, simplified):
    client.force_login(music["user"])
    route = url(music, "lesson_booking_offer_edit", pk=offer.pk)
    page = client.get(route)
    assert "family_booking" not in page.context["form"].fields
    assert "makeup_eligible" not in page.context["form"].fields
    revision = page.context["form"].fields["edit_revision"].initial
    response = client.post(route, offer_data(offer, edit_revision=revision, family_booking="on", makeup_eligible="on"))
    assert response.status_code == 302
    offer.refresh_from_db()
    assert not offer.family_booking
    assert not offer.makeup_eligible
    assert offer.public_trials


def test_disabled_operator_tools_reject_ledger_post(music, offer, client, simplified):
    offer.fee_cents = 3000
    offer.save()
    booking, _, _ = booked(music, offer)
    client.force_login(music["user"])
    route = url(music, "lesson_booking_detail", public_id=booking.public_id)
    page = client.get(route)
    assert page.status_code == 200
    assert not page.context["operator_tools_enabled"]
    response = client.post(route, dict(key=uuid.uuid4(), kind="payment", amount="30", reference="blocked", confirm="on"))
    assert response.status_code == 404
    assert not booking.order.entries.exists()


def test_optional_offer_and_ledger_tools_can_stay_enabled(music, offer, client, simplified):
    simplified["optional_features"] = ["family_booking", "makeup_credits", "operator_tools"]
    client.force_login(music["user"])
    page = client.get(url(music, "lesson_booking_offer_edit", pk=offer.pk))
    assert "family_booking" in page.context["form"].fields
    assert "makeup_eligible" in page.context["form"].fields
    offer.fee_cents = 3000
    offer.save()
    booking, _, _ = booked(music, offer)
    route = url(music, "lesson_booking_detail", public_id=booking.public_id)
    response = client.post(route, dict(key=uuid.uuid4(), kind="payment", amount="30", reference="allowed", confirm="on"))
    assert response.status_code == 302
    assert booking.order.entries.count() == 1
