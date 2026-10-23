"""Atomic cancellation and refund authorization; provider calls happen after commit."""
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from core.models import AdminAuditLog
from core.payments.models import Refund
from core.payments.service import totals
from .checkout import approve_refund
from .access import get_staff_reservation
from .reservations import _audit, cancel_reservation, locked_profile


@transaction.atomic
def cancel(user, slug, public_id, *, key, resolution, reason):
    profile = locked_profile(user, slug)
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    if resolution not in {"without_refund", "with_refund"} or not reason.strip() or len(reason.strip()) > 240:
        raise ValidationError("Choose how to handle payment and enter a cancellation reason.")
    previous = AdminAuditLog.objects.filter(model_label="scheduling.reservation", object_id=str(item.pk),
        changes__action="cancellation_decision", extra__cancellation_key=str(key)).first()
    if previous:
        if (previous.actor_id, previous.extra["resolution"], previous.extra["reason"]) != (user.pk, resolution, reason.strip()):
            raise ValidationError("This cancellation was already submitted with different details.")
        return item, list(Refund.objects.filter(attempt__order=item.order, public_id__in=previous.extra["refunds"]))
    if item.state == "canceled":
        raise ValidationError("This reservation is already canceled. Review its payments before requesting a refund.")
    planned = []
    simulated = False
    if resolution == "with_refund":
        from .experience_demo.service import has_simulated_payment
        simulated = has_simulated_payment(profile, item)
        if Refund.objects.filter(attempt__order=item.order, state__in=Refund.ACTIVE).exists():
            raise ValidationError("A refund is already in progress. Check its status before canceling with a refund.")
        for attempt in item.order.attempts.filter(state="paid").select_related("order"):
            allocated = attempt.refunds.exclude(state__in=["failed", "canceled"]).aggregate(total=Sum("amount_cents"))["total"] or 0
            if attempt.amount_cents > allocated:
                planned.append((attempt, attempt.amount_cents - allocated))
        net = totals(item.order)["net_cents"]
        if net <= 0:
            raise ValidationError("There is no remaining payment to refund. Choose cancel without refund.")
        if not simulated and sum(amount for _, amount in planned) != net:
            raise ValidationError("This payment was recorded outside Pontora. Refund it through the original payment provider; Pontora cannot return it automatically.")
    item = cancel_reservation(user, slug, public_id)
    if simulated:
        from .experience_demo.service import refund_simulated_payment
        refund_simulated_payment(user, profile, item, key)
    approved = [approve_refund(user, slug, public_id, attempt.public_id,
        key=uuid.uuid5(key, str(attempt.public_id)), amount_cents=amount, reason=reason.strip())
        for attempt, amount in planned]
    _audit(user, item, "cancellation_decision", cancellation_key=str(key), resolution=resolution,
        reason=reason.strip(), refunds=[str(refund.public_id) for refund in approved])
    return item, approved
