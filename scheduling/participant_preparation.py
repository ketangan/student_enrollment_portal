"""Participant readiness stored alongside the reservation's frozen selection evidence."""
from decimal import Decimal

from django.core.exceptions import ValidationError

from .lessons import digest


def rows_for(item):
    from .reservation_workspace import participant_context
    people = participant_context(item)["participants"]
    party_rows = []
    if item.party_request_id:
        from .guest_lists import saved_rows
        party_rows = saved_rows(item)
    state = item.snapshot.get("participant_preparation", {})
    result = []
    for index in range(item.quantity):
        person = people[index] if index < len(people) else {"name": f"Guest {index + 1}", "selections": []}
        key = str(person.get("pending_id", index))
        selections = person.get("selections", [])
        if item.party_request_id:
            raw = party_rows[index] if index < len(party_rows) else {}
            selected = bool(raw.get("item_id"))
            signature = digest(raw.get("item_id"))
            recipe = item.snapshot.get("party_material_recipes", {}).get(str(raw.get("item_id")), {})
        else:
            selected = bool(selections) and not person.get("selections_deferred")
            signature = digest(selections)
            recipe = item.snapshot.get("workshop_participant_materials", {}).get(key, {})
        prepared = selected and (state.get(key) == signature if "participant_preparation" in item.snapshot else bool(item.prepared_at))
        result.append({**person, "key": key, "index": index, "selected": selected,
            "signature": signature, "prepared": prepared, "recipe": recipe,
            "age": next((s["option"] for s in selections if s["group"].lower() == "age"), ""),
            "design": " / ".join(str(s["option"]) for s in selections if s["group"].lower() != "age") if selected else "Picks at studio"})
    return result


def seed(item):
    if "participant_preparation" not in item.snapshot:
        item.snapshot = {**item.snapshot, "participant_preparation": {
            row["key"]: row["signature"] for row in rows_for(item) if row["prepared"]}}


def invalidate(item, key):
    seed(item)
    item.snapshot["participant_preparation"].pop(str(key), None)
    item.prepared_at = None
    item.prepared_by = None
    item.preparation_version += 1


def remaining_materials(item, allocations):
    prepared = rows_for(item)
    pulled = {}
    for person in prepared:
        if person["prepared"]:
            for key, value in person["recipe"].items():
                pulled[int(key)] = pulled.get(int(key), Decimal(0)) + Decimal(value)
    return [(row, max(Decimal(0), row.quantity - pulled.get(row.level.material_id, Decimal(0)))) for row in allocations]


def ensure_participant_stock(item, person):
    from .inventory import StockForecast, ensure_stock
    from django.utils import timezone
    people = [row for row in rows_for(item) if row["prepared"] or row["key"] == person["key"]]
    if any(not row["recipe"] for row in people):
        ensure_stock(item, physical=True)
        return
    needed = {}
    for row in people:
        for key, amount in row["recipe"].items():
            needed[int(key)] = needed.get(int(key), Decimal(0)) + Decimal(amount)
    allocations = list(item.stock_allocations.select_related("level__material", "level__location"))
    forecast = StockForecast([row.level for row in allocations], exclude=item.pk)
    for row in allocations:
        available = min(row.level.on_hand, forecast.available(row.level, timezone.now()))
        if needed.get(row.level.material_id, Decimal(0)) > available:
            raise ValidationError(f"{row.level.material.name}: not enough material is at the studio to prepare this participant.")


def apply(item, user, key, prepared):
    from .inventory import validate_allocations
    from django.utils import timezone
    seed(item)
    person = next((row for row in rows_for(item) if row["key"] == key), None)
    if person is None:
        raise ValidationError("Participant not found. Reload this booking.")
    if prepared:
        if not person["selected"]:
            raise ValidationError("Choose a design and color before marking this participant prepared.")
        if item.party_request_id and item.snapshot.get("guest_selections_pending") and not person["recipe"]:
            raise ValidationError("Save this guest's selection to reserve its materials before preparing it.")
        validate_allocations(item, allow_pending=True)
        ensure_participant_stock(item, person)
        item.snapshot["participant_preparation"][key] = person["signature"]
    else:
        item.snapshot["participant_preparation"].pop(key, None)
    complete = all(row["prepared"] for row in rows_for(item))
    item.prepared_at = timezone.now() if complete else None
    item.prepared_by = user if complete else None
    item.preparation_version += 1
    item.save(update_fields=["snapshot", "prepared_at", "prepared_by", "preparation_version"])
