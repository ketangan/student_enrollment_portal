"""Visits share sessions and reservations, without requiring the workshop product."""

import uuid

from django.db import models


class VisitProgram(models.Model):
    offering = models.OneToOneField("scheduling.Offering", on_delete=models.PROTECT, related_name="visit_program")
    capture_leads = models.BooleanField(default=False)
    kind = models.CharField(max_length=12, choices=[("tour", "Tour"), ("playdate", "Playdate")], default="tour")


class VisitSlot(models.Model):
    program = models.ForeignKey(VisitProgram, on_delete=models.PROTECT, related_name="slots")
    session = models.OneToOneField("scheduling.Session", on_delete=models.PROTECT, related_name="visit_slot")
    host = models.ForeignKey("scheduling.SchedulingMembership", on_delete=models.PROTECT, related_name="visit_slots")

    class Meta:
        ordering = ["session__starts_at", "pk"]


class VisitBooking(models.Model):
    class State(models.TextChoices):
        BOOKED = "booked", "Scheduled"
        ATTENDED = "attended", "Attended"
        NO_SHOW = "no_show", "Did not attend"
        CANCELED = "canceled", "Canceled"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    slot = models.ForeignKey(VisitSlot, on_delete=models.PROTECT, related_name="bookings")
    contact_name = models.CharField(max_length=100)
    contact_email = models.EmailField()
    child_name = models.CharField(max_length=100)
    adults = models.PositiveSmallIntegerField()
    children = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=16, choices=State.choices, default=State.BOOKED)
    lead = models.ForeignKey("core.Lead", null=True, blank=True, on_delete=models.PROTECT)
    admission_case = models.ForeignKey("scheduling.AdmissionCase", null=True, blank=True,
        on_delete=models.PROTECT, related_name="playdates")
    prior_submission_status = models.CharField(max_length=40, blank=True)
    submission_key = models.UUIDField(unique=True, editable=False)
    submission_digest = models.CharField(max_length=64, editable=False)
    browser_digest = models.CharField(max_length=64, editable=False)
    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(adults__gte=1, adults__lte=10, children__gte=0, children__lte=10), name="visit_party_size_valid"),
            models.CheckConstraint(condition=models.Q(state__in=["booked", "attended", "no_show", "canceled"]), name="visit_booking_state_valid"),
        ]
