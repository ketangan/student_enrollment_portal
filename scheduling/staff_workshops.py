"""Calendar intake for staff-reserved workshop places, using the shared ledger."""
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from core.models import AdminAuditLog

from . import reservations, setup, workshop_notifications
from .access import require_location_access
from .booking_policies import notice
from .lessons import digest
from .models import RequestParticipant, ReservationOccurrence, SchedulingProfile, Session, WorkshopRequest
from .request_forms import MAX_PARTICIPANTS, PurchaserForm
from .requests import catalog_for, submission_digest

SALT = "scheduling.staff-workshop.v1"


def booking_available(profile, offering):
    from .capabilities import has_capability
    return bool(profile.native_reservations_enabled and has_capability(profile, "reservations")
        and offering.accepts_requests and offering.is_published
        and offering.allow_deferred_selections and offering.inventory_catalog_id
        and offering.price_per_participant is not None)


def configuration(offering):
    return digest({"price": format(offering.price_per_participant, ".2f") if offering.price_per_participant is not None else None,
        "deposit": format(offering.deposit_per_participant, ".2f"), "tax": str(Decimal(offering.location.default_tax_rate or 0).normalize()),
        "catalog": offering.inventory_catalog_id, "deferred": offering.allow_deferred_selections,
        "published": offering.is_published, "accepts_requests": offering.accepts_requests,
        "policy": notice(offering.location.profile.school, "workshop")})


def token_for(user, profile, offering, *, new=False, session=None):
    sessions = {str(s.pk): session_revision(s) for s in offering.sessions.filter(
        starts_at__gt=timezone.now(), camps__isnull=True)}
    return signing.dumps({"user": user.pk, "profile": profile.pk, "offering": offering.pk,
        "configuration": configuration(offering), "sessions": sessions, "key": str(uuid.uuid4()),
        "new": new, "session": session}, salt=SALT, compress=True)


def session_revision(session):
    return digest({"start": session.starts_at.isoformat(), "end": session.ends_at.isoformat(),
        "room": session.room_id, "capacity": session.capacity})


def session_label(session, timezone_name):
    zone = ZoneInfo(timezone_name)
    start, end = timezone.localtime(session.starts_at, zone), timezone.localtime(session.ends_at, zone)
    end_format = "%I:%M %p" if start.date() == end.date() else "%a, %b %d, %Y at %I:%M %p"
    return (start.strftime("%a, %b %d, %Y at %I:%M %p") + " - " + end.strftime(end_format)
        + (" (unpublished)" if not session.is_published else ""))


def check_room(session, *, exclude=None):
    """Draft sessions may be staff-booked, but may not bypass room conflicts."""
    if not session.room_id:
        return
    from .lessons import validate_lesson_room
    validate_lesson_room(session.room_id, session.starts_at, session.ends_at)
    window = {"starts_at__lt": session.ends_at, "ends_at__gt": session.starts_at}
    claims = reservations.active_claims().exclude(pk=exclude)
    room_claims = claims.filter(room_id=session.room_id, **window)
    occurrences = ReservationOccurrence.objects.filter(reservation__in=claims, room_id=session.room_id, **window)
    if session.pk:
        room_claims = room_claims.exclude(session_id=session.pk)
        occurrences = occurrences.exclude(session_id=session.pk)
    if (Session.objects.filter(room_id=session.room_id, is_published=True, **window).exclude(pk=session.pk).exists()
            or room_claims.exists() or occurrences.exists()):
        raise ValidationError("This room is already booked at that time. Choose another room or workshop time.")


class StaffWorkshopForm(PurchaserForm):
    session = forms.ModelChoiceField(label="Workshop time", queryset=None,
        empty_label="Choose a workshop time")
    quantity = forms.IntegerField(label="Number of participants", min_value=1, max_value=MAX_PARTICIPANTS, initial=1)
    request_token = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, profile, offering, initial=None, new=False, fixed_session=None, **kwargs):
        initial = dict(initial or {})
        super().__init__(*args, initial=initial, **kwargs)
        self.profile, self.offering = profile, offering
        self.new = new
        self.can_book = booking_available(profile, offering)
        self.fields["purchaser_name"].label = "Customer name"
        self.fields["session"].queryset = offering.sessions.filter(starts_at__gt=timezone.now(), camps__isnull=True).select_related("room").order_by("starts_at", "pk")
        self.fields["session"].label_from_instance = lambda s: session_label(s, offering.location.timezone_name)
        self.has_times = self.fields["session"].queryset.exists()
        self.fixed_session = self.fields["session"].queryset.filter(pk=fixed_session).first() if fixed_session else None
        if self.fixed_session:
            self.fields["session"].initial = self.fixed_session.pk
            self.fields["session"].disabled = True
            self.fields["session"].widget = forms.HiddenInput()
        self.remaining = (max(0, self.fixed_session.capacity - reservations.session_places_taken(self.fixed_session))
            if self.fixed_session else None)
        if self.remaining is not None:
            self.fields["quantity"].widget.attrs["max"] = self.remaining
        if new:
            self.fields.pop("session")
            self.fields["purchaser_name"].required = False
            self.fields["purchaser_email"].required = False
            self.fields["quantity"].required = False
        self.session_form = setup.build_form(profile, "sessions", self.data if self.is_bound else None,
            offering_pk=offering.pk) if new else None
        if self.session_form and not self.is_bound:
            self.session_form.initial.update({k: v for k, v in initial.items() if k in self.session_form.fields})

    def clean(self):
        values = super().clean()
        self.with_customer = not self.new or any(values.get(k) for k in ("purchaser_name", "purchaser_email", "purchaser_phone"))
        if self.with_customer:
            if not self.can_book:
                raise ValidationError("Customer bookings are not enabled for this workshop.")
            for name in ("purchaser_name", "purchaser_email", "quantity"):
                if not values.get(name) and name not in self.errors:
                    self.add_error(name, "Required when booking a customer.")
        return values


@transaction.atomic
def create(user, slug, offering_pk, data):
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="workshops")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="workshops")
    offering = get_object_or_404(setup.records(profile, "workshops"), pk=offering_pk)
    try:
        token = signing.loads(data.get("request_token", ""), salt=SALT, max_age=7200)
        key = uuid.UUID(token["key"])
        if (token["user"], token["profile"], token["offering"]) != (user.pk, profile.pk, offering.pk):
            raise ValueError
    except (signing.BadSignature, KeyError, ValueError, TypeError):
        raise ValidationError("This form expired. Reopen the calendar booking.") from None
    form = StaffWorkshopForm(data, profile=profile, offering=offering,
        new=token.get("new", False), fixed_session=token.get("session"))
    stamp = submission_digest(data)
    existing = WorkshopRequest.objects.filter(submission_key=key, session__offering=offering).first()
    if existing:
        if existing.submission_digest != stamp:
            raise ValidationError("This booking was already saved with different details. Open the reservation.")
        return existing.reservation, form
    prior = AdminAuditLog.objects.filter(model_label="scheduling.session", extra__scheduling_profile_id=profile.pk,
        extra__calendar_key=str(key)).first()
    if prior:
        if prior.extra.get("submission_digest") != stamp:
            raise ValidationError("This session was already saved with different details. Reopen it from the calendar.")
        return get_object_or_404(setup.records(profile, "sessions"), pk=prior.object_id), form
    if token["configuration"] != configuration(offering):
        raise ValidationError("Workshop pricing or policies changed. Reopen the booking to review them.")
    if token.get("session") and not form.fixed_session:
        raise ValidationError("This workshop time is no longer available. Reopen the calendar.")
    if not form.new and not form.can_book:
        raise ValidationError("Customer bookings are not enabled for this workshop. Check its booking settings.")
    with timezone.override(ZoneInfo(offering.location.timezone_name)):
        valid = form.is_valid()
        if form.session_form is not None:
            valid = form.session_form.is_valid() and valid
    if not valid:
        return None, form
    values = form.cleaned_data
    session = values.get("session")
    if form.new:
        session = form.session_form.instance
        if offering.sessions.filter(starts_at=session.starts_at).exists():
            raise ValidationError("A workshop session already starts at this time. Open it on the calendar to add a booking.")
        session.is_published = True
        session.full_clean()
        check_room(session)
        reservations.ensure_studio_available(offering.location_id, session.starts_at, session.ends_at)
        if session.starts_at <= timezone.now():
            raise ValidationError("Choose a future workshop time.")
        session.save()
        if not form.with_customer:
            AdminAuditLog.objects.create(actor=user, action="add", model_label="scheduling.session",
                object_id=str(session.pk), object_repr=str(session),
                extra={"scheduling_profile_id": profile.pk, "calendar_key": str(key), "submission_digest": stamp})
            return session, form
        setup._audit(user, profile, session, {}, "add")
    elif token.get("sessions", {}).get(str(session.pk)) != session_revision(session):
        raise ValidationError("This workshop time changed. Reopen the booking to review it.")
    check_room(session)
    if not catalog_for(offering):
        raise ValidationError("Configure participant choices before taking a booking.")
    total, deposit = reservations.workshop_quote(offering, reservations.workshop_unit_price(offering), values["quantity"])
    # Demo previews can use offline orders; never create a test ledger for a real studio.
    try:
        account = reservations.booking_account(profile, True, offering.location)
    except ValidationError:
        if not profile.school.is_demo:
            raise
        account = None
    source = WorkshopRequest.objects.create(session=session,
        purchaser_name=values["purchaser_name"], purchaser_email=values["purchaser_email"],
        purchaser_phone=values.get("purchaser_phone", ""), submission_key=key,
        submission_digest=stamp, browser_digest=digest({"staff": user.pk, "profile": profile.pk}),
        session_snapshot={"workshop": offering.name, "location": offering.location.name,
            "timezone": offering.location.timezone_name, "room": session.room.name if session.room else "",
            "starts_at": session.starts_at.isoformat(), "ends_at": session.ends_at.isoformat(),
            "price_breakdown": reservations.workshop_price_breakdown(offering, reservations.workshop_unit_price(offering), values["quantity"])})
    RequestParticipant.objects.bulk_create([RequestParticipant(request=source, position=i,
        name=f"Participant {i + 1}", selections=[], selections_deferred=True) for i in range(values["quantity"])])
    item = reservations.workshop_reservation_item(profile, source)
    item.state = "confirmed"
    item.snapshot.update(staff_managed=True, staff_workshop=True,
        customer_policy=notice(profile.school, "workshop"))
    item = reservations.save_reservation(item, source, "workshop", total, deposit, account, user)
    workshop_notifications.capture(item)
    return item, form
