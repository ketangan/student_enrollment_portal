import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import default_storage

from scheduling.guide_storage import guide_storage


def test_unconfigured_guides_keep_existing_local_storage(settings):
    settings.BOOKINGS_GUIDE_R2 = {}
    assert guide_storage() is default_storage


@pytest.mark.parametrize("overrides", [
    {"secret_key": ""},
    {"endpoint_url": "http://account.r2.cloudflarestorage.com"},
    {"endpoint_url": "https://example.com"},
    {"endpoint_url": "https://account.r2.cloudflarestorage.com/path"},
    {"endpoint_url": "https://user@account.r2.cloudflarestorage.com"},
])
def test_incomplete_or_invalid_r2_fails_closed(settings, overrides):
    settings.BOOKINGS_GUIDE_R2 = {
        "access_key": "test", "secret_key": "test",
        "endpoint_url": "https://account.r2.cloudflarestorage.com",
        "bucket_name": "test-guides", **overrides,
    }
    with pytest.raises(ImproperlyConfigured):
        guide_storage()


def test_r2_is_private_and_does_not_change_default_storage(settings):
    settings.BOOKINGS_GUIDE_R2 = {
        "access_key": "test", "secret_key": "test",
        "endpoint_url": "https://account.r2.cloudflarestorage.com",
        "bucket_name": "test-guides",
    }
    original = settings.STORAGES.copy()
    storage = guide_storage()
    assert storage is not default_storage
    assert storage.bucket_name == "test-guides"
    assert storage.default_acl is None
    assert storage.custom_domain is None
    assert storage.querystring_auth
    assert not storage.file_overwrite
    assert storage.client_config.connect_timeout == 5
    assert settings.STORAGES == original
