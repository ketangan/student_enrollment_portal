import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


class PaymentAccount(models.Model):
    """Operator-provisioned merchant binding; never accepts a customer's API key."""

    school = models.ForeignKey("core.School", on_delete=models.PROTECT)
    reference = models.CharField(max_length=100, unique=True)
    environment = models.CharField(max_length=8, default="test")
    enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(environment="test"), name="payment_account_test_only"),
            models.UniqueConstraint(fields=["school", "environment"], name="payment_account_school_unique"),
        ]


class PaymentOrder(models.Model):
    """Immutable commercial snapshot. Domain state and financial state are separate."""

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    school = models.ForeignKey("core.School", on_delete=models.PROTECT)
    purpose = models.CharField(max_length=40)
    source_key = models.CharField(max_length=100)
    environment = models.CharField(max_length=8, default="test")
    merchant_reference = models.CharField(max_length=100)
    account = models.ForeignKey(PaymentAccount, null=True, blank=True, on_delete=models.PROTECT)
    currency = models.CharField(max_length=3, default="usd")
    total_cents = models.PositiveIntegerField()
    deposit_cents = models.PositiveIntegerField()
    description = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["school", "environment", "purpose", "source_key"], name="payment_source_unique"),
            models.CheckConstraint(condition=Q(environment__in=["test", "live"]), name="payment_environment_valid"),
            models.CheckConstraint(condition=Q(currency="usd"), name="payment_currency_valid"),
            models.CheckConstraint(condition=Q(total_cents__gte=0, total_cents__lte=99999999, deposit_cents__gte=0, deposit_cents__lte=models.F("total_cents")), name="payment_quote_valid"),
        ]

    @property
    def total(self):
        return Decimal(self.total_cents) / 100

    @property
    def deposit(self):
        return Decimal(self.deposit_cents) / 100


class LedgerEntry(models.Model):
    """Append-only financial evidence, with its source recorded explicitly."""

    order = models.ForeignKey(PaymentOrder, on_delete=models.PROTECT, related_name="entries")
    school = models.ForeignKey("core.School", on_delete=models.PROTECT)
    environment = models.CharField(max_length=8)
    key = models.UUIDField(unique=True)
    kind = models.CharField(max_length=8, choices=[("payment", "Offline payment"), ("refund", "Offline refund")])
    amount_cents = models.PositiveIntegerField()
    reference = models.CharField(max_length=100)
    channel = models.CharField(max_length=8, default="offline", choices=[("offline", "Offline"), ("stripe", "Stripe")])
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["school", "environment", "reference"], name="payment_receipt_unique"),
            models.CheckConstraint(condition=Q(kind__in=["payment", "refund"]), name="payment_entry_kind_valid"),
            models.CheckConstraint(condition=Q(environment__in=["test", "live"]), name="payment_entry_environment_valid"),
            models.CheckConstraint(condition=Q(amount_cents__gt=0, amount_cents__lte=99999999), name="payment_entry_amount_valid"),
            models.CheckConstraint(condition=Q(channel="offline", actor__isnull=False) | Q(channel="stripe", actor__isnull=True), name="payment_entry_source_valid"),
        ]

    @property
    def amount(self):
        return Decimal(self.amount_cents) / 100


class RecoveryRecord(models.Model):
    """Durable, bounded recovery; a crashed worker's lease expires without losing the operation."""

    checked_at = models.DateTimeField(null=True)
    next_check_at = models.DateTimeField(null=True, db_index=True)
    lease_until = models.DateTimeField(null=True)
    lease_token = models.UUIDField(null=True)
    recovery_failures = models.PositiveIntegerField(default=0)
    attention = models.CharField(max_length=240, blank=True)

    class Meta:
        abstract = True


class PaymentAttempt(RecoveryRecord):
    """One immutable checkout request, retried with the same provider idempotency key."""

    ACTIVE = ("pending", "open", "unknown")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    order = models.ForeignKey(PaymentOrder, related_name="attempts", on_delete=models.PROTECT)
    account = models.ForeignKey(PaymentAccount, on_delete=models.PROTECT)
    amount_cents = models.PositiveIntegerField()
    state = models.CharField(max_length=10, default="pending", choices=[
        ("pending", "Starting checkout"), ("open", "Awaiting payment"),
        ("unknown", "Checkout needs verification"), ("paid", "Payment verified"), ("expired", "Checkout expired"),
    ])
    provider_session = models.CharField(max_length=100, null=True, unique=True)
    provider_intent = models.CharField(max_length=100, null=True, unique=True)
    checkout_url = models.URLField(max_length=2000, blank=True)
    success_url = models.URLField(max_length=2000)
    cancel_url = models.URLField(max_length=2000)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    entry = models.OneToOneField(LedgerEntry, null=True, on_delete=models.PROTECT)
    close_requested = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["order"], condition=Q(state__in=["pending", "open", "unknown"]), name="payment_one_active_attempt"),
            models.CheckConstraint(condition=Q(state__in=["pending", "open", "unknown", "paid", "expired"]), name="payment_attempt_state_valid"),
            models.CheckConstraint(condition=Q(amount_cents__gt=0, amount_cents__lte=99999999), name="payment_attempt_amount_valid"),
        ]

    @property
    def amount(self):
        return Decimal(self.amount_cents) / 100


class Refund(RecoveryRecord):
    """Approved refund intent, not proof of returned money. Only succeeded writes the ledger."""

    ACTIVE = ("queued", "unknown", "pending", "requires_action")
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    key = models.UUIDField(unique=True)
    attempt = models.ForeignKey(PaymentAttempt, related_name="refunds", on_delete=models.PROTECT)
    amount_cents = models.PositiveIntegerField()
    reason = models.CharField(max_length=240)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    state = models.CharField(max_length=20, default="queued", choices=[
        ("queued", "Approved; not submitted"), ("unknown", "Refund needs verification"),
        ("pending", "Refund processing"), ("requires_action", "Provider action required"),
        ("succeeded", "Refund verified"), ("failed", "Refund failed"), ("canceled", "Refund canceled"),
    ])
    provider_refund = models.CharField(max_length=100, null=True, unique=True)
    entry = models.OneToOneField(LedgerEntry, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(amount_cents__gt=0, amount_cents__lte=99999999), name="payment_refund_amount_valid"),
            models.CheckConstraint(condition=Q(state__in=["queued", "unknown", "pending", "requires_action", "succeeded", "failed", "canceled"]), name="payment_refund_state_valid"),
        ]

    @property
    def amount(self):
        return Decimal(self.amount_cents) / 100


class ProviderEventReceipt(models.Model):
    """Minimal verified-event inbox. No raw payment or customer payloads retained."""

    account = models.ForeignKey(PaymentAccount, on_delete=models.PROTECT)
    environment = models.CharField(max_length=8, default="test")
    event_id = models.CharField(max_length=100)
    event_type = models.CharField(max_length=100)
    attempt = models.ForeignKey(PaymentAttempt, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "environment", "event_id"], name="payment_provider_event_unique"),
            models.CheckConstraint(condition=Q(environment="test"), name="payment_event_test_only"),
        ]
