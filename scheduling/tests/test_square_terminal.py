import base64
import hashlib
import hmac
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.http import Http404
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.payments import checkout, square, square_terminal, refunds
from core.payments.models import LedgerEntry, PaymentAttempt, ProviderEventReceipt
from core.payments.service import totals
from scheduling import terminal_checkout
from scheduling.checkout import recover_attempt, request_close
from scheduling.integrations import square_terminals
from scheduling.models import Location, SquareTerminal
from .test_checkout import online
from .test_reservations import inventory, workshop_request
from .test_square_payments import square_account, payment_for, post_event

pytestmark = pytest.mark.django_db


@pytest.fixture
def terminal(inventory, square_account):
    return SquareTerminal.objects.create(location=inventory[1].location, account=square_account,
        name="Front desk", device_id=square_terminal.SIMULATIONS["success"][1], status="paired", simulation=True)


@pytest.fixture
def item(inventory, square_account, booking_member):
    item = online(booking_member, workshop_request(inventory))
    item.state = "confirmed"
    item.save(update_fields=["state"])
    return item


def prepare(member, item, terminal, key=None, option="due"):
    return terminal_checkout.prepare(member.user, item.profile.school.slug, item.public_id, terminal.pk,
        key or uuid.uuid4(), option, totals(item.order)["outstanding_cents"])


def result_for(attempt, status="COMPLETED"):
    return {"id": "checkout_" + attempt.public_id.hex, "status": status,
        "amount_money": {"amount": attempt.amount_cents, "currency": "USD"},
        "reference_id": str(attempt.public_id), "device_options": {"device_id": attempt.device_reference},
        "location_id": attempt.account.location_reference, "app_id": "sandbox-sq0idb-test",
        "payment_ids": ["square_" + attempt.public_id.hex] if status == "COMPLETED" else []}


def payment(attempt):
    value = payment_for(attempt)
    value["terminal_checkout_id"] = result_for(attempt)["id"]
    return value


def transport(attempt, monkeypatch, status="COMPLETED"):
    def respond(method, path, payload=None):
        if path.startswith("/payments/"):
            return {"payment": payment(attempt)}
        if path.endswith("/search"):
            return {"checkouts": [result_for(attempt, status)]}
        return {"checkout": result_for(attempt, status)}
    call = Mock(side_effect=respond)
    monkeypatch.setattr(square, "request", call)
    return call


def test_terminal_uses_same_ledger_receipts_refunds_and_idempotency(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    call = transport(attempt, monkeypatch)
    terminal_checkout.submit(attempt)
    terminal_checkout.submit(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "paid" and attempt.entry.channel == "square"
    assert attempt.entry.actor_id is None and attempt.entry.amount_cents == 2500
    assert totals(item.order)["outstanding_cents"] == 7500
    assert LedgerEntry.objects.count() == 1 and call.call_count == 2
    payload = call.call_args_list[0].args[2]
    assert payload["idempotency_key"] == str(attempt.public_id)
    assert payload["checkout"]["device_options"]["tip_settings"] == {"allow_tipping": False}
    assert prepare(booking_member, item, terminal, key=attempt.public_id).pk == attempt.pk
    # The original provider reference remains usable by the existing refund service.
    refund = refunds.approve(attempt, key=uuid.uuid4(), amount_cents=2500, reason="Canceled", actor=booking_member.user)
    snapshot = square.refund_snapshot(refund, {"id": "refund_test", "payment_id": attempt.provider_intent,
        "location_id": "LTEST", "reason": square.refund_reason(refund),
        "amount_money": {"amount": 2500, "currency": "USD"}, "status": "COMPLETED"})
    refunds.apply_snapshot(refund, snapshot)
    assert totals(item.order)["outstanding_cents"] == 10000


@pytest.mark.parametrize("status", ["PENDING", "IN_PROGRESS", "CANCEL_REQUESTED", "CANCELED"])
def test_unpaid_terminal_states_never_record_money(item, terminal, booking_member, monkeypatch, status):
    attempt = prepare(booking_member, item, terminal)
    transport(attempt, monkeypatch, status)
    terminal_checkout.submit(attempt)
    attempt.refresh_from_db()
    assert attempt.state == ("expired" if status == "CANCELED" else "unknown")
    assert not LedgerEntry.objects.exists()
    if status != "CANCELED":
        with pytest.raises(ValidationError):
            prepare(booking_member, item, terminal)
        with pytest.raises(ValidationError):
            checkout.prepare_attempt(item.order, return_url="/", payment_option="due")


def test_lost_create_response_recovers_without_resending(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    call = Mock(side_effect=ValidationError("Timeout"))
    monkeypatch.setattr(square, "request", call)
    with pytest.raises(ValidationError):
        terminal_checkout.submit(attempt)
    terminal_checkout.submit(attempt)
    assert call.call_count == 1
    call = transport(attempt, monkeypatch)
    assert recover_attempt(attempt, force=True)
    assert call.call_args_list[0].args[:2] == ("POST", "/terminals/checkouts/search")
    assert all(row.args[1] != "/terminals/checkouts" for row in call.call_args_list)
    assert LedgerEntry.objects.count() == 1


def test_unknown_absence_keeps_payment_locked(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(submitted_at=timezone.now(), state="unknown")
    monkeypatch.setattr(square, "request", Mock(return_value={"checkouts": []}))
    assert not recover_attempt(attempt, force=True)
    attempt.refresh_from_db()
    assert attempt.attention and attempt.state == "unknown"
    with pytest.raises(ValidationError):
        prepare(booking_member, item, terminal)


def test_cancellation_requires_provider_confirmation(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    transport(attempt, monkeypatch, "PENDING")
    terminal_checkout.submit(attempt)
    request_close(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id)
    call = transport(attempt, monkeypatch, "CANCEL_REQUESTED")
    terminal_checkout.recover(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "unknown"
    transport(attempt, monkeypatch, "CANCELED")
    terminal_checkout.recover(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "expired" and not LedgerEntry.objects.exists()


def test_cancel_race_can_still_settle_a_completed_payment(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    transport(attempt, monkeypatch, "PENDING")
    terminal_checkout.submit(attempt)
    request_close(booking_member.user, item.profile.school.slug, item.public_id, attempt.public_id)
    def response(method, path, payload=None):
        if path.endswith("/cancel"):
            return {"checkout": result_for(attempt)}
        if path.startswith("/payments/"):
            return {"payment": payment(attempt)}
        return {"checkout": result_for(attempt, "PENDING")}
    call = Mock(side_effect=response)
    monkeypatch.setattr(square, "request", call)
    terminal_checkout.recover(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "paid" and LedgerEntry.objects.count() == 1
    assert any(row.args[1].endswith("/cancel") for row in call.call_args_list)


@pytest.mark.parametrize("field,value", [("location_id", "OTHER"), ("app_id", "OTHER"),
    ("reference_id", str(uuid.uuid4())), ("device_options", {"device_id": "OTHER"}),
    ("amount_money", {"amount": 1, "currency": "USD"}), ("status", "INVALID"), ("id", "../bad"),
    ("payment_ids", ["one", "two"])])
def test_terminal_mismatch_is_not_settled(item, terminal, booking_member, monkeypatch, field, value):
    attempt = prepare(booking_member, item, terminal)
    result = result_for(attempt)
    result[field] = value
    monkeypatch.setattr(square, "request", Mock(return_value={"checkout": result}))
    with pytest.raises(ValidationError):
        terminal_checkout.submit(attempt)
    assert not LedgerEntry.objects.exists()


@pytest.mark.parametrize("field,value", [("terminal_checkout_id", "other_checkout"),
    ("total_money", {"amount": 2600, "currency": "USD"}), ("location_id", "OTHER"), ("status", "PENDING")])
def test_completed_checkout_also_verifies_payment(item, terminal, booking_member, monkeypatch, field, value):
    attempt = prepare(booking_member, item, terminal)
    invalid = payment(attempt)
    invalid[field] = value
    monkeypatch.setattr(square, "request", Mock(side_effect=[{"checkout": result_for(attempt)}, {"payment": invalid}]))
    with pytest.raises(ValidationError):
        terminal_checkout.submit(attempt)
    assert not LedgerEntry.objects.exists()


def test_terminal_cannot_use_card_entry_or_change_methods(item, terminal, booking_member, monkeypatch, client):
    from django.core import signing
    from scheduling.square_checkout import submit
    attempt = prepare(booking_member, item, terminal)
    with pytest.raises(ValidationError):
        checkout.open_checkout(attempt)
    with pytest.raises(ValidationError):
        submit(attempt, "cnon:card")
    route = reverse("scheduling:square_checkout", kwargs={"school_slug": item.profile.school.slug,
        "token": signing.dumps(str(attempt.public_id), salt="square-booking-checkout")})
    assert client.get(route).status_code == 404


def test_wrong_location_disabled_device_and_stale_balance(item, terminal, booking_member):
    terminal.status = "disabled"
    terminal.save()
    with pytest.raises(Http404):
        prepare(booking_member, item, terminal)
    terminal.status = "paired"
    terminal.location = Location.objects.create(profile=item.profile, name="Other studio")
    terminal.save()
    with pytest.raises(Http404):
        prepare(booking_member, item, terminal)
    with pytest.raises(ValidationError, match="balance changed"):
        terminal_checkout.prepare(booking_member.user, item.profile.school.slug, item.public_id,
            terminal.pk, uuid.uuid4(), "full", 1)


def test_live_terminal_gate_does_not_change_online_gate(settings, square_account):
    square_account.environment = "live"
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = True
    settings.PAYMENTS_SQUARE_TERMINAL_LIVE_ENABLED = False
    assert not square_terminal.enabled(square_account)
    settings.PAYMENTS_SQUARE_TERMINAL_LIVE_ENABLED = True
    assert square_terminal.enabled(square_account)


def test_staff_endpoint_permissions_csrf_and_rendering(item, terminal, booking_member, client, monkeypatch):
    route = reverse("scheduling:terminal_collect", kwargs={"school_slug": item.profile.school.slug, "public_id": item.public_id})
    assert client.post(route, {}).status_code == 302
    client.force_login(booking_member.user)
    assert client.get(route).status_code == 405
    assert client.post(route, {"action": "unknown"}).status_code == 400
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(booking_member.user)
    assert csrf.post(route, {"action": "collect"}).status_code == 403
    detail = reverse("scheduling:reservation_detail", kwargs={"school_slug": item.profile.school.slug, "public_id": item.public_id})
    page = client.get(detail)
    assert page.status_code == 200 and b"Collect on Terminal" in page.content
    assert f'type="hidden" name="device" value="{terminal.pk}"'.encode() in page.content
    assert b'<select name="device"' not in page.content
    booking_member.role = "viewer"
    booking_member.save()
    assert client.post(route, {"action": "collect"}).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_concurrent_terminal_submission_calls_square_once(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    entered, release = Event(), Event()
    def response(method, path, payload=None):
        if path == "/terminals/checkouts":
            entered.set()
            assert release.wait(10)
            return {"checkout": result_for(attempt)}
        return {"payment": payment(attempt)}
    call = Mock(side_effect=response)
    monkeypatch.setattr(square, "request", call)
    def first():
        close_old_connections()
        try:
            terminal_checkout.submit(PaymentAttempt.objects.select_related("order__account").get(pk=attempt.pk))
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first)
        try:
            assert entered.wait(10)
            terminal_checkout.submit(attempt)
        finally:
            release.set()
        future.result(timeout=10)
    assert len([row for row in call.call_args_list if row.args[1] == "/terminals/checkouts"]) == 1
    assert LedgerEntry.objects.count() == 1


def test_payment_webhook_recovers_terminal_before_create_returns(item, terminal, booking_member, monkeypatch, client, settings):
    attempt = prepare(booking_member, item, terminal)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(submitted_at=timezone.now(), state="unknown")
    transport(attempt, monkeypatch)
    identity = str(uuid.uuid4())
    assert post_event(client, settings, attempt, event_id=identity).status_code == 200
    assert post_event(client, settings, attempt, event_id=identity).status_code == 200
    assert LedgerEntry.objects.count() == ProviderEventReceipt.objects.count() == 1


def test_sandbox_setup_scoped_and_idempotent(inventory, square_account, booking_member):
    request = RequestFactory().post("/")
    request.user = booking_member.user
    data = {"key": str(uuid.uuid4()), "location": inventory[1].location_id,
        "name": "Counter", "simulation": "canceled"}
    device = square_terminals.add(request, inventory[0].school.slug, data)
    assert device.device_id == square_terminal.SIMULATIONS["canceled"][1]
    assert square_terminals.add(request, inventory[0].school.slug, data).pk == device.pk
    assert square_terminals.check(request, inventory[0].school.slug, device.pk) == ""
    square_terminals.disable(request, inventory[0].school.slug, device.pk)
    device.refresh_from_db()
    assert device.status == "disabled"


def test_retry_replays_original_request_and_stops_after_deadline(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    calls = []
    def response(method, path, payload=None):
        if path == "/terminals/checkouts":
            calls.append(payload)
            if len(calls) == 1:
                raise ValidationError("Lost response")
            return {"checkout": result_for(attempt)}
        return {"payment": payment(attempt)}
    monkeypatch.setattr(square, "request", response)
    with pytest.raises(ValidationError):
        terminal_checkout.submit(attempt)
    assert terminal_checkout.retry(attempt)
    assert calls[0] == calls[1]
    assert LedgerEntry.objects.count() == 1
    PaymentAttempt.objects.filter(pk=attempt.pk).update(checked_at=None)
    assert not terminal_checkout.retry(attempt)
    assert len(calls) == 2


@pytest.mark.parametrize("close", [True, False])
def test_unsubmitted_expiry_or_close_never_calls_square(item, terminal, booking_member, monkeypatch, close):
    attempt = prepare(booking_member, item, terminal)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(close_requested=close,
        expires_at=timezone.now() - timedelta(seconds=1))
    call = Mock()
    monkeypatch.setattr(square, "request", call)
    terminal_checkout.recover(attempt)
    terminal_checkout.recover(attempt)
    terminal_checkout.submit(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "expired" and not call.called


def test_retry_rejects_expired_request(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(state="unknown", submitted_at=timezone.now(),
        expires_at=timezone.now() - timedelta(seconds=1))
    call = Mock()
    monkeypatch.setattr(square, "request", call)
    assert not terminal_checkout.retry(attempt)
    call.assert_not_called()


@pytest.mark.parametrize("rows,cursor", [(None, None), ([None], None), ([], 1), ([], "x" * 2001), ([], "next")])
def test_search_fails_closed_for_invalid_or_unbounded_results(item, terminal, booking_member, monkeypatch, rows, cursor):
    attempt = prepare(booking_member, item, terminal)
    monkeypatch.setattr(square, "request", Mock(return_value={"checkouts": rows, "cursor": cursor}))
    with pytest.raises(ValidationError):
        square_terminal.retrieve(attempt)


def test_search_pagination_and_identity_validation(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    call = Mock(side_effect=[{"checkouts": [], "cursor": "next"}, {"checkouts": [result_for(attempt)]}])
    monkeypatch.setattr(square, "request", call)
    assert square_terminal.retrieve(attempt)["id"] == result_for(attempt)["id"]
    with pytest.raises(ValidationError):
        square_terminal.retrieve(attempt, "../bad")
    with pytest.raises(ValidationError):
        square_terminal.cancel(attempt)


def test_pending_with_payment_id_and_missing_completed_id_do_not_unlock(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(submitted_at=timezone.now())
    for status, ids in [("PENDING", ["some_payment"]), ("COMPLETED", [])]:
        result = result_for(attempt, status)
        result["payment_ids"] = ids
        with pytest.raises(ValidationError):
            terminal_checkout.settle(attempt, result)
    assert not LedgerEntry.objects.exists()


def test_terminal_event_signature_replay_and_foreign_references(item, terminal, booking_member, monkeypatch, client, settings):
    attempt = prepare(booking_member, item, terminal)
    PaymentAttempt.objects.filter(pk=attempt.pk).update(submitted_at=timezone.now())
    call = transport(attempt, monkeypatch)
    event = {"event_id": str(uuid.uuid4()), "type": "terminal.checkout.updated", "merchant_id": "MTEST",
        "data": {"id": result_for(attempt)["id"], "object": {"checkout": result_for(attempt)}}}
    def post(signature=True):
        body = json.dumps(event).encode()
        digest = base64.b64encode(hmac.new(settings.PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET.encode(),
            settings.PAYMENTS_SQUARE_TEST_WEBHOOK_URL.encode() + body, hashlib.sha256).digest()).decode()
        return client.post(reverse("scheduling:square_test_webhook"), body, content_type="application/json",
            HTTP_X_SQUARE_HMACSHA256_SIGNATURE=digest if signature else "invalid")
    assert post(False).status_code == 400 and not call.called
    assert post().status_code == post().status_code == 200
    assert LedgerEntry.objects.count() == ProviderEventReceipt.objects.count() == 1
    event["event_id"] = str(uuid.uuid4())
    event["data"]["object"]["checkout"]["reference_id"] = "foreign"
    assert post().status_code == 200
    event["data"]["object"]["checkout"]["id"] = "../invalid"
    assert post().status_code == 503


def test_unsubmitted_terminal_webhook_is_rejected(item, terminal, booking_member):
    attempt = prepare(booking_member, item, terminal)
    event = {"event_id": "evt_terminal", "type": "terminal.checkout.updated", "merchant_id": "MTEST"}
    with pytest.raises(ValidationError):
        terminal_checkout.settle_terminal_event(attempt, event, result_for(attempt)["id"])


def test_device_setup_validation_and_enable(inventory, square_account, booking_member, terminal):
    request = RequestFactory().post("/")
    request.user = booking_member.user
    slug = inventory[0].school.slug
    with pytest.raises(ValidationError):
        square_terminals.add(request, slug, {})
    data = {"key": str(uuid.uuid4()), "location": terminal.location_id, "name": "Duplicate", "simulation": "success"}
    with pytest.raises(ValidationError, match="already configured"):
        square_terminals.add(request, slug, data)
    data["key"] = str(terminal.pk)
    with pytest.raises(ValidationError, match="already been used"):
        square_terminals.add(request, slug, data)
    square_terminals.disable(request, slug, terminal.pk)
    square_terminals.check(request, slug, terminal.pk, enable=True)
    terminal.refresh_from_db()
    assert terminal.status == "paired"


def pairing_result(device, status="UNPAIRED"):
    return {"id": "code_123", "location_id": device.account.location_reference, "product_type": "TERMINAL_API",
        "status": status, "device_id": "real_device_123" if status == "PAIRED" else "", "code": "ABCDEF"}


def test_pairing_uses_same_key_and_does_not_store_code(item, terminal, booking_member, monkeypatch, client):
    terminal.simulation, terminal.device_id, terminal.status = False, "", "pairing"
    terminal.save()
    route = reverse("scheduling:square_terminal_update", kwargs={"school_slug": item.profile.school.slug})
    client.force_login(booking_member.user)
    call = Mock(return_value={"device_code": pairing_result(terminal)})
    monkeypatch.setattr(square, "request", call)
    page = client.post(route, {"action": "check", "device": terminal.pk})
    assert page.status_code == 200 and b"ABCDEF" in page.content
    assert "no-store" in page["Cache-Control"] and page["Referrer-Policy"] == "no-referrer"
    terminal.refresh_from_db()
    assert terminal.code_id == "code_123" and not hasattr(terminal, "code")
    assert call.call_args.args[2]["idempotency_key"] == str(terminal.pk)
    call.return_value = {"device_code": pairing_result(terminal, "PAIRED")}
    assert client.post(route, {"action": "check", "device": terminal.pk}).status_code == 302
    terminal.refresh_from_db()
    assert terminal.status == "paired" and terminal.device_id == "real_device_123"
    assert call.call_args.args[:2] == ("GET", "/devices/codes/code_123")
    assert client.post(route, {"action": "disable", "device": terminal.pk}).status_code == 302
    assert client.post(route, {"action": "enable", "device": terminal.pk}).status_code == 302
    terminal.refresh_from_db()
    assert terminal.status == "paired"


@pytest.mark.parametrize("field,value", [("location_id", "OTHER"), ("product_type", "OTHER"), ("status", "INVALID"),
    ("id", "../bad"), ("device_id", "../bad")])
def test_pairing_rejects_wrong_identity(item, terminal, booking_member, monkeypatch, field, value):
    terminal.simulation, terminal.device_id = False, ""
    terminal.save()
    result = pairing_result(terminal, "PAIRED")
    result[field] = value
    monkeypatch.setattr(square, "request", Mock(return_value={"device_code": result}))
    request = RequestFactory().post("/")
    request.user = booking_member.user
    with pytest.raises(ValidationError):
        square_terminals.check(request, item.profile.school.slug, terminal.pk)


def test_staff_actions_cover_pending_cancel_and_validation(item, terminal, booking_member, client, monkeypatch):
    client.force_login(booking_member.user)
    route = reverse("scheduling:terminal_collect", kwargs={"school_slug": item.profile.school.slug, "public_id": item.public_id})
    key = uuid.uuid4()
    def response(method, path, payload=None):
        attempt = PaymentAttempt.objects.get(public_id=key)
        return {"checkout": result_for(attempt, "CANCELED" if path.endswith("/cancel") else "PENDING")}
    monkeypatch.setattr(square, "request", response)
    assert client.post(route, {"action": "collect", "key": key, "device": terminal.pk,
        "expected_balance": 10000, "expected_due": 2500, "payment_option": "due"}).status_code == 302
    attempt = PaymentAttempt.objects.get(public_id=key)
    assert client.post(route, {"action": "cancel", "attempt": key}, follow=True).status_code == 200
    attempt.refresh_from_db()
    assert attempt.state == "expired"
    for data in [{"action": "collect", "key": "bad"}, {"action": "check", "attempt": "bad"},
                 {"action": "retry", "attempt": key}, {"action": "check", "attempt": key}]:
        assert client.post(route, data).status_code == 302
    setup_route = reverse("scheduling:square_terminal_update", kwargs={"school_slug": item.profile.school.slug})
    assert client.post(setup_route, {"action": "invalid"}).status_code == 400
    assert client.post(setup_route, {"action": "check", "device": "bad"}).status_code == 302
    assert client.post(setup_route, {"action": "add", "key": uuid.uuid4(), "location": terminal.location_id,
        "name": "Offline", "simulation": "offline"}).status_code == 302


def test_location_restricted_owner_cannot_collect_other_studio(item, terminal, booking_member, client):
    other = Location.objects.create(profile=item.profile, name="Other")
    booking_member.all_locations = False
    booking_member.save()
    booking_member.locations.add(other)
    client.force_login(booking_member.user)
    route = reverse("scheduling:terminal_collect", kwargs={"school_slug": item.profile.school.slug, "public_id": item.public_id})
    assert client.post(route, {"action": "collect"}).status_code == 404
    setup_route = reverse("scheduling:square_terminal_update", kwargs={"school_slug": item.profile.school.slug})
    assert client.post(setup_route, {"action": "disable", "device": terminal.pk}).status_code == 404


def test_stale_deposit_and_reused_device_intent_are_rejected(item, terminal, booking_member):
    with pytest.raises(ValidationError, match="deposit changed"):
        terminal_checkout.prepare(booking_member.user, item.profile.school.slug, item.public_id,
            terminal.pk, uuid.uuid4(), "due", 10000, 2400)
    assert not PaymentAttempt.objects.exists()
    attempt = prepare(booking_member, item, terminal)
    other = SquareTerminal.objects.create(location=terminal.location, account=terminal.account,
        name="Other counter", device_id="other_device", status="paired", simulation=True)
    with pytest.raises(ValidationError, match="already been used"):
        prepare(booking_member, item, other, key=attempt.public_id)


@pytest.mark.parametrize("change", ["device", "profile", "reservation", "environment"])
def test_first_send_revalidates_current_collection_permission(item, terminal, booking_member, monkeypatch, change):
    attempt = prepare(booking_member, item, terminal)
    if change == "device":
        terminal.status = "disabled"
        terminal.save()
    elif change == "environment":
        terminal.simulation = False
        terminal.save()
    elif change == "profile":
        item.profile.enabled = False
        item.profile.save()
    else:
        item.state = "canceled"
        item.save()
    call = transport(attempt, monkeypatch)
    with pytest.raises(ValidationError):
        terminal_checkout.submit(attempt)
    attempt.refresh_from_db()
    assert attempt.submitted_at is None and not call.called
    assert not LedgerEntry.objects.exists()


def test_disabling_device_blocks_retry_but_preserves_verified_recovery(item, terminal, booking_member, monkeypatch):
    attempt = prepare(booking_member, item, terminal)
    monkeypatch.setattr(square, "request", Mock(side_effect=ValidationError("Timeout")))
    with pytest.raises(ValidationError):
        terminal_checkout.submit(attempt)
    terminal.status = "disabled"
    terminal.save()
    call = transport(attempt, monkeypatch)
    assert not terminal_checkout.retry(attempt) and not call.called
    PaymentAttempt.objects.filter(pk=attempt.pk).update(checked_at=None)
    assert recover_attempt(attempt, force=True)
    attempt.refresh_from_db()
    assert attempt.state == "paid" and LedgerEntry.objects.count() == 1
    assert all(row.args[1] != "/terminals/checkouts" for row in call.call_args_list)


def test_pairing_disabled_changed_and_duplicate_devices(item, terminal, booking_member, monkeypatch):
    request = RequestFactory().post("/")
    request.user = booking_member.user
    slug = item.profile.school.slug
    terminal.simulation, terminal.device_id, terminal.status = False, "", "disabled"
    terminal.save()
    call = Mock(return_value={"device_code": pairing_result(terminal, "PAIRED")})
    monkeypatch.setattr(square, "request", call)
    assert square_terminals.check(request, slug, terminal.pk) == "" and not call.called
    SquareTerminal.objects.create(location=terminal.location, account=terminal.account,
        name="Already paired", device_id="real_device_123", status="paired")
    with pytest.raises(ValidationError, match="already paired"):
        square_terminals.check(request, slug, terminal.pk, enable=True)
    terminal.device_id, terminal.status = "original_device", "paired"
    terminal.save()
    with pytest.raises(ValidationError, match="identity changed"):
        square_terminals.check(request, slug, terminal.pk)
    terminal.simulation, terminal.status = True, "disabled"
    terminal.save()
    item.profile.school.is_demo = False
    item.profile.school.save()
    with pytest.raises(ValidationError, match="cannot be used for live"):
        square_terminals.check(request, slug, terminal.pk, enable=True)
