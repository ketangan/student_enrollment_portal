"""Bounded, tenant-scoped daily workbench. Never calls an external provider."""

from datetime import timedelta
from urllib.parse import urlencode
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from .capabilities import has_capability
from .access import scope_locations, staff_locations, location_restricted
from .models import LessonAvailability, LessonOccurrence, Location, Offering, PartyRequest, Reservation, Session, WorkshopRequest
from .reservation_queries import ATTENTION, reservation_location, staff_reservations, preparation_queue


def overview(profile, *, location=None):
    if location is not None and location.profile_id != profile.pk:
        raise ValidationError("Choose a location in this workspace.")
    now = timezone.now()
    end = now + timedelta(days=7)
    attention, steps, agenda = [], [], []
    cards, activity = [], []

    def url(name, **kwargs):
        return reverse(f"scheduling:{name}", kwargs={"school_slug": profile.school.slug, **kwargs})

    def step(label, complete, destination):
        steps.append({"label": label, "complete": complete, "url": url(destination)})

    def scoped(items, lookup):
        items = scope_locations(items, profile, lookup)
        return items.filter(**{lookup: location}) if location else items

    def list_url(name, **filters):
        if location:
            filters["location"] = location.pk
        query = urlencode(filters)
        return url(name) + ("?" + query if query else "")

    def event(start, end, label, place, room, kind, destination, reservation_count=None):
        agenda.append({"start": start, "end": end, "label": label, "location": place.name,
            "zone": place.timezone_name, "room": room.name if room else "No room assigned",
            "kind": kind, "url": destination, "reservation_count": reservation_count})

    if not location_restricted(profile):
        step("Add a location", staff_locations(profile).exists(), "locations")
    if has_capability(profile, "workshops"):
        offerings = Offering.objects.filter(location__in=staff_locations(profile), visit_program__isnull=True)
        step("Add a workshop", offerings.exists(), "workshops")
        step("Publish a future session", Session.objects.filter(offering__in=offerings,
            offering__is_published=True, is_published=True, starts_at__gt=now).exists(), "workshops")
        pending = WorkshopRequest.objects.filter(session__offering__location__profile=profile,
            status="pending", reservation__isnull=True)
        attention.append({"label": "Workshop requests awaiting review", "count": scoped(pending, "session__offering__location").count(), "url": list_url("requests", review=1)})
        sessions = Session.objects.filter(offering__in=scoped(offerings, "location"), offering__is_published=True,
            is_published=True, ends_at__gt=now, starts_at__lt=end).annotate(
                confirmed_reservation_count=Count("reservations", filter=Q(
                    reservations__profile=profile, reservations__state=Reservation.State.CONFIRMED))
            ).select_related("offering__location", "room").order_by("starts_at", "pk")[:21]
        for session in sessions:
            event(session.starts_at, session.ends_at, session.offering.name, session.offering.location,
                session.room, "Published session", url("preparation", pk=session.pk), session.confirmed_reservation_count)
    if has_capability(profile, "parties"):
        pending = PartyRequest.objects.filter(offering__location__profile=profile, status="pending", reservation__isnull=True)
        attention.append({"label": "New party inquiries", "count": scoped(pending, "offering__location").count(), "url": list_url("parties", review=1)})
    if has_capability(profile, "reservations"):
        reservations = staff_reservations(profile)
        if location:
            reservations = reservations.filter(reservation_location(location))
        metrics = reservations.aggregate(total=Count("pk"), review=Count("pk", filter=ATTENTION),
            preparation=Count("pk", filter=Q(pk__in=preparation_queue(reservations, now).values("pk"))))
        attention.append({"label": "Reservations needing payment review",
            "count": metrics["review"], "url": list_url("reservations", attention=1, hide_completed=0)})
        cards.extend([
            {"label": "Reservations", "count": metrics["total"], "sub": "All reservations", "color": "red", "accent": "primary", "icon": "calendar-check", "url": list_url("reservations", hide_completed=0), "action": "View reservations"},
            {"label": "Payment follow-up", "count": metrics["review"], "sub": "Need review", "color": "amber", "accent": "amber", "icon": "triangle-alert", "url": list_url("reservations", attention=1, hide_completed=0), "action": "Review now"},
            {"label": "Preparation Pending", "count": metrics["preparation"], "sub": "Upcoming confirmed bookings", "color": "blue", "icon": "clipboard-list", "url": list_url("reservations", preparation=1), "action": "View pending"},
        ])
        from .dashboard_activity import recent_activity
        activity = recent_activity(profile, reservations)
        if not location_restricted(profile):
            step("Enable reservations", profile.native_reservations_enabled, "reservations")
        parties = scoped(Reservation.objects.filter(profile=profile, state="confirmed", party_request__isnull=False,
            ends_at__gt=now, starts_at__lt=end), "party_request__offering__location").select_related("party_request__offering__location", "room").order_by("starts_at", "pk")[:21]
        for party in parties:
            event(party.starts_at, party.ends_at, party.snapshot["workshop"], party.party_request.offering.location,
                party.room, "Confirmed party", url("reservation_detail", public_id=party.public_id))
    if has_capability(profile, "lessons") and not location_restricted(profile):
        step("Enable lesson scheduling", profile.lessons_enabled, "lesson_settings")
        if profile.lessons_enabled:
            step("Add teacher availability", LessonAvailability.objects.filter(teacher__profile=profile).exists(), "lesson_setup")
            lessons = scoped(LessonOccurrence.objects.filter(series__profile=profile, state="scheduled", ends_at__gt=now,
                starts_at__lt=end), "series__location").select_related("series__location", "series__room", "series__student", "series__service", "series__teacher").order_by("starts_at", "pk")[:21]
            for lesson in lessons:
                series = lesson.series
                event(lesson.starts_at, lesson.ends_at, f"{series.student.name} - {series.service.name}",
                    series.location, series.room, f"Lesson with {series.teacher.name}", url("lesson_series", public_id=series.public_id))
    if has_capability(profile, "visits") and not location_restricted(profile):
        from .visits import slots, bookings
        upcoming = slots(profile).filter(session__ends_at__gt=now, session__is_published=True)
        step("Publish a tour time", upcoming.filter(program__offering__is_published=True).exists(), "visits")
        attention.append({"label": "Tour attendance to record", "count": scoped(bookings(profile).filter(
            state="booked", slot__session__ends_at__lte=now), "slot__program__offering__location").count(), "url": list_url("visits")})
        for slot in scoped(upcoming, "program__offering__location").filter(session__starts_at__lt=end)[:21]:
            session = slot.session
            event(session.starts_at, session.ends_at, slot.program.offering.name, slot.program.offering.location,
                session.room, slot.program.get_kind_display(), list_url("visits"))
    agenda.sort(key=lambda item: (item["start"], item["label"], item["url"]))
    for item in attention:
        if "payment review" not in item["label"]:
            if profile.native_reservations_enabled and "Workshop requests" in item["label"] and not item["count"]:
                continue
            cards.append({**item, "sub": "Need follow-up", "color": "green", "icon": "users", "action": "View all"})
    hour = timezone.localtime(now).hour
    return {"attention_items": attention, "setup_steps": steps, "agenda": agenda[:20],
        "dashboard_cards": cards, "recent_activity": activity,
        "greeting": "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening",
        "agenda_truncated": len(agenda) > 20, "setup_remaining": sum(not step["complete"] for step in steps)}
