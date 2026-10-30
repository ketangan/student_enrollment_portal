"""Bounded calendar reads. Capacity claims remain owned by reservations.py."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db.models import Count, F, Q, Sum
from django.http import Http404
from django.urls import reverse

from .capabilities import has_capability
from .models import Offering, Reservation, ReservationOccurrence, Session
from .reservations import active_claims
from .access import staff_locations

MAX_EVENTS = 1500


def calendar_available(profile):
    return profile.calendar_enabled and has_capability(profile, "workshops")


def offerings(profile):
    return Offering.objects.filter(location__in=staff_locations(profile), visit_program__isnull=True).select_related("location")


def date_window(params):
    try:
        start, end = (date.fromisoformat(params[key]) for key in ("start", "end"))
        if not 1 <= (end - start).days <= 42 or start.year < 1900 or end.year > 2200:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValidationError("Choose a date range of at most six weeks.")
    return start, end


def local_times(start, end, location):
    zone = ZoneInfo(location.timezone_name)
    return start.astimezone(zone), end.astimezone(zone)


def event_payload(profile, kind, pk, title, start, end, location, room, seats, capacity, published=True):
    local_start, local_end = local_times(start, end, location)
    return {
        "id": f"{kind}-{pk}", "title": title,
        # The multi-location view deliberately uses each venue's local wall clock.
        "start": local_start.replace(tzinfo=None).isoformat(),
        "end": local_end.replace(tzinfo=None).isoformat(), "resourceIds": [str(location.pk)],
        "extendedProps": {"kind": kind, "location": location.name, "zone": location.timezone_name,
            "room": room or "", "seats": seats, "capacity": capacity, "published": published,
            "detailUrl": reverse("scheduling:calendar_detail", kwargs={"school_slug": profile.school.slug, "kind": kind, "pk": str(pk)})},
    }


def calendar_events(profile, params, location=None):
    start, end = date_window(params)
    selected = offerings(profile)
    if location:
        selected = selected.filter(location=location)
    requested = params.getlist("offering")
    if requested:
        if len(requested) > 200 or any(not value.isdecimal() or len(value) > 18 for value in requested):
            raise ValidationError("Choose a valid workshop.")
        ids = {int(value) for value in requested}
        if set(selected.filter(pk__in=ids).values_list("pk", flat=True)) != ids:
            raise ValidationError("Choose a workshop at the selected location.")
        selected = selected.filter(pk__in=ids)
    # UTC padding captures all location-local date boundaries, including DST.
    lower = datetime.combine(start - timedelta(days=1), time.min, timezone.utc)
    upper = datetime.combine(end + timedelta(days=1), time.min, timezone.utc)
    sessions = list(Session.objects.filter(offering__in=selected, starts_at__lt=upper, ends_at__gt=lower)
        .select_related("offering__location", "room").prefetch_related("camps")[:MAX_EVENTS + 1])
    if len(sessions) > MAX_EVENTS:
        raise ValidationError("Too many sessions. Select a location or a shorter date range.")
    session_ids = [item.pk for item in sessions]
    claims = active_claims().filter(profile=profile)
    counts = defaultdict(int)
    if has_capability(profile, "reservations"):
        for row in claims.filter(session_id__in=session_ids).values("session_id").annotate(seats=Sum("quantity")):
            counts[row["session_id"]] += row["seats"]
        if has_capability(profile, "camps"):
            for row in ReservationOccurrence.objects.filter(session_id__in=session_ids, reservation__in=claims).values("session_id").annotate(seats=Sum("reservation__quantity")):
                counts[row["session_id"]] += row["seats"]
    events = []
    for item in sessions:
        camps = list(item.camps.all()) if has_capability(profile, "camps") else []
        event = event_payload(profile, "session", item.pk, item.offering.name, item.starts_at, item.ends_at,
            item.offering.location, str(item.room.name) if item.room else "", counts[item.pk], item.capacity, item.is_published)
        event["extendedProps"]["camp"] = bool(camps)
        events.append(event)
    if has_capability(profile, "parties") and has_capability(profile, "reservations"):
        parties = list(claims.filter(party_request__offering__in=selected, starts_at__lt=upper, ends_at__gt=lower)
            .select_related("party_request__offering__location", "room")[:MAX_EVENTS + 1])
        if len(parties) + len(sessions) > MAX_EVENTS:
            raise ValidationError("Too many events. Select a location or a shorter date range.")
        for item in parties:
            package = item.snapshot.get("party_package") or {}
            events.append(event_payload(profile, "party", item.pk, package.get("name") or item.snapshot.get("workshop", "Party"),
                item.starts_at, item.ends_at, item.party_request.offering.location,
                item.room.name if item.room else "", item.quantity, item.quantity))
    return [event for event in events if event["start"] < end.isoformat()
        and event["end"] > start.isoformat() + "T00:00:00"
        and (params.get("hide_empty") != "1" or event["extendedProps"]["seats"] > 0)]


def detail_time(start, end):
    def date(value):
        return f"{value:%a, %b} {value.day}, {value.year}"

    def clock(value, period=True):
        result = str(value.hour % 12 or 12)
        if value.minute:
            result += f":{value.minute:02d}"
        return result + (value.strftime(" %p") if period else "")

    if start.date() != end.date():
        return f"{date(start)} \u00b7 {clock(start)} {start:%Z} \u2013 {date(end)} \u00b7 {clock(end)} {end:%Z}"
    if start.utcoffset() != end.utcoffset():
        return f"{date(start)} \u00b7 {clock(start)} {start:%Z}\u2013{clock(end)} {end:%Z}"
    return f"{date(start)} \u00b7 {clock(start, start.hour // 12 != end.hour // 12)}\u2013{clock(end)} {end:%Z}"


def calendar_detail(profile, kind, pk):
    from .reservation_queries import ATTENTION, staff_reservations
    if not str(pk).isdecimal() or len(str(pk)) > 18:
        raise Http404
    if kind == "session":
        try:
            session = Session.objects.select_related("offering__location", "room").get(pk=int(pk), offering__in=offerings(profile))
        except (ValueError, Session.DoesNotExist):
            raise Http404
        title, start, end, location = session.offering.name, session.starts_at, session.ends_at, session.offering.location
        predicate = Q(session=session)
        if has_capability(profile, "camps"):
            predicate |= Q(occurrences__session=session)
        url = reverse("scheduling:workshop_detail", kwargs={"school_slug": profile.school.slug, "pk": session.offering_id})
        room = session.room.name if session.room else ""
        capacity, published = session.capacity, session.is_published
        category = "Camp" if has_capability(profile, "camps") and session.camps.exists() else "Group workshop"
    elif kind == "party" and has_capability(profile, "parties") and has_capability(profile, "reservations"):
        try:
            item = active_claims().select_related("party_request__offering__location", "room").get(pk=pk, profile=profile, party_request__offering__in=offerings(profile))
        except (ValueError, ValidationError, Reservation.DoesNotExist):
            raise Http404
        title, start, end, location = item.snapshot.get("workshop", "Party"), item.starts_at, item.ends_at, item.party_request.offering.location
        predicate, room = Q(pk=item.pk), item.room.name if item.room else ""
        url = reverse("scheduling:reservation_detail", kwargs={"school_slug": profile.school.slug, "public_id": item.public_id})
        capacity, published, category = item.quantity, False, "Party"
    else:
        raise Http404
    rows = []
    summary = {"booked": 0, "capacity": capacity, "open": capacity, "needs_action": 0, "reservation_count": 0, "payments": []}
    if has_capability(profile, "reservations"):
        reservations = staff_reservations(profile).filter(predicate).filter(pk__in=active_claims().values("pk")).distinct().order_by("created_at")
        totals = reservations.aggregate(booked=Sum("quantity"), needs_action=Count("pk", filter=ATTENTION), reservation_count=Count("pk"))
        summary.update(booked=totals["booked"] or 0, needs_action=totals["needs_action"], reservation_count=totals["reservation_count"])
        summary["open"] = max(0, capacity - summary["booked"])
        summary["payments"] = list(reservations.order_by().values("order__currency").annotate(
            received=Sum("net_received"), expected=Sum("effective_total")))
        for item in reservations[:501]:
            rows.append({"name": item.snapshot.get("name", "Guest"), "quantity": item.quantity,
                "state": item.get_state_display(), "received": item.net_received, "total": item.effective_total,
                "payment_status": "Paid" if item.net_received >= item.effective_total else "Part paid" if item.net_received > 0 else "Unpaid",
                "currency": item.order.currency, "url": reverse("scheduling:reservation_detail", kwargs={"school_slug": profile.school.slug, "public_id": item.public_id})})
    local_start, local_end = local_times(start, end, location)
    return {"title": title, "location": location.name, "room": room,
        "time": detail_time(local_start, local_end),
        "url": url, "category": category, "availability": "Reserved" if kind == "party" else "Published" if published else "Unpublished",
        "summary": summary, "reservations": rows[:500], "truncated": len(rows) > 500}


def calendar_summary(profile, events):
    from .reservation_queries import staff_reservations
    result = {"unpaid": 0, "balance": 0, "empty": sum(event["extendedProps"]["seats"] == 0 for event in events)}
    if not has_capability(profile, "reservations"):
        return result
    sessions = [int(event["id"].split("-",1)[1]) for event in events if event["extendedProps"]["kind"] == "session"]
    parties = [int(event["id"].split("-",1)[1]) for event in events if event["extendedProps"]["kind"] == "party"]
    predicate = Q(session_id__in=sessions) | Q(pk__in=parties)
    if has_capability(profile, "camps"):
        predicate |= Q(occurrences__session_id__in=sessions)
    totals = staff_reservations(profile).filter(predicate, pk__in=active_claims().values("pk")).aggregate(
        unpaid=Count("pk", distinct=True, filter=Q(net_received__lte=0, effective_total__gt=0)),
        balance=Count("pk", distinct=True, filter=Q(net_received__gt=0, net_received__lt=F("effective_total"))))
    return {**result, **totals}
