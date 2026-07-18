"""
Centralized absolute URL construction.

All code that needs an absolute URL must go through here — never call
request.build_absolute_uri() or concatenate BASE_URL manually.

  app_url(path)  → https://app.mypontora.com<path>
  demo_url(path) → https://demo.mypontora.com<path>

Both fall back to BASE_URL in local dev so a single env var is enough.

Use app_url for everything user/client-facing:
    magic links, confirmation emails, family portal, embed snippets,
    billing redirects, QR codes, password resets.

Use demo_url for prospect-facing links:
    DemoAccessToken magic links, demo page base URLs.
"""

from django.conf import settings
from django.urls import reverse as _reverse


def _base(attr: str) -> str:
    val = getattr(settings, attr, None) or getattr(settings, "BASE_URL", "http://localhost:8000")
    return val.rstrip("/")


def app_url(path: str) -> str:
    """Absolute URL on the production app domain."""
    return _base("APP_BASE_URL") + path


def demo_url(path: str) -> str:
    """Absolute URL on the demo domain."""
    return _base("DEMO_BASE_URL") + path


def app_reverse(viewname: str, args=None, kwargs=None) -> str:
    """Named URL reversed against the app domain."""
    return app_url(_reverse(viewname, args=args, kwargs=kwargs))


def demo_reverse(viewname: str, args=None, kwargs=None) -> str:
    """Named URL reversed against the demo domain."""
    return demo_url(_reverse(viewname, args=args, kwargs=kwargs))
