"""Party-specific parts of the shared staff booking amendment workflow."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError

from . import guest_lists, inventory, participant_preparation
from .models import PartyPackage


def saved_package(item):
    """Re-use package quoting with the terms agreed when this booking was saved."""
    saved = item.party_request.package_snapshot
    try:
        package = PartyPackage(offering=item.party_request.offering, room=item.room,
            name=saved["name"], participant_limit=saved["participant_limit"],
            duration_minutes=saved["duration_minutes"], total=Decimal(saved["base_price"]),
            deposit=Decimal(saved["deposit"]), space=item.party_request.space,
            additional_participant_limit=saved["additional_participant_limit"],
            additional_participant_price=Decimal(saved["additional_participant_price"]),
            tax_rate=Decimal(saved["tax_rate"] or "0"), included_observers=saved["included_observers"],
            booking_mode=saved["booking_mode"])
        package.clean_fields(exclude=["offering", "room"])
        adjustment = Decimal(saved.get("price_adjustment", "0"))
        original = package.quote(item.quantity, adjustment=adjustment)
        if int(Decimal(original["total"]) * 100) != item.order.total_cents:
            raise ValueError
    except (KeyError, TypeError, ValueError, ArithmeticError, ValidationError):
        raise ValidationError("The saved package price is incomplete or inconsistent. Contact and time can still be edited; reconcile the saved quote before changing the package or count.") from None
    return package, adjustment


def plan(item, values):
    source = item.party_request
    selected = values["package"]
    changed_package = (selected.pk if selected else None) != source.package_id
    adjustment = values.get("price_adjustment") or Decimal("0")
    quantity = values["quantity"]
    if not changed_package and quantity == item.quantity and not adjustment:
        return None
    quote = None
    room, space = item.room, source.space
    duration = item.ends_at - item.starts_at
    if source.package_id:
        agreed, previous_adjustment = saved_package(item)
    else:
        agreed, previous_adjustment = None, Decimal("0")
    if selected:
        package = selected if changed_package else agreed
        if changed_package:
            if not selected.room_id or not selected.duration_minutes:
                raise ValidationError("Set a room and duration for this package before using it for a reservation.")
            room, space = selected.room, selected.space
            duration = timedelta(minutes=selected.duration_minutes)
        quote = package.quote(quantity, adjustment=previous_adjustment + adjustment)
        total = int(Decimal(quote["total"]) * 100)
    else:
        # A custom agreed quote has no inferred per-person price or tax rate.
        total = item.order.total_cents + int(adjustment * 100)
    if not 0 <= total <= 99999999:
        raise ValidationError("The revised party price must be between $0.00 and $999,999.99.")
    deposit = min(item.order.deposit_cents, total)
    if quote:
        quote["deposit"] = str(Decimal(deposit) / 100)
    return {"package": selected, "quote": quote, "quantity": quantity,
        "room": room, "space": space, "duration": duration, "total": total, "deposit": deposit}


def resize(item, quantity, removed):
    if quantity == item.quantity:
        return
    source = item.party_request
    rows = guest_lists.saved_rows(item)
    removed = {int(index) for index in removed}
    retained = [(index, row) for index, row in enumerate(rows) if index not in removed]
    if len(retained) > quantity:
        raise ValidationError("Choose which saved participants are no longer attending.")
    participant_preparation.seed(item)
    new_rows = [row for _, row in retained]
    inventory.allocate_party_selections(item, new_rows)
    prepared = item.snapshot.get("participant_preparation", {})
    item.snapshot["participant_preparation"] = {str(new): prepared[str(old)]
        for new, (old, _) in enumerate(retained) if str(old) in prepared}
    source.materials = [row for row in new_rows if row.get("item_id")]
    complete = len(new_rows) == quantity and all(row.get("item_id") for row in new_rows)
    if source.offering.inventory_catalog_id:
        item.snapshot["guest_selections_pending"] = not complete
    if not complete or "guest_list_draft" in item.snapshot:
        item.snapshot["guest_list_draft"] = new_rows
        item.snapshot.pop("guest_list_submitted_at", None)
    item.quantity = quantity
    item.prepared_at = item.prepared_by = None
    item.preparation_version += 1


def apply(item, values, proposal):
    source = item.party_request
    if proposal:
        original_quantity = item.quantity
        resize(item, proposal["quantity"], values["remove"])
        source.group_size = item.quantity
        source.package = proposal["package"]
        source.package_snapshot = proposal["quote"] or {}
        source.room = item.room = proposal["room"]
        source.space = proposal["space"]
        item.exclusive = source.space in ("exclusive", "studio")
        source.proposed_total = Decimal(proposal["total"]) / 100
        source.proposed_deposit = Decimal(proposal["deposit"]) / 100
        if item.quantity != original_quantity:
            source.participant_estimate = ""
        item.snapshot.update(party_package=source.package_snapshot, space=source.space,
            participant_estimate=source.participant_estimate)
    if source.party_slot_id and (source.party_slot.starts_at != item.starts_at
            or source.party_slot.package_id != source.package_id):
        source.party_slot = None
