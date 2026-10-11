import uuid

from django.db import models
from django.db.models.functions import Lower


class IntegrationConnection(models.Model):
    """Saved preparation only; intentionally has no enabled/connected flag."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.PROTECT, related_name="connections")
    provider = models.CharField(max_length=32, editable=False)
    environment = models.CharField(max_length=16, choices=[("test", "Testing"), ("live", "Production")])
    label = models.CharField(max_length=100)
    configuration = models.JSONField(default=dict, blank=True)
    field_mapping = models.JSONField(default=dict, blank=True)
    source = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="destinations")
    credential_ciphertext = models.TextField(blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["label", "pk"]
        constraints = [
            models.UniqueConstraint(Lower("label"), "profile", "environment", name="connection_label_unique"),
            models.CheckConstraint(condition=models.Q(environment__in=["test", "live"]), name="connection_environment_valid"),
        ]

    def __str__(self):
        return self.label

    @property
    def credential_saved(self):
        return bool(self.credential_ciphertext)

    @property
    def provider_spec(self):
        from .integrations.registry import provider
        return provider(self.provider)
