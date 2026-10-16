from urllib.parse import urlsplit
import re

import dj_database_url


def test_database(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Tests require an explicit loopback PostgreSQL database.")
    name = parsed.path.removeprefix("/")
    if not re.fullmatch(r"test_pontora_[a-z0-9_]+", name) or parsed.query or parsed.fragment:
        raise ValueError("Use a disposable test_pontora_* database without connection options.")
    config = dj_database_url.parse(url, conn_max_age=0)
    config["TEST"] = {"NAME": name}
    return config


def test_environment(current):
    environment = dict(current)
    for key in environment:
        if key.startswith(("STRIPE_", "PAYMENTS_STRIPE_", "PAYMENTS_SQUARE_", "BOOKING_HOSTED_", "BOOKING_DEMO_STRIPE_", "RESEND_", "SENTRY_", "GOOGLE_", "ANTHROPIC_", "OPENAI_", "RENDER_", "REDIS_", "MOCK_TEMPLATE_", "BOOKINGS_INTEGRATION_")):
            environment[key] = ""
    environment.update(DJANGO_ENV="local", DJANGO_DEBUG="False", DJANGO_SECRET_KEY="local-tests-only",
        DATABASE_URL="sqlite:///:memory:", REDIS_URL="", SENTRY_DSN="", RESEND_EMAIL_API_KEY="",
        STRIPE_MODE="test", DEV_SKIP_PAYMENT="False", ALLOWED_HOSTS="localhost,127.0.0.1,testserver",
        ADMIN_URL="admin/", BASE_URL="http://localhost:8000", APP_BASE_URL="http://localhost:8000",
        DEMO_BASE_URL="http://localhost:8000")
    return environment
