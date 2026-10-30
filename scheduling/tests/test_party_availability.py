from datetime import timedelta

import pytest
from django.utils import timezone

from scheduling.models import Reservation, Session
from scheduling.party_forms import PartyInquiryForm
from scheduling.tests.test_parties import party_offering, send
from scheduling.tests.test_party_packages import package, direct_party, package_data
from scheduling.tests.test_package_only_booking import policy

pytestmark = pytest.mark.django_db


def booked(client, slot):
    data = package_data(client, slot.package)
    data["party_slot"] = slot.pk
    assert send(client, slot.package.offering, data).status_code == 302
    return Reservation.objects.get(), data


def test_private_room_hidden_before_review_but_own_edit_allowed(client, direct_party, policy):
    item, data = booked(client, direct_party)
    form = PartyInquiryForm(offering=direct_party.package.offering)
    assert str(direct_party.pk) not in form.slot_dates
    edit = PartyInquiryForm(offering=direct_party.package.offering, exclude_reservation=item.pk)
    assert str(direct_party.pk) in edit.slot_dates
    response = send(client, direct_party.package.offering, {**data, "validate_only": "1"})
    assert response.status_code == 200
    assert "party_slot" in response.json()["errors"]
    assert Reservation.objects.count() == 1


@pytest.mark.parametrize("state", ["expired", "canceled", "adjacent"])
def test_released_or_adjacent_claim_does_not_hide_slot(client, direct_party, state):
    item, _ = booked(client, direct_party)
    if state == "expired":
        item.hold_until = timezone.now() - timedelta(seconds=1)
    elif state == "canceled":
        item.state = "canceled"
    else:
        item.starts_at = item.ends_at
        item.ends_at += timedelta(minutes=90)
    item.save()
    assert str(direct_party.pk) in PartyInquiryForm(offering=direct_party.package.offering).slot_dates


def test_shared_capacity_checked_before_review(client, direct_party, policy):
    package = direct_party.package
    package.space = "shared"
    package.save()
    package.room.capacity = 8
    package.room.save()
    _, data = booked(client, direct_party)
    assert str(direct_party.pk) in PartyInquiryForm(offering=package.offering).slot_dates
    response = send(client, package.offering, {**data, "validate_only": "1"})
    assert "capacity" in str(response.json()["errors"]["party_slot"])
    response = send(client, package.offering, {**data, "group_size": "2", "validate_only": "1"})
    assert not response.json()["errors"]


def test_published_workshop_hides_party_time(direct_party):
    package = direct_party.package
    Session.objects.create(offering=package.offering, room=package.room,
        starts_at=direct_party.starts_at, ends_at=direct_party.starts_at + timedelta(hours=1),
        capacity=10, is_published=True)
    assert str(direct_party.pk) not in PartyInquiryForm(offering=package.offering).slot_dates
