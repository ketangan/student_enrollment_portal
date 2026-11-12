from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from scheduling.experience_demo import service
from scheduling.models import Reservation
from .test_experience_demo import local, demo, browser, confirmed, form_data, url

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("provider,host", [("google", "calendar.google.com"), ("outlook", "outlook.live.com")])
def test_calendar_choices_use_current_booking_time_and_encode_text(demo, browser, provider, host):
    item = confirmed(browser, demo)
    start = datetime(2027, 3, 14, 10, tzinfo=ZoneInfo("America/Los_Angeles"))
    Reservation.objects.filter(pk=item.reservation.pk).update(starts_at=start, ends_at=start+timedelta(hours=2), internal_notes="Private note")
    item.session_snapshot.update(workshop="Arts & crafts + music", location="Room #2, Upstairs")
    item.save(update_fields=["session_snapshot"])
    response = browser.get(url("calendar", public_id=item.public_id), {"provider": provider})
    assert response.status_code == 302 and response["Referrer-Policy"] == "no-referrer"
    target = urlparse(response.url)
    assert target.scheme == "https" and target.netloc == host
    params = parse_qs(target.query)
    assert params["text" if provider == "google" else "subject"] == ["Arts & crafts + music"]
    assert params["location"] == ["Room #2, Upstairs"]
    assert params["dates"] == ["20270314T170000Z/20270314T190000Z"] if provider == "google" else params["startdt"] == ["2027-03-14T17:00:00+00:00"]
    assert "Private" not in response.url and item.purchaser_email not in response.url
    assert str(item.public_id) not in response.url
    calendar = browser.get(url("calendar", public_id=item.public_id))
    assert b"DTSTART:20270314T170000Z" in calendar.content
    assert 'attachment;' in calendar["Content-Disposition"]


def test_calendar_choices_privacy_invalid_provider_and_unconfirmed(demo, browser):
    item = confirmed(browser, demo)
    target = url("calendar", public_id=item.public_id)
    for provider in ("google", "outlook", "https://evil.invalid"):
        assert Client(HTTP_HOST="localhost").get(target, {"provider": provider}).status_code == 404
    assert browser.get(target, {"provider": "https://evil.invalid"}).status_code == 404
    Reservation.objects.filter(pk=item.reservation.pk).update(state="canceled")
    assert browser.get(target, {"provider": "google"}).status_code == 404


def test_confirmation_has_choices_and_consistent_cents(demo, browser):
    item = confirmed(browser, demo)
    item.reservation.order.total_cents = 14430
    item.reservation.order.save(update_fields=["total_cents"])
    page = browser.get(url("booking", public_id=item.public_id))
    assert b"$144.30</strong>" in page.content
    assert b"Google Calendar" in page.content and b"Apple / other calendar (.ics)" in page.content


def test_review_price_has_cents(demo, browser):
    session, data = form_data(browser, demo)
    page = browser.post(url("choose", pk=session.pk), data)
    assert page.status_code == 200
    assert f'&times; ${page.context["unit_price"]:.2f}</p>'.encode() in page.content


def test_confirmation_balance_date_matches_rescheduled_booking(demo, browser):
    item = confirmed(browser, demo)
    start = datetime(2027, 3, 14, 10, tzinfo=ZoneInfo("America/Los_Angeles"))
    Reservation.objects.filter(pk=item.reservation.pk).update(starts_at=start, ends_at=start+timedelta(hours=2))
    item.reservation.order.total_cents += 100
    item.reservation.order.save(update_fields=["total_cents"])
    page = browser.get(url("booking", public_id=item.public_id))
    assert b"Due on Mar 14, 2027." in page.content
