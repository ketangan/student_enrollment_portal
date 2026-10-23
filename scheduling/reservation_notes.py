"""One staff notes editor, retaining party preparation notes as their source."""
from django import forms
from django.db import transaction
from .access import get_staff_reservation
from django.shortcuts import get_object_or_404

from .access import require_location_access
from .editing import protect_edit
from .lessons import audit
from .models import PartyRequest, Reservation, SchedulingProfile


class ReservationNotesForm(forms.ModelForm):
    class Meta:
        model = Reservation
        fields = ["internal_notes"]
        labels = {"internal_notes": "Internal notes"}
        widgets = {"internal_notes": forms.Textarea(attrs={"rows": 3})}


class PartyNotesForm(forms.ModelForm):
    class Meta:
        model = PartyRequest
        fields = ["preparation_notes"]
        labels = {"preparation_notes": "Internal notes"}
        widgets = {"preparation_notes": forms.Textarea(attrs={"rows": 3})}


def form_for(item, profile, data=None):
    form = (PartyNotesForm(data, instance=item.party_request) if item.party_request_id
            else ReservationNotesForm(data, instance=item))
    return protect_edit(form, profile)


@transaction.atomic
def update(user, slug, public_id, data):
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    form = form_for(item, profile, data)
    if form.is_valid() and form.has_changed():
        source = form.save(commit=False)
        fields = list(form._meta.fields)
        if item.party_request_id:
            source.revision += 1
            fields.extend(["revision", "updated_at"])
        source.save(update_fields=fields)
        audit(user, profile, item, "change", {"action": "internal_notes_updated"})
    return form
