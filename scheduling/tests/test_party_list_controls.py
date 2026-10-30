from datetime import datetime, timezone, timedelta
from django.utils import timezone as django_timezone
import pytest

from scheduling.models import Location, Offering
from scheduling.tests.test_parties import party_offering, create_inquiry, route, review_data
from scheduling.parties import review_inquiry
from scheduling.tests.test_reservations import reserve

pytestmark = pytest.mark.django_db


def test_past_parties_hidden_and_canceled_labels_are_grouped(party_offering, booking_member, client):
    item = create_inquiry(client, party_offering)
    item.starts_at = django_timezone.now() - timedelta(days=2)
    item.ends_at = item.starts_at + timedelta(hours=2)
    item.save()
    client.force_login(booking_member.user)
    url = route("parties", party_offering)
    assert client.get(url).context["items"].paginator.count == 0
    page = client.get(url, {"show_completed": "on"})
    assert page.context["items"].paginator.count == 1
    assert page.context["filter_form"]["show_completed"].value()
    assert not client.get(url, {"show_completed": "false"}).context["items"]
    for state in ("declined", "withdrawn"):
        item.status = state
        item.save()
        page = client.get(url, {"hide_completed": "0", "state": "canceled"})
        assert page.context["items"].paginator.count == 1
        assert page.context["items"][0].list_status == "Canceled"


def test_party_list_shared_controls_and_filters(party_offering, booking_member, client):
    item = create_inquiry(client, party_offering)
    client.force_login(booking_member.user)
    url = route("parties", party_offering)
    page = client.get(url)
    assert page.context["total_count"] == page.context["review_count"] == 1
    assert b'booking-quick-filters' in page.content and b'Awaiting review' in page.content
    assert b'party-workshop-filter' in page.content and b'party-date-filter' in page.content and b'party-status-filter' in page.content
    assert b'Party packages</a>' not in page.content and b'Original inquiries' not in page.content
    assert b'Main Studio' in page.content
    assert b'Show completed' in page.content
    assert b'dash-initials' not in page.content
    assert not page.context['filter_form']['show_completed'].value()
    assert b'name="offering"' not in page.content
    assert list(page.context["filter_form"].fields["state"].choices) == [("", "All statuses"),
        ("pending", "Needs review"), ("reviewed", "Reviewed"), ("held", "Awaiting payment"),
        ("confirmed", "Confirmed"), ("completed", "Completed"), ("canceled", "Canceled")]
    for data in ({"q": "Morgan"}, {"state": "pending"}, {"review": "1"}):
        assert client.get(url, data).context["items"].paginator.count == 1
    for data in ({"q": "No match"}, {"state": "reviewed"}, {"state": "confirmed"}):
        assert client.get(url, data).context["items"].paginator.count == 0
    for data in ({"state": "bogus"}, {"date_from": "bad"}, {"date_from": "2026-11-02", "date_to": "2026-11-01"}):
        response = client.get(url, data)
        assert response.status_code == 400 and response.context["items"].paginator.count == 0


def test_party_list_scope_and_local_dates(party_offering, booking_member, client):
    item = create_inquiry(client, party_offering)
    location = party_offering.location
    location.timezone_name = "America/Los_Angeles"
    location.save()
    item.starts_at = datetime(2026, 11, 2, 7, 30, tzinfo=timezone.utc)
    item.ends_at = datetime(2026, 11, 2, 9, 30, tzinfo=timezone.utc)
    item.save()
    client.force_login(booking_member.user)
    url = route("parties", party_offering)
    assert client.get(url, {"date_from": "2026-11-01", "date_to": "2026-11-01"}).context["items"].paginator.count == 1
    assert client.get(url, {"date_from": "2026-11-02"}).context["items"].paginator.count == 0
    other = Location.objects.create(profile=booking_member.profile, name="Restricted")
    offering = Offering.objects.create(location=other, name="Other")
    booking_member.all_locations = False
    booking_member.save()
    booking_member.locations.set([location])
    assert client.get(url, {"location": other.pk}).status_code == 400
    assert client.get(url, {"location": location.pk}).context["items"].paginator.count == 1


def test_converted_inquiry_filters_use_the_displayed_reservation_state(party_offering, booking_member, client):
    booking_member.profile.native_reservations_enabled = True
    booking_member.profile.save()
    item = create_inquiry(client, party_offering)
    form = review_inquiry(booking_member.user, booking_member.profile.school.slug, item.public_id, review_data(item))
    assert form.is_valid(), form.errors
    item.refresh_from_db()
    reservation = reserve(booking_member, item)
    reservation.state = "confirmed"
    reservation.save()
    client.force_login(booking_member.user)
    url = route("parties", party_offering)
    page = client.get(url, {"state": "confirmed"})
    assert page.context["items"].paginator.count == 1
    assert page.context["review_count"] == 0
    assert client.get(url, {"state": "reviewed"}).context["items"].paginator.count == 0
    reservation.state = "completed"
    reservation.save()
    assert client.get(url).context["items"].paginator.count == 0
    page = client.get(url, {"hide_completed": "0", "state": "completed"})
    assert page.context["items"].paginator.count == 1
    assert page.context["total_count"] == 1
