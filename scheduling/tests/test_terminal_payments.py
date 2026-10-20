import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import LedgerEntry, PaymentAccount, PaymentAttempt, Refund
from core.payments.service import totals
from scheduling import terminal_payments as terminal
from scheduling.cancellation import cancel
from scheduling.reservation_audit import audit_page
from scheduling.reservation_queries import ATTENTION, staff_reservations
from scheduling.tests.test_reservations import inventory, workshop_request, reserve, party_request

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["stripe", "square"])
def paid_booking(inventory, booking_member, request):
    item = reserve(booking_member, workshop_request(inventory), total=6500, deposit=2500)
    account = PaymentAccount.objects.create(school=item.profile.school, provider=request.param,
        reference="acct_test" if request.param == "stripe" else "square-test", enabled=True,
        scope="connected" if request.param == "stripe" else "platform_demo",
        location_reference="" if request.param == "stripe" else "location-test")
    item.order.account = account
    item.order.merchant_reference = account.reference
    item.order.save()
    entry = LedgerEntry.objects.create(order=item.order, school=item.profile.school, environment="test",
        key=uuid.uuid4(), kind="payment", channel=request.param, amount_cents=2500, reference="online-deposit")
    PaymentAttempt.objects.create(order=item.order, account=account, amount_cents=2500, state="paid",
        entry=entry, provider_intent="pi_test", expires_at=timezone.now(), success_url="https://example.invalid/",
        cancel_url="https://example.invalid/")
    item.state = "confirmed"
    item.save()
    return item


def data(item, **overrides):
    result = dict(key=str(uuid.uuid4()), expected_balance=totals(item.order)["outstanding_cents"],
        expected_total=totals(item.order)["total_cents"],
        amount="40.00", paid_on=str(terminal.studio_today(item)), external_reference="Receipt-123",
        note="Collected at studio", confirm="on")
    result.update(overrides)
    return result


def record(member, item, raw=None):
    return terminal.record(member.user, member.profile.school.slug, item.public_id, raw or data(item))


def void(member, item, entry, reason="Incorrect amount"):
    return terminal.void(member.user, member.profile.school.slug, item.public_id, entry.pk,
        {"reason": reason, "confirm": "on"})


def url(member, item):
    return reverse("scheduling:reservation_detail", kwargs={"school_slug": member.profile.school.slug, "public_id": item.public_id})


def test_partial_full_payment_and_correction(paid_booking, booking_member):
    item = paid_booking
    school_before = School.objects.values().get(pk=item.profile.school_id)
    record(booking_member, item, data(item, amount="10.00"))
    assert totals(item.order)["outstanding_cents"] == 3000
    raw = data(item, amount="30.00", external_reference="Receipt-456")
    entry = record(booking_member, item, raw)
    assert record(booking_member, item, raw).pk == entry.pk
    assert totals(item.order)["paid_cents"] == 6500
    reversal = void(booking_member, item, entry)
    assert void(booking_member, item, entry).pk == reversal.pk
    with pytest.raises(ValidationError, match="entry was voided"):
        record(booking_member, item, raw)
    assert totals(item.order)["paid_cents"] == 3500
    assert totals(item.order)["refunded_cents"] == 0
    record(booking_member, item, data(item, amount="30", external_reference="Receipt-456"))
    assert totals(item.order)["outstanding_cents"] == 0
    item.refresh_from_db()
    assert item.state == "confirmed"
    assert School.objects.values().get(pk=item.profile.school_id) == school_before
    assert Refund.objects.count() == 0
    assert AdminAuditLog.objects.filter(changes__action="terminal_payment_voided").count() == 1
    titles = [row["title"] for row in audit_page(item, 1)["rows"]]
    assert "Terminal entry voided (no refund)" in titles
    assert "Terminal payment recorded by staff" in titles
    assert "Refund recorded" not in titles
    # The grid and detail must agree after an entry correction.
    assert staff_reservations(item.profile).get(pk=item.pk).net_received == 6500


@pytest.mark.parametrize("changes", [
    {"amount": "0"}, {"amount": "-2"}, {"amount": "40.001"},
    {"amount": "NaN"}, {"confirm": ""}, {"external_reference": "x" * 101}, {"key": "bad"},
    {"expected_balance": 0}, {"paid_on": "2099-01-01"}, {"paid_on": "bad"},
])
def test_invalid_record_is_atomic(paid_booking, booking_member, changes):
    with pytest.raises(ValidationError):
        record(booking_member, paid_booking, data(paid_booking, **changes))
    assert totals(paid_booking.order)["paid_cents"] == 2500
    assert not LedgerEntry.objects.filter(channel="terminal").exists()


def test_duplicate_reference_and_changed_retry(paid_booking, booking_member):
    raw = data(paid_booking, amount="10")
    record(booking_member, paid_booking, raw)
    for payload in (data(paid_booking, amount="10", external_reference="receipt-123"),
                    {**raw, "amount": "11"}, data(paid_booking, amount="10", external_reference="online-deposit")):
        with pytest.raises(ValidationError):
            record(booking_member, paid_booking, payload)


def test_extra_purchase_updates_all_totals_without_overpayment(paid_booking, booking_member, client):
    from scheduling.checkout import approve_refund, payment_token
    from core.payments.checkout import amount_due
    item = paid_booking
    raw = data(item, amount="55")
    entry = record(booking_member, item, raw)
    assert entry.extra_charge_cents == 1500
    assert record(booking_member, item, raw).pk == entry.pk
    amounts = totals(item.order)
    assert (amounts["total_cents"], amounts["paid_cents"], amounts["outstanding_cents"]) == (8000, 8000, 0)
    item.order.refresh_from_db()
    assert item.order.total_cents == 6500 and item.order.deposit_cents == 2500
    assert item.order.total == 80 and amount_due(item.order) == 0
    assert not staff_reservations(item.profile).filter(ATTENTION, pk=item.pk).exists()
    assert staff_reservations(item.profile).get(pk=item.pk).effective_total == 8000
    client.force_login(booking_member.user)
    response = client.get(url(booking_member, item))
    assert not response.context["overpaid"]
    assert b"Includes $15.00 extra purchases" in response.content
    assert b"confirm_extras" not in response.content
    assert b"Refund excess payment" not in response.content
    customer = client.get(reverse("scheduling:customer_payment", args=[item.profile.school.slug, payment_token(item)]))
    assert b"$80.00" in customer.content and b"Paid in full" in customer.content
    assert any("Extra purchases: $15.00" in row["detail"] for row in audit_page(item, 1)["rows"])
    with pytest.raises(ValidationError, match="Only excess"):
        approve_refund(booking_member.user, item.profile.school.slug, item.public_id,
            item.order.attempts.get().public_id, key=uuid.uuid4(), amount_cents=1500, reason="Not an overpayment")
    void(booking_member, item, entry)
    amounts = totals(item.order)
    assert (amounts["total_cents"], amounts["paid_cents"], amounts["outstanding_cents"], amounts["extras_cents"]) == (6500, 2500, 4000, 0)
    assert amount_due(item.order) == 4000 and amounts["refunded_cents"] == 0


@pytest.mark.parametrize("state", ["confirmed", "completed"])
def test_extras_on_fully_paid_booking_and_stale_zero_balance(paid_booking, booking_member, state):
    item = paid_booking
    record(booking_member, item)
    item.state = state
    item.save()
    first = data(item, amount="12.50", external_reference="Extras-1")
    stale = data(item, amount="5", external_reference="Extras-2")
    entry = record(booking_member, item, first)
    assert entry.extra_charge_cents == 1250
    with pytest.raises(ValidationError, match="balance changed"):
        record(booking_member, item, stale)
    next_entry = record(booking_member, item, data(item, amount="5", external_reference="Extras-2"))
    assert totals(item.order)["total_cents"] == 8250
    void(booking_member, item, entry)
    assert totals(item.order)["total_cents"] == 7000
    assert totals(item.order)["outstanding_cents"] == 0
    void(booking_member, item, next_entry)
    assert totals(item.order)["total_cents"] == 6500
    item.refresh_from_db()
    assert item.state == state


def test_extras_limits_and_tampering(paid_booking, booking_member):
    item = paid_booking
    with pytest.raises(ValidationError, match="balance changed"):
        record(booking_member, item, data(item, amount="50", expected_balance=0))
    with pytest.raises(ValidationError, match="supported amount"):
        record(booking_member, item, data(item, amount="999999.99"))
    assert totals(item.order)["total_cents"] == 6500
    raw = data(item, amount="50")
    record(booking_member, item, raw)
    with pytest.raises(ValidationError):
        record(booking_member, item, {**raw, "expected_balance": 5000})


def test_extras_do_not_hide_real_overpayment(paid_booking, booking_member):
    record(booking_member, paid_booking, data(paid_booking, amount="55"))
    LedgerEntry.objects.create(order=paid_booking.order, school=paid_booking.order.school, environment="test",
        key=uuid.uuid4(), kind="payment", channel="stripe", amount_cents=100, reference="unexpected-payment")
    assert staff_reservations(paid_booking.profile).filter(ATTENTION, pk=paid_booking.pk).exists()


def test_optional_receipt_and_removed_note(paid_booking, booking_member, client):
    client.force_login(booking_member.user)
    address = url(booking_member, paid_booking)
    page = client.get(address)
    assert b'Square receipt number (optional)' in page.content
    assert b'Record payment already received' in page.content
    assert b'Take online payment' in page.content
    assert b'Collect balance:' not in page.content
    assert b'<details' not in page.content
    assert b'aria-labelledby="terminal-payment-title"' in page.content
    assert b'name="note"' not in page.content
    assert b'Payment &amp; refund status' not in page.content
    assert b'Check payment status' not in page.content
    raw = data(paid_booking, amount="10", external_reference=" ", note="No longer accepted")
    entry = record(booking_member, paid_booking, raw)
    assert entry.external_reference == "" and entry.note == ""
    assert record(booking_member, paid_booking, raw).pk == entry.pk
    raw = data(paid_booking, amount="10")
    del raw["external_reference"]
    assert client.post(address, {**raw, "action": "record_terminal"}).status_code == 302
    assert totals(paid_booking.order)["paid_cents"] == 4500
    assert b'Not added' in client.get(address).content
    void(booking_member, paid_booking, entry)
    assert totals(paid_booking.order)["paid_cents"] == 3500
    assert totals(paid_booking.order)["refunded_cents"] == 0


def test_only_unresolved_payment_controls_remain(paid_booking, booking_member, client):
    client.force_login(booking_member.user)
    address = url(booking_member, paid_booking)
    attempt = PaymentAttempt.objects.create(order=paid_booking.order, account=paid_booking.order.account,
        amount_cents=4000, state="unknown", expires_at=timezone.now(),
        success_url="https://example.invalid/", cancel_url="https://example.invalid/")
    page = client.get(address)
    assert list(page.context["attempts"]) == [attempt]
    assert b'Check payment status' in page.content and b'Close checkout' in page.content
    assert b'Payment &amp; refund status' not in page.content
    attempt.state = "expired"
    attempt.save()
    refund = Refund.objects.create(attempt=paid_booking.order.attempts.get(state="paid"), key=uuid.uuid4(),
        amount_cents=100, reason="Return deposit", actor=booking_member.user)
    assert b'Check refund status' in client.get(address).content
    refund.state = "failed"
    refund.save()
    page = client.get(address)
    assert b'Check refund status' not in page.content
    assert b'Check payment status' not in page.content


@pytest.mark.parametrize("state", ["held", "review", "canceled"])
def test_unconfirmed_or_canceled_payment_denied(paid_booking, booking_member, state):
    paid_booking.state = state
    paid_booking.save()
    with pytest.raises(ValidationError, match="confirmed or completed"):
        record(booking_member, paid_booking)


@pytest.mark.parametrize("state", ["pending", "open", "unknown"])
def test_active_checkout_blocks_record_and_correction(paid_booking, booking_member, state):
    entry = record(booking_member, paid_booking, data(paid_booking, amount="10"))
    PaymentAttempt.objects.create(order=paid_booking.order, account=paid_booking.order.account,
        amount_cents=3000, state=state, expires_at=timezone.now() - timedelta(days=1),
        success_url="https://example.invalid/", cancel_url="https://example.invalid/")
    with pytest.raises(ValidationError, match="checkout"):
        record(booking_member, paid_booking, data(paid_booking, amount="30", external_reference="another"))
    with pytest.raises(ValidationError, match="checkout"):
        void(booking_member, paid_booking, entry)


def test_pending_refund_blocks_record(paid_booking, booking_member):
    Refund.objects.create(attempt=paid_booking.order.attempts.get(), key=uuid.uuid4(), amount_cents=100,
        reason="pending", actor=booking_member.user)
    with pytest.raises(ValidationError, match="refund"):
        record(booking_member, paid_booking)


def test_correction_permissions_reason_and_source(paid_booking, booking_member):
    entry = record(booking_member, paid_booking)
    with pytest.raises(ValidationError):
        terminal.void(booking_member.user, booking_member.profile.school.slug, paid_booking.public_id, entry.pk, {})
    with pytest.raises(Http404):
        void(booking_member, paid_booking, paid_booking.order.entries.get(channel=paid_booking.order.account.provider))
    void(booking_member, paid_booking, entry)
    with pytest.raises(ValidationError, match="already been voided"):
        void(booking_member, paid_booking, entry, "Changed reason")


@pytest.mark.parametrize("role", ["scheduler", "viewer"])
def test_non_financial_staff_denied(paid_booking, booking_member, client, role):
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    response = client.get(url(booking_member, paid_booking))
    assert response.status_code == 200
    assert b"Record payment already received" not in response.content
    assert client.post(url(booking_member, paid_booking), {**data(paid_booking), "action": "record_terminal"}).status_code == 404
    with pytest.raises(Http404):
        record(booking_member, paid_booking)


def test_cross_school_and_anonymous_denied(paid_booking, booking_member, client):
    other = School.objects.create(slug="other-studio")
    with pytest.raises(Http404):
        terminal.record(booking_member.user, other.slug, paid_booking.public_id, data(paid_booking))
    assert client.post(url(booking_member, paid_booking), data(paid_booking)).status_code == 302
    booking_member.is_active = False
    booking_member.save()
    client.force_login(booking_member.user)
    assert client.get(url(booking_member, paid_booking)).status_code == 404


def test_csrf_and_environment_boundaries(paid_booking, booking_member, client, settings):
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(booking_member.user)
    payload = {**data(paid_booking), "action": "record_terminal"}
    address = url(booking_member, paid_booking)
    assert csrf_client.post(address, payload).status_code == 403
    settings.APP_BASE_URL = "https://app.example.com"
    settings.DEMO_BASE_URL = "https://demo.example.com"
    settings.ALLOWED_HOSTS = ["app.example.com", "demo.example.com", "testserver"]
    school = paid_booking.profile.school
    school.is_demo = True
    school.save()
    client.force_login(booking_member.user)
    assert client.post(address, payload, HTTP_HOST="app.example.com").status_code == 403
    assert client.post(address, payload, HTTP_HOST="demo.example.com").status_code == 302
    entry = paid_booking.order.entries.get(channel="terminal")
    assert entry.environment == "test" and entry.school_id == school.pk
    assert AdminAuditLog.objects.get(changes__action="terminal_payment_recorded").environment == "demo"


def test_staff_ui_correction_and_no_automatic_refund(paid_booking, booking_member, client):
    client.force_login(booking_member.user)
    address = url(booking_member, paid_booking)
    page = client.get(address)
    assert b"Record payment already received" in page.content
    assert client.post(address, {**data(paid_booking), "action": "record_terminal"}).status_code == 302
    page = client.get(address)
    assert b"Staff-recorded" in page.content and b"Receipt-123" in page.content
    assert b"Record payment already received" in page.content
    assert b'data-payment-dialog="correct-payment-' in page.content
    with pytest.raises(ValidationError, match="outside Pontora"):
        cancel(booking_member.user, booking_member.profile.school.slug, paid_booking.public_id,
            key=uuid.uuid4(), resolution="with_refund", reason="Cancel")
    entry = paid_booking.order.entries.get(channel="terminal")
    response = client.post(address, {"action": "void_terminal", "entry_id": entry.pk, "reason": "Wrong amount", "confirm": "on"})
    assert response.status_code == 302
    assert b"Voided" in client.get(address).content
    assert totals(paid_booking.order)["refunded_cents"] == 0
    invalid = client.post(address, {**data(paid_booking, amount="0"), "action": "record_terminal"})
    assert invalid.status_code == 400
    assert b'data-payment-error' in invalid.content
    assert client.post(address, {"action": "void_terminal", "entry_id": "bad"}).status_code == 400
    assert client.post(address, {"action": "other"}).status_code == 400


def test_completed_and_canceled_correction(paid_booking, booking_member):
    paid_booking.state = "completed"
    paid_booking.save()
    entry = record(booking_member, paid_booking)
    paid_booking.state = "canceled"
    paid_booking.save()
    void(booking_member, paid_booking, entry)
    paid_booking.refresh_from_db()
    assert paid_booking.state == "canceled"


@pytest.mark.parametrize("extra", [0, 50])
def test_party_balance(inventory, booking_member, extra):
    source = party_request(inventory)
    item = reserve(booking_member, source, total=40000, deposit=10000)
    item.state = "confirmed"
    item.save()
    entry = record(booking_member, item, data(item, amount=str(400 + extra)))
    assert totals(item.order)["outstanding_cents"] == 0
    assert totals(item.order)["total_cents"] == 40000 + extra * 100
    assert entry.extra_charge_cents == extra * 100
    void(booking_member, item, entry)
    assert totals(item.order)["total_cents"] == 40000
    assert totals(item.order)["outstanding_cents"] == 40000


def test_void_cannot_erase_money_already_refunded(paid_booking, booking_member):
    entry = record(booking_member, paid_booking)
    LedgerEntry.objects.create(order=paid_booking.order, school=paid_booking.profile.school,
        environment="test", key=uuid.uuid4(), kind="refund", channel=paid_booking.order.account.provider,
        amount_cents=3000, reference="refund-already-recorded")
    with pytest.raises(ValidationError, match="conflicts with recorded refunds"):
        void(booking_member, paid_booking, entry)
    assert not LedgerEntry.objects.filter(kind="void").exists()


def test_local_reset_preserves_terminal_receipts(inventory, booking_member):
    from scheduling.experience_demo.reset import reset_demo
    item = reserve(booking_member, party_request(inventory))
    item.state = "confirmed"
    item.save()
    entry = record(booking_member, item, data(item, amount="10"))
    void(booking_member, item, entry)
    school = item.profile.school
    school.slug = "plushumi-demo"
    school.is_demo = True
    school.feature_flags = {"booking_experience_demo": True}
    school.save()
    with patch("scheduling.experience_demo.reset.local_database", return_value=True):
        with pytest.raises(CommandError, match="Terminal receipts"):
            reset_demo(item.profile)
    assert item.order.entries.count() == 2


def test_customer_page_and_cross_reservation_correction(paid_booking, inventory, booking_member, client):
    from scheduling.checkout import payment_token
    entry = record(booking_member, paid_booking)
    address = reverse("scheduling:customer_payment", kwargs={"school_slug": booking_member.profile.school.slug,
        "token": payment_token(paid_booking)})
    response = client.get(address)
    assert response.status_code == 200
    assert b"Paid in full" in response.content
    assert b"Pay remaining balance" not in response.content
    other = reserve(booking_member, party_request(inventory))
    with pytest.raises(Http404):
        void(booking_member, other, entry)
    void(booking_member, paid_booking, entry)
    response = client.get(address)
    assert b"Paid in full" not in response.content
    assert response.context["amounts"]["outstanding_cents"] == 4000
    assert b"$40.00" in response.content


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("extras", [False, True])
def test_competing_receipts_cannot_use_stale_totals(paid_booking, booking_member, extras):
    if extras:
        record(booking_member, paid_booking)
    barrier = Barrier(2)
    payloads = [data(paid_booking, external_reference=f"Terminal-{i}", amount="10" if extras else "40") for i in range(2)]
    def submit(raw):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                record(booking_member, paid_booking, raw)
                return "recorded"
            except ValidationError:
                return "rejected"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, payloads)) == ["recorded", "rejected"]
    assert totals(paid_booking.order)["paid_cents"] == (7500 if extras else 6500)


@pytest.mark.django_db(transaction=True)
def test_online_checkout_and_terminal_payment_serialize(paid_booking, booking_member):
    from scheduling.checkout import prepare_booking_checkout
    barrier = Barrier(2)
    raw = data(paid_booking)
    def submit(online):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                if online:
                    prepare_booking_checkout(paid_booking, "http://localhost:8002/payment-return/")
                else:
                    record(booking_member, paid_booking, raw)
                return "recorded"
            except ValidationError:
                return "rejected"
        finally:
            close_old_connections()
    # Provider readiness is the only mocked boundary; both paths use real locks.
    with patch("core.payments.providers.ready", return_value=True):
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(submit, [True, False])) == ["recorded", "rejected"]
    amounts = totals(paid_booking.order)
    active = paid_booking.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists()
    assert (active, amounts["outstanding_cents"]) in {(True, 4000), (False, 0)}


@pytest.mark.django_db(transaction=True)
def test_concurrent_submits_only_record_once(paid_booking, booking_member):
    barrier = Barrier(2)
    raw = data(paid_booking)
    def submit():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return record(booking_member, paid_booking, raw).pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        assert futures[0].result() == futures[1].result()
    assert totals(paid_booking.order)["paid_cents"] == 6500
