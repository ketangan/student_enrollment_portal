"""Reservation-scoped participant follow-up using the existing catalog and stock ledger."""
from collections import Counter
from datetime import timedelta
import uuid

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.services.config_loader import load_school_config
from .inventory_forms import party_material_forms
from .models import Reservation, SchedulingProfile

SALT = "scheduling.guest-list.v1"


def deadline_days(school):
    config = load_school_config(school.slug)
    value = (config.raw if config else {}).get("scheduling", {}).get("guest_list_deadline_days")
    if value is not None and (type(value) is not int or not 0 <= value <= 365):
        raise ValidationError("Guest-list deadline must be a whole number from 0 to 365.")
    return value


def token_for(item):
    return signing.dumps({"reservation": str(item.public_id), "school": item.profile.school.slug}, salt=SALT)


def resolve(slug, token):
    try:
        data = signing.loads(token, salt=SALT, max_age=366 * 86400)
        if data["school"] != slug:
            raise ValueError
        public_id = uuid.UUID(data["reservation"])
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        raise Http404("Guest link is invalid or expired.")
    return get_object_or_404(guest_reservations(), public_id=public_id, profile__school__slug=slug)


def guest_reservations():
    return Reservation.objects.select_related(
        "profile__school", "party_request__offering__location__profile__school",
        "party_request__offering__inventory_catalog").filter(profile__enabled=True,
        party_request__isnull=False, party_request__offering__inventory_catalog__isnull=False)


def for_staff(user, slug, public_id):
    from .access import require_location_access, scope_reservations
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    return get_object_or_404(scope_reservations(guest_reservations(), profile), profile=profile, public_id=public_id)


def editable(item):
    return item.state == "confirmed" and not item.prepared_at and item.starts_at > timezone.now()


def due_at(item):
    days = (item.snapshot["guest_list_deadline_days"] if "guest_list_deadline_days" in item.snapshot
            else deadline_days(item.profile.school))
    return item.starts_at - timedelta(days=days) if days is not None else None


class GuestListForm(forms.Form):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    guest_mode = True

    def __init__(self, *args, item, **kwargs):
        super().__init__(*args, **kwargs)
        self.item = item
        self.fields["revision"].initial = item.party_request.revision
        initial = [dict(row, quantity=1) for row in item.snapshot.get("guest_list_draft", item.party_request.materials)
                   for _ in range(row["quantity"])]
        self.material_forms = party_material_forms(item.party_request.offering,
            self.data if self.is_bound else None, initial=None if self.is_bound else initial, guest_mode=True)
        if not self.is_bound and initial:
            self.material_forms.extra = 0

    def clean(self):
        data = super().clean()
        if not self.material_forms.is_valid():
            raise ValidationError("Check the guest selections below.")
        rows = [{"item_id": row.cleaned_data["item"].pk, "name": row.cleaned_data["item"].name,
                 "quantity": 1, "guest_name": row.cleaned_data.get("guest_name", ""),
                 "guest_age": row.cleaned_data.get("guest_age")}
                for row in self.material_forms if row.cleaned_data and not row.cleaned_data.get("DELETE")]
        if len(rows) > self.item.quantity:
            raise ValidationError("There are more guests than reserved places.")
        data["rows"] = rows
        return data


@transaction.atomic
def save(slug, token, data):
    item = resolve(slug, token)
    SchedulingProfile.objects.select_for_update().get(pk=item.profile_id)
    item = resolve(slug, token)
    return _save(item, data)


@transaction.atomic
def save_for_staff(user, slug, public_id, data):
    item = for_staff(user, slug, public_id)
    SchedulingProfile.objects.select_for_update().get(pk=item.profile_id)
    item = for_staff(user, slug, public_id)
    return _save(item, data, actor=user)


def _save(item, data, actor=None):
    form = GuestListForm(data, item=item)
    if not form.is_valid():
        return item, form
    if not editable(item):
        form.add_error(None, "This guest list is closed. Please contact the studio for changes.")
    elif form.cleaned_data["revision"] != item.party_request.revision:
        form.add_error(None, "The guest list changed in another window. Reload before saving.")
    elif data.get("action") not in {"draft", "submit"}:
        form.add_error(None, "Choose save progress or submit guest list.")
    elif data.get("action") == "submit" and len(form.cleaned_data["rows"]) != item.quantity:
        form.add_error(None, "Add a selection for every guest before submitting.")
    if form.errors:
        return item, form
    rows = form.cleaned_data["rows"]
    source = item.party_request
    if data["action"] == "submit":
        from .inventory import allocate, check_items
        counts = Counter(row["item_id"] for row in rows)
        previous = Counter()
        for row in source.materials:
            previous[row["item_id"]] += row["quantity"]
        # Check first; all replacements and the existing allocation service share this lock.
        if item.stock_allocations.filter(consumed_at__isnull=False).exists():
            raise ValidationError("Materials have already been used. Contact the studio.")
        changed = counts != previous or not item.stock_allocations.exists()
        if changed:
            check_items(source.offering, counts, item.pk)
            item.stock_allocations.all().delete()
        source.materials = rows
        item.snapshot = {**item.snapshot, "guest_selections_pending": False,
                         "guest_list_submitted_at": timezone.now().isoformat()}
        item.snapshot.pop("guest_list_draft", None)
        if changed:
            allocate(item)
    else:
        item.snapshot = {**item.snapshot, "guest_list_draft": rows}
    source.revision += 1
    source.save(update_fields=["materials", "revision"])
    item.save(update_fields=["snapshot"])
    from .reservations import _audit
    _audit(actor, item, "guest_list_submitted" if data["action"] == "submit" else "guest_list_draft_saved")
    return item, form
