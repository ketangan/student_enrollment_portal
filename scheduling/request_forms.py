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

    def __init__(self, *args, catalog, allow_deferred=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.empty_permitted = False
        self.catalog = catalog
        self.allow_deferred = allow_deferred and any(g.tracks_stock for g in catalog)
        if self.allow_deferred:
            self.fields["selections_deferred"] = forms.BooleanField(label="Choose my design at the studio", required=False)
        deferred = self.allow_deferred and self.is_bound and forms.BooleanField(required=False).to_python(
            self.data.get(self.add_prefix("selections_deferred")))
        if deferred:
            self.fields["name"].required = False
            self.fields["name"].widget.attrs.pop("required", None)
        for group in catalog:
            field = forms.ChoiceField(label=group.name, choices=[(str(o.pk), o.label) for o in group.active_options], widget=forms.RadioSelect)
            if deferred:
                field.required = False
            self.fields[f"choice_{group.pk}"] = field

    def clean(self):
        data = super().clean()
        if not self.allow_deferred and self.data.get(self.add_prefix("selections_deferred")):
            raise forms.ValidationError("Choose a design before continuing.")
        if data.get("selections_deferred") and not data.get("name"):
            index = self.prefix.rsplit("-", 1)[-1] if self.prefix else "0"
            data["name"] = f"Participant {int(index) + 1}" if index.isdigit() else "Participant"
        return data

    def participant_snapshot(self):
        return {"selections": self.snapshot(), "selections_deferred": bool(self.cleaned_data.get("selections_deferred"))}

    def selection_fields(self):
        return [{"field": self[f"choice_{g.pk}"], "group": g, "options": g.active_options} for g in self.catalog]

    def snapshot(self):
        result = []
        for group in self.catalog:
            if self.cleaned_data.get("selections_deferred") and (group.tracks_stock or not self.cleaned_data.get(f"choice_{group.pk}")):
                continue
            option = next(o for o in group.active_options if str(o.pk) == self.cleaned_data[f"choice_{group.pk}"])
            snapshot = {"group_id": group.pk, "group": group.name, "option_id": option.pk, "option": option.label}
            if group.catalog_id:
                snapshot.update(swatch=option.swatch, image_url=option.image_url)
            result.append(snapshot)
        return result


def request_forms(session, catalog, data=None):
    if data is not None and "choose_when" in data:
        data = data.copy()
        for index in range(MAX_PARTICIPANTS + 1):
            data[f"participants-{index}-selections_deferred"] = "on" if data.get("choose_when") == "later" else ""
    return participant_forms(session.capacity, catalog, data,
        allow_deferred=session.offering.allow_deferred_selections and bool(session.offering.inventory_catalog_id))


def participant_forms(capacity, catalog, data=None, *, allow_deferred=False):
    limit = min(MAX_PARTICIPANTS, capacity)
    factory = formset_factory(ParticipantForm, extra=0, min_num=1, validate_min=True,
                              max_num=limit, validate_max=True, absolute_max=MAX_PARTICIPANTS + 1)
    return PurchaserForm(data), factory(data, prefix="participants", form_kwargs={"catalog": catalog, "allow_deferred": allow_deferred})
