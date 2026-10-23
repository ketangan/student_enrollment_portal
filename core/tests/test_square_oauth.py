import io
import json
from dataclasses import replace
from datetime import timedelta
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.payments import square_oauth as oauth


@pytest.fixture
def application():
    return oauth.Application("test", "sandbox-sq0idb-example", "app-secret",
                             "http://localhost:8002/payments/square/callback/")


@pytest.fixture
def token_result():
    return {"token_type": "bearer", "merchant_id": "MERCHANT1", "access_token": "access-secret",
            "refresh_token": "refresh-secret", "expires_at": (timezone.now() + timedelta(days=30)).isoformat()}


def status_for(application, result):
    return {"client_id": application.application_id, "merchant_id": result["merchant_id"],
            "expires_at": result["expires_at"], "scopes": list(oauth.SCOPES)}


def responses(monkeypatch, *values):
    items = []
    for value in values:
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = value if isinstance(value, bytes) else json.dumps(value).encode()
        items.append(response)
    opener = Mock(open=Mock(side_effect=items))
    monkeypatch.setattr(oauth, "build_opener", Mock(return_value=opener))
    return opener


@pytest.mark.parametrize("environment,app_id,redirect,host", [
    ("test", "sandbox-sq0idb-example", "http://localhost:8002/callback/", "connect.squareupsandbox.com"),
    ("live", "sq0idp-example", "https://app.example.com/callback/", "connect.squareup.com"),
])
def test_authorization_uses_explicit_environment_and_minimal_permissions(environment, app_id, redirect, host):
    app = oauth.Application(environment, app_id, "private-secret", redirect)
    url = urlsplit(app.authorization_url("n" * 43))
    values = parse_qs(url.query)
    assert url.netloc == host and url.path == "/oauth2/authorize"
    assert values == {"client_id": [app_id], "redirect_uri": [redirect], "session": ["false"],
                      "state": ["n" * 43], "scope": [" ".join(oauth.SCOPES)]}
    assert "private-secret" not in repr(app) and "private-secret" not in url.query


@pytest.mark.parametrize("changes", [
    {"environment": "production"}, {"environment": "live"}, {"application_id": "sq0idp-live"},
    {"application_id": "sandbox-sq0idb-../bad"}, {"secret": ""}, {"secret": "bad\nsecret"},
    {"redirect_uri": "http://remote.example/callback/"}, {"redirect_uri": "https://u:p@host/callback/"},
    {"redirect_uri": "https://host/callback/#fragment"}, {"redirect_uri": "https://host/callback/?school=1"},
    {"redirect_uri": "/callback/"}, {"redirect_uri": "https://host"},
])
def test_invalid_configuration_never_falls_back(application, changes):
    with pytest.raises(ValidationError):
        replace(application, **changes)


@pytest.mark.parametrize("state", [None, "short", "x" * 129, "x" * 32 + "&other=1"])
def test_state_must_be_an_opaque_nonce(application, state):
    with pytest.raises(ValidationError):
        application.authorization_url(state)


@pytest.mark.parametrize("live", [False, True])
def test_exchange_verifies_token_and_hides_credentials(application, token_result, monkeypatch, live):
    if live:
        application = oauth.Application("live", "sq0idp-example", "app-secret", "https://app.example.com/callback/")
    opener = responses(monkeypatch, token_result, status_for(application, token_result))
    grant = application.exchange("authorization-code")
    assert grant.merchant_id == "MERCHANT1" and grant.access_token == "access-secret"
    assert grant.refresh_token == "refresh-secret"
    assert "secret" not in repr(grant)
    first, second = [call.args[0] for call in opener.open.call_args_list]
    assert first.full_url == oauth.HOSTS[application.environment] + "/oauth2/token"
    assert json.loads(first.data)["grant_type"] == "authorization_code"
    assert json.loads(first.data)["client_secret"] == "app-secret"
    assert second.get_header("Authorization") == "Bearer access-secret"
    assert all(call.kwargs["timeout"] == 10 for call in opener.open.call_args_list)


def test_refresh_and_revoke_keep_same_merchant(application, token_result, monkeypatch):
    opener = responses(monkeypatch, token_result, status_for(application, token_result), {"success": True})
    assert application.refresh("refresh-secret", merchant_id="MERCHANT1").merchant_id == "MERCHANT1"
    assert json.loads(opener.open.call_args_list[0].args[0].data)["grant_type"] == "refresh_token"
    application.revoke("access-secret")
    request = opener.open.call_args.args[0]
    assert request.get_header("Authorization") == "Client app-secret"
    assert json.loads(request.data)["revoke_only_access_token"] is False


@pytest.mark.parametrize("changes", [
    {"token_type": "other"}, {"merchant_id": "../wrong"}, {"access_token": ""},
    {"refresh_token": ""}, {"expires_at": None}, {"expires_at": "bad"},
    {"expires_at": "2000-01-01T00:00:00Z"}, {"expires_at": "2099-01-01T00:00:00"},
    {"expires_at": "2099-99-99T00:00:00Z"},
])
def test_invalid_grants_fail_closed(application, token_result, monkeypatch, changes):
    responses(monkeypatch, {**token_result, **changes})
    with pytest.raises(ValidationError):
        application.exchange("code")


@pytest.mark.parametrize("changes", [
    {"client_id": "OTHER"}, {"merchant_id": "OTHER"}, {"scopes": []}, {"scopes": None},
    {"scopes": [{}]}, {"expires_at": "2099-01-01T00:00:00Z"},
])
def test_token_introspection_must_match(application, token_result, monkeypatch, changes):
    responses(monkeypatch, token_result, {**status_for(application, token_result), **changes})
    with pytest.raises(ValidationError):
        application.exchange("code")


def test_refresh_cannot_change_merchant(application, token_result, monkeypatch):
    responses(monkeypatch, token_result)
    with pytest.raises(ValidationError):
        application.refresh("refresh-secret", merchant_id="OTHER")


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"errors":[{"detail":"private-secret"}]}', b"x" * 65537])
def test_bad_responses_are_bounded_and_redacted(application, monkeypatch, raw):
    responses(monkeypatch, raw)
    with pytest.raises(ValidationError) as exc:
        application.exchange("code")
    assert "private-secret" not in str(exc.value)


@pytest.mark.parametrize("error", [OSError("private-secret"), ValidationError("private-secret"),
    HTTPError("https://square", 401, "private-secret", {}, io.BytesIO(b"private-secret"))])
def test_network_errors_are_redacted_without_retry(application, monkeypatch, error):
    opener = Mock(open=Mock(side_effect=error))
    monkeypatch.setattr(oauth, "build_opener", Mock(return_value=opener))
    with pytest.raises(ValidationError) as exc:
        application.exchange("code")
    assert "private-secret" not in str(exc.value) and opener.open.call_count == 1


def test_invalid_inputs_and_failed_revocation(application, monkeypatch):
    for call in (lambda: application.exchange(""), lambda: application.exchange("x" * 192),
                 lambda: application.refresh("", merchant_id="MERCHANT1"),
                 lambda: application.refresh("token", merchant_id="../bad"), lambda: application.revoke("")):
        with pytest.raises(ValidationError):
            call()
    responses(monkeypatch, {"success": False})
    with pytest.raises(ValidationError):
        application.revoke("token")
