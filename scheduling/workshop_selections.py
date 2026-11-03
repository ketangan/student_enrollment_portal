"""Edit workshop choices without changing prices or other participants' frozen recipes."""
from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .access import get_staff_reservation, require_location_access
from .inventory import check_items, selection_items, validate_allocations
from .lessons import digest
from .models import SchedulingProfile, StockAllocation
from .request_forms import ParticipantForm
from .requests import catalog_for
from .reservations import _audit


def editable(item, *, staff=False):
    return bool(item.workshop_request_id and item.state == "confirmed" and (staff or not item.prepared_at))


def revision(person):
    return digest({"id": person.pk, "name": person.name, "selections": person.selections, "pending": person.selections_deferred})


def form_for(person, catalog, data=None, *, edit_name=False):
    initial = {"name": person.name, **{f"choice_{s['group_id']}": str(s["option_id"]) for s in person.selections}}
    form = ParticipantForm(data, catalog=[g for g in catalog if g.tracks_stock], initial=initial, auto_id=f"participant_{person.pk}_%s")
    form.fields["selection_revision"] = forms.CharField(required=False, widget=forms.HiddenInput(), initial=revision(person))
    if not edit_name:
        form.fields["name"].disabled = True
        form.fields["name"].widget = forms.HiddenInput()
    for name, field in form.fields.items():
        if name.startswith("choice_"):
            field.choices = [("", "Choose " + field.label.lower()), *field.choices]
            field.widget = forms.Select(choices=field.choices)
    return form


@transaction.atomic
def update(user, slug, public_id, participant_id, data):
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    if not editable(item, staff=True) or item.stock_allocations.filter(consumed_at__isnull=False).exists():
        raise ValidationError("Selections can only be changed for confirmed workshops with unused materials.")
    person = get_object_or_404(item.workshop_request.participants, pk=participant_id)
    complete(item, person, data, actor=user)
    return item


def complete(item, person, data, *, actor=None):
    """Caller holds the profile lock and has checked staff or private-link access."""
    if not person.selections_deferred and not data.get("selection_revision"):
        raise ValidationError("This participant's selections have already been saved. Reload the reservation.")
    if data.get("selection_revision") and data["selection_revision"] != revision(person):
        raise ValidationError("This participant's choices changed while the page was open. Reload before saving.")
    offering = item.workshop_request.session.offering
    if (offering.inventory_catalog_id != item.snapshot.get("material_catalog_id")
            or offering.location_id != item.snapshot.get("material_location_id")):
        raise ValidationError("This booking's catalog or studio has changed. Contact support before editing its designs.")
    form = form_for(person, catalog_for(offering), data, edit_name=actor is not None and "name" in data)
    if not form.is_valid():
        raise ValidationError([message for errors in form.errors.values() for message in errors])
    validate_allocations(item, allow_pending=True)
    selected = form.snapshot()
    group_ids = {group.pk for group in form.catalog}
    previous = person.selections
    retained = [row for row in previous if row["group_id"] not in group_ids]
    if person.name != form.cleaned_data["name"]:
        old_name = person.name
        person.name = form.cleaned_data["name"]
        person.save(update_fields=["name"])
        _audit(actor, item, "participant_name_changed", participant_id=person.pk,
            previous_name=old_name, participant_name=person.name)
    if not person.selections_deferred and {(row["group_id"], row["option_id"]) for row in selected} == {
            (row["group_id"], row["option_id"]) for row in previous if row["group_id"] in group_ids}:
        return item
    counts = selection_items(offering, [{"selections": selected}])
    from .legacy_materials import workshop_recipes, RecalculationRequired, confirmed
    try:
        recipes = workshop_recipes(item)
    except RecalculationRequired:
        if not confirmed(data, actor):
            raise
        return recalculate(item, person, selected, retained, previous, actor)
    old_recipe = recipes.get(str(person.pk), {})
    if not person.selections_deferred and not old_recipe:
        raise ValidationError("This participant's saved material recipe is missing. Contact support.")
    allocated = {str(row["material_id"]): Decimal(row["quantity"]) for row in item.snapshot.get("materials", [])}
    if any(Decimal(value) <= 0 or Decimal(value) > allocated.get(key, Decimal(0)) for key, value in old_recipe.items()):
        raise ValidationError("This participant's reserved materials do not match. Contact support.")
    # Release only this participant's frozen recipe inside the caller's atomic transaction.
    for allocation in item.stock_allocations.select_related("level"):
        amount = Decimal(old_recipe.get(str(allocation.level.material_id), "0"))
        if not amount:
            continue
        allocation.quantity -= amount
        if allocation.quantity:
            allocation.save(update_fields=["quantity"])
        else:
            allocation.delete()
    additions = check_items(offering, counts,
        starts_at=item.starts_at if item.starts_at > timezone.now() else None)
    frozen = {row["material_id"]: dict(row) for row in item.snapshot.get("materials", [])}
    for key, value in old_recipe.items():
        row = frozen[int(key)]
        row["quantity"] = str(Decimal(row["quantity"]) - Decimal(value))
        if Decimal(row["quantity"]) == 0:
            del frozen[int(key)]
    for level, quantity in additions:
        allocation, created = StockAllocation.objects.get_or_create(reservation=item, level=level, defaults={"quantity": quantity})
        if not created:
            allocation.quantity += quantity
            allocation.save(update_fields=["quantity"])
        row = frozen.setdefault(level.material_id, {"material_id": level.material_id,
            "name": level.material.name, "unit": level.material.unit, "quantity": "0"})
        row["quantity"] = str(Decimal(row["quantity"]) + quantity)
    recipes = {**recipes, str(person.pk): {str(level.material_id): str(quantity) for level, quantity in additions}}
    was_pending = person.selections_deferred
    from .participant_preparation import invalidate
    invalidate(item, str(person.pk))
    person.selections = [*retained, *selected]
    person.selections_deferred = False
    person.save(update_fields=["selections", "selections_deferred"])
    item.snapshot = {**item.snapshot, "materials": list(frozen.values()),
        "workshop_participant_materials": recipes,
        "workshop_selections_pending": item.workshop_request.participants.filter(selections_deferred=True).exists()}
    item.save(update_fields=["snapshot", "prepared_at", "prepared_by", "preparation_version"])
    _audit(actor, item, "workshop_selections_completed" if was_pending else "workshop_selections_changed", participant_id=person.pk,
        participant_name=person.name, selections=selected, previous_selections=previous)
    return item


def recalculate(item, person, selected, retained, previous, actor):
    """Explicit staff-only rebase when historical recipes cannot be recovered."""
    from .inventory import participant_recipes, check_materials
    from .legacy_materials import reset_preparation
    people = list(item.workshop_request.participants.all())
    for participant in people:
        if participant.pk == person.pk:
            participant.selections = [*retained, *selected]
            participant.selections_deferred = False
    offering = item.workshop_request.session.offering
    recipes = participant_recipes(offering, people)
    quantities = {}
    for recipe in recipes.values():
        for key, value in recipe.items():
            quantities[int(key)] = quantities.get(int(key), Decimal(0)) + Decimal(value)
    levels = check_materials(offering, quantities, item.pk,
        starts_at=item.starts_at if item.starts_at > timezone.now() else None)
    reset_preparation(item, actor)
    item.stock_allocations.exclude(level__material_id__in=quantities).delete()
    materials = []
    for level, quantity in levels:
        StockAllocation.objects.update_or_create(reservation=item, level=level, defaults={"quantity": quantity})
        materials.append({"material_id": level.material_id, "name": level.material.name,
            "unit": level.material.unit, "quantity": str(quantity)})
    person.selections, person.selections_deferred = [*retained, *selected], False
    person.save(update_fields=["selections", "selections_deferred"])
    item.snapshot = {**item.snapshot, "materials": materials, "workshop_participant_materials": recipes,
        "workshop_selections_pending": any(p.selections_deferred for p in people)}
    item.save(update_fields=["snapshot", "prepared_at", "prepared_by", "preparation_version"])
    _audit(actor, item, "workshop_selections_changed", participant_id=person.pk,
        participant_name=person.name, selections=selected, previous_selections=previous)
    return item
