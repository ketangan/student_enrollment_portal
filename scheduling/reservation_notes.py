"""Staff notes with scoped edits, conflict detection and an immutable audit trail."""
import uuid

from django import forms
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q

from .access import get_staff_reservation, require_location_access
from .lessons import audit, digest
from .models import ReservationNote, SchedulingProfile


class ReservationNotesForm(forms.Form):
    body = forms.CharField(label="Add a note", max_length=3000,
        widget=forms.Textarea(attrs={"rows": 2}))
    submission_id = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)
    note_key = forms.CharField(required=False, max_length=40, widget=forms.HiddenInput)
    edit_revision = forms.CharField(required=False, max_length=64, widget=forms.HiddenInput)


def form_for(item, profile, data=None, *, note=None):
    initial = {"note_key": note["key"], "body": note["body"],
        "edit_revision": digest(note["body"])} if note else {}
    return ReservationNotesForm(data, initial=initial, auto_id=f"note_{item.pk}_%s")


def editable_notes(item):
    """Uses the roster's prefetched notes; no per-booking queries there."""
    result = []
    if item.internal_notes:
        result.append({"key": "reservation", "body": item.internal_notes})
    if item.party_request_id and item.party_request.preparation_notes:
        result.append({"key": "party", "body": item.party_request.preparation_notes})
    result.extend({"key": str(note.pk), "body": note.body} for note in item.notes.all())
    if item.party_request_id:
        result.extend({"key": str(note.pk), "body": note.body} for note in item.party_request.staff_notes.all())
    return result


def edit(user, profile, item, form, *, inquiry=False):
    key, body = form.cleaned_data["note_key"], form.cleaned_data["body"]
    owner, field = None, "body"
    if key == "reservation" and not inquiry:
        owner, field = item, "internal_notes"
    elif key == "party" and (inquiry or item.party_request_id):
        owner, field = (item if inquiry else item.party_request), "preparation_notes"
    elif key.isdecimal() and len(key) <= 18:
        owners = Q(party_request=item) if inquiry else Q(reservation=item)
        if not inquiry and item.party_request_id:
            owners |= Q(party_request_id=item.party_request_id)
        owner = ReservationNote.objects.filter(owners, pk=int(key)).first()
    if owner is None:
        form.add_error(None, "This note is no longer available. Reload before editing.")
        return
    before = getattr(owner, field)
    if before == body:
        return  # A repeated save must not add another audit entry.
    if not before or form.cleaned_data["edit_revision"] != digest(before):
        form.add_error(None, "This note changed in another window. Reload before saving.")
        return
    setattr(owner, field, body)
    owner.save(update_fields=[field])
    audit(user, profile, item, "change", {"action": "internal_notes_updated",
        "note_key": key, "before": before, "after": body})


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
        if form.cleaned_data["note_key"]:
            edit(user, profile, item, form, inquiry=inquiry)
            return form
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
