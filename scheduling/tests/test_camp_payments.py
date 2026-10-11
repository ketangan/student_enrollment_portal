from datetime import timedelta
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.payments import checkout, refunds
from core.payments.models import LedgerEntry, PaymentAttempt
from core.payments.service import totals
from core.services import billing_stripe as gateway
from scheduling.camps import save_camp
from scheduling.checkout import approve_refund, recover_attempt, resolve_review, settle_booking_event
from scheduling.models import Reservation
from scheduling.reservations import cancel_reservation
from scheduling.tests.test_camps import camp, payload, register, setup_data
from scheduling.tests.test_checkout import merchant, event_for, post_event, prepare, session_for
from scheduling.tests.test_payment_recovery import refund_for
from scheduling.tests.test_reservations import inventory, path, reserve, workshop_request

pytestmark = pytest.mark.django_db


def test_camp_customer_deposit_balance_refund_share_existing_module(camp, merchant, booking_member, client, settings, monkeypatch):
    camp.online = True
    camp.save()
    url = path(booking_member, "camp_register", pk=camp.pk)
    page = client.get(url)
    response = client.post(url, payload(camp, token=page.context["request_token"]))
    receipt = client.get(response.url)
    payment_url = receipt.context["payment_url"]
    item = Reservation.objects.get()
    school = booking_member.profile.school
    before = (school.plan, school.stripe_customer_id, school.stripe_subscription_id)
    monkeypatch.setattr(gateway, "create_order_checkout", session_for)
    assert b"Pay $50.02" in client.get(payment_url).content
    assert b"Camp dates" in client.get(payment_url).content
    assert client.post(payment_url).status_code == 302
    attempt = item.order.attempts.get()
    assert attempt.amount_cents == 5002 and item.occurrences.count() == 3
    event = event_for(attempt)
    assert post_event(client, settings, event).status_code == 200
    assert post_event(client, settings, event).status_code == 200
    item.refresh_from_db()
    assert item.state == "confirmed" and LedgerEntry.objects.count() == 1
    booking_member.profile.native_reservations_enabled = False
    booking_member.profile.save(update_fields=["native_reservations_enabled"])
    assert b"Pay $150.00" in client.get(payment_url).content
    assert client.post(payment_url).status_code == 302
    balance = item.order.attempts.order_by("pk").last()
    assert balance.amount_cents == 15000
    assert post_event(client, settings, event_for(balance)).status_code == 200
    cancel_reservation(booking_member.user, school.slug, item.public_id)
    refund = approve_refund(booking_member.user, school.slug, item.public_id, attempt.public_id,
        key=uuid.uuid4(), amount_cents=5002, reason="Whole camp canceled")
    monkeypatch.setattr(gateway, "create_order_refund", refund_for)
    assert refunds.process(refund)
    assert totals(item.order)["refunded_cents"] == 5002
    assert item.occurrences.count() == 3
    school.refresh_from_db()
    assert (school.plan, school.stripe_customer_id, school.stripe_subscription_id) == before


def test_open_checkout_pins_every_camp_date_until_verified_expiry(camp, merchant, booking_member, monkeypatch):
    camp.online = True
    camp.save()
    item = register(camp, 3).reservation
    attempt = prepare(item)
    Reservation.objects.filter(pk=item.pk).update(hold_until=timezone.now()-timedelta(hours=1))
    PaymentAttempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now()-timedelta(minutes=1))
    with pytest.raises(ValidationError): register(camp)
    with pytest.raises(ValidationError): cancel_reservation(booking_member.user, booking_member.profile.school.slug, item.public_id)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", lambda a: session_for(a, status="expired"))
    assert recover_attempt(attempt)
    assert register(camp, 4).reservation.pk


def test_missed_payment_recovery_confirms_every_occurrence(camp, merchant, monkeypatch):
    camp.online = True
    camp.save()
    item = register(camp).reservation
    attempt = prepare(item)
    monkeypatch.setattr(gateway, "retrieve_order_checkout", lambda a: session_for(a, status="complete"))
    assert recover_attempt(attempt)
    settle_booking_event(event_for(attempt))
    item.refresh_from_db()
    assert item.state == "confirmed" and item.occurrences.count() == 3
    assert totals(item.order)["net_cents"] == 5002


def test_expired_checkout_then_late_payment_conflict_requires_whole_camp_review(camp, merchant, inventory, booking_member):
    camp.online = True
    camp.save()
    item = register(camp, 3).reservation
    attempt = prepare(item)
    settle_booking_event(event_for(attempt, expired=True))
    middle = list(camp.sessions.all())[1]
    other = reserve(booking_member, workshop_request((*inventory[:3], middle), 2))
    settle_booking_event(event_for(attempt))
    item.refresh_from_db()
    assert item.state == "review" and totals(item.order)["net_cents"] == 7503
    with pytest.raises(ValidationError): resolve_review(booking_member.user, item.profile.school.slug, item.public_id)
    cancel_reservation(booking_member.user, item.profile.school.slug, other.public_id)
    resolve_review(booking_member.user, item.profile.school.slug, item.public_id)
    item.refresh_from_db()
    assert item.state == "confirmed"


def test_online_camp_setup_and_no_real_provider_calls(camp, merchant, booking_member):
    form = save_camp(booking_member.user, booking_member.profile.school.slug, camp.offering_id,
        setup_data(camp, online="on"), camp.pk)
    assert form.is_valid(), form.errors
    camp.refresh_from_db()
    assert register(camp).reservation.order.account_id == merchant.pk


def test_paid_camp_closes_before_checkout_cutoff(camp, merchant):
    camp.online = True
    camp.save()
    session = camp.sessions.first()
    session.starts_at = timezone.now()+timedelta(minutes=39)
    session.ends_at = session.starts_at+timedelta(hours=1)
    session.save()
    with pytest.raises(ValidationError, match="40 minutes"):
        register(camp)
    assert not Reservation.objects.exists()
