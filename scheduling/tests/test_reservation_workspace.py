import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AdminAuditLog
from core.payments import refunds, service
from core.payments.models import Refund
from core.services import billing_stripe as gateway
from scheduling import cancellation, inventory as stock_service, preparation
from scheduling.models import Reservation, SchedulingMembership
from scheduling.reservation_audit import audit_page
from scheduling.reservation_workspace import prep_context
from .test_checkout import merchant, prepare, event_for
from .test_payment_recovery import paid, refund_for, ready
from .test_reservations import inventory, workshop_request, reserve, settle, path, party_request
from .test_inventory import stock, reserve as stock_reserve, confirm, url
from .test_workshop_polish import command
from .test_camps import camp, register

pytestmark = pytest.mark.django_db


def cancel_data(**changes):
    return {"key": uuid.uuid4(), "resolution": "without_refund", "reason": "Plans changed", **changes}


def test_list_and_record_tabs_have_distinct_scope(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory))
    client.force_login(booking_member.user)
    listing = client.get(path(booking_member, "reservations"))
    assert "Workshop Prep" not in [tab["label"] for tab in listing.context["booking_navigation"]["section"]["tabs"]]
    assert b">Prep</th>" in listing.content and b"On hold" in listing.content
    target = path(booking_member, "reservation_detail", public_id=item.public_id)
    response = client.get(target)
    assert [tab["label"] for tab in response.context["booking_navigation"]["section"]["tabs"]] == ["Reservation", "Workshop Prep"]
    assert b"<dt>Room</dt>" not in response.content
    assert b"customer@example.com" in response.content and b"<dt>Studio</dt>" in response.content
    assert b"Record offline" not in response.content and b"Payment history" not in response.content
    assert b"Audit log" in response.content and b"Reservation created" in response.content
    assert b'name="confirm"' not in response.content
    assert client.post(target, {"kind": "payment", "amount": "1"}).status_code == 405
    assert not item.order.entries.exists()
    assert response.context["location_action"] == path(booking_member, "reservations")
    body = response.content.decode().split('class="reservation-money"')[1].split('</dl>')[0]
    assert body.index('Total') < body.index('Deposit') < body.index('Received') < body.index('Balance') < body.index('Refunded')


def test_prep_is_one_booking_and_ready_state_is_in_list_and_audit(stock):
    s = stock
    first = stock_reserve(s); confirm(s, first)
    second = stock_reserve(s); confirm(s, second)
    second.workshop_request.participants.update(name="Not this booking")
    target = url(s, "reservation_preparation", public_id=first.public_id)
    response = s.client.get(target)
    assert b"<dt>Room</dt>" in response.content and b"Not this booking" not in response.content
    assert b"Design" in response.content and b"Blue" in response.content and b"Materials" in response.content
    assert b"Pending" in s.client.get(url(s, "reservations")).content
    post = s.client.post(url(s, "preparation_update", public_id=first.public_id), command(s, first, "prepare"))
    assert post.url == target
    assert b"Complete" in s.client.get(url(s, "reservations")).content
    assert b"Preparation completed" in s.client.get(url(s, "reservation_detail", public_id=first.public_id)).content
    s.client.post(url(s, "preparation_update", public_id=first.public_id), command(s, first, "reopen"))
    assert b"Preparation reopened" in s.client.get(url(s, "reservation_detail", public_id=first.public_id)).content
    used = s.client.post(url(s, "reservation_complete", public_id=first.public_id), {"action": "complete"})
    assert used.url == url(s, "reservation_detail", public_id=first.public_id)
    page = s.client.get(url(s, "reservation_detail", public_id=first.public_id))
    assert b"Materials used" in page.content and b"Blue cat" in page.content
    assert b"Reopen preparation" not in s.client.get(target).content
    assert b"Complete" in s.client.get(url(s, "reservations")).content


def test_prep_party_and_camp_preserve_saved_choices(inventory, booking_member, camp, client):
    registration = register(camp)
    item = registration.reservation
    client.force_login(booking_member.user)
    response = client.get(path(booking_member, "reservation_preparation", public_id=item.public_id))
    assert b"Camper 0" in response.content and b"Color" in response.content and b"Blue" in response.content
    assert b"Camp dates" in response.content
    source = party_request(inventory, start=inventory[3].ends_at + timedelta(days=5))
    source.customer_notes = "Please seat us together"
    source.preparation_notes = "Prepare four stations"
    source.save()
    party = reserve(booking_member, source)
    values = prep_context(party, booking_member.profile)
    assert values["participants"] == [] and values["party_materials"] == source.materials
    response = client.get(path(booking_member, "reservation_preparation", public_id=party.public_id))
    assert b"Please seat us together" in response.content and b"Prepare four stations" in response.content
    response = client.get(path(booking_member, "reservation_detail", public_id=party.public_id))
    assert b"party@example.com" in response.content


@pytest.mark.parametrize("state,label", [("held", "On hold"), ("review", "On hold"), ("confirmed", "Pending"), ("canceled", "Not required")])
def test_badge_states(inventory, booking_member, client, state, label):
    item = reserve(booking_member, workshop_request(inventory))
    Reservation.objects.filter(pk=item.pk).update(state=state)
    client.force_login(booking_member.user)
    assert label.encode() in client.get(path(booking_member, "reservations")).content


def test_old_booking_without_contact_remains_readable(inventory, booking_member, client):
    source = workshop_request(inventory)
    source.purchaser_email = ""
    source.save()
    item = reserve(booking_member, source)
    client.force_login(booking_member.user)
    response = client.get(path(booking_member, "reservation_detail", public_id=item.public_id))
    assert response.status_code == 200 and b"Not provided" in response.content


def test_time_is_compact_and_compares_local_dates(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory))
    client.force_login(booking_member.user)
    start = datetime(2026, 10, 11, 1, tzinfo=ZoneInfo("UTC"))
    Reservation.objects.filter(pk=item.pk).update(starts_at=start, ends_at=start + timedelta(hours=2))
    body = client.get(path(booking_member, "reservation_detail", public_id=item.public_id)).content.decode()
    assert 'Sat, Oct 10, 2026' in body and '6:00 PM &ndash; 8:00 PM' in body
    assert 'Oct 11, 2026' not in body
    Reservation.objects.filter(pk=item.pk).update(ends_at=start + timedelta(hours=9))
    body = client.get(path(booking_member, "reservation_detail", public_id=item.public_id)).content.decode()
    assert 'Sat, Oct 10, 2026 &ndash; Oct 11, 2026' in body


def test_without_refund_is_explicit_idempotent_and_audited(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory)); settle(booking_member, item)
    client.force_login(booking_member.user)
    target = path(booking_member, "reservation_cancel", public_id=item.public_id)
    response = client.get(target)
    assert b'type="checkbox"' not in response.content and b'type="radio"' in response.content
    assert client.post(target, {}).status_code == 400
    data = cancel_data()
    assert client.post(target, data).status_code == 302
    assert client.post(target, data).status_code == 302
    assert service.totals(item.order)["net_cents"] == 2500 and not Refund.objects.exists()
    assert AdminAuditLog.objects.filter(changes__action="cancellation_decision").count() == 1
    assert client.post(target, {**data, "reason": "Different"}).status_code == 400
    assert client.post(target, cancel_data()).status_code == 400
    detail = client.get(path(booking_member, "reservation_detail", public_id=item.public_id))
    assert b"Without refund: Plans changed" in detail.content and b"Not due" in detail.content


@pytest.mark.parametrize("resolution,reason", [("invalid", "why"), ("with_refund", " "), ("without_refund", "x" * 241)])
def test_cancellation_service_rejects_invalid_decisions(inventory, booking_member, resolution, reason):
    item = reserve(booking_member, workshop_request(inventory))
    with pytest.raises(ValidationError):
        cancellation.cancel(booking_member.user, item.profile.school.slug, item.public_id, **cancel_data(resolution=resolution, reason=reason))
    item.refresh_from_db(); assert item.state == "held"


def test_refund_rejects_unpaid_and_offline_without_canceling(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory))
    client.force_login(booking_member.user)
    target = path(booking_member, "reservation_cancel", public_id=item.public_id)
    assert b"no remaining payment" in client.post(target, cancel_data(resolution="with_refund")).content
    settle(booking_member, item)
    assert b"outside Pontora" in client.post(target, cancel_data(resolution="with_refund")).content
    item.refresh_from_db(); assert item.state == "confirmed"


def test_cancel_refunds_deposit_and_balance_with_duplicate_protection(inventory, merchant, booking_member, client, monkeypatch):
    from scheduling.checkout import settle_booking_event
    item, first = paid(inventory, booking_member)
    balance = prepare(item); settle_booking_event(event_for(balance))
    provider = MagicMock(side_effect=refund_for)
    monkeypatch.setattr(gateway, "create_order_refund", provider)
    client.force_login(booking_member.user)
    target = path(booking_member, "reservation_cancel", public_id=item.public_id)
    data = cancel_data(resolution="with_refund")
    assert client.post(target, data).status_code == 302
    assert provider.call_count == 2 and Refund.objects.count() == 2
    assert service.totals(item.order)["refunded_cents"] == 10000
    assert client.post(target, data).status_code == 302 and provider.call_count == 2
    detail = client.get(path(booking_member, "reservation_detail", public_id=item.public_id))
    assert b"Refund recorded" in detail.content and b"Refund verified" in detail.content
    assert b"With refund: Plans changed" in detail.content


def test_failed_refund_stays_visible_without_false_returned_money(inventory, merchant, booking_member, client, monkeypatch):
    item, attempt = paid(inventory, booking_member)
    monkeypatch.setattr(gateway, "create_order_refund", lambda r: refund_for(r, "failed"))
    client.force_login(booking_member.user)
    target = path(booking_member, "reservation_cancel", public_id=item.public_id)
    assert client.post(target, cancel_data(resolution="with_refund")).status_code == 302
    item.refresh_from_db(); assert item.state == "canceled"
    assert service.totals(item.order)["refunded_cents"] == 0
    detail = client.get(path(booking_member, "reservation_detail", public_id=item.public_id))
    assert b"Refund failed" in detail.content and b"still received" in detail.content


def test_refund_guards_pending_checkout_pending_refund_and_rollback(inventory, merchant, booking_member, monkeypatch):
    item, attempt = paid(inventory, booking_member)
    pending = prepare(item)
    with pytest.raises(ValidationError, match="checkout"):
        cancellation.cancel(booking_member.user, item.profile.school.slug, item.public_id, **cancel_data(resolution="with_refund"))
    pending.state = "expired"; pending.save()
    refund = Refund.objects.create(attempt=attempt, key=uuid.uuid4(), amount_cents=100, reason="Prior", actor=booking_member.user)
    with pytest.raises(ValidationError, match="already in progress"):
        cancellation.cancel(booking_member.user, item.profile.school.slug, item.public_id, **cancel_data(resolution="with_refund"))
    refund.state = "failed"; refund.save()
    monkeypatch.setattr(cancellation, "approve_refund", MagicMock(side_effect=ValidationError("Payment account changed")))
    with pytest.raises(ValidationError, match="account changed"):
        cancellation.cancel(booking_member.user, item.profile.school.slug, item.public_id, **cancel_data(resolution="with_refund"))
    item.refresh_from_db(); assert item.state == "confirmed"
    assert not AdminAuditLog.objects.filter(changes__action="canceled").exists()


def test_partial_prior_refund_only_returns_remaining_payment(inventory, merchant, booking_member):
    item, attempt = paid(inventory, booking_member)
    previous = refunds.approve(attempt, key=uuid.uuid4(), amount_cents=500, reason="Adjustment", actor=booking_member.user)
    refunds.apply_snapshot(previous, refund_for(previous))
    _, approved = cancellation.cancel(booking_member.user, item.profile.school.slug, item.public_id, **cancel_data(resolution="with_refund"))
    assert len(approved) == 1 and approved[0].amount_cents == 2000


def test_fully_refunded_deposit_is_not_refunded_again(inventory, merchant, booking_member):
    from scheduling.checkout import settle_booking_event
    item, attempt = paid(inventory, booking_member)
    balance = prepare(item); settle_booking_event(event_for(balance))
    previous = refunds.approve(attempt, key=uuid.uuid4(), amount_cents=2500, reason="Deposit returned", actor=booking_member.user)
    refunds.apply_snapshot(previous, refund_for(previous))
    _, approved = cancellation.cancel(booking_member.user, item.profile.school.slug, item.public_id, **cancel_data(resolution="with_refund"))
    assert len(approved) == 1 and approved[0].amount_cents == 7500
    assert approved[0].attempt_id == balance.pk


@pytest.mark.django_db(transaction=True)
def test_concurrent_cancel_refund_submissions_create_one_refund(inventory, merchant, booking_member):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    item, attempt = paid(inventory, booking_member)
    data, barrier = cancel_data(resolution="with_refund"), Barrier(2)
    def submit(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            _, approved = cancellation.cancel(booking_member.user, item.profile.school.slug, item.public_id, **data)
            return approved[0].pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(submit, range(2)))
    assert ids[0] == ids[1] and Refund.objects.count() == 1
    assert AdminAuditLog.objects.filter(changes__action="cancellation_decision").count() == 1


@pytest.mark.parametrize("role", ["viewer", "scheduler"])
def test_money_permissions_and_record_scope(inventory, booking_member, client, role):
    item = reserve(booking_member, workshop_request(inventory))
    booking_member.role = role; booking_member.save()
    client.force_login(booking_member.user)
    detail = client.get(path(booking_member, "reservation_detail", public_id=item.public_id))
    assert b"Cancel reservation</a>" not in detail.content
    cancel_url = path(booking_member, "reservation_cancel", public_id=item.public_id)
    assert client.get(cancel_url).status_code == client.post(cancel_url, cancel_data()).status_code == 404
    prep = path(booking_member, "reservation_preparation", public_id=item.public_id)
    assert client.get(prep).status_code == 200
    assert client.get(prep.replace(str(item.public_id), str(uuid.uuid4()))).status_code == 404
    csrf = Client(enforce_csrf_checks=True); csrf.force_login(booking_member.user)
    assert csrf.post(cancel_url, cancel_data()).status_code == 403


def test_audit_keeps_prep_diffs_escapes_text_and_paginates(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory))
    for i in range(30):
        AdminAuditLog.objects.create(actor=booking_member.user, action="change", model_label="scheduling.reservation",
            object_id=str(item.pk), changes={"note": {"from": "old", "to": f"<script>{i}</script>"}})
    AdminAuditLog.objects.create(actor=booking_member.user, action="change", model_label="scheduling.reservation",
        object_id=str(item.pk), changes={"prepared_at": {"from": None, "to": timezone.now().isoformat()}})
    unrelated = AdminAuditLog.objects.create(actor=booking_member.user, action="change", model_label="scheduling.reservation",
        object_id="9999999", changes={"action": "cancellation_decision"}, extra={"reason": "Private other booking"})
    first = audit_page(item, "bad")
    assert first["number"] == 1 and first["has_next"] and len(first["rows"]) == 25
    assert first["rows"][0]["title"] == "Preparation completed"
    assert not any("Private" in row["detail"] for row in first["rows"])
    second = audit_page(item, 2)
    assert second["has_previous"] and not second["has_next"]
    assert not set(r["key"] for r in first["rows"]) & set(r["key"] for r in second["rows"])
    client.force_login(booking_member.user)
    body = client.get(path(booking_member, "reservation_detail", public_id=item.public_id)).content
    assert b"&lt;script&gt;" in body and b"<script>" not in body
    AdminAuditLog.objects.filter(object_id=str(item.pk)).delete()
    assert audit_page(item, None)["rows"][0]["title"] == "Reservation created"


def test_row_prep_indicator_adds_no_per_reservation_queries(stock):
    s = stock
    one = stock_reserve(s); confirm(s, one)
    with CaptureQueriesContext(connection) as initial:
        s.client.get(url(s, "reservations"))
    for _ in range(3):
        stock_reserve(s)
    with CaptureQueriesContext(connection) as repeated:
        s.client.get(url(s, "reservations"))
    assert len(repeated) == len(initial)
