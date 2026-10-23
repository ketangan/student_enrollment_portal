import uuid
from datetime import date
from decimal import Decimal
from django import forms
from django.core.validators import MaxValueValidator
from .models import Location, Reservation
from .access import staff_locations


class ReservationFilterForm(forms.Form):
    q = forms.CharField(label="Customer or workshop", required=False, max_length=100,
                        widget=forms.TextInput(attrs={"type": "search", "placeholder": "Name, email or workshop"}))
    location = forms.ModelChoiceField(label="Studio", queryset=Location.objects.none(), required=False, empty_label="All studios")
    state = forms.ChoiceField(label="Status", required=False, choices=[("", "All statuses"), *Reservation.State.choices])
    date_from = forms.DateField(label="From", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="Through", required=False, widget=forms.DateInput(attrs={"type": "date"}),
                              validators=[MaxValueValidator(date(9999, 12, 30))])

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["location"].queryset = staff_locations(profile)

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            self.add_error("date_to", "Choose a date on or after From.")
        return data


class QuoteForm(forms.Form):
    online = forms.BooleanField(required=False, label="Use Stripe sandbox checkout (requires a connected test account)")
    source_revision = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)
    total = forms.DecimalField(label="Total (USD)", min_value=0, max_value=Decimal("999999.99"), decimal_places=2, max_digits=8)
    deposit = forms.DecimalField(label="Deposit (USD; zero means full payment required)", min_value=0, max_value=Decimal("999999.99"), decimal_places=2, max_digits=8, initial=0)
    confirm = forms.BooleanField(label="I approve this quote and the 30-minute capacity hold")

    def clean(self):
        data = super().clean()
        if data.get("deposit") is not None and data.get("total") is not None and data["deposit"] > data["total"]:
            self.add_error("deposit", "The deposit cannot exceed the total.")
        return data


class CancellationForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    resolution = forms.ChoiceField(label="Payment handling", widget=forms.RadioSelect,
        choices=[("without_refund", "Cancel without refund"), ("with_refund", "Cancel and refund remaining payment")])
    reason = forms.CharField(label="Reason for cancellation", max_length=240,
        widget=forms.Textarea(attrs={"rows": 2}))


class RefundForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    attempt = forms.ChoiceField(label="Original payment")
    amount = forms.DecimalField(label="Refund (USD)", min_value=Decimal("0.01"), max_value=Decimal("999999.99"), decimal_places=2, max_digits=8)
    reason = forms.CharField(label="Reason", max_length=240)
    confirm = forms.BooleanField(label="I approve returning this amount to the original payment method")

    def __init__(self, *args, order, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["attempt"].choices = [(str(a.public_id), f"${a.amount:.2f} (payment ...{a.provider_intent[-8:]})")
            for a in order.attempts.filter(state="paid").order_by("created_at")]
