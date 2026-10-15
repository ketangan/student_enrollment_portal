from django import forms

from core.forms_ops import _apply_dash_attrs


class SchoolPricingForm(forms.Form):
    label = forms.CharField(label="Agreement name", max_length=100)
    base_price_id = forms.CharField(label="Base subscription Price ID", max_length=255)
    included_locations = forms.IntegerField(min_value=1, max_value=999, initial=1)
    location_price_id = forms.CharField(label="Additional location Price ID", required=False, max_length=255)
    additional_locations = forms.IntegerField(label="Additional locations to bill", min_value=0, max_value=999, initial=0)
    expected_offer_id = forms.CharField(required=False, widget=forms.HiddenInput)
    verification = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_dash_attrs(self)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("additional_locations") and not cleaned.get("location_price_id"):
            self.add_error("location_price_id", "Enter a price for the additional locations being billed.")
        return cleaned
