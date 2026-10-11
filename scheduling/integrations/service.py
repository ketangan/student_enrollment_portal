"""Tenant-scoped preparation writes. Never changes active booking authority."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.shortcuts import get_object_or_404
from django.views.decorators.debug import sensitive_variables

from core.models import AdminAuditLog
from scheduling.access import require_access
from scheduling.editing import protect_edit
from scheduling.models import IntegrationConnection, SchedulingProfile
from scheduling.setup import _audit
from .forms import FORMS
from .registry import provider
from .vault import seal


def access(user, slug):
    return require_access(user, slug, "setup.edit", capability="integrations")


def connections(profile):
    return IntegrationConnection.objects.filter(profile=profile).select_related("source").defer("credential_ciphertext")


def form_for(profile, provider_key=None, pk=None, data=None):
    instance = get_object_or_404(IntegrationConnection, profile=profile, pk=pk) if pk else IntegrationConnection(
        profile=profile, provider=provider(provider_key).key, environment="test")
    return protect_edit(FORMS[provider(instance.provider).key](data, instance=instance), profile)


@sensitive_variables()
@transaction.atomic
def save_connection(user, slug, data, provider_key=None, pk=None):
    profile, _ = access(user, slug)
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = access(user, slug)
    form = form_for(profile, provider_key, pk, data)
    before = model_to_dict(form.instance) if pk else {}
    if form.is_valid():
        connection = form.save(commit=False)
        action = "kept"
        if form.cleaned_data["credential"]:
            try:
                connection.credential_ciphertext = seal(connection, form.cleaned_data["credential"])
            except ValidationError as exc:
                form.add_error("credential", exc)
                return form
            action = "replaced"
        elif form.cleaned_data["clear_credential"]:
            connection.credential_ciphertext = ""
            action = "removed"
        connection.save()
        _audit(user, profile, connection, before, "change" if pk else "add")
        if action != "kept":
            AdminAuditLog.objects.create(actor=user, action="action", model_label=connection._meta.label_lower,
                object_id=str(connection.pk), object_repr=connection.label,
                changes={"credential": action}, extra={"scheduling_profile_id": profile.pk})
    return form
