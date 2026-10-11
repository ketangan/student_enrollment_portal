"""Opt-in visit operations; no existing school is activated by the schema."""
import uuid

from django.db import models
from django.core.validators import MaxValueValidator


class VisitPilotSettings(models.Model):
    profile = models.OneToOneField("scheduling.SchedulingProfile", on_delete=models.PROTECT)
    family_changes = models.BooleanField(default=False)
    notice_hours = models.PositiveSmallIntegerField(default=24, validators=[MaxValueValidator(168)])
    contact_email = models.EmailField()


class VisitAccessGrant(models.Model):
    booking = models.ForeignKey("scheduling.VisitBooking", on_delete=models.PROTECT, related_name="access_grants")
    recipient = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    token_expires_at = models.DateTimeField()
    session_hash = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField()
    redeemed_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)
    delivery = models.OneToOneField("scheduling.BookingMessage", null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class VisitScheduleBatch(models.Model):
    program = models.ForeignKey("scheduling.VisitProgram", on_delete=models.PROTECT)
    key = models.UUIDField(default=uuid.uuid4, unique=True)
    digest = models.CharField(max_length=64)
    slot_ids = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
