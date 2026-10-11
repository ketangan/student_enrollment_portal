"""Explicit disposable PostgreSQL, no .env credentials, no production integrations."""
import os
from unittest.mock import patch

from .safety import test_database, test_environment

_database = test_database(os.environ.get(
    "PONTORA_TEST_DATABASE_URL", "postgresql://localhost:5432/test_pontora_local"
))
os.environ.update(test_environment(os.environ))
with patch("dotenv.load_dotenv", return_value=False):
    from config.settings import *  # noqa: F403

DATABASES = {"default": _database}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
RATELIMIT_ENABLE = False
