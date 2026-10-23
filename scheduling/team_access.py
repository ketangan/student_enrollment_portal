"""Scheduling team management using the shared school team workflow."""
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.http import Http404
from core.team_forms import TeamEmailForm

from .access import require_access
from .editing import protect_edit
from .models import SchedulingMembership, SchedulingProfile
from .lessons import audit


class MembershipForm(forms.ModelForm):
    location_access = forms.ChoiceField(choices=[("all", "All locations"), ("selected", "Selected locations")],
        widget=forms.RadioSelect)

    class Meta:
        model = SchedulingMembership
        fields = ["role", "is_active", "locations"]
        labels = {"is_active": "Active", "locations": "Locations"}
        widgets = {"locations": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        member = self.instance
        self.fields["locations"].queryset = member.profile.locations.order_by("name", "pk")
        self.initial["location_access"] = "all" if member.all_locations else "selected"

    def clean(self):
        data = super().clean()
        if data.get("location_access") == "selected" and not data.get("locations"):
            self.add_error("locations", "Select at least one location.")
        return data


def membership_form(member, data=None):
    return protect_edit(MembershipForm(data, instance=member), member.profile)


@transaction.atomic
def save_membership(user, slug, pk, data):
    profile, _ = require_access(user, slug, "access.view")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_access(user, slug, "access.view")
    member = get_object_or_404(SchedulingMembership.objects.select_related("profile", "user"), profile=profile, pk=pk)
    before = {"role": member.role, "is_active": member.is_active, "all_locations": member.all_locations,
              "locations": list(member.locations.values_list("pk", flat=True))}
    form = membership_form(member, data)
    if form.is_valid():
        loses_owner = not (form.cleaned_data["role"] == "owner" and form.cleaned_data["is_active"]
                            and form.cleaned_data["location_access"] == "all")
        last_owner = (before["role"] == "owner" and before["is_active"] and before["all_locations"]
            and not profile.memberships.filter(role="owner", is_active=True, all_locations=True,
                user__is_active=True).exclude(pk=member.pk).exists())
        if loses_owner and last_owner:
            form.add_error(None, "Keep at least one active Owner with access to all locations.")
        else:
            member.all_locations = form.cleaned_data["location_access"] == "all"
            member.save()
            member.locations.set([] if member.all_locations else form.cleaned_data["locations"])
            after = {"role": member.role, "is_active": member.is_active, "all_locations": member.all_locations,
                     "locations": list(member.locations.values_list("pk", flat=True))}
            changes = {key: {"from": value, "to": after[key]} for key, value in before.items() if value != after[key]}
            if changes:
                audit(user, profile, member, "change", changes)
    return form


class NewMemberForm(TeamEmailForm):
    password = forms.CharField(min_length=8, strip=False, widget=forms.PasswordInput)
    role = forms.ChoiceField(choices=SchedulingMembership.Role.choices)
    location_access = forms.ChoiceField(choices=[("all", "All locations"), ("selected", "Selected locations")])
    locations = forms.ModelMultipleChoiceField(queryset=None, required=False)

    class Meta:
        model = get_user_model()
        fields = ["username", "first_name", "last_name", "email"]

    def __init__(self, profile, data=None):
        super().__init__(data)
        self.fields["locations"].queryset = profile.locations.all()

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise ValidationError("This username is already taken.")
        return username

    def clean(self):
        data = super().clean()
        if data.get("location_access") == "selected" and not data.get("locations"):
            self.add_error("locations", "Select at least one location.")
        return data


@transaction.atomic
def add_member(user, slug, data):
    profile, _ = require_access(user, slug, "access.view")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_access(user, slug, "access.view")
    form = NewMemberForm(profile, data)
    if form.is_valid():
        account = form.save(commit=False)
        account.set_password(form.cleaned_data["password"])
        # Booking membership does not grant Django staff or enrollment access.
        try:
            with transaction.atomic():
                account.save()
        except IntegrityError:
            if get_user_model().objects.filter(username__iexact=account.username).exists():
                form.add_error("username", "This username is already taken.")
                return form
            raise
        member = SchedulingMembership.objects.create(profile=profile, user=account,
            role=form.cleaned_data["role"], all_locations=form.cleaned_data["location_access"] == "all",
            created_by=user)
        if not member.all_locations:
            member.locations.set(form.cleaned_data["locations"])
        audit(user, profile, member, "add", {"username": account.username, "role": member.role,
            "all_locations": member.all_locations, "locations": list(member.locations.values_list("pk", flat=True))})
    return form


@transaction.atomic
def update_member(user, slug, pk, action, data):
    profile, _ = require_access(user, slug, "access.view")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_access(user, slug, "access.view")
    member = get_object_or_404(profile.memberships.select_related("user", "profile"), pk=pk, is_active=True)
    if action in ("name", "email"):
        account = get_user_model().objects.select_for_update().get(pk=member.user_id)
        if action == "email" and account.is_superuser and not user.is_superuser:
            raise Http404
        fields = ["email"] if action == "email" else ["first_name", "last_name"]
        form_class = TeamEmailForm if action == "email" else forms.modelform_factory(get_user_model(), fields=fields)
        before = {key: getattr(account, key) for key in fields}
        form = form_class(data, instance=account)
        if form.is_valid():
            form.save()
            changes = {key: {"from": value, "to": getattr(account, key)} for key, value in before.items()
                       if value != getattr(account, key)}
            if changes:
                audit(user, profile, member, "change", changes)
        return form
    values = data.copy()
    if action != "role":
        values["role"] = member.role
    values["is_active"] = "on" if action != "remove" else ""
    if action != "locations":
        values["location_access"] = "all" if member.all_locations else "selected"
        values.setlist("locations", list(member.locations.values_list("pk", flat=True)))
    if action == "remove" and member.user_id == user.pk:
        form = membership_form(member, values)
        form.is_valid()
        form.add_error(None, "You cannot remove yourself from the team.")
        return form
    return save_membership(user, slug, pk, values)
