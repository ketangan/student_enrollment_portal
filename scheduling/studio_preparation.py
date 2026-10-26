"""Read-only, location-scoped preparation using the existing reservation records."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.db.models import Prefetch, Q

from .access import scope_reservations
from .models import Reservation, StockAllocation
from .reservation_queries import reservation_location
from .reservation_workspace import participant_context, preparation_materials


class PreparationRangeForm(forms.Form):
    date = forms.DateField(label="Starting", widget=forms.DateInput(attrs={"type": "date"}))
    period = forms.ChoiceField(choices=(("day", "Day"), ("week", "Week")))

    def clean_date(self):
        value = self.cleaned_data["date"]
        if value.year >= 9999:
            raise forms.ValidationError("Choose a date before the year 9999.")
        return value


def summary(profile, locations, start, days):
    bounds, predicate = {}, Q(pk__in=[])
    for location in locations:
        zone = ZoneInfo(location.timezone_name)
        lower = datetime.combine(start, time.min, zone)
        upper = datetime.combine(start + timedelta(days=days), time.min, zone)
        bounds[location.pk] = (lower, upper, location)
        scheduled = (Q(camp_registration__isnull=True, starts_at__gte=lower, starts_at__lt=upper)
            | Q(camp_registration__isnull=False, occurrences__starts_at__gte=lower, occurrences__starts_at__lt=upper))
        predicate |= reservation_location(location) & scheduled
    items = scope_reservations(Reservation.objects.filter(profile=profile, state="confirmed",
        visit_booking__isnull=True), profile).filter(predicate).distinct().select_related(
        "workshop_request__session__offering__location", "party_request__offering__location",
        "camp_registration__camp__offering__location").prefetch_related(
        "workshop_request__participants", "occurrences",
        Prefetch("stock_allocations", queryset=StockAllocation.objects.select_related(
            "level__material", "level__location"), to_attr="prep_allocations"))
    studios = {}
    for item in items:
        source = item.workshop_request or item.party_request or item.camp_registration
        offering = (source.session.offering if item.workshop_request_id else
            source.camp.offering if item.camp_registration_id else source.offering)
        lower, upper, location = bounds[offering.location_id]
        studio = studios.setdefault(location.pk, {"location": location, "entries": [], "totals": {}})
        allocations = preparation_materials(item)
        used = any(row.consumed_at for row in allocations)
        participants = participant_context(item)
        schedule = item.occurrences.all() if item.camp_registration_id else [item]
        for occurrence in schedule:
            if lower <= occurrence.starts_at < upper:
                studio["entries"].append({"item": item, **participants,
                    "starts_at": occurrence.starts_at, "ends_at": occurrence.ends_at,
                    "materials_used": used})
        # Camp allocations belong to the whole reservation, not each attendance date.
        if not item.prepared_at and not used:
            for row in allocations:
                key = (row.level.material_id, row.material_unit)
                total = studio["totals"].setdefault(key, {"name": row.material_name,
                    "unit": row.material_unit, "quantity": 0})
                total["quantity"] += row.quantity
    result = sorted(studios.values(), key=lambda studio: studio["location"].name)
    for studio in result:
        studio["entries"].sort(key=lambda entry: (entry["starts_at"], entry["item"].pk))
        studio["totals"] = sorted(studio["totals"].values(), key=lambda row: row["name"])
    return result
