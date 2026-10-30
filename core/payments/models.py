import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


class PaymentAccount(models.Model):
    """Operator-provisioned merchant binding; never accepts a customer's API key."""

    class Scope(models.TextChoices):
        CONNECTED = "connected", "Connected merchant"
        PLATFORM_DEMO = "platform_demo", "Pontora shared sandbox (demos only)"

    class Provider(models.TextChoices):
        STRIPE = "stripe", "Stripe"
        SQUARE = "square", "Square"

    school = models.ForeignKey("core.School", on_delete=models.PROTECT)
    provider = models.CharField(max_length=8, choices=Provider.choices, default=Provider.STRIPE)
    location_reference = models.CharField(max_length=100, blank=True)
    reference = models.CharField(max_length=100)
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.CONNECTED)
    environment = models.CharField(max_length=8, default="test")
    enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(environment="test") | Q(environment="live", provider="square", scope="connected"), name="payment_account_environment_valid"),
            models.CheckConstraint(condition=Q(scope__in=["connected", "platform_demo"]), name="payment_account_scope_valid"),
            models.UniqueConstraint(fields=["reference"], condition=Q(scope="connected", provider="stripe"), name="payment_connected_reference_unique"),
            models.UniqueConstraint(fields=["school", "environment", "provider"], condition=Q(provider="stripe") | Q(scope="platform_demo"), name="payment_account_school_provider_unique"),
            models.UniqueConstraint(fields=["school", "environment", "reference", "location_reference"], condition=Q(provider="square", scope="connected"), name="payment_square_location_unique"),
            models.CheckConstraint(condition=Q(provider="stripe", location_reference="") | (Q(provider="square") & ~Q(location_reference="")), name="payment_provider_binding_valid"),
        ]


class PaymentOrder(models.Model):
    """Commercial quote; unpaid booking amendments are audited before any new checkout.

    Settled orders and individual payment attempts are immutable.
    """

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
        from .service import totals
        return totals(self)["total"]

    @property
    def deposit(self):
        return Decimal(self.deposit_cents) / 100


class LedgerEntry(models.Model):
    """Append-only financial evidence, with its source recorded explicitly."""

    order = models.ForeignKey(PaymentOrder, on_delete=models.PROTECT, related_name="entries")
    school = models.ForeignKey("core.School", on_delete=models.PROTECT)
    environment = models.CharField(max_length=8)
    key = models.UUIDField(unique=True)
    kind = models.CharField(max_length=8, choices=[("payment", "Payment"), ("refund", "Refund"), ("void", "Entry correction")])
    amount_cents = models.PositiveIntegerField()
    reference = models.CharField(max_length=100)
    channel = models.CharField(max_length=8, default="offline", choices=[("offline", "Offline"), ("stripe", "Stripe"), ("square", "Square"), ("terminal", "Square Terminal (staff-recorded)"), ("cash", "Cash")])
    external_reference = models.CharField(max_length=100, blank=True)
    extra_charge_cents = models.PositiveIntegerField(default=0)
    paid_on = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=240, blank=True)
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["school", "environment", "reference"], name="payment_receipt_unique"),
            models.CheckConstraint(condition=Q(kind__in=["payment", "refund", "void"]), name="payment_entry_kind_valid"),
            models.CheckConstraint(condition=Q(environment__in=["test", "live"]), name="payment_entry_environment_valid"),
            models.CheckConstraint(condition=Q(amount_cents__gt=0, amount_cents__lte=99999999), name="payment_entry_amount_valid"),
            models.CheckConstraint(condition=Q(channel__in=["offline", "terminal", "cash"], actor__isnull=False) | Q(channel__in=["stripe", "square"], actor__isnull=True), name="payment_entry_source_valid"),
            models.CheckConstraint(condition=(Q(kind="void", channel__in=["terminal", "cash"], reverses__isnull=False) | (~Q(kind="void") & Q(reverses__isnull=True))), name="payment_correction_source_valid"),
            models.CheckConstraint(condition=(~Q(channel__in=["terminal", "cash"]) | Q(kind__in=["payment", "void"], paid_on__isnull=False)), name="payment_terminal_details_valid"),
            models.CheckConstraint(condition=Q(extra_charge_cents=0) | Q(channel__in=["terminal", "cash"], kind__in=["payment", "void"], extra_charge_cents__lte=models.F("amount_cents")), name="payment_extra_charge_valid"),
        ]

    @property
    def amount(self):
        return Decimal(self.amount_cents) / 100

    @property
    def extra_charge(self):
        return Decimal(self.extra_charge_cents) / 100


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
    submitted_at = models.DateTimeField(null=True, blank=True)

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
            models.CheckConstraint(condition=Q(environment__in=["test", "live"]), name="payment_event_environment_valid"),
        ]
