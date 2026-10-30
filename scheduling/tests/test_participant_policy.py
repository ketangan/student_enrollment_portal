from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from scheduling import participant_policy, requests
from scheduling.inventory_forms import PartyMaterialRow
from scheduling.models import ChoiceGroup, ChoiceOption, PartyRequest, Reservation
from scheduling.participant_policy import AgeRangeField, estimated_participants
from scheduling.request_forms import request_forms
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_party_packages import direct_party, package, package_data, send
from scheduling.tests.test_parties import party_offering
from scheduling.tests.test_staff_parties import studio, data_for

pytestmark = pytest.mark.django_db


@pytest.fixture
def policy(monkeypatch):
    value = {"minimum": 7, "choice_group": "Age", "adult_label": "Adult"}
    monkeypatch.setattr(participant_policy, "load_school_config", lambda slug:
        SimpleNamespace(raw={"scheduling": {"participant_age": value}}))
    return value


@pytest.mark.parametrize("label,valid", [("6", False), ("7", True), ("Adult", True), ("Under 5", False), ("Prefer not to say", False)])
@pytest.mark.parametrize("deferred", [False, True])
def test_workshop_choices_are_filtered_and_forged_ages_rejected(stock, policy, label, valid, deferred):
    group = ChoiceGroup.objects.create(catalog=stock.catalog, name="Age")
    options = {name: ChoiceOption.objects.create(group=group, label=name) for name in ["6", "7", "Adult", "Under 5", "Prefer not to say"]}
    stock.offering.allow_deferred_selections = True
    catalog = requests.catalog_for(stock.offering)
    age = next(g for g in catalog if g.pk == group.pk)
    assert [o.label for o in age.active_options] == ["7", "Adult"]
    data = {"participants-TOTAL_FORMS": "1", "participants-INITIAL_FORMS": "0",
            "participants-0-name": "Guest", f"participants-0-choice_{group.pk}": options[label].pk,
            f"participants-0-choice_{stock.design.pk}": stock.cat.pk,
            f"participants-0-choice_{stock.color.pk}": stock.blue.pk}
    stock.session.offering = stock.offering
    if deferred:
        data["choose_when"] = "later"
    _, forms = request_forms(stock.session, catalog, data)
    assert forms.is_valid() is valid
    # Filtering does not delete historical options or rewrite their labels.
    assert group.options.count() == 5


def test_missing_age_configuration_fails_closed(stock, policy):
    data = {"participants-TOTAL_FORMS": "1", "participants-INITIAL_FORMS": "0", "participants-0-name": "Guest",
            f"participants-0-choice_{stock.design.pk}": stock.cat.pk,
            f"participants-0-choice_{stock.color.pk}": stock.blue.pk}
    _, forms = request_forms(stock.session, requests.catalog_for(stock.offering), data)
    assert not forms.is_valid()
    assert "Age choices are not configured" in str(forms.errors)


def test_other_schools_keep_their_choices(stock):
    group = ChoiceGroup.objects.create(catalog=stock.catalog, name="Age")
    option = ChoiceOption.objects.create(group=group, label="6")
    assert next(g for g in requests.catalog_for(stock.offering) if g.pk == group.pk).active_options == [option]
    assert PartyMaterialRow(offering=stock.offering, guest_mode=True).fields["guest_age"].clean("6") == 6


def test_guest_age_field_shared_by_staff_and_customers(stock, policy):
    field = PartyMaterialRow(offering=stock.offering, guest_mode=True).fields["guest_age"]
    with pytest.raises(ValidationError):
        field.clean("6")
    assert field.clean("7") == 7
    assert field.clean("22") == 22
    assert field.clean("") is None


@pytest.mark.parametrize("value", ["7-9", "7 to 9", "7", "Adults", "7-9 years", "7-Adult", "7 to adults"])
def test_age_range_examples(value):
    assert AgeRangeField(minimum=7, max_length=100).clean(value) == value


@pytest.mark.parametrize("value", ["6-8", "6-Adult", "Adult-7", "131-Adult", "9-7", "131", "Prefer not to say", "<script>alert(1)</script>", "7" * 101])
def test_invalid_age_ranges(value):
    with pytest.raises(ValidationError):
        AgeRangeField(minimum=7, max_length=100).clean(value)


@pytest.mark.parametrize("value", ["7-9 guests", "7 to 9", "7", ""])
def test_estimate_examples(value):
    estimated_participants(value)


@pytest.mark.parametrize("value", ["1", "30", "7-30"])
def test_scoped_estimate_limit(value):
    estimated_participants(value, maximum=30)


@pytest.mark.parametrize("value", ["31", "7-31", "9-7", "0"])
def test_scoped_estimate_error(value):
    with pytest.raises(ValidationError, match=r"Enter a participant estimate such as 7-9 \(1 to 30\)."):
        estimated_participants(value, maximum=30)


@pytest.mark.parametrize("value", ["9-7", "0", "10001", "7.5", "<script>", "7" * 100])
def test_invalid_estimates(value):
    with pytest.raises(ValidationError):
        estimated_participants(value)


def test_direct_booking_estimate_does_not_change_price_or_capacity(client, direct_party):
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, starts_at="", participant_estimate="7-9 guests")
    response = send(client, direct_party.package.offering, data)
    assert response.status_code == 302
    source, reservation = PartyRequest.objects.get(), Reservation.objects.get()
    assert source.participant_estimate == reservation.snapshot["participant_estimate"] == "7-9 guests"
    assert reservation.quantity == source.group_size == int(data["group_size"])
    assert reservation.order.total_cents == 49500


@pytest.mark.parametrize("field,value", [("birthday_age", "6"), ("age_range", "6-8"), ("participant_estimate", "9-7 guests")])
def test_customer_party_rejects_invalid_values_before_booking(client, direct_party, policy, field, value):
    data = package_data(client, direct_party.package)
    data.update(party_slot=direct_party.pk, starts_at="", **{field: value})
    response = send(client, direct_party.package.offering, data)
    assert response.status_code == 400
    assert field in response.context["form"].errors
    assert not PartyRequest.objects.exists() and not Reservation.objects.exists()


def test_staff_age_validation_and_estimate_persistence(booking_member, studio, policy):
    from scheduling import staff_parties
    data = data_for(booking_member, studio, group_size="8", age_range="6-8", participant_estimate="7-9 guests")
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert item is None and "age_range" in form.errors
    data["age_range"] = "Adults"
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert not form.errors
    item.refresh_from_db()
    assert (item.group_size, item.participant_estimate, item.age_range) == (8, "7-9 guests", "Adults")


@pytest.mark.parametrize("minimum", [True, "7", -1, 131])
def test_invalid_age_configuration_rejected(stock, policy, minimum):
    policy["minimum"] = minimum
    with pytest.raises(ValidationError):
        participant_policy.age_policy(stock.school)


def test_deferred_workshop_cannot_omit_age(stock, policy):
    group = ChoiceGroup.objects.create(catalog=stock.catalog, name="Age")
    ChoiceOption.objects.create(group=group, label="7")
    stock.offering.allow_deferred_selections = True
    stock.session.offering = stock.offering
    _, forms = request_forms(stock.session, requests.catalog_for(stock.offering), {
        "participants-TOTAL_FORMS": "1", "participants-INITIAL_FORMS": "0", "choose_when": "later"})
    assert not forms.is_valid()
    assert f"choice_{group.pk}" in forms.errors[0]
