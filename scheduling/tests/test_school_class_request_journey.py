from datetime import datetime, timedelta, timezone

import pytest
from django.test import Client

from scheduling.models import Reservation, WorkshopRequest
from scheduling.tests.test_lessons import music
from scheduling.tests.test_school_workspace import school_workspace, session, url

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("total,expected_state", [("0", "confirmed"), ("25", "held")])
def test_public_request_is_reachable_and_owner_can_create_reservation(school_workspace, total, expected_state):
    s = school_workspace
    s.profile.native_reservations_enabled = True
    s.profile.save(update_fields=["native_reservations_enabled"])
    group = session(s)
    family = Client()
    intake_url = url(s, "workshop_request", pk=group.pk)
    form = family.get(intake_url)
    assert form.status_code == 200
    submitted = family.post(intake_url, {
        "request_token": form.context["request_token"], "purchaser_name": "Class parent",
        "purchaser_email": "class-parent@example.invalid", "participants-TOTAL_FORMS": "1",
        "participants-INITIAL_FORMS": "0", "participants-MIN_NUM_FORMS": "1",
        "participants-MAX_NUM_FORMS": "12", "participants-0-name": "Class student",
    })
    assert submitted.status_code == 302
    source = WorkshopRequest.objects.get(session=group)
    assert not Reservation.objects.filter(workshop_request=source).exists()
    requests_url = url(s, "requests")
    programs = s.client.get(url(s, "school_programs"))
    assert f'href="{url(s, "school_schedule")}"'.encode() in programs.content
    schedule = s.client.get(url(s, "school_schedule"))
    assert f'href="{requests_url}">Class requests</a>'.encode() in schedule.content
    listing = s.client.get(requests_url)
    detail_url = url(s, "request_detail", public_id=source.public_id)
    assert detail_url.encode() in listing.content
    detail = s.client.get(detail_url)
    create_url = url(s, "reservation_from_workshop", public_id=source.public_id)
    assert not detail.context["features"]["reservations"]
    assert create_url.encode() in detail.content
    assert b"Session roster &amp; preparation" not in detail.content
    assert b"pending preparation" not in detail.content
    roster = s.client.get(url(s, "preparation", pk=group.pk))
    assert b"Class student" in roster.content
    assert b"Preparation totals" not in roster.content and b"Studio inventory" not in roster.content
    s.member.role = "viewer"
    s.member.save(update_fields=["role"])
    assert create_url.encode() not in s.client.get(detail_url).content
    assert s.client.post(create_url, {"total": total, "deposit": "0", "confirm": "on"}).status_code == 404
    s.member.role = "owner"
    s.member.save(update_fields=["role"])
    assert s.client.get(create_url).status_code == 200
    accepted = s.client.post(create_url, {"total": total, "deposit": "0", "confirm": "on"})
    assert accepted.status_code == 302
    reservation = Reservation.objects.get(workshop_request=source)
    assert reservation.state == expected_state
    assert reservation.quantity == 1 and reservation.order.total_cents == int(total) * 100
    saved_detail = s.client.get(accepted.url)
    assert saved_detail.status_code == 200
    assert not saved_detail.context["booking_navigation"]["section"]["tabs"]
    assert b"Workshop Prep" not in saved_detail.content
    assert b"View reservation" in s.client.get(detail_url).content
    repeated = s.client.post(create_url, {"total": total, "deposit": "0", "confirm": "on"})
    assert repeated.status_code == 302
    assert Reservation.objects.filter(workshop_request=source).count() == 1


@pytest.mark.parametrize("start,hours,end_label", [
    (datetime(2030, 1, 2, 23, 30, tzinfo=timezone.utc), 1, "to 4:30 PM"),
    (datetime(2030, 1, 3, 7, 30, tzinfo=timezone.utc), 1, "to Jan 3, 2030 12:30 AM"),
])
def test_public_class_end_date_compares_local_days(school_workspace, start, hours, end_label):
    s = school_workspace
    s.location.timezone_name = "America/Los_Angeles"
    s.location.save(update_fields=["timezone_name"])
    group = session(s, start=start)
    group.ends_at = start + timedelta(hours=hours)
    group.save(update_fields=["ends_at"])
    html = s.client.get(url(s, "school_classes")).content.decode()
    assert end_label in html
