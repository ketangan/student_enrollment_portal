"""Staff-facing inquiry states, without exposing payment recovery internals."""
from django.db.models import Q

FILTERS = {
    "pending": ("Needs review", Q(status="pending", reservation__isnull=True) | Q(reservation__state="review")),
    "reviewed": ("Reviewed", Q(status="reviewed", reservation__isnull=True)),
    "held": ("Awaiting payment", Q(reservation__state="held")),
    "confirmed": ("Confirmed", Q(reservation__state="confirmed")),
    "completed": ("Completed", Q(reservation__state="completed")),
    "canceled": ("Canceled", Q(reservation__state="canceled") | Q(status__in=["declined", "withdrawn"], reservation__isnull=True)),
}


def label(item):
    reservation = getattr(item, "reservation", None)
    state = reservation.state if reservation else item.status
    state = {"review": "pending", "declined": "canceled", "withdrawn": "canceled"}.get(state, state)
    return FILTERS[state][0]
