import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier
from unittest.mock import MagicMock

import pytest
import stripe
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import close_old_connections, connection
from django.test import Client
from django.utils import timezone

from core.payments import checkout, recovery, refunds, service
from core.payments.models import LedgerEntry, PaymentAccount, PaymentAttempt, Refund
from core.services import billing_stripe as gateway
from scheduling.checkout import (approve_refund, recover_attempt, request_close, resolve_review,
    settle_booking_event, settle_booking_snapshot)
from scheduling.models import Reservation
from scheduling.reservations import active_claims, cancel_reservation
from .test_checkout import merchant, online, prepare, session_for, event_for
from .test_reservations import inventory, workshop_request, path

pytestmark = pytest.mark.django_db


def paid(inventory, member):
    item = online(member, workshop_request(inventory))
    attempt = prepare(item)
    settle_booking_event(event_for(attempt))
    attempt.refresh_from_db()
    item.refresh_from_db()
    return item, attempt


def approved(inventory, member):
    item, attempt = paid(inventory, member)
    cancel_reservation(member.user, member.profile.school.slug, item.public_id)
    refund = approve_refund(member.user, member.profile.school.slug, item.public_id, attempt.public_id,
        key=uuid.uuid4(), amount_cents=1500, reason="Customer cancellation")
    return item, attempt, refund


def refund_for(refund, status="succeeded"):
    return {"id": "re_" + refund.public_id.hex, "status": status, "currency": "usd", "amount": refund.amount_cents,
        "payment_intent": refund.attempt.provider_intent, "metadata": {
            "payment_refund": str(refund.public_id), "payment_order": str(refund.attempt.order.public_id)}}


def ready(record):
    type(record).objects.filter(pk=record.pk).update(checked_at=None, next_check_at=None)


def test_recovery_finds_unknown_and_restores_same_checkout(inventory, merchant, booking_member, monkeypatch):
    item = online(booking_member, workshop_request(inventory))
    attempt = prepare(item)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(state="unknown")
    create = MagicMock(side_effect=AssertionError("Recovery must not create checkout"))
    monkeypatch.setattr(gateway, "create_order_checkout", create)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", session_for)
    assert recover_attempt(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "open" and attempt.provider_session
    assert checkout.open_checkout(attempt) == session_for(attempt)["url"]
    assert not create.called and active_claims().filter(pk=item.pk).exists()
    assert not recover_attempt(attempt, force=True)  # double-click cooldown


@pytest.mark.parametrize("status", ["expired", "complete"])
def test_close_payment_race_is_verified_not_assumed(inventory, merchant, booking_member, monkeypatch, status):
    item = online(booking_member, workshop_request(inventory))
    attempt = prepare(item)
    attempt = request_close(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id)
    with pytest.raises(ValidationError):
        checkout.open_checkout(attempt)
    with pytest.raises(ValidationError):
        cancel_reservation(booking_member.user, item.profile.school.slug, item.public_id)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", session_for)
    monkeypatch.setattr(gateway, "expire_order_checkout", lambda a, sid: session_for(a, status=status))
    assert recover_attempt(attempt)
    attempt.refresh_from_db()
    item.refresh_from_db()
    assert attempt.state == ("paid" if status == "complete" else "expired")
    assert service.totals(item.order)["net_cents"] == (2500 if status == "complete" else 0)
    assert item.state == ("confirmed" if status == "complete" else "held")
    if status == "expired":
        assert not active_claims().filter(pk=item.pk).exists()
    cancel_reservation(booking_member.user, item.profile.school.slug, item.public_id)
    assert service.totals(item.order)["refunded_cents"] == 0


def test_missed_webhook_recovered_and_duplicates_do_not_double_settle(inventory, merchant, booking_member, monkeypatch):
    item = online(booking_member, workshop_request(inventory))
    attempt = prepare(item)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", lambda a: session_for(a, status="complete"))
    assert recover_attempt(attempt)
    settle_booking_event(event_for(attempt))
    assert LedgerEntry.objects.count() == 1
    ready(attempt)
    assert recover_attempt(attempt)
    item.refresh_from_db()
    assert item.state == "confirmed"


def test_unknown_old_create_never_reissued_outage_retains_capacity_and_pauses(inventory, merchant, booking_member, monkeypatch):
    item = online(booking_member, workshop_request(inventory))
    attempt = prepare(item)
    past = timezone.now() - timedelta(days=2)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(state="unknown", created_at=past, expires_at=past)
    Reservation.objects.filter(pk=item.pk).update(hold_until=past)
    create = MagicMock()
    monkeypatch.setattr(gateway, "create_order_checkout", create)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", MagicMock(side_effect=TimeoutError("secret must not be saved")))
    for count in range(1, 7):
        ready(attempt)
        assert not recover_attempt(attempt)
        attempt.refresh_from_db()
        assert attempt.recovery_failures == count and "secret" not in attempt.attention
        assert attempt.next_check_at > timezone.now()
        assert active_claims().filter(pk=item.pk).exists()
    ready(attempt)
    assert not recover_attempt(attempt) and not create.called
    assert not recovery.due_records(PaymentAttempt, active=PaymentAttempt.ACTIVE).exists()
    monkeypatch.setattr(gateway, "retrieve_order_checkout", lambda a: session_for(a, status="expired"))
    assert recover_attempt(attempt, force=True)
    attempt.refresh_from_db()
    assert attempt.attention == "" and attempt.recovery_failures == 0


def test_recovery_lease_backoff_and_crash_recovery(inventory, merchant, booking_member):
    attempt = prepare(online(booking_member, workshop_request(inventory)))
    action = MagicMock()
    PaymentAttempt.objects.filter(pk=attempt.pk).update(lease_until=timezone.now() + timedelta(minutes=2))
    assert not recovery.run(attempt, action, force=True)
    assert not recovery.due_records(PaymentAttempt, active=PaymentAttempt.ACTIVE).exists()
    PaymentAttempt.objects.filter(pk=attempt.pk).update(lease_until=timezone.now() - timedelta(seconds=1), next_check_at=timezone.now()+timedelta(minutes=5))
    assert not recovery.run(attempt, action)
    assert recovery.run(attempt, action, force=True)
    assert action.call_count == 1
    attempt.refresh_from_db()
    assert attempt.lease_token is None and attempt.lease_until is None


@pytest.mark.parametrize("field,value", [("amount_total", 1), ("currency", "eur"), ("livemode", True), ("status", "invalid")])
def test_recovery_rejects_unbound_provider_response(inventory, merchant, booking_member, monkeypatch, field, value):
    item = online(booking_member, workshop_request(inventory))
    attempt = prepare(item)
    data = session_for(attempt, status="complete")
    data[field] = value
    monkeypatch.setattr(gateway, "retrieve_order_checkout", lambda a: data)
    assert not recover_attempt(attempt)
    assert not LedgerEntry.objects.exists()
    assert active_claims().filter(pk=item.pk).exists()


def test_refund_pending_success_retry_failed_releases_allocation(inventory, merchant, booking_member, monkeypatch):
    item, attempt, refund = approved(inventory, booking_member)
    create = MagicMock(side_effect=lambda r: refund_for(r, "pending"))
    monkeypatch.setattr(gateway, "create_order_refund", create)
    assert refunds.process(refund)
    refund.refresh_from_db()
    assert refund.state == "pending" and refund.submitted_at and not refund.entry_id
    assert service.totals(item.order)["refunded_cents"] == 0
    with pytest.raises(ValidationError, match="exceeds"):
        refunds.approve(attempt, key=uuid.uuid4(), amount_cents=1001, reason="Second", actor=booking_member.user)
    ready(refund)
    monkeypatch.setattr(gateway, "retrieve_order_refund", lambda r: refund_for(r))
    assert refunds.process(refund)
    assert service.totals(item.order)["refunded_cents"] == 1500
    refunds.apply_snapshot(refund, refund_for(refund))
    ready(refund)
    assert refunds.process(refund)
    assert create.call_count == 1 and LedgerEntry.objects.filter(kind="refund").count() == 1
    with pytest.raises(ValidationError, match="Terminal"):
        refunds.apply_snapshot(refund, refund_for(refund, "pending"))
    second = refunds.approve(attempt, key=uuid.uuid4(), amount_cents=1000, reason="Remaining", actor=booking_member.user)
    refunds.apply_snapshot(second, refund_for(second, "failed"))
    third = refunds.approve(attempt, key=uuid.uuid4(), amount_cents=1000, reason="Retry after failure", actor=booking_member.user)
    refunds.apply_snapshot(third, refund_for(third, "canceled"))
    refunds.apply_snapshot(third, refund_for(third, "canceled"))
    assert service.totals(item.order)["refunded_cents"] == 1500


def test_unknown_refund_is_never_blindly_created_again(inventory, merchant, booking_member, monkeypatch):
    item, attempt, refund = approved(inventory, booking_member)
    create = MagicMock(side_effect=TimeoutError)
    monkeypatch.setattr(gateway, "create_order_refund", create)
    assert not refunds.process(refund)
    refund.refresh_from_db()
    assert refund.state == "unknown" and refund.submitted_at and not refund.entry_id
    Refund.objects.filter(pk=refund.pk).update(submitted_at=timezone.now()-timedelta(days=3))
    ready(refund)
    monkeypatch.setattr(gateway, "retrieve_order_refund", lambda r: refund_for(r))
    assert refunds.process(refund)
    assert create.call_count == 1
    assert service.totals(item.order)["net_cents"] == 1000


@pytest.mark.parametrize("field,value", [("amount", True), ("amount", 500), ("currency", "eur"),
    ("payment_intent", "pi_wrong"), ("metadata", {}), ("id", "bad"), ("status", "unknown")])
def test_refund_snapshot_binding(inventory, merchant, booking_member, field, value):
    item, attempt, refund = approved(inventory, booking_member)
    data = refund_for(refund)
    data[field] = value
    with pytest.raises(ValidationError):
        refunds.apply_snapshot(refund, data)
    assert not LedgerEntry.objects.filter(kind="refund").exists()


def test_refund_eligibility_idempotency_identity_and_reasons(inventory, merchant, booking_member):
    item, attempt = paid(inventory, booking_member)
    args = dict(key=uuid.uuid4(), amount_cents=1000, reason="Reason")
    with pytest.raises(ValidationError, match="cancel"):
        approve_refund(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id, **args)
    cancel_reservation(booking_member.user, item.profile.school.slug, item.public_id)
    refund = approve_refund(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id, **args)
    assert approve_refund(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id, **args).pk == refund.pk
    with pytest.raises(ValidationError, match="different"):
        refunds.approve(attempt, actor=booking_member.user, **{**args, "amount_cents": 900})
    for amount, reason in [(True, "Reason"), (0, "Reason"), (1, " "), (1, "x"*241)]:
        with pytest.raises(ValidationError):
            refunds.approve(attempt, key=uuid.uuid4(), amount_cents=amount, reason=reason, actor=booking_member.user)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(state="expired")
    with pytest.raises(ValidationError, match="verified"):
        refunds.approve(attempt, actor=booking_member.user, **args)


def test_new_sales_disabled_does_not_stop_existing_money_recovery(inventory, merchant, booking_member, monkeypatch):
    item, attempt, refund = approved(inventory, booking_member)
    merchant.enabled = False
    merchant.save()
    profile = item.profile
    profile.native_reservations_enabled = False
    profile.enabled = False
    profile.save()
    monkeypatch.setattr(gateway, "create_order_refund", lambda r: refund_for(r))
    assert refunds.process(refund)
    assert service.totals(item.order)["refunded_cents"] == 1500


def test_review_explicitly_rechecks_capacity_and_payment(inventory, merchant, booking_member):
    item, attempt = paid(inventory, booking_member)
    Reservation.objects.filter(pk=item.pk).update(state="review")
    resolve_review(booking_member.user, item.profile.school.slug, item.public_id)
    item.refresh_from_db()
    assert item.state == "confirmed"
    with pytest.raises(ValidationError):
        resolve_review(booking_member.user, item.profile.school.slug, item.public_id)
    Reservation.objects.filter(pk=item.pk).update(state="review", quantity=99)
    with pytest.raises(ValidationError):
        resolve_review(booking_member.user, item.profile.school.slug, item.public_id)
    item.refresh_from_db()
    assert item.state == "review"


def test_staff_recovery_and_refund_ui_no_provider_calls_on_get(inventory, merchant, booking_member, client, monkeypatch):
    item = online(booking_member, workshop_request(inventory))
    attempt = prepare(item)
    client.force_login(booking_member.user)
    fetch = MagicMock(side_effect=lambda a: session_for(a, status="complete"))
    monkeypatch.setattr(gateway, "retrieve_order_checkout", fetch)
    detail = path(booking_member, "reservation_detail", public_id=item.public_id)
    page = client.get(detail)
    assert b"is being verified" in page.content
    for label in (b"Check payment status", b"Stop this checkout", b"Close checkout"):
        assert label not in page.content
    removed_url = f"{detail}payments/{attempt.public_id}/check/"
    assert client.post(removed_url, {"action": "check"}).status_code == 404
    listing = client.get(path(booking_member, "reservations")+"?attention=1")
    assert listing.context["attention_count"] == 1 and not fetch.called
    recover_attempt(attempt, force=True)
    assert fetch.call_count == 1
    cancel_reservation(booking_member.user, item.profile.school.slug, item.public_id)
    refund_url = path(booking_member, "refund_create", public_id=item.public_id)
    refund_page = client.get(refund_url)
    assert refund_page.status_code == 200 and b">Approve refund</button>" in refund_page.content
    assert b"Test refund only" not in refund_page.content
    assert b"original payment method" in refund_page.content
    assert client.post(refund_url, {}).status_code == 400
    data = {"key": str(uuid.uuid4()), "attempt": str(attempt.public_id), "amount": "15.00", "reason": "Cancellation", "confirm": "on"}
    monkeypatch.setattr(gateway, "create_order_refund", lambda r: refund_for(r, "requires_action"))
    assert client.post(refund_url, data).status_code == 302
    refund = Refund.objects.get()
    page = client.get(detail)
    assert b"Provider action required" in page.content and b"merchant&#x27;s" not in page.content
    assert b"Check refund status" in page.content
    ready(refund)
    monkeypatch.setattr(gateway, "retrieve_order_refund", lambda r: refund_for(r))
    assert client.post(path(booking_member, "refund_recover", public_id=item.public_id, refund_id=refund.public_id)).status_code == 302
    assert b"Refund recorded" in client.get(detail).content and b"Stripe" in client.get(detail).content
    assert client.post(refund_url, {**data, "key": str(uuid.uuid4()), "amount": "50.00"}).status_code == 400
    listing = client.get(path(booking_member, "reservations")+"?attention=1")
    assert listing.context["attention_count"] == 1  # still $10 held after cancellation


@pytest.mark.parametrize("action", ["check", "retry"])
def test_recovery_views_permissions_csrf_and_cross_order(inventory, merchant, booking_member, client, action):
    item, attempt, refund = approved(inventory, booking_member)
    urls = [path(booking_member, "payment_review", public_id=item.public_id),
        path(booking_member, "refund_create", public_id=item.public_id),
        path(booking_member, "refund_recover", public_id=item.public_id, refund_id=refund.public_id)]
    for url in urls:
        assert client.post(url, {"action": action}).status_code == 302
    client.force_login(booking_member.user)
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(booking_member.user)
    for url in urls:
        assert csrf.post(url, {"action": action}).status_code == 403
    assert client.post(urls[2].replace(str(refund.public_id), str(uuid.uuid4())), {"action": action}).status_code == 404
    if action == "retry":
        assert client.post(urls[2], {"action": action}).status_code == 400  # Stripe is unchanged.
    booking_member.role = "viewer"
    booking_member.save()
    for url in urls:
        assert client.post(url, {"action": action}).status_code == 404


def test_close_and_review_actions(inventory, merchant, booking_member, client, monkeypatch):
    item = online(booking_member, workshop_request(inventory))
    attempt = prepare(item)
    client.force_login(booking_member.user)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", session_for)
    monkeypatch.setattr(gateway, "expire_order_checkout", lambda a, sid: session_for(a, status="expired"))
    request_close(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id)
    recover_attempt(attempt, force=True)
    url = path(booking_member, "payment_review", public_id=item.public_id)
    assert client.post(url).status_code == 400
    assert client.post(url, {"confirm": "yes"}).status_code == 302
    settle_booking_event(event_for(attempt))
    Reservation.objects.filter(pk=item.pk).update(state="review")
    assert client.post(url, {"confirm": "yes"}).status_code == 302
    item.refresh_from_db()
    assert item.state == "confirmed"


def test_bounded_reconciliation_command(inventory, merchant, booking_member, monkeypatch):
    item, attempt, refund = approved(inventory, booking_member)
    attempt.state = "unknown"
    attempt.save()
    monkeypatch.setattr(gateway, "create_order_refund", lambda r: refund_for(r))
    out = StringIO()
    before_attempt = PaymentAttempt.objects.filter(pk=attempt.pk).values().get()
    before_refund = Refund.objects.filter(pk=refund.pk).values().get()
    call_command("reconcile_booking_payments", provider="square", stdout=out)
    assert "Checked 0; verified 0" in out.getvalue()
    assert PaymentAttempt.objects.filter(pk=attempt.pk).values().get() == before_attempt
    assert Refund.objects.filter(pk=refund.pk).values().get() == before_refund
    out = StringIO()
    call_command("reconcile_booking_payments", limit=1, stdout=out)
    assert "Checked 1; verified 1" in out.getvalue()
    for bad in [0, 101]:
        with pytest.raises(CommandError):
            call_command("reconcile_booking_payments", limit=bad)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", lambda a: session_for(a, status="complete"))
    out = StringIO()
    call_command("reconcile_booking_payments", limit=20, stdout=out)
    assert "Checked 1; verified 1" in out.getvalue()


def test_sdk_recovery_and_refund_transport_scope(inventory, merchant, booking_member, monkeypatch):
    item, attempt, refund = approved(inventory, booking_member)
    sdk = MagicMock()
    factory = MagicMock(return_value=sdk)
    monkeypatch.setattr(stripe, "StripeClient", factory)
    sdk.v1.checkout.sessions.retrieve.return_value = session_for(attempt, status="complete")
    assert gateway.retrieve_order_checkout(attempt)["status"] == "complete"
    sdk.v1.checkout.sessions.retrieve.assert_called_once_with(attempt.provider_session)
    sdk.v1.checkout.sessions.expire.return_value = session_for(attempt, status="expired")
    assert gateway.expire_order_checkout(attempt, attempt.provider_session)["status"] == "expired"
    sdk.v1.checkout.sessions.expire.side_effect = TimeoutError()
    assert gateway.expire_order_checkout(attempt, attempt.provider_session)["status"] == "complete"
    sdk.v1.refunds.create.return_value = stripe.StripeObject.construct_from(refund_for(refund), "sk_test_x")
    assert gateway.create_order_refund(refund)["status"] == "succeeded"
    params = sdk.v1.refunds.create.call_args.kwargs
    assert params["options"]["idempotency_key"] == f"payment-refund:{refund.public_id}"
    assert params["params"]["payment_intent"] == attempt.provider_intent and params["params"]["amount"] == 1500
    refund.provider_refund = refund_for(refund)["id"]
    sdk.v1.refunds.retrieve.return_value = refund_for(refund)
    assert gateway.retrieve_order_refund(refund)["id"] == refund.provider_refund
    for call in factory.call_args_list:
        assert call.args == ("sk_test_local_unit_tests_only",) and call.kwargs["stripe_account"] == "acct_local_test"


def test_bounded_lookup_pagination_missing_duplicate_and_old_attempt(inventory, merchant, booking_member, monkeypatch):
    item, attempt, refund = approved(inventory, booking_member)
    sdk = MagicMock()
    monkeypatch.setattr(stripe, "StripeClient", lambda *a, **k: sdk)
    attempt.provider_session = None
    data = session_for(attempt)
    sdk.v1.checkout.sessions.list.side_effect = [
        {"data": [{"id": "cs_other", "metadata": {}}], "has_more": True}, {"data": [data], "has_more": False}]
    assert gateway.retrieve_order_checkout(attempt) == data
    assert sdk.v1.checkout.sessions.list.call_args.kwargs["params"]["starting_after"] == "cs_other"
    sdk.v1.refunds.list.return_value = {"data": [refund_for(refund)], "has_more": False}
    assert gateway.retrieve_order_refund(refund)["id"] == refund_for(refund)["id"]
    for rows in [[], [data, data]]:
        sdk.v1.checkout.sessions.list.side_effect = None
        sdk.v1.checkout.sessions.list.return_value = {"data": rows, "has_more": False}
        with pytest.raises(ValidationError, match="unique"):
            gateway.retrieve_order_checkout(attempt)
    sdk.v1.checkout.sessions.list.return_value = {"data": [data], "has_more": True}
    with pytest.raises(ValidationError, match="limit"):
        gateway.retrieve_order_checkout(attempt)


@pytest.mark.django_db(transaction=True)
def test_refund_concurrent_approvals_cannot_overallocate(inventory, merchant, booking_member):
    item, attempt = paid(inventory, booking_member)
    cancel_reservation(booking_member.user, item.profile.school.slug, item.public_id)
    barrier = Barrier(2)
    def execute():
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            return approve_refund(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id,
                key=uuid.uuid4(), amount_cents=2000, reason="Cancel",).pk
        except ValidationError:
            return None
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(), range(2)))
    assert sum(value is not None for value in results) == 1
    assert Refund.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_provider_io_not_inside_database_transaction(inventory, merchant, booking_member, monkeypatch):
    item, attempt, refund = approved(inventory, booking_member)
    def create(r):
        assert not connection.in_atomic_block
        assert Refund.objects.get(pk=r.pk).submitted_at
        return refund_for(r)
    monkeypatch.setattr(gateway, "create_order_refund", create)
    assert refunds.process(refund)


@pytest.mark.parametrize("field,value", [("url", "http://evil.example"), ("expires_at", 1), ("payment_status", "paid")])
def test_open_recovered_session_must_be_safe(inventory, merchant, booking_member, field, value):
    attempt = prepare(online(booking_member, workshop_request(inventory)))
    data = session_for(attempt)
    data[field] = value
    with pytest.raises(ValidationError):
        settle_booking_snapshot(attempt, data)
    assert PaymentAttempt.objects.get(pk=attempt.pk).state == "pending"


def test_stale_open_cannot_reopen_terminal_payment(inventory, merchant, booking_member):
    item, attempt = paid(inventory, booking_member)
    settle_booking_snapshot(attempt, session_for(attempt))
    attempt.refresh_from_db()
    assert attempt.state == "paid" and LedgerEntry.objects.count() == 1


def test_expiry_claim_with_paid_status_cannot_release_capacity(inventory, merchant, booking_member):
    item = online(booking_member, workshop_request(inventory))
    attempt = prepare(item)
    data = session_for(attempt, status="expired")
    data["payment_status"] = "paid"
    with pytest.raises(ValidationError, match="Expiry"):
        settle_booking_snapshot(attempt, data)
    assert active_claims().filter(pk=item.pk).exists()


def test_snapshot_duplicate_session_and_changed_account_rejected(inventory, merchant, booking_member, monkeypatch):
    item, attempt = paid(inventory, booking_member)
    second = prepare(item)
    data = session_for(second)
    data["id"] = attempt.provider_session
    with pytest.raises(ValidationError, match="already bound"):
        settle_booking_snapshot(second, data)
    # Change only the order's binding; no key fallback or provider call is permitted.
    second.order.account = None
    second.order.save(update_fields=["account"])
    fetch = MagicMock()
    monkeypatch.setattr(gateway, "retrieve_order_checkout", fetch)
    assert not recover_attempt(second) and not fetch.called


def test_refund_changed_identity_is_rejected(inventory, merchant, booking_member):
    item, attempt, first = approved(inventory, booking_member)
    refunds.apply_snapshot(first, refund_for(first, "pending"))
    data = refund_for(first)
    data["id"] = "re_different"
    with pytest.raises(ValidationError):
        refunds.apply_snapshot(first, data)
    second = refunds.approve(attempt, actor=booking_member.user, key=uuid.uuid4(), amount_cents=1000, reason="Rest")
    data = refund_for(second)
    data["id"] = refund_for(first)["id"]
    with pytest.raises(ValidationError, match="already bound"):
        refunds.apply_snapshot(second, data)
    data = refund_for(second)
    data["livemode"] = True
    with pytest.raises(ValidationError):
        refunds.apply_snapshot(second, data)


def test_refund_account_mismatch_never_calls_provider(inventory, merchant, booking_member, monkeypatch):
    from core.models import School
    item, attempt, refund = approved(inventory, booking_member)
    school = School.objects.create(slug="other-recovery", display_name="Other recovery")
    other = PaymentAccount.objects.create(school=school, reference="acct_other")
    PaymentAttempt.objects.filter(pk=attempt.pk).update(account=other)
    provider = MagicMock()
    monkeypatch.setattr(gateway, "create_order_refund", provider)
    assert not refunds.process(refund) and not provider.called
    with pytest.raises(ValidationError, match="binding"):
        settle_booking_snapshot(attempt, session_for(attempt))
    ready(attempt)
    assert not recover_attempt(attempt)


@pytest.mark.django_db(transaction=True)
def test_concurrent_refund_workers_make_one_provider_call(inventory, merchant, booking_member, monkeypatch):
    from threading import Event
    item, attempt, refund = approved(inventory, booking_member)
    entered, finish = Event(), Event()
    def create(r):
        entered.set()
        assert finish.wait(timeout=10)
        return refund_for(r)
    provider = MagicMock(side_effect=create)
    monkeypatch.setattr(gateway, "create_order_refund", provider)
    def execute():
        close_old_connections()
        try:
            return refunds.process(Refund.objects.get(pk=refund.pk))
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute)
        assert entered.wait(timeout=10)
        assert not pool.submit(execute).result(timeout=10)
        finish.set()
        assert first.result(timeout=10)
    assert provider.call_count == 1 and LedgerEntry.objects.filter(kind="refund").count() == 1


@pytest.mark.parametrize("state", ["confirmed", "completed"])
def test_excess_payment_refund_preserves_confirmed_capacity(inventory, merchant, booking_member, state):
    item, attempt = paid(inventory, booking_member)
    if state == "completed":
        from scheduling.completion import complete
        complete(booking_member.user, item.profile.school.slug, item.public_id)
    balance = prepare(item)
    settle_booking_event(event_for(balance, expired=True))
    replacement = prepare(item)
    settle_booking_event(event_for(replacement))
    settle_booking_event(event_for(balance))  # late payment from expired checkout
    assert service.totals(item.order)["net_cents"] == 17500
    args = dict(key=uuid.uuid4(), amount_cents=7500, reason="Duplicate late payment")
    refund = approve_refund(booking_member.user, item.profile.school.slug, item.public_id, balance.public_id, **args)
    assert approve_refund(booking_member.user, item.profile.school.slug, item.public_id, balance.public_id, **args).pk == refund.pk
    with pytest.raises(ValidationError, match="excess"):
        approve_refund(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id,
            key=uuid.uuid4(), amount_cents=1, reason="Would underpay")
    refunds.apply_snapshot(refund, refund_for(refund))
    assert approve_refund(booking_member.user, item.profile.school.slug, item.public_id, balance.public_id, **args).pk == refund.pk
    assert service.totals(item.order)["net_cents"] == 10000
    item.refresh_from_db()
    assert item.state == state and active_claims().filter(pk=item.pk).exists()


def test_refund_cannot_race_outstanding_checkout(inventory, merchant, booking_member):
    item, attempt = paid(inventory, booking_member)
    prepare(item)
    with pytest.raises(ValidationError, match="finish or expire"):
        approve_refund(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id,
            key=uuid.uuid4(), amount_cents=100, reason="Too early")
