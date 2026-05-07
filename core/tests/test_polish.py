"""
Phase 9 — Admin UX Polish.

Tests:
  1. test_submissions_view_context_has_apply_url   — apply_url in context
  2. test_submissions_empty_state_has_copy_cta     — copy button in HTML when no submissions
  3. test_leads_view_context_has_lead_capture_url  — lead_capture_url in context
  4. test_leads_empty_state_has_copy_cta           — copy button in HTML when no leads
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    UserFactory,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _school_admin_user(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _submissions_url(school):
    return reverse("school_submissions", kwargs={"school_slug": school.slug})


def _leads_url(school):
    return reverse("school_leads", kwargs={"school_slug": school.slug})


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_submissions_view_context_has_apply_url(client):
    """submissions view must expose apply_url so the empty-state CTA can use it."""
    school = SchoolFactory()
    user = _school_admin_user(school)

    client.force_login(user)
    resp = client.get(_submissions_url(school))

    assert resp.status_code == 200
    assert "apply_url" in resp.context
    apply_url = resp.context["apply_url"]
    # Must be an absolute URL containing the school slug
    assert school.slug in apply_url
    assert apply_url.startswith("http")


@pytest.mark.django_db
def test_submissions_empty_state_has_copy_cta(client):
    """When there are no submissions and no active filters, the copy-link CTA renders."""
    school = SchoolFactory()
    user = _school_admin_user(school)

    client.force_login(user)
    resp = client.get(_submissions_url(school))

    assert resp.status_code == 200
    content = resp.content.decode()
    # The copy button element must be present
    assert "copy-apply-btn" in content
    assert "Copy Application Link" in content


@pytest.mark.django_db
def test_submissions_empty_state_no_cta_when_filter_active(client):
    """Copy CTA must NOT appear when a status filter is active (filtered empty state)."""
    school = SchoolFactory()
    user = _school_admin_user(school)

    client.force_login(user)
    resp = client.get(_submissions_url(school) + "?status=Accepted")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "copy-apply-btn" not in content


@pytest.mark.django_db
def test_leads_view_context_has_lead_capture_url(client):
    """leads view must expose lead_capture_url so the empty-state CTA can use it."""
    school = SchoolFactory(feature_flags={"leads_enabled": True})
    user = _school_admin_user(school)

    client.force_login(user)
    resp = client.get(_leads_url(school))

    assert resp.status_code == 200
    assert "lead_capture_url" in resp.context
    url = resp.context["lead_capture_url"]
    assert school.slug in url
    assert url.startswith("http")


@pytest.mark.django_db
def test_leads_empty_state_has_copy_cta(client):
    """When there are no leads and no active filters, the copy-link CTA renders."""
    school = SchoolFactory(feature_flags={"leads_enabled": True})
    user = _school_admin_user(school)

    client.force_login(user)
    resp = client.get(_leads_url(school))

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "copy-lead-form-btn" in content
    assert "Copy Interest Form Link" in content
