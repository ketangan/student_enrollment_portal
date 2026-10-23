import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import Http404
from django.db import connection as database, IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone

from core.models import School
from core.payments import square
from core.payments.models import PaymentAccount
from core.payments.service import create_order
from core.payments.settings import save_provider
from scheduling.integrations import square_connection as service, vault
from scheduling.models import Location, SquareConnection, SquareLocationMapping
from scheduling.reservations import booking_account
from .test_square_connection import setup, connected, url
from .test_square_payments import square_account, attempt
from .test_reservations import inventory
from . import test_square_payments as scenarios

pytestmark = pytest.mark.django_db


@pytest.fixture
def mapped(client, setup, settings, monkeypatch):
    settings.DEBUG = True
    connection = connected(client, setup)
    profile = setup[0].profile
    first = Location.objects.create(profile=profile, name="First studio")
    second = Location.objects.create(profile=profile, name="Second studio")
    locations = [{"id": "LOC1", "name": "Square One"}, {"id": "LOC2", "name": "Square Two"}]
    listing = Mock(return_value=locations)
    monkeypatch.setattr(square, "list_locations", listing)
    request = RequestFactory().post("/", secure=True)
    request.user = setup[0].user
    service.update_locations(request, profile.school.slug, connection.revision,
        mappings={f"location_{first.pk}": "LOC1", f"location_{second.pk}": "LOC2"})
    connection.refresh_from_db()
    return connection, first, second, request, listing


def test_mapping_creates_immutable_bindings_without_switching_provider(mapped):
    connection, first, second, _, _ = mapped
    assert connection.profile.payment_provider == "stripe"
    assert connection.mappings.count() == 2
    connection.profile.payment_provider = "square"
    account = booking_account(connection.profile, True, first)
    other = booking_account(connection.profile, True, second)
    assert account.reference == other.reference == connection.merchant_id
    assert account.pk != other.pk
    assert account.location_reference == "LOC1" and other.location_reference == "LOC2"
    assert account.scope == "connected"


def test_mapping_save_and_refresh_ui(client, setup, mapped):
    connection, first, second, _, _ = mapped
    page = client.get(url(setup, "integrations"), secure=True)
    assert b"Save mappings" in page.content and b"Square One" in page.content
    assert b"Online payment provider: Stripe" in page.content
    assert b"access-secret" not in page.content
    response = client.post(url(setup), {"action": "map", "revision": connection.revision,
        f"location_{first.pk}": "LOC2", f"location_{second.pk}": ""}, secure=True, follow=True)
    assert b"Location mappings saved" in response.content
    assert connection.mappings.count() == 1
    connection.refresh_from_db()
    response = client.post(url(setup), {"action": "locations", "revision": connection.revision}, secure=True, follow=True)
    assert b"Square locations refreshed" in response.content


def test_remapping_does_not_change_old_order_or_account(mapped):
    connection, first, second, request, _ = mapped
    original = connection.mappings.get(location=first).account
    order = create_order(school=connection.profile.school, purpose="booking", source_key="mapping-history",
        total_cents=10000, deposit_cents=2500, description="Reservation", account=original)
    service.update_locations(request, connection.profile.school.slug, connection.revision,
        mappings={f"location_{first.pk}": "LOC2", f"location_{second.pk}": ""})
    order.refresh_from_db()
    original.refresh_from_db()
    assert order.account_id == original.pk and original.location_reference == "LOC1" and original.enabled
    assert connection.mappings.get(location=first).account_id != original.pk
    assert not connection.mappings.filter(location=second).exists()


@pytest.mark.parametrize("change", ["unknown", "foreign", "omitted", "stale"])
def test_invalid_mapping_is_atomic(mapped, change):
    connection, first, second, request, _ = mapped
    data = {f"location_{first.pk}": "LOC2", f"location_{second.pk}": "LOC1"}
    revision = connection.revision
    if change == "unknown":
        data[f"location_{second.pk}"] = "FOREIGN"
    elif change == "foreign":
        data["location_999999"] = "LOC1"
    elif change == "omitted":
        del data[f"location_{second.pk}"]
    else:
        revision -= 1
    with pytest.raises(ValidationError):
        service.update_locations(request, connection.profile.school.slug, revision, mappings=data)
    assert connection.mappings.get(location=first).account.location_reference == "LOC1"
    assert connection.mappings.get(location=second).account.location_reference == "LOC2"


@pytest.mark.parametrize("change", ["disconnected", "expired", "unmapped", "foreign"])
def test_no_fallback_to_shared_credentials(mapped, change):
    connection, first, second, _, _ = mapped
    profile = connection.profile
    profile.payment_provider = "square"
    PaymentAccount.objects.create(school=profile.school, provider="square", scope="platform_demo",
        reference="SHARED", location_reference="SHAREDLOCATION", enabled=True)
    if change == "disconnected":
        SquareConnection.objects.filter(pk=connection.pk).update(status="disconnected")
    elif change == "expired":
        SquareConnection.objects.filter(pk=connection.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    elif change == "unmapped":
        connection.mappings.filter(location=first).delete()
    else:
        other = School.objects.create(slug="foreign-mapping", is_demo=True)
        PaymentAccount.objects.filter(pk=connection.mappings.get(location=first).account_id).update(school=other)
    with pytest.raises(ValidationError):
        booking_account(profile, True, first)
    assert booking_account(profile, False, first) is None


def test_stale_network_result_cannot_overwrite_connection(mapped):
    connection, _, _, request, listing = mapped
    def changed(current):
        SquareConnection.objects.filter(pk=current.pk).update(revision=current.revision + 1, status="disconnected")
        return []
    listing.side_effect = changed
    with pytest.raises(ValidationError, match="changed"):
        service.update_locations(request, connection.profile.school.slug, connection.revision)
    assert connection.mappings.count() == 2


@pytest.mark.parametrize("role,all_locations", [("viewer", True), ("scheduler", True), ("owner", False)])
def test_mapping_requires_school_wide_owner(client, setup, mapped, role, all_locations):
    connection = mapped[0]
    setup[0].role, setup[0].all_locations = role, all_locations
    setup[0].save()
    assert client.post(url(setup), {"action": "locations", "revision": connection.revision}, secure=True).status_code == 404
    assert mapped[4].call_count == 1


def test_permission_removed_during_location_fetch_cannot_save(mapped, setup):
    connection, _, _, request, listing = mapped
    def revoked(current):
        setup[0].role = "viewer"
        setup[0].save()
        return []
    listing.side_effect = revoked
    with pytest.raises(Http404):
        service.update_locations(request, connection.profile.school.slug, connection.revision)
    assert connection.mappings.count() == 2


def test_location_request_has_no_extra_database_transaction(mapped):
    connection, _, _, request, listing = mapped
    depth = len(database.atomic_blocks)
    def outside(current):
        assert len(database.atomic_blocks) == depth
        return []
    listing.side_effect = outside
    service.update_locations(request, connection.profile.school.slug, connection.revision)


def test_superadmin_provider_selection_uses_oauth_not_shared_token(mapped, monkeypatch):
    connection, first, _, _, _ = mapped
    operator = get_user_model().objects.create_superuser("square-operator", password="local")
    legacy = Mock(side_effect=AssertionError("Must not use shared token"))
    monkeypatch.setattr(square, "verified_location", legacy)
    save_provider(school=connection.profile.school, provider="square", expected_provider="stripe", actor=operator)
    connection.profile.refresh_from_db()
    assert booking_account(connection.profile, True, first).scope == "connected"
    assert not legacy.called


def test_stripe_uniqueness_and_square_binding_uniqueness_preserved(mapped):
    connection, first, _, _, _ = mapped
    account = connection.mappings.get(location=first).account
    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAccount.objects.create(school=account.school, provider="square", scope="connected", environment="test",
            reference=account.reference, location_reference=account.location_reference)
    PaymentAccount.objects.create(school=account.school, reference="acct_unique")
    with pytest.raises(IntegrityError), transaction.atomic():
        PaymentAccount.objects.create(school=account.school, reference="acct_second")


@pytest.mark.parametrize("status,currency,country,capabilities,accepted", [
    ("ACTIVE", "USD", "US", ["CREDIT_CARD_PROCESSING"], True),
    ("INACTIVE", "USD", "US", ["CREDIT_CARD_PROCESSING"], False),
    ("ACTIVE", "CAD", "CA", ["CREDIT_CARD_PROCESSING"], False),
    ("ACTIVE", "USD", "US", [], False),
])
def test_only_eligible_locations_are_offered(client, setup, monkeypatch, status, currency, country, capabilities, accepted):
    connection = connected(client, setup)
    monkeypatch.setattr(square, "request", Mock(return_value={"locations": [{"id": "L1", "name": "Studio",
        "merchant_id": connection.merchant_id, "status": status, "currency": currency, "country": country,
        "capabilities": capabilities}]}))
    assert bool(square.list_locations(connection)) == accepted


def test_foreign_square_location_rejected(client, setup, monkeypatch):
    connection = connected(client, setup)
    monkeypatch.setattr(square, "request", Mock(return_value={"locations": [{"id": "L1", "merchant_id": "OTHER"}]}))
    with pytest.raises(ValidationError, match="belong"):
        square.list_locations(connection)


def test_connected_transport_uses_encrypted_seller_token(mapped, monkeypatch, settings):
    connection, first, _, _, _ = mapped
    settings.PAYMENTS_SQUARE_TEST_ACCESS_TOKEN = "wrong-platform-token"
    account = connection.mappings.get(location=first).account
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = json.dumps({"payment": {"id": "P1"}}).encode()
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(square, "build_opener", Mock(return_value=opener))
    square.account_request(account, "GET", "/payments/P1")
    sent = opener.open.call_args.args[0]
    assert sent.full_url == square.API_ROOT + "/payments/P1"
    assert sent.get_header("Authorization") == "Bearer access-secret"
    SquareConnection.objects.filter(pk=connection.pk).update(status="disconnected")
    with pytest.raises(ValidationError):
        square.account_request(account, "POST", "/payments", {})
    assert opener.open.call_count == 1


@pytest.fixture
def oauth_attempt(attempt, settings):
    settings.PAYMENTS_SQUARE_TEST_APPLICATION_SECRET = "oauth-app-secret"
    settings.PAYMENTS_SQUARE_TEST_OAUTH_REDIRECT_URI = "https://testserver/bookings/square/oauth/callback/"
    settings.BOOKINGS_INTEGRATION_KEYS = [Fernet.generate_key()]
    account = attempt.account
    account.scope = "connected"
    account.save()
    connection = SquareConnection.objects.create(profile=attempt.order.reservation.profile, environment="test",
        status="connected", merchant_id=account.reference, application_id=settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID,
        expires_at=timezone.now() + timedelta(days=10))
    connection.credential_ciphertext = vault.seal(connection, json.dumps({"access_token": "seller-token",
        "refresh_token": "refresh-token", "merchant_id": connection.merchant_id, "application_id": connection.application_id}))
    connection.save()
    return attempt


def test_oauth_deposit_settles_once(oauth_attempt, monkeypatch, client):
    scenarios.test_payment_deposit_is_verified_once_without_touching_school(oauth_attempt, monkeypatch, client)


def test_oauth_decline_can_retry(oauth_attempt, monkeypatch, client):
    scenarios.test_decline_can_retry_without_a_ledger_entry(oauth_attempt, monkeypatch, client)


def test_oauth_timeout_recovers_without_second_charge(oauth_attempt, monkeypatch):
    scenarios.test_timeout_blocks_resubmission_and_recovers_without_new_charge(oauth_attempt, monkeypatch)


def test_oauth_payment_webhook_replay(oauth_attempt, monkeypatch, settings, client):
    scenarios.test_webhook_signature_account_and_replay(oauth_attempt, monkeypatch, settings, client)


def test_oauth_refund_survives_provider_switch(oauth_attempt, booking_member, inventory, monkeypatch, settings):
    scenarios.test_provider_switch_keeps_original_checkout_and_refund(oauth_attempt, booking_member, inventory, monkeypatch, settings)


def test_oauth_refund_webhook_replay(oauth_attempt, booking_member, monkeypatch, client, settings):
    scenarios.test_refund_webhook_verifies_account_and_settles_once(oauth_attempt, booking_member, monkeypatch, client, settings)


def test_balance_after_mapping_change_uses_original_location(oauth_attempt, monkeypatch):
    from scheduling.checkout import prepare_booking_checkout
    scenarios.pay(oauth_attempt, monkeypatch)
    reservation = oauth_attempt.order.reservation
    connection = SquareConnection.objects.get(profile=reservation.profile)
    new_account = PaymentAccount.objects.create(school=oauth_attempt.account.school, provider="square", scope="connected",
        reference=connection.merchant_id, location_reference="NEWLOCATION", enabled=True)
    SquareLocationMapping.objects.create(connection=connection, location=reservation.room.location, account=new_account)
    balance = prepare_booking_checkout(reservation, oauth_attempt.success_url)
    assert balance.account_id == oauth_attempt.account_id
    assert balance.amount_cents == 7500
    call = scenarios.pay(balance, monkeypatch)
    assert call.call_args.args[2]["location_id"] == "LTEST"
