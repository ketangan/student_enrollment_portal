"""Participant intake for unpaid requests and checkout; intake alone never reserves seats."""

from collections import Counter
import hashlib
import json
import uuid

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare, get_random_string

from core.models import AdminAuditLog
from .access import require_access
from .capabilities import has_capability, require_capability
from .models import ChoiceGroup, ChoiceOption, RequestParticipant, SchedulingProfile, Session, WorkshopRequest
from .request_forms import ChoiceGroupForm, ChoiceOptionForm, request_forms
from .setup import _audit, records

TOKEN_SALT = "scheduling.workshop-request.v1"


def browser_key(request, create=False):
    key = request.session.get("bookings_request_browser", "")
    if not key and create:
        key = get_random_string(48)
        request.session["bookings_request_browser"] = key
    return hashlib.sha256(key.encode()).hexdigest() if key else ""


def catalog_for(offering):
    groups = offering.inventory_catalog.choice_groups if offering.inventory_catalog_id else offering.choice_groups
    return list(groups.filter(is_active=True).prefetch_related(
        Prefetch("options", queryset=ChoiceOption.objects.filter(is_active=True), to_attr="active_options")))


def session_for_request(slug, pk):
    session = get_object_or_404(Session.objects.select_related("offering__location__profile__school", "room"),
                             pk=pk, offering__visit_program__isnull=True, offering__location__profile__school__slug=slug, offering__location__profile__enabled=True)
    require_capability(session.offering.location.profile, "workshops")
    return session


def session_available(session, catalog):
    return session.is_published and session.offering.is_published and session.starts_at > timezone.now() and all(g.active_options for g in catalog)


def accepts_requests(session, catalog):
    return session.offering.accepts_requests and session_available(session, catalog)


def accepts_reservations(session, catalog):
    profile = session.offering.location.profile
    return (profile.enabled and profile.native_reservations_enabled
            and has_capability(profile, "reservations") and session_available(session, catalog)
            and not session.camps.exists())


def catalog_digest(session, catalog):
    state = [session.starts_at.isoformat(), session.ends_at.isoformat(), session.room_id, session.capacity,
             session.offering.name, session.offering.location.name, session.offering.location.timezone_name,
             [(g.pk, g.name, g.reference_image, [(o.pk, o.label, o.swatch, o.image_url) for o in g.active_options]) for g in catalog]]
    if session.offering.inventory_catalog_id:
        state.append([(v.pk, v.is_active, v.option_ids,
            [(r.material_id, str(r.quantity), r.material.is_active) for r in v.requirements.all()])
            for v in session.offering.inventory_catalog.items.prefetch_related("requirements__material")])
    return hashlib.sha256(json.dumps(state).encode()).hexdigest()


def request_token(session, catalog, browser):
    return signing.dumps({"key": str(uuid.uuid4()), "session": session.pk, "browser": browser, "catalog": catalog_digest(session, catalog)}, salt=TOKEN_SALT)


def submission_digest(data):
    # Exclude CSRF, which may rotate after login; a retry must otherwise be identical.
    return hashlib.sha256(json.dumps({k: data.getlist(k) for k in sorted(data) if k not in {"csrfmiddlewaretoken", "request_token"}}).encode()).hexdigest()


@transaction.atomic
def submit_request(slug, pk, data, browser, *, for_reservation=False):
    try:
        token = signing.loads(data.get("request_token", ""), salt=TOKEN_SALT, max_age=7200)
        key = uuid.UUID(str(token["key"]))
        if token["session"] != pk or not browser or not constant_time_compare(token["browser"], browser):
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError("This form has expired or belongs to another browser. Reload it before trying again.")
    session = session_for_request(slug, pk)
    SchedulingProfile.objects.select_for_update().get(pk=session.offering.location.profile_id)
    session = session_for_request(slug, pk)
    existing = WorkshopRequest.objects.filter(submission_key=key, session=session, browser_digest=browser).first()
    digest = submission_digest(data)
    if existing:
        if existing.submission_digest != digest:
            raise ValidationError("This form was already submitted with different details. Open a new request to start again.")
        return existing, None, None
    catalog = catalog_for(session.offering)
    # The reservation checkout reuses participant intake, not the unpaid-request opt-in.
    accepting = accepts_reservations if for_reservation else accepts_requests
    if not accepting(session, catalog):
        raise ValidationError("This session is not accepting requests. Please choose another session.")
    if not constant_time_compare(token.get("catalog", ""), catalog_digest(session, catalog)):
        raise ValidationError("The schedule or choices changed. Open a new request from All workshops before submitting.")
    purchaser, participants = request_forms(session, catalog, data)
    purchaser_valid, participants_valid = purchaser.is_valid(), participants.is_valid()
    if not purchaser_valid or not participants_valid:
        return None, purchaser, participants
    from .inventory import check_selections
    check_selections(session.offering, [{"selections": form.snapshot()} for form in participants])
    result = WorkshopRequest.objects.create(
        session=session, **purchaser.cleaned_data, submission_key=key, submission_digest=digest, browser_digest=browser,
        session_snapshot={"workshop": session.offering.name, "location": session.offering.location.name,
                          "room": session.room.name if session.room else "", "timezone": session.offering.location.timezone_name,
                          "starts_at": session.starts_at.isoformat(), "ends_at": session.ends_at.isoformat()},
    )
    RequestParticipant.objects.bulk_create([RequestParticipant(request=result, name=form.cleaned_data["name"],
        position=index, selections=form.snapshot()) for index, form in enumerate(participants)])
    AdminAuditLog.objects.create(action="add", model_label="scheduling.workshoprequest", object_id=str(result.pk),
        object_repr=str(result.public_id), changes={"status": {"from": None, "to": "pending"}},
        extra={"school_slug": slug, "scheduling_profile_id": session.offering.location.profile_id})
    return result, purchaser, participants


def choice_form(profile, offering_pk, group_pk=None, option_pk=None, data=None, option=False):
    from .editing import protect_edit
    offering = get_object_or_404(records(profile, "workshops"), pk=offering_pk)
    if offering.inventory_catalog_id:
        raise Http404("Shared choices are managed in Settings > Inventory.")
    group = get_object_or_404(ChoiceGroup, offering=offering, pk=group_pk) if group_pk else None
    if option:
        if not group:
            raise Http404("Page not found")
        instance = get_object_or_404(ChoiceOption, group=group, pk=option_pk) if option_pk else ChoiceOption(group=group)
        form = ChoiceOptionForm(data, instance=instance)
    else:
        form = ChoiceGroupForm(data, instance=group or ChoiceGroup(offering=offering))
    return offering, protect_edit(form, profile)


@transaction.atomic
def save_choice(user, slug, offering_pk, data, **kwargs):
    from django.forms.models import model_to_dict
    profile, _ = require_access(user, slug, "setup.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_access(user, slug, "setup.edit")
    offering, form = choice_form(profile, offering_pk, data=data, **kwargs)
    before = model_to_dict(form.instance) if form.instance.pk else {}
    if form.is_valid():
        try:
            form.instance.full_clean()
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            item = form.save()
            _audit(user, profile, item, before, "change" if before else "add")
    return offering, form


def scoped_requests(profile):
    require_capability(profile, "workshops")
    return WorkshopRequest.objects.filter(session__offering__location__profile=profile).select_related("session__offering__location", "session__room", "reservation").prefetch_related("participants")


@transaction.atomic
def withdraw_request(user, slug, public_id):
    profile, _ = require_access(user, slug, "sessions.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_access(user, slug, "sessions.edit")
    item = get_object_or_404(scoped_requests(profile), public_id=public_id)
    from .models import Reservation
    if Reservation.objects.filter(workshop_request=item).exclude(state="canceled").exists():
        raise ValidationError("Cancel the reservation before withdrawing its request.")
    if item.status != WorkshopRequest.Status.WITHDRAWN:
        item.status = WorkshopRequest.Status.WITHDRAWN
        item.save(update_fields=["status"])
        AdminAuditLog.objects.create(actor=user, action="change", model_label="scheduling.workshoprequest",
            object_id=str(item.pk), object_repr=str(item.public_id), changes={"status": {"from": "pending", "to": "withdrawn"}},
            extra={"school_slug": slug, "scheduling_profile_id": profile.pk})
    return item


def preparation_totals(requests):
    counts = Counter()
    total = 0
    for item in requests:
        if item.status != WorkshopRequest.Status.PENDING:
            continue
        for participant in item.participants.all():
            total += 1
            for selection in participant.selections:
                counts[(selection["group_id"], selection["group"], selection["option_id"], selection["option"])] += 1
    return total, [{"group": group, "option": option, "count": count} for (_, group, _, option), count in sorted(counts.items())]
