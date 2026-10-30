from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.urls import reverse

from scheduling import inventory, requests, workshop_followup, workshop_selections
from scheduling.models import ChoiceOption, CatalogVariant, InventoryCatalog, Material, MaterialRequirement, StockLevel
from scheduling.tests.test_inventory import stock, reserve, confirm
from scheduling.tests.test_workshop_selections import choices, save

pytestmark = pytest.mark.django_db


def replacement(s, *, quantity=1):
    red = ChoiceOption.objects.create(group=s.color, label="Red", swatch="#ee1111")
    variant = CatalogVariant(catalog=s.catalog, name="Red cat", option_ids=[s.cat.pk, red.pk])
    variant.full_clean()
    variant.save()
    material = Material.objects.create(profile=s.profile, name="Red fabric", sku="RED", unit="pieces")
    MaterialRequirement.objects.create(variant=variant, material=material, quantity=1)
    level = StockLevel.objects.create(material=material, location=s.location, on_hand=quantity)
    return red, level


def booked(s, quantity=2):
    item = reserve(s, quantity)
    confirm(s, item)
    item.refresh_from_db()
    return item, item.workshop_request.participants.first()


def edit_data(s, person, option=None):
    return {**choices(s), f"choice_{s.color.pk}": str((option or s.blue).pk),
        "selection_revision": workshop_selections.revision(person)}


def test_replace_preserves_other_recipes_money_and_nonstock_choices(stock):
    s = stock
    item, person = booked(s)
    other = item.workshop_request.participants.last()
    previous_other = other.selections
    person.selections.append({"group_id": 99999, "group": "Age", "option_id": 99999, "option": "Adult"})
    person.save()
    red, level = replacement(s)
    recipe = s.item.requirements.get()
    recipe.quantity = 3
    recipe.save()
    total, paid, count = item.order.total_cents, item.order.entries.count(), item.quantity
    item = save(s, item, person, edit_data(s, person, red))
    assert inventory.reserved(s.level) == 1
    assert inventory.reserved(level) == 1
    assert item.snapshot["workshop_participant_materials"][str(other.pk)] == {str(s.material.pk): "1.000"}
    other.refresh_from_db()
    person.refresh_from_db()
    assert other.selections == previous_other
    assert person.selections[0]["group"] == "Age"
    assert (item.order.total_cents, item.order.entries.count(), item.quantity) == (total, paid, count)
    inventory.validate_allocations(item)
    assert any(row["option"] == "Red" for row in person.selections)


def test_noop_keeps_frozen_recipe_and_stale_edit_is_rejected(stock):
    s = stock
    item, person = booked(s, 1)
    original = dict(item.snapshot)
    recipe = s.item.requirements.get()
    recipe.quantity = 5
    recipe.save()
    old_data = edit_data(s, person)
    save(s, item, person, old_data)
    item.refresh_from_db()
    assert item.snapshot == original
    red, level = replacement(s)
    save(s, item, person, edit_data(s, person, red))
    with pytest.raises(ValidationError, match="changed while"):
        save(s, item, person, old_data)
    assert inventory.reserved(level) == 1 and inventory.reserved(s.level) == 0


def test_shortage_rolls_back_old_choice_allocations_and_name(stock):
    s = stock
    item, person = booked(s)
    red, level = replacement(s, quantity=0)
    before = person.selections
    original = dict(item.snapshot)
    with pytest.raises(ValidationError, match="only 0"):
        workshop_followup.save(s.school.slug, workshop_followup.token_for(item), person.pk, {
            "selection-design": s.cat.pk, "selection-color": red.pk,
            "selection-guest_name": "Replacement name",
            "selection-selection_revision": workshop_selections.revision(person)})
    person.refresh_from_db()
    item.refresh_from_db()
    assert person.selections == before and person.name == "Child"
    assert item.snapshot == original
    assert inventory.reserved(s.level) == 2 and inventory.reserved(level) == 0


def test_customer_edit_and_staff_prefill(stock):
    s = stock
    item, person = booked(s, 1)
    red, level = replacement(s)
    url = reverse("scheduling:workshop_selections", kwargs={"school_slug": s.school.slug,
        "token": workshop_followup.token_for(item)})
    response = s.client.get(url + f"?participant={person.pk}")
    form = response.context["form"]
    assert str(form["color"].value()) == str(s.blue.pk)
    assert s.client.post(url, {"participant": person.pk, "selection-design": s.cat.pk,
        "selection-color": red.pk, "selection-selection_revision": workshop_selections.revision(person)}).status_code == 302
    person.refresh_from_db()
    form = workshop_selections.form_for(person, requests.catalog_for(s.offering))
    assert form[f"choice_{s.color.pk}"].value() == str(red.pk)
    assert inventory.reserved(level) == 1


def test_swap_can_reuse_own_stock_but_not_another_participants(stock):
    s = stock
    item, person = booked(s)
    red, level = replacement(s)
    variant = s.catalog.items.get(name="Red cat")
    variant.requirements.all().delete()
    MaterialRequirement.objects.create(variant=variant, material=s.material, quantity=1)
    s.level.on_hand = 2
    s.level.save()
    save(s, item, person, edit_data(s, person, red))
    assert inventory.reserved(s.level) == 2
    person.refresh_from_db()
    recipe = variant.requirements.get()
    recipe.quantity = 3
    recipe.save()
    # Switching away and back must not silently re-price another participant's frozen stock.
    save(s, item, person, edit_data(s, person))
    person.refresh_from_db()
    with pytest.raises(ValidationError, match="only 1"):
        save(s, item, person, edit_data(s, person, red))
    assert inventory.reserved(s.level) == 2


def test_consumed_booking_cannot_be_changed(stock):
    s = stock
    item, person = booked(s, 1)
    red, _ = replacement(s)
    inventory.consume(s.user, s.school.slug, item.public_id)
    with pytest.raises(ValidationError, match="confirmed workshops with unused materials"):
        save(s, item, person, edit_data(s, person, red))


@pytest.mark.parametrize("outcome", ["change", "noop", "shortage"])
def test_prepared_staff_edits_reopen_only_after_a_successful_change(stock, outcome):
    from scheduling import preparation
    s = stock
    item, person = booked(s, 1)
    red, level = replacement(s, quantity=0 if outcome == "shortage" else 1)
    item = preparation.update(s.user, s.school.slug, item.public_id, {
        "action": "prepare",
        "edit_revision": preparation.form_for(item, s.profile)["edit_revision"].value(),
    })
    original_prepared = item.prepared_at
    original_version = item.preparation_version
    assert not workshop_selections.editable(item)
    assert workshop_selections.editable(item, staff=True)
    url = reverse("scheduling:reservation_detail", kwargs={"school_slug": s.school.slug, "public_id": item.public_id})
    assert f'edit_participant={person.pk}'.encode() in s.client.get(url).content
    data = edit_data(s, person, None if outcome == "noop" else red)
    if outcome == "shortage":
        with pytest.raises(ValidationError, match="only 0"):
            save(s, item, person, data)
    else:
        save(s, item, person, data)
    item.refresh_from_db()
    person.refresh_from_db()
    changed = outcome == "change"
    assert item.prepared_at == (None if changed else original_prepared)
    assert item.preparation_version == original_version + int(changed)
    assert item.prepared_by_id == (None if changed else s.user.pk)
    assert inventory.reserved(level) == int(changed)
    assert inventory.reserved(s.level) == int(not changed)
    assert any(row["option"] == ("Red" if changed else "Blue") for row in person.selections)


def test_catalog_replacement_cannot_rewrite_old_reservation(stock):
    s = stock
    item, person = booked(s, 1)
    s.offering.inventory_catalog = InventoryCatalog.objects.create(profile=s.profile, name="New catalog")
    s.offering.save()
    with pytest.raises(ValidationError, match="catalog or studio has changed"):
        save(s, item, person, edit_data(s, person))
    assert inventory.reserved(s.level) == 1


def test_participant_recipe_lookup_is_batched(stock, django_assert_num_queries):
    s = stock
    from scheduling.tests.test_inventory import request as make_request
    source = make_request(s, 20)
    people = list(source.participants.all())
    # Constant query count: groups, variants, recipes, requirements, materials, inactive choices.
    with django_assert_num_queries(6):
        recipes = inventory.participant_recipes(s.offering, people)
    assert len(recipes) == 20


def test_legacy_group_cannot_guess_individual_materials(stock):
    s = stock
    item, person = booked(s, 2)
    item.snapshot.pop("workshop_participant_materials")
    item.save()
    red, level = replacement(s)
    with pytest.raises(ValidationError, match="older booking"):
        save(s, item, person, edit_data(s, person, red))
    assert inventory.reserved(s.level) == 2
    assert inventory.reserved(level) == 0


def test_legacy_single_choice_uses_exact_frozen_total(stock):
    s = stock
    item, person = booked(s, 1)
    item.snapshot.pop("workshop_participant_materials")
    item.save()
    red, level = replacement(s)
    recipe = s.item.requirements.get()
    recipe.quantity = 3
    recipe.save()
    save(s, item, person, edit_data(s, person, red))
    assert inventory.reserved(level) == 1
    assert inventory.reserved(s.level) == 0


def test_legacy_group_can_still_complete_an_undecided_participant(stock):
    s = stock
    from scheduling import reservations
    from scheduling.tests.test_inventory import request as make_request
    source = make_request(s, 3)
    person = source.participants.last()
    person.selections, person.selections_deferred = [], True
    person.save()
    item = reservations.create_reservation(s.user, s.school.slug, "workshop", source.public_id,
        total_cents=19500, deposit_cents=0)
    confirm(s, item)
    item.refresh_from_db()
    item.snapshot.pop("workshop_participant_materials")
    item.save()
    save(s, item, person)
    assert inventory.reserved(s.level) == 3
    person.refresh_from_db()
    red, level = replacement(s)
    save(s, item, person, edit_data(s, person, red))
    assert inventory.reserved(s.level) == 2 and inventory.reserved(level) == 1


@pytest.mark.parametrize("corruption", ["missing", "excess"])
def test_invalid_saved_participant_recipe_is_blocked(stock, corruption):
    s = stock
    item, person = booked(s, 1)
    red, _ = replacement(s)
    item.snapshot["workshop_participant_materials"][str(person.pk)] = {} if corruption == "missing" else {str(s.material.pk): "99"}
    item.save()
    with pytest.raises(ValidationError, match="Contact support"):
        save(s, item, person, edit_data(s, person, red))
    assert inventory.reserved(s.level) == 1


@pytest.mark.django_db(transaction=True)
def test_competing_edits_keep_loser_original_selection(stock):
    s = stock
    first = booked(s, 1)
    second = booked(s, 1)
    red, level = replacement(s)

    def submit(pair):
        close_old_connections()
        try:
            item, person = pair
            save(s, item, person, edit_data(s, person, red))
            return True
        except ValidationError:
            return False
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, [first, second]))
    assert sorted(results) == [False, True]
    assert inventory.reserved(level) == 1 and inventory.reserved(s.level) == 1
    for item, _ in (first, second):
        item.refresh_from_db()
        inventory.validate_allocations(item)
