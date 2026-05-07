"""
Phase 16 — Production Readiness.

Covers:
  - Rate limiting is disabled in test environment (RATELIMIT_ENABLE=false)
    so public form views still return 200/302 under normal load
  - ratelimited_error_view returns 429 with correct content
  - Health check endpoint returns 200 {"status": "ok"}
  - LOGGING config is present in settings (no AttributeError)
  - django_ratelimit is installed and importable
  - Public apply + lead_capture views handle GET normally (no crash)
"""
from __future__ import annotations

import json

import pytest
from django.test import RequestFactory, override_settings
from django.urls import reverse

from core.tests.factories import SchoolFactory


# ── Health check ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_healthz_returns_200(client):
    """GET /healthz/ must return 200 with status=ok."""
    resp = client.get(reverse("healthz"))
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["status"] == "ok"


# ── Rate-limited error view ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_ratelimited_error_view_returns_429(client):
    """
    Calling ratelimited_error_view directly (simulating Django's handler429
    dispatch) must return HTTP 429 with the user-friendly page.
    """
    from core.views import ratelimited_error_view
    from django.test import RequestFactory

    factory = RequestFactory()
    req = factory.get("/")
    response = ratelimited_error_view(req)
    assert response.status_code == 429
    content = response.content.decode()
    assert "Too many requests" in content or "too many" in content.lower()


# ── RATELIMIT_ENABLE is False in tests ────────────────────────────────────────


def test_ratelimit_disabled_in_test_environment():
    """
    settings.RATELIMIT_ENABLE must be False during test runs.
    This ensures @ratelimit decorators are no-ops in the test suite.
    """
    from django.conf import settings
    assert settings.RATELIMIT_ENABLE is False, (
        "RATELIMIT_ENABLE must be False in tests to prevent false rate-limit failures"
    )


# ── django_ratelimit importable ───────────────────────────────────────────────


def test_django_ratelimit_importable():
    """django_ratelimit package must be importable (in requirements.txt)."""
    from django_ratelimit.decorators import ratelimit  # noqa: F401
    from django_ratelimit.exceptions import Ratelimited  # noqa: F401


# ── LOGGING config present ────────────────────────────────────────────────────


def test_logging_config_in_settings():
    """settings.LOGGING must be defined with at least a console handler."""
    from django.conf import settings
    assert hasattr(settings, "LOGGING"), "LOGGING not configured in settings"
    logging_cfg = settings.LOGGING
    assert "handlers" in logging_cfg
    assert "console" in logging_cfg["handlers"]
    assert "loggers" in logging_cfg
    assert "core" in logging_cfg["loggers"]


# ── Public form views work normally (rate limiting off) ───────────────────────


@pytest.mark.django_db
def test_apply_view_get_returns_200_or_404(client):
    """
    GET /schools/<slug>/apply/ either renders the form (200) or returns 404
    for unknown slugs. Must not crash with 500.
    """
    resp = client.get(reverse("apply", kwargs={"school_slug": "nonexistent-school-xyz"}))
    assert resp.status_code in (200, 404)


@pytest.mark.django_db
def test_lead_capture_view_get_returns_200_or_404(client):
    """
    GET /schools/<slug>/interest/ must not crash (200 or 404).
    With rate limiting off in tests, no 429 should appear.
    """
    resp = client.get(
        reverse("lead_capture", kwargs={"school_slug": "nonexistent-school-xyz"})
    )
    assert resp.status_code in (200, 404)
    assert resp.status_code != 429
