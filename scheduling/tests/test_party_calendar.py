from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from django.urls import reverse

from core.payments.models import PaymentAccount
from scheduling.checkout import payment_token
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking

pytestmark = pytest.mark.django_db


@pytest.fixture
def calendar_booking(booking):
    booking.order.account = PaymentAccount.objects.create(school=booking.profile.school, reference="acct_calendar", enabled=True)
    booking.order.save()
    booking.starts_at = datetime(2027, 3, 14, 10, tzinfo=ZoneInfo("America/Los_Angeles"))
    booking.ends_at = booking.starts_at + timedelta(hours=3)
    booking.internal_notes = "Private staff note"
    booking.save()
    booking.party_request.offering_snapshot = {"workshop": "Party & crafts", "location": "Room #2"}
    booking.party_request.save()
    return booking


@pytest.mark.parametrize("provider", ["google", "outlook", ""])
def test_party_calendar_current_times_privacy_and_download(client, calendar_booking, provider):
    item = calendar_booking
    kwargs = {"school_slug": item.profile.school.slug, "token": payment_token(item)}
    page = client.get(reverse("scheduling:customer_payment", kwargs=kwargs))
    assert page.status_code == 200 and b"Google Calendar" in page.content
    response = client.get(reverse("scheduling:customer_calendar", kwargs=kwargs), {"provider": provider} if provider else {})
    assert response["Referrer-Policy"] == "no-referrer"
    if provider:
        assert response.status_code == 302
        parsed = urlparse(response.url)
        assert parsed.netloc == ("calendar.google.com" if provider == "google" else "outlook.live.com")
        values = parse_qs(parsed.query)
        assert values["text" if provider == "google" else "subject"] == ["Party & crafts"]
        assert "20270314T170000Z" in response.url if provider == "google" else values["startdt"] == ["2027-03-14T17:00:00+00:00"]
        contents = response.url
    else:
        assert response.status_code == 200 and "attachment" in response["Content-Disposition"]
        contents = response.content.decode()
        assert "DTSTART:20270314T170000Z" in contents and "DTEND:20270314T200000Z" in contents
    assert item.party_request.organizer_email not in contents and "Private staff" not in contents


def test_party_calendar_rejects_wrong_school_tampering_and_unconfirmed(client, calendar_booking):
    item = calendar_booking
    kwargs = {"school_slug": item.profile.school.slug, "token": payment_token(item)}
    target = reverse("scheduling:customer_calendar", kwargs=kwargs)
    assert client.get(target, {"provider": "https://evil.invalid"}).status_code == 404
    assert client.get(reverse("scheduling:customer_calendar", kwargs={**kwargs, "school_slug": "another"})).status_code == 404
    assert client.get(reverse("scheduling:customer_calendar", kwargs={**kwargs, "token": "bad"})).status_code == 404
    for state in ["held", "canceled", "review"]:
        item.state = state
        item.save()
        assert client.get(target).status_code == 404
