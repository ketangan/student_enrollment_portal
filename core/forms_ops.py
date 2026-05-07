"""
ModelForms for the /ops/ superadmin portal.
Using ModelForm means new model fields auto-appear without code changes.
"""
import json

from django import forms
from django.contrib.auth.models import User

from core.models import School

_INPUT_STYLE = (
    "width:100%;padding:8px 10px;border:1px solid var(--dash-border,#e2e8f0);"
    "border-radius:6px;font-size:13px;box-sizing:border-box;"
    "background:var(--dash-bg,#fff);color:var(--dash-text,#0f172a);"
)
_TEXTAREA_STYLE = _INPUT_STYLE + "resize:vertical;"
_SELECT_CLASS = "dash-select"


def _apply_dash_attrs(form):
    """Apply consistent dash styling to all visible widgets after form init."""
    for name, field in form.fields.items():
        w = field.widget
        if isinstance(w, forms.CheckboxInput):
            continue
        elif isinstance(w, forms.Textarea):
            existing = w.attrs.get("style", "")
            w.attrs["style"] = _TEXTAREA_STYLE + existing
        elif isinstance(w, (forms.Select, forms.SelectMultiple)):
            w.attrs["class"] = w.attrs.get("class", "") + " " + _SELECT_CLASS
            w.attrs["style"] = "width:100%;box-sizing:border-box;"
        else:
            w.attrs["style"] = _INPUT_STYLE


class OpsSchoolCreateForm(forms.ModelForm):
    class Meta:
        model = School
        fields = [
            "slug", "display_name", "website_url",
            "plan", "is_active",
            "trial_end_date",
            "feature_flags",
        ]
        widgets = {
            "feature_flags": forms.Textarea(attrs={"rows": 6, "style": "font-family:monospace;font-size:12px;"}),
            "trial_end_date": forms.DateInput(attrs={"type": "date"}),
        }
        help_texts = {
            "slug": "URL-safe identifier, e.g. 'young-minds-la'. Cannot be changed after creation.",
            "feature_flags": "JSON overrides on top of plan defaults. Leave {} unless you need per-school overrides.",
            "trial_end_date": "Override the default trial length for this school.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_dash_attrs(self)

    def clean_slug(self):
        slug = self.cleaned_data.get("slug", "").strip().lower()
        if not slug:
            raise forms.ValidationError("Slug is required.")
        if School.objects.filter(slug=slug).exists():
            raise forms.ValidationError(f"A school with slug '{slug}' already exists.")
        return slug

    def clean_feature_flags(self):
        v = self.cleaned_data.get("feature_flags")
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (ValueError, TypeError):
                raise forms.ValidationError("Invalid JSON.")
        if not isinstance(v, dict):
            raise forms.ValidationError("Must be a JSON object.")
        return v


class OpsSchoolEditForm(forms.ModelForm):
    class Meta:
        model = School
        fields = [
            "display_name", "website_url",
            "plan", "is_active",
            "trial_end_date",
            "feature_flags",
            "stripe_customer_id", "stripe_subscription_id",
            "stripe_subscription_status",
            "stripe_cancel_at_period_end", "stripe_current_period_end",
        ]
        widgets = {
            "feature_flags": forms.Textarea(attrs={"rows": 8, "style": "font-family:monospace;font-size:12px;"}),
            "trial_end_date": forms.DateInput(attrs={"type": "date"}),
            "stripe_current_period_end": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
        help_texts = {
            "feature_flags": "JSON overrides on top of plan defaults. Leave {} to use plan defaults.",
            "trial_end_date": "Superadmin override for trial end. Clears itself when plan changes off trial.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_dash_attrs(self)

    def clean_feature_flags(self):
        v = self.cleaned_data.get("feature_flags")
        if v is None:
            return {}
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (ValueError, TypeError):
                raise forms.ValidationError("Invalid JSON.")
        if not isinstance(v, dict):
            raise forms.ValidationError("Must be a JSON object.")
        return v


class OpsUserCreateForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput,
        min_length=8,
        help_text="Minimum 8 characters.",
    )
    password_confirm = forms.CharField(
        widget=forms.PasswordInput,
        label="Confirm password",
    )
    school = forms.ModelChoiceField(
        queryset=School.objects.order_by("display_name", "slug"),
        required=False,
        empty_label="— No school (superuser / staff only) —",
        help_text="Assigns this user as a school admin. Leave blank for superusers.",
    )

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "is_staff", "is_superuser"]
        help_texts = {
            "is_staff": "Allows login to Django admin.",
            "is_superuser": "Full access to everything including the ops portal.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Render school labels as "Display Name (slug)" for clarity
        self.fields["school"].label_from_instance = lambda s: (
            f"{s.display_name} ({s.slug})" if s.display_name else s.slug
        )
        _apply_dash_attrs(self)

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get("password")
        pw2 = cleaned.get("password_confirm")
        if pw and pw2 and pw != pw2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned


class OpsUserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "is_active", "is_staff", "is_superuser"]
        help_texts = {
            "is_staff": "Allows login to Django admin.",
            "is_superuser": "Full access to everything including the ops portal.",
            "is_active": "Uncheck to deactivate (block login) without deleting.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _apply_dash_attrs(self)
