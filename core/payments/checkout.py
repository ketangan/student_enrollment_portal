"""Shared test checkout lifecycle. Provider calls are outside database transactions."""

from datetime import timedelta
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import School
from core.services import billing_stripe as gateway
from .models import LedgerEntry, PaymentAttempt, PaymentOrder, ProviderEventReceipt
from .accounts import matches_event_account, validate_scope
from .service import totals


def validate_account(order, *, new_payment=True):
    account = order.account
    if (not account or account.school_id != order.school_id or order.environment != "test"
            or account.environment != "test" or order.merchant_reference != account.reference
            or not account.reference.startswith("acct_") or order.currency != "usd"):
        raise ValidationError("This order has no valid sandbox merchant binding.")
    validate_scope(account)
    if new_payment and (not account.enabled or not gateway.merchant_test_checkout_ready()):
        raise ValidationError("Stripe sandbox checkout is not enabled for this business.")


def amount_due(order):
    net = totals(order)["net_cents"]
    required = order.deposit_cents or order.total_cents
    return max(0, required - net) if net < required else max(0, order.total_cents - net)


@transaction.atomic
def prepare_attempt(order, *, return_url):
    """Call inside the domain inventory lock; no mutable client amount or account input."""
    School.objects.select_for_update().get(pk=order.school_id)
    order = PaymentOrder.objects.select_for_update(of=("self",)).select_related("account").get(pk=order.pk)
    validate_account(order)
    existing = order.attempts.filter(state__in=PaymentAttempt.ACTIVE).first()
    if existing:
        if existing.amount_cents != amount_due(order):
            raise ValidationError("The balance changed while checkout was open. Provider review is required.")
        return existing
    amount = amount_due(order)
    if amount < 50:
        raise ValidationError("No payable balance of at least $0.50 remains.")
    # 35 minutes leaves room for network latency within Stripe's 30-minute minimum.
    return PaymentAttempt.objects.create(order=order, account=order.account, amount_cents=amount,
        success_url=return_url, cancel_url=return_url, expires_at=timezone.now() + timedelta(minutes=35))


def _validate_session(attempt, session):
    order = attempt.order
    metadata = session.get("metadata") or {}
    # Deferred methods need another settlement lifecycle. This guard also covers
    # recovery, so an unsupported checkout cannot become usable after a retry.
    if (session.get("livemode") is not False or session.get("mode") != "payment"
            or session.get("payment_method_types") != ["card"]
            or session.get("currency") != order.currency
            or type(session.get("amount_total")) is not int or session["amount_total"] != attempt.amount_cents
            or metadata.get("payment_order") != str(order.public_id)
            or metadata.get("payment_attempt") != str(attempt.public_id)
            or metadata.get("purpose") != order.purpose
            or not isinstance(session.get("id"), str) or not session["id"].startswith("cs_test_") or len(session["id"]) > 100
            or (attempt.provider_session and attempt.provider_session != session["id"])):
        raise ValidationError("Checkout does not match its order, amount, currency or environment.")


def open_checkout(attempt):
    """Timeouts keep the same attempt and capacity claim. Never invent a new payment."""
    attempt.refresh_from_db()
    if attempt.state == "paid":
        return ""
    validate_account(attempt.order)
    if attempt.close_requested or attempt.state == "expired" or attempt.expires_at <= timezone.now():
        raise ValidationError("Checkout needs provider verification before another payment can start.")
    if attempt.state == "open":
        return attempt.checkout_url
    try:
        session = gateway.create_order_checkout(attempt)
        _validate_session(attempt, session)
        url = urlsplit(session.get("url") or "")
        if (session.get("status") != "open" or session.get("expires_at") != int(attempt.expires_at.timestamp())
                or url.scheme != "https" or url.netloc != "checkout.stripe.com"):
            raise ValidationError("Checkout response could not be verified.")
    except Exception as exc:
        PaymentAttempt.objects.filter(pk=attempt.pk, state__in=["pending", "unknown"]).update(state="unknown")
        raise ValidationError("Checkout could not be verified. Your reservation stays held; retry uses the same payment request.") from exc
    with transaction.atomic():
        current = PaymentAttempt.objects.select_for_update().get(pk=attempt.pk)
        if current.state in ("paid", "expired"):
            return ""
        current.provider_session, current.checkout_url, current.state = session["id"], session["url"], "open"
        current.save(update_fields=["provider_session", "checkout_url", "state"])
    return session["url"]


def event_attempt(event):
    """Resolve only an account-bound test event, not arbitrary metadata from another account."""
    if not isinstance(event, dict):
        raise ValidationError("Invalid event payload.")
    if event.get("type") not in {"checkout.session.completed", "checkout.session.expired"}:
        return None
    data = event.get("data")
    session = data.get("object") if isinstance(data, dict) else None
    if not isinstance(session, dict) or not isinstance(session.get("metadata"), dict):
        raise ValidationError("Invalid checkout payload.")
    expected = "expired" if event["type"] == "checkout.session.expired" else "complete"
    if session.get("status") != expected:
        raise ValidationError("Event type and checkout status disagree.")
    attempt_id = session["metadata"].get("payment_attempt")
    if not attempt_id:
        return None
    try:
        attempt = PaymentAttempt.objects.select_related("order__account", "account").get(public_id=attempt_id)
    except (PaymentAttempt.DoesNotExist, ValidationError, ValueError) as exc:
        raise ValidationError("Unknown payment attempt.") from exc
    validate_account(attempt.order, new_payment=False)
    if (event.get("livemode") is not False or not matches_event_account(attempt.account, event)
            or attempt.account_id != attempt.order.account_id
            or not isinstance(event.get("id"), str) or not event["id"].startswith("evt_") or len(event["id"]) > 100):
        raise ValidationError("Event account or environment does not match this payment.")
    _validate_session(attempt, session)
    return attempt


@transaction.atomic
def apply_event(event):
    """Caller holds its domain inventory lock. Receipt, ledger and domain update commit together."""
    attempt = event_attempt(event)
    if attempt is None:
        return None
    School.objects.select_for_update().get(pk=attempt.order.school_id)
    PaymentOrder.objects.select_for_update().get(pk=attempt.order_id)
    attempt = PaymentAttempt.objects.select_for_update().select_related("order").get(pk=attempt.pk)
    if ProviderEventReceipt.objects.filter(account=attempt.account, environment="test", event_id=event["id"]).exists():
        return attempt
    attempt = apply_snapshot(attempt, event["data"]["object"])
    ProviderEventReceipt.objects.create(account=attempt.account, event_id=event["id"],
        event_type=event["type"], attempt=attempt)
    return attempt


@transaction.atomic
def apply_snapshot(attempt, session):
    """Only called with account-scoped provider data, under the domain inventory lock."""
    School.objects.select_for_update().get(pk=attempt.order.school_id)
    PaymentOrder.objects.select_for_update().get(pk=attempt.order_id)
    attempt = PaymentAttempt.objects.select_for_update(of=("self",)).select_related("order__account").get(pk=attempt.pk)
    validate_account(attempt.order, new_payment=False)
    if attempt.account_id != attempt.order.account_id:
        raise ValidationError("Checkout account binding changed.")
    _validate_session(attempt, session)
    if PaymentAttempt.objects.filter(provider_session=session["id"]).exclude(pk=attempt.pk).exists():
        raise ValidationError("Checkout session is already bound to another order.")
    if session.get("status") == "open":
        if attempt.state not in PaymentAttempt.ACTIVE:
            return attempt
        if session.get("payment_status") != "unpaid":
            raise ValidationError("Open checkout has an unexpected payment status.")
        url = urlsplit(session.get("url") or "")
        if (url.scheme != "https" or url.netloc != "checkout.stripe.com"
                or session.get("expires_at") != int(attempt.expires_at.timestamp())):
            raise ValidationError("Open checkout address or expiry could not be verified.")
        attempt.checkout_url = session["url"]
        attempt.state = "open"
    elif session.get("status") == "expired":
        if session.get("status") != "expired" or session.get("payment_status") != "unpaid":
            raise ValidationError("Expiry is not verified.")
        if attempt.state != "paid":
            attempt.state = "expired"
    else:
        intent = session.get("payment_intent")
        if (session.get("status") != "complete" or session.get("payment_status") != "paid"
                or not isinstance(intent, str) or not intent.startswith("pi_") or len(intent) > 100):
            raise ValidationError("Payment is not verified as paid.")
        if attempt.provider_intent and attempt.provider_intent != intent:
            raise ValidationError("Payment identity changed.")
        if PaymentAttempt.objects.filter(provider_intent=intent).exclude(pk=attempt.pk).exists():
            raise ValidationError("This payment has already settled another order.")
        if not attempt.entry_id:
            # Record verified money even if it arrived late. Domain code decides availability.
            attempt.entry = LedgerEntry.objects.create(order=attempt.order, school_id=attempt.order.school_id,
                environment="test", key=attempt.public_id, kind="payment", channel="stripe",
                amount_cents=attempt.amount_cents, reference=intent)
        attempt.provider_intent, attempt.state = intent, "paid"
        from core.services.payment_notifications import queue_payment_notice
        queue_payment_notice(key=f"booking:{attempt.order.merchant_reference}:{intent}:paid",
            school=attempt.order.school, kind="Booking payment received", reference=intent,
            amount=attempt.amount_cents, currency=attempt.order.currency, livemode=False)
    attempt.provider_session = session["id"]
    attempt.save(update_fields=["provider_intent", "provider_session", "state", "entry", "checkout_url"])
    return attempt
