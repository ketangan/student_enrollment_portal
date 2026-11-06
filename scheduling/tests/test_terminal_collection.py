import uuid
from datetime import timedelta
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.payments import checkout, square, square_terminal, refunds
from core.payments.models import PaymentAttempt
from core.payments.service import totals
from scheduling import booking_documents, terminal_checkout
from scheduling.checkout import recover_attempt
from scheduling.integrations import square_terminals
from scheduling.models import SquareTerminal
from scheduling.reservation_queries import staff_reservations
from scheduling.reservation_audit import audit_page
from .test_square_terminal import item, terminal as sandbox_terminal, prepare, transport, result_for
from .test_square_payments import square_account
from .test_reservations import inventory

pytestmark = pytest.mark.django_db


@pytest.fixture
def terminal(sandbox_terminal):
    # Domain-money tests mock a device; the real Sandbox success limit is tested below.
    sandbox_terminal.device_id = "mock-terminal"
    sandbox_terminal.save(update_fields=["device_id"])
    return sandbox_terminal


def custom(member, item, terminal, amount="20.00", treatment="partial", reason="", key=None, balance=10000):
    return terminal_checkout.prepare(member.user, item.profile.school.slug, item.public_id, terminal.pk,
        key or uuid.uuid4(), "full", balance, collection={"amount": amount, "treatment": treatment, "reason": reason})


@pytest.mark.parametrize("amount,treatment,reason,adjustment,balance,label", [
    ("0.01", "partial", "", 0, 9999, None),
    ("20.00", "partial", "", 0, 8000, None),
    ("20.00", "partial", "Deposit collected", 0, 8000, None),
    ("100.00", "partial", "Paid at the counter", 0, 0, None),
    ("120.00", "partial", "Accessory including tax", 2000, 0, "Additional purchases"),
    ("80.00", "discount", "Manager discount", -2000, 0, "Discount"),
    ("75.00", "credit", "Credit from canceled reservation 123", -2500, 0, "Manual credit"),
    ("120.00", "partial", "", 2000, 0, "Additional purchases"),
    ("80.00", "discount", "", -2000, 0, "Discount"),
    ("75.00", "credit", "", -2500, 0, "Manual credit"),
])
def test_custom_settlement_is_auditable_and_idempotent(item, terminal, booking_member, monkeypatch,
        amount, treatment, reason, adjustment, balance, label):
    key = uuid.uuid4()
    attempt = custom(booking_member, item, terminal, amount, treatment, reason, key)
    assert attempt.adjustment_cents == adjustment and attempt.requested_by == booking_member.user
    assert totals(item.order)["total_cents"] == 10000  # Pending is not money or a committed adjustment.
    transport(attempt, monkeypatch)
    terminal_checkout.submit(attempt)
    terminal_checkout.settle(attempt, result_for(attempt))
    assert custom(booking_member, item, terminal, amount, treatment, reason, key).pk == attempt.pk
    amounts = totals(item.order)
    assert amounts["outstanding_cents"] == balance
    assert amounts["total_cents"] == 10000 + adjustment
    assert amounts["paid_cents"] == int(float(amount) * 100)
    assert item.order.entries.count() == 1 and item.order.total_cents == 10000
    assert item.order.entries.get().note == reason
    assert staff_reservations(item.profile).get(pk=item.pk).effective_total == amounts["total_cents"]
    if reason:
        assert any(reason in row["detail"] for row in audit_page(item, None)["rows"])
    document = booking_documents.snapshot(item, "receipt")
    if label:
        assert label in [line["label"] for line in document["lines"]]
    if adjustment < 0:
        assert next(line["amount"] for line in document["lines"] if line["label"] == label).startswith("-$")
    with pytest.raises(ValidationError, match="different"):
        custom(booking_member, item, terminal, "21.00", treatment, reason, key)
    with pytest.raises(ValidationError, match="different"):
        custom(booking_member, item, terminal, amount, treatment, reason + " changed", key)


@pytest.mark.parametrize("amount,treatment,reason", [
    ("", "partial", ""), ("NaN", "partial", ""), ("Infinity", "partial", ""),
    ("0", "partial", ""), ("0.499", "partial", ""), ("-1", "partial", ""),
    ("1000000", "partial", ""), ("100", "credit", "Reason"), ("110", "discount", "Reason"),
    ("80", "unknown", "Reason"), ("120", "partial", "x" * 241),
])
def test_invalid_amounts_and_adjustments_create_no_attempt(item, terminal, booking_member, amount, treatment, reason):
    with pytest.raises(ValidationError):
        custom(booking_member, item, terminal, amount, treatment, reason)
    assert not item.order.attempts.exists()


def test_canceled_discount_changes_neither_quote_nor_balance(item, terminal, booking_member, monkeypatch):
    attempt = custom(booking_member, item, terminal, "80", "discount", "Approved")
    transport(attempt, monkeypatch, "CANCELED")
    terminal_checkout.submit(attempt)
    assert totals(item.order)["total_cents"] == totals(item.order)["outstanding_cents"] == 10000
    assert not item.order.entries.exists()


def test_refund_is_limited_to_real_money_not_discount_or_credit(item, terminal, booking_member, monkeypatch):
    attempt = custom(booking_member, item, terminal, "75", "credit", "Prior cancellation")
    transport(attempt, monkeypatch)
    terminal_checkout.submit(attempt)
    attempt.refresh_from_db()
    with pytest.raises(ValidationError):
        refunds.approve(attempt, key=uuid.uuid4(), amount_cents=10000, reason="Refund", actor=booking_member.user)
    refund = refunds.approve(attempt, key=uuid.uuid4(), amount_cents=7500, reason="Refund", actor=booking_member.user)
    snapshot = square.refund_snapshot(refund, {"id": "refund_credit", "payment_id": attempt.provider_intent,
        "location_id": "LTEST", "reason": square.refund_reason(refund),
        "amount_money": {"amount": 7500, "currency": "USD"}, "status": "COMPLETED"})
    refunds.apply_snapshot(refund, snapshot)
    assert totals(item.order)["refunded_cents"] == 7500
    assert totals(item.order)["outstanding_cents"] == 7500
    assert totals(item.order)["credit_cents"] == 2500


def test_stale_adjustment_and_total_overflow_are_rejected(item, terminal, booking_member):
    with pytest.raises(ValidationError, match="balance changed"):
        custom(booking_member, item, terminal, "20", balance=9999)
    item.order.total_cents = 99999999
    item.order.save(update_fields=["total_cents"])
    # An existing payment permits extras but never an unbounded aggregate quote.
    from core.payments.models import LedgerEntry
    LedgerEntry.objects.create(order=item.order, school=item.order.school, environment="test", kind="payment",
        channel="cash", amount_cents=99999999, actor=booking_member.user, key=uuid.uuid4(), paid_on=timezone.localdate())
    with pytest.raises(ValidationError, match="supported range"):
        custom(booking_member, item, terminal, "20", reason="Extra", balance=0)


def test_extra_purchase_on_fully_paid_booking(item, terminal, booking_member, monkeypatch, client):
    attempt = custom(booking_member, item, terminal, "100")
    transport(attempt, monkeypatch)
    terminal_checkout.submit(attempt)
    client.force_login(booking_member.user)
    url = reverse("scheduling:reservation_detail", args=[item.profile.school.slug, item.public_id])
    page = client.get(url).content.decode()
    assert 'data-payment-dialog="terminal-collection-dialog"' in page
    assert 'value="" placeholder="0.00" required' in page
    attempt = custom(booking_member, item, terminal, "18", reason="Accessory", balance=0)
    transport(attempt, monkeypatch)
    terminal_checkout.submit(attempt)
    assert totals(item.order)["paid_cents"] == totals(item.order)["total_cents"] == 11800


def test_custom_amount_cannot_override_online_checkout(item):
    with pytest.raises(ValidationError):
        checkout.prepare_attempt(item.order, return_url="/", terminal_amount=500)


def test_adjustment_constraint_rejects_unattributed_or_online_adjustments(item, terminal, booking_member):
    attempt = prepare(booking_member, item, terminal)
    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAttempt.objects.filter(pk=attempt.pk).update(adjustment_cents=-500, adjustment_kind="discount", requested_by=None)
    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAttempt.objects.filter(pk=attempt.pk).update(adjustment_reason="Unattributed", requested_by=None)
    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAttempt.objects.filter(pk=attempt.pk).update(collection_method="online", device_reference="",
            adjustment_reason="Not a Terminal", requested_by=booking_member.user)


@pytest.mark.parametrize("amount", ["25.01", "33.22"])
def test_success_simulator_limit_rejected_before_attempt_or_provider_call(item, sandbox_terminal, booking_member, monkeypatch, amount):
    call = Mock()
    monkeypatch.setattr(square, "request", call)
    with pytest.raises(ValidationError, match="up to \\$25.00"):
        custom(booking_member, item, sandbox_terminal, amount)
    assert not item.order.attempts.exists()
    call.assert_not_called()


def test_success_simulator_boundary_and_regular_form_default(item, sandbox_terminal, booking_member, monkeypatch):
    attempt = terminal_checkout.prepare(booking_member.user, item.profile.school.slug, item.public_id,
        sandbox_terminal.pk, uuid.uuid4(), "full", 10000, collection={"amount": "25.00"})
    assert attempt.adjustment_cents == 0 and attempt.adjustment_kind == ""
    transport(attempt, monkeypatch)
    terminal_checkout.submit(attempt)
    assert totals(item.order)["outstanding_cents"] == 7500
    assert square_terminal.maximum_amount_cents("live", sandbox_terminal.device_id) is None
    assert square_terminal.maximum_amount_cents("test", square_terminal.SIMULATIONS["canceled"][1]) is None


def test_sandbox_full_balance_option_cannot_bypass_limit(item, sandbox_terminal, booking_member):
    with pytest.raises(ValidationError, match="Sandbox"):
        prepare(booking_member, item, sandbox_terminal, option="full")
    assert not item.order.attempts.exists()


def test_live_amount_is_not_limited_by_sandbox_rule(item, terminal, booking_member):
    attempt = custom(booking_member, item, terminal, "33.22")
    # Exercise the guard directly without enabling or sending a live payment.
    attempt.account.environment = "live"
    attempt.device_reference = square_terminal.SIMULATIONS["success"][1]
    square_terminal.validate_amount(attempt)


def test_collection_form_uses_optional_controls_and_device_limit(item, sandbox_terminal, booking_member, client):
    client.force_login(booking_member.user)
    page = client.get(reverse("scheduling:reservation_detail", args=[item.profile.school.slug, item.public_id])).content.decode()
    assert 'data-maximum="25.00"' in page
    assert 'Nothing more to pay after this' in page and 'Description (optional):' in page
    assert '<textarea id="collection-reason"' in page
    assert '<select name="device"' not in page
    assert f'type="hidden" name="device" value="{sandbox_terminal.pk}"' in page
    assert 'data-collection-treatment hidden disabled' in page
    assert 'Keep remaining balance due' not in page and 'Adjustment description' not in page


def test_multiple_terminals_require_explicit_selection_and_disabled_ones_are_hidden(item, sandbox_terminal, booking_member, client):
    other = SquareTerminal.objects.create(location=sandbox_terminal.location, account=sandbox_terminal.account,
        name="Second counter", device_id="second-counter", simulation=True, status="paired")
    client.force_login(booking_member.user)
    route = reverse("scheduling:reservation_detail", args=[item.profile.school.slug, item.public_id])
    page = client.get(route).content.decode()
    assert '<select name="device"' in page and 'Choose a Terminal' in page
    assert str(other.pk) in page
    other.status = "disabled"
    other.save(update_fields=["status"])
    page = client.get(route).content.decode()
    assert '<select name="device"' not in page and str(other.pk) not in page


def test_hidden_terminal_cannot_be_replaced_with_disabled_or_foreign_device(item, sandbox_terminal, booking_member):
    sandbox_terminal.status = "disabled"
    sandbox_terminal.save(update_fields=["status"])
    from django.http import Http404
    with pytest.raises(Http404):
        custom(booking_member, item, sandbox_terminal, "1")
    assert not item.order.attempts.exists()


@pytest.mark.parametrize("provider_status", ["COMPLETED", "CANCELED", "PENDING", "IN_PROGRESS", "CANCEL_REQUESTED"])
@pytest.mark.parametrize("action", ["check", "cancel"])
def test_terminal_outcomes_have_no_standalone_messages(item, terminal, booking_member, monkeypatch, client, provider_status, action):
    from django.contrib.messages import get_messages

    attempt = prepare(booking_member, item, terminal)
    transport(attempt, monkeypatch, "PENDING")
    terminal_checkout.submit(attempt)
    transport(attempt, monkeypatch, provider_status)
    client.force_login(booking_member.user)
    response = client.post(reverse("scheduling:terminal_collect", args=[item.profile.school.slug, item.public_id]),
        {"action": action, "attempt": attempt.public_id})
    attempt.refresh_from_db()
    expected_state = "paid" if provider_status == "COMPLETED" else "expired" if provider_status == "CANCELED" else "unknown"
    assert response.status_code == 302 and attempt.state == expected_state
    assert not list(get_messages(response.wsgi_request))


def test_terminal_error_slots_are_inside_forms_not_action_rows(item, terminal, booking_member, client):
    client.force_login(booking_member.user)
    url = reverse("scheduling:reservation_detail", args=[item.profile.school.slug, item.public_id])
    slot = '<p data-terminal-message class="notice error" role="alert" hidden></p>'
    assert slot in client.get(url).content.decode()
    prepare(booking_member, item, terminal)
    page = client.get(url).content.decode()
    assert slot in page and 'class="actions" data-terminal-poll' not in page


def test_legacy_oversized_request_cannot_be_submitted_but_can_be_recovered(item, sandbox_terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, sandbox_terminal)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(amount_cents=3322)
    attempt.refresh_from_db()
    call = Mock()
    monkeypatch.setattr(square, "request", call)
    with pytest.raises(ValidationError, match="Sandbox"):
        terminal_checkout.submit(attempt)
    with pytest.raises(ValidationError, match="Sandbox"):
        square_terminal.create(attempt)
    attempt.refresh_from_db()
    assert attempt.submitted_at is None
    call.assert_not_called()
    PaymentAttempt.objects.filter(pk=attempt.pk).update(submitted_at=timezone.now(), terminal_status="PENDING", state="unknown")
    attempt.refresh_from_db()
    assert "cannot approve $33.22" in attempt.terminal_message
    transport(attempt, monkeypatch, "CANCELED")
    terminal_checkout.recover(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "expired" and not item.order.entries.exists()


@pytest.mark.parametrize("status,closing,text", [
    ("PENDING", False, "Waiting for Terminal"), ("IN_PROGRESS", False, "Complete the"),
    ("CANCEL_REQUESTED", False, "Canceling"), ("PENDING", True, "Canceling"),
    ("", False, "Waiting for Square"),
])
def test_pending_status_describes_actual_stage(item, terminal, booking_member, status, closing, text):
    attempt = prepare(booking_member, item, terminal)
    attempt.terminal_status, attempt.close_requested = status, closing
    assert text in attempt.terminal_message


def test_status_json_reconciles_after_five_seconds_and_does_not_repeat(item, terminal, booking_member, monkeypatch, client):
    attempt = prepare(booking_member, item, terminal)
    transport(attempt, monkeypatch, "PENDING")
    terminal_checkout.submit(attempt)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(checked_at=timezone.now() - timedelta(seconds=6))
    call = transport(attempt, monkeypatch)
    client.force_login(booking_member.user)
    url = reverse("scheduling:terminal_collect", args=[item.profile.school.slug, item.public_id])
    response = client.post(url, {"action": "check", "attempt": attempt.public_id}, HTTP_ACCEPT="application/json")
    assert response.json()["state"] == "paid" and not response.json()["active"]
    calls = call.call_count
    assert client.post(url, {"action": "check", "attempt": attempt.public_id, "automatic": "yes"}, HTTP_ACCEPT="application/json").status_code == 200
    assert call.call_count == calls
    response = client.post(url, {"action": "collect", "key": "bad"}, HTTP_ACCEPT="application/json")
    assert response.status_code == 400 and response.json()["error"]


def test_automatic_failure_backoff_is_not_bypassed(item, terminal, booking_member, monkeypatch, client):
    attempt = prepare(booking_member, item, terminal)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(recovery_failures=1, next_check_at=timezone.now() + timedelta(minutes=5))
    call = Mock()
    monkeypatch.setattr(square, "request", call)
    client.force_login(booking_member.user)
    url = reverse("scheduling:terminal_collect", args=[item.profile.school.slug, item.public_id])
    assert client.post(url, {"action": "check", "attempt": attempt.public_id, "automatic": "yes"}, HTTP_ACCEPT="application/json").status_code == 200
    call.assert_not_called()


def ping_result(device, **changes):
    return {"action": {"id": "termapia:123", "device_id": device.device_id, "type": "PING", "status": "PENDING",
        "location_id": device.account.location_reference, "app_id": "sandbox-sq0idb-test", **changes}}


def test_connection_ping_is_scoped_throttled_and_never_charges(item, terminal, booking_member, monkeypatch, client):
    terminal.simulation = False
    terminal.save()
    request = RequestFactory().post("/")
    request.user = booking_member.user
    call = Mock(return_value=ping_result(terminal))
    monkeypatch.setattr(square, "request", call)
    slug = item.profile.school.slug
    device = square_terminals.ping(request, slug, terminal.pk)
    assert device.ping_status == "PENDING" and device.ping_id == "termapia:123"
    assert call.call_args.args[1] == "/terminals/actions"
    square_terminals.ping(request, slug, terminal.pk, automatic=True)
    assert call.call_count == 1
    SquareTerminal.objects.filter(pk=device.pk).update(ping_checked_at=timezone.now() - timedelta(seconds=6))
    call.return_value = ping_result(terminal, status="COMPLETED")
    client.force_login(booking_member.user)
    response = client.post(reverse("scheduling:square_terminal_update", args=[slug]),
        {"action": "ping", "device": terminal.pk, "automatic": "yes"}, HTTP_ACCEPT="application/json")
    assert not response.json()["active"] and "verified" in response.json()["message"]
    assert call.call_args.args[1] == "/terminals/actions/termapia%3A123"
    square_terminals.ping(request, slug, terminal.pk, automatic=True)
    assert call.call_count == 2 and not item.order.entries.exists()


@pytest.mark.parametrize("changes", [{"device_id": "other"}, {"app_id": "other"}, {"location_id": "other"},
    {"id": "../bad"}, {"type": "CHARGE"}, {"status": "INVALID"}])
def test_ping_rejects_foreign_or_invalid_response(item, terminal, booking_member, monkeypatch, changes):
    terminal.simulation = False
    terminal.save()
    request = RequestFactory().post("/")
    request.user = booking_member.user
    monkeypatch.setattr(square, "request", Mock(return_value=ping_result(terminal, **changes)))
    with pytest.raises(ValidationError):
        square_terminals.ping(request, item.profile.school.slug, terminal.pk)


def test_sandbox_cannot_claim_physical_connection(item, terminal, booking_member, client):
    client.force_login(booking_member.user)
    response = client.post(reverse("scheduling:square_terminal_update", args=[item.profile.school.slug]),
        {"action": "ping", "device": terminal.pk}, HTTP_ACCEPT="application/json")
    assert response.status_code == 400 and "physical" in response.json()["error"]
