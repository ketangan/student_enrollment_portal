from datetime import timedelta, timezone as datetime_timezone
from decimal import Decimal
from functools import partial
import re
from zoneinfo import ZoneInfo

from django import forms
from django.core.validators import MaxValueValidator
from django.utils import timezone

from .models import Offering, PartyPackage, PartyRequest, PartySlot, Room
from .inventory_forms import party_material_forms, material_snapshot
from .access import staff_locations
from .reservation_forms import ReservationFilterForm
from .party_status import FILTERS
from .participant_policy import AgeRangeField, age_policy, estimated_participants, estimate_upper_count
from .party_policy import party_policy
from .booking_policies import party_acknowledgments


class PartyListFilterForm(ReservationFilterForm):
    state = forms.ChoiceField(label="Status", required=False, choices=[("", "All statuses"),
        *[(value, label) for value, (label, _) in FILTERS.items()]])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("prep", "offering"):
            self.fields.pop(name)
        self.fields["hide_completed"].label = "Hide completed parties"


class PartyInquiryForm(forms.Form):
    participant_estimate = forms.CharField(label="Estimated participants", max_length=32, required=False,
        validators=[estimated_participants], widget=forms.TextInput(attrs={"placeholder": "e.g. 7-9 guests"}),
        help_text="Estimate only. The exact booked count determines price and reserved capacity.")
    organizer_name = forms.CharField(label="Your name", max_length=100)
    organizer_email = forms.EmailField(label="Email", max_length=254)
    organizer_phone = forms.CharField(label="Phone", max_length=40, required=False,
        widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}))
    starts_at = forms.DateTimeField(label="Preferred date and time", widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    group_size = forms.IntegerField(label="Number of participants", min_value=1, max_value=10000,
        widget=forms.NumberInput(attrs={"step": "1", "inputmode": "numeric"}),
        error_messages={"invalid": "Enter a whole number of participants."})
    space = forms.ChoiceField(label="Space preference", choices=PartyRequest.Space.choices)
    customer_notes = forms.CharField(label="Anything we should know?", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, offering=None, exclude_reservation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.exclude_reservation = exclude_reservation
        self.age_policy = age_policy(offering.location.profile.school) if offering else None
        policy = party_policy(offering.location.profile.school) if offering else {}
        self.packages_only = policy.get("packages_only", False)
        maximum = policy.get("maximum_participants", 10000)
        self.fields["participant_estimate"].validators = [
            validator for validator in self.fields["participant_estimate"].validators
            if validator is not estimated_participants]
        self.fields["participant_estimate"].validators.append(partial(estimated_participants, maximum=maximum))
        self.fields["group_size"].validators.append(MaxValueValidator(maximum))
        self.fields["group_size"].widget.attrs["max"] = maximum
        self.acknowledgments = party_acknowledgments(offering.location.profile.school) if offering else {}
        self.acknowledgment_names = []
        for kind in ("waiver", "attendance", "policy"):
            if self.acknowledgments:
                name = f"{kind}_acknowledged"
                self.fields[name] = forms.BooleanField(label=self.acknowledgments[kind], required=True,
                    error_messages={"required": "Please confirm this acknowledgement before continuing to payment."})
                self.acknowledgment_names.append(name)
        if self.packages_only:
            self.fields["participant_estimate"].help_text = ""
            self.fields["participant_count_mode"] = forms.ChoiceField(
                choices=[("exact", "Exact count"), ("estimate", "Guest range")], required=False,
                initial="exact", widget=forms.RadioSelect, label="Plushie makers")
            if self.is_bound and self.data.get("participant_count_mode") in {"exact", "estimate"}:
                self.data = self.data.copy()
                if self.data["participant_count_mode"] == "estimate":
                    self.fields["participant_estimate"].required = True
                    self.fields["group_size"].required = False
                    try:
                        count = estimate_upper_count(self.data.get("participant_estimate", ""), maximum=maximum)
                    except forms.ValidationError:
                        count = None
                    self.data["group_size"] = str(count) if count else ""
                else:
                    self.data["participant_estimate"] = ""
        self.package_mode = self.packages_only or bool(offering and offering.party_packages.exists())
        self.guest_mode = True
        self.material_forms = (party_material_forms(offering, self.data if self.is_bound else None, guest_mode=True)
                               if offering and not self.package_mode else None)
        self.order_fields(["space", "starts_at", "organizer_name", "organizer_email", "organizer_phone", "group_size", "participant_estimate", "customer_notes"])
        if self.package_mode:
            packages = offering.party_packages.filter(is_published=True).select_related("offering__location")
            if self.packages_only:
                packages = packages.filter(booking_mode="direct")
            self.fields["package"] = forms.ModelChoiceField(queryset=packages,
                label="Party", required=self.packages_only,
                empty_label="Choose a package" if self.packages_only else "Custom group / request a quote")
            self.fields["package"].label_from_instance = lambda package: (
                f"{package.name} (up to {package.participant_limit}) - ${package.total:,.2f}"
                if package.total is not None else f"{package.name} (up to {package.participant_limit})")
            self.package_details = {
                str(package.pk): {"direct": package.booking_mode == "direct",
                    "name": package.name, "included": package.participant_limit,
                    "extra_limit": package.additional_participant_limit,
                    "extra_cents": int(package.additional_participant_price * 100),
                    "price_cents": int(package.total * 100), "deposit_cents": int(package.deposit * 100),
                    "tax_rate": str(package.effective_tax_rate or 0), "observers": package.included_observers,
                    "duration": package.duration_minutes, "text": (
                    f"Up to {package.participant_limit} participants, {package.duration_minutes} minutes. "
                    f"${package.total} package price. "
                    + (f"${package.deposit} deposit." if package.deposit else "Full payment.")
                    + (f" Up to {package.additional_participant_limit} extra participants at ${package.additional_participant_price} each." if package.additional_participant_limit else "")
                    + f" {package.included_observers} observers included. {package.get_space_display()}."
                )}
                for package in self.fields["package"].queryset
            }
            self.fields["occasion"] = forms.CharField(max_length=150, label="Occasion")
            self.fields["occasion"].widget.attrs["list"] = "party-occasions"
            if self.packages_only:
                self.fields["occasion"].widget.attrs.pop("list", None)
            self.fields["birthday_name"] = forms.CharField(max_length=100, required=False, label="Birthday person's name")
            self.fields["birthday_age"] = forms.IntegerField(min_value=self.age_policy["minimum"] if self.age_policy else 0, max_value=130, required=False, label="Age they're turning")
            self.fields["age_range"] = AgeRangeField(minimum=self.age_policy["minimum"] if self.age_policy else None,
                max_length=100, required=False, label="Participants' ages",
                widget=forms.TextInput(attrs={"placeholder": "e.g. 8, 7-9, or Adults"}))
            self.fields["space"].required = False
            self.fields["starts_at"].required = False
            if self.packages_only:
                for name in ("space", "starts_at"):
                    self.fields[name].disabled = True
                    self.fields[name].widget = forms.HiddenInput()
            from django.db.models import DateTimeField, ExpressionWrapper, F
            from .reservations import available_party_slots
            slots = PartySlot.objects.filter(package__offering=offering, package__is_published=True,
                    package__booking_mode="direct", is_published=True, starts_at__gt=timezone.now() + timedelta(minutes=40))
            slots = slots.annotate(slot_end=ExpressionWrapper(
                F("starts_at") + F("package__duration_minutes") * timedelta(minutes=1), output_field=DateTimeField()))
            slots = available_party_slots(slots, offering.location_id, exclude=exclude_reservation)
            self.fields["party_slot"] = forms.ModelChoiceField(label="Available party time", required=False,
                queryset=slots, error_messages={"invalid_choice": "This time is no longer available. Choose another party time."},
                empty_label="Choose a time")
            local_timezone = ZoneInfo(offering.location.timezone_name)
            self.fields["party_slot"].label_from_instance = lambda slot: timezone.localtime(slot.starts_at, local_timezone).strftime("%a, %b %d, %Y at %I:%M %p %Z")
            self.slot_packages = {str(slot.pk): str(slot.package_id) for slot in self.fields["party_slot"].queryset}
            self.slot_dates = {str(slot.pk): {"date": timezone.localtime(slot.starts_at, local_timezone).strftime("%Y-%m-%d"),
                "time": timezone.localtime(slot.starts_at, local_timezone).strftime("%I:%M %p %Z")}
                for slot in self.fields["party_slot"].queryset}
            self.order_fields(["package", "space", "starts_at", "party_slot", "organizer_name", "organizer_email", "organizer_phone", "group_size", "participant_estimate", "occasion", "birthday_name", "birthday_age", "age_range", "customer_notes"])

    def clean(self):
        data = super().clean()
        if self.package_mode:
            if not re.search(r"\bbirthday\b", data.get("occasion", ""), re.IGNORECASE):
                data["birthday_name"], data["birthday_age"] = "", None
            package = data.get("package")
            if package:
                slot = data.get("party_slot")
                if package.booking_mode == "direct":
                    if not slot or slot.package_id != package.pk:
                        self.add_error("party_slot", "Choose an available time for this party.")
                    else:
                        data["starts_at"] = slot.starts_at
                    if not package.room_id or package.effective_tax_rate is None:
                        self.add_error("package", "This party is not ready for online booking. Please contact the studio.")
                else:
                    data["party_slot"] = None
                data["duration_minutes"] = package.duration_minutes
                data["space"] = package.space
                if data.get("group_size"):
                    try:
                        quote = package.quote(data["group_size"])
                    except forms.ValidationError as exc:
                        self.add_error("participant_estimate" if data.get("participant_count_mode") == "estimate" else "group_size", exc)
                    else:
                        data["package_snapshot"] = quote
                        data["proposed_total"], data["proposed_deposit"] = Decimal(quote["total"]), package.deposit
                        data["room"] = package.room
                if package.booking_mode == "direct" and package.room_id and slot and data.get("group_size"):
                    from .reservations import ensure_party_capacity
                    try:
                        ensure_party_capacity(package.room, slot.starts_at,
                            slot.starts_at + timedelta(minutes=package.duration_minutes), data["group_size"],
                            exclusive=package.space != "shared", space=package.space, exclude=self.exclude_reservation)
                    except forms.ValidationError as exc:
                        self.add_error("party_slot", exc)
            elif "package" not in self.errors and not data.get("space"):
                self.add_error("space", "Required for a custom group request.")
            if not package:
                data["party_slot"] = None
            if not self.packages_only and (not package or package.booking_mode != "direct") and not data.get("starts_at"):
                self.add_error("starts_at", "Choose a preferred date and time.")
        if self.material_forms is not None:
            if not self.material_forms.is_valid():
                raise forms.ValidationError("Check the guest selections below.")
            data["materials"] = material_snapshot(self.material_forms, data.get("group_size"))
        return data

    def clean_organizer_phone(self):
        value = self.cleaned_data["organizer_phone"]
        if value and (sum(char.isdigit() for char in value) < 7
                      or any(not char.isdigit() and char not in "+(). -" for char in value)):
            raise forms.ValidationError("Enter a phone number, including the area or country code.")
        return value

    def clean_starts_at(self):
        value = self.cleaned_data["starts_at"]
        if not value:
            return value
        if value <= timezone.now():
            raise forms.ValidationError("Choose a future date and time.")
        if value > timezone.now() + timedelta(days=366):
            raise forms.ValidationError("Choose a date within the next year.")
        return value

    def inquiry_values(self):
        values = self.cleaned_data.copy()
        for name in self.acknowledgment_names:
            values.pop(name, None)
        values.pop("participant_count_mode", None)
        values.pop("selections_now", None)
        duration = values.pop("duration_minutes", None)
        # Use elapsed minutes across daylight-saving boundaries.
        values["ends_at"] = values["starts_at"].astimezone(datetime_timezone.utc) + timedelta(minutes=duration) if duration else None
        return values

    @property
    def acknowledgment_fields(self):
        return [self[name] for name in self.acknowledgment_names]

    def acknowledgment_record(self):
        if not self.acknowledgments:
            return {}
        package = self.cleaned_data.get("package")
        observers = package.included_observers if package else 0
        return {"revision": self.acknowledgments["revision"],
                "source_url": self.acknowledgments["source_url"],
                "accepted_at": timezone.now().isoformat(),
                "items": {kind: {"accepted": bool(self.cleaned_data[f"{kind}_acknowledged"]),
                    "text": self.acknowledgments[kind].replace("{observers}", str(observers))}
                    for kind in ("waiver", "attendance", "policy")}}


class PartySlotForm(forms.ModelForm):
    class Meta:
        model = PartySlot
        fields = ["starts_at"]
        labels = {"starts_at": "Party start time"}
        widgets = {"starts_at": forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"})}

    def clean_starts_at(self):
        value = self.cleaned_data["starts_at"]
        if not timezone.now() + timedelta(minutes=40) < value <= timezone.now() + timedelta(days=366):
            raise forms.ValidationError("Choose a time more than 40 minutes ahead and within the next year.")
        return value


class PartyPackageForm(forms.ModelForm):
    class Meta:
        model = PartyPackage
        fields = ["offering", "name", "participant_limit", "duration_minutes", "total", "deposit", "space", "is_published",
                  "room", "included_observers", "additional_participant_limit", "additional_participant_price", "tax_rate", "booking_mode"]
        labels = {"name": "Package name", "offering": "Workshop / location", "participant_limit": "Participants included",
                  "duration_minutes": "Duration (minutes)", "total": "Package price (USD)",
                  "deposit": "Deposit (USD, optional)", "space": "Space included", "is_published": "Published",
                  "room": "Room / table", "included_observers": "Observers included",
                  "additional_participant_limit": "Extra participants allowed", "additional_participant_price": "Price per extra participant (USD)",
                  "tax_rate": "Sales tax (%)", "booking_mode": "Booking method"}

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy = party_policy(profile.school)
        if not self.instance.pk:
            self.initial["additional_participant_limit"] = self.policy["default_extra_participants"]
            self.initial["additional_participant_price"] = self.policy["default_extra_price"]
        if self.policy["packages_only"]:
            self.initial["booking_mode"] = "direct"
            self.fields["booking_mode"].disabled = True
            self.fields["booking_mode"].widget = forms.HiddenInput()
        self.fields["offering"].queryset = Offering.objects.filter(location__in=staff_locations(profile), visit_program__isnull=True).select_related("location")
        self.fields["offering"].label_from_instance = lambda obj: f"{obj.name} ({obj.location.name})"
        if self.instance.pk:
            self.fields["offering"].disabled = True
        offering_id = self.instance.offering_id if self.instance.pk else self.data.get("offering") if self.is_bound else None
        self.fields["room"].queryset = Room.objects.filter(location__in=staff_locations(profile)).select_related("location")
        self.fields["room"].empty_label = "Assign during review"
        if self.policy["packages_only"]:
            self.fields["room"].empty_label = "Choose a room / table"
        if str(offering_id or "").isdecimal():
            self.fields["room"].queryset = self.fields["room"].queryset.filter(location__offerings__pk=offering_id)
        elif self.is_bound:
            self.fields["room"].queryset = self.fields["room"].queryset.none()
        for name in ("included_observers", "additional_participant_limit", "additional_participant_price", "booking_mode", "room", "tax_rate"):
            self.fields[name].required = False
            # Older callers omit these fields; preserve the saved configuration.
            if self.is_bound and name not in self.data:
                self.fields[name].disabled = True
        self.fields["deposit"].required = False
        self.fields["tax_rate"].help_text = "Leave blank to use the location's default tax rate. Enter 0 for no tax."

    def clean_booking_mode(self):
        return self.cleaned_data.get("booking_mode") or "review"

    def clean_included_observers(self):
        return self.cleaned_data.get("included_observers") or 0

    def clean_additional_participant_limit(self):
        return self.cleaned_data.get("additional_participant_limit") or 0

    def clean_additional_participant_price(self):
        return self.cleaned_data.get("additional_participant_price") or Decimal(0)

    def clean_deposit(self):
        return self.cleaned_data.get("deposit") or Decimal(0)

    def clean(self):
        data = super().clean()
        if not self.instance.pk and "save_action" in self.data:
            action = self.data.get("save_action")
            if action not in {"draft", "publish"}:
                raise forms.ValidationError("Choose Save Draft or Save and Publish.")
            data["is_published"] = action == "publish"
        if self.policy["packages_only"] and data.get("is_published"):
            if not data.get("room"):
                self.add_error("room", "Choose a room before publishing this package.")
            offering = data.get("offering")
            if data.get("tax_rate") is None and (not offering or offering.location.default_tax_rate is None):
                self.add_error("tax_rate", "Set a location default or enter a tax rate (0 if no tax applies) before publishing.")
        return data


def package_editor_context(form, profile, selected_location=None):
    """Supply scoped choices for the editor after server-side validation has finished."""
    offerings = list(form.fields["offering"].queryset)
    candidates = [offering for offering in offerings if not selected_location or offering.location_id == selected_location.pk]
    if not form.is_bound and len(candidates) == 1:
        form.initial["offering"] = candidates[0].pk
    # An invalid submission can change workshops without losing the other room options.
    form.fields["room"].queryset = Room.objects.filter(location__in=staff_locations(profile)).select_related("location")
    return {"package_offerings": {str(offering.pk): {"location": offering.location_id,
            "tax_rate": str(offering.location.default_tax_rate) if offering.location.default_tax_rate is not None else None} for offering in offerings},
        "package_rooms": {str(room.pk): {"location": room.location_id} for room in form.fields["room"].queryset}}


class PartyReviewForm(forms.ModelForm):
    duration_minutes = forms.IntegerField(label="Duration (minutes)", min_value=15, max_value=720, required=False)
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=0)

    class Meta:
        model = PartyRequest
        fields = ["status", "room", "proposed_total", "proposed_deposit"]
        labels = {"room": "Proposed room", "proposed_total": "Proposed total (USD)",
                  "proposed_deposit": "Deposit per reservation (USD)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["room"].queryset = Room.objects.filter(location=self.instance.offering.location)
        self.fields["revision"].initial = self.instance.revision
        if self.instance.starts_at and self.instance.ends_at:
            self.fields.pop("duration_minutes")
        else:
            self.fields["duration_minutes"].label = "Set duration (minutes)"
        self.material_forms = (None if self.instance.offering.party_packages.exists() else
            party_material_forms(self.instance.offering, self.data if self.is_bound else None, self.instance.materials))

    def clean(self):
        data = super().clean()
        duration = data.get("duration_minutes")
        if duration:
            self.instance.ends_at = self.instance.starts_at.astimezone(datetime_timezone.utc) + timedelta(minutes=duration)
        if data.get("status") == PartyRequest.Status.REVIEWED and not self.instance.ends_at:
            self.add_error("duration_minutes", "Set the duration before completing staff review.")
        if self.material_forms is not None:
            if not self.material_forms.is_valid():
                raise forms.ValidationError("Check the party materials below.")
            self.instance.materials = material_snapshot(self.material_forms, self.instance.group_size)
        total, deposit = data.get("proposed_total"), data.get("proposed_deposit")
        if deposit is not None and 0 < deposit < Decimal("0.50"):
            self.add_error("proposed_deposit", "Enter 0 or a deposit of at least $0.50.")
        elif deposit and total is not None and total - deposit < Decimal("0.50"):
            self.add_error("proposed_deposit", "Leave at least $0.50 to pay later, or enter 0 for the deposit.")
        if (total is None) != (deposit is None):
            if total is None and "proposed_total" not in self.errors:
                self.add_error("proposed_total", "Enter the total price for this party.")
            elif deposit is None and "proposed_deposit" not in self.errors:
                self.add_error("proposed_deposit", "Enter the deposit amount, or 0 if no deposit is required.")
        if total is not None and deposit is not None and deposit > total:
            self.add_error("proposed_deposit", "The deposit cannot exceed the total.")
        if data.get("status") == PartyRequest.Status.REVIEWED and self.instance.space in (PartyRequest.Space.EXCLUSIVE, PartyRequest.Space.STUDIO) and not data.get("room"):
            self.add_error("room", "Choose a proposed room before marking a private-room inquiry reviewed.")
        return data
