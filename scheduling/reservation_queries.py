"""Shared staff views of payment exceptions; no money is moved here."""

from decimal import Decimal
from django.db.models import Case, CharField, DecimalField, Exists, ExpressionWrapper, F, IntegerField, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.payments.models import LedgerEntry, PaymentAttempt, Refund
from .capabilities import require_capability
from .models import Reservation, StockAllocation
from .access import scope_reservations, staff_locations


ATTENTION = (Q(state="review") | Q(state="canceled", net_received__gt=0)
    | Q(net_received__gt=F("effective_total")) | Q(payment_pending=True) | Q(refund_pending=True))
BALANCE_DUE = Q(effective_total__gt=F("net_received")) & ~Q(state="canceled")


def completion_filter_query(query):
    """Keep existing saved links working while exposing the positive switch."""
    query = query.copy()
    if "show_completed" in query:
        show = query.get("show_completed") in {"1", "true", "on"}
    else:
        show = query.get("hide_completed") in {"0", "false"}
    query["show_completed"] = "true" if show else "false"
    query["hide_completed"] = "false" if show else "true"
    return query


def reservation_location(location):
    return (Q(workshop_request__session__offering__location=location)
        | Q(party_request__offering__location=location)
        | Q(camp_registration__camp__offering__location=location))


def preparation_queue(items, now):
    return items.filter(state="confirmed", prepared_at__isnull=True, ends_at__gt=now).exclude(
        stock_allocations__consumed_at__isnull=False)


def hide_completed_reservations(items, now):
    return items.exclude(Q(state="completed") | Q(ends_at__lte=now))


def staff_reservations(profile):
    require_capability(profile, "reservations")
    net = LedgerEntry.objects.filter(order_id=OuterRef("order_id")).order_by().values("order_id").annotate(
        value=Sum(Case(When(kind="payment", then=F("amount_cents")), default=-F("amount_cents"), output_field=IntegerField()))).values("value")
    extras = LedgerEntry.objects.filter(order_id=OuterRef("order_id")).order_by().values("order_id").annotate(
        value=Sum(Case(When(kind="void", then=-F("extra_charge_cents")), default=F("extra_charge_cents"), output_field=IntegerField()))).values("value")
    return scope_reservations(Reservation.objects.filter(profile=profile, visit_booking__isnull=True), profile).select_related("order").annotate(
        location_name=Coalesce("workshop_request__session__offering__location__name", "party_request__offering__location__name",
            "camp_registration__camp__offering__location__name", Value(""), output_field=CharField()),
        net_received=Coalesce(Subquery(net), 0),
        effective_total=F("order__total_cents") + Coalesce(Subquery(extras), 0),
        payment_pending=Exists(PaymentAttempt.objects.filter(order_id=OuterRef("order_id"), state__in=PaymentAttempt.ACTIVE)),
        refund_pending=Exists(Refund.objects.filter(attempt__order_id=OuterRef("order_id"), state__in=Refund.ACTIVE)),
        materials_used=Exists(StockAllocation.objects.filter(reservation_id=OuterRef("pk"), consumed_at__isnull=False)),
    ).annotate(total_amount=ExpressionWrapper(F("effective_total") / Value(Decimal("100.0")), output_field=DecimalField()))


def filter_reservations(items, form, profile):
    if not form.is_valid():
        return items.none()
    data = form.cleaned_data
    if data["q"]:
        query = data["q"]
        items = items.filter(Q(snapshot__name__icontains=query) | Q(snapshot__workshop__icontains=query)
            | Q(workshop_request__purchaser_email__icontains=query) | Q(party_request__organizer_email__icontains=query)
            | Q(camp_registration__purchaser_email__icontains=query))
    if data["state"]:
        items = items.filter(state=data["state"])
    if data["offering"]:
        items = items.filter(Q(workshop_request__session__offering=data["offering"])
            | Q(party_request__offering=data["offering"]) | Q(camp_registration__camp__offering=data["offering"]))
    complete = Q(state="completed") | Q(prepared_at__isnull=False) | Q(materials_used=True)
    if data["prep"] == "not_required":
        items = items.filter(state="canceled")
    elif data["prep"] == "complete":
        items = items.exclude(state="canceled").filter(complete)
    elif data["prep"] == "pending":
        items = items.filter(state="confirmed").exclude(complete)
    elif data["prep"] == "hold":
        items = items.exclude(state__in=["canceled", "confirmed"]).exclude(complete)
    return filter_local_dates(items, data, profile, reservation_location)


def filter_local_dates(items, data, profile, location_predicate):
    """Apply inclusive calendar dates in each permitted studio's local timezone."""
    if data["location"] or data["date_from"] or data["date_to"]:
        locations = [data["location"]] if data["location"] else staff_locations(profile)
        predicate = Q(pk__in=[])
        for location in locations:
            local = location_predicate(location)
            zone = ZoneInfo(location.timezone_name)
            if data["date_from"]:
                local &= Q(starts_at__gte=datetime.combine(data["date_from"], time.min, zone))
            if data["date_to"]:
                # Local midnight boundaries include the entire final day, including DST changes.
                local &= Q(starts_at__lt=datetime.combine(data["date_to"] + timedelta(days=1), time.min, zone))
            predicate |= local
        items = items.filter(predicate)
    return items
