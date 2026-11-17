"""Read-only, scoped choices for the shared staff reservation editor."""
from zoneinfo import ZoneInfo

from django.db.models import Sum
from django.utils import timezone

from .reservations import active_claims


def choices(form, offering, *, reservation=None):
    if "session" not in form.fields or form.fields["session"].disabled:
        return []
    sessions = list(form.fields["session"].queryset)
    claims = active_claims().filter(session_id__in=[s.pk for s in sessions])
    occupied = dict(claims.values("session_id").annotate(total=Sum("quantity")).values_list("session_id", "total"))
    own = dict(claims.filter(pk=reservation.pk).values_list("session_id", "quantity")) if reservation else {}
    zone = ZoneInfo(offering.location.timezone_name)
    return [{"id": str(s.pk), "date": timezone.localtime(s.starts_at, zone).date().isoformat(),
        "label": timezone.localtime(s.starts_at, zone).strftime("%a, %b %d · %I:%M %p"),
        "name": offering.name, "open": max(0, s.capacity - occupied.get(s.pk, 0)),
        "limit": max(0, s.capacity - occupied.get(s.pk, 0) + own.get(s.pk, 0)),
        "duration": round((s.ends_at - s.starts_at).total_seconds() / 60)} for s in sessions]
