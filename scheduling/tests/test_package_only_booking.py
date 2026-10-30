from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.urls import reverse

from core.payments.models import PaymentOrder
from scheduling import party_policy, staff_parties
from scheduling.models import PartyPackage, PartyRequest, Reservation, Session
from scheduling.party_forms import PartyInquiryForm, PartyPackageForm
from scheduling.tests.test_parties import party_offering, begin, send
from scheduling.tests.test_party_packages import package, direct_party, package_data

pytestmark = pytest.mark.django_db


@pytest.fixture
def policy(monkeypatch):
    value = {"packages_only": True, "default_extra_participants": 2, "default_extra_price": "65.00"}
    monkeypatch.setattr(party_policy, "load_school_config", lambda slug:
        SimpleNamespace(raw={"scheduling": {"party_booking": value}}))
    return value


def staff_data(member, slot, **changes):
    data = QueryDict(mutable=True)
    data.update({"request_token": staff_parties.token_for(member.user, member.profile, slot.package.offering),
        "offering": str(slot.package.offering_id), "package": str(slot.package_id), "party_slot": str(slot.pk),
        "organizer_name": "Phone guest", "organizer_email": "phone@example.com", "occasion": "Birthday",
        "group_size": "6", "participant_estimate": "7-9 guests", "age_range": "7-9", **changes})
    return data


@pytest.mark.parametrize("field,value", [("package", ""), ("package", "999999"), ("party_slot", ""), ("party_slot", "999999")])
def test_public_cannot_fall_back_to_custom(client, direct_party, policy, field, value):
    data = package_data(client, direct_party.package)
    data["party_slot"] = direct_party.pk
    data[field] = value
    assert send(client, direct_party.package.offering, data).status_code == 400
    assert not PartyRequest.objects.exists() and not PaymentOrder.objects.exists()


def test_review_and_missing_packages_fail_closed(client, package, policy):
    data = package_data(client, package)
    assert send(client, package.offering, data).status_code == 400
    package.delete()
    form = PartyInquiryForm(data, offering=package.offering)
    assert form.package_mode and form.fields["package"].required
    assert not form.is_valid() and "package" in form.errors
    assert not PartyRequest.objects.exists()


def test_public_package_uses_exact_count_and_ignores_custom_fields(client, direct_party, policy):
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, participant_estimate="7-9 guests", proposed_total="1",
                starts_at="bad", duration_minutes="5", space="studio")
    assert send(client, direct_party.package.offering, data).status_code == 302
    reservation = Reservation.objects.get()
    assert reservation.quantity == 6 and reservation.order.total_cents == 49500
    assert reservation.starts_at == direct_party.starts_at
    assert reservation.ends_at - reservation.starts_at == timedelta(minutes=90)
    assert reservation.snapshot["participant_estimate"] == "7-9 guests"
    assert reservation.snapshot["space"] == "exclusive"
    assert send(client, direct_party.package.offering, data).status_code == 302
    assert Reservation.objects.count() == 1


def test_staff_uses_same_package_and_time_and_retry_preserves_snapshot(booking_member, direct_party, policy, client):
    data = staff_data(booking_member, direct_party, proposed_total="1", starts_at="bad", duration_minutes="1", space="studio")
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert form.is_valid(), form.errors
    reservation = item.reservation
    assert reservation.quantity == 6 and reservation.order.total_cents == 49500
    assert reservation.state == "confirmed" and reservation.snapshot["staff_managed"]
    assert reservation.starts_at == direct_party.starts_at
    assert not reservation.order.entries.exists()
    direct_party.package.total = Decimal("500")
    direct_party.package.save()
    direct_party.is_published = False
    direct_party.save()
    repeated, _ = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert repeated.pk == item.pk and Reservation.objects.count() == 1
    reservation.order.refresh_from_db()
    assert reservation.order.total_cents == 49500
    changed = data.copy()
    changed["group_size"] = "5"
    rejected, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, changed)
    assert rejected is None and "already saved" in str(form.errors)
    client.force_login(booking_member.user)
    url = reverse("scheduling:staff_party_create", kwargs={"school_slug": booking_member.profile.school.slug})
    assert client.post(url, data).url.endswith(f"/reservations/{reservation.public_id}/")


@pytest.mark.parametrize("changes", [{"package": ""}, {"party_slot": ""}, {"offering": "999999"}, {"request_token": "bad"}])
def test_staff_rejects_custom_or_invalid_booking(booking_member, direct_party, policy, changes):
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug,
        staff_data(booking_member, direct_party, **changes))
    assert item is None and form.errors
    assert not PartyRequest.objects.exists() and not PaymentOrder.objects.exists()


def test_staff_stale_quote_and_conflict_roll_back(booking_member, direct_party, policy, client):
    data = staff_data(booking_member, direct_party)
    direct_party.package.total = Decimal("500")
    direct_party.package.save()
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert item is None and "changed" in str(form.errors)
    first = staff_data(booking_member, direct_party)
    item, _ = staff_parties.create(booking_member.user, booking_member.profile.school.slug, first)
    client.force_login(booking_member.user)
    url = reverse("scheduling:staff_party_create", kwargs={"school_slug": booking_member.profile.school.slug})
    response = client.post(url, staff_data(booking_member, direct_party))
    assert response.status_code == 400
    assert PartyRequest.objects.count() == Reservation.objects.count() == PaymentOrder.objects.count() == 1


def test_new_defaults_do_not_change_existing_packages(booking_member, package, policy):
    new = PartyPackageForm(profile=booking_member.profile)
    assert new.initial["additional_participant_limit"] == 2
    assert new.initial["additional_participant_price"] == Decimal("65")
    assert new.initial["booking_mode"] == "direct" and new.fields["booking_mode"].disabled
    old = PartyPackageForm(profile=booking_member.profile, instance=package)
    assert old.initial["additional_participant_limit"] == 0
    assert old.initial["additional_participant_price"] == 0
    package.refresh_from_db()
    assert package.total == 450 and package.booking_mode == "review"
    data = {"offering": package.offering_id, "name": "New", "participant_limit": 6, "duration_minutes": 90,
            "total": "450", "space": "exclusive", "save_action": "publish"}
    form = PartyPackageForm(data, profile=booking_member.profile)
    assert not form.is_valid() and {"room", "tax_rate"} <= form.errors.keys()
    data["save_action"] = "draft"
    form = PartyPackageForm(data, profile=booking_member.profile)
    assert form.is_valid(), form.errors
    saved = form.save()
    assert not saved.is_published and saved.booking_mode == "direct"
    assert saved.additional_participant_limit == 2 and saved.additional_participant_price == 65


def test_policy_change_invalidates_public_form(client, direct_party, policy):
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk)
    policy["packages_only"] = False
    assert send(client, direct_party.package.offering, data).status_code == 400
    assert not Reservation.objects.exists()


def test_studio_block_masks_party_slots_and_workshop_rows(booking_member, direct_party, policy, monkeypatch):
    from scheduling.experience_demo.service import session_rows
    from scheduling import requests
    package = direct_party.package
    package.space = "studio"
    package.save()
    item, _ = staff_parties.create(booking_member.user, booking_member.profile.school.slug, staff_data(booking_member, direct_party))
    form = PartyInquiryForm(offering=package.offering)
    assert not form.fields["party_slot"].queryset.exists()
    # Simulate a stale published session overlapping a newly blocked studio.
    session = Session.objects.create(offering=package.offering, room=package.room, starts_at=direct_party.starts_at,
        ends_at=item.ends_at, capacity=12, is_published=True)
    later = Session.objects.create(offering=package.offering, room=package.room, starts_at=item.ends_at,
        ends_at=item.ends_at + timedelta(hours=1), capacity=12, is_published=True)
    monkeypatch.setattr(requests, "accepts_reservations", lambda *args: True)
    rows = {row["id"]: row for row in session_rows(booking_member.profile)}
    assert rows[session.pk]["places"] == 0 and rows[later.pk]["places"] == 12
    item.reservation.state = "canceled"
    item.reservation.save()
    assert not PartyInquiryForm(offering=package.offering).fields["party_slot"].queryset.exists()
    session.is_published = False
    session.save(update_fields=["is_published"])
    assert PartyInquiryForm(offering=package.offering).fields["party_slot"].queryset.exists()


def test_staff_and_customer_pages_exclude_custom_controls(client, booking_member, direct_party, policy):
    offering = direct_party.package.offering
    page = client.get(reverse("scheduling:party_inquiry", kwargs={"school_slug": booking_member.profile.school.slug, "pk": offering.pk}))
    assert page.status_code == 200 and b"Custom group / request a quote" not in page.content
    client.force_login(booking_member.user)
    page = client.get(reverse("scheduling:staff_party_create", kwargs={"school_slug": booking_member.profile.school.slug}), {"offering": offering.pk})
    assert page.status_code == 200 and b"Create booking" in page.content
    assert b'name="duration_minutes"' not in page.content
    assert b"Package summary" in page.content


@pytest.mark.django_db(transaction=True)
def test_concurrent_staff_package_booking_retries_create_one_order(booking_member, direct_party, policy):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    data = staff_data(booking_member, direct_party)
    barrier = Barrier(2)

    def submit():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
            assert item is not None, form.errors
            return item.pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: submit(), range(2)))
    assert results[0] == results[1]
    assert Reservation.objects.count() == PaymentOrder.objects.count() == PartyRequest.objects.count() == 1


def test_staff_time_labels_use_location_timezone(booking_member, direct_party, policy):
    from zoneinfo import ZoneInfo
    from django.utils import timezone
    location = direct_party.package.offering.location
    location.timezone_name = "America/New_York"
    location.save()
    form = staff_parties.StaffPartyForm(profile=booking_member.profile, initial={"offering": direct_party.package.offering_id})
    label = form.fields["party_slot"].label_from_instance(direct_party)
    assert label == timezone.localtime(direct_party.starts_at, ZoneInfo("America/New_York")).strftime("%a, %b %d, %Y at %I:%M %p %Z")


def test_public_start_retry_survives_package_unpublishing(client, direct_party, policy):
    data = package_data(client, direct_party.package)
    data["party_slot"] = direct_party.pk
    url = reverse("scheduling:party_start", kwargs={"school_slug": direct_party.package.offering.location.profile.school.slug})
    url += f"?offering={direct_party.package.offering_id}"
    assert client.post(url, data).status_code == 302
    direct_party.package.is_published = False
    direct_party.package.save()
    assert client.post(url, data).status_code == 302
    assert Reservation.objects.count() == 1


def test_reassigned_staff_cannot_retrieve_previous_booking(booking_member, direct_party, policy):
    data = staff_data(booking_member, direct_party)
    staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    booking_member.all_locations = False
    booking_member.save()
    booking_member.locations.clear()
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert item is None and form.errors
    assert Reservation.objects.count() == 1


@pytest.mark.parametrize("value", [None, [], {"packages_only": "yes"},
    {"maximum_participants": 0}, {"maximum_participants": True},
    {"default_extra_price": "invalid"}, {"default_extra_price": "NaN"},
    {"default_extra_price": "Infinity"}, {"default_extra_price": "-1"},
    {"default_extra_price": "1.001"}, {"default_extra_participants": -1}])
def test_invalid_party_policy_fails_closed(monkeypatch, value):
    monkeypatch.setattr(party_policy, "load_school_config", lambda slug:
        SimpleNamespace(raw={"scheduling": {"party_booking": value}}))
    with pytest.raises(ValidationError):
        party_policy.party_policy(SimpleNamespace(slug="policy-test"))


def test_staff_late_booking_failure_preserves_form(client, booking_member, direct_party, policy, monkeypatch):
    def unavailable(*args, **kwargs):
        raise ValidationError("Payment account is unavailable.")
    monkeypatch.setattr(staff_parties, "reserve_package", unavailable)
    client.force_login(booking_member.user)
    path = reverse("scheduling:staff_party_create", kwargs={"school_slug": booking_member.profile.school.slug})
    response = client.post(path, staff_data(booking_member, direct_party))
    assert response.status_code == 400
    assert b"Payment account is unavailable" in response.content
    assert response.context["form"]["organizer_name"].value() == "Phone guest"
    assert not PartyRequest.objects.exists()
