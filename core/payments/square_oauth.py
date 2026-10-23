"""Server-side Square authorization client; does not enable booking payments.

The connection workflow must consume its single-use authorization state before
exchanging a code, and persist returned grants through encrypted storage only.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.debug import sensitive_variables

from .square import API_VERSION, NoRedirect, identifier

SCOPES = ("MERCHANT_PROFILE_READ", "PAYMENTS_READ", "PAYMENTS_WRITE")
HOSTS = {"test": "https://connect.squareupsandbox.com", "live": "https://connect.squareup.com"}
ERROR = "Square authorization could not be verified. Reconnect the account or try again later."


@dataclass(frozen=True)
class Grant:
    merchant_id: str
    expires_at: datetime
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


@dataclass(frozen=True)
class Application:
    environment: str
    application_id: str
    secret: str = field(repr=False)
    redirect_uri: str

    def __post_init__(self):
        redirect = urlsplit(self.redirect_uri)
        prefix = "sandbox-sq0idb-" if self.environment == "test" else "sq0idp-"
        local_test = (self.environment == "test" and redirect.scheme == "http"
                      and redirect.hostname in {"localhost", "127.0.0.1", "::1"})
        if (self.environment not in HOSTS or not self.application_id.startswith(prefix)
                or not identifier(self.application_id) or not _token(self.secret)
                or not redirect.hostname or redirect.username or redirect.password
                or redirect.fragment or redirect.query or not redirect.path.startswith("/")
                or not (redirect.scheme == "https" or local_test)):
            raise ValidationError("Square authorization configuration is invalid.")

    def authorization_url(self, state):
        if not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", state):
            raise ValidationError("Square authorization state is invalid.")
        return HOSTS[self.environment] + "/oauth2/authorize?" + urlencode({
            "client_id": self.application_id, "redirect_uri": self.redirect_uri,
            "scope": " ".join(SCOPES), "session": "false", "state": state})

    @sensitive_variables()
    def exchange(self, code):
        if not _token(code, maximum=191):
            raise ValidationError(ERROR)
        return self._grant({"grant_type": "authorization_code", "code": code})

    @sensitive_variables()
    def refresh(self, refresh_token, *, merchant_id):
        if not _token(refresh_token) or not identifier(merchant_id):
            raise ValidationError(ERROR)
        return self._grant({"grant_type": "refresh_token", "refresh_token": refresh_token}, merchant_id)

    @sensitive_variables()
    def _request(self, path, payload, authorization=None):
        headers = {"Square-Version": API_VERSION, "Content-Type": "application/json"}
        if authorization:
            headers["Authorization"] = authorization
        request = Request(HOSTS[self.environment] + path, method="POST",
                          data=json.dumps(payload).encode(), headers=headers)
        try:
            with build_opener(NoRedirect).open(request, timeout=10) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("errors"):
                raise ValueError
        except (OSError, ValueError, ValidationError):
            # Provider errors and request objects can contain credentials.
            raise ValidationError(ERROR) from None
        return result

    @sensitive_variables()
    def _grant(self, payload, expected_merchant=None):
        result = self._request("/oauth2/token", {**payload, "client_id": self.application_id,
            "client_secret": self.secret, "redirect_uri": self.redirect_uri})
        expiry = _expiry(result.get("expires_at"))
        if (result.get("token_type") != "bearer" or not identifier(result.get("merchant_id"))
                or not _token(result.get("access_token")) or not _token(result.get("refresh_token"))
                or (expected_merchant is not None and result["merchant_id"] != expected_merchant)):
            raise ValidationError(ERROR)
        status = self._request("/oauth2/token/status", {}, "Bearer " + result["access_token"])
        scopes = status.get("scopes")
        if (status.get("client_id") != self.application_id
                or status.get("merchant_id") != result["merchant_id"]
                or not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes)
                or not set(SCOPES).issubset(scopes)
                or _expiry(status.get("expires_at")) != expiry):
            raise ValidationError(ERROR)
        return Grant(result["merchant_id"], expiry, result["access_token"], result["refresh_token"])

    @sensitive_variables()
    def revoke(self, access_token):
        if not _token(access_token):
            raise ValidationError(ERROR)
        result = self._request("/oauth2/revoke", {"client_id": self.application_id,
            "access_token": access_token, "revoke_only_access_token": False}, "Client " + self.secret)
        if result.get("success") is not True:
            raise ValidationError(ERROR)


def _token(value, maximum=1024):
    return isinstance(value, str) and 2 <= len(value) <= maximum and all(32 < ord(c) < 127 for c in value)


def _expiry(value):
    try:
        expiry = parse_datetime(value) if isinstance(value, str) else None
        if expiry is None or timezone.is_naive(expiry) or expiry <= timezone.now():
            raise ValueError
        return expiry
    except ValueError:
        raise ValidationError(ERROR) from None
