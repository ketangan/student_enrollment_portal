from decimal import Decimal

import pytest
from django.http import Http404
from django.urls import reverse

from core.models import AdminAuditLog
from scheduling.forms import LocationForm
from scheduling.models import PartyRequest, Reservation
from scheduling.party_forms import PartyInquiryForm, PartyPackageForm
from scheduling.setup import build_form, save_setup
from scheduling.tests.test_parties import party_offering, send
from scheduling.tests.test_party_packages import package, direct_party, package_data
from scheduling.tests.test_package_only_booking import policy

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("value", ["-1", "100", "8.1234", "NaN", "Infinity", "bad"])
def test_invalid_location_rate_rejected(booking_member, value):
    form = LocationForm({"name": "Studio", "timezone_name": "America/Los_Angeles", "default_tax_rate": value}, profile=booking_member.profile)
    assert not form.is_valid() and "default_tax_rate" in form.errors


def test_location_edit_preserves_omitted_tax_and_audits_explicit_change(booking_member, party_offering):
    location = party_offering.location
    location.default_tax_rate = Decimal("8.875")
    location.save()
    form = build_form(booking_member.profile, "locations", pk=location.pk)
    data = {"name": location.name, "timezone_name": location.timezone_name,
            "edit_revision": form["edit_revision"].value()}
    saved = save_setup(booking_member.user, booking_member.profile.school.slug, "locations", data, pk=location.pk)
    assert saved.is_valid(), saved.errors
    location.refresh_from_db()
    assert location.default_tax_rate == Decimal("8.875")
    data["default_tax_rate"] = "0"
    assert save_setup(booking_member.user, booking_member.profile.school.slug, "locations", data, pk=location.pk).is_valid()
    location.refresh_from_db()
    assert location.default_tax_rate == 0
    audit = AdminAuditLog.objects.filter(model_label="scheduling.location", object_id=str(location.pk)).latest("pk")
    assert audit.changes["default_tax_rate"]["to"] == "0"
    # The same old edit token cannot overwrite a newer rate.
    data["default_tax_rate"] = "10"
    stale = save_setup(booking_member.user, booking_member.profile.school.slug, "locations", data, pk=location.pk)
    assert not stale.is_valid() and "edit_revision" in stale.errors


def test_location_tax_edit_is_scoped(booking_member, party_offering):
    location = party_offering.location
    booking_member.all_locations = False
    booking_member.save()
    booking_member.locations.clear()
    with pytest.raises(Http404):
        save_setup(booking_member.user, booking_member.profile.school.slug, "locations", {"default_tax_rate": "11"}, pk=location.pk)


def test_package_inherits_location_rate_but_explicit_zero_and_override_win(package):
    package.offering.location.default_tax_rate = Decimal("8.875")
    assert package.tax_rate is None
    quote = package.quote(6)
    assert quote["tax"] == "39.94" and quote["total"] == "489.94"
    assert quote["tax_rate"] == "8.875"
    package.tax_rate = Decimal("0")
    assert package.quote(6)["tax"] == "0.00"
    package.tax_rate = Decimal("5")
    assert package.quote(6)["tax"] == "22.50"


def test_package_publish_and_slots_accept_location_default(booking_member, direct_party, policy):
    package = direct_party.package
    package.tax_rate = None
    package.offering.location.default_tax_rate = Decimal("8.25")
    package.offering.location.save()
    package.save()
    direct_party.full_clean()
    form = PartyPackageForm({"offering": package.offering_id, "name": "Inherited tax", "participant_limit": "6",
        "duration_minutes": "90", "total": "450", "space": "exclusive", "room": package.room_id,
        "tax_rate": "", "save_action": "publish"}, profile=booking_member.profile)
    assert form.is_valid(), form.errors
    saved = form.save()
    assert saved.tax_rate is None and saved.effective_tax_rate == Decimal("8.25")
    details = PartyInquiryForm(offering=package.offering).package_details[str(package.pk)]
    assert Decimal(details["tax_rate"]) == Decimal("8.25")
    assert "tax" not in details["text"].lower()


def test_default_change_invalidates_new_quote_without_repricing_saved_booking(client, direct_party, policy):
    package = direct_party.package
    package.tax_rate = None
    package.save()
    location = package.offering.location
    location.default_tax_rate = Decimal("8.875")
    location.save()
    data = package_data(client, package)
    data["party_slot"] = direct_party.pk
    stale = package_data(client, package)
    stale["party_slot"] = direct_party.pk
    assert send(client, package.offering, data).status_code == 302
    reservation = Reservation.objects.get()
    assert reservation.order.total_cents == 48994
    assert reservation.snapshot["party_package"]["tax_rate"] == "8.875"
    location.default_tax_rate = Decimal("11")
    location.save()
    assert send(client, package.offering, stale).status_code == 400
    # Successful retries return the original reservation despite new settings.
    assert send(client, package.offering, data).status_code == 302
    reservation.order.refresh_from_db()
    assert reservation.order.total_cents == 48994 and Reservation.objects.count() == 1


def test_retired_inquiry_screens_redirect_without_deleting_records(client, booking_member, party_offering, policy):
    import uuid
    from django.utils import timezone
    from datetime import timedelta
    old = PartyRequest.objects.create(offering=party_offering, organizer_name="Old inquiry",
        organizer_email="old@example.com", group_size=6, space="shared", submission_key=uuid.uuid4(),
        starts_at=timezone.now()+timedelta(days=2))
    client.force_login(booking_member.user)
    kwargs = {"school_slug": booking_member.profile.school.slug}
    home = reverse("scheduling:reservations", kwargs=kwargs)
    assert client.get(reverse("scheduling:parties", kwargs=kwargs)).url == home
    detail = reverse("scheduling:party_detail", kwargs={**kwargs, "public_id": old.public_id})
    assert client.get(detail).url == home
    assert client.post(detail, {"status": "declined"}).status_code == 404
    page = client.get(home)
    assert b"Past inquiries" not in page.content and b"Party inquiries" not in page.content
    assert PartyRequest.objects.filter(pk=old.pk).exists()


def test_party_reservation_back_link_and_cancellations_stay_in_reservations(client, booking_member, direct_party, policy):
    data = package_data(client, direct_party.package)
    data["party_slot"] = direct_party.pk
    assert send(client, direct_party.package.offering, data).status_code == 302
    item = Reservation.objects.get()
    item.state = "canceled"
    item.save()
    client.force_login(booking_member.user)
    kwargs = {"school_slug": booking_member.profile.school.slug}
    detail = client.get(reverse("scheduling:reservation_detail", kwargs={**kwargs, "public_id": item.public_id}))
    assert b"All reservations" in detail.content and b"All parties" not in detail.content
    listing = client.get(reverse("scheduling:reservations", kwargs=kwargs), {"state": "canceled", "show_completed": "1"})
    assert any(row.pk == item.pk for row in listing.context["items"])
