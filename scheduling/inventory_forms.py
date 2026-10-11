import re

from django import forms
from .models import ChoiceGroup, ChoiceOption, InventoryCatalog, CatalogVariant, Material, MaterialRequirement
from .inventory_models import validate_amount, validate_catalog_image
from .request_forms import ChoiceGroupForm, ChoiceOptionForm


class CatalogForm(forms.ModelForm):
    class Meta:
        model = InventoryCatalog
        fields = ["name"]


class ColorPickerWidget(forms.TextInput):
    template_name = "scheduling/widgets/color_picker.html"

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context["widget"]["picker_value"] = value if re.fullmatch(r"#[0-9a-fA-F]{6}", value or "") else "#ffffff"
        return context


class CatalogColorForm(ChoiceOptionForm):
    is_color_option = True

    class Meta(ChoiceOptionForm.Meta):
        fields = ["label", "swatch", "position", "is_active"]
        labels = {**ChoiceOptionForm.Meta.labels, "label": "Color name", "swatch": "Color"}
        widgets = {"swatch": ColorPickerWidget(attrs={
            "placeholder": "#RRGGBB", "pattern": "#[0-9a-fA-F]{6}",
            "autocomplete": "off", "spellcheck": "false",
        })}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["swatch"].required = True


class CatalogGroupForm(ChoiceGroupForm):
    class Meta(ChoiceGroupForm.Meta):
        fields = [*ChoiceGroupForm.Meta.fields, "tracks_stock"]
        labels = {**ChoiceGroupForm.Meta.labels, "tracks_stock": "Changes the material recipe (for example, design or color)"}


class CatalogVariantForm(forms.ModelForm):
    class Meta:
        model = CatalogVariant
        fields = ["name", "is_active"]
        labels = {"name": "Design / color name", "is_active": "Available for new bookings"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.groups = list(self.instance.catalog.choice_groups.filter(tracks_stock=True, is_active=True))
        for group in self.groups:
            field = forms.ModelChoiceField(label=group.name, queryset=ChoiceOption.objects.filter(group=group),
                                           disabled=bool(self.instance.pk))
            field.initial = next((pk for pk in self.instance.option_ids if field.queryset.filter(pk=pk).exists()), None)
            self.fields[f"group_{group.pk}"] = field

    def clean(self):
        data = super().clean()
        self.instance.option_ids = sorted(data[f"group_{g.pk}"].pk for g in self.groups if data.get(f"group_{g.pk}"))
        return data


class StockCountForm(forms.Form):
    mode = forms.ChoiceField(choices=[("receive", "Receive delivery"), ("count", "Correct stock count")])
    on_hand = forms.DecimalField(label="Amount", min_value=0, max_value=1000000, max_digits=10, decimal_places=3)
    previous = forms.DecimalField(widget=forms.HiddenInput, max_digits=10, decimal_places=3)
    reason = forms.CharField(label="Reason", max_length=250)
    low_stock_threshold = forms.DecimalField(label="Low-stock alert at", required=False,
        min_value=0, max_value=1000000, max_digits=10, decimal_places=3,
        help_text="Available stock at or below this amount is low. Use 0 to turn off the alert.")
    previous_threshold = forms.DecimalField(required=False, widget=forms.HiddenInput, max_digits=10, decimal_places=3)


class MaterialForm(forms.ModelForm):
    class Meta:
        model = Material
        fields = ["name", "sku", "unit", "is_active"]
        labels = {"name": "Material name", "sku": "Material code", "unit": "Stock unit", "is_active": "Use in new recipes"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["unit"].disabled = True


class RecipeRow(forms.ModelForm):
    class Meta:
        model = MaterialRequirement
        fields = ["material", "quantity"]

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].queryset = Material.objects.filter(profile=profile)
        self.fields["material"].label_from_instance = lambda m: f"{m.name} ({m.unit})"
        self.fields["material"].widget.attrs["aria-label"] = "Raw material"
        self.fields["quantity"].widget.attrs["aria-label"] = "Amount for one participant"

    def clean(self):
        data = super().clean()
        if data.get("material") and data.get("quantity") is not None:
            validate_amount(data["quantity"], data["material"].unit)
            if not data["material"].is_active:
                self.add_error("material", "Choose an active material.")
        return data


def recipe_forms(variant, profile, data=None):
    factory = forms.inlineformset_factory(CatalogVariant, MaterialRequirement, form=RecipeRow,
        extra=1, min_num=1, validate_min=True, max_num=30, validate_max=True, absolute_max=61, can_delete=True)
    return factory(data, instance=variant, prefix="recipe", form_kwargs={"profile": profile})


class DesignForm(ChoiceOptionForm):
    colors = forms.ModelMultipleChoiceField(queryset=ChoiceOption.objects.none(), widget=forms.CheckboxSelectMultiple,
        label="Available colors")

    class Meta(ChoiceOptionForm.Meta):
        fields = ["label", "image_url", "is_active"]
        labels = {"label": "Design name", "image_url": "Photo URL", "is_active": "Show to customers"}

    def __init__(self, *args, catalog, **kwargs):
        super().__init__(*args, **kwargs)
        groups = list(catalog.choice_groups.filter(tracks_stock=True, is_active=True))
        self.fields["colors"].queryset = ChoiceOption.objects.filter(group=groups[1], is_active=True) if len(groups) == 2 else ChoiceOption.objects.none()
        if self.instance.pk:
            self.fields["colors"].initial = [pk for v in catalog.items.filter(is_active=True)
                if self.instance.pk in v.option_ids for pk in v.option_ids if pk != self.instance.pk]
        variants = [v for v in catalog.items.all() if self.instance.pk in v.option_ids] if self.instance.pk else []
        self.color_rows = []
        selected = {str(pk) for pk in (self["colors"].value() or [])}
        for color in self.fields["colors"].queryset:
            key = f"image_{color.pk}"
            variant = next((v for v in variants if color.pk in v.option_ids), None)
            self.fields[key] = forms.CharField(label=f"{color.label} photo URL", required=False, max_length=500,
                validators=[validate_catalog_image], initial=variant.image_url if variant else "",
                widget=forms.TextInput(attrs={"placeholder": "https://...", "inputmode": "url"}))
            self.color_rows.append({"color": color, "selected": str(color.pk) in selected, "image": self[key]})

    @property
    def design_fields(self):
        return [self[name] for name in self.Meta.fields]


class PartyMaterialRow(forms.Form):
    item = forms.ModelChoiceField(queryset=CatalogVariant.objects.none(), label="Design / color")
    quantity = forms.IntegerField(min_value=1, max_value=10000, label="Participants")

    def __init__(self, *args, offering, **kwargs):
        super().__init__(*args, **kwargs)
        self.offering = offering
        self.fields["item"].queryset = CatalogVariant.objects.filter(catalog_id=offering.inventory_catalog_id, is_active=True)
        groups = list(offering.inventory_catalog.choice_groups.filter(tracks_stock=True, is_active=True)) if offering.inventory_catalog_id else []
        self.separate_choices = len(groups) == 2
        if self.separate_choices:
            previous = self.fields["item"].queryset.filter(pk=self.initial.get("item")).first()
            del self.fields["item"]
            for key, group in zip(("design", "color"), groups):
                self.fields[key] = forms.ModelChoiceField(queryset=group.options.filter(is_active=True), label=group.name)
                self.fields[key].initial = next((pk for pk in previous.option_ids if group.options.filter(pk=pk).exists()), None) if previous else None
            self.order_fields(["design", "color", "quantity"])

    def clean(self):
        data = super().clean()
        if self.separate_choices and data.get("design") and data.get("color"):
            key = ":".join(map(str, sorted([data["design"].pk, data["color"].pk])))
            variant = CatalogVariant.objects.filter(catalog_id=self.offering.inventory_catalog_id, choice_key=key, is_active=True).first()
            if not variant:
                raise forms.ValidationError("This design is not available in that color.")
            data["item"] = variant
        return data


def party_material_forms(offering, data=None, initial=None):
    if not offering.inventory_catalog_id:
        return None
    factory = forms.formset_factory(PartyMaterialRow, extra=1, max_num=50, validate_max=True, absolute_max=51, can_delete=True)
    return factory(data, initial=[{"item": row["item_id"], "quantity": row["quantity"]} for row in initial or []],
                   prefix="materials", form_kwargs={"offering": offering})


def material_snapshot(formset, group_size):
    if formset is None:
        return []
    rows = [{"item_id": row.cleaned_data["item"].pk, "name": row.cleaned_data["item"].name,
             "quantity": row.cleaned_data["quantity"]}
            for row in formset if row.cleaned_data and not row.cleaned_data.get("DELETE")]
    if rows and sum(row["quantity"] for row in rows) != group_size:
        raise forms.ValidationError("Materials must total the number of participants, or leave all rows empty to choose later.")
    return rows
