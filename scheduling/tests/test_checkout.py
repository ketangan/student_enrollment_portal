import copy
import hashlib
import hmac
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import MagicMock

import pytest
import stripe
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School, Submission
from core.payments import checkout, service
from core.payments.models import LedgerEntry, PaymentAccount, PaymentAttempt, PaymentOrder, ProviderEventReceipt
from core.services import billing_stripe as gateway
from scheduling.checkout import customer_reservation, payment_token, prepare_booking_checkout, settle_booking_event
from scheduling.models import Reservation
from scheduling.reservations import active_claims, cancel_reservation, create_reservation
from .test_reservations import inventory, party_request, path, reserve, workshop_request

pytestmark = pytest.mark.django_db


@pytest.fixture
def merchant(inventory, settings):
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_local_tests"
    return PaymentAccount.objects.create(school=inventory[0].school, reference="acct_local_test", enabled=True)


def online(member, source):
    return create_reservation(member.user, member.profile.school.slug,
        "party" if hasattr(source, "organizer_email") else "workshop", source.public_id, 10000, 2500, online=True)


def prepare(item):
    return prepare_booking_checkout(item, "http://localhost:8002/payment-return/")


def session_for(attempt, *, status="open"):
    return {"id": "cs_test_" + attempt.public_id.hex, "mode": "payment", "livemode": False,
        "payment_method_types": ["card"],
        "amount_total": attempt.amount_cents, "currency": "usd", "status": status,
        "payment_status": "paid" if status == "complete" else "unpaid",
        "payment_intent": "pi_" + attempt.public_id.hex if status == "complete" else None,
        "url": "https://checkout.stripe.com/c/pay/" + attempt.public_id.hex,
        "expires_at": int(attempt.expires_at.timestamp()),
        "metadata": {"payment_order": str(attempt.order.public_id), "payment_attempt": str(attempt.public_id), "purpose": "booking"}}


def event_for(attempt, *, expired=False):
    return {"id": "evt_" + uuid.uuid4().hex, "type": "checkout.session.expired" if expired else "checkout.session.completed",
        "account": attempt.order.merchant_reference, "livemode": False,
        "data": {"object": session_for(attempt, status="expired" if expired else "complete")}}


def post_event(client, settings, event, *, secret=None):
    payload = json.dumps(event).encode()
    stamp = str(int(time.time()))
    digest = hmac.new((secret or settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET).encode(), stamp.encode()+b"."+payload, hashlib.sha256).hexdigest()
    return client.post(reverse("scheduling:stripe_test_webhook"), data=payload, content_type="application/json", HTTP_STRIPE_SIGNATURE=f"t={stamp},v1={digest}")


def test_customer_deposit_and_balance_signed_events_do_not_change_school(inventory, merchant, booking_member, client, settings, monkeypatch):
    item = online(booking_member, workshop_request(inventory))
    school = inventory[0].school
    before = (school.plan, school.is_active, school.stripe_customer_id, school.stripe_subscription_id)
    token = payment_token(item)
    url = path(booking_member, "customer_payment", token=token)
    assert customer_reservation(school.slug, token).pk == item.pk
    page = client.get(url)
    assert page.status_code == 200 and b"Pay deposit: $25.00" in page.content
    assert b"Sandbox only. No real payment" not in page.content
    assert b"Returning from checkout alone does not confirm payment" not in page.content
    assert b"Back to workshops" in page.content
    assert b"Back to reservation" not in page.content
    assert page["Referrer-Policy"] == "same-origin"
    assert b"Sign out" not in page.content and b"Team access" not in page.content
    gateway_call = MagicMock(side_effect=session_for)
    monkeypatch.setattr(gateway, "create_order_checkout", gateway_call)
    response = client.post(url, {"amount": "1", "account": "acct_evil", "mode": "live"})
    assert response.status_code == 302 and response.url.startswith("https://checkout.stripe.com/")
    attempt = PaymentAttempt.objects.get()
    assert attempt.amount_cents == 2500
    assert client.post(url).url == response.url and gateway_call.call_count == 1
    # A forged success return cannot settle money or confirm capacity.
    assert client.get(url+"?success=true&payment_status=paid").status_code == 200
    item.refresh_from_db()
    assert item.state == "held" and not LedgerEntry.objects.exists()
    event = event_for(attempt)
    assert post_event(client, settings, event).status_code == 200
    assert post_event(client, settings, event).status_code == 200
    assert LedgerEntry.objects.count() == ProviderEventReceipt.objects.count() == 1
    item.refresh_from_db()
    confirmed_page = client.get(url)
    assert item.state == "confirmed" and b"Pay remaining balance: $75.00" in confirmed_page.content
    assert b"Your reservation is confirmed." in confirmed_page.content
    assert b"Paying the remaining balance now is optional." in confirmed_page.content
    assert client.post(url).status_code == 302
    balance = PaymentAttempt.objects.order_by("pk").last()
    assert balance.amount_cents == 7500
    assert post_event(client, settings, event_for(balance)).status_code == 200
    assert b"Paid in full" in client.get(url).content
    assert service.totals(item.order)["net_cents"] == 10000
    assert checkout.open_checkout(balance) == ""
    school.refresh_from_db()
    assert (school.plan, school.is_active, school.stripe_customer_id, school.stripe_subscription_id) == before
    assert not Submission.objects.exists()
    assert LedgerEntry.objects.filter(channel="stripe", actor=None).count() == 2


def test_payment_return_navigation_requires_workspace_access(inventory, merchant, booking_member, client, django_user_model):
    item = online(booking_member, workshop_request(inventory))
    url = path(booking_member, "customer_payment", token=payment_token(item))
    detail = path(booking_member, "reservation_detail", public_id=item.public_id)
    client.force_login(booking_member.user)
    assert f'href="{detail}"'.encode() in client.get(url).content
    other = django_user_model.objects.create_user(username="other-payment-viewer")
    client.force_login(other)
    assert b"Back to reservation" not in client.get(url).content
    assert client.get(detail).status_code == 404


@pytest.mark.parametrize("state,heading", [("canceled", "Reservation canceled"), ("review", "Reservation needs review")])
def test_payment_return_never_claims_canceled_or_review_booking_is_confirmed(inventory, merchant, booking_member, client, state, heading):
    item = online(booking_member, workshop_request(inventory))
    Reservation.objects.filter(pk=item.pk).update(state=state)
    page = client.get(path(booking_member, "customer_payment", token=payment_token(item)))
    assert heading.encode() in page.content
    assert b"Your reservation is confirmed" not in page.content
    assert b"Pay deposit:" not in page.content


def test_transport_uses_explicit_account_card_only_and_stable_key(inventory, merchant, booking_member, monkeypatch):
    attempt = prepare(online(booking_member, workshop_request(inventory)))
    sdk = MagicMock()
    sdk.v1.checkout.sessions.create.return_value = stripe.StripeObject.construct_from(session_for(attempt), "sk_test_x")
    factory = MagicMock(return_value=sdk)
    monkeypatch.setattr(stripe, "StripeClient", factory)
    global_key = stripe.api_key
    assert gateway.create_order_checkout(attempt)["livemode"] is False
    assert factory.call_args.args == ("sk_test_local_unit_tests_only",)
    assert factory.call_args.kwargs["stripe_account"] == "acct_local_test"
    assert factory.call_args.kwargs["max_network_retries"] == 0
    assert factory.call_args.kwargs["http_client"]._timeout == 10
    kwargs = sdk.v1.checkout.sessions.create.call_args.kwargs
    assert kwargs["options"] == {"idempotency_key": f"payment-attempt:{attempt.public_id}"}
    params = kwargs["params"]
    assert params["mode"] == "payment"
    assert "payment_method_types" not in params
    assert "payment_method_configuration" not in params
    assert params["line_items"][0]["price_data"]["unit_amount"] == 2500
    assert params["payment_intent_data"]["metadata"] == params["metadata"]
    assert 30*60 < params["expires_at"] - time.time() <= 35*60
    assert stripe.api_key == global_key


def test_platform_sdk_no_longer_mutates_global_key(settings):
    settings.STRIPE_SECRET_KEY = "sk_test_platform"
    before = stripe.api_key
    assert gateway._get_stripe() is stripe
    assert stripe.api_key == before


def test_existing_subscription_and_school_fee_calls_keep_their_own_keys(inventory, merchant, booking_member, settings, monkeypatch):
    settings.STRIPE_SECRET_KEY = " sk_test_platform "
    settings.STRIPE_PRICE_PRO_MONTHLY = "price_pro"
    school = inventory[0].school
    sdk = MagicMock()
    sdk.checkout.Session.create.return_value.url = "https://checkout.stripe.com/platform"
    sdk.billing_portal.Session.create.return_value.url = "https://billing.stripe.com/platform"
    sdk.Subscription.retrieve.return_value = stripe.Subscription.construct_from({
        "id":"sub_platform", "status":"active", "items":{"data":[{"price":{"id":"price_pro"}}]}}, "sk_test_platform")
    monkeypatch.setattr(gateway,"_get_stripe",lambda:sdk)
    assert gateway.create_checkout_session(school=school,price_id="price_pro",success_url="http://localhost/ok",cancel_url="http://localhost/no",customer_email="owner@example.invalid")
    assert sdk.checkout.Session.create.call_args.kwargs["api_key"]=="sk_test_platform"
    gateway.handle_checkout_completed({"metadata":{"school_slug":school.slug},"customer":"cus_platform","subscription":"sub_platform"})
    sdk.Subscription.retrieve.assert_called_once_with("sub_platform",api_key="sk_test_platform")
    school.refresh_from_db()
    assert school.plan=="pro" and school.stripe_subscription_status=="active"
    assert gateway.create_portal_session(school=school,return_url="http://localhost/return")
    assert sdk.billing_portal.Session.create.call_args.kwargs["api_key"]=="sk_test_platform"
    assert gateway.create_checkout_session(school=school,price_id="price_pro",success_url="http://localhost/ok",cancel_url="http://localhost/no")
    assert sdk.checkout.Session.create.call_args.kwargs["customer"]=="cus_platform"
    school.app_fee_stripe_secret_key=" sk_test_school_fee "
    fee=MagicMock(return_value=stripe.PaymentIntent.construct_from({"id":"pi_fee","client_secret":"local_secret"},"sk_test_school_fee"))
    retrieve=MagicMock(return_value=stripe.PaymentIntent.construct_from({"id":"pi_fee","status":"succeeded"},"sk_test_school_fee"))
    monkeypatch.setattr(stripe.PaymentIntent,"create",fee)
    monkeypatch.setattr(stripe.PaymentIntent,"retrieve",retrieve)
    assert gateway.create_application_fee_intent(school=school,amount_cents=5000)==("local_secret","pi_fee")
    assert fee.call_args.kwargs["api_key"]=="sk_test_school_fee"
    assert gateway.retrieve_application_fee_intent(school=school,payment_intent_id="pi_fee").status=="succeeded"
    retrieve.assert_called_once_with("pi_fee",api_key="sk_test_school_fee")
    # Booking settlement never overwrites the platform subscription just synchronized above.
    settle_booking_event(event_for(prepare(online(booking_member,workshop_request(inventory)))))
    school.refresh_from_db()
    assert (school.plan, school.stripe_customer_id, school.stripe_subscription_id)==("pro","cus_platform","sub_platform")


@pytest.mark.parametrize("field,value", [("environment","live"),("merchant_reference","acct_wrong"),("currency","eur"),("school_id",999)])
def test_account_snapshot_validation(inventory, merchant, booking_member, field, value):
    item = online(booking_member, workshop_request(inventory))
    setattr(item.order, field, value)
    with pytest.raises(ValidationError):
        checkout.validate_account(item.order)


@pytest.mark.parametrize("key",["", "sk_live_never", "rk_test_restricted"])
def test_no_platform_key_fallback(inventory, merchant, booking_member, settings, key):
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = key
    settings.STRIPE_SECRET_KEY = "sk_live_platform"
    with pytest.raises(ValidationError):
        online(booking_member, workshop_request(inventory))
    with pytest.raises(ValidationError):
        gateway._merchant_test_client("acct_valid")
    assert not PaymentOrder.objects.exists()


def test_missing_disabled_or_foreign_account_is_denied(inventory, booking_member, merchant):
    merchant.enabled = False
    merchant.save()
    source = workshop_request(inventory)
    with pytest.raises(ValidationError):
        online(booking_member, source)
    with pytest.raises(ValidationError):
        service.create_order(school=merchant.school, purpose="booking", source_key="no", total_cents=100, deposit_cents=0, description="No", account=merchant)
    other = School.objects.create(slug="other-merchant")
    merchant.enabled = True
    merchant.save()
    with pytest.raises(ValidationError):
        service.create_order(school=other, purpose="booking", source_key="no", total_cents=100, deposit_cents=0, description="No", account=merchant)
    merchant.delete()
    with pytest.raises(ValidationError):
        online(booking_member, source)


def test_unknown_checkout_keeps_capacity_and_retries_same_attempt(inventory, merchant, booking_member, monkeypatch, client):
    item = online(booking_member, workshop_request(inventory, 3))
    attempt = prepare(item)
    call = MagicMock(side_effect=TimeoutError("network uncertain"))
    monkeypatch.setattr(gateway,"create_order_checkout",call)
    with pytest.raises(ValidationError):
        checkout.open_checkout(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "unknown"
    Reservation.objects.filter(pk=item.pk).update(hold_until=timezone.now()-timedelta(minutes=1))
    item.refresh_from_db()
    assert "needs verification" in item.state_label and active_claims().filter(pk=item.pk).exists()
    with pytest.raises(ValidationError):
        reserve(booking_member,workshop_request(inventory,2))
    with pytest.raises(ValidationError):
        cancel_reservation(booking_member.user, inventory[0].school.slug,item.public_id)
    client.force_login(booking_member.user)
    response = client.post(path(booking_member,"reservation_cancel",public_id=item.public_id),
        {"key": uuid.uuid4(), "resolution": "without_refund", "reason": "Plans changed"}, follow=True)
    assert b"checkout may still accept payment" in response.content
    assert prepare(item).pk == attempt.pk
    call.side_effect = session_for
    assert checkout.open_checkout(attempt).startswith("https://checkout.stripe.com/")
    assert call.call_count == 2 and PaymentAttempt.objects.count() == 1
    assert str(call.call_args_list[0].args[0].public_id) == str(call.call_args_list[1].args[0].public_id)


def test_verified_expiry_releases_capacity_late_money_goes_to_review(inventory, merchant, booking_member):
    item = online(booking_member,workshop_request(inventory,3))
    attempt = prepare(item)
    # Local expiry alone never releases capacity or creates another charge.
    PaymentAttempt.objects.filter(pk=attempt.pk).update(expires_at=timezone.now()-timedelta(minutes=1))
    with pytest.raises(ValidationError):
        checkout.open_checkout(attempt)
    assert active_claims().filter(pk=item.pk).exists()
    event = event_for(attempt,expired=True)
    settle_booking_event(event)
    assert not active_claims().filter(pk=item.pk).exists()
    reserve(booking_member,workshop_request(inventory,2))
    settle_booking_event(event_for(attempt))
    item.refresh_from_db()
    assert item.state == "review" and service.totals(item.order)["net_cents"] == 2500
    # Redelivery cannot opportunistically change domain state.
    settle_booking_event(event_for(attempt))
    item.refresh_from_db()
    assert item.state == "review" and LedgerEntry.objects.count() == 1


@pytest.mark.parametrize("field,value", [("amount_total",1),("amount_total",True),("currency","eur"),("livemode",True),("mode","subscription"),("id","cs_live_wrong"),("payment_status","unpaid"),("payment_intent",None),("status","open")])
def test_signed_wrong_payment_payload_cannot_settle(inventory, merchant, booking_member, client, settings, field, value):
    attempt=prepare(online(booking_member,workshop_request(inventory)))
    event=event_for(attempt)
    event["data"]["object"][field]=value
    assert post_event(client,settings,event).status_code==400
    assert not LedgerEntry.objects.exists() and not ProviderEventReceipt.objects.exists()


@pytest.mark.parametrize("field,value", [("account","acct_other"),("livemode",True),("id","bad")])
def test_signed_wrong_event_scope_cannot_settle(inventory, merchant, booking_member, client, settings, field, value):
    event=event_for(prepare(online(booking_member,workshop_request(inventory))))
    event[field]=value
    assert post_event(client,settings,event).status_code==400
    assert not LedgerEntry.objects.exists()


@pytest.mark.parametrize("field,value",[("payment_order","wrong"),("payment_attempt","bad-uuid"),("payment_attempt",str(uuid.uuid4())),("purpose","subscription")])
def test_signed_wrong_metadata_cannot_settle(inventory, merchant, booking_member, client, settings, field, value):
    event=event_for(prepare(online(booking_member,workshop_request(inventory))))
    event["data"]["object"]["metadata"][field]=value
    assert post_event(client,settings,event).status_code==400
    assert not LedgerEntry.objects.exists()


def test_signature_method_limits_unrelated_events_and_disabled_merchant_settlement(inventory, merchant, booking_member, client, settings):
    attempt=prepare(online(booking_member,workshop_request(inventory)))
    event=event_for(attempt)
    assert post_event(client,settings,event,secret="whsec_wrong").status_code==400
    endpoint=reverse("scheduling:stripe_test_webhook")
    assert client.get(endpoint).status_code==405
    assert client.post(endpoint,data="x"*262145,content_type="application/json").status_code==400
    unrelated=copy.deepcopy(event)
    unrelated["type"]="customer.subscription.updated"
    assert post_event(client,settings,unrelated).status_code==200
    unrelated["type"]="checkout.session.completed"
    unrelated["data"]["object"]["metadata"]={"school_slug":merchant.school.slug}
    assert post_event(client,settings,unrelated).status_code==200
    assert not LedgerEntry.objects.exists()
    merchant.enabled=False
    merchant.save()
    assert post_event(client,settings,event).status_code==200  # Already collected money must not be lost.
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET=""
    assert post_event(client,settings,event,secret="whsec_wrong").status_code==400


@pytest.mark.parametrize("field,value",[("url","https://evil.example/"),("expires_at",1),("status","complete"),("amount_total",1)])
def test_unverified_checkout_response_is_not_redirected(inventory, merchant, booking_member, monkeypatch, field, value):
    attempt=prepare(online(booking_member,workshop_request(inventory)))
    session=session_for(attempt)
    session[field]=value
    monkeypatch.setattr(gateway,"create_order_checkout",lambda _:session)
    with pytest.raises(ValidationError):
        checkout.open_checkout(attempt)
    attempt.refresh_from_db()
    assert attempt.state=="unknown" and not attempt.checkout_url


def test_webhook_racing_checkout_does_not_reopen_paid_attempt(inventory, merchant, booking_member, monkeypatch):
    attempt=prepare(online(booking_member,workshop_request(inventory)))
    def delayed_response(current):
        settle_booking_event(event_for(current))
        return session_for(current)
    monkeypatch.setattr(gateway,"create_order_checkout",delayed_response)
    assert checkout.open_checkout(attempt)==""
    attempt.refresh_from_db()
    assert attempt.state=="paid"


def test_payment_link_scoping_expiry_and_csrf(inventory, merchant, booking_member, client, monkeypatch):
    item=online(booking_member,workshop_request(inventory))
    url=path(booking_member,"customer_payment",token=payment_token(item))
    assert client.get(url.replace("workshop-test","other-school")).status_code==404
    assert client.get(url+"changed").status_code==404
    assert Client(enforce_csrf_checks=True).post(url).status_code==403
    now=time.time()
    monkeypatch.setattr(time,"time",lambda:now+86401)
    assert client.get(url).status_code==404


@pytest.mark.parametrize("change",["canceled","review","disabled","native_off","started"])
def test_unavailable_customer_checkout_never_calls_stripe(inventory, merchant, booking_member, client, monkeypatch, change):
    item=online(booking_member,workshop_request(inventory))
    if change in ("canceled","review"):
        Reservation.objects.filter(pk=item.pk).update(state=change)
    elif change=="started":
        Reservation.objects.filter(pk=item.pk).update(starts_at=timezone.now()+timedelta(minutes=30))
    else:
        profile=inventory[0]
        setattr(profile,"enabled" if change=="disabled" else "native_reservations_enabled",False)
        profile.save()
    call=MagicMock()
    monkeypatch.setattr(gateway,"create_order_checkout",call)
    url=path(booking_member,"customer_payment",token=payment_token(item))
    assert b'Pay deposit:' not in client.get(url).content
    response=client.post(url)
    assert response.status_code==400 and not call.called and not PaymentAttempt.objects.exists()


@pytest.mark.parametrize("kind,balance_cents", [("workshop", 7500), ("party", 30000)])
def test_paused_intake_preserves_confirmed_balance_checkout(inventory, merchant, booking_member, client, monkeypatch, settings, kind, balance_cents):
    source = workshop_request(inventory) if kind == "workshop" else party_request(inventory)
    item = online(booking_member, source)
    deposit = prepare(item)
    assert post_event(client, settings, event_for(deposit)).status_code == 200
    profile = inventory[0]
    profile.native_reservations_enabled = False
    profile.save(update_fields=["native_reservations_enabled"])
    item.refresh_from_db()
    assert item.state == "confirmed"
    assert service.totals(item.order)["outstanding_cents"] == balance_cents
    with pytest.raises(ValidationError):
        online(booking_member, workshop_request(inventory))
    call = MagicMock(side_effect=session_for)
    monkeypatch.setattr(gateway, "create_order_checkout", call)
    url = path(booking_member, "customer_payment", token=payment_token(item))
    assert f"Pay remaining balance: ${balance_cents / 100:.2f}".encode() in client.get(url).content
    assert client.post(url).status_code == 302
    assert client.post(url).status_code == 302
    assert call.call_count == 1
    balance = item.order.attempts.exclude(pk=deposit.pk).get()
    assert balance.amount_cents == balance_cents
    event = event_for(balance)
    assert post_event(client, settings, event).status_code == 200
    assert post_event(client, settings, event).status_code == 200
    assert service.totals(item.order)["outstanding_cents"] == 0
    assert item.order.entries.count() == 2
    assert b"Paid in full" in client.get(url).content


@pytest.mark.parametrize("blocked", ["merchant_disabled", "wrong_account", "profile_disabled", "canceled", "review", "no_key"])
def test_confirmed_balance_still_enforces_payment_safety(inventory, merchant, booking_member, client, monkeypatch, settings, blocked):
    item = online(booking_member, workshop_request(inventory))
    settle_booking_event(event_for(prepare(item)))
    if blocked == "merchant_disabled":
        merchant.enabled = False
        merchant.save()
    elif blocked == "wrong_account":
        merchant.reference = "acct_replaced"
        merchant.save()
    elif blocked == "profile_disabled":
        inventory[0].enabled = False
        inventory[0].save()
    elif blocked in ("canceled", "review"):
        Reservation.objects.filter(pk=item.pk).update(state=blocked)
    else:
        settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = ""
    call = MagicMock()
    monkeypatch.setattr(gateway, "create_order_checkout", call)
    url = path(booking_member, "customer_payment", token=payment_token(item))
    assert not client.get(url).context["ready"]
    assert client.post(url).status_code == 400
    assert not call.called and item.order.attempts.count() == 1


def test_owner_ui_online_quote_and_immutable_payment_method(inventory, merchant, booking_member, client):
    client.force_login(booking_member.user)
    source=party_request(inventory)
    url=path(booking_member,"reservation_from_party",public_id=source.public_id)
    result=client.post(url,{"confirm":"on","source_revision":0,"online":"on"})
    assert result.status_code==302
    item=Reservation.objects.get()
    assert item.order.account_id==merchant.pk
    assert online(booking_member,source).pk==item.pk
    with pytest.raises(ValidationError):
        reserve(booking_member,source)
    response=client.get(result.url)
    assert b"Open customer payment page" in response.content
    assert b"Record offline entry" not in response.content
    assert client.post(result.url,{"key":uuid.uuid4(),"amount":"100","kind":"payment","reference":"no","confirm":"on"}).status_code==405
    assert not LedgerEntry.objects.exists()
    assert prepare(item).amount_cents==10000


def test_financial_and_domain_writes_roll_back_together(inventory, merchant, booking_member, monkeypatch):
    attempt=prepare(online(booking_member,workshop_request(inventory)))
    def fail(**kwargs):
        raise RuntimeError("audit failed")
    monkeypatch.setattr(AdminAuditLog.objects,"create",fail)
    with pytest.raises(RuntimeError):
        settle_booking_event(event_for(attempt))
    assert not LedgerEntry.objects.exists() and not ProviderEventReceipt.objects.exists()
    attempt.refresh_from_db()
    assert attempt.state=="pending"


def test_expiry_after_payment_and_payment_after_cancellation(inventory, merchant, booking_member):
    item=online(booking_member,workshop_request(inventory))
    attempt=prepare(item)
    settle_booking_event(event_for(attempt))
    settle_booking_event(event_for(attempt,expired=True))
    attempt.refresh_from_db()
    assert attempt.state=="paid" and active_claims().filter(pk=item.pk).exists()
    balance=prepare(item)
    settle_booking_event(event_for(balance,expired=True))
    cancel_reservation(booking_member.user,merchant.school.slug,item.public_id)
    settle_booking_event(event_for(balance))
    item.refresh_from_db()
    assert item.state=="canceled" and service.totals(item.order)["net_cents"]==10000
    assert not active_claims().exists()


def test_late_money_while_another_checkout_is_open_needs_review(inventory, merchant, booking_member):
    item=online(booking_member,workshop_request(inventory))
    first=prepare(item)
    settle_booking_event(event_for(first,expired=True))
    second=prepare(item)
    assert first.pk!=second.pk
    settle_booking_event(event_for(first))
    item.refresh_from_db()
    assert item.state=="review" and active_claims().filter(pk=item.pk).exists()
    with pytest.raises(ValidationError):
        checkout.prepare_attempt(item.order,return_url="http://localhost/")
    settle_booking_event(event_for(second,expired=True))
    assert not active_claims().filter(pk=item.pk).exists()


def test_no_repayment_or_cross_domain_event(inventory, merchant, booking_member):
    item=online(booking_member,workshop_request(inventory))
    for _ in range(2):
        settle_booking_event(event_for(prepare(item)))
    with pytest.raises(ValidationError):
        prepare(item)
    assert checkout.apply_event({"type":"other"}) is None
    attempt=PaymentAttempt.objects.first()
    PaymentOrder.objects.filter(pk=item.order_id).update(purpose="application")
    attempt=PaymentAttempt.objects.get(pk=attempt.pk)
    event=event_for(attempt)
    event["data"]["object"]["metadata"]["purpose"]="application"
    with pytest.raises(ValidationError):
        settle_booking_event(event)


def test_overpayment_never_releases_an_already_confirmed_place(inventory, merchant, booking_member, client):
    item=online(booking_member,workshop_request(inventory))
    settle_booking_event(event_for(prepare(item)))
    old_balance=prepare(item)
    settle_booking_event(event_for(old_balance,expired=True))
    settle_booking_event(event_for(prepare(item)))
    settle_booking_event(event_for(old_balance))
    item.refresh_from_db()
    assert item.state=="confirmed" and active_claims().filter(pk=item.pk).exists()
    assert service.totals(item.order)["net_cents"]==17500
    client.force_login(booking_member.user)
    assert b"payments exceed this quote" in client.get(path(booking_member,"reservation_detail",public_id=item.public_id)).content


def test_changed_payment_session_identity_and_invalid_expiry(inventory, merchant, booking_member, monkeypatch):
    item=online(booking_member,workshop_request(inventory))
    attempt=prepare(item)
    monkeypatch.setattr(gateway,"create_order_checkout",session_for)
    checkout.open_checkout(attempt)
    event=event_for(attempt)
    event["data"]["object"]["id"]="cs_test_changed"
    with pytest.raises(ValidationError):
        settle_booking_event(event)
    expired=event_for(attempt,expired=True)
    expired["data"]["object"]["status"]="open"
    with pytest.raises(ValidationError):
        settle_booking_event(expired)
    settle_booking_event(event_for(attempt))
    event=event_for(attempt)
    event["data"]["object"]["payment_intent"]="pi_changed"
    with pytest.raises(ValidationError):
        settle_booking_event(event)
    other=prepare(online(booking_member,workshop_request(inventory)))
    event=event_for(other)
    event["data"]["object"]["payment_intent"]="pi_"+attempt.public_id.hex
    with pytest.raises(ValidationError):
        settle_booking_event(event)
    event=event_for(other)
    event["data"]["object"]["id"]="cs_test_"+attempt.public_id.hex
    with pytest.raises(ValidationError):
        settle_booking_event(event)
    assert LedgerEntry.objects.count()==1


@pytest.mark.parametrize("payload",[[],{"type":"checkout.session.completed","data":[]},
    {"type":"checkout.session.completed","data":{"object":{"metadata":[]}}}])
def test_malformed_verified_payload_is_rejected(payload):
    with pytest.raises(ValidationError):
        checkout.event_attempt(payload)


def test_disabled_account_new_attempt_and_invalid_gateway_reference(inventory, merchant, booking_member):
    item=online(booking_member,workshop_request(inventory))
    merchant.enabled=False
    merchant.save()
    with pytest.raises(ValidationError):
        prepare(item)
    with pytest.raises(ValidationError):
        gateway._merchant_test_client("wrong")


@pytest.mark.django_db(transaction=True)
def test_concurrent_checkout_preparation_and_duplicate_settlement(inventory, merchant, booking_member):
    item=online(booking_member,workshop_request(inventory))
    barrier=Barrier(2)
    def start(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return prepare(item).pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(start,range(2)))
    assert result[0]==result[1] and PaymentAttempt.objects.count()==1
    event=event_for(PaymentAttempt.objects.get())
    def settle(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            settle_booking_event(event)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(settle,range(2)))
    assert LedgerEntry.objects.count()==ProviderEventReceipt.objects.count()==1
