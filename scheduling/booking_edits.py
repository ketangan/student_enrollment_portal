"""Staff amendments retain the reservation, agreed prices and payment evidence."""
from collections import Counter
from copy import copy, deepcopy
import json
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from django import forms
from django.core import signing
from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import School
from core.payments.models import PaymentAttempt, PaymentOrder, Refund
from core.payments.service import totals
from . import inventory, participant_preparation, reservations, party_amendments
from .access import get_staff_reservation
from .lessons import digest
from .models import PartyPackage, RequestParticipant, StockAllocation
from .request_forms import MAX_PARTICIPANTS, PurchaserForm
from .requests import submission_digest
from .staff_workshops import session_label, session_revision, check_room

SALT = "scheduling.staff-booking-edit.v1"


def editable(item):
    from .capabilities import has_capability
    return (item.state == "confirmed" and item.starts_at > timezone.now()
        and ((item.workshop_request_id and has_capability(item.profile, "workshops"))
             or (item.party_request_id and has_capability(item.profile, "parties"))))


def unavailable_reason(item):
    if item.state in {"held", "review"}:
        return "Check payment and confirm this booking before editing."
    return "Only upcoming, confirmed bookings can be edited."


def offering_for(item):
    return item.workshop_request.session.offering if item.workshop_request_id else item.party_request.offering


def evidence(item):
    source = item.workshop_request if item.workshop_request_id else item.party_request
    values = {"start": item.starts_at.isoformat(), "end": item.ends_at.isoformat(),
        "session": item.session_id, "room": item.room_id, "quantity": item.quantity,
        "state": item.state, "snapshot": item.snapshot, "total": item.order.total_cents,
        "deposit": item.order.deposit_cents, "money": {k: v for k, v in totals(item.order).items() if k.endswith("_cents")},
        "source": json.loads(json.dumps({field.name: field.value_from_object(source) for field in source._meta.fields}, cls=DjangoJSONEncoder)),
        "people": list(source.participants.values("pk", "name", "selections", "selections_deferred")) if item.workshop_request_id else []}
    return json.loads(json.dumps(values, cls=DjangoJSONEncoder))


def token_for(user, item):
    from .parties import inquiry_digest
    sessions = {str(s.pk): session_revision(s) for s in offering_for(item).sessions.filter(
        starts_at__gt=timezone.now(), camps__isnull=True)} if item.workshop_request_id else {}
    return signing.dumps({"user": user.pk, "item": str(item.public_id), "revision": digest(evidence(item)),
        "sessions": sessions,
        "party_config": inquiry_digest(offering_for(item)) if item.party_request_id else None}, salt=SALT, compress=True)


class BookingEditForm(PurchaserForm):
    edit_token = forms.CharField(widget=forms.HiddenInput)
    review_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, item, user, **kwargs):
        self.item = item
        self.offering = offering_for(item)
        source = item.workshop_request if item.workshop_request_id else item.party_request
        prefix = "purchaser" if item.workshop_request_id else "organizer"
        initial = {"purchaser_" + key: getattr(source, prefix + "_" + key) for key in ("name", "email", "phone")}
        initial["edit_token"] = token_for(user, item)
        super().__init__(*args, initial=initial, **kwargs)
        self.fields["purchaser_name"].label = "Customer name"
        self.removal_count = item.quantity
        if item.workshop_request_id:
            self.fields["session"] = forms.ModelChoiceField(label="Workshop time", initial=item.session_id,
                queryset=self.offering.sessions.filter(starts_at__gt=timezone.now(), camps__isnull=True).select_related("room"))
            self.fields["session"].label_from_instance = lambda s: session_label(s, self.offering.location.timezone_name)
            self.fields["quantity"] = forms.IntegerField(label="Participants", min_value=1,
                max_value=max(MAX_PARTICIPANTS, item.quantity), initial=item.quantity)
            self.fields["remove"] = forms.ModelMultipleChoiceField(label="Participants to remove", required=False,
                queryset=source.participants.all(), widget=forms.CheckboxSelectMultiple)
            self.fields["remove"].label_from_instance = lambda p: p.name
        else:
            from .guest_lists import saved_rows
            from .party_policy import party_policy
            self.fields["starts_at"] = forms.DateTimeField(label="Party date and time", initial=item.starts_at,
                widget=forms.DateTimeInput(format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}))
            self.fields["package"] = forms.ModelChoiceField(label="Package", required=bool(source.package_id),
                initial=source.package_id, empty_label=None if source.package_id else "Custom party",
                queryset=PartyPackage.objects.filter(offering=self.offering).filter(
                    Q(is_published=True) | Q(pk=source.package_id)).select_related("room", "offering__location"))
            self.fields["quantity"] = forms.IntegerField(label="Participants", min_value=1,
                max_value=max(item.quantity, party_policy(item.profile.school)["maximum_participants"]), initial=item.quantity)
            self.fields["price_adjustment"] = forms.DecimalField(required=False, decimal_places=2, max_digits=8,
                label=f"Price adjustment ({item.order.currency.upper()}{', before tax' if source.package_id else ''})",
                widget=forms.NumberInput(attrs={"placeholder": "0.00", "step": "0.01"}))
            self.fields["adjustment_note"] = forms.CharField(label="Adjustment note (optional)", required=False,
                max_length=500, widget=forms.Textarea(attrs={"rows": 2}))
            rows = saved_rows(item)
            self.removal_count = len(rows)
            self.fields["remove"] = forms.MultipleChoiceField(label="Participants to remove", required=False,
                choices=[(str(index), row.get("guest_name") or f"Participant {index + 1}") for index, row in enumerate(rows)],
                widget=forms.CheckboxSelectMultiple)

    def clean(self):
        values = super().clean()
        if self.item.party_request_id and values.get("starts_at") == self.item.starts_at.replace(second=0, microsecond=0):
            values["starts_at"] = self.item.starts_at
        if "quantity" in values:
            removed = len(values.get("remove", []))
            expected = max(0, self.removal_count - values["quantity"])
            if removed != expected:
                self.add_error("remove", f"Select exactly {expected} participant(s) to remove." if expected else "Clear the participant removals, or reduce the count.")
        return values


def price_for(item, quantity):
    if quantity == item.quantity:
        return item.order.total_cents, item.order.deposit_cents, None
    saved = item.workshop_request.session_snapshot.get("price_breakdown", {})
    try:
        unit, remainder = divmod(saved["subtotal_cents"], item.quantity)
        deposit, deposit_remainder = divmod(item.order.deposit_cents, item.quantity)
        rate = Decimal(saved["tax_rate"])
        original_total = int(saved["total_cents"])
        if remainder or deposit_remainder or not rate.is_finite() or rate < 0:
            raise ValueError
    except (KeyError, ValueError, TypeError, ArithmeticError):
        raise ValidationError("This booking has no reliable per-participant price. Its count cannot be changed here; contact details and time can still be edited.") from None
    subtotal = unit * quantity
    tax = int((Decimal(subtotal) * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    breakdown = {"subtotal_cents": subtotal, "tax_cents": tax, "total_cents": subtotal + tax, "tax_rate": str(rate)}
    # Retain any explicitly agreed difference from the original standard quote.
    total = item.order.total_cents + breakdown["total_cents"] - original_total
    if total < 0 or total < deposit * quantity:
        raise ValidationError("The revised price conflicts with the agreed deposit. Review the booking's pricing first.")
    return total, deposit * quantity, breakdown


def resize_workshop(item, quantity, removed):
    if quantity == item.quantity:
        return
    from .legacy_materials import workshop_recipes
    source, offering = item.workshop_request, offering_for(item)
    participant_preparation.seed(item)
    people = list(source.participants.all())
    if len(people) != item.quantity:
        raise ValidationError("The saved participant count does not match this booking. Review its participant details first.")
    removed_ids = {p.pk for p in removed}
    if offering.inventory_catalog_id:
        inventory.validate_allocations(item, allow_pending=True)
        recipes = workshop_recipes(item)
        recipes = {key: value for key, value in recipes.items() if int(key) not in removed_ids}
        quantities = Counter()
        for recipe in recipes.values():
            for material, amount in recipe.items():
                quantities[int(material)] += Decimal(amount)
        levels = inventory.check_materials(offering, quantities, item.pk, starts_at=item.starts_at)
        item.stock_allocations.all().delete()
        StockAllocation.objects.bulk_create([StockAllocation(reservation=item, level=level, quantity=amount) for level, amount in levels])
        item.snapshot.update(workshop_participant_materials=recipes, materials=[{
            "material_id": level.material_id, "name": level.material.name, "unit": level.material.unit,
            "quantity": str(amount)} for level, amount in levels])
    source.participants.filter(pk__in=removed_ids).delete()
    remaining = [person for person in people if person.pk not in removed_ids]
    # Existing positions stay stable; new rows append without renumbering survivors.
    position = max((p.position for p in people), default=-1) + 1
    RequestParticipant.objects.bulk_create([RequestParticipant(request=source, position=position + i,
        name=f"Participant {len(remaining) + i + 1}", selections=[], selections_deferred=True)
        for i in range(max(0, quantity - len(remaining)))])
    for pk in removed_ids:
        item.snapshot["participant_preparation"].pop(str(pk), None)
    item.snapshot["workshop_selections_pending"] = source.participants.filter(selections_deferred=True).exists()
    item.prepared_at = item.prepared_by = None
    item.preparation_version += 1
    item.quantity = quantity


@transaction.atomic
def save(user, slug, public_id, data):
    profile = reservations.locked_profile(user, slug)
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    School.objects.select_for_update().get(pk=profile.school_id)
    item.order = PaymentOrder.objects.select_for_update().get(pk=item.order_id)
    form = BookingEditForm(data, item=item, user=user)
    stamp = submission_digest(data)
    receipt = {"token": data.get("edit_token", ""), "digest": stamp}
    if item.snapshot.get("staff_edit_receipt") == receipt:
        return item, form, None
    if not editable(item) or item.stock_allocations.filter(consumed_at__isnull=False).exists():
        raise ValidationError("Only upcoming, confirmed bookings with unused materials can be edited.")
    try:
        token = signing.loads(data.get("edit_token", ""), salt=SALT, max_age=7200)
        if (token["user"], token["item"], token["revision"]) != (user.pk, str(item.public_id), digest(evidence(item))):
            raise ValueError
    except (signing.BadSignature, ValueError, TypeError, KeyError):
        raise ValidationError("This booking changed. Reopen Edit booking before saving.") from None
    if (item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists()
            or Refund.objects.filter(attempt__order=item.order, state__in=Refund.ACTIVE).exists()):
        raise ValidationError("A payment or refund is still processing. Check its status before editing this booking.")
    with timezone.override(ZoneInfo(offering_for(item).location.timezone_name)):
        if not form.is_valid():
            return None, form, None
    values = form.cleaned_data
    if item.workshop_request_id and token.get("sessions", {}).get(str(values["session"].pk)) != session_revision(values["session"]):
        raise ValidationError("The selected workshop time changed. Reopen Edit booking to review it.")
    proposal = None
    if item.party_request_id:
        from .parties import inquiry_digest
        if token.get("party_config") != inquiry_digest(offering_for(item)):
            raise ValidationError("The party settings changed. Reopen Edit booking to review them.")
        proposal = party_amendments.plan(item, values)
        # Validate the proposed space/time before asking staff to confirm pricing.
        candidate = copy(item)
        candidate.snapshot = deepcopy(item.snapshot)
        candidate.starts_at = values["starts_at"]
        duration = proposal["duration"] if proposal else item.ends_at - item.starts_at
        from datetime import timezone as datetime_timezone
        try:
            candidate.ends_at = candidate.starts_at.astimezone(datetime_timezone.utc) + duration
        except OverflowError:
            raise ValidationError("Choose a valid party date.") from None
        if proposal:
            candidate.room, candidate.quantity = proposal["room"], proposal["quantity"]
            candidate.snapshot["space"] = proposal["space"]
            candidate.exclusive = proposal["space"] in ("exclusive", "studio")
        if proposal or candidate.starts_at != item.starts_at:
            reservations.ensure_party_capacity(candidate.room, candidate.starts_at, candidate.ends_at,
                candidate.quantity, exclusive=candidate.exclusive, space=candidate.snapshot.get("space"), exclude=item.pk)
    before = evidence(item)
    total, deposit, breakdown = price_for(item, values["quantity"]) if item.workshop_request_id else (item.order.total_cents, item.order.deposit_cents, None)
    if proposal:
        total, deposit = proposal["total"], proposal["deposit"]
    money = totals(item.order)
    updated_total = money["total_cents"] + total - item.order.total_cents
    if updated_total < 0:
        raise ValidationError("The revised price is less than the existing discounts or credits. Review those adjustments first.")
    if total != item.order.total_cents or proposal:
        preview_data = data.copy()
        preview_data.pop("review_token", None)
        review = {"edit": data.get("edit_token"), "data": submission_digest(preview_data), "total": updated_total}
        try:
            confirmed = signing.loads(values.get("review_token", ""), salt=SALT + ".review", max_age=7200) == review
        except signing.BadSignature:
            confirmed = False
        if not confirmed:
            form.data = form.data.copy()
            form.data["review_token"] = signing.dumps(review, salt=SALT + ".review")
            return None, form, {"total": Decimal(updated_total) / 100, "previous": money["total"],
                "balance": Decimal(max(0, updated_total - money["net_cents"])) / 100,
                "excess": Decimal(max(0, money["net_cents"] - updated_total)) / 100,
                "paid": money["paid"], "refunded": money["refunded"],
                "money": money, "base_total": Decimal(total) / 100,
                "party": proposal, "starts_at": candidate.starts_at if proposal else None,
                "ends_at": candidate.ends_at if proposal else None,
                "schedule_changed": bool(proposal and (candidate.starts_at != item.starts_at or candidate.ends_at != item.ends_at)),
                "room_changed": bool(proposal and candidate.room_id != item.room_id),
                "currency": item.order.currency.upper()}
    if item.workshop_request_id:
        session = values["session"]
        if session.pk != item.session_id:
            check_room(session, exclude=item.pk)
        item.session, item.room = session, session.room
        item.starts_at, item.ends_at = session.starts_at, session.ends_at
        resize_workshop(item, values["quantity"], values["remove"])
        source = item.workshop_request
        source.session = session
        source.session_snapshot.update(starts_at=item.starts_at.isoformat(), ends_at=item.ends_at.isoformat(),
            room=item.room.name if item.room else "")
        if breakdown:
            source.session_snapshot["price_breakdown"] = breakdown
        prefix = "purchaser"
    else:
        item.starts_at, item.ends_at = candidate.starts_at, candidate.ends_at
        party_amendments.apply(item, values, proposal)
        source = item.party_request
        source.starts_at, source.ends_at = item.starts_at, item.ends_at
        source.revision += 1
        prefix = "organizer"
    for key in ("name", "email", "phone"):
        setattr(source, prefix + "_" + key, values["purchaser_" + key])
    source.save()
    item.snapshot.update(name=values["purchaser_name"], organizer_phone=values["purchaser_phone"], staff_edit_receipt=receipt)
    if (before["start"], before["end"], before["room"], before["quantity"]) != (
            item.starts_at.isoformat(), item.ends_at.isoformat(), item.room_id, item.quantity) or proposal:
        reservations.ensure_capacity(item)
    item.full_clean()
    item.save()
    item.order.total_cents, item.order.deposit_cents = total, deposit
    item.order.full_clean()
    item.order.save(update_fields=["total_cents", "deposit_cents"])
    reservations._audit(user, item, "staff_booking_updated", before=before, after=evidence(item),
        adjustment_note=values.get("adjustment_note", ""), price_adjustment=str(values.get("price_adjustment") or "0"))
    return item, form, None
