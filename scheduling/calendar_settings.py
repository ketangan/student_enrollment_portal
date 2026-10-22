"""Operator-owned booking dashboard preferences, independent of enrollment."""

from django import forms
from django.contrib import messages
from django.db import transaction
from django.forms.models import model_to_dict
from django.http import Http404
from django.shortcuts import redirect

from .capabilities import has_capability
from .editing import protect_edit
from .features import operator_profile
from .models import SchedulingProfile
from .setup import _audit


class CalendarSettingsForm(forms.ModelForm):
    class Meta:
        model = SchedulingProfile
        fields = ["calendar_enabled", "default_dashboard_view"]
        labels = {"calendar_enabled": "Enable booking calendar", "default_dashboard_view": "Default dashboard view"}
        widgets = {"default_dashboard_view": forms.Select(attrs={"class": "dash-select"})}

    def clean(self):
        data = super().clean()
        if data.get("calendar_enabled") and not has_capability(self.instance, "workshops"):
            self.add_error("calendar_enabled", "Enable workshops before enabling the booking calendar.")
        if data.get("default_dashboard_view") == "calendar" and not data.get("calendar_enabled"):
            self.add_error("default_dashboard_view", "Enable the calendar or choose Overview.")
        return data


def calendar_form(profile, data=None):
    return protect_edit(CalendarSettingsForm(data, instance=profile), profile)


@transaction.atomic
def save_calendar(user, slug, data):
    profile = operator_profile(user, slug)
    profile = SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    before = model_to_dict(profile)
    form = calendar_form(profile, data)
    if form.is_valid():
        form.save()
        _audit(user, profile, profile, before, "change")
    return form


def settings_context(request, school):
    posting = request.method == "POST" and request.POST.get("action") == "save_booking_calendar"
    profile = SchedulingProfile.objects.filter(school=school, enabled=True).first()
    if not profile:
        if posting:
            raise Http404
        return None
    form = save_calendar(request.user, school.slug, request.POST) if posting else calendar_form(profile)
    if posting and form.is_valid():
        messages.success(request, "Booking dashboard updated.")
        return redirect("ops_school_detail", slug=school.slug)
    return {"form": form}
