from django import forms
from django.core.exceptions import ValidationError

from core.services.admin_submission_yaml import get_submission_status_choices
from core.services.config_loader import load_school_config
from .editing import EditRevisionField, protect_edit
from .models import AdmissionPolicy


class PolicyForm(forms.ModelForm):
    eligible_statuses = forms.MultipleChoiceField(label="Application statuses allowed to book a playdate",
        widget=forms.CheckboxSelectMultiple)
    scheduled_status = forms.ChoiceField(label="Status after booking a playdate")
    completed_status = forms.ChoiceField(label="Status after attending a playdate")

    class Meta:
        model = AdmissionPolicy
        fields = ["enabled", "require_tour", "application_release", "playdate_release", "eligible_statuses", "scheduled_status", "completed_status"]
        labels = {"enabled": "Enable staged admissions", "require_tour": "Require a completed tour before the application",
            "application_release": "Release applications", "playdate_release": "Release playdate booking"}

    def __init__(self, *args, profile, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.profile = profile
        statuses, _ = get_submission_status_choices(load_school_config(profile.school.slug).raw)
        for name in ("eligible_statuses", "scheduled_status", "completed_status"):
            self.fields[name].choices = [(status, status) for status in statuses]
        if self.instance._state.adding:
            for name in ("scheduled_status", "completed_status"):
                self.fields[name].initial = getattr(self.instance, name)
        protect_edit(self, profile)
        if self.instance._state.adding:
            self.fields["create_revision"] = EditRevisionField({"profile": profile.pk, "new": True})

    def clean(self):
        data = super().clean()
        if self.instance.pk and self.instance.cases.exists() and not data.get("enabled"):
            raise ValidationError("This workflow has application history. Keep it enabled; pause Bookings access instead.")
        if data.get("scheduled_status") == data.get("completed_status"):
            raise ValidationError("Scheduled and completed statuses must be different.")
        if set(data.get("eligible_statuses", [])) & {data.get("scheduled_status"), data.get("completed_status")}:
            raise ValidationError("Scheduled/completed applications cannot be eligible for another playdate.")
        if data.get("enabled") and not self.instance.profile.school.features.family_portal_enabled:
            raise ValidationError("Enable the school's family portal before connecting staged admissions.")
        return data
