from datetime import timedelta, timezone as datetime_timezone

from django import forms
from django.utils import timezone

from .models import PartyRequest, Room
from .inventory_forms import party_material_forms, material_snapshot


class PartyInquiryForm(forms.Form):
    organizer_name = forms.CharField(label="Your name", max_length=100)
    organizer_email = forms.EmailField(label="Email", max_length=254)
    starts_at = forms.DateTimeField(label="Preferred date and time", widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
    duration_minutes = forms.IntegerField(label="Duration (minutes)", min_value=15, max_value=720, initial=120)
    group_size = forms.IntegerField(label="Number of participants", min_value=1, max_value=10000)
    space = forms.ChoiceField(label="Space preference", choices=PartyRequest.Space.choices)
    customer_notes = forms.CharField(label="Anything we should know? (optional)", max_length=2000, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, offering=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.material_forms = party_material_forms(offering, self.data if self.is_bound else None) if offering else None

    def clean(self):
        data = super().clean()
        if self.material_forms is not None:
            if not self.material_forms.is_valid():
                raise forms.ValidationError("Check the party materials below.")
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
        duration = values.pop("duration_minutes")
        # Use elapsed minutes across daylight-saving boundaries.
        values["ends_at"] = values["starts_at"].astimezone(datetime_timezone.utc) + timedelta(minutes=duration)
        return values


class PartyReviewForm(forms.ModelForm):
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=0)

    class Meta:
        model = PartyRequest
        fields = ["status", "room", "proposed_total", "proposed_deposit", "preparation_notes"]
        labels = {"room": "Proposed room", "proposed_total": "Proposed total (USD)",
                  "proposed_deposit": "Proposed deposit (USD)", "preparation_notes": "Internal preparation notes"}
        widgets = {"preparation_notes": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["room"].queryset = Room.objects.filter(location=self.instance.offering.location)
        self.fields["revision"].initial = self.instance.revision
        self.material_forms = party_material_forms(self.instance.offering, self.data if self.is_bound else None,
                                                   self.instance.materials)

    def clean(self):
        data = super().clean()
        if self.material_forms is not None:
            if not self.material_forms.is_valid():
                raise forms.ValidationError("Check the party materials below.")
            self.instance.materials = material_snapshot(self.material_forms, self.instance.group_size)
        total, deposit = data.get("proposed_total"), data.get("proposed_deposit")
        if (total is None) != (deposit is None):
            raise forms.ValidationError("Enter both the proposed total and deposit, or leave both empty.")
        if total is not None and deposit is not None and deposit > total:
            self.add_error("proposed_deposit", "The deposit cannot exceed the total.")
        if data.get("status") == PartyRequest.Status.REVIEWED and self.instance.space == PartyRequest.Space.EXCLUSIVE and not data.get("room"):
            self.add_error("room", "Choose a proposed room before marking a private-room inquiry reviewed.")
        return data
