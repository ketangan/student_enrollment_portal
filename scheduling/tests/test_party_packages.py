from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from scheduling.models import Location, Offering, PartyPackage, PartyRequest, PartySlot, Reservation
from scheduling.party_forms import PartyPackageForm
from scheduling.setup import build_form, save_setup
from scheduling.tests.test_parties import party_offering, begin, send, route
from scheduling.tests.test_inventory import stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def direct_party(package, settings):
    from core.payments.models import PaymentAccount
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    profile = package.offering.location.profile
    profile.native_reservations_enabled = True
    profile.save()
    PaymentAccount.objects.create(school=profile.school, reference="acct_local_test", enabled=True)
    package.booking_mode = "direct"
    package.room = package.offering.location.rooms.first()
    package.space = "exclusive"
    package.tax_rate = Decimal("10")
    package.save()
    from django.utils import timezone
    return PartySlot.objects.create(package=package, starts_at=timezone.now() + timedelta(days=10))


def test_direct_party_holds_once_and_uses_existing_checkout(client, direct_party):
    from scheduling.checkout import payment_choices, prepare_booking_checkout, settle_booking_event
    from scheduling.tests.test_checkout import event_for
    slot = direct_party
    data = package_data(client, slot.package)
    data.update(party_slot=slot.pk, starts_at="")
    response = send(client, slot.package.offering, data)
    assert response.status_code == 302 and "/pay/" in response.url
    item = Reservation.objects.get()
    assert item.state == "held"
    assert item.starts_at == slot.starts_at
    assert item.order.total_cents == 49500 and item.order.deposit_cents == 15000
    assert payment_choices(item.order)["payment_has_choice"]
    assert send(client, slot.package.offering, data).url == response.url
    assert Reservation.objects.count() == 1
    attempt = prepare_booking_checkout(item, "http://localhost:8002/return/")
    assert attempt.amount_cents == 15000
    attempt.provider_session_id = "cs_test_" + attempt.public_id.hex
    attempt.save()
    settle_booking_event(event_for(attempt))
    item.refresh_from_db()
    assert item.state == "confirmed"


def test_direct_party_rejects_unoffered_or_wrong_package_time(client, direct_party):
    data = package_data(client, direct_party.package)
    assert send(client, direct_party.package.offering, data).status_code == 400
    other = PartyPackage.objects.create(offering=direct_party.package.offering, name="Other", participant_limit=6,
        duration_minutes=90, total=450, deposit=150, booking_mode="direct", is_published=True)
    data.update(package=other.pk, party_slot=direct_party.pk)
    assert send(client, direct_party.package.offering, data).status_code == 400
    assert not Reservation.objects.exists() and not PartyRequest.objects.exists()


def test_direct_conflict_rolls_back_inquiry_and_order(client, direct_party):
    from django.test import Client
    from core.payments.models import PaymentOrder
    first = package_data(client, direct_party.package)
    first.update(party_slot=direct_party.pk, starts_at="")
    assert send(client, direct_party.package.offering, first).status_code == 302
    other = Client()
    data = package_data(other, direct_party.package)
    data.update(party_slot=direct_party.pk, starts_at="")
    assert send(other, direct_party.package.offering, data).status_code == 400
    assert PartyRequest.objects.count() == PaymentOrder.objects.count() == Reservation.objects.count() == 1


def test_unpublishing_time_invalidates_customer_form(client, direct_party):
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, starts_at="")
    direct_party.is_published = False
    direct_party.save()
    assert send(client, direct_party.package.offering, data).status_code == 400
    assert not PartyRequest.objects.exists()


def test_expired_direct_hold_cannot_displace_new_booking(client, direct_party):
    from django.test import Client
    from django.utils import timezone
    from scheduling.checkout import prepare_booking_checkout
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, starts_at="")
    assert send(client, direct_party.package.offering, data).status_code == 302
    first = Reservation.objects.get()
    first.hold_until = timezone.now() - timedelta(seconds=1)
    first.save()
    other = Client()
    next_data = package_data(other, direct_party.package)
    next_data.update(party_slot=direct_party.pk, starts_at="")
    assert send(other, direct_party.package.offering, next_data).status_code == 302
    with pytest.raises(ValidationError):
        prepare_booking_checkout(first, "http://localhost:8002/return/")


def test_party_time_actions_require_setup_access_and_package_scope(client, direct_party, booking_member):
    client.force_login(booking_member.user)
    package = direct_party.package
    other = PartyPackage.objects.create(offering=package.offering, name="Other", participant_limit=6)
    url = route("party_setup", package.offering) + f"?section=rules&edit={other.pk}"
    assert client.post(url, {"package_id": other.pk, "slot_action": "close", "slot_id": direct_party.pk}).status_code == 404
    booking_member.role = "viewer"
    booking_member.save()
    assert client.post(url, {"package_id": other.pk, "slot_action": "add", "starts_at": "2026-12-01T12:00"}).status_code == 404


def test_party_times_setup_scoped_and_does_not_overwrite_package(client, direct_party, booking_member):
    from django.utils import timezone
    client.force_login(booking_member.user)
    package = direct_party.package
    url = route("party_setup", package.offering) + f"?section=rules&edit={package.pk}"
    payload = {"package_id": package.pk, "slot_action": "add", "starts_at": (timezone.now() + timedelta(days=20)).strftime("%Y-%m-%dT%H:%M")}
    assert client.post(url, payload).status_code == 302
    assert package.slots.count() == 2
    assert client.post(url, payload).status_code == 400
    assert client.post(url, {"package_id": package.pk, "slot_action": "close", "slot_id": direct_party.pk}).status_code == 302
    direct_party.refresh_from_db()
    package.refresh_from_db()
    assert not direct_party.is_published and package.total == 450 and package.deposit == 150


@pytest.mark.parametrize("field,value", [("room", None), ("tax_rate", None), ("booking_mode", "review")])
def test_unready_packages_cannot_publish_times(direct_party, field, value):
    setattr(direct_party.package, field, value)
    with pytest.raises(ValidationError):
        direct_party.full_clean()


def test_direct_party_defers_materials_but_not_preparation(client, stock, settings):
    from core.payments.models import PaymentAccount
    from scheduling.inventory import ensure_stock, consume
    from scheduling.preparation import validate_materials
    from scheduling.checkout import prepare_booking_checkout
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    PaymentAccount.objects.create(school=stock.school, reference="acct_local_test", enabled=True)
    package = PartyPackage.objects.create(offering=stock.offering, name="Deferred selections", participant_limit=6,
        duration_minutes=180, total=495, deposit=150, tax_rate=0, room=stock.room,
        booking_mode="direct", is_published=True)
    slot = PartySlot.objects.create(package=package, starts_at=stock.start + timedelta(days=1))
    data = package_data(client, package)
    data.update(party_slot=slot.pk, starts_at="")
    assert send(client, package.offering, data).status_code == 302
    item = Reservation.objects.get()
    assert item.snapshot["guest_selections_pending"]
    assert not item.stock_allocations.exists()
    ensure_stock(item)
    assert prepare_booking_checkout(item, "http://localhost:8002/return/").amount_cents == 15000
    with pytest.raises(ValidationError, match="guest designs"):
        validate_materials(item, [])
    item.state = "confirmed"
    item.save()
    with pytest.raises(ValidationError, match="guest designs"):
        consume(stock.user, stock.school.slug, item.public_id)


@pytest.mark.django_db(transaction=True)
def test_concurrent_direct_party_submissions_do_not_double_book(direct_party):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    from scheduling.parties import inquiry_token, submit_inquiry
    from scheduling.tests.test_parties import payload
    slot = direct_party
    barrier = Barrier(2)
    submissions = []
    for browser in ("first", "second"):
        data = payload(inquiry_token(slot.package.offering, browser))
        data.update(package=slot.package_id, party_slot=slot.pk, group_size=6, starts_at="", occasion="Birthday")
        submissions.append((browser, data))
    def send_one(pair):
        close_old_connections()
        barrier.wait()
        try:
            submit_inquiry(slot.package.offering.location.profile.school.slug, slot.package.offering_id, pair[1], pair[0])
            return "reserved"
        except ValidationError:
            return "unavailable"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send_one, submissions))
    assert sorted(results) == ["reserved", "unavailable"]
    assert Reservation.objects.count() == PartyRequest.objects.count() == 1


@pytest.mark.parametrize("quantity,subtotal,tax,total", [(5, "450.00", "42.75", "492.75"), (7, "515.00", "48.93", "563.93"), (8, "580.00", "55.10", "635.10")])
def test_configured_extras_tax_and_snapshot(client, package, quantity, subtotal, tax, total):
    package.additional_participant_limit = 2
    package.additional_participant_price = Decimal("65")
    package.tax_rate = Decimal("9.5")
    package.included_observers = 2
    package.save()
    data = package_data(client, package)
    data.update(group_size=str(quantity), proposed_total="0", tax_rate="0")
    assert send(client, package.offering, data).status_code == 302
    item = PartyRequest.objects.get()
    assert item.proposed_total == Decimal(total)
    assert item.package_snapshot["subtotal"] == subtotal
    assert item.package_snapshot["tax"] == tax
    assert item.package_snapshot["included_observers"] == 2
    package.additional_participant_price = 90
    package.save()
    item.refresh_from_db()
    assert item.proposed_total == Decimal(total)


@pytest.mark.parametrize("field,value", [("additional_participant_limit", 2), ("additional_participant_price", 65), ("included_observers", 2), ("tax_rate", 8), ("booking_mode", "direct"), ("space", "studio")])
def test_new_configuration_invalidates_old_inquiry(client, package, field, value):
    data = package_data(client, package)
    setattr(package, field, value)
    package.save()
    assert send(client, package.offering, data).status_code == 400
    assert not PartyRequest.objects.exists()


def test_extra_limit_and_quote_overflow(package):
    for size in (0, 7):
        with pytest.raises(ValidationError):
            package.quote(size)
    package.total = Decimal("999999.99")
    package.tax_rate = Decimal("10")
    with pytest.raises(ValidationError, match="supported"):
        package.quote(6)


def test_package_space_scoped_and_capacity_includes_extra_people(package, booking_member):
    from scheduling.models import Room
    other = Location.objects.create(profile=booking_member.profile, name="Other")
    wrong_room = Room.objects.create(location=other, name="Private table", capacity=20)
    package.room = wrong_room
    with pytest.raises(ValidationError, match="location"):
        package.full_clean()
    package.room = package.offering.location.rooms.first()
    package.additional_participant_limit = 7
    with pytest.raises(ValidationError, match="capacity"):
        package.full_clean()


def test_room_capacity_cannot_invalidate_assigned_package(package):
    room = package.offering.location.rooms.first()
    package.room = room
    package.additional_participant_limit = 2
    package.save()
    room.capacity = 7
    with pytest.raises(ValidationError, match="party packages"):
        room.full_clean()


@pytest.mark.parametrize("field,value", [("included_observers", -1), ("additional_participant_limit", -1), ("additional_participant_price", -1), ("tax_rate", -1), ("tax_rate", 100), ("booking_mode", "unknown"), ("space", "unknown")])
def test_invalid_package_configuration_rejected(package, field, value):
    setattr(package, field, value)
    with pytest.raises(ValidationError):
        package.full_clean()


def test_tax_unconfigured_is_distinct_from_explicit_zero(package):
    assert package.quote(6)["tax_rate"] is None
    package.tax_rate = Decimal("0")
    assert package.quote(6)["tax_rate"] == "0"
    assert package.quote(6)["total"] == "450.00"


def test_rules_modal_save_preserves_pricing(client, package, booking_member):
    from django.forms.models import model_to_dict
    client.force_login(booking_member.user)
    url = route("party_setup", package.offering) + f"?section=rules&edit={package.pk}"
    response = client.get(url)
    assert response.status_code == 200
    assert b'data-setup-dialog' in response.content
    assert b'Capacity &amp; booking rules</a>' not in response.content
    data = model_to_dict(package)
    data.update(package_id=package.pk, included_observers=2, additional_participant_limit=2,
        additional_participant_price="65.00", room=package.offering.location.rooms.first().pk,
        tax_rate="", edit_revision=response.context["package_form"]["edit_revision"].value())
    result = client.post(url, data)
    assert result.status_code == 302
    assert "section=rules" not in result.url
    package.refresh_from_db()
    assert package.total == 450 and package.deposit == 150 and package.additional_participant_limit == 2
    assert package.included_observers == 2
    stale = client.post(url, data)
    assert stale.status_code == 400
    assert b'data-setup-dialog' in stale.content


@pytest.mark.parametrize("section", ["pricing", "rules"])
def test_inline_hidden_values_belong_to_submitted_form(client, package, booking_member, section):
    from html.parser import HTMLParser

    class Inputs(HTMLParser):
        current_form = None

        def __init__(self):
            super().__init__()
            self.names = set()

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "form":
                self.current_form = attrs.get("id")
            if tag in ("input", "select") and attrs.get("form", self.current_form) == "package-edit":
                self.names.add(attrs.get("name"))

        def handle_endtag(self, tag):
            if tag == "form":
                self.current_form = None

    client.force_login(booking_member.user)
    response = client.get(route("party_setup", package.offering), {"edit": package.pk, "section": section})
    parser = Inputs()
    parser.feed(response.content.decode())
    assert set(PartyPackageForm.Meta.fields) <= parser.names


@pytest.fixture
def package(party_offering):
    return PartyPackage.objects.create(offering=party_offering, name="Small party", participant_limit=6,
        duration_minutes=90, total=Decimal("450"), deposit=Decimal("150"), is_published=True)


def package_data(client, package):
    data = begin(client, package.offering)
    data.update(package=str(package.pk), group_size="6", occasion="Birthday", age_range="8-12")
    return data


def test_initial_contact_and_birthday_details_reach_staff(client, package, booking_member):
    data = package_data(client, package)
    data.update(organizer_phone="+1 (626) 555-0123", birthday_name="Alex", birthday_age="9", age_range="")
    assert send(client, package.offering, data).status_code == 302
    item = PartyRequest.objects.get()
    assert (item.organizer_phone, item.birthday_name, item.birthday_age) == ("+1 (626) 555-0123", "Alex", 9)
    client.force_login(booking_member.user)
    response = client.get(route("party_detail", package.offering, public_id=item.public_id))
    assert b"Alex" in response.content and b"+1 (626) 555-0123" in response.content


@pytest.mark.parametrize("updates", [{"organizer_phone": "abc"}, {"birthday_age": "-1"}, {"birthday_age": "131"}])
def test_initial_contact_rejects_invalid_values(client, package, updates):
    data = package_data(client, package)
    data.update(updates)
    assert send(client, package.offering, data).status_code == 400
    assert not PartyRequest.objects.exists()


def test_nonbirthday_drops_stale_birthday_details(client, package):
    data = package_data(client, package)
    data.update(occasion="Team retreat", birthday_name="Previous entry", birthday_age="9")
    assert send(client, package.offering, data).status_code == 302
    item = PartyRequest.objects.get()
    assert item.birthday_name == "" and item.birthday_age is None and item.organizer_phone == ""


def test_direct_contact_snapshot_preserves_booking_details(client, direct_party):
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, starts_at="", organizer_phone="626-555-0123",
                birthday_name="Alex", birthday_age="10")
    assert send(client, direct_party.package.offering, data).status_code == 302
    item = Reservation.objects.get()
    assert item.snapshot["birthday_name"] == "Alex" and item.snapshot["birthday_age"] == 10
    assert item.snapshot["organizer_phone"] == "626-555-0123"


def test_contact_and_birthday_are_optional(client, package):
    data = package_data(client, package)
    data["age_range"] = ""
    assert send(client, package.offering, data).status_code == 302
    item = PartyRequest.objects.get()
    assert not item.organizer_phone and not item.birthday_name and item.birthday_age is None


def test_package_submission_uses_server_quote_and_snapshot(client, package):
    data = package_data(client, package)
    data.update(proposed_total="1", proposed_deposit="0", duration_minutes="15", space="exclusive")
    assert send(client, package.offering, data).status_code == 302
    item = PartyRequest.objects.get()
    assert item.proposed_total == 450 and item.proposed_deposit == 150
    assert item.space == "shared" and item.ends_at - item.starts_at == timedelta(minutes=90)
    assert item.package_snapshot["included_observers"] == 0
    assert item.occasion == "Birthday" and item.age_range == "8-12"
    assert item.status == "pending" and not hasattr(item, "reservation")
    package.name = "Changed"
    package.total = 500
    package.save()
    item.refresh_from_db()
    assert item.package_snapshot["name"] == "Small party" and item.proposed_total == 450
    assert send(client, package.offering, data).status_code == 302
    assert PartyRequest.objects.count() == 1


@pytest.mark.parametrize("field,value", [("group_size", "7"), ("occasion", ""), ("age_range", "x" * 101)])
def test_package_invalid_details(client, package, field, value):
    data = package_data(client, package)
    data[field] = value
    assert send(client, package.offering, data).status_code == 400
    assert not PartyRequest.objects.exists()


def test_ages_optional_and_required_fields_marked(client, package):
    response = client.get(route("party_inquiry", package.offering, pk=package.offering_id))
    html = response.content.decode()
    assert 'for="id_organizer_name">Your name *</label>' in html
    assert 'for="id_age_range">Participants&#x27; ages</label>' in html
    assert not response.context["form"].fields["age_range"].required
    data = package_data(client, package)
    data["age_range"] = ""
    assert send(client, package.offering, data).status_code == 302
    assert PartyRequest.objects.get().age_range == ""


def test_other_location_package_and_draft_rejected(client, package):
    other = Offering.objects.create(location=package.offering.location, name="Other")
    hidden = PartyPackage.objects.create(offering=other, name="Private", participant_limit=6)
    data = package_data(client, package)
    data["package"] = str(hidden.pk)
    assert send(client, package.offering, data).status_code == 400
    hidden.offering = package.offering
    hidden.save()
    assert send(client, package.offering, data).status_code == 400


@pytest.mark.parametrize("change", ["total", "is_published"])
def test_changed_configuration_rejected(client, package, change):
    data = package_data(client, package)
    setattr(package, change, 500 if change == "total" else False)
    package.save()
    response = send(client, package.offering, data)
    assert response.status_code == 400 and b"details changed" in response.content
    assert not PartyRequest.objects.exists()


def test_custom_quote_has_no_package_pricing(client, package):
    data = package_data(client, package)
    data.update(package="", group_size="24")
    assert send(client, package.offering, data).status_code == 302
    item = PartyRequest.objects.get()
    assert item.package is None and item.proposed_total is None and item.package_snapshot == {}


def test_custom_quote_leaves_duration_for_staff(client, package):
    data = package_data(client, package)
    data["package"] = ""
    data["duration_minutes"] = ""
    assert send(client, package.offering, data).status_code == 302
    assert PartyRequest.objects.get().ends_at is None


def test_drafts_leave_custom_requests_available(client, package):
    package.is_published = False
    package.save()
    response = client.get(route("party_inquiry", package.offering, pk=package.offering_id))
    assert b"Custom group / request a quote" in response.content
    assert b"Send party request" in response.content
    assert b"Small party" not in response.content


def test_viewer_cannot_edit_party_configuration(client, package, booking_member):
    booking_member.role = "viewer"
    booking_member.save()
    client.force_login(booking_member.user)
    assert client.get(route("party_package_edit", package.offering, pk=package.pk)).status_code == 404
    assert client.post(route("party_setup", package.offering), {}).status_code == 404
    response = client.get(route("party_setup", package.offering))
    assert response.status_code == 200 and b"Add package" not in response.content


def test_package_form_drafts_and_publication(package, booking_member):
    data = {"offering": package.offering_id, "name": "Draft", "participant_limit": 12, "deposit": 300, "space": "shared"}
    form = PartyPackageForm(data, profile=booking_member.profile)
    assert form.is_valid(), form.errors
    form.save()
    data.update(is_published="on")
    form = PartyPackageForm(data, profile=booking_member.profile)
    assert not form.is_valid()
    assert {"total", "duration_minutes"} <= set(form.errors)


@pytest.mark.parametrize("total,deposit", [(100, 150), (150, 150), (150, Decimal("0.01"))])
def test_package_invalid_money(package, total, deposit):
    package.total, package.deposit = total, deposit
    with pytest.raises(ValidationError):
        package.full_clean()


def test_setup_scoping_edit_and_stale_guard(client, package, booking_member):
    client.force_login(booking_member.user)
    url = route("party_setup", package.offering)
    assert client.get(url).status_code == 200
    assert b"Small party" in client.get(url).content
    assert client.get(route("party_package_edit", package.offering, pk=package.pk)).status_code == 302
    edit_url = url + f"?edit={package.pk}"
    response = client.get(edit_url)
    assert response.status_code == 200
    data = {"offering": package.offering_id, "name": "Revised", "participant_limit": 6,
            "duration_minutes": 90, "total": 500, "deposit": 150,
            "space": "shared", "is_published": "on", "package_id": package.pk, "edit_revision": response.context["package_form"]["edit_revision"].value()}
    assert client.post(edit_url, data).status_code == 302
    assert client.post(edit_url, data).status_code == 400
    assert "edit_revision" in client.post(edit_url, data).context["package_form"].errors
    other_location = Location.objects.create(profile=booking_member.profile, name="Elsewhere")
    filtered = client.get(url, {"location": other_location.pk})
    assert b"Small party" not in filtered.content and b"Revised" not in filtered.content


def test_setup_denies_other_school(client, package, booking_member):
    from core.models import School
    from scheduling.models import SchedulingProfile
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="other-school"), enabled=True, capabilities=["parties"])
    location = Location.objects.create(profile=other, name="Secret location")
    offering = Offering.objects.create(location=location, name="Secret workshop")
    secret = PartyPackage.objects.create(offering=offering, name="Secret package", participant_limit=6)
    client.force_login(booking_member.user)
    assert client.get(route("party_package_edit", package.offering, pk=secret.pk)).status_code == 404
    assert client.get(route("party_setup", package.offering), {"edit": secret.pk}).status_code == 404
    assert client.post(route("party_setup", package.offering), {"package_id": secret.pk}).status_code == 404
    form = PartyPackageForm({"offering": offering.pk, "name": "Bad", "participant_limit": 6, "deposit": 0, "space": "shared"}, profile=booking_member.profile)
    assert not form.is_valid() and "offering" in form.errors


def test_deposit_is_optional_and_defaults_to_zero(package, booking_member):
    data = {"offering": package.offering_id, "name": "No deposit", "participant_limit": 6, "space": "shared"}
    form = PartyPackageForm(data, profile=booking_member.profile)
    assert form.is_valid(), form.errors
    assert form.save().deposit == 0


def test_shared_entry_and_admin_preview(client, package, booking_member):
    url = route("party_start", package.offering)
    response = client.get(url)
    assert response.status_code == 200
    assert b'Your name *' in response.content
    assert b'<option value="" selected>Choose a location</option>' in response.content
    assert b'<fieldset class="party-fields" disabled>' in response.content
    assert response.context.get("offering") is None
    assert client.post(url, {}).status_code == 404
    assert b'Back to party setup' not in client.get(url, {"preview": 1}).content
    client.force_login(booking_member.user)
    response = client.get(url, {"preview": 1, "offering": package.offering_id})
    assert response.status_code == 200 and b'Back to party setup' in response.content
    assert b'Small party' in response.content
    assert client.get(url, {"offering": 999999}).status_code == 404
    data = package_data(client, package)
    sent = client.post(url + f'?offering={package.offering_id}&preview=1', data)
    assert sent.status_code == 302
    assert b'Back to party setup' in client.get(sent.url).content


def test_inline_editor_alignment_labels_and_duration(client, package, booking_member):
    client.force_login(booking_member.user)
    response = client.get(route("party_setup", package.offering), {"edit": package.pk})
    assert b'90 min' in client.get(route("party_setup", package.offering)).content
    assert response.context["package_form"]["duration_minutes"].value() == 90
    assert b'Price (USD)' in response.content and b'Non-participating guests' not in response.content
    assert b'Party settings' not in response.content
    assert b'Enter 0 to collect full payment' not in response.content
    assert b'form="package-edit"' in response.content and b'editing-package' in response.content


@pytest.mark.parametrize("host,is_demo", [("localhost:8002", False), ("app.mypontora.com", False), ("demo.mypontora.com", True)])
def test_share_link_follows_environment_and_preview_opens_new_tab(client, package, booking_member, settings, host, is_demo):
    settings.ALLOWED_HOSTS = ["localhost", "app.mypontora.com", "demo.mypontora.com", "testserver"]
    settings.APP_BASE_URL = "https://app.mypontora.com"
    settings.DEMO_BASE_URL = "https://demo.mypontora.com"
    school = booking_member.profile.school
    school.is_demo = is_demo
    school.save(update_fields=["is_demo"])
    client.force_login(booking_member.user)
    response = client.get(route("party_setup", package.offering), HTTP_HOST=host, secure=host != "localhost:8002")
    assert response.status_code == 200
    scheme = "http" if host == "localhost:8002" else "https"
    assert response.context["share_url"] == f"{scheme}://{host}{route('party_start', package.offering)}"
    assert b'?preview=1" target="_blank" rel="noopener"' in response.content


def test_public_entry_renders_complete_form_and_scoped_catalog(stock, client):
    response = client.get(route("party_start", stock.offering), {"offering": stock.offering.pk})
    assert response.status_code == 200
    assert b'Your name' in response.content and b'party-request-form' in response.content
    assert b'name="duration_minutes"' not in response.content
    assert b'>Continue<' not in response.content and b'>Change location<' not in response.content
    catalog = response.context["party_catalog"]
    assert any(option["id"] == str(stock.cat.pk) for options in catalog["options"].values() for option in options)
    assert catalog["variants"]


def test_party_form_orders_space_before_time_and_removes_optional_labels(client, package):
    response = client.get(route("party_start", package.offering), {"offering": package.offering_id})
    html = response.content.decode()
    assert html.index('data-party-field="space"') < html.index('data-party-field="starts_at"') < html.index('data-party-field="organizer_name"')
    assert "(optional)" not in html
    assert "Shared space." in html


def test_slot_calendar_uses_location_dates_not_browser_timezone(client, direct_party):
    from django.utils import timezone
    direct_party.starts_at = (timezone.now() + timedelta(days=20)).replace(hour=1, minute=0)
    direct_party.save()
    location = direct_party.package.offering.location
    location.timezone_name = "America/Los_Angeles"
    location.save()
    response = client.get(route("party_start", direct_party.package.offering), {"offering": direct_party.package.offering_id})
    from zoneinfo import ZoneInfo
    local = direct_party.starts_at.astimezone(ZoneInfo(location.timezone_name))
    assert response.context["form"].slot_dates[str(direct_party.pk)]["date"] == local.strftime("%Y-%m-%d")
    assert b'party-time-picker' in response.content


@pytest.mark.parametrize("now", [False, True])
def test_package_intake_defers_guest_selections_even_for_old_clients(stock, now):
    from scheduling.party_forms import PartyInquiryForm
    from scheduling.tests.test_parties import payload
    PartyPackage.objects.create(offering=stock.offering, name="Group", participant_limit=6)
    data = payload("")
    data.update(group_size="2", occasion="Birthday", age_range="Adults", package="",
                selections_now="on" if now else "",
                **{"materials-TOTAL_FORMS": "2", "materials-INITIAL_FORMS": "0",
                   "materials-0-design": str(stock.cat.pk), "materials-0-color": str(stock.blue.pk),
                   "materials-0-guest_name": "Alex", "materials-0-quantity": "99",
                   "materials-1-design": str(stock.cat.pk), "materials-1-color": str(stock.blue.pk)})
    form = PartyInquiryForm(data, offering=stock.offering)
    assert form.is_valid(), form.errors
    assert not form.cleaned_data.get("materials")
    assert form.material_forms is None
    assert "selections_now" not in form.fields
    assert "selections_now" not in form.inquiry_values()


def test_guest_deletion_and_incomplete_selection_validation(stock):
    from scheduling.party_forms import PartyInquiryForm
    from scheduling.tests.test_parties import payload
    data = payload("")
    data.update(group_size="1", **{"materials-TOTAL_FORMS": "2", "materials-INITIAL_FORMS": "0",
        "materials-0-DELETE": "on", "materials-1-guest_name": "Sam",
        "materials-1-design": str(stock.cat.pk), "materials-1-color": str(stock.blue.pk)})
    form = PartyInquiryForm(data, offering=stock.offering)
    assert form.is_valid(), form.errors
    assert len(form.cleaned_data["materials"]) == 1
    assert form.cleaned_data["materials"][0]["guest_name"] == "Sam"
    data["materials-1-color"] = ""
    assert not PartyInquiryForm(data, offering=stock.offering).is_valid()


def test_hidden_workshop_explained_and_cannot_receive_requests(client, package, booking_member):
    package.offering.accepts_party_requests = False
    package.offering.save()
    client.force_login(booking_member.user)
    response = client.get(route("party_setup", package.offering))
    assert b'party requests are disabled' in response.content and b'Edit workshop' in response.content
    assert client.get(route("party_start", package.offering), {"offering": package.offering_id}).status_code == 404


def test_package_listing_queries_do_not_grow_per_row(client, package, booking_member):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    client.force_login(booking_member.user)
    url = route("party_setup", package.offering)
    client.get(url)
    with CaptureQueriesContext(connection) as first:
        assert client.get(url).status_code == 200
    for i in range(12):
        location = Location.objects.create(profile=booking_member.profile, name=f"Branch {i}")
        offering = Offering.objects.create(location=location, name=f"Party {i}")
        PartyPackage.objects.create(offering=offering, name="Package", participant_limit=6)
    with CaptureQueriesContext(connection) as expanded:
        assert client.get(url).status_code == 200
    assert len(expanded) <= len(first) + 1
