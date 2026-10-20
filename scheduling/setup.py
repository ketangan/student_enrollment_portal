"""Validated setup writes. All supported writers serialize on the tenant profile."""

import json
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.forms.models import model_to_dict
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import AdminAuditLog
from .access import require_access
from .capabilities import require_capability
from .editing import protect_edit
from .forms import LocationForm, OfferingForm, RoomForm, WorkshopSessionForm
from .models import Location, Offering, PartyPackage, Room, SchedulingProfile, Session
from .party_forms import PartyPackageForm


FORMS = {"locations": LocationForm, "rooms": RoomForm, "workshops": OfferingForm, "sessions": WorkshopSessionForm}
FORMS.update(party_packages=PartyPackageForm)


def records(profile, kind):
    if kind == "party_packages":
        require_capability(profile, "parties")
        return PartyPackage.objects.filter(offering__location__profile=profile).select_related("offering__location")
    if kind in {"workshops", "sessions"}:
        require_capability(profile, "workshops")
    if kind == "locations":
        return Location.objects.filter(profile=profile)
    if kind == "rooms":
        return Room.objects.filter(location__profile=profile).select_related("location")
    if kind == "workshops":
        return Offering.objects.filter(location__profile=profile, visit_program__isnull=True).select_related("location")
    if kind == "sessions":
        return Session.objects.filter(offering__location__profile=profile, offering__visit_program__isnull=True).select_related("offering__location", "room")
    raise Http404("Page not found")


def edit_permission(kind):
    return "sessions.edit" if kind == "sessions" else "setup.edit"


def build_form(profile, kind, data=None, pk=None, offering_pk=None):
    objects = records(profile, kind)
    instance = get_object_or_404(objects, pk=pk) if pk else None
    if kind == "sessions":
        offering = instance.offering if instance else get_object_or_404(records(profile, "workshops"), pk=offering_pk)
        with timezone.override(ZoneInfo(offering.location.timezone_name)):
            form = WorkshopSessionForm(data, instance=instance, offering=offering)
    else:
        options = {}
        if kind == "workshops":
            from .experience_demo.config import configuration
            try:
                options["unit_price_cents"] = configuration(profile.school.slug)["price_cents"]
            except Http404:
                pass
        form = FORMS[kind](data, instance=instance, profile=profile, **options)
    return protect_edit(form, profile)


def _audit(user, profile, instance, before, action):
    after = json.loads(json.dumps(model_to_dict(instance), cls=DjangoJSONEncoder))
    before = json.loads(json.dumps(before, cls=DjangoJSONEncoder))
    AdminAuditLog.objects.create(
        actor=user, action=action, model_label=instance._meta.label_lower,
        object_id=str(instance.pk), object_repr=str(instance),
        changes={key: {"from": before.get(key), "to": value} for key, value in after.items() if before.get(key) != value},
        extra={"scheduling_profile_id": profile.pk, "school_slug": profile.school.slug},
    )


@transaction.atomic
def save_setup(user, school_slug, kind, data, pk=None, offering_pk=None):
    profile, _ = require_access(user, school_slug, edit_permission(kind))
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    # Re-check access after waiting for another setup writer.
    profile, _ = require_access(user, school_slug, edit_permission(kind))
    form = build_form(profile, kind, data, pk, offering_pk)
    before = model_to_dict(form.instance) if pk else {}
    zone = form.instance.offering.location.timezone_name if kind == "sessions" else "UTC"
    with timezone.override(ZoneInfo(zone)):
        if form.is_valid():
            try:
                # ModelForm omits server-owned fields from constraint validation.
                form.instance.full_clean()
            except ValidationError as exc:
                for field, errors in exc.message_dict.items():
                    form.add_error(field if field in form.fields else None, errors)
            else:
                instance = form.save()
                if kind == "locations":
                    from .models import Material, StockLevel
                    for material in Material.objects.filter(profile=profile):
                        StockLevel.objects.get_or_create(material=material, location=instance)
                _audit(user, profile, instance, before, "change" if pk else "add")
    return form


@transaction.atomic
def set_publication(user, school_slug, kind, pk, publish):
    if kind not in {"workshops", "sessions"}:
        raise Http404("Page not found")
    profile, _ = require_access(user, school_slug, edit_permission(kind))
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_access(user, school_slug, edit_permission(kind))
    instance = get_object_or_404(records(profile, kind), pk=pk)
    if instance.is_published == publish:
        return instance
    before = model_to_dict(instance)
    if publish:
        if kind == "workshops" and not instance.accepts_party_requests and not instance.sessions.filter(is_published=True, starts_at__gt=timezone.now()).exists():
            raise ValidationError("Publish at least one upcoming session before publishing this workshop.")
        if kind == "sessions" and instance.starts_at <= timezone.now():
            raise ValidationError("A session that has already started cannot be published.")
    instance.is_published = publish
    instance.full_clean()
    instance.save(update_fields=["is_published"])
    _audit(user, profile, instance, before, "change")
    return instance
