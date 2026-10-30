import uuid
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction

from scheduling import reservation_notes
from scheduling.models import ReservationNote
from scheduling.parties import review_inquiry
from scheduling.tests.test_parties import party_offering, create_inquiry, review_data, route
from scheduling.tests.test_reservations import reserve

pytestmark = pytest.mark.django_db


def test_inquiry_notes_append_and_follow_reservation(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    item.preparation_notes = "Earlier note"
    item.save()
    client.force_login(booking_member.user)
    target = route("party_detail", party_offering, public_id=item.public_id)
    data = {"action": "save_notes", "body": "Bring extra chairs <script>x</script>", "submission_id": uuid.uuid4()}
    assert client.post(target, data).status_code == 302
    assert client.post(target, data).status_code == 302
    assert item.staff_notes.count() == 1
    assert client.post(target, {**data, "body": "Replace"}).status_code == 400
    for params in ({}, {"tab": "preparation"}):
        page = client.get(target, params)
        assert b"Earlier note" in page.content and b"&lt;script&gt;x&lt;/script&gt;" in page.content
        assert b'name="preparation_notes"' not in page.content
    assert review_inquiry(booking_member.user, booking_member.profile.school.slug, item.public_id, review_data(item)).is_valid()
    item.refresh_from_db()
    assert item.preparation_notes == "Earlier note"
    booking_member.profile.native_reservations_enabled = True
    booking_member.profile.save()
    reservation = reserve(booking_member, item)
    response = client.get(target)
    assert response.status_code == 302
    page = client.get(response.url)
    assert page.status_code == 200 and b"Party Prep" in page.content
    assert b"Bring extra chairs" in page.content and b"Earlier note" in page.content
    assert b"Customer notes" in page.content
    assert reservation_notes.context_for(reservation)["staff_notes"].paginator.count == 1


def test_inquiry_notes_permissions_and_location_scope(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    client.force_login(booking_member.user)
    url = route("party_detail", party_offering, public_id=item.public_id)
    data = {"action": "save_notes", "body": "Private", "submission_id": uuid.uuid4()}
    assert client.post(url, {**data, "body": " "}).status_code == 400
    assert client.post(url, {**data, "body": "x" * 3001}).status_code == 400
    booking_member.role = "viewer"
    booking_member.save()
    assert client.post(url, data).status_code == 404
    booking_member.role = "owner"
    booking_member.all_locations = False
    booking_member.save()
    assert client.get(url).status_code == client.post(url, data).status_code == 404
    assert not item.staff_notes.exists()


def test_duration_is_set_once_and_removed_payment_mode_is_ignored(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    item.ends_at = item.starts_at + timedelta(minutes=90)
    item.save()
    form = review_inquiry(booking_member.user, booking_member.profile.school.slug, item.public_id,
        review_data(item, duration_minutes="15", payment_at_booking="full"))
    assert form.is_valid(), form.errors
    item.refresh_from_db()
    assert item.ends_at - item.starts_at == timedelta(minutes=90)
    assert item.proposed_deposit == 100
    assert "duration_minutes" not in form.fields and "payment_at_booking" not in form.fields
    client.force_login(booking_member.user)
    page = client.get(route("party_detail", party_offering, public_id=item.public_id))
    assert b"90 minutes" in page.content
    assert b'name="duration_minutes"' not in page.content


def test_note_requires_exactly_one_owner(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    with pytest.raises(IntegrityError), transaction.atomic():
        ReservationNote.objects.create(body="No owner")
    ReservationNote.objects.create(party_request=item, body="Inquiry note")
