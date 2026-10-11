"""Explicit, expiring read-only access, separate from staff and enrollment access."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class FamilyScheduleGrant(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    household = models.ForeignKey("scheduling.LessonHousehold", on_delete=models.PROTECT)
    students = models.ManyToManyField("scheduling.LessonStudent")
    recipient_name = models.CharField(max_length=100)
    recipient_email = models.EmailField()
    allow_booking = models.BooleanField(default=False)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    token_expires_at = models.DateTimeField()
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    session_hash = models.CharField(max_length=64, unique=True, null=True, editable=False)
    redeemed_at = models.DateTimeField(null=True, editable=False)
    revoked_at = models.DateTimeField(null=True, editable=False)
    submission_key = models.UUIDField(unique=True, editable=False)
    submission_digest = models.CharField(max_length=64, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(expires_at__gt=models.F("created_at"),
                token_expires_at__gt=models.F("created_at"), token_expires_at__lte=models.F("expires_at")),
                name="family_grant_dates_valid"),
            models.CheckConstraint(condition=(models.Q(redeemed_at__isnull=True, session_hash__isnull=True)
                | models.Q(redeemed_at__isnull=False, session_hash__isnull=False)), name="family_grant_session_valid"),
        ]

    @property
    def status_label(self):
        if self.revoked_at:
            return "Revoked"
        if self.expires_at <= timezone.now():
            return "Expired"
        if self.redeemed_at:
            return "Active"
        return "Link expired" if self.token_expires_at <= timezone.now() else "Not opened"
