"""Append staff notes without replacing earlier comments or customer data."""
import uuid

from django import forms
from django.core.paginator import Paginator
from django.db import transaction

from .access import get_staff_reservation, require_location_access
from .lessons import audit
from .models import ReservationNote, SchedulingProfile


class ReservationNotesForm(forms.Form):
    body = forms.CharField(label="Add a note", max_length=3000,
        widget=forms.Textarea(attrs={"rows": 2}))
    submission_id = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)


def form_for(item, profile, data=None):
    return ReservationNotesForm(data)


def context_for(item, raw_page=None):
    legacy = [item.internal_notes] if item.internal_notes else []
    if item.party_request_id and item.party_request.preparation_notes:
        legacy.append(item.party_request.preparation_notes)
    return {"legacy_notes": legacy,
        "staff_notes": Paginator(item.notes.select_related("actor"), 20).get_page(raw_page)}


@transaction.atomic
def update(user, slug, public_id, data):
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    form = form_for(item, profile, data)
    if form.is_valid():
        note, created = ReservationNote.objects.get_or_create(reservation=item,
            submission_id=form.cleaned_data["submission_id"],
            defaults={"actor": user, "body": form.cleaned_data["body"]})
        if created:
            audit(user, profile, item, "change", {"action": "internal_note_added"})
        elif note.body != form.cleaned_data["body"] or note.actor_id != user.pk:
            form.add_error(None, "This note was already submitted. Reload to add another note.")
    return form
