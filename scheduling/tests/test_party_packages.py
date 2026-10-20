from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from scheduling.models import Location, Offering, PartyPackage, PartyRequest
from scheduling.party_forms import PartyPackageForm
from scheduling.setup import build_form, save_setup
from scheduling.tests.test_parties import party_offering, begin, send, route
from scheduling.tests.test_inventory import stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def package(party_offering):
    return PartyPackage.objects.create(offering=party_offering, name="Small party", participant_limit=6,
        duration_minutes=90, total=Decimal("450"), deposit=Decimal("150"), is_published=True)


def package_data(client, package):
    data = begin(client, package.offering)
    data.update(package=str(package.pk), group_size="6", occasion="Birthday", age_range="8-12")
    return data


def test_package_submission_uses_server_quote_and_snapshot(client, package):
    data = package_data(client, package)
    data.update(proposed_total="1", proposed_deposit="0", duration_minutes="15", space="exclusive")
    assert send(client, package.offering, data).status_code == 302
    item = PartyRequest.objects.get()
    assert item.proposed_total == 450 and item.proposed_deposit == 150
    assert item.space == "shared" and item.ends_at - item.starts_at == timedelta(minutes=90)
    assert "included_observers" not in item.package_snapshot
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
    response = client.get(route("party_start", stock.offering))
    assert response.status_code == 200
    assert b'Your name' in response.content and b'party-request-form' in response.content
    assert b'name="duration_minutes"' not in response.content
    assert b'>Continue<' not in response.content and b'>Change location<' not in response.content
    catalog = response.context["party_catalog"]
    assert any(option["id"] == str(stock.cat.pk) for options in catalog["options"].values() for option in options)
    assert catalog["variants"]


@pytest.mark.parametrize("now", [False, True])
def test_guest_selections_now_or_later(stock, now):
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
    assert bool(form.cleaned_data.get("materials")) == now
    assert "selections_now" not in form.inquiry_values()
    if now:
        assert form.cleaned_data["materials"][0]["quantity"] == 1
        assert form.cleaned_data["materials"][0]["guest_name"] == "Alex"
        data["group_size"] = "1"
        assert not PartyInquiryForm(data, offering=stock.offering).is_valid()


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
