"""Reversible staff readiness, independent of payment and inventory consumption."""
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .access import require_access
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


def validate_materials(item, allocations):
    """Recipes are frozen at booking; catalog edits never alter a saved kit."""
    from .inventory import validate_allocations
    validate_allocations(item, allocations)


@transaction.atomic
def update(user, slug, public_id, data):
    profile, _ = require_access(user, slug, "sessions.edit", capability="reservations")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_access(user, slug, "sessions.edit", capability="reservations")
    item = get_object_or_404(Reservation, profile=profile, public_id=public_id, visit_booking__isnull=True)
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
        validate_materials(item, allocations)
    before = model_to_dict(item)
    item.prepared_at = timezone.now() if prepared else None
    item.prepared_by = user if prepared else None
    item.preparation_version += 1
    item.save(update_fields=["prepared_at", "prepared_by", "preparation_version"])
    _audit(user, profile, item, before, "change")
    return item
