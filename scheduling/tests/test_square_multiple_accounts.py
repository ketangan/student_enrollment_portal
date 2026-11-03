from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.utils import timezone

from core.models import School
from core.payments import square
from core.payments.models import PaymentAccount
from core.payments.settings import save_provider
from core.payments.square_oauth import Grant
from scheduling.integrations import square_connection as service
from scheduling.models import Location, SchedulingProfile, SquareConnection, SquareLocationMapping
from scheduling.reservations import booking_account
from .test_square_connection import setup, connected, finish, url
from .test_square_locations import mapped, mapping_data, oauth_attempt
from .test_square_payments import square_account, attempt
from .test_reservations import inventory
from . import test_square_payments as scenarios

pytestmark = pytest.mark.django_db


def add_account(client, setup, merchant="MERCHANT2", label="East account"):
    setup[2].return_value = Grant(merchant, timezone.now() + timedelta(days=30), f"token-{merchant}", "refresh")
    response = client.post(url(setup), {"action": "add", "label": label}, secure=True)
    assert response.status_code == 302 and response.url.startswith("https://connect.squareupsandbox.com/")
    state = parse_qs(urlsplit(response.url).query)["state"][0]
    assert finish(client, state).status_code == 302
    return SquareConnection.objects.get(profile=setup[0].profile, merchant_id=merchant)


@pytest.fixture
def multiple(client, setup, mapped):
    first, west, east, request, listing = mapped
    second = add_account(client, setup)
    listing.side_effect = lambda connection: [
        {"id": "LOC1", "name": "Square One"}, {"id": "LOC2", "name": "Square Two"}]
    service.update_locations(request, first.profile.school.slug, second.revision, connection_id=second.pk)
    second.refresh_from_db()
    return first, second, west, east, request, listing


def selection_data(first, second, west, east):
    data = mapping_data(first, {f"location_{west.pk}": "LOC1", f"location_{east.pk}": "LOC2"})
    data[f"location_{east.pk}"] = f"{second.pk}:LOC1"
    return data


def test_shared_separate_and_mixed_accounts_route_only_the_selected_studio(multiple):
    first, second, west, east, request, listing = multiple
    profile = first.profile
    profile.payment_provider = "square"
    assert booking_account(profile, True, west).reference == booking_account(profile, True, east).reference == first.merchant_id
    service.save_mappings(request, profile.school.slug, selection_data(first, second, west, east))
    assert booking_account(profile, True, west).reference == first.merchant_id
    assert booking_account(profile, True, east).reference == second.merchant_id
    third = Location.objects.create(profile=profile, name="Third")
    data = selection_data(first, second, west, east)
    data[f"location_{third.pk}"] = f"{second.pk}:LOC2"
    listing.reset_mock()
    service.save_mappings(request, profile.school.slug, data)
    assert listing.call_count == 2
    assert booking_account(profile, True, third).reference == second.merchant_id
    assert booking_account(profile, True, third).location_reference == "LOC2"
    assert SquareLocationMapping.objects.count() == 3


def test_ui_groups_accounts_and_scopes_every_action(client, setup, multiple):
    first, second, west, east, request, _ = multiple
    page = client.get(url(setup, "integrations"), secure=True)
    html = page.content.decode()
    assert 'optgroup label="East account"' in html
    assert "East account / Square One" in html and "Add Square account" in html
    assert f'name="connection_id" value="{first.pk}"' in html
    assert f'name="connection_id" value="{second.pk}"' in html
    assert "access-secret" not in html and "token-MERCHANT2" not in html
    before = first.credential_ciphertext
    response = client.post(url(setup), {"action": "rename", "connection_id": second.pk,
        "revision": second.revision, "label": "Eastern studios"}, secure=True, follow=True)
    assert b"Account name saved" in response.content
    first.refresh_from_db(); second.refresh_from_db()
    assert first.credential_ciphertext == before and first.label == ""
    assert second.label == "Eastern studios"
    for action in ("connect", "locations", "check", "disconnect", "rename"):
        assert client.post(url(setup), {"action": action, "revision": first.revision}, secure=True).status_code == 404


@pytest.mark.parametrize("bad_id", ["invalid", "00000000-0000-0000-0000-000000000000"])
def test_account_commands_reject_invalid_identifiers(client, setup, mapped, bad_id):
    assert client.post(url(setup), {"action": "connect", "connection_id": bad_id}, secure=True).status_code == 404


def test_account_and_mapping_cannot_cross_school_or_environment(client, setup, multiple):
    first, second, west, east, request, _ = multiple
    foreign_profile = SchedulingProfile.objects.create(school=School.objects.create(slug="isolated-school", is_demo=True))
    foreign = SquareConnection.objects.create(profile=foreign_profile, environment="test", merchant_id="FOREIGN")
    live = SquareConnection.objects.create(profile=first.profile, environment="live", merchant_id=first.merchant_id)
    for connection in (foreign, live):
        for action in ("connect", "locations", "check", "disconnect", "rename"):
            assert client.post(url(setup), {"action": action, "connection_id": connection.pk,
                "revision": connection.revision, "label": "Hacked"}, secure=True).status_code == 404
        data = selection_data(first, second, west, east)
        data[f"location_{east.pk}"] = f"{connection.pk}:LOC1"
        with pytest.raises(ValidationError):
            service.save_mappings(request, first.profile.school.slug, data)
    assert first.mappings.count() == 2 and not second.mappings.exists()
    html = client.get(url(setup, "integrations"), secure=True).content
    assert str(foreign.pk).encode() not in html and str(live.pk).encode() not in html


def test_duplicate_seller_does_not_replace_original_credentials(client, setup, mapped):
    original = mapped[0]
    before = original.credential_ciphertext
    response = client.post(url(setup), {"action": "add", "label": "Duplicate"}, secure=True)
    state = parse_qs(urlsplit(response.url).query)["state"][0]
    assert finish(client, state).status_code == 400
    original.refresh_from_db()
    assert original.status == "connected" and original.credential_ciphertext == before
    assert original.mappings.count() == 2


def test_disconnected_unselected_account_does_not_block_remapping(multiple):
    first, second, west, east, request, _ = multiple
    SquareConnection.objects.filter(pk=first.pk).update(status="disconnected")
    data = mapping_data(second, {f"location_{west.pk}": "LOC1", f"location_{east.pk}": "LOC2"})
    service.save_mappings(request, first.profile.school.slug, data)
    profile = first.profile
    profile.payment_provider = "square"
    assert booking_account(profile, True, west).reference == second.merchant_id
    assert not first.mappings.exists() and second.mappings.count() == 2


@pytest.mark.parametrize("change", ["revision", "permission", "removed_location", "disconnect"])
def test_changes_during_remote_verification_cannot_partially_remap(multiple, setup, change):
    from django.http import Http404
    first, second, west, east, request, listing = multiple
    data = selection_data(first, second, west, east)
    def changed(connection):
        if change == "revision":
            SquareConnection.objects.filter(pk=first.pk).update(revision=first.revision + 10)
        elif change == "permission":
            setup[0].role = "viewer"; setup[0].save()
        elif change == "removed_location":
            return []
        else:
            SquareConnection.objects.filter(pk=second.pk).update(status="disconnected")
        return [{"id": "LOC1", "name": "One"}, {"id": "LOC2", "name": "Two"}]
    listing.side_effect = changed
    with pytest.raises((ValidationError, Http404)):
        service.save_mappings(request, first.profile.school.slug, data)
    assert first.mappings.count() == 2 and not second.mappings.exists()


def test_mapping_revision_rejects_repeated_save_and_added_studios(multiple):
    first, second, west, east, request, _ = multiple
    data = selection_data(first, second, west, east)
    service.save_mappings(request, first.profile.school.slug, data)
    with pytest.raises(ValidationError):
        service.save_mappings(request, first.profile.school.slug, data)
    data = selection_data(first, second, west, east)
    Location.objects.create(profile=first.profile, name="Added while editing")
    with pytest.raises(ValidationError):
        service.save_mappings(request, first.profile.school.slug, data)


def test_one_mapping_per_studio_per_environment(multiple):
    first, second, west, _, _, _ = multiple
    account = first.mappings.get(location=west).account
    with pytest.raises(IntegrityError), transaction.atomic():
        SquareLocationMapping.objects.create(connection=second, location=west, account=account, environment="test")


def test_operator_activation_and_disconnect_are_account_specific(client, setup, multiple):
    first, second, west, east, request, _ = multiple
    service.save_mappings(request, first.profile.school.slug, selection_data(first, second, west, east))
    operator = get_user_model().objects.create_superuser("operator", password="local")
    save_provider(school=first.profile.school, provider="square", expected_provider="stripe", actor=operator)
    second.refresh_from_db()
    response = client.post(url(setup), {"action": "disconnect", "connection_id": second.pk,
        "revision": second.revision}, secure=True, follow=True)
    assert b"Square disconnected" in response.content
    first.profile.refresh_from_db()
    assert booking_account(first.profile, True, west).reference == first.merchant_id
    with pytest.raises(ValidationError):
        booking_account(first.profile, True, east)
    assert setup[4].call_args.args == ("token-MERCHANT2",)


def test_existing_balance_and_refund_keep_original_seller_after_remapping(oauth_attempt, monkeypatch, booking_member, settings):
    from scheduling.checkout import prepare_booking_checkout, approve_refund
    from core.payments import refunds
    from scheduling.reservations import cancel_reservation
    from uuid import uuid4
    scenarios.pay(oauth_attempt, monkeypatch)
    reservation = oauth_attempt.order.reservation
    original = oauth_attempt.account
    replacement = PaymentAccount.objects.create(school=original.school, environment="test", provider="square",
        scope="connected", reference="REPLACEMENT", location_reference="OTHER", enabled=True)
    connection = SquareConnection.objects.create(profile=reservation.profile, environment="test", merchant_id="REPLACEMENT")
    SquareLocationMapping.objects.create(connection=connection, location=reservation.room.location, account=replacement)
    balance = prepare_booking_checkout(reservation, oauth_attempt.success_url)
    assert balance.account_id == original.pk
    payment = scenarios.pay(balance, monkeypatch)
    assert payment.call_args.args[2]["location_id"] == original.location_reference
    assert service.payment_connection(balance.account).merchant_id == original.reference
    cancel_reservation(booking_member.user, original.school.slug, reservation.public_id)
    refund = approve_refund(booking_member.user, original.school.slug, reservation.public_id, oauth_attempt.public_id,
        amount_cents=1000, key=uuid4(), reason="Canceled")
    transport = Mock(return_value={"refund": {"id": "REFUND_ORIGINAL", "status": "COMPLETED",
        "payment_id": oauth_attempt.provider_intent, "amount_money": {"amount": 1000, "currency": "USD"},
        "location_id": original.location_reference, "reason": square.refund_reason(refund)}})
    monkeypatch.setattr(square, "request", transport)
    assert refunds.process(refund, force=True)
    refund.refresh_from_db()
    assert refund.state == "succeeded"
    assert refund.attempt.account_id == original.pk


@pytest.mark.django_db(transaction=True)
def test_concurrent_mapping_saves_have_one_winner(multiple, monkeypatch):
    first, second, west, east, request, _ = multiple
    barrier = Barrier(2)
    data = selection_data(first, second, west, east)
    def listed(connection):
        if connection.pk == first.pk:
            barrier.wait(timeout=15)
        return [{"id": "LOC1", "name": "One"}, {"id": "LOC2", "name": "Two"}]
    monkeypatch.setattr(square, "list_locations", listed)
    def save():
        close_old_connections()
        try:
            service.save_mappings(request, first.profile.school.slug, data)
            return "saved"
        except ValidationError:
            return "stale"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: save(), range(2)))
    assert sorted(outcomes) == ["saved", "stale"]
    assert SquareLocationMapping.objects.count() == 2


@pytest.mark.parametrize("label", ["", " " * 3, "a" * 81])
def test_invalid_account_names_do_not_connect_or_rename(client, setup, mapped, label):
    original = mapped[0]
    for action in ("add", "rename"):
        response = client.post(url(setup), {"action": action, "label": label, "connection_id": original.pk,
            "revision": original.revision}, secure=True, follow=True)
        assert b"Enter an account name" in response.content
    assert SquareConnection.objects.count() == 1
    original.refresh_from_db()
    assert original.label == ""


def test_stale_rename_and_pending_add_do_not_create_extra_accounts(client, setup, mapped):
    original = mapped[0]
    response = client.post(url(setup), {"action": "rename", "label": "Changed", "connection_id": original.pk,
        "revision": original.revision - 1}, secure=True, follow=True)
    assert b"settings changed" in response.content
    for label in ("New account", "Other name"):
        response = client.post(url(setup), {"action": "add", "label": label}, secure=True)
        state = parse_qs(urlsplit(response.url).query)["state"][0]
        assert finish(client, state, error="access_denied").status_code == 302
    assert SquareConnection.objects.count() == 2
    assert SquareConnection.objects.get(merchant_id="").label == "Other name"


def test_unmapping_all_does_not_contact_sellers_or_change_payment_accounts(multiple):
    first, _, west, east, request, listing = multiple
    accounts = list(PaymentAccount.objects.values())
    listing.reset_mock()
    service.save_mappings(request, first.profile.school.slug, mapping_data(first,
        {f"location_{west.pk}": "", f"location_{east.pk}": ""}))
    assert not SquareLocationMapping.objects.exists() and not listing.called
    assert list(PaymentAccount.objects.values()) == accounts


@pytest.mark.django_db(transaction=True)
def test_upgrade_preserves_legacy_bindings_and_enrollment_school():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0051_workshop_price")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="square-upgrade", is_demo=True)
        enrollment = apps.get_model("core", "School").objects.create(slug="enrollment-unchanged", plan="custom")
        enrollment_before = apps.get_model("core", "School").objects.filter(pk=enrollment.pk).values().get()
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=True)
        location = apps.get_model("scheduling", "Location").objects.create(profile=profile, name="Existing studio")
        rows = []
        for environment in ("test", "live"):
            seller = apps.get_model("scheduling", "SquareConnection").objects.create(profile=profile,
                environment=environment, merchant_id="LEGACY", credential_ciphertext="existing-encrypted-value", revision=8)
            account = apps.get_model("payments", "PaymentAccount").objects.create(school=school, environment=environment,
                provider="square", scope="connected", reference="LEGACY", location_reference="OLD_LOCATION", enabled=True)
            mapping = apps.get_model("scheduling", "SquareLocationMapping").objects.create(
                connection=seller, location=location, account=account)
            order = apps.get_model("payments", "PaymentOrder").objects.create(school=school, account=account,
                environment=environment, purpose="booking", source_key=environment, merchant_reference="LEGACY",
                total_cents=6500, deposit_cents=0)
            rows.append((seller.pk, mapping.pk, account.pk, order.pk, environment))
        stripe = apps.get_model("payments", "PaymentAccount").objects.create(school=enrollment, reference="acct_existing", enabled=True)
        stripe_before = apps.get_model("payments", "PaymentAccount").objects.filter(pk=stripe.pk).values().get()
        MigrationExecutor(connection).migrate(latest)
        from core.payments.models import PaymentOrder
        for seller_pk, mapping_pk, account_pk, order_pk, environment in rows:
            seller = SquareConnection.objects.get(pk=seller_pk)
            assert seller.credential_ciphertext == "existing-encrypted-value" and seller.revision == 8 and seller.label == ""
            mapping = SquareLocationMapping.objects.get(pk=mapping_pk)
            assert mapping.connection_id == seller_pk and mapping.account_id == account_pk and mapping.environment == environment
            assert PaymentOrder.objects.get(pk=order_pk).account_id == account_pk
        assert School.objects.filter(pk=enrollment.pk).values().get() == enrollment_before
        assert PaymentAccount.objects.filter(pk=stripe.pk).values().get() == stripe_before
    finally:
        MigrationExecutor(connection).migrate(latest)


def test_mapping_form_does_not_query_per_studio(multiple, django_assert_num_queries):
    first, _, _, _, _, _ = multiple
    Location.objects.bulk_create([Location(profile=first.profile, name=f"Studio {i}") for i in range(12)])
    with django_assert_num_queries(3):
        form = service.MappingForm(first.profile, "test")
        assert len(form.visible_fields()) == 14
