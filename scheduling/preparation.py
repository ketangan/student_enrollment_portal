"""Reversible staff readiness, independent of payment and inventory consumption."""
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.utils import timezone

from .access import get_staff_reservation, require_location_access
from .editing import protect_edit
from .models import Reservation, SchedulingProfile
from .setup import _audit


class PreparationForm(forms.ModelForm):
    action = forms.ChoiceField(choices=[("prepare", "Mark prepared"), ("reopen", "Reopen preparation")])

    class Meta:
        model = Reservation
        fields = []


def form_for(item, profile, data=None):
    return protect_edit(PreparationForm(data, instance=item), profile)


class StaffNamesForm(forms.Form):
    staff_names = forms.CharField(label="Staff", max_length=200, required=False)


def staff_form_for(item, profile, data=None):
    form = StaffNamesForm(data, prefix="staff", initial={"staff_names": item.staff_names})
    form.instance = item
    return protect_edit(form, profile)


def save_staff_names(item, profile, user, data):
    """Called under the preparation writer's profile lock."""
    form = staff_form_for(item, profile, data)
    if not form.is_valid():
        raise ValidationError([message for errors in form.errors.values() for message in errors])
    target = form.instance
    names = form.cleaned_data["staff_names"]
    if target.staff_names == names:
        return
    before = model_to_dict(target)
    target.staff_names = names
    target.save(update_fields=["staff_names"])
    _audit(user, profile, target, before, "change")


def validate_materials(item, allocations):
    """Recipes are frozen at booking; catalog edits never alter a saved kit."""
    from .inventory import validate_allocations
    validate_allocations(item, allocations)


@transaction.atomic
def update(user, slug, public_id, data):
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    form = form_for(item, profile, data)
    if not form.is_valid():
        raise ValidationError([message for errors in form.errors.values() for message in errors])
    if item.state != "confirmed":
        raise ValidationError("Only confirmed reservations can be prepared.")
    allocations = list(item.stock_allocations.select_related("level"))
    if any(row.consumed_at for row in allocations):
        raise ValidationError("Materials have already been used; preparation cannot be changed.")
    prepared = form.cleaned_data["action"] == "prepare"
    if prepared == bool(item.prepared_at):
        return item
    if prepared:
        if item.party_request_id and item.snapshot.get("guest_selections_pending"):
            raise ValidationError("Finish the guest selections before marking this party prepared.")
        validate_materials(item, allocations)
        from .inventory import ensure_stock
        ensure_stock(item, physical=True)
    before = model_to_dict(item)
    from .participant_preparation import rows_for
    item.snapshot = {**item.snapshot, "participant_preparation": {
        row["key"]: row["signature"] for row in rows_for(item) if prepared and row["selected"]}}
    item.prepared_at = timezone.now() if prepared else None
    item.prepared_by = user if prepared else None
    item.preparation_version += 1
    item.save(update_fields=["snapshot", "prepared_at", "prepared_by", "preparation_version"])
    _audit(user, profile, item, before, "change")
    return item
