"""Lesson entitlements, deliberately separate from the financial payment ledger."""
import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class LessonCreditPolicy(models.Model):
    profile = models.OneToOneField("scheduling.SchedulingProfile", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=False)
    family_cancellations = models.BooleanField(default=True)
    studio_cancellations = models.BooleanField(default=True)
    notice_hours = models.PositiveSmallIntegerField(default=24, validators=[MaxValueValidator(720)])
    expiry_days = models.PositiveSmallIntegerField(default=90, validators=[MinValueValidator(1), MaxValueValidator(365)])
    version = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(notice_hours__lte=720, expiry_days__gte=1,
            expiry_days__lte=365), name="lesson_credit_policy_bounds")]


class LessonCancellation(models.Model):
    occurrence = models.OneToOneField("scheduling.LessonOccurrence", on_delete=models.PROTECT, related_name="cancellation")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    origin = models.CharField(max_length=10, choices=[("family", "Family"), ("studio", "Studio")])
    reason = models.CharField(max_length=200)
    outcome = models.CharField(max_length=200)
    policy_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)


class LessonCredit(models.Model):
    source = models.OneToOneField("scheduling.LessonOccurrence", on_delete=models.PROTECT, related_name="issued_credit")
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.PROTECT)
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    snapshot = models.JSONField(default=dict)
    expires_at = models.DateTimeField()
    state = models.CharField(max_length=10, default="available", choices=[("available", "Available"),
        ("redeemed", "Booked"), ("revoked", "Revoked"), ("forfeited", "Forfeited")])
    used_occurrence = models.OneToOneField("scheduling.LessonOccurrence", null=True, blank=True,
        on_delete=models.PROTECT, related_name="redeemed_credit")
    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["expires_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(state__in=["available", "redeemed", "revoked", "forfeited"]), name="lesson_credit_state_valid"),
            models.CheckConstraint(condition=models.Q(state="redeemed", used_occurrence__isnull=False) |
                (~models.Q(state="redeemed") & models.Q(used_occurrence__isnull=True)), name="lesson_credit_use_valid"),
        ]

    @property
    def available(self):
        return self.state == "available" and self.expires_at > timezone.now()

    @property
    def status_label(self):
        if self.state == "available" and not self.available:
            return "Expired"
        if self.state == "redeemed" and self.used_occurrence.ends_at <= timezone.now():
            return "Used"
        return self.get_state_display()


class LessonCreditEvent(models.Model):
    credit = models.ForeignKey(LessonCredit, on_delete=models.PROTECT, related_name="events")
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=10, choices=[(v, v.title()) for v in ["issued", "redeemed", "moved", "restored", "revoked", "forfeited"]])
    occurrence = models.ForeignKey("scheduling.LessonOccurrence", null=True, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    reason = models.CharField(max_length=200)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [models.CheckConstraint(condition=models.Q(kind__in=["issued", "redeemed", "moved", "restored", "revoked", "forfeited"]), name="lesson_credit_event_valid")]
