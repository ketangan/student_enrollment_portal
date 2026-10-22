"""Dedicated guide storage; enrollment and static-file storage stay unchanged."""
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage


def guide_storage():
    options = settings.BOOKINGS_GUIDE_R2
    if not any(options.values()):
        return default_storage
    required = ("access_key", "secret_key", "endpoint_url", "bucket_name")
    if not all(options.get(key) for key in required):
        raise ImproperlyConfigured("All four BOOKINGS_GUIDE_R2 settings are required.")
    endpoint = urlsplit(options["endpoint_url"])
    if (endpoint.scheme != "https" or not endpoint.hostname
            or not endpoint.hostname.endswith(".r2.cloudflarestorage.com")
            or endpoint.username or endpoint.password or endpoint.port
            or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment):
        raise ImproperlyConfigured("Booking guide storage requires an HTTPS R2 endpoint.")
    from botocore.config import Config
    from storages.backends.s3 import S3Storage

    return S3Storage(
        **{key: options[key] for key in required},
        region_name="auto", signature_version="s3v4", default_acl=None,
        file_overwrite=False, custom_domain=None, querystring_auth=True,
        client_config=Config(connect_timeout=5, read_timeout=15,
            retries={"max_attempts": 2, "mode": "standard"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required"),
    )
