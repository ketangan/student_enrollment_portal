from django import forms
from django.forms import formset_factory

from .models import ChoiceGroup, ChoiceOption

MAX_PARTICIPANTS = 20


class ChoiceGroupForm(forms.ModelForm):
    class Meta:
        model = ChoiceGroup
        fields = ["name", "reference_image", "position", "is_active"]
        labels = {"name": "Choice name", "reference_image": "Reference image (optional)", "position": "Display order", "is_active": "Available to customers"}


class ChoiceOptionForm(forms.ModelForm):
    class Meta:
        model = ChoiceOption
        fields = ["label", "swatch", "image_url", "position", "is_active"]
        labels = {"label": "Option label", "swatch": "Color hex (optional)", "image_url": "Image URL (optional)", "position": "Display order", "is_active": "Available to customers"}


class PurchaserForm(forms.Form):
    purchaser_name = forms.CharField(label="Your name", max_length=100, widget=forms.TextInput(attrs={"autocomplete": "name"}))
    purchaser_email = forms.EmailField(label="Email", max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class ParticipantForm(forms.Form):
    name = forms.CharField(label="Participant name", max_length=100, widget=forms.TextInput(attrs={"required": True}))

    def __init__(self, *args, catalog, **kwargs):
        super().__init__(*args, **kwargs)
        self.empty_permitted = False
        self.catalog = catalog
        for group in catalog:
            field = forms.ChoiceField(label=group.name, choices=[(str(o.pk), o.label) for o in group.active_options], widget=forms.RadioSelect)
            self.fields[f"choice_{group.pk}"] = field

    def selection_fields(self):
        return [{"field": self[f"choice_{g.pk}"], "group": g, "options": g.active_options} for g in self.catalog]

    def snapshot(self):
        result = []
        for group in self.catalog:
            option = next(o for o in group.active_options if str(o.pk) == self.cleaned_data[f"choice_{group.pk}"])
            snapshot = {"group_id": group.pk, "group": group.name, "option_id": option.pk, "option": option.label}
            if group.catalog_id:
                snapshot.update(swatch=option.swatch, image_url=option.image_url)
            result.append(snapshot)
        return result


def request_forms(session, catalog, data=None):
    return participant_forms(session.capacity, catalog, data)


def participant_forms(capacity, catalog, data=None):
    limit = min(MAX_PARTICIPANTS, capacity)
    factory = formset_factory(ParticipantForm, extra=0, min_num=1, validate_min=True,
                              max_num=limit, validate_max=True, absolute_max=MAX_PARTICIPANTS + 1)
    return PurchaserForm(data), factory(data, prefix="participants", form_kwargs={"catalog": catalog})
