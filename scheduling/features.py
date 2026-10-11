"""Audited feature grants; normal workspace owners cannot grant themselves products."""

from django import forms
from django.db import transaction
from django.forms.models import model_to_dict
from django.http import Http404

from .access import require_access
from .capabilities import CAPABILITIES, validate_capabilities, validate_removal
from .editing import protect_edit
from .models import SchedulingProfile
from .setup import _audit


class FeatureForm(forms.ModelForm):
    capabilities = forms.MultipleChoiceField(
        label="Available features", choices=CAPABILITIES.items(), required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = SchedulingProfile
        fields = ["capabilities"]

    def clean_capabilities(self):
        selected = self.cleaned_data["capabilities"]
        validate_capabilities(selected)
        validate_removal(self.instance, selected)
        return [key for key in CAPABILITIES if key in selected]


def operator_profile(user, slug):
    if not user.is_authenticated or not user.is_active or not user.is_superuser:
        raise Http404("Page not found")
    return require_access(user, slug, "setup.edit")[0]


def feature_form(profile, data=None):
    return protect_edit(FeatureForm(data, instance=profile), profile)


@transaction.atomic
def save_features(user, slug, data):
    profile = operator_profile(user, slug)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile = operator_profile(user, slug)
    before = model_to_dict(profile)
    form = feature_form(profile, data)
    if form.is_valid():
        profile = form.save(commit=False)
        # Do not leave latent on-switches behind after a permitted removal.
        if "lessons" not in profile.capabilities:
            profile.lessons_enabled = False
        if "reservations" not in profile.capabilities:
            profile.native_reservations_enabled = False
        profile.save(update_fields=["capabilities", "lessons_enabled", "native_reservations_enabled"])
        _audit(user, profile, profile, before, "change")
    return form
