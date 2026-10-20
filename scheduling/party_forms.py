from datetime import timedelta, timezone as datetime_timezone
from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import Offering, PartyPackage, PartyRequest, Room
from .inventory_forms import party_material_forms, material_snapshot


class PartyInquiryForm(forms.Form):
    organizer_name = forms.CharField(label="Your name", max_length=100)
    organizer_email = forms.EmailField(label="Email", max_length=254)
    starts_at = forms.DateTimeField(label="Preferred date and time", widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    group_size = forms.IntegerField(label="Number of participants", min_value=1, max_value=10000)
    space = forms.ChoiceField(label="Space preference", choices=PartyRequest.Space.choices)
    customer_notes = forms.CharField(label="Anything we should know? (optional)", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, offering=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.package_mode = bool(offering and offering.party_packages.exists())
        self.guest_mode = True
        self.material_forms = party_material_forms(offering, self.data if self.is_bound else None, guest_mode=True) if offering else None
        if self.package_mode:
            self.fields["package"] = forms.ModelChoiceField(
                queryset=offering.party_packages.filter(is_published=True),
                required=False, empty_label="Custom group / request a quote")
            self.package_details = {
                str(package.pk): {"text": (
                    f"Up to {package.participant_limit} participants, {package.duration_minutes} minutes. "
                    f"${package.total} package price. "
                    + (f"${package.deposit} deposit." if package.deposit else "Full payment.")
                )}
                for package in self.fields["package"].queryset
            }
            self.fields["occasion"] = forms.CharField(max_length=150, label="Occasion")
            self.fields["age_range"] = forms.CharField(max_length=100, required=False, label="Participants' ages",
                widget=forms.TextInput(attrs={"placeholder": "e.g. 8-12 years, adults, or mixed ages"}))
            if self.material_forms is not None:
                self.fields["selections_now"] = forms.BooleanField(required=False, label="Add design and color selections now")
            self.fields["space"].required = False
            self.order_fields(["package", "organizer_name", "organizer_email", "starts_at", "group_size", "occasion", "age_range", "space", "customer_notes"])

    def clean(self):
        data = super().clean()
        if self.package_mode:
            package = data.get("package")
            if package:
                data["duration_minutes"] = package.duration_minutes
                data["space"] = package.space
                if data.get("group_size", 0) > package.participant_limit:
                    self.add_error("group_size", f"This package includes up to {package.participant_limit} participants. Choose another package or request a custom quote.")
                data["proposed_total"], data["proposed_deposit"] = package.total, package.deposit
                data["package_snapshot"] = {"name": package.name, "participant_limit": package.participant_limit,
                    "duration_minutes": package.duration_minutes, "total": str(package.total), "deposit": str(package.deposit),
                    "space": package.space}
            elif "package" not in self.errors and not data.get("space"):
                self.add_error("space", "Required for a custom group request.")
        if self.material_forms is not None and (not self.package_mode or data.get("selections_now")):
            if not self.material_forms.is_valid():
                raise forms.ValidationError("Check the guest selections below.")
            data["materials"] = material_snapshot(self.material_forms, data.get("group_size"))
        return data

    def clean_starts_at(self):
        value = self.cleaned_data["starts_at"]
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


class PartyPackageForm(forms.ModelForm):
    class Meta:
        model = PartyPackage
        fields = ["offering", "name", "participant_limit", "duration_minutes", "total", "deposit", "space", "is_published"]
        labels = {"offering": "Workshop / location", "participant_limit": "Participants included (maximum)",
                  "duration_minutes": "Duration (minutes)", "total": "Package price (USD)",
                  "deposit": "Deposit (USD, optional)", "space": "Space included", "is_published": "Published"}

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["offering"].queryset = Offering.objects.filter(location__profile=profile, visit_program__isnull=True).select_related("location")
        self.fields["offering"].label_from_instance = lambda obj: f"{obj.name} ({obj.location.name})"
        if self.instance.pk:
            self.fields["offering"].disabled = True
        self.fields["deposit"].required = False

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
        self.material_forms = party_material_forms(self.instance.offering, self.data if self.is_bound else None,
                                                   self.instance.materials)

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
        if data.get("status") == PartyRequest.Status.REVIEWED and self.instance.space == PartyRequest.Space.EXCLUSIVE and not data.get("room"):
            self.add_error("room", "Choose a proposed room before marking a private-room inquiry reviewed.")
        return data
