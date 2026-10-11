"""Authenticated, context-bound encryption with explicit key rotation support."""

import json
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.views.decorators.debug import sensitive_variables


ERROR = "Credential storage is unavailable. Ask the operator to check the integration encryption keys."


@sensitive_variables()
def cipher():
    keys = getattr(settings, "BOOKINGS_INTEGRATION_KEYS", tuple(filter(None,
        (key.strip() for key in os.environ.get("BOOKINGS_INTEGRATION_KEYS", "").split(",")))))
    try:
        if not isinstance(keys, (list, tuple)) or not keys:
            raise ValueError
        return MultiFernet([Fernet(key) for key in keys])
    except (ValueError, TypeError):
        raise ValidationError(ERROR) from None


def binding(connection):
    return [connection.profile_id, str(connection.pk), connection.provider, connection.environment]


@sensitive_variables()
def seal(connection, credential):
    envelope = {"binding": binding(connection), "credential": credential}
    return cipher().encrypt(json.dumps(envelope).encode()).decode()


@sensitive_variables()
def unseal(connection):
    try:
        envelope = json.loads(cipher().decrypt(connection.credential_ciphertext.encode()))
        if envelope["binding"] != binding(connection) or not isinstance(envelope["credential"], str):
            raise ValueError
        return envelope["credential"]
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise ValidationError(ERROR) from None
