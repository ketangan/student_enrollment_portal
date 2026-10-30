"""Record external receipts; never invokes a payment provider or sends a receipt."""
import uuid
from decimal import Decimal
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from core.models import School
from core.payments.models import LedgerEntry, PaymentAttempt, PaymentOrder, Refund
from core.payments.service import totals
from .access import get_staff_reservation
from .reservations import locked_profile, _audit


class TerminalPaymentForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    expected_balance = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    expected_total = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    amount = forms.DecimalField(label="Amount received (USD)", min_value=Decimal("0.01"),
        max_value=Decimal("999999.99"), max_digits=8, decimal_places=2)
    paid_on = forms.DateField(label="Payment date", widget=forms.DateInput(attrs={"type": "date"}))
    external_reference = forms.CharField(label="Square receipt number (optional)", required=False, max_length=100)
    method = forms.ChoiceField(label="Paid with", choices=[("terminal", "Square Terminal"), ("cash", "Cash")],
        widget=forms.RadioSelect, required=False, initial="terminal")
    description = forms.CharField(label="What was it for?", required=False, max_length=240)
    confirm = forms.BooleanField(label="This payment was already collected")

    def clean(self):
        data = super().clean()
        data["method"] = data.get("method") or "terminal"
        if data["method"] == "cash":
            data["external_reference"] = ""
        return data


class VoidTerminalPaymentForm(forms.Form):
    reason = forms.CharField(label="Reason for correction", max_length=240,
        widget=forms.Textarea(attrs={"rows": 2}))
    confirm = forms.BooleanField(label="This is an incorrect record, not a refund to the customer")


class CorrectTerminalPaymentForm(forms.Form):
    key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)
    expected_balance = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    expected_total = forms.IntegerField(widget=forms.HiddenInput, min_value=0)
    amount = forms.DecimalField(label="Amount", min_value=Decimal("0.01"),
        max_value=Decimal("999999.99"), max_digits=8, decimal_places=2)
    external_reference = forms.CharField(label="Receipt number", required=False, max_length=100)


def studio_today(item):
    return timezone.localdate(timezone=ZoneInfo(item.snapshot["timezone"]))


def locked_reservation(user, slug, public_id):
    profile = locked_profile(user, slug)
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    School.objects.select_for_update().get(pk=profile.school_id)
    PaymentOrder.objects.select_for_update().get(pk=item.order_id)
    return item


def require_settled_operations(item):
    if item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists():
        raise ValidationError("An online checkout is still open or awaiting verification. Wait for it to finish or expire before recording a payment.")
    if Refund.objects.filter(attempt__order=item.order, state__in=Refund.ACTIVE).exists():
        raise ValidationError("A refund is still in progress. Verify it before changing payment records.")


@transaction.atomic
def record(user, slug, public_id, raw):
    item = locked_reservation(user, slug, public_id)
    form = TerminalPaymentForm(raw)
    if not form.is_valid():
        raise ValidationError(form.errors.as_text())
    data = form.cleaned_data
    amount = int(data["amount"] * 100)
    extra_charge = max(0, amount - data["expected_balance"])
    existing = LedgerEntry.objects.filter(key=data["key"]).first()
    if existing:
        if (existing.order_id, existing.channel, existing.kind, existing.actor_id,
                existing.amount_cents, existing.paid_on, existing.external_reference, existing.extra_charge_cents, existing.note) != (
                item.order_id, data["method"], "payment", user.pk, amount,
                data["paid_on"], data["external_reference"], extra_charge, data["description"]):
            raise ValidationError("This request was already submitted with different details. Refresh the reservation.")
        if LedgerEntry.objects.filter(reverses=existing).exists():
            raise ValidationError("This entry was voided. Refresh the reservation before recording a replacement.")
        return existing
    if item.state not in {"confirmed", "completed"}:
        raise ValidationError("Record payments only against confirmed or completed reservations.")
    require_settled_operations(item)
    if data["paid_on"] > studio_today(item):
        raise ValidationError("The payment date cannot be in the future at this studio.")
    amounts = totals(item.order)
    if data["expected_balance"] != amounts["outstanding_cents"] or data["expected_total"] != amounts["total_cents"]:
        raise ValidationError("The balance changed. Refresh the reservation before recording this payment.")
    if amounts["total_cents"] + extra_charge > 99999999:
        raise ValidationError("The reservation total exceeds the supported amount.")
    # All manual writers hold the school lock. Voided references may be re-entered
    # with corrected details; the original receipt and correction remain in history.
    receipts = LedgerEntry.objects.filter(school_id=item.order.school_id, environment=item.order.environment)
    if data["external_reference"] and (receipts.filter(channel="terminal", kind="payment", reversal__isnull=True,
            external_reference__iexact=data["external_reference"]).exists() or receipts.filter(
            reference__iexact=data["external_reference"]).exists()):
        raise ValidationError("This payment reference is already recorded. Check the existing payment instead.")
    entry = LedgerEntry(order=item.order, school_id=item.order.school_id,
        environment=item.order.environment, key=data["key"], kind="payment", channel=data["method"],
        amount_cents=amount, reference=f'{data["method"]}:{data["key"]}', actor=user, note=data["description"],
        paid_on=data["paid_on"], external_reference=data["external_reference"], extra_charge_cents=extra_charge)
    entry.full_clean()
    entry.save()
    _audit(user, item, "terminal_payment_recorded", entry_id=entry.pk,
        amount_cents=amount, extra_charge_cents=extra_charge, reference=entry.external_reference, paid_on=str(entry.paid_on))
    return entry


@transaction.atomic
def void(user, slug, public_id, entry_id, raw):
    item = locked_reservation(user, slug, public_id)
    form = VoidTerminalPaymentForm(raw)
    if not form.is_valid():
        raise ValidationError(form.errors.as_text())
    entry = get_object_or_404(item.order.entries, pk=entry_id, channel__in=["terminal", "cash"], kind="payment")
    reason = form.cleaned_data["reason"]
    existing = LedgerEntry.objects.filter(reverses=entry).first()
    if existing:
        if (existing.actor_id, existing.note) != (user.pk, reason):
            raise ValidationError("This entry has already been voided. Refresh the reservation.")
        return existing
    require_settled_operations(item)
    if totals(item.order)["net_cents"] < entry.amount_cents:
        raise ValidationError("This correction conflicts with recorded refunds. Review the payment history first.")
    correction = LedgerEntry(order=item.order, school_id=item.order.school_id,
        environment=item.order.environment, key=uuid.uuid4(), kind="void", channel=entry.channel,
        amount_cents=entry.amount_cents, reference=f'terminal-void:{entry.pk}', actor=user,
        paid_on=entry.paid_on, external_reference=entry.external_reference, note=reason, reverses=entry,
        extra_charge_cents=entry.extra_charge_cents)
    correction.full_clean()
    correction.save()
    _audit(user, item, "terminal_payment_voided", entry_id=entry.pk, reason=reason)
    return correction


@transaction.atomic
def correct(user, slug, public_id, entry_id, raw):
    """Replace a staff receipt atomically without changing its date, method or extra charges."""
    item = locked_reservation(user, slug, public_id)
    form = CorrectTerminalPaymentForm(raw)
    if not form.is_valid():
        raise ValidationError(form.errors.as_text())
    data = form.cleaned_data
    entry = get_object_or_404(item.order.entries, pk=entry_id, channel__in=["terminal", "cash"], kind="payment")
    amount = int(data["amount"] * 100)
    reference = f'terminal-correction:{entry.pk}:{data["key"]}'
    existing = LedgerEntry.objects.filter(key=data["key"]).first()
    if existing:
        if (existing.order_id, existing.reference, existing.amount_cents, existing.external_reference) != (
                item.order_id, reference, amount, data["external_reference"]):
            raise ValidationError("This correction was already submitted with different details. Refresh the reservation.")
        return existing
    if LedgerEntry.objects.filter(reverses=entry).exists():
        raise ValidationError("This payment has already changed. Refresh the reservation before editing it.")
    require_settled_operations(item)
    amounts = totals(item.order)
    if (data["expected_balance"], data["expected_total"]) != (amounts["outstanding_cents"], amounts["total_cents"]):
        raise ValidationError("The balance changed. Refresh the reservation before correcting this payment.")
    net = amounts["net_cents"] - entry.amount_cents + amount
    if net < 0:
        raise ValidationError("This correction conflicts with recorded refunds. Review the payment history first.")
    if amount < entry.extra_charge_cents or net > max(amounts["total_cents"], amounts["net_cents"]):
        raise ValidationError("This correction would change extra charges. Record additional purchases separately.")
    receipts = LedgerEntry.objects.filter(school_id=item.order.school_id, environment=item.order.environment).exclude(pk=entry.pk)
    if data["external_reference"] and (receipts.filter(channel__in=["terminal", "cash"], kind="payment",
            reversal__isnull=True, external_reference__iexact=data["external_reference"]).exists()
            or receipts.filter(reference__iexact=data["external_reference"]).exists()):
        raise ValidationError("This payment reference is already recorded. Check the existing payment instead.")
    common = dict(order=item.order, school_id=item.order.school_id, environment=item.order.environment,
        channel=entry.channel, paid_on=entry.paid_on, extra_charge_cents=entry.extra_charge_cents)
    reversal = LedgerEntry(**common, key=uuid.uuid4(), kind="void", amount_cents=entry.amount_cents,
        reference=f'terminal-void:{entry.pk}', actor=user, external_reference=entry.external_reference,
        note="Payment record corrected", reverses=entry)
    reversal.full_clean()
    reversal.save()
    replacement = LedgerEntry(**common, key=data["key"], kind="payment", amount_cents=amount,
        reference=reference, actor=entry.actor, external_reference=data["external_reference"], note=entry.note)
    replacement.full_clean()
    replacement.save()
    _audit(user, item, "terminal_payment_corrected", entry_id=entry.pk, replacement_id=replacement.pk,
        old_amount_cents=entry.amount_cents, amount_cents=amount,
        old_reference=entry.external_reference, reference=replacement.external_reference)
    return replacement
