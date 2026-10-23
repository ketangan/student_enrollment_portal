"""Fulfil a reservation and consume its allocated stock in one transaction."""
from django.core.exceptions import ValidationError
from django.db import transaction

from . import inventory, preparation
from .access import get_staff_reservation, require_location_access
from .models import Reservation, SchedulingProfile
from .reservations import _audit


@transaction.atomic
def complete(user, slug, public_id):
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    # All booking/stock mutations share this lock, including cancellation.
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(user, slug, "sessions.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    if item.state == Reservation.State.COMPLETED:
        return item
    if item.state != Reservation.State.CONFIRMED:
        raise ValidationError("Only confirmed reservations can be completed.")
    preparation.validate_materials(item, list(item.stock_allocations.select_related("level")))
    inventory.consume(user, slug, public_id)
    item.state = Reservation.State.COMPLETED
    item.save(update_fields=["state"])
    _audit(user, item, "completed")
    return item
