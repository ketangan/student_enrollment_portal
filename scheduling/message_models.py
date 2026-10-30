"""Bookings outbox. Transport and enrollment notifications remain shared services."""
import uuid

from django.db import models
from django.conf import settings
from django.utils import timezone

MESSAGE_STATES = [
    ("draft", "Not queued"), ("queued", "Queued"), ("sending", "Processing"),
    ("retry", "Retry scheduled"), ("failed", "Retries exhausted"),
    ("uncertain", "Needs review"), ("simulated", "Simulated, not sent"),
    ("suppressed", "Suppressed"), ("superseded", "Replaced by newer update"),
    ("sent", "Accepted by email provider"),
]
RESULT_LABELS = {
    "newer_update": "A newer update is available",
    "not_accepted": "Not accepted; safe to retry",
    "local_backend_accepted": "Accepted by the local simulator",
    "provider_accepted": "Accepted by the email provider",
    "acceptance_unknown": "Acceptance could not be confirmed",
    "worker_interrupted": "Processing was interrupted; outcome unknown",
    "recipient_access_ended": "Recipient access ended; not delivered",
}


class BookingMessage(models.Model):
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    is_html = models.BooleanField(default=False)
    reservation = models.ForeignKey("scheduling.Reservation", null=True, blank=True, on_delete=models.PROTECT)
    notice_kind = models.CharField(max_length=16, blank=True)
    notice_signature = models.CharField(max_length=64, blank=True)
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    profile = models.ForeignKey("scheduling.SchedulingProfile", on_delete=models.PROTECT)
    event_key = models.CharField(max_length=180)
    group_key = models.CharField(max_length=80)
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    payload_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=16, choices=MESSAGE_STATES, default="draft")
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    lease_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    result_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-pk"]
        constraints = [
            models.UniqueConstraint(fields=["profile", "event_key"], name="booking_message_event_unique"),
            models.CheckConstraint(condition=models.Q(state__in=[s[0] for s in MESSAGE_STATES]), name="booking_message_state_valid"),
            models.CheckConstraint(condition=models.Q(attempts__lte=5), name="booking_message_attempt_limit"),
            models.CheckConstraint(condition=(models.Q(state="sending", lease_token__isnull=False, lease_expires_at__isnull=False)
                | (~models.Q(state="sending") & models.Q(lease_token__isnull=True, lease_expires_at__isnull=True))),
                name="booking_message_lease_valid"),
        ]
        indexes = [models.Index(fields=["profile", "state", "next_attempt_at"], name="booking_message_due_idx")]

    @property
    def can_queue(self):
        return self.state in {"draft", "failed"} and self.attempts < 5

    @property
    def can_suppress(self):
        return self.state in {"draft", "queued", "retry", "failed", "uncertain"}


class BookingMessageAttempt(models.Model):
    message = models.ForeignKey(BookingMessage, on_delete=models.PROTECT, related_name="delivery_attempts")
    token = models.UUIDField(unique=True)
    number = models.PositiveSmallIntegerField()
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True)
    outcome = models.CharField(max_length=16, default="sending")
    result_code = models.CharField(max_length=40, blank=True)

    @property
    def outcome_label(self):
        if self.outcome == "retry":
            return "Not accepted"
        return dict(MESSAGE_STATES).get(self.outcome, self.outcome)

    @property
    def result_label(self):
        return RESULT_LABELS.get(self.result_code, "-")

    class Meta:
        ordering = ["number"]
        constraints = [models.UniqueConstraint(fields=["message", "number"], name="booking_message_attempt_unique")]
