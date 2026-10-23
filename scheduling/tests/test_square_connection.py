import io
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.http import Http404
from django.db import connection as database, IntegrityError, transaction
from django.db import close_old_connections
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import PaymentAccount
from core.payments.square_oauth import Grant
from scheduling.integrations import square_connection as service, vault
from scheduling.models import SchedulingProfile, SchedulingMembership, SquareConnection, SquareAuthorization

pytestmark = pytest.mark.django_db


@pytest.fixture
def setup(client, booking_member, settings, monkeypatch):
    school = booking_member.profile.school
    school.is_demo = True
    school.save()
    settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID = "sandbox-sq0idb-example"
    settings.PAYMENTS_SQUARE_TEST_APPLICATION_SECRET = "test-app-secret"
    settings.PAYMENTS_SQUARE_TEST_OAUTH_REDIRECT_URI = "https://testserver" + reverse("scheduling:square_oauth_callback")
    settings.BOOKINGS_INTEGRATION_KEYS = [Fernet.generate_key()]
    grant = Grant("MERCHANT1", timezone.now() + timedelta(days=30), "access-secret", "refresh-secret")
    app = service.application("test")
    exchange = Mock(return_value=grant)
    refresh = Mock(return_value=grant)
    revoke = Mock()
    monkeypatch.setattr(type(app), "exchange", exchange)
    monkeypatch.setattr(type(app), "refresh", refresh)
    monkeypatch.setattr(type(app), "revoke", revoke)
    client.force_login(booking_member.user)
    return booking_member, grant, exchange, refresh, revoke


def url(setup, name="square_connection_update"):
    return reverse("scheduling:" + name, kwargs={"school_slug": setup[0].profile.school.slug})


def start(client, setup):
    response = client.post(url(setup), {"action": "connect"}, secure=True)
    assert response.status_code == 302 and response.url.startswith("https://connect.squareupsandbox.com/")
    return parse_qs(urlsplit(response.url).query)["state"][0]


def finish(client, state, **values):
    return client.get(reverse("scheduling:square_oauth_callback"), {"state": state, "code": "auth-code", **values}, secure=True)


def connected(client, setup):
    assert finish(client, start(client, setup)).status_code == 302
    return SquareConnection.objects.get(profile=setup[0].profile)


def test_connection_is_encrypted_scoped_and_does_not_enable_payments(client, setup):
    state = start(client, setup)
    pending = SquareAuthorization.objects.get()
    assert pending.state_hash != state and state not in pending.state_hash
    assert pending.session_hash != client.session.session_key
    assert finish(client, state).status_code == 302
    saved = SquareConnection.objects.get()
    assert saved.status == "connected" and saved.merchant_id == "MERCHANT1"
    assert "secret" not in saved.credential_ciphertext
    assert json.loads(vault.unseal(saved))["refresh_token"] == "refresh-secret"
    assert not PaymentAccount.objects.exists()
    setup[0].profile.refresh_from_db()
    assert setup[0].profile.payment_provider == "stripe"
    html = client.get(url(setup, "integrations"), secure=True).content
    assert b"Connected" in html and b"Check connection" in html
    assert b"secret" not in html and saved.credential_ciphertext.encode() not in html
    logs = str(list(AdminAuditLog.objects.values("changes", "extra")))
    assert "secret" not in logs and "auth-code" not in logs
    assert finish(client, state).status_code == 400
    assert setup[2].call_count == 1


def test_denial_consumes_state_without_provider_call(client, setup):
    state = start(client, setup)
    assert finish(client, state, error="access_denied").status_code == 302
    assert SquareAuthorization.objects.get().consumed_at
    assert not setup[2].called
    assert finish(client, state).status_code == 400


@pytest.mark.parametrize("change", ["expiry", "session", "user", "revision", "application", "role", "host", "environment"])
def test_callback_rejects_changed_authority(client, setup, settings, change):
    state = start(client, setup)
    if change == "expiry":
        SquareAuthorization.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    elif change == "session":
        client.logout()
        client.force_login(setup[0].user)
    elif change == "user":
        other = get_user_model().objects.create_user("different", password="local-test")
        client.force_login(other)
    elif change == "revision":
        start(client, setup)
    elif change == "application":
        settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID = "sandbox-sq0idb-other"
    elif change == "role":
        setup[0].role = "viewer"
        setup[0].save()
    elif change == "host":
        settings.PAYMENTS_SQUARE_TEST_OAUTH_REDIRECT_URI = "https://elsewhere.example/callback/"
    elif change == "environment":
        School.objects.filter(pk=setup[0].profile.school_id).update(is_demo=False)
    assert finish(client, state).status_code == 400
    assert not setup[2].called


@pytest.mark.parametrize("role,all_locations", [("viewer", True), ("scheduler", True), ("owner", False)])
def test_only_school_wide_owner_can_connect(client, setup, role, all_locations):
    setup[0].role, setup[0].all_locations = role, all_locations
    setup[0].save()
    assert client.post(url(setup), {"action": "connect"}, secure=True).status_code == 404
    response = client.get(url(setup, "integrations"), secure=True)
    assert response.status_code == 404
    request = RequestFactory().get(url(setup, "integrations"), secure=True)
    request.user = setup[0].user
    with pytest.raises(Http404):
        service.access(request, setup[0].profile.school.slug)
    assert not SquareConnection.objects.exists()


def test_no_session_bad_callback_and_wrong_origin(client, setup, settings):
    assert finish(client, "short").status_code == 400
    anonymous = Client()
    assert finish(anonymous, "x" * 43).status_code == 400
    settings.PAYMENTS_SQUARE_TEST_OAUTH_REDIRECT_URI = "https://different.example/callback/"
    response = client.post(url(setup), {"action": "connect"}, secure=True, follow=True)
    assert b"not configured for this site" in response.content
    assert not SquareConnection.objects.exists()


def test_missing_connection_and_invalid_revision(client, setup):
    assert client.post(url(setup), {"action": "check", "revision": "bad"}, secure=True).status_code == 302
    saved = SquareConnection.objects.create(profile=setup[0].profile, environment="test")
    with pytest.raises(ValidationError, match="Connect a Square account first"):
        service.maintain(saved.pk)


def test_malformed_configuration_is_recoverable(client, setup, settings):
    settings.PAYMENTS_SQUARE_TEST_OAUTH_REDIRECT_URI = "https://[invalid"
    response = client.post(url(setup), {"action": "connect"}, secure=True, follow=True)
    assert response.status_code == 200
    assert b"Square connection settings need to be checked" in response.content
    assert not SquareConnection.objects.exists()


def test_authority_removed_during_exchange_does_not_save_grant(client, setup):
    state = start(client, setup)
    def exchange(code):
        SchedulingMembership.objects.filter(pk=setup[0].pk).update(role="viewer")
        return setup[1]
    setup[2].side_effect = exchange
    assert finish(client, state).status_code == 400
    saved = SquareConnection.objects.get()
    assert saved.status == "disconnected" and not saved.credential_ciphertext and saved.operation is None


def test_busy_connection_blocks_callback_without_consuming_state(client, setup):
    state = start(client, setup)
    SquareConnection.objects.update(operation=uuid.uuid4(), operation_until=timezone.now() + timedelta(minutes=5))
    assert finish(client, state).status_code == 400
    assert SquareAuthorization.objects.get().consumed_at is None
    assert not setup[2].called


def test_successful_renewal_rotates_encryption_key(client, setup, settings):
    saved = connected(client, setup)
    old_key = settings.BOOKINGS_INTEGRATION_KEYS[0]
    new_key = Fernet.generate_key()
    settings.BOOKINGS_INTEGRATION_KEYS = [new_key, old_key]
    service.maintain(saved.pk)
    settings.BOOKINGS_INTEGRATION_KEYS = [new_key]
    saved.refresh_from_db()
    assert json.loads(vault.unseal(saved))["access_token"] == "access-secret"


def test_csrf_get_and_cross_school_denied(client, setup):
    assert client.get(url(setup), secure=True).status_code == 405
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(setup[0].user)
    assert csrf.post(url(setup), {"action": "connect"}, secure=True).status_code == 403
    other = School.objects.create(slug="other-square")
    SchedulingProfile.objects.create(school=other, enabled=True)
    assert client.post(reverse("scheduling:square_connection_update", kwargs={"school_slug": other.slug}),
                       {"action": "connect"}, secure=True).status_code == 404
    assert client.post(url(setup), {"action": "bad"}, secure=True).status_code == 400


def test_setup_unavailable_and_host_environment_guard(client, setup, settings):
    settings.BOOKINGS_INTEGRATION_KEYS = []
    page = client.get(url(setup, "integrations"), secure=True)
    assert b"Connection setup pending" in page.content and b"Connect Square" not in page.content
    assert client.post(url(setup), {"action": "connect"}, secure=True).status_code == 302
    assert not SquareConnection.objects.exists()
    settings.APP_BASE_URL = "https://testserver"
    settings.DEMO_BASE_URL = "https://demo.example"
    response = client.get(url(setup, "integrations"), secure=True)
    assert response.status_code == 302 and response.url.startswith("https://demo.example/")
    request = RequestFactory().get(url(setup, "integrations"), secure=True)
    request.user = setup[0].user
    with pytest.raises(Http404):
        service.access(request, setup[0].profile.school.slug)


def test_refresh_disconnect_and_stale_submission(client, setup):
    saved = connected(client, setup)
    response = client.post(url(setup), {"action": "check", "revision": saved.revision}, secure=True)
    assert response.status_code == 302 and setup[3].called
    old_revision = saved.revision
    saved.refresh_from_db()
    assert saved.revision > old_revision and saved.status == "connected"
    client.post(url(setup), {"action": "disconnect", "revision": old_revision}, secure=True)
    assert not setup[4].called
    client.post(url(setup), {"action": "disconnect", "revision": saved.revision}, secure=True)
    saved.refresh_from_db()
    assert saved.status == "disconnected" and saved.credential_ciphertext == ""
    assert saved.merchant_id == "MERCHANT1" and not saved.expires_at
    setup[4].assert_called_once_with("access-secret")


def test_failure_keeps_recovery_credentials_and_retry_disconnect(client, setup):
    saved = connected(client, setup)
    setup[3].side_effect = ValidationError("provider-secret")
    with pytest.raises(ValidationError, match="could not verify"):
        service.maintain(saved.pk)
    saved.refresh_from_db()
    assert saved.status == "attention" and saved.credential_ciphertext
    setup[4].side_effect = ValidationError("provider-secret")
    with pytest.raises(ValidationError):
        service.maintain(saved.pk, disconnect=True)
    saved.refresh_from_db()
    assert saved.status == "disconnecting" and saved.credential_ciphertext
    assert b"Retry disconnect" in client.get(url(setup, "integrations"), secure=True).content
    response = client.post(url(setup), {"action": "connect"}, secure=True, follow=True)
    assert b"Finish disconnecting" in response.content
    setup[4].side_effect = None
    service.maintain(saved.pk)
    saved.refresh_from_db()
    assert saved.status == "disconnected"


def test_reconnection_cannot_replace_original_seller(client, setup):
    saved = connected(client, setup)
    original = saved.credential_ciphertext
    setup[2].return_value = Grant("OTHER", setup[1].expires_at, "different-access", "different-refresh")
    assert finish(client, start(client, setup)).status_code == 400
    saved.refresh_from_db()
    assert saved.merchant_id == "MERCHANT1" and saved.credential_ciphertext == original


def test_lease_blocks_concurrent_actions_and_late_results(client, setup):
    saved = connected(client, setup)
    SquareConnection.objects.filter(pk=saved.pk).update(operation=uuid.uuid4(), operation_until=timezone.now() + timedelta(minutes=5))
    with pytest.raises(ValidationError, match="in progress"):
        service.maintain(saved.pk)
    assert client.post(url(setup), {"action": "connect"}, secure=True).status_code == 302
    saved.refresh_from_db()
    with pytest.raises(ValidationError):
        service._save_grant(saved.pk, uuid.uuid4(), service.application("test"), setup[1], None)
    service._failed(saved.pk, uuid.uuid4())
    saved.refresh_from_db()
    assert saved.status == "connected"


def test_renewal_job_is_bounded_and_reports_failures(client, setup):
    saved = connected(client, setup)
    SquareConnection.objects.filter(pk=saved.pk).update(next_check_at=timezone.now() - timedelta(seconds=1))
    assert service.due(20, "wrong-school") == []
    output = io.StringIO()
    call_command("refresh_square_connections", school=setup[0].profile.school.slug, stdout=output)
    assert "Checked 1" in output.getvalue()
    assert service.due(20) == []
    SquareConnection.objects.filter(pk=saved.pk).update(next_check_at=timezone.now() - timedelta(seconds=1))
    setup[3].side_effect = ValidationError("private-error")
    with pytest.raises(CommandError):
        call_command("refresh_square_connections", stdout=output)
    assert "private-error" not in output.getvalue()
    with pytest.raises(CommandError):
        call_command("refresh_square_connections", limit=0)


def test_status_expiry_and_due_check(client, setup):
    saved = connected(client, setup)
    saved.expires_at = timezone.now() - timedelta(seconds=1)
    assert saved.display_status == "Expired - reconnect"
    saved.expires_at = setup[1].expires_at
    saved.next_check_at = timezone.now() - timedelta(seconds=1)
    assert saved.display_status == "Verification due"


@pytest.mark.parametrize("change", ["app", "cipher", "environment", "payload"])
def test_stored_credentials_cannot_be_rebound(client, setup, settings, change):
    saved = connected(client, setup)
    if change == "app":
        settings.PAYMENTS_SQUARE_TEST_APPLICATION_ID = "sandbox-sq0idb-other"
    elif change == "cipher":
        saved.credential_ciphertext = "tampered"
        saved.save()
    elif change == "environment":
        School.objects.filter(pk=setup[0].profile.school_id).update(is_demo=False)
    else:
        saved.credential_ciphertext = vault.seal(saved, "[]")
        saved.save()
    with pytest.raises(ValidationError):
        service.maintain(saved.pk)
    assert not setup[3].called


def test_provider_calls_outside_lifecycle_transaction(client, setup):
    saved = connected(client, setup)
    # pytest's outer rollback transaction is expected; the service must add none.
    depth = len(database.atomic_blocks)
    def refresh(*args, **kwargs):
        assert len(database.atomic_blocks) == depth
        return setup[1]
    setup[3].side_effect = refresh
    service.maintain(saved.pk)


def test_seller_cannot_be_connected_to_two_schools(client, setup):
    connected(client, setup)
    other_school = School.objects.create(slug="other-seller-school", is_demo=True)
    other_profile = SchedulingProfile.objects.create(school=other_school, enabled=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        SquareConnection.objects.create(profile=other_profile, environment="test", merchant_id="MERCHANT1")


@pytest.mark.django_db(transaction=True)
def test_simultaneous_callback_exchanges_code_only_once(client, setup):
    state = start(client, setup)
    entered, release = Event(), Event()
    duplicate = Client()
    duplicate.cookies = client.cookies.copy()

    def exchange(code):
        entered.set()
        assert release.wait(timeout=10)
        return setup[1]

    def first_callback():
        close_old_connections()
        try:
            return finish(duplicate, state).status_code
        finally:
            close_old_connections()

    setup[2].side_effect = exchange
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(first_callback)
        try:
            assert entered.wait(timeout=10)
            assert finish(client, state).status_code == 400
        finally:
            release.set()
        assert future.result(timeout=10) == 302
    assert setup[2].call_count == 1
    assert SquareConnection.objects.get().status == "connected"
