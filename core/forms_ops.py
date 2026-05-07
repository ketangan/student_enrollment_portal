"""
ModelForms for the /ops/ superadmin portal.
Using ModelForm means new model fields auto-appear without code changes.
"""
from django import forms
from django.contrib.auth.models import User

from core.models import School


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
            import json
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

    def clean_feature_flags(self):
        v = self.cleaned_data.get("feature_flags")
        if v is None:
            return {}
        if isinstance(v, str):
            import json
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

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name", "is_staff", "is_superuser"]
        help_texts = {
            "is_staff": "Allows login to Django admin.",
            "is_superuser": "Full access to everything including the ops portal.",
        }

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
