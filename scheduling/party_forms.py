from datetime import timedelta, timezone as datetime_timezone
from decimal import Decimal
import re

from django import forms
from django.utils import timezone

from .models import Offering, PartyPackage, PartyRequest, PartySlot, Room
from .inventory_forms import party_material_forms, material_snapshot


class PartyInquiryForm(forms.Form):
    organizer_name = forms.CharField(label="Your name", max_length=100)
    organizer_email = forms.EmailField(label="Email", max_length=254)
    organizer_phone = forms.CharField(label="Phone", max_length=40, required=False,
        widget=forms.TextInput(attrs={"type": "tel", "autocomplete": "tel"}))
    starts_at = forms.DateTimeField(label="Preferred date and time", widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    group_size = forms.IntegerField(label="Number of participants", min_value=1, max_value=10000)
    space = forms.ChoiceField(label="Space preference", choices=PartyRequest.Space.choices)
    customer_notes = forms.CharField(label="Anything we should know?", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, offering=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.package_mode = bool(offering and offering.party_packages.exists())
        self.guest_mode = True
        self.material_forms = (party_material_forms(offering, self.data if self.is_bound else None, guest_mode=True)
                               if offering and not self.package_mode else None)
        self.order_fields(["space", "starts_at", "organizer_name", "organizer_email", "organizer_phone", "group_size", "customer_notes"])
        if self.package_mode:
            self.fields["package"] = forms.ModelChoiceField(
                queryset=offering.party_packages.filter(is_published=True),
                label="Party", required=False, empty_label="Custom group / request a quote")
            self.package_details = {
                str(package.pk): {"direct": package.booking_mode == "direct", "text": (
                    f"Up to {package.participant_limit} participants, {package.duration_minutes} minutes. "
                    f"${package.total} package price. "
                    + (f"${package.deposit} deposit." if package.deposit else "Full payment.")
                    + (f" Up to {package.additional_participant_limit} extra participants at ${package.additional_participant_price} each." if package.additional_participant_limit else "")
                    + f" {package.included_observers} observers included. {package.get_space_display()}."
                    + (f" Plus {package.tax_rate}% tax." if package.tax_rate is not None else " Tax to be confirmed.")
                )}
                for package in self.fields["package"].queryset
            }
            self.fields["occasion"] = forms.CharField(max_length=150, label="Occasion")
            self.fields["occasion"].widget.attrs["list"] = "party-occasions"
            self.fields["birthday_name"] = forms.CharField(max_length=100, required=False, label="Birthday person's name")
            self.fields["birthday_age"] = forms.IntegerField(min_value=0, max_value=130, required=False, label="Age they're turning")
            self.fields["age_range"] = forms.CharField(max_length=100, required=False, label="Participants' ages",
                widget=forms.TextInput(attrs={"placeholder": "e.g. 8-12 years, adults, or mixed ages"}))
            self.fields["space"].required = False
            self.fields["starts_at"].required = False
            self.fields["party_slot"] = forms.ModelChoiceField(label="Available party time", required=False,
                queryset=PartySlot.objects.filter(package__offering=offering, package__is_published=True,
                    package__booking_mode="direct", is_published=True, starts_at__gt=timezone.now() + timedelta(minutes=40)),
                empty_label="Choose a time")
            self.fields["party_slot"].label_from_instance = lambda slot: timezone.localtime(slot.starts_at).strftime("%a, %b %d, %Y at %I:%M %p %Z")
            self.slot_packages = {str(slot.pk): str(slot.package_id) for slot in self.fields["party_slot"].queryset}
            self.slot_dates = {str(slot.pk): {"date": timezone.localtime(slot.starts_at).strftime("%Y-%m-%d"),
                "time": timezone.localtime(slot.starts_at).strftime("%I:%M %p %Z")}
                for slot in self.fields["party_slot"].queryset}
            self.order_fields(["package", "space", "starts_at", "party_slot", "organizer_name", "organizer_email", "organizer_phone", "group_size", "occasion", "birthday_name", "birthday_age", "age_range", "customer_notes"])

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
                    if not package.room_id or package.tax_rate is None:
                        self.add_error("package", "This party is not ready for online booking. Please contact the studio.")
                else:
                    data["party_slot"] = None
                data["duration_minutes"] = package.duration_minutes
                data["space"] = package.space
                if data.get("group_size"):
                    try:
                        quote = package.quote(data["group_size"])
                    except forms.ValidationError as exc:
                        self.add_error("group_size", exc)
                    else:
                        data["package_snapshot"] = quote
                        data["proposed_total"], data["proposed_deposit"] = Decimal(quote["total"]), package.deposit
                        data["room"] = package.room
            elif "package" not in self.errors and not data.get("space"):
                self.add_error("space", "Required for a custom group request.")
            if not package:
                data["party_slot"] = None
            if (not package or package.booking_mode != "direct") and not data.get("starts_at"):
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
        values.pop("selections_now", None)
        duration = values.pop("duration_minutes", None)
        # Use elapsed minutes across daylight-saving boundaries.
        values["ends_at"] = values["starts_at"].astimezone(datetime_timezone.utc) + timedelta(minutes=duration) if duration else None
        return values


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
        labels = {"offering": "Workshop / location", "participant_limit": "Participants included (maximum)",
                  "duration_minutes": "Duration (minutes)", "total": "Package price (USD)",
                  "deposit": "Deposit (USD, optional)", "space": "Space included", "is_published": "Published",
                  "room": "Room / table", "included_observers": "Observers included",
                  "additional_participant_limit": "Extra participants allowed", "additional_participant_price": "Price per extra participant (USD)",
                  "tax_rate": "Sales tax (%)", "booking_mode": "Booking method"}

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["offering"].queryset = Offering.objects.filter(location__profile=profile, visit_program__isnull=True).select_related("location")
        self.fields["offering"].label_from_instance = lambda obj: f"{obj.name} ({obj.location.name})"
        if self.instance.pk:
            self.fields["offering"].disabled = True
        offering_id = self.instance.offering_id if self.instance.pk else self.data.get("offering") if self.is_bound else None
        self.fields["room"].queryset = Room.objects.filter(location__profile=profile).select_related("location")
        self.fields["room"].empty_label = "Assign during review"
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


class PartyReviewForm(forms.ModelForm):
    duration_minutes = forms.IntegerField(label="Duration (minutes)", min_value=15, max_value=720, required=False)
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    payment_at_booking = forms.ChoiceField(label="Payment at booking", required=False,
        choices=[("full", "Full payment"), ("deposit", "Deposit")])

    class Meta:
        model = PartyRequest
        fields = ["status", "room", "proposed_total", "payment_at_booking", "proposed_deposit", "preparation_notes"]
        labels = {"room": "Proposed room", "proposed_total": "Proposed total (USD)",
                  "proposed_deposit": "Deposit per reservation (USD)", "preparation_notes": "Internal preparation notes"}
        widgets = {"preparation_notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["room"].queryset = Room.objects.filter(location=self.instance.offering.location)
        self.fields["revision"].initial = self.instance.revision
        if self.instance.starts_at and self.instance.ends_at:
            self.fields["duration_minutes"].initial = int((self.instance.ends_at - self.instance.starts_at).total_seconds() / 60)
        self.initial["payment_at_booking"] = "deposit" if self.instance.proposed_deposit else "full"
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
        if data.get("payment_at_booking") == "full":
            deposit = data["proposed_deposit"] = Decimal(0) if total is not None else None
        elif data.get("payment_at_booking") == "deposit":
            if deposit is None or deposit < Decimal("0.50"):
                self.add_error("proposed_deposit", "Enter a deposit of at least $0.50 per reservation.")
            elif total is not None and deposit > total - Decimal("0.50"):
                self.add_error("proposed_deposit", "Leave at least $0.50 to pay later, or choose full payment.")
        if (total is None) != (deposit is None):
            raise forms.ValidationError("Enter both the proposed total and deposit, or leave both empty.")
        if total is not None and deposit is not None and deposit > total:
            self.add_error("proposed_deposit", "The deposit cannot exceed the total.")
        if data.get("status") == PartyRequest.Status.REVIEWED and self.instance.space in (PartyRequest.Space.EXCLUSIVE, PartyRequest.Space.STUDIO) and not data.get("room"):
            self.add_error("room", "Choose a proposed room before marking a private-room inquiry reviewed.")
        return data
