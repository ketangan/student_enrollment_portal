"""Fulfil a reservation and consume its allocated stock in one transaction."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from . import inventory, preparation
from .access import require_access
from .models import Reservation, SchedulingProfile
from .reservations import _audit


@transaction.atomic
def complete(user, slug, public_id):
    profile, _ = require_access(user, slug, "sessions.edit", capability="reservations")
    # All booking/stock mutations share this lock, including cancellation.
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_access(user, slug, "sessions.edit", capability="reservations")
    item = get_object_or_404(Reservation, profile=profile, public_id=public_id, visit_booking__isnull=True)
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
