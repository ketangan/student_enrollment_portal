"""Raw materials share reservation locks and the existing payment lifecycle."""
from collections import Counter, defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from .access import get_staff_reservation, require_location_access, staff_locations
from .inventory_models import validate_amount
from .models import ChoiceOption, SchedulingProfile, StockAllocation, CatalogVariant, StockLevel, StockAdjustment, IncomingDelivery


def claims():
    from .reservations import active_claims
    return StockAllocation.objects.filter(consumed_at__isnull=True, reservation__in=active_claims())


def reserved(level, exclude=None):
    return claims().filter(level=level).exclude(reservation_id=exclude).aggregate(total=Sum("quantity"))["total"] or Decimal(0)


def available(level, exclude=None, *, starts_at=None):
    if starts_at is not None:
        return StockForecast([level], exclude=exclude).available(level, starts_at)
    return max(Decimal(0), level.on_hand - reserved(level, exclude))


class StockForecast:
    """One batched supply/demand timeline; future commitments constrain earlier sales too."""

    def __init__(self, levels, exclude=None):
        self.levels = {level.pk: level for level in levels}
        self.events = defaultdict(Counter)
        self.bookings = defaultdict(lambda: defaultdict(list))
        for delivery in IncomingDelivery.objects.filter(level_id__in=self.levels, canceled=False, received__lt=F("quantity")):
            level = self.levels[delivery.level_id]
            if delivery.arrives_on >= self.today(level):
                self.events[level.pk][delivery.arrives_on] += delivery.remaining
        allocations = claims().filter(level_id__in=self.levels).exclude(reservation_id=exclude).select_related("reservation").only(
            "level_id", "quantity", "reservation__id", "reservation__public_id", "reservation__starts_at")
        for claim in allocations:
            level = self.levels[claim.level_id]
            day = self.needed_on(level, claim.reservation.starts_at)
            self.events[level.pk][day] -= claim.quantity
            self.bookings[level.pk][day].append(claim.reservation)

    @staticmethod
    def today(level):
        return timezone.localdate(timezone.now(), ZoneInfo(level.location.timezone_name))

    def needed_on(self, level, starts_at):
        if starts_at is None:
            return self.today(level)
        day = timezone.localdate(starts_at, ZoneInfo(level.location.timezone_name))
        return max(self.today(level), day - timedelta(days=level.location.inventory_buffer_days))

    def available(self, level, starts_at):
        day = self.needed_on(level, starts_at)
        events = self.events[level.pk].copy()
        events[day] += 0
        balance, remaining = level.on_hand, None
        for date, delta in sorted(events.items()):
            balance += delta
            if date >= day:
                remaining = balance if remaining is None else min(remaining, balance)
        return max(Decimal(0), remaining)

    def shortages(self):
        rows = []
        for level in self.levels.values():
            balance = level.on_hand
            for day, delta in sorted(self.events[level.pk].items()):
                balance += delta
                if balance < 0:
                    for booking in self.bookings[level.pk][day]:
                        rows.append({"level": level, "reservation": booking, "needed_on": day})
        return rows


def selection_items(offering, participants):
    if not offering.inventory_catalog_id:
        return Counter()
    catalog = offering.inventory_catalog
    groups = set(catalog.choice_groups.filter(tracks_stock=True, is_active=True).values_list("pk", flat=True))
    if not groups:
        raise ValidationError("This catalog has no design choices. Contact the studio.")
    variants = {v.choice_key: v for v in catalog.items.filter(is_active=True)}
    result = Counter()
    for person in participants:
        selected = [s["option_id"] for s in person["selections"] if s["group_id"] in groups]
        variant = variants.get(":".join(map(str, sorted(selected))))
        if not variant or len(selected) != len(groups):
            raise ValidationError("This design/color combination is unavailable. Choose another combination.")
        result[variant.pk] += 1
    return result


def material_requirements(offering, counts):
    variants = {v.pk: v for v in CatalogVariant.objects.filter(pk__in=counts,
        catalog_id=offering.inventory_catalog_id, catalog__profile_id=offering.location.profile_id)
        .prefetch_related("requirements__material")}
    inactive = set(ChoiceOption.objects.filter(group__catalog_id=offering.inventory_catalog_id,
        is_active=False).values_list("pk", flat=True))
    result = Counter()
    for variant_id, quantity in counts.items():
        variant = variants.get(variant_id)
        if (not variant or not variant.is_active or inactive.intersection(variant.option_ids)
                or type(quantity) is not int or quantity < 1):
            raise ValidationError("A selected design is no longer available. Choose another design.")
        recipe = list(variant.requirements.all())
        if not recipe:
            raise ValidationError(f"{variant.name} has no material recipe yet. Contact the studio.")
        for row in recipe:
            if not row.material.is_active or row.material.profile_id != offering.location.profile_id:
                raise ValidationError(f"Materials for {variant.name} are unavailable. Choose another design.")
            result[row.material_id] += row.quantity * quantity
    return result


def check_materials(offering, quantities, exclude=None, *, starts_at=None):
    levels = {level.material_id: level for level in StockLevel.objects.filter(
        material_id__in=quantities, material__profile_id=offering.location.profile_id,
        location=offering.location).select_related("material", "location")}
    forecast = StockForecast(levels.values(), exclude=exclude) if starts_at is not None else None
    result = []
    for material_id, quantity in quantities.items():
        level = levels.get(material_id)
        remaining = (forecast.available(level, starts_at) if forecast else available(level, exclude)) if level else Decimal(0)
        if quantity > remaining:
            if not level:
                raise ValidationError("Materials are unavailable at this studio. Choose another design or contact the studio.")
            raise ValidationError(f"{level.material.name}: only {remaining:g} {level.material.unit} available at {level.location.name}; this booking needs {quantity:g}.")
        result.append((level, quantity))
    return result


def check_items(offering, counts, exclude=None, *, starts_at=None):
    return check_materials(offering, material_requirements(offering, counts), exclude, starts_at=starts_at)


def check_selections(offering, participants, *, starts_at=None):
    return check_items(offering, selection_items(offering, participants), starts_at=starts_at)


def allocate(reservation):
    if reservation.workshop_request_id:
        source = reservation.workshop_request
        offering = source.session.offering
        counts = selection_items(offering, [{"selections": p.selections} for p in source.participants.all()])
    elif reservation.camp_registration_id:
        source = reservation.camp_registration
        offering = source.camp.offering
        # Recipes are per participant for the entire camp, not per attendance date.
        counts = selection_items(offering, source.participants)
    elif reservation.party_request_id:
        source = reservation.party_request
        offering = source.offering
        counts = Counter()
        if offering.inventory_catalog_id:
            if not source.materials:
                reservation.snapshot = {**reservation.snapshot, "guest_selections_pending": True,
                    "material_catalog_id": offering.inventory_catalog_id,
                    "material_location_id": offering.location_id, "materials": []}
                reservation.save(update_fields=["snapshot"])
                return
            for row in source.materials:
                counts[row["item_id"]] += row["quantity"]
            if sum(counts.values()) != source.group_size:
                raise ValidationError("Choose a design for every party participant before reserving.")
    else:
        return
    if not offering.inventory_catalog_id:
        return
    snapshot = []
    for level, quantity in check_items(offering, counts, reservation.pk, starts_at=reservation.starts_at):
        StockAllocation.objects.create(reservation=reservation, level=level, quantity=quantity)
        snapshot.append({"material_id": level.material_id, "name": level.material.name,
                         "unit": level.material.unit, "quantity": str(quantity)})
    reservation.snapshot = {**reservation.snapshot, "materials": snapshot,
                           "material_catalog_id": offering.inventory_catalog_id,
                           "material_location_id": offering.location_id}
    reservation.save(update_fields=["snapshot"])


def validate_allocations(reservation, allocations=None, *, allow_pending=False):
    if reservation.snapshot.get("guest_selections_pending"):
        if (not reservation.party_request_id
                or reservation.party_request.materials or reservation.stock_allocations.exists()):
            raise ValidationError("Pending guest selections do not match this booking. Contact support.")
        if not allow_pending:
            raise ValidationError("Add the guest designs and colors before preparing or completing this party.")
        return
    if not reservation.snapshot.get("material_catalog_id"):
        source = reservation.workshop_request or reservation.camp_registration or reservation.party_request
        offering = (source.session.offering if reservation.workshop_request_id else source.camp.offering
                    if reservation.camp_registration_id else source.offering) if source else None
        if offering and offering.inventory_catalog_id:
            raise ValidationError("This booking is missing its saved material recipe. Contact support before continuing.")
        return
    allocations = list(allocations if allocations is not None else reservation.stock_allocations.select_related("level__material", "level__location"))
    expected = {r["material_id"]: Decimal(r["quantity"]) for r in reservation.snapshot.get("materials", [])}
    actual = {a.level.material_id: a.quantity for a in allocations}
    if (not expected or expected != actual or len(allocations) != len(expected)
            or any(a.level.material.profile_id != reservation.profile_id
                   or a.level.location.profile_id != reservation.profile_id
                   or a.level.location_id != reservation.snapshot.get("material_location_id") for a in allocations)):
        raise ValidationError("Reserved materials do not match this booking. Contact support before continuing.")


def ensure_stock(reservation, *, physical=False):
    if not reservation.pk:
        return
    validate_allocations(reservation, allow_pending=True)
    allocations = list(reservation.stock_allocations.filter(consumed_at__isnull=True).select_related("level__material", "level__location"))
    forecast = StockForecast([a.level for a in allocations], exclude=reservation.pk)
    for allocation in allocations:
        remaining = forecast.available(allocation.level, timezone.now() if physical else reservation.starts_at)
        if physical:
            remaining = min(remaining, allocation.level.on_hand)
        if allocation.quantity > remaining:
            raise ValidationError(f"{allocation.level.material.name} no longer has enough stock at {allocation.level.location.name}.")


@transaction.atomic
def adjust(user, slug, level_id, *, on_hand, previous, reason, low_stock_threshold=None, previous_threshold=None):
    profile, _ = require_location_access(user, slug, "setup.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "setup.edit")
    level = get_object_or_404(StockLevel, pk=level_id, location__in=staff_locations(profile), material__profile=profile)
    if level.on_hand != previous:
        raise ValidationError("Stock changed while this page was open. Reload before saving.")
    on_hand = validate_amount(on_hand, level.material.unit, allow_zero=True)
    if not reason.strip() or len(reason) > 250:
        raise ValidationError("Enter a reason for the adjustment.")
    if on_hand < reserved(level):
        previous_count = level.on_hand
        level.on_hand = on_hand
        shortages = StockForecast([level]).shortages()
        level.on_hand = previous_count
        if shortages:
            raise ValidationError("The count cannot be lower than reserved materials. Resolve affected reservations first.")
    if low_stock_threshold is not None:
        low_stock_threshold = validate_amount(low_stock_threshold, level.material.unit, allow_zero=True)
        if previous_threshold != level.low_stock_threshold:
            raise ValidationError("The low-stock alert changed while this page was open. Reload before saving.")
        if low_stock_threshold != level.low_stock_threshold:
            from core.models import AdminAuditLog
            AdminAuditLog.objects.create(actor=user, action="change", model_label="scheduling.stocklevel",
                object_id=str(level.pk), object_repr=str(level.material),
                changes={"low_stock_threshold": {"from": str(level.low_stock_threshold), "to": str(low_stock_threshold)}},
                extra={"scheduling_profile_id": profile.pk, "school_slug": slug})
            level.low_stock_threshold = low_stock_threshold
            level.save(update_fields=["low_stock_threshold"])
    delta = on_hand - level.on_hand
    if delta:
        StockAdjustment.objects.create(level=level, delta=delta, reason=reason.strip(), actor=user)
        level.on_hand = on_hand
        level.save(update_fields=["on_hand"])
    return level


@transaction.atomic
def consume(user, slug, public_id):
    profile, _ = require_location_access(user, slug, "sessions.edit")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit")
    reservation = get_staff_reservation(profile, public_id)
    if reservation.state != "confirmed":
        raise ValidationError("Only confirmed bookings can use materials.")
    validate_allocations(reservation)
    ensure_stock(reservation, physical=True)
    for allocation in reservation.stock_allocations.filter(consumed_at__isnull=True).select_related("level"):
        level = allocation.level
        level.on_hand -= allocation.quantity
        level.save(update_fields=["on_hand"])
        allocation.consumed_at = timezone.now()
        allocation.save(update_fields=["consumed_at"])
        StockAdjustment.objects.create(level=level, delta=-allocation.quantity,
            reason=f"Materials used for reservation {reservation.public_id}", actor=user)


def stock_status(level, available):
    if not level.material.is_active:
        return "inactive", "Inactive"
    if level.on_hand <= 0:
        return "empty", "Out of stock"
    if available <= 0:
        return "reserved", "Fully reserved"
    if level.low_stock_threshold > 0 and available <= level.low_stock_threshold:
        return "low", "Low stock"
    return "available", "Available"


def stock_rows(profile, location=None, catalog=None):
    levels = StockLevel.objects.filter(location__in=staff_locations(profile), material__profile=profile)
    if location:
        levels = levels.filter(location=location)
    if catalog:
        levels = levels.filter(material__recipes__variant__catalog=catalog).distinct()
    totals = {row["level_id"]: row["total"] for row in claims().filter(level__in=levels)
              .values("level_id").annotate(total=Sum("quantity"))}
    rows = []
    for level in levels.select_related("material", "location").order_by("location__name", "material__name", "pk"):
        available = max(0, level.on_hand-totals.get(level.pk, 0))
        status, label = stock_status(level, available)
        rows.append({"level": level, "reserved": totals.get(level.pk, 0), "available": available,
            "status": status, "status_label": label})
    return rows


def availability_payload(offering, *, starts_at=None):
    rows = stock_rows(offering.location.profile, offering.location, offering.inventory_catalog)
    forecast = StockForecast([row["level"] for row in rows]) if starts_at is not None else None
    materials = {str(row["level"].material_id): int((forecast.available(row["level"], starts_at) if forecast else row["available"]) * 1000) for row in rows}
    variants = []
    for variant in offering.inventory_catalog.items.filter(is_active=True).prefetch_related("requirements__material"):
        recipe = list(variant.requirements.all())
        if recipe and all(r.material.is_active and r.material.profile_id == offering.location.profile_id for r in recipe):
            variants.append({"options": variant.option_ids, "image_url": variant.image_url,
                             "requirements": {str(r.material_id): int(r.quantity * 1000) for r in recipe}})
    return {"variants": variants, "materials": materials}
