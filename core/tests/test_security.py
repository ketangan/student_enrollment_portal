"""
Security boundary tests.

Verifies that tenant isolation, expiry guards, and access-control walls
hold at the HTTP layer.  These tests exist to catch regressions where a
fix in one view is not applied consistently across sibling views.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.models import (
    DraftSubmission,
    School,
    Submission,
)
from core.tests.factories import SchoolFactory, SubmissionFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _school_with_plan(plan: str = "trial", **kwargs) -> School:
    """Create an in-DB school without a YAML config. Trial plan = all features."""
    return SchoolFactory(plan=plan, **kwargs)


def _submission_for_school(school: School) -> Submission:
    return SubmissionFactory(school=school)


# ---------------------------------------------------------------------------
# Cross-school status token isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_family_status_token_cross_school_returns_404():
    """
    A submission token from school A must not be accessible via school B's URL.
    The query is scoped to school=school_B so it returns 404.
    """
    school_a = _school_with_plan(plan="trial")
    school_b = _school_with_plan(plan="trial")

    submission_a = _submission_for_school(school_a)
    token = submission_a.status_token

    client = Client()
    # Token belongs to school_a; request via school_b URL
    response = client.get(f"/schools/{school_b.slug}/status/{token}/")
    assert response.status_code == 404, (
        f"Expected 404 for cross-school token access, got {response.status_code}"
    )


@pytest.mark.django_db
def test_family_status_own_school_token_succeeds():
    """
    Accessing a submission via its own school's URL renders the status page.
    Requires family_portal_enabled (trial plan enables it).
    """
    school = _school_with_plan(plan="trial")
    submission = _submission_for_school(school)
    token = submission.status_token

    client = Client()
    response = client.get(f"/schools/{school.slug}/status/{token}/")
    # 200 on success; or 404 if no YAML config and the view hard-requires it
    # (it doesn't — branding load is wrapped in try/except)
    assert response.status_code == 200, (
        f"Expected 200 for own-school token, got {response.status_code}"
    )


@pytest.mark.django_db
def test_family_status_unknown_token_returns_404():
    """Unknown token (not in DB at all) must return 404, not 500."""
    school = _school_with_plan(plan="trial")
    client = Client()
    response = client.get(f"/schools/{school.slug}/status/this-token-does-not-exist/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_family_status_disabled_feature_returns_404():
    """
    Schools on a plan that doesn't include family_portal_enabled get 404 even
    for a valid token — no information leakage about config.
    """
    school = _school_with_plan(plan="trial")
    # Override the flag off explicitly
    school.feature_flags = {"family_portal_enabled": False}
    school.save()

    submission = _submission_for_school(school)
    client = Client()
    response = client.get(f"/schools/{school.slug}/status/{submission.status_token}/")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Expired DraftSubmission — payment views
# ---------------------------------------------------------------------------


def _make_expired_draft(school_slug: str) -> DraftSubmission:
    """
    Create an expired DraftSubmission for a school that already exists in the DB.
    The school must exist before calling this (created by a prior view request
    or test setup that uses the YAML config).
    """
    school = School.objects.get(slug=school_slug)
    return DraftSubmission.objects.create(
        school=school,
        data={},
        token_expires_at=timezone.now() - timedelta(days=1),
    )


@pytest.mark.django_db
def test_expired_draft_payment_view_shows_expired_page():
    """
    GET /schools/<slug>/apply/pay/<token>/ with an expired draft renders
    apply_expired.html, not a payment form or 500.
    """
    client = Client()

    # Trigger school creation via the enrollment form landing (which creates School from YAML)
    client.get("/schools/south-bay-music/apply/")

    draft = _make_expired_draft("south-bay-music")
    response = client.get(f"/schools/south-bay-music/apply/pay/{draft.token}/")

    assert response.status_code == 200
    assert b"apply_expired" in response.content or b"expired" in response.content.lower(), (
        "Expected expiry page content in response"
    )
    assert response.templates, "No templates rendered"
    template_names = [t.name for t in response.templates]
    assert any("expired" in t for t in template_names), (
        f"Expected apply_expired template, got: {template_names}"
    )


@pytest.mark.django_db
def test_expired_draft_payment_confirm_shows_expired_page():
    """
    GET /schools/<slug>/apply/pay/<token>/confirm with an expired draft renders
    apply_expired.html — same guard as payment view.
    """
    client = Client()
    client.get("/schools/south-bay-music/apply/")

    draft = _make_expired_draft("south-bay-music")
    response = client.get(
        f"/schools/south-bay-music/apply/pay/{draft.token}/confirm",
        {"payment_intent": "pi_fake", "redirect_status": "failed"},
    )

    assert response.status_code in (200, 301, 302)
    if response.status_code == 200:
        template_names = [t.name for t in response.templates]
        assert any("expired" in t for t in template_names), (
            f"Expected apply_expired template, got: {template_names}"
        )


@pytest.mark.django_db
def test_submitted_draft_payment_view_redirects_to_success():
    """
    A draft that has already been submitted redirects to the success page, not
    to the payment form.  The applicant cannot double-pay.
    """
    client = Client()
    client.get("/schools/south-bay-music/apply/")

    school = School.objects.get(slug="south-bay-music")
    draft = DraftSubmission.objects.create(
        school=school,
        data={},
        submitted_at=timezone.now() - timedelta(hours=1),
    )
    response = client.get(f"/schools/south-bay-music/apply/pay/{draft.token}/")
    # Must redirect, not render a payment page
    assert response.status_code in (301, 302), (
        f"Expected redirect for submitted draft, got {response.status_code}"
    )
    assert "success" in response.headers.get("Location", "")


# ---------------------------------------------------------------------------
# Expired draft — resume view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resume_draft_expired_token_shows_expired_page():
    """
    GET /schools/<slug>/apply/resume/<token>/ with an expired draft renders
    the expiry page, not the enrollment form.
    """
    client = Client()
    client.get("/schools/south-bay-music/apply/")

    school = School.objects.get(slug="south-bay-music")
    draft = DraftSubmission.objects.create(
        school=school,
        data={},
        token_expires_at=timezone.now() - timedelta(days=1),
    )
    response = client.get(f"/schools/south-bay-music/apply/resume/{draft.token}/")

    # Should show expiry page (200) or redirect, never 500
    assert response.status_code in (200, 301, 302), (
        f"Unexpected status {response.status_code} for expired resume link"
    )
    if response.status_code == 200:
        template_names = [t.name for t in response.templates]
        assert any("expired" in t for t in template_names), (
            f"Expected apply_expired template for expired draft, got: {template_names}"
        )


@pytest.mark.django_db
def test_resume_draft_cross_school_slug_returns_404():
    """
    A draft token belongs to school A.  Accessing it via school B's resume URL
    returns 404 because the lookup is school-scoped.
    """
    client = Client()
    # Create both schools via YAML-backed config hits
    client.get("/schools/south-bay-music/apply/")
    client.get("/schools/beverly-hills-gymnastics/apply/")

    school_sbmc = School.objects.get(slug="south-bay-music")
    draft = DraftSubmission.objects.create(school=school_sbmc, data={})

    # Try to resume via a different school's slug
    response = client.get(f"/schools/beverly-hills-gymnastics/apply/resume/{draft.token}/")
    assert response.status_code == 404, (
        f"Expected 404 for cross-school draft resume, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Cross-school submission access via school admin views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_submission_detail_wrong_school_returns_404(client):
    """
    A logged-in school admin for school A cannot access submission detail for
    school B's submission by guessing the ID.
    """
    from django.contrib.auth.models import User
    from core.models import SchoolAdminMembership
    from core.tests.factories import SchoolAdminMembershipFactory, UserFactory

    membership = SchoolAdminMembershipFactory()
    school_a = membership.school
    school_b = SchoolFactory()

    # Create a submission belonging to school_b
    submission_b = _submission_for_school(school_b)

    client.force_login(membership.user)
    # Try to access school_b submission via school_a admin URL
    response = client.get(
        f"/schools/{school_a.slug}/admin/submissions/{submission_b.id}/"
    )
    assert response.status_code == 404, (
        f"Expected 404 for cross-school submission access, got {response.status_code}"
    )


@pytest.mark.django_db
def test_lead_detail_wrong_school_returns_404(client):
    """School admin for A cannot access school B's lead by ID."""
    from core.tests.factories import SchoolAdminMembershipFactory, LeadFactory

    membership = SchoolAdminMembershipFactory()
    school_a = membership.school
    school_b = SchoolFactory()

    lead_b = LeadFactory(school=school_b)

    client.force_login(membership.user)
    response = client.get(
        f"/schools/{school_a.slug}/admin/leads/{lead_b.id}/"
    )
    assert response.status_code == 404, (
        f"Expected 404 for cross-school lead access, got {response.status_code}"
    )
