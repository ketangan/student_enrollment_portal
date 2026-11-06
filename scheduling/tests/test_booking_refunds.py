import uuid
from datetime import timedelta
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, IntegrityError, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from decimal import Decimal

from core.payments import refunds, square
from core.payments.models import Refund
from core.payments.service import totals
from scheduling import booking_documents, terminal_checkout
from scheduling.checkout import approve_booking_refund, prepare_booking_checkout, refundable_payments
from scheduling.reservation_queries import staff_reservations
from scheduling.reservation_audit import group_refund_history
from .test_terminal_collection import item, terminal, sandbox_terminal, square_account, inventory, custom, transport

pytestmark = pytest.mark.django_db


def pay(item, terminal, member, monkeypatch, amount="100.00"):
    attempt = custom(member, item, terminal, amount, balance=totals(item.order)["outstanding_cents"])
    transport(attempt, monkeypatch)
    terminal_checkout.submit(attempt)
    attempt.refresh_from_db()
    return attempt


def approve(item, attempt, member, amount=2000, **overrides):
    values = totals(item.order)
    args = dict(key=uuid.uuid4(), amount_cents=amount, description="Participant did not attend",
        expected_total=values["total_cents"], expected_net=values["net_cents"], expected_state=item.state)
    args.update(overrides)
    return approve_booking_refund(member.user, item.profile.school.slug, item.public_id, **args)[0]


def result(refund, status="COMPLETED"):
    return square.refund_snapshot(refund, {"id": "refund_" + refund.public_id.hex,
        "payment_id": refund.attempt.provider_intent, "location_id": "LTEST", "reason": square.refund_reason(refund),
        "amount_money": {"amount": refund.amount_cents, "currency": "USD"}, "status": status})


@pytest.mark.parametrize("state", ["confirmed", "completed", "canceled"])
@pytest.mark.parametrize("amount", [1, 2000, 10000])
def test_partial_full_refunds_preserve_quote_capacity_and_evidence(item, terminal, booking_member, monkeypatch, client, state, amount):
    quantity = item.quantity
    attempt = pay(item, terminal, booking_member, monkeypatch)
    item.state = state
    item.save(update_fields=["state"])
    refund = approve(item, attempt, booking_member, amount)
    reduction = 0 if state == "canceled" else amount
    assert refund.reduction_cents == reduction
    assert totals(item.order)["total_cents"] == 10000
    refunds.apply_snapshot(refund, result(refund, "PENDING"))
    assert totals(item.order)["refunded_cents"] == 0
    refunds.apply_snapshot(refund, result(refund))
    refunds.apply_snapshot(refund, result(refund))
    item.refresh_from_db()
    attempt.refresh_from_db()
    assert item.state == state and item.quantity == quantity
    assert item.order.total_cents == 10000 and attempt.entry.amount_cents == 10000
    values = totals(item.order)
    assert values["refunded_cents"] == amount
    assert values["total_cents"] == 10000 - reduction
    assert values["outstanding_cents"] == (amount if state == "canceled" else 0)
    assert item.order.entries.filter(kind="refund").count() == 1
    assert staff_reservations(item.profile).get(pk=item.pk).effective_total == values["total_cents"]
    receipt = booking_documents.snapshot(item, "receipt")
    if reduction:
        assert {"label": "Refund price adjustment", "amount": f"-${amount / 100:,.2f}"} in receipt["lines"]
    if reduction == 10000:
        client.force_login(booking_member.user)
        assert b">Refunded</span>" in client.get(url(item, "reservation_detail")).content


@pytest.mark.parametrize("status", ["FAILED", "REJECTED"])
def test_unsuccessful_refund_never_reduces_price(item, terminal, booking_member, monkeypatch, status):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    refund = approve(item, attempt, booking_member)
    refunds.apply_snapshot(refund, result(refund, status))
    assert totals(item.order)["total_cents"] == totals(item.order)["paid_cents"] == 10000
    assert totals(item.order)["refund_reduction_cents"] == totals(item.order)["refunded_cents"] == 0
    assert approve(item, attempt, booking_member).pk != refund.pk


def test_overpayment_is_returned_before_reducing_price(item, terminal, booking_member, monkeypatch):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    # Historical overcollection, not an additional purchase.
    attempt.entry.amount_cents = 12000
    attempt.entry.save(update_fields=["amount_cents"])
    attempt.amount_cents = 12000
    attempt.save(update_fields=["amount_cents"])
    first = approve(item, attempt, booking_member, 1500)
    assert first.reduction_cents == 0
    refunds.apply_snapshot(first, result(first))
    second = approve(item, attempt, booking_member, 1000)
    assert second.reduction_cents == 500
    refunds.apply_snapshot(second, result(second))
    assert totals(item.order)["total_cents"] == totals(item.order)["net_cents"] == 9500


def test_deposit_refund_keeps_only_original_unpaid_balance(item, terminal, booking_member, monkeypatch):
    attempt = pay(item, terminal, booking_member, monkeypatch, "25")
    refund = approve(item, attempt, booking_member, 1000)
    refunds.apply_snapshot(refund, result(refund))
    assert totals(item.order)["outstanding_cents"] == 7500
    assert totals(item.order)["total_cents"] == 9000


def test_refund_retry_staleness_and_pending_guards(item, terminal, booking_member, monkeypatch):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    with pytest.raises(ValidationError, match="changed"):
        approve(item, attempt, booking_member, expected_total=9999)
    with pytest.raises(ValidationError, match="changed"):
        approve(item, attempt, booking_member, expected_state="canceled")
    key = uuid.uuid4()
    refund = approve(item, attempt, booking_member, key=key)
    assert approve(item, attempt, booking_member, key=key).pk == refund.pk
    with pytest.raises(ValidationError, match="different"):
        approve(item, attempt, booking_member, key=key, description="Other")
    with pytest.raises(ValidationError, match="already in progress"):
        approve(item, attempt, booking_member)
    with pytest.raises(ValidationError, match="refund"):
        prepare_booking_checkout(item, "https://example.invalid/return")
    with pytest.raises(ValidationError, match="refund"):
        custom(booking_member, item, terminal, "1", balance=0)
    refunds.apply_snapshot(refund, result(refund))
    assert approve(item, attempt, booking_member, key=key, expected_total=10000, expected_net=10000).pk == refund.pk
    with pytest.raises(ValidationError, match="or less"):
        approve(item, attempt, booking_member, 9000)


@pytest.mark.parametrize("amount", [0, -1, True, 10001])
def test_invalid_amount_creates_no_refund(item, terminal, booking_member, monkeypatch, amount):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    with pytest.raises(ValidationError):
        approve(item, attempt, booking_member, amount)
    assert not Refund.objects.exists()


def test_database_reduction_guard(item, terminal, booking_member, monkeypatch):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    refund = approve(item, attempt, booking_member)
    with pytest.raises(IntegrityError), transaction.atomic():
        Refund.objects.filter(pk=refund.pk).update(reduction_cents=refund.amount_cents + 1)


def test_refund_permissions_and_csrf(item, terminal, booking_member, monkeypatch, client):
    pay(item, terminal, booking_member, monkeypatch)
    assert client.get(url(item)).status_code == 302
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(booking_member.user)
    assert csrf.post(url(item, "reservation_detail"), {"action": "refund_payment"}).status_code == 403
    client.force_login(booking_member.user)
    assert client.get(url(item).replace(item.profile.school.slug, "another-school")).status_code == 404
    booking_member.role = "viewer"
    booking_member.save(update_fields=["role"])
    assert client.get(url(item)).status_code == 404
    assert client.post(url(item), {"action": "confirm"}).status_code == 404
    assert client.post(url(item, "reservation_detail"), {"action": "refund_payment"}).status_code == 404


def test_price_adjustment_cannot_change_stripe_behavior(item, terminal, booking_member, monkeypatch):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    account = attempt.account
    account.provider = "stripe"
    account.location_reference = ""
    account.save(update_fields=["provider", "location_reference"])
    with pytest.raises(ValidationError, match="not available"):
        approve(item, attempt, booking_member)
    assert not Refund.objects.exists()


def url(item, name="refund_create", **kwargs):
    return reverse("scheduling:" + name, kwargs={"school_slug": item.profile.school.slug, "public_id": item.public_id, **kwargs})


def form_data(item, **changes):
    values = totals(item.order)
    data = dict(key=str(uuid.uuid4()), amount="20.00", description="", expected_total=values["total_cents"],
        expected_net=values["net_cents"], expected_state=item.state)
    data.update(changes)
    return {"action": "refund_payment", **{"refund-" + key: value for key, value in data.items()}}


def refund_action(response):
    class Actions(HTMLParser):
        in_header = False

        def __init__(self):
            super().__init__()
            self.refund_buttons = []

        def handle_starttag(self, tag, attrs):
            if tag == "header":
                self.in_header = True
            attrs = dict(attrs)
            if tag == "button" and attrs.get("data-payment-dialog") == "booking-refund-dialog":
                self.refund_buttons.append((self.in_header, attrs))

        def handle_endtag(self, tag):
            if tag == "header":
                self.in_header = False

    actions = Actions()
    actions.feed(response.content.decode())
    assert len(actions.refund_buttons) == 1
    in_header, attrs = actions.refund_buttons[0]
    assert in_header, "Refund must stay alongside Statement and Receipt in the payment header"
    return attrs


def test_ui_single_dialog_and_automatic_status(item, terminal, booking_member, monkeypatch, client):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    client.force_login(booking_member.user)
    assert client.get(url(item) + "?payment=" + str(attempt.public_id)).status_code == 302
    page = client.get(url(item, "reservation_detail"))
    assert page.status_code == 200
    assert b'data-payment-dialog="booking-refund-dialog"' in page.content
    assert "disabled" not in refund_action(page)
    assert b'payment-refund-action' not in page.content and b'Review refund' not in page.content
    assert list(page.context["refund_form"].visible_fields())[0].name == "amount"
    assert [field.name for field in page.context["refund_form"].visible_fields()] == ["amount", "description"]
    provider = Mock(side_effect=lambda r: result(r, "PENDING"))
    monkeypatch.setattr(square, "create_order_refund", provider)
    data = form_data(item)
    assert not provider.called and not Refund.objects.exists()
    assert client.post(url(item, "reservation_detail"), data).status_code == 302
    refund = Refund.objects.get()
    assert provider.call_count == 1
    assert client.post(url(item, "reservation_detail"), data).status_code == 302
    assert provider.call_count == 1 and Refund.objects.count() == 1
    assert refund.reason == "Customer refund"
    detail = client.get(url(item, "reservation_detail"))
    assert b"data-refund-poll" in detail.content
    assert "disabled" in refund_action(detail)
    assert refund_action(detail)["aria-describedby"] == "booking-refund-pending"
    assert b'Refund completed' not in detail.content
    assert b'data-payment-dialog="terminal-collection-dialog"' not in detail.content
    assert b'data-payment-dialog="terminal-payment-dialog"' not in detail.content
    Refund.objects.filter(pk=refund.pk).update(checked_at=None)
    monkeypatch.setattr(square, "retrieve_order_refund", lambda r: result(r))
    response = client.post(url(item, "refund_recover", refund_id=refund.public_id), {"action": "check"}, HTTP_ACCEPT="application/json")
    assert response.json()["state"] == "succeeded"
    detail = client.get(url(item, "reservation_detail"))
    assert b"Refund price adjustment" in detail.content and b"$80.00" in detail.content
    assert b"data-refund-poll" not in detail.content
    assert "disabled" not in refund_action(detail)
    assert b'Square &middot; Refund completed' in detail.content
    assert totals(item.order)["outstanding_cents"] == 0


@pytest.mark.parametrize("outcome", ["COMPLETED", "FAILED"])
def test_full_refund_action_stays_visible_after_pending_and_settlement(item, terminal, booking_member, monkeypatch, client, outcome):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    refund = approve(item, attempt, booking_member, 10000)
    refunds.apply_snapshot(refund, result(refund, "PENDING"))
    client.force_login(booking_member.user)
    page = client.get(url(item, "reservation_detail"))
    assert "disabled" in refund_action(page)
    assert b'Refund completed' not in page.content
    refunds.apply_snapshot(refund, result(refund, outcome))
    page = client.get(url(item, "reservation_detail"))
    assert ("disabled" in refund_action(page)) == (outcome == "COMPLETED")
    assert (b'Refund completed' in page.content) == (outcome == "COMPLETED")
    if outcome == "COMPLETED":
        assert refund_action(page)["title"] == "No refundable payment remaining"


@pytest.mark.parametrize("changes", [{"amount": "100.01"}, {"amount": "0"}, {"amount": "1.001"},
    {"amount": ""}, {"key": "bad"}, {"description": "x" * 241},
    {"expected_total": "9999"}, {"expected_state": "canceled"}])
def test_ui_invalid_refund_never_calls_square(item, terminal, booking_member, monkeypatch, client, changes):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    client.force_login(booking_member.user)
    provider = Mock()
    monkeypatch.setattr(square, "create_order_refund", provider)
    response = client.post(url(item, "reservation_detail"), form_data(item, **changes))
    assert response.status_code == 400
    assert b'data-payment-error' in response.content
    assert response.context["refund_form"].is_bound
    assert not provider.called and not Refund.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_simultaneous_refunds_only_approve_once(item, terminal, booking_member, monkeypatch):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    barrier = Barrier(2)
    def execute():
        close_old_connections()
        barrier.wait(timeout=10)
        try:
            return approve(item, attempt, booking_member).pk
        except ValidationError:
            return None
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(), range(2)))
    assert sum(value is not None for value in results) == 1
    assert Refund.objects.count() == 1


@pytest.mark.parametrize("amount,parts", [(1000, [1000]), (7500, [6000, 1500]), (10000, [6000, 4000])])
def test_booking_refund_allocates_latest_payments_and_retries_once(item, terminal, booking_member, monkeypatch, amount, parts):
    first = pay(item, terminal, booking_member, monkeypatch, "40")
    with CaptureQueriesContext(connection) as single_queries:
        refundable_payments(item.order)
    second = pay(item, terminal, booking_member, monkeypatch, "60")
    with CaptureQueriesContext(connection) as multiple_queries:
        refundable_payments(item.order)
    assert len(single_queries) == len(multiple_queries) == 2
    key = uuid.uuid4()
    args = dict(key=key, amount_cents=amount, description="", expected_total=10000, expected_net=10000, expected_state=item.state)
    approved = approve_booking_refund(booking_member.user, item.profile.school.slug, item.public_id, **args)
    assert [refund.amount_cents for refund in approved] == parts
    assert approved[0].attempt_id == second.pk
    if len(parts) == 2:
        assert approved[1].attempt_id == first.pk
    assert totals(item.order)["refunded_cents"] == 0
    for refund in approved:
        refunds.apply_snapshot(refund, result(refund))
    again = approve_booking_refund(booking_member.user, item.profile.school.slug, item.public_id, **args)
    assert {r.pk for r in again} == {r.pk for r in approved}
    assert Refund.objects.count() == len(parts)
    assert totals(item.order)["total_cents"] == totals(item.order)["net_cents"] == 10000 - amount
    with pytest.raises(ValidationError, match="different"):
        approve_booking_refund(booking_member.user, item.profile.school.slug, item.public_id, **{**args, "amount_cents": amount - 1})


def test_batch_failure_preserves_success_and_remaining_refundable_money(item, terminal, booking_member, monkeypatch):
    pay(item, terminal, booking_member, monkeypatch, "40")
    attempt = pay(item, terminal, booking_member, monkeypatch, "60")
    approved = approve_booking_refund(booking_member.user, item.profile.school.slug, item.public_id,
        key=uuid.uuid4(), amount_cents=7500, expected_total=10000, expected_net=10000, expected_state=item.state)
    refunds.apply_snapshot(approved[0], result(approved[0]))
    refunds.apply_snapshot(approved[1], result(approved[1], "FAILED"))
    assert totals(item.order)["total_cents"] == totals(item.order)["net_cents"] == 4000
    retry = approve(item, attempt, booking_member, 1500)
    refunds.apply_snapshot(retry, result(retry))
    assert totals(item.order)["total_cents"] == totals(item.order)["net_cents"] == 2500


def test_batch_approval_rolls_back_all_intents_on_error(item, terminal, booking_member, monkeypatch):
    pay(item, terminal, booking_member, monkeypatch, "40")
    pay(item, terminal, booking_member, monkeypatch, "60")
    original = refunds.approve
    count = 0
    def failing(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise ValidationError("Payment account changed")
        return original(*args, **kwargs)
    monkeypatch.setattr(refunds, "approve", failing)
    with pytest.raises(ValidationError, match="account changed"):
        approve_booking_refund(booking_member.user, item.profile.school.slug, item.public_id,
            key=uuid.uuid4(), amount_cents=7500, expected_total=10000, expected_net=10000, expected_state=item.state)
    assert not Refund.objects.exists()


def test_ui_batch_submission_uses_original_payments_and_escapes_description(item, terminal, booking_member, monkeypatch, client):
    pay(item, terminal, booking_member, monkeypatch, "40")
    pay(item, terminal, booking_member, monkeypatch, "60")
    client.force_login(booking_member.user)
    provider = Mock(side_effect=lambda r: result(r))
    monkeypatch.setattr(square, "create_order_refund", provider)
    data = form_data(item, amount="75", description="<script>alert(1)</script>")
    response = client.post(url(item, "reservation_detail"), data)
    assert response.status_code == 302 and response.url.endswith("#money-title")
    assert provider.call_count == 2
    assert client.post(url(item, "reservation_detail"), data).status_code == 302
    assert provider.call_count == 2
    detail = client.get(url(item, "reservation_detail"))
    assert b"<script>alert(1)</script>" not in detail.content
    assert totals(item.order)["outstanding_cents"] == 0


def test_split_refund_has_one_pending_summary_and_no_manual_status_blocks(item, terminal, booking_member, monkeypatch, client):
    for amount in ("11.12", "13.45", "8.43"):
        pay(item, terminal, booking_member, monkeypatch, amount)
    client.force_login(booking_member.user)
    monkeypatch.setattr(square, "create_order_refund", lambda r: result(r, "PENDING"))
    assert client.post(url(item, "reservation_detail"), form_data(item, amount="33")).status_code == 302
    detail = client.get(url(item, "reservation_detail"))
    assert detail.content.count(b'data-refund-summary') == 1
    assert b'Refund pending &middot; $33.00' in detail.content
    assert detail.content.count(b'data-refund-poll') == 3
    assert detail.content.count(b'data-refund-check') == 1
    assert b'value="check_all"' in detail.content
    assert b'<h3>Refund processing' not in detail.content
    assert b'<p>Customer refund</p>' not in detail.content
    assert b'Check refund status' not in detail.content
    assert b'data-refund-feedback hidden' in detail.content
    approved = list(Refund.objects.order_by("created_at"))
    refunds.apply_snapshot(approved[0], result(approved[0]))
    detail = client.get(url(item, "reservation_detail"))
    assert b'Refund pending &middot; $24.57' in detail.content
    assert detail.content.count(b'data-refund-poll') == 2
    rows = detail.context["payment_history"]
    grouped = [row for row in rows if isinstance(row, dict)]
    assert len(grouped) == 1 and grouped[0]["amount"] == Decimal("8.43")
    assert grouped[0]["refund_label"] == "Refund processing"
    assert grouped[0]["refund_detail"] == "$8.43 of $33.00 returned"
    for refund in approved[1:]:
        refunds.apply_snapshot(refund, result(refund))
    detail = client.get(url(item, "reservation_detail"))
    assert b'data-refund-summary' not in detail.content
    assert b'data-refund-poll' not in detail.content
    assert totals(item.order)["refunded_cents"] == 3300
    grouped = [row for row in detail.context["payment_history"] if isinstance(row, dict)]
    assert len(grouped) == 1 and grouped[0]["amount"] == Decimal("33.00")
    assert grouped[0]["refund_label"] == "Refund completed"
    assert not grouped[0]["refund_detail"]
    assert item.order.entries.filter(kind="refund").count() == 3


def test_grouped_refund_failure_and_separate_requests_keep_accurate_history(item, terminal, booking_member, monkeypatch, client):
    pay(item, terminal, booking_member, monkeypatch, "40")
    pay(item, terminal, booking_member, monkeypatch, "60")
    approved = approve_booking_refund(booking_member.user, item.profile.school.slug, item.public_id,
        key=uuid.uuid4(), amount_cents=7500, expected_total=10000, expected_net=10000, expected_state=item.state)
    refunds.apply_snapshot(approved[0], result(approved[0]))
    refunds.apply_snapshot(approved[1], result(approved[1], "FAILED"))
    client.force_login(booking_member.user)
    page = client.get(url(item, "reservation_detail"))
    row = next(row for row in page.context["payment_history"] if isinstance(row, dict))
    assert row["refund_label"] == "Refund partially completed"
    assert row["amount"] == Decimal("60") and row["refund_detail"] == "$60.00 of $75.00 returned"
    for _ in range(2):
        refund = approve(item, None, booking_member, 100)
        refunds.apply_snapshot(refund, result(refund))
    page = client.get(url(item, "reservation_detail"))
    groups = [row for row in page.context["payment_history"] if isinstance(row, dict)]
    assert [row["amount"] for row in groups] == [Decimal("1"), Decimal("1"), Decimal("60")]
    assert item.order.entries.filter(kind="refund").count() == 3
    with CaptureQueriesContext(connection) as queries:
        group_refund_history(item, list(item.order.entries.select_related("paymentattempt", "reversal")), list(Refund.objects.filter(attempt__order=item.order)))
    assert len(queries) == 3  # Entries, refund intents and scoped batch audits; never one query per row.


def test_grouping_uses_scoped_request_identity_not_dates_or_amounts(item, terminal, booking_member, monkeypatch):
    from core.models import AdminAuditLog
    attempt = pay(item, terminal, booking_member, monkeypatch)
    refund = approve(item, attempt, booking_member, 100)
    refunds.apply_snapshot(refund, result(refund))
    entries = list(item.order.entries.filter(kind="refund"))
    records = list(Refund.objects.filter(attempt__order=item.order))
    audit = AdminAuditLog.objects.get(extra__refunds__contains=[str(refund.public_id)])
    original = dict(audit.extra)
    for changes in ({"scheduling_profile_id": -1}, {"refunds": [str(uuid.uuid4())]}, {"amount_cents": 999}, {"refunds": []}):
        audit.extra = {**original, **changes}
        audit.save(update_fields=["extra"])
        assert group_refund_history(item, entries, records) == entries


def test_refresh_status_checks_all_parts_without_resubmitting_or_crossing_orders(item, terminal, booking_member, monkeypatch, client):
    pay(item, terminal, booking_member, monkeypatch, "40")
    pay(item, terminal, booking_member, monkeypatch, "60")
    client.force_login(booking_member.user)
    monkeypatch.setattr(square, "create_order_refund", lambda r: result(r, "PENDING"))
    client.post(url(item, "reservation_detail"), form_data(item, amount="75"))
    approved = list(Refund.objects.filter(attempt__order=item.order))
    Refund.objects.filter(pk__in=[r.pk for r in approved]).update(checked_at=None)
    retrieve = Mock(side_effect=lambda r: result(r))
    create = Mock(side_effect=AssertionError("Refresh must not submit a new refund"))
    monkeypatch.setattr(square, "retrieve_order_refund", retrieve)
    monkeypatch.setattr(square, "create_order_refund", create)
    endpoint = url(item, "refund_recover", refund_id=approved[0].public_id)
    assert client.post(endpoint, {"action": "check_all"}, HTTP_ACCEPT="application/json").status_code == 200
    assert retrieve.call_count == 2 and not create.called
    assert client.post(endpoint, {"action": "check_all"}, HTTP_ACCEPT="application/json").status_code == 200
    assert retrieve.call_count == 2 and totals(item.order)["refunded_cents"] == 7500
    assert client.post(url(item, "refund_recover", refund_id=uuid.uuid4()), {"action": "check_all"}).status_code == 404


@pytest.mark.parametrize("age,checked,leased,expected", [(0, 4, False, 0), (0, 6, False, 1),
    (180, 6, False, 0), (180, 31, False, 1), (0, 6, True, 0)])
def test_interactive_refund_cadence_preserves_leases_and_slows_old_requests(
        item, terminal, booking_member, monkeypatch, client, age, checked, leased, expected):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    refund = approve(item, attempt, booking_member)
    now = timezone.now()
    refunds.apply_snapshot(refund, result(refund, "PENDING"))
    Refund.objects.filter(pk=refund.pk).update(submitted_at=now - timedelta(seconds=age),
        checked_at=now - timedelta(seconds=checked), lease_until=now + timedelta(minutes=1) if leased else None)
    retrieve = Mock(side_effect=lambda r: result(r))
    monkeypatch.setattr(square, "retrieve_order_refund", retrieve)
    client.force_login(booking_member.user)
    endpoint = url(item, "refund_recover", refund_id=refund.public_id)
    assert client.post(endpoint, {"action": "check_all", "automatic": "yes"}).status_code == 302
    assert retrieve.call_count == expected
    assert totals(item.order)["refunded_cents"] == (2000 if expected else 0)


def test_batch_poll_preserves_backoff_and_reports_webhook_changes(item, terminal, booking_member, monkeypatch, client):
    pay(item, terminal, booking_member, monkeypatch, "40")
    pay(item, terminal, booking_member, monkeypatch, "60")
    client.force_login(booking_member.user)
    monkeypatch.setattr(square, "create_order_refund", lambda r: result(r, "PENDING"))
    client.post(url(item, "reservation_detail"), form_data(item, amount="75"))
    first, second = Refund.objects.filter(attempt__order=item.order).order_by("pk")
    refunds.apply_snapshot(second, result(second))
    Refund.objects.filter(pk=first.pk).update(checked_at=None, recovery_failures=1,
        next_check_at=timezone.now() + timedelta(minutes=2))
    retrieve = Mock(side_effect=lambda r: result(r))
    monkeypatch.setattr(square, "retrieve_order_refund", retrieve)
    endpoint = url(item, "refund_recover", refund_id=first.public_id)
    response = client.post(endpoint, {"action": "check_all", "automatic": "yes"}, HTTP_ACCEPT="application/json")
    assert response.json()["state"] == "pending"
    assert {r["public_id"]: r["state"] for r in response.json()["refunds"]} == {
        str(first.public_id): "pending", str(second.public_id): "succeeded"}
    assert not retrieve.called
    client.post(endpoint, {"action": "check_all"}, HTTP_ACCEPT="application/json")
    assert retrieve.call_count == 1
    assert totals(item.order)["refunded_cents"] == 7500


def test_background_refunds_keep_existing_throttle(item, terminal, booking_member, monkeypatch):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    refund = approve(item, attempt, booking_member)
    refunds.apply_snapshot(refund, result(refund, "PENDING"))
    Refund.objects.filter(pk=refund.pk).update(submitted_at=timezone.now(), checked_at=timezone.now() - timedelta(seconds=6))
    retrieve = Mock(side_effect=lambda r: result(r))
    monkeypatch.setattr(square, "retrieve_order_refund", retrieve)
    refund.refresh_from_db()
    assert not refunds.process(refund, force=True)
    assert not retrieve.called


@pytest.mark.parametrize("state,failures,expected", [
    ("failed", 0, b'Refund of $20.00 failed'),
    ("requires_action", 0, b'Check the $20.00 refund in Square'),
    ("unknown", 0, b'Retry $20.00 refund'),
    ("pending", 6, b'Check $20.00 refund'),
])
def test_refund_recovery_remains_visible_when_needed(item, terminal, booking_member, monkeypatch, client, state, failures, expected):
    attempt = pay(item, terminal, booking_member, monkeypatch)
    refund = approve(item, attempt, booking_member)
    Refund.objects.filter(pk=refund.pk).update(state=state, recovery_failures=failures)
    client.force_login(booking_member.user)
    detail = client.get(url(item, "reservation_detail"))
    assert expected in detail.content
    assert b'Customer refund' not in detail.content
    assert totals(item.order)["refunded_cents"] == 0
    if failures:
        assert b'data-paused="true"' in detail.content
