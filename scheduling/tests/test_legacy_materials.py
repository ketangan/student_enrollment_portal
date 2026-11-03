import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from scheduling import guest_lists, inventory, preparation
from scheduling.legacy_materials import RecalculationRequired, recover
from scheduling.tests.test_inventory import stock
from scheduling.tests.test_guest_lists import booking, payload
from scheduling.tests.test_workshop_selection_edits import booked, replacement, edit_data, save

pytestmark = pytest.mark.django_db


def old_mixed_workshop(s):
    item, person = booked(s)
    red, level = replacement(s, quantity=4)
    other = item.workshop_request.participants.last()
    save(s, item, other, edit_data(s, other, red))
    item.refresh_from_db()
    item = preparation.update(s.user, s.school.slug, item.public_id, {
        "action": "prepare", "edit_revision": preparation.form_for(item, s.profile)["edit_revision"].value()})
    item.snapshot.pop("workshop_participant_materials")
    item.save()
    return item, person, red, level


def test_mixed_workshop_recovers_exact_totals_and_preserves_other_prep(stock):
    item, person, red, level = old_mixed_workshop(stock)
    other = item.workshop_request.participants.last()
    original_money = item.order.total_cents
    save(stock, item, person, edit_data(stock, person, red))
    item.refresh_from_db()
    assert inventory.reserved(stock.level) == 0 and inventory.reserved(level) == 2
    assert str(other.pk) in item.snapshot["participant_preparation"]
    assert str(person.pk) not in item.snapshot["participant_preparation"]
    assert item.order.total_cents == original_money
    inventory.validate_allocations(item)


@pytest.mark.parametrize("shortage", [False, True])
def test_changed_recipes_require_confirmation_and_rebase_atomically(stock, shortage):
    item, person, red, level = old_mixed_workshop(stock)
    recipe = stock.item.requirements.get(); recipe.quantity = 3; recipe.save()
    original = dict(item.snapshot)
    data = edit_data(stock, person, red)
    with pytest.raises(RecalculationRequired):
        save(stock, item, person, data)
    item.refresh_from_db()
    assert item.snapshot == original and item.prepared_at
    if shortage:
        level.on_hand = 1; level.save()
        with pytest.raises(ValidationError, match="only 1"):
            save(stock, item, person, {**data, "recalculate_materials": "on", "name": "Changed"})
        item.refresh_from_db(); person.refresh_from_db()
        assert item.snapshot == original and item.prepared_at and person.name == "Child"
    else:
        save(stock, item, person, {**data, "recalculate_materials": "on"})
        item.refresh_from_db()
        assert not item.prepared_at and not item.snapshot["participant_preparation"]
        assert inventory.reserved(level) == 2 and inventory.reserved(stock.level) == 0
        from scheduling.reservation_audit import audit_page
        assert any(row["title"] == "Materials recalculated; preparation reset" for row in audit_page(item, 1)["rows"])
        inventory.validate_allocations(item)


def test_confirmation_shown_only_when_needed_on_both_staff_pages(stock):
    item, person, red, level = old_mixed_workshop(stock)
    detail = reverse("scheduling:reservation_detail", args=[stock.school.slug, item.public_id])
    prep = reverse("scheduling:studio_preparation", args=[stock.school.slug])
    from scheduling.tests.test_studio_preparation import query
    for path, params in [(detail, {"edit_participant": person.pk}),
            (prep, query(item, edit=f"{item.public_id}:{person.pk}"))]:
        assert b'name="recalculate_materials"' not in stock.client.get(path, params).content
    recipe = stock.item.requirements.get(); recipe.quantity = 3; recipe.save()
    for path, params in [(detail, {"edit_participant": person.pk}),
            (prep, query(item, edit=f"{item.public_id}:{person.pk}"))]:
        assert b'name="recalculate_materials"' in stock.client.get(path, params).content


def test_customer_cannot_authorize_material_recalculation(stock):
    from scheduling import workshop_followup
    item, person, red, _ = old_mixed_workshop(stock)
    item.prepared_at = item.prepared_by = None; item.save()
    recipe = stock.item.requirements.get(); recipe.quantity = 3; recipe.save()
    with pytest.raises(RecalculationRequired):
        workshop_followup.save(stock.school.slug, workshop_followup.token_for(item), person.pk, {
            "selection-design": stock.cat.pk, "selection-color": red.pk,
            "selection-selection_revision": edit_data(stock, person)["selection_revision"],
            "recalculate_materials": "on"})


@pytest.mark.parametrize("changed", [False, True])
def test_older_party_mixed_design_edit(stock, booking, changed):
    red, level = replacement(stock, quantity=4)
    data = payload(stock); data["materials-1-color"] = red.pk
    guest_lists.save_for_staff(stock.user, stock.school.slug, booking.public_id, data)
    booking.refresh_from_db()
    booking.snapshot.pop("party_material_recipes"); booking.save()
    if changed:
        recipe = stock.item.requirements.get(); recipe.quantity = 3; recipe.save()
    edit = {"revision": 1, "guest_name": "Guest 1", "design": stock.cat.pk, "color": red.pk}
    if changed:
        with pytest.raises(RecalculationRequired):
            guest_lists.save_guest(stock.user, stock.school.slug, booking.public_id, 0, edit)
        edit["recalculate_materials"] = "on"
    _, form = guest_lists.save_guest(stock.user, stock.school.slug, booking.public_id, 0, edit)
    assert not form.errors
    booking.refresh_from_db()
    assert inventory.reserved(level) == 2 and inventory.reserved(stock.level) == 0
    inventory.validate_allocations(booking)


def test_recovery_rejects_fractional_pieces_and_inconsistent_known_totals():
    materials = [{"material_id": 1, "quantity": "3", "unit": "pieces"}]
    with pytest.raises(RecalculationRequired):
        recover(materials, [("a", "same", 1), ("b", "same", 1)], {}, lambda: {})
    with pytest.raises(RecalculationRequired):
        recover(materials, [("a", "same", 1), ("b", "same", 1)], {"a": {"1": "4"}}, lambda: {})
    assert recover(materials, [("a", "same", 1)], {}, lambda: {}) == {"a": {"1": "3"}}
