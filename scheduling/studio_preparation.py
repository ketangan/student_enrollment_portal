"""Read-only, location-scoped preparation using the existing reservation records."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.db.models import Prefetch, Q
from django.utils import timezone

from .access import scope_reservations
from .models import ChoiceGroup, Reservation, StockAllocation
from .reservation_queries import reservation_location
from .reservation_workspace import participant_context, participant_table, preparation_materials, selection_summary


class PreparationRangeForm(forms.Form):
    date = forms.DateField(label="Starting", widget=forms.DateInput(attrs={"type": "date"}))
    period = forms.ChoiceField(choices=(("day", "Day"), ("week", "Week"), ("month", "Month")))

    def clean_date(self):
        value = self.cleaned_data["date"]
        if value.year >= 9999:
            raise forms.ValidationError("Choose a date before the year 9999.")
        return value


def summary(profile, locations, start, days, *, for_print=False, offering_ids=None):
    now = timezone.now()
    groups = {}
    if for_print:
        for group in ChoiceGroup.objects.filter(
            Q(catalog__profile=profile) | Q(offering__location__profile=profile), is_active=True):
            key = ("catalog", group.catalog_id) if group.catalog_id else ("offering", group.offering_id)
            groups.setdefault(key, []).append(group)
    bounds, predicate = {}, Q(pk__in=[])
    for location in locations:
        zone = ZoneInfo(location.timezone_name)
        lower = datetime.combine(start, time.min, zone)
        upper = datetime.combine(start + timedelta(days=days), time.min, zone)
        bounds[location.pk] = (lower, upper, location)
        scheduled = (Q(camp_registration__isnull=True, starts_at__gte=lower, starts_at__lt=upper)
            | Q(camp_registration__isnull=False, occurrences__starts_at__gte=lower, occurrences__starts_at__lt=upper))
        predicate |= reservation_location(location) & scheduled
    items = scope_reservations(Reservation.objects.filter(profile=profile,
        state__in=["confirmed", "completed"] if for_print else ["confirmed"],
        visit_booking__isnull=True), profile).filter(predicate).distinct().select_related(
        "workshop_request__session__offering__location", "party_request__offering__location",
        "camp_registration__camp__offering__location", "room").prefetch_related(
        "workshop_request__participants", "occurrences", "notes",
        Prefetch("stock_allocations", queryset=StockAllocation.objects.select_related(
            "level__material", "level__location"), to_attr="prep_allocations"))
    studios = {}
    for item in items:
        source = item.workshop_request or item.party_request or item.camp_registration
        offering = (source.session.offering if item.workshop_request_id else
            source.camp.offering if item.camp_registration_id else source.offering)
        if offering_ids is not None and offering.pk not in offering_ids:
            continue
        lower, upper, location = bounds[offering.location_id]
        studio = studios.setdefault(location.pk, {"location": location, "entries": [], "totals": {}})
        allocations = preparation_materials(item)
        used = any(row.consumed_at for row in allocations)
        participants = participant_context(item)
        notes = [item.internal_notes] if item.internal_notes else []
        if item.party_request_id and source.preparation_notes:
            notes.append(source.preparation_notes)
        notes.extend(note.body for note in item.notes.all())
        table = participants["participant_table"]
        choices = "; ".join(f'{group["label"]}: {group["value"]}'
            for group in selection_summary(participants["participants"]))
        deferred = sum(bool(person.get("selections_deferred")) for person in participants["participants"])
        if deferred:
            choices += ("; " if choices else "") + f"{deferred} choosing at the studio"
        # Unnamed attendees still need a row for staff to write in their choices.
        print_table = table
        if for_print:
            key = ("catalog", offering.inventory_catalog_id) if offering.inventory_catalog_id else ("offering", offering.pk)
            people = participants["participants"] or [{"name": str(i + 1), "selections": []} for i in range(item.quantity)]
            print_table = participant_table([{**person, "selections_deferred": False} for person in people], groups.get(key, []))
        print_rows = print_table["rows"]
        schedule = item.occurrences.all() if item.camp_registration_id else [item]
        for occurrence in schedule:
            if lower <= occurrence.starts_at < upper:
                studio["entries"].append({"item": item, **participants,
                    "starts_at": occurrence.starts_at, "ends_at": occurrence.ends_at,
                    "is_past": occurrence.ends_at < now,
                    "materials_used": used, "notes": notes,
                    "print_rows": print_rows, "print_columns": print_table["columns"],
                    "selection_summary": choices})
        # Camp allocations belong to the whole reservation, not each attendance date.
        if not item.prepared_at and not used and item.state != "completed" and (not for_print or item.ends_at >= now):
            for row in allocations:
                key = (row.level.material_id, row.material_unit)
                total = studio["totals"].setdefault(key, {"name": row.material_name,
                    "unit": row.material_unit, "quantity": 0})
                total["quantity"] += row.quantity
    result = sorted(studios.values(), key=lambda studio: studio["location"].name)
    for studio in result:
        studio["entries"].sort(key=lambda entry: (entry["starts_at"], entry["item"].pk))
        studio["totals"] = sorted(studio["totals"].values(), key=lambda row: row["name"])
        studio["participants"] = sum(entry["item"].quantity for entry in studio["entries"])
        studio["pending"] = sum(not entry["item"].prepared_at and entry["item"].state != "completed"
            and not entry["materials_used"] and (not for_print or not entry["is_past"])
            for entry in studio["entries"])
    return result
