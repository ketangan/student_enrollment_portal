from datetime import timedelta, timezone as datetime_timezone
from decimal import Decimal
from zoneinfo import available_timezones

from django import forms

from .models import InventoryCatalog, LessonAvailability, LessonSeries, Location, Offering, PartyRequest, Room, Session
from .capabilities import has_capability
from .access import staff_locations


class LocationForm(forms.ModelForm):
    timezone_name = forms.ChoiceField(label="Time zone", choices=[(name, name.replace("_", " ")) for name in sorted(available_timezones())])

    class Meta:
        model = Location
        fields = ["name", "address", "timezone_name"]
        labels = {"name": "Location name"}

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.profile = profile
        self.fields["timezone_name"].initial = self.instance.timezone_name
        if self.instance.pk and (Session.objects.filter(offering__location=self.instance).exists() or PartyRequest.objects.filter(offering__location=self.instance).exists() or LessonAvailability.objects.filter(location=self.instance).exists() or LessonSeries.objects.filter(location=self.instance).exists()):
            self.fields["timezone_name"].disabled = True


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["name", "location", "capacity"]
        labels = {"name": "Room name", "capacity": "Room capacity"}

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = staff_locations(profile)
        self.fields["location"].disabled = bool(self.instance.pk)


class OfferingForm(forms.ModelForm):
    payment_at_booking = forms.ChoiceField(label="Payment at booking", required=False,
        choices=[("full", "Full payment"), ("deposit", "Deposit")])

    class Meta:
        model = Offering
        fields = ["name", "location", "description", "inventory_catalog", "allow_deferred_selections", "payment_at_booking", "deposit_per_participant"]
        labels = {"name": "Workshop name", "inventory_catalog": "Catalog",
                  "deposit_per_participant": "Deposit per participant (USD)",
                  "allow_deferred_selections": "Allow participants to choose designs at the studio"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, profile, unit_price_cents=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.unit_price_cents = unit_price_cents
        self.initial["payment_at_booking"] = "deposit" if self.instance.deposit_per_participant else "full"
        self.fields["deposit_per_participant"].required = False
        self.fields["location"].queryset = staff_locations(profile)
        self.fields["location"].disabled = bool(self.instance.pk)
        if not has_capability(profile, "parties"):
            self.instance.accepts_party_requests = False
        self.fields["inventory_catalog"].queryset = InventoryCatalog.objects.filter(profile=profile)
        # Existing uncatalogued bookings must keep their original selection source.
        legacy_choices = self.instance.pk and not self.instance.inventory_catalog_id and (
            self.instance.sessions.filter(requests__isnull=False).exists()
            or self.instance.party_requests.exists()
            or self.instance.camps.filter(registrations__isnull=False).exists()
        )
        if legacy_choices or not self.fields["inventory_catalog"].queryset.exists():
            del self.fields["inventory_catalog"]
        else:
            self.fields["inventory_catalog"].required = True

    def clean(self):
        data = super().clean()
        if "payment_at_booking" not in self.data and "deposit_per_participant" not in self.data:
            data["deposit_per_participant"] = self.instance.deposit_per_participant
            return data
        if data.get("payment_at_booking") == "full":
            data["deposit_per_participant"] = Decimal(0)
        else:
            deposit = data.get("deposit_per_participant")
            if deposit is None or deposit < Decimal("0.50"):
                self.add_error("deposit_per_participant", "Enter a deposit of at least $0.50 per participant.")
            elif self.unit_price_cents is not None and int(deposit * 100) > self.unit_price_cents - 50:
                self.add_error("deposit_per_participant", "The deposit must leave at least $0.50 per participant to pay later. Choose full payment instead.")
        return data


class SessionForm(forms.ModelForm):
    class Meta:
        model = Session
        fields = ["starts_at", "ends_at", "room", "capacity"]
        labels = {"starts_at": "Starts", "ends_at": "Ends", "room": "Room (optional)", "capacity": "Participant capacity"}
        widgets = {name: forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}) for name in ["starts_at", "ends_at"]}

    def __init__(self, *args, offering, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.offering = offering
        self.fields["room"].queryset = Room.objects.filter(location=offering.location)


class WorkshopSessionForm(SessionForm):
    duration_minutes = forms.IntegerField(label="Duration (min)", min_value=1, initial=120)

    class Meta:
        model = Session
        fields = ["starts_at", "duration_minutes", "room", "capacity"]
        labels = {"starts_at": "Start time", "room": "Room (optional)", "capacity": "Capacity"}
        widgets = {"starts_at": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"})}

    def __init__(self, *args, offering, **kwargs):
        super().__init__(*args, offering=offering, **kwargs)
        self._original_start = self.instance.starts_at
        self._original_end = self.instance.ends_at
        self._original_duration = None
        if self._original_start and self._original_end:
            elapsed = self._original_end.astimezone(datetime_timezone.utc) - self._original_start.astimezone(datetime_timezone.utc)
            self._original_duration = max(1, round(elapsed.total_seconds() / 60))
            self.initial["duration_minutes"] = self._original_duration

    def clean(self):
        cleaned = super().clean()
        start, duration = cleaned.get("starts_at"), cleaned.get("duration_minutes")
        # A minute-resolution editor must not alter legacy seconds on a capacity-only edit.
        unchanged_start = start and self._original_start and start == self._original_start.replace(second=0, microsecond=0)
        if unchanged_start:
            start = cleaned["starts_at"] = self._original_start
        self.instance.ends_at = self._original_end if unchanged_start else None
        if start and duration:
            if unchanged_start and duration == self._original_duration:
                self.instance.ends_at = self._original_end
            else:
                try:
                    # Duration is elapsed time, including sessions spanning a DST transition.
                    self.instance.ends_at = start.astimezone(datetime_timezone.utc) + timedelta(minutes=duration)
                except OverflowError:
                    self.add_error("duration_minutes", "Enter a shorter duration.")
        return cleaned
