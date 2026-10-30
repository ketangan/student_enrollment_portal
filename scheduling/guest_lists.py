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
from .inventory_forms import PartyMaterialRow, party_material_forms
from .models import CatalogVariant, Reservation, SchedulingProfile

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


def editable(item, *, staff=False):
    return item.state == "confirmed" and (staff or (not item.prepared_at and item.starts_at > timezone.now()))


def saved_rows(item):
    return [{**row_snapshot({}), **row, "quantity": 1} for row in item.snapshot.get("guest_list_draft", item.party_request.materials)
            for _ in range(row.get("quantity", 1))]


def row_snapshot(data):
    variant = data.get("item")
    return {"item_id": variant.pk if variant else None, "name": variant.name if variant else "",
        "quantity": 1, "guest_name": data.get("guest_name", ""), "guest_age": data.get("guest_age")}


class StaffGuestForm(PartyMaterialRow):
    revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, item, index, **kwargs):
        rows = saved_rows(item)
        row = rows[index] if index < len(rows) else {}
        super().__init__(*args, offering=item.party_request.offering, guest_mode=True, allow_incomplete=True,
            initial={"item": row.get("item_id"), "guest_name": row.get("guest_name", ""),
                     "guest_age": row.get("guest_age"), "revision": item.party_request.revision},
            auto_id=f"guest_{index}_%s", **kwargs)

    def clean(self):
        data = super().clean()
        if self.separate_choices and bool(data.get("design")) != bool(data.get("color")):
            raise ValidationError("Choose both a design and color, or leave both empty.")
        return data


def staff_context(request, item, *, can_edit=False, bound_form=None, editing=None):
    """Render all saved guests with one catalog lookup, creating only the active row form."""
    rows = saved_rows(item)
    groups = list(item.party_request.offering.inventory_catalog.choice_groups.filter(
        tracks_stock=True, is_active=True).prefetch_related("options"))
    separate = len(groups) == 2
    columns = [group.name for group in groups] if separate else ["Selection"]
    labels = {option.pk: option.label for group in groups for option in group.options.all()}
    variants = {variant.pk: variant for variant in CatalogVariant.objects.filter(
        catalog_id=item.party_request.offering.inventory_catalog_id, pk__in=[row.get("item_id") for row in rows])}
    query = request.GET.copy()
    query.pop("edit_guest", None)
    base = request.path + ("?" + query.urlencode() if query else "")
    editing = str(editing) if editing is not None else request.GET.get("edit_guest", "")
    writable = can_edit and editable(item, staff=True) and not item.stock_allocations.filter(consumed_at__isnull=False).exists()
    result = []
    active_form = None
    for index in range(min(item.quantity, 50)):
        row = rows[index] if index < len(rows) else row_snapshot({})
        variant = variants.get(row.get("item_id"))
        if separate:
            values = [next((labels[option.pk] for option in group.options.all()
                       if variant and option.pk in variant.option_ids), "Not selected") for group in groups]
        else:
            values = [row.get("name") or "Not selected"]
        query["edit_guest"] = index
        entry = {"index": index, "name": row.get("guest_name") or f"Guest {index + 1}",
            "age": row.get("guest_age"), "values": values, "selected": bool(row.get("item_id")),
            "edit_url": request.path + "?" + query.urlencode() + "#party-guests" if writable else ""}
        if writable and editing == str(index):
            active_form = bound_form if bound_form is not None else StaffGuestForm(item=item, index=index)
            for field in active_form.fields.values():
                field.widget.attrs["form"] = "party-guest-form"
                field.widget.attrs["aria-label"] = field.label or "Guest name"
            entry.update(form=active_form, choice_fields=[active_form[name] for name in
                (("design", "color") if separate else ("item",))])
        result.append(entry)
    return {"party_guest_rows": result, "party_guest_columns": columns, "party_guest_form": active_form,
        "party_guest_editing": editing, "party_guest_cancel": base + "#party-guests",
        "party_guest_selected_count": sum(bool(row.get("item_id")) for row in rows),
        "party_guest_draft": "guest_list_draft" in item.snapshot}


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
        initial = saved_rows(item)
        self.material_forms = party_material_forms(item.party_request.offering,
            self.data if self.is_bound else None, initial=None if self.is_bound else initial, guest_mode=True, allow_incomplete=True)
        if not self.is_bound and initial:
            self.material_forms.extra = 0

    def clean(self):
        data = super().clean()
        if not self.material_forms.is_valid():
            raise ValidationError("Check the guest selections below.")
        for row in self.material_forms:
            if row.cleaned_data and not row.cleaned_data.get("DELETE") and row.separate_choices:
                if bool(row.cleaned_data.get("design")) != bool(row.cleaned_data.get("color")):
                    row.add_error(None, "Choose both a design and color, or leave both empty.")
                    raise ValidationError("Check the guest selections below.")
        rows = [row_snapshot(row.cleaned_data)
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
    if data.get("action") not in {"draft", "submit"}:
        form.add_error(None, "Choose save progress or submit guest list.")
        return item, form
    if not form.cleaned_data["rows"] and data.get("action") == "submit":
        form.add_error(None, "Add at least one guest before submitting.")
        return item, form
    return _persist(item, form, form.cleaned_data["rows"], actor=actor, submit=data["action"] == "submit")


@transaction.atomic
def save_guest(user, slug, public_id, index, data):
    item = for_staff(user, slug, public_id)
    SchedulingProfile.objects.select_for_update().get(pk=item.profile_id)
    item = for_staff(user, slug, public_id)
    if not 0 <= index < min(item.quantity, 50):
        raise Http404("Guest not found")
    form = StaffGuestForm(data, item=item, index=index)
    if not form.is_valid():
        return item, form
    rows = saved_rows(item)
    while len(rows) <= index:
        rows.append(row_snapshot({}))
    rows[index] = row_snapshot(form.cleaned_data)
    return _persist(item, form, rows, actor=user, submit=True)


def _persist(item, form, rows, *, actor=None, submit=False):
    if not editable(item, staff=actor is not None):
        form.add_error(None, "This guest list is closed. Please contact the studio for changes.")
    elif form.cleaned_data["revision"] != item.party_request.revision:
        form.add_error(None, "The guest list changed in another window. Reload before saving.")
    elif item.stock_allocations.filter(consumed_at__isnull=False).exists():
        form.add_error(None, "Materials have already been used. Guest details cannot be changed.")
    if form.errors:
        return item, form
    source = item.party_request
    changed_rows = rows != saved_rows(item)
    complete = len(rows) == item.quantity and all(row.get("item_id") for row in rows)
    submitted = submit and complete
    if submitted:
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
            check_items(source.offering, counts, item.pk, starts_at=item.starts_at)
            item.stock_allocations.all().delete()
        source.materials = rows
        item.snapshot = {**item.snapshot, "guest_selections_pending": False,
                         "guest_list_submitted_at": timezone.now().isoformat()}
        item.snapshot.pop("guest_list_draft", None)
        if changed:
            allocate(item)
    else:
        item.snapshot = {**item.snapshot, "guest_list_draft": rows}
    if actor and item.prepared_at and changed_rows:
        item.prepared_at = None
        item.prepared_by = None
        item.preparation_version += 1
    source.revision += 1
    source.save(update_fields=["materials", "revision"])
    item.save(update_fields=["snapshot", "prepared_at", "prepared_by", "preparation_version"])
    from .reservations import _audit
    _audit(actor, item, "guest_list_submitted" if submitted else "guest_list_draft_saved")
    return item, form
