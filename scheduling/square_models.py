"""Seller authorization, separate from immutable payment-account bindings."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class SquareConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.PROTECT)
    environment = models.CharField(max_length=8, choices=[("test", "Sandbox"), ("live", "Production")])
    application_id = models.CharField(max_length=100, blank=True, editable=False)
    merchant_id = models.CharField(max_length=100, blank=True, editable=False)
    credential_ciphertext = models.TextField(blank=True, editable=False)
    status = models.CharField(max_length=16, default="disconnected", choices=[
        ("disconnected", "Not connected"), ("connected", "Connected"),
        ("attention", "Needs attention"), ("disconnecting", "Disconnect pending")])
    expires_at = models.DateTimeField(null=True, editable=False)
    checked_at = models.DateTimeField(null=True, editable=False)
    next_check_at = models.DateTimeField(null=True, editable=False, db_index=True)
    revision = models.PositiveIntegerField(default=0, editable=False)
    operation = models.UUIDField(null=True, editable=False)
    operation_until = models.DateTimeField(null=True, editable=False)
    locations = models.JSONField(default=list, editable=False)
    locations_checked_at = models.DateTimeField(null=True, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["profile", "environment"], name="square_profile_environment_unique"),
            models.UniqueConstraint(fields=["environment", "merchant_id"],
                condition=~models.Q(merchant_id=""), name="square_seller_environment_unique"),
            models.CheckConstraint(condition=models.Q(environment__in=["test", "live"]), name="square_connection_environment_valid"),
            models.CheckConstraint(condition=models.Q(status__in=["disconnected", "connected", "attention", "disconnecting"]),
                                   name="square_connection_status_valid"),
        ]

    @property
    def provider(self):
        return "square"

    @property
    def display_status(self):
        if self.status == "connected" and (not self.expires_at or self.expires_at <= timezone.now()):
            return "Expired - reconnect"
        if self.status == "connected" and self.next_check_at and self.next_check_at <= timezone.now():
            return "Verification due"
        return self.get_status_display()


class SquareAuthorization(models.Model):
    state_hash = models.CharField(max_length=64, primary_key=True)
    connection = models.ForeignKey(SquareConnection, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    session_hash = models.CharField(max_length=64)
    application_id = models.CharField(max_length=100)
    revision = models.PositiveIntegerField()
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True)


class SquareLocationMapping(models.Model):
    connection = models.ForeignKey(SquareConnection, on_delete=models.PROTECT, related_name="mappings")
    location = models.ForeignKey("scheduling.Location", on_delete=models.PROTECT)
    account = models.ForeignKey("payments.PaymentAccount", on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["connection", "location"], name="square_location_mapping_unique")]
