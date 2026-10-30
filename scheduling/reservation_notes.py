"""Append staff notes without replacing earlier comments or customer data."""
import uuid

from django import forms
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q

from .access import get_staff_reservation, require_location_access
from .lessons import audit
from .models import ReservationNote, SchedulingProfile


class ReservationNotesForm(forms.Form):
    body = forms.CharField(label="Add a note", max_length=3000,
        widget=forms.Textarea(attrs={"rows": 2}))
    submission_id = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)


def form_for(item, profile, data=None):
    return ReservationNotesForm(data)


def context_for(item, raw_page=None, *, inquiry=False):
    if inquiry:
        legacy = [item.preparation_notes] if item.preparation_notes else []
        notes = item.staff_notes.all()
    else:
        legacy = [item.internal_notes] if item.internal_notes else []
        owners = Q(reservation=item)
        if item.party_request_id:
            owners |= Q(party_request_id=item.party_request_id)
        notes = ReservationNote.objects.filter(owners)
    if not inquiry and item.party_request_id and item.party_request.preparation_notes:
        legacy.append(item.party_request.preparation_notes)
    return {"legacy_notes": legacy,
        "staff_notes": Paginator(notes.select_related("actor"), 20).get_page(raw_page),
        "notes_timezone": item.offering_snapshot.get("timezone") if inquiry else item.snapshot.get("timezone")}


@transaction.atomic
def update(user, slug, public_id, data, *, inquiry=False):
    capability = "parties" if inquiry else "reservations"
    profile, _ = require_location_access(user, slug, "sessions.edit", capability=capability)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability=capability)
    if inquiry:
        from django.shortcuts import get_object_or_404
        from .parties import scoped_parties
        item = get_object_or_404(scoped_parties(profile), public_id=public_id)
    else:
        item = get_staff_reservation(profile, public_id, exclude_visits=True)
    form = form_for(item, profile, data)
    if form.is_valid():
        note, created = ReservationNote.objects.get_or_create(**{"party_request" if inquiry else "reservation": item},
            submission_id=form.cleaned_data["submission_id"],
            defaults={"actor": user, "body": form.cleaned_data["body"]})
        if created:
            if inquiry:
                from .parties import audit_party
                audit_party(user, profile, item, {"action": "internal_note_added"})
            else:
                audit(user, profile, item, "change", {"action": "internal_note_added"})
        elif note.body != form.cleaned_data["body"] or note.actor_id != user.pk:
            form.add_error(None, "This note was already submitted. Reload to add another note.")
    return form
