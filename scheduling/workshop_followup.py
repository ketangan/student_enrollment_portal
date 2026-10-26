"""Private, one-participant-at-a-time completion of undecided workshop choices."""
from django.core import signing
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404

from . import guest_lists, workshop_selections
from .inventory_forms import PartyMaterialRow
from .models import Reservation, SchedulingProfile

SALT = "scheduling.workshop-selections.v1"


def token_for(item):
    return signing.dumps({"reservation": str(item.public_id), "school": item.profile.school.slug}, salt=SALT)


def resolve(slug, token):
    import uuid
    try:
        data = signing.loads(token, salt=SALT, max_age=366 * 86400)
        if data["school"] != slug:
            raise ValueError
        public_id = uuid.UUID(data["reservation"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise Http404("This booking link is invalid or expired.") from None
    return get_object_or_404(Reservation.objects.select_related("profile__school",
        "workshop_request__session__offering__location__profile__school",
        "workshop_request__session__offering__inventory_catalog"),
        public_id=public_id, profile__school__slug=slug, profile__enabled=True,
        workshop_request__isnull=False, workshop_request__session__offering__inventory_catalog__isnull=False)


def form_for(item, person, data=None):
    offering = item.workshop_request.session.offering
    groups = set(offering.inventory_catalog.choice_groups.filter(tracks_stock=True).values_list("pk", flat=True))
    key = ":".join(map(str, sorted(row["option_id"] for row in person.selections if row["group_id"] in groups)))
    variant = offering.inventory_catalog.items.filter(choice_key=key, is_active=True).first()
    form = PartyMaterialRow(data, offering=item.workshop_request.session.offering,
        guest_mode=True, prefix="selection", initial={"guest_name": person.name, "item": variant.pk if variant else None})
    form.fields["selection_revision"] = forms.CharField(required=False, widget=forms.HiddenInput(), initial=workshop_selections.revision(person))
    # Workshop age choices can be catalog values, not necessarily numerical ages.
    form.fields.pop("guest_age")
    return form


@transaction.atomic
def save(slug, token, participant_id, data):
    item = resolve(slug, token)
    SchedulingProfile.objects.select_for_update().get(pk=item.profile_id)
    item = resolve(slug, token)
    if not guest_lists.editable(item) or item.stock_allocations.filter(consumed_at__isnull=False).exists():
        raise ValidationError("Online selections are closed. Please speak with the studio.")
    person = get_object_or_404(item.workshop_request.participants, pk=participant_id)
    form = form_for(item, person, data)
    if not form.is_valid():
        return item, form
    variant = form.cleaned_data["item"]
    from .requests import catalog_for
    fields = {f"choice_{group.pk}": str(option.pk)
        for group in catalog_for(item.workshop_request.session.offering) if group.tracks_stock
        for option in group.active_options if option.pk in variant.option_ids}
    fields["selection_revision"] = form.cleaned_data["selection_revision"]
    workshop_selections.complete(item, person, fields)
    if form.cleaned_data["guest_name"] and form.cleaned_data["guest_name"] != person.name:
        from .reservations import _audit
        previous_name = person.name
        person.name = form.cleaned_data["guest_name"]
        person.save(update_fields=["name"])
        _audit(None, item, "workshop_participant_named", participant_id=person.pk,
            participant_name=person.name, previous_name=previous_name)
    return item, form
