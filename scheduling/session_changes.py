"""Reviewed workshop changes, serialized with the existing reservation writers."""
from copy import copy
from datetime import timezone as datetime_timezone
from decimal import Decimal
import uuid

from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import PaymentOrder
from . import cancellation, reservations, setup
from .access import require_location_access
from .capabilities import has_capability
from .forms import WorkshopSessionForm, clean_session_time
from .lessons import digest
from .models import SchedulingProfile, Session
from .message_models import BookingMessage
from .requests import submission_digest
from .reservation_forms import CancellationForm
from .reservation_queries import staff_reservations
from .staff_workshops import check_room

SALT = "scheduling.session-change.v1"


def available(profile, session):
    return (profile.native_reservations_enabled and has_capability(profile, "reservations")
        and has_capability(profile, "workshops") and not session.camps.exists())


def get_session(profile, pk):
    return get_object_or_404(Session.objects.select_related("offering__location", "room"),
        pk=pk, offering__in=setup.records(profile, "workshops"))


def affected(profile, session):
    return list(staff_reservations(profile).filter(session=session).exclude(state="canceled")
        .select_related("workshop_request").order_by("pk"))


def revision(session, items):
    return digest({"session": {"start": str(session.starts_at), "end": str(session.ends_at),
        "room": session.room_id, "capacity": session.capacity, "published": session.is_published,
        "canceled": str(session.canceled_at)}, "bookings": [{"id": item.pk, "state": item.state,
        "start": str(item.starts_at), "end": str(item.ends_at), "room": item.room_id,
        "quantity": item.quantity, "net": item.net_received, "total": item.effective_total,
        "payment_pending": item.payment_pending, "refund_pending": item.refund_pending,
        "materials_used": item.materials_used, "snapshot": item.snapshot,
        "name": item.workshop_request.purchaser_name, "email": item.workshop_request.purchaser_email,
        "phone": item.workshop_request.purchaser_phone} for item in items]})


class SessionChangeForm(forms.Form):
    def __init__(self, *args, session, **kwargs):
        super().__init__(*args, **kwargs)
        source = WorkshopSessionForm(instance=copy(session), offering=session.offering)
        self.instance = source.instance
        self.fields = source.fields
        self.initial.update(source.initial)
        self._original_start, self._original_end = source._original_start, source._original_end
        self._original_duration = source._original_duration

    def clean(self):
        values = clean_session_time(self, super().clean())
        for name in ("starts_at", "room", "capacity"):
            if name in values:
                setattr(self.instance, name, values[name])
        return values


def build_form(user, profile, session, action, data=None):
    items = affected(profile, session)
    form = (CancellationForm(data, unpaid=not any(i.net_received > 0 for i in items)) if action == "cancel"
        else SessionChangeForm(data, session=session))
    if action == "cancel":
        form.fields.pop("key")
        form.fields["email_customers"] = forms.BooleanField(required=False, label="Email customers")
        form.fields["customer_message"] = forms.CharField(required=False, max_length=2000,
            label="Customer message (optional)", widget=forms.Textarea(attrs={"rows": 2}))
    form.fields["change_token"] = forms.CharField(widget=forms.HiddenInput, initial=signing.dumps({
        "user": user.pk, "profile": profile.pk, "session": session.pk, "action": action,
        "revision": revision(session, items), "key": str(uuid.uuid4())}, salt=SALT))
    form.fields["review_token"] = forms.CharField(required=False, widget=forms.HiddenInput)
    return form


def preview_rows(items):
    return [{"name": item.workshop_request.purchaser_name, "phone": item.workshop_request.purchaser_phone,
        "quantity": item.quantity,
        "paid": Decimal(item.net_received) / 100, "currency": item.order.currency.upper()} for item in items]


@transaction.atomic
def save(user, slug, pk, action, data, *, preview_only=False):
    permission = "setup.edit" if action == "cancel" else "sessions.edit"
    profile, _ = require_location_access(user, slug, permission, capability="workshops")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, permission, capability="workshops")
    session = get_session(profile, pk)
    if action not in {"edit", "cancel"} or not available(profile, session):
        raise ValidationError("This workshop change is not available.")
    try:
        token = signing.loads(data.get("change_token", ""), salt=SALT, max_age=7200)
        key = uuid.UUID(token["key"])
        if (token["user"], token["profile"], token["session"], token["action"]) != (user.pk, profile.pk, session.pk, action):
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError("Reopen the session before saving. This form has expired.") from None
    stamp = submission_digest(data)
    previous = AdminAuditLog.objects.filter(model_label="scheduling.session", object_id=str(pk),
        extra__session_change_key=str(key), extra__scheduling_profile_id=profile.pk).first()
    if previous:
        if previous.extra["submission_digest"] != stamp:
            raise ValidationError("This change was already saved with different details. Reopen the calendar.")
        return True, None, None
    if session.canceled_at or session.starts_at <= timezone.now():
        raise ValidationError("Only upcoming sessions that have not been canceled can be changed.")
    items = affected(profile, session)
    # Payment webhooks lock orders; take the same locks before reading review evidence.
    School.objects.select_for_update().get(pk=profile.school_id)
    list(PaymentOrder.objects.select_for_update().filter(pk__in=[i.order_id for i in items]).order_by("pk"))
    items = affected(profile, session)
    if token.get("revision") != revision(session, items):
        raise ValidationError("The session or its bookings changed. Reopen it to review the latest details.")
    form = build_form(user, profile, session, action, data)
    if not form.is_valid():
        return False, form, None
    values = form.cleaned_data
    candidate = form.instance if action == "edit" else session
    time_changed = (session.starts_at, session.ends_at, session.room_id) != (candidate.starts_at, candidate.ends_at, candidate.room_id)
    if items and (time_changed or action == "cancel"):
        require_location_access(user, slug, "setup.edit", capability="reservations")
        if any(i.payment_pending or i.refund_pending or i.state in {"review", "completed"} or i.materials_used for i in items):
            raise ValidationError("Resolve pending payments, refunds or completed bookings before changing the whole session.")
        if BookingMessage.objects.filter(reservation_id__in=[i.pk for i in items], state__in=["sending", "uncertain"]).exists():
            raise ValidationError("A customer email is still being delivered or needs review. Resolve its delivery status before changing this session.")
    if session.requests.filter(status="pending", reservation__isnull=True).exists() and (time_changed or action == "cancel"):
        raise ValidationError("Review the pending workshop requests before changing this session.")
    if action == "edit":
        candidate.clean_fields()
        candidate.clean(allow_booked_move=True)
        candidate.validate_constraints()
        if time_changed:
            check_room(candidate)
    elif values["resolution"] == "with_refund":
        for item in items:
            if item.net_received > 0:
                cancellation.refund_plan(profile, item)
    review_data = data.copy()
    review_data.pop("review_token", None)
    review = {"change": data.get("change_token"), "data": submission_digest(review_data)}
    try:
        accepted = signing.loads(values.get("review_token", ""), salt=SALT + ".review", max_age=7200) == review
    except signing.BadSignature:
        accepted = False
    if preview_only or (action == "cancel" or items and time_changed) and not accepted:
        from .session_notifications import delivery_enabled
        from .messaging import preview_enabled
        recipients = [i for i in items if i.state == "confirmed" or action == "cancel" and i.net_received > 0]
        if action == "cancel" and not values.get("email_customers"):
            recipients = []
        email_count = sum(delivery_enabled(i) for i in recipients)
        form.data = form.data.copy()
        form.data["review_token"] = signing.dumps(review, salt=SALT + ".review")
        return False, form, {"rows": preview_rows(items), "count": len(items), "before": session,
            "after": candidate, "cancel": action == "cancel", "refund": values.get("resolution") == "with_refund",
            "show_payments": action == "cancel" and any(i.net_received > 0 for i in items),
            "email_unavailable": len(recipients) - email_count if not preview_enabled() else 0}
    before = {"start": session.starts_at.isoformat(), "end": session.ends_at.isoformat(), "room": session.room_id}
    if action == "cancel":
        for item in items:
            choice = values["resolution"] if item.net_received > 0 else "without_refund"
            canceled, _ = cancellation.cancel(user, slug, item.public_id, key=uuid.uuid5(key, str(item.public_id)),
                resolution=choice, reason=values["reason"])
            if values.get("email_customers") and (item.state == "confirmed" or item.net_received > 0):
                from .session_notifications import capture
                capture(canceled, "canceled", before, key, refund_requested=choice == "with_refund",
                    customer_message=values.get("customer_message", ""))
        session.is_published, session.canceled_at = False, timezone.now()
        session.save(update_fields=["is_published", "canceled_at"])
    else:
        # Notice signatures must match the UTC values read back by the mail worker.
        candidate.starts_at = candidate.starts_at.astimezone(datetime_timezone.utc)
        candidate.ends_at = candidate.ends_at.astimezone(datetime_timezone.utc)
        candidate.save(update_fields=["starts_at", "ends_at", "room", "capacity"])
        if time_changed:
            for item in items:
                item.starts_at, item.ends_at, item.room = candidate.starts_at, candidate.ends_at, candidate.room
                source = item.workshop_request
                source.session_snapshot.update(starts_at=item.starts_at.isoformat(), ends_at=item.ends_at.isoformat(),
                    room=item.room.name if item.room else "")
                source.save(update_fields=["session_snapshot"])
                reservations.ensure_capacity(item)
                item.save(update_fields=["starts_at", "ends_at", "room"])
                reservations._audit(user, item, "session_moved", before=before, session_change_key=str(key))
                if item.state == "confirmed":
                    from .session_notifications import capture
                    capture(item, "rescheduled", before, key)
    AdminAuditLog.objects.create(actor=user, action="change", model_label="scheduling.session", object_id=str(pk),
        object_repr=str(session), changes={"action": action, "before": before,
            "after": {"start": str(candidate.starts_at), "end": str(candidate.ends_at), "room": candidate.room_id}},
        extra={"scheduling_profile_id": profile.pk, "session_change_key": str(key), "submission_digest": stamp,
            "reservations": [str(i.public_id) for i in items], "resolution": values.get("resolution", "")})
    # Approved refunds use the existing maintenance recovery worker after commit.
    return True, form, None
