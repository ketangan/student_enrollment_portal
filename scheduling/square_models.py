"""Seller authorization, separate from immutable payment-account bindings."""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class SquareConnection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.PROTECT)
    environment = models.CharField(max_length=8, choices=[("test", "Sandbox"), ("live", "Production")])
    label = models.CharField(max_length=80, blank=True)
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
            models.UniqueConstraint(fields=["profile", "environment"], condition=models.Q(merchant_id=""),
                                    name="square_pending_account_unique"),
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
    def display_name(self):
        return self.label or self.merchant_id or "Square account"

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
    environment = models.CharField(max_length=8, choices=[("test", "Sandbox"), ("live", "Production")], default="test")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["location", "environment"], name="square_location_environment_unique"),
            models.CheckConstraint(condition=models.Q(environment__in=["test", "live"]), name="square_mapping_environment_valid"),
        ]


class SquareTerminal(models.Model):
    """A paired device stays bound to its original seller/location account."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey("scheduling.Location", on_delete=models.PROTECT)
    account = models.ForeignKey("payments.PaymentAccount", on_delete=models.PROTECT)
    name = models.CharField(max_length=60)
    device_id = models.CharField(max_length=100, blank=True)
    code_id = models.CharField(max_length=100, blank=True)
    simulation = models.BooleanField(default=False)
    ping_key = models.UUIDField(null=True, editable=False)
    ping_id = models.CharField(max_length=255, blank=True, editable=False)
    ping_started_at = models.DateTimeField(null=True, editable=False)
    ping_checked_at = models.DateTimeField(null=True, editable=False)
    ping_status = models.CharField(max_length=20, blank=True, editable=False)
    status = models.CharField(max_length=10, default="pairing", choices=[
        ("pairing", "Pairing"), ("paired", "Paired"), ("expired", "Code expired"), ("disabled", "Disabled")])
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def simulation_label(self):
        from core.payments.square_terminal import SIMULATIONS
        return next((label for label, identity in SIMULATIONS.values() if identity == self.device_id), "Virtual device")

    @property
    def collection_maximum(self):
        from core.payments.square_terminal import maximum_amount_cents
        maximum = maximum_amount_cents("test" if self.simulation else "live", self.device_id)
        return maximum / 100 if maximum is not None else 999999.99

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "device_id"],
                condition=~models.Q(device_id=""), name="square_terminal_device_unique"),
        ]
