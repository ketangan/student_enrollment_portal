"""Recover older material allocations without silently changing their totals."""
from collections import Counter
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError

from .inventory_models import validate_amount


RECALCULATE_LABEL = "Recalculate this booking's materials using current recipes and clear its prepared marks"


class RecalculationRequired(ValidationError):
    def __init__(self):
        super().__init__("The saved material totals no longer match the design recipes. "
            "Staff can confirm recalculating this booking's materials before saving.")


def recover(materials, rows, known, current):
    """Rows are (key, saved design identity, quantity); known recipes are per unit."""
    recipes = dict(known)
    missing = [(key, identity, count) for key, identity, count in rows if key not in recipes]
    if not missing:
        return recipes
    remaining = Counter({str(row["material_id"]): Decimal(row["quantity"]) for row in materials})
    for key, _, count in rows:
        for material, amount in recipes.get(key, {}).items():
            remaining[material] -= Decimal(amount) * count
    if any(amount < 0 for amount in remaining.values()):
        raise RecalculationRequired()
    remaining = {key: value for key, value in remaining.items() if value}
    if len({identity for _, identity, _ in missing}) == 1:
        count = sum(count for _, _, count in missing)
        units = {str(row["material_id"]): row["unit"] for row in materials}
        try:
            recipe = {key: str(validate_amount(amount / count, units[key])) for key, amount in remaining.items()}
        except ValidationError as exc:
            raise RecalculationRequired() from exc
        if not recipe:
            raise RecalculationRequired()
        recipes.update({key: dict(recipe) for key, _, _ in missing})
    else:
        try:
            candidates = current()
        except ValidationError as exc:
            raise RecalculationRequired() from exc
        total = Counter()
        for key, _, count in missing:
            for material, amount in candidates[key].items():
                total[material] += Decimal(amount) * count
        if dict(total) != remaining:
            raise RecalculationRequired()
        recipes.update({key: candidates[key] for key, _, _ in missing})
    return recipes


def workshop_recipes(item):
    from .inventory import participant_recipes
    from .lessons import digest
    offering = item.workshop_request.session.offering
    people = list(item.workshop_request.participants.all())
    known = item.snapshot.get("workshop_participant_materials", {})
    if all(p.selections_deferred or str(p.pk) in known for p in people):
        return dict(known)
    stock_groups = set(offering.inventory_catalog.choice_groups.filter(tracks_stock=True).values_list("pk", flat=True))
    rows = [(str(p.pk), digest(sorted(s["option_id"] for s in p.selections if s["group_id"] in stock_groups)), 1)
        for p in people if not p.selections_deferred]
    return recover(item.snapshot.get("materials", []), rows,
        known, lambda: participant_recipes(offering, people))


def party_recipes(item):
    from .inventory import material_recipes
    counts = Counter()
    for row in item.party_request.materials:
        if row.get("item_id"):
            counts[row["item_id"]] += row.get("quantity", 1)
    def current():
        return {str(key): {str(material): str(amount) for material, amount in recipe.items()}
            for key, recipe in material_recipes(item.party_request.offering, counts).items()}
    return recover(item.snapshot.get("materials", []), [(str(key), key, count) for key, count in counts.items()],
        item.snapshot.get("party_material_recipes", {}), current)


def add_confirmation(form, item):
    try:
        (workshop_recipes if item.workshop_request_id else party_recipes)(item)
    except RecalculationRequired:
        form.fields["recalculate_materials"] = forms.BooleanField(required=False, label=RECALCULATE_LABEL)


def confirmed(data, actor):
    return actor is not None and forms.BooleanField(required=False).clean(data.get("recalculate_materials"))


def reset_preparation(item, actor):
    from .reservations import _audit
    item.snapshot = {**item.snapshot, "participant_preparation": {}}
    item.prepared_at = item.prepared_by = None
    item.preparation_version += 1
    _audit(actor, item, "materials_recalculated", previous_materials=item.snapshot.get("materials", []))
