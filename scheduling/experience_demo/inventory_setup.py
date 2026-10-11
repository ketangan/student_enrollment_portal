"""One-time sample inventory for the guarded local seed; preserves later staff edits."""
from itertools import product
from collections import Counter
from decimal import Decimal
from django.db import transaction
from django.core.exceptions import ValidationError

from scheduling.models import (ChoiceGroup, ChoiceOption, InventoryCatalog, Offering, Reservation,
    StockAdjustment, StockAllocation, CatalogVariant, StockLevel, Material, MaterialRequirement)


def bind_catalog(profile, catalog, config):
    roles = {key: catalog.choice_groups.get(name=config[key]).pk
             for key in ("design_group", "color_group", "age_group")}
    profile.school.feature_flags = {**profile.school.feature_flags,
        "booking_demo_inventory": {"catalog": catalog.pk, "groups": roles}}
    profile.school.save(update_fields=["feature_flags"])
    bind_previews(profile, catalog, config)


def bind_previews(profile, catalog, config):
    binding = profile.school.feature_flags["booking_demo_inventory"]
    if "design_previews" in binding:
        return
    by_name = {row["name"]: {key: row[key] for key in ("x", "y") if key in row}
               for row in config.get("designs", [])}
    # Bind the original sprite to option identity, not an editable label or display order.
    previews = {str(option.pk): by_name[option.label] for option in ChoiceOption.objects.filter(
        group_id=binding["groups"]["design_group"], group__catalog=catalog) if option.label in by_name}
    profile.school.feature_flags = {**profile.school.feature_flags,
        "booking_demo_inventory": {**binding, "design_previews": previews}}
    profile.school.save(update_fields=["feature_flags"])


@transaction.atomic
def seed_inventory(profile, config, user):
    binding = profile.school.feature_flags.get("booking_demo_inventory", {})
    if binding:
        catalog = InventoryCatalog.objects.get(profile=profile, pk=binding["catalog"])
        bind_previews(profile, catalog, config)
        return catalog
    catalog, created = InventoryCatalog.objects.get_or_create(profile=profile, name="Plushie designs")
    if not created:
        bind_catalog(profile, catalog, config)
        return catalog
    dimensions = []
    for position, (name, choices) in enumerate([(config["design_group"], config["designs"]),
        (config["color_group"], config["colors"]), (config["age_group"], [{"name": x} for x in ["Under 5", *map(str, range(5, 18)), "Adult", "Prefer not to say"]])]):
        group = ChoiceGroup.objects.create(catalog=catalog, name=name, position=position, tracks_stock=position < 2,
            reference_image=config.get("design_image", "") if position == 0 else "")
        options = [ChoiceOption.objects.create(group=group, label=c["name"], position=i, swatch=c.get("hex", ""))
                   for i, c in enumerate(choices)]
        if group.tracks_stock:
            dimensions.append(options)
    lookup = {}
    materials = {}
    for index, color in enumerate(config["colors"]):
        materials[color["name"]] = Material.objects.create(profile=profile, name=f'{color["name"]} fleece', sku=f'FABRIC-{index+1:02d}', unit="m")
    stuffing = Material.objects.create(profile=profile, name="Polyester stuffing", sku="FILL-001", unit="g")
    eyes = Material.objects.create(profile=profile, name="Safety eyes", sku="EYES-001", unit="pieces")
    for material in [*materials.values(), stuffing, eyes]:
        amount = {"m": 20, "g": 12000, "pieces": 200}[material.unit]
        for location in profile.locations.all():
            level = StockLevel.objects.create(material=material, location=location, on_hand=amount)
            StockAdjustment.objects.create(level=level, delta=amount, reason="Opening material balance", actor=user)
    for options in product(*dimensions):
        item = CatalogVariant(catalog=catalog, name=" / ".join(o.label for o in options), option_ids=[o.pk for o in options])
        item.full_clean(); item.save()
        lookup[tuple(o.label for o in options)] = item
        for material, quantity in [(materials[options[1].label], Decimal(".5")), (stuffing, Decimal(150)), (eyes, Decimal(2))]:
            MaterialRequirement.objects.create(variant=item, material=material, quantity=quantity)
    Offering.objects.filter(location__profile=profile, visit_program__isnull=True).update(inventory_catalog=catalog)
    bind_catalog(profile, catalog, config)
    # Backfill historical local workshop/camp selections without rewriting their snapshots.
    for reservation in Reservation.objects.filter(profile=profile, state__in=["held", "confirmed", "review"]):
        counts = Counter()
        if reservation.workshop_request_id:
            source = reservation.workshop_request
            location = source.session.offering.location
            selections = [p.selections for p in source.participants.all()]
        elif reservation.camp_registration_id:
            source = reservation.camp_registration
            location = source.camp.offering.location
            selections = [p["selections"] for p in source.participants]
        elif reservation.party_request_id:
            raise ValidationError("Existing party bookings need material reconciliation before demo inventory is enabled.")
        else:
            continue
        for selection in selections:
            choices = {c["group"]: c["option"] for c in selection}
            item = lookup.get((choices.get(config["design_group"]), choices.get(config["color_group"])))
            if not item:
                raise ValidationError("An existing demo booking has unmatched material selections. Reconcile it before enabling inventory.")
            counts[item.pk] += 1
        from scheduling.inventory import check_items
        offering = source.session.offering if reservation.workshop_request_id else source.camp.offering
        offering.refresh_from_db()
        snapshot = []
        for level, quantity in check_items(offering, counts, reservation.pk):
            StockAllocation.objects.create(reservation=reservation, level=level, quantity=quantity)
            snapshot.append({"material_id": level.material_id, "name": level.material.name, "unit": level.material.unit, "quantity": str(quantity)})
        reservation.snapshot = {**reservation.snapshot, "materials": snapshot, "material_catalog_id": catalog.pk, "material_location_id": location.pk}
        reservation.save(update_fields=["snapshot"])
    return catalog
