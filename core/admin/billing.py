# core/admin/billing.py
"""
Admin URL registrations for billing views.

Billing views live in core.views_billing; this module only wires them
into the admin URL namespace via admin.site.get_urls().
"""
from __future__ import annotations

from django.urls import path
from django.contrib import admin

from core.views_billing import billing_view, billing_create_checkout, billing_portal


def get_billing_urls():
    """Return URL patterns for billing pages (admin URL space)."""
    return [
        path("billing/", admin.site.admin_view(billing_view), name="billing"),
        path(
            "billing/checkout/",
            admin.site.admin_view(billing_create_checkout),
            name="billing_create_checkout",
        ),
        path(
            "billing/portal/",
            admin.site.admin_view(billing_portal),
            name="billing_portal",
        ),
    ]
