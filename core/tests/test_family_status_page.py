"""
Family Status Page — tests for Phase 15.

Covers:
  - Public token-based status page: 200, 404, feature-flag gate
  - Shows public_notes when present; shows generic message when absent
  - Admin POST: post_public_note creates/prepends note, audit logged
  - Empty POST does nothing
  - Submission detail context: family_portal_enabled + family_status_url present
  - Confirmation email builder threads status_url into body
"""
from __future__ import annotations

import secrets

import pytest
from django.urls import reverse

from core.models import AdminAuditLog
from core.services.notifications import _build_confirmation_email_bodies
from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _school_with_flag(flag_on: bool = True):
    """Starter-plan school. Override family_portal_enabled explicitly."""
    school = SchoolFactory(plan="trial")
    if not flag_on:
        school.feature_flags = {"family_portal_enabled": False}
        school.save()
    return school


def _admin_for(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _status_url(school, token):
    return reverse("family_status", kwargs={"school_slug": school.slug, "token": token})


def _detail_url(school, sub):
    return reverse("school_submission_detail", kwargs={"school_slug": school.slug, "submission_id": sub.id})


def _post_note_url(school, sub):
    return reverse("school_submission_post_public_note", kwargs={"school_slug": school.slug, "submission_id": sub.id})


# ── public status page ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_family_status_page_renders_200(client):
    """Valid token → 200, page contains application ID."""
    school = _school_with_flag(True)
    sub = SubmissionFactory(school=school)
    resp = client.get(_status_url(school, sub.status_token))
    assert resp.status_code == 200
    assert sub.public_id in resp.content.decode()


@pytest.mark.django_db
def test_family_status_page_404_on_bad_token(client):
    """Non-existent token → 404 (no info leakage)."""
    school = _school_with_flag(True)
    SubmissionFactory(school=school)
    bad_token = secrets.token_urlsafe(32)
    resp = client.get(_status_url(school, bad_token))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_family_status_page_404_when_flag_disabled(client):
    """Feature flag off → 404 regardless of valid token."""
    school = _school_with_flag(flag_on=False)
    sub = SubmissionFactory(school=school)
    resp = client.get(_status_url(school, sub.status_token))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_family_status_page_shows_public_notes(client):
    """When public_notes is set, the notes appear in the response."""
    school = _school_with_flag(True)
    sub = SubmissionFactory(school=school, public_notes="We will follow up by Friday.")
    resp = client.get(_status_url(school, sub.status_token))
    assert resp.status_code == 200
    assert "We will follow up by Friday." in resp.content.decode()


@pytest.mark.django_db
def test_family_status_page_no_notes_shows_generic(client):
    """When public_notes is empty, generic message is shown (no crash)."""
    school = _school_with_flag(True)
    sub = SubmissionFactory(school=school, public_notes="")
    resp = client.get(_status_url(school, sub.status_token))
    assert resp.status_code == 200
    # No traceback — generic next-steps text appears
    assert "reviewing" in resp.content.decode().lower()


# ── admin: post public note ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_post_public_note_creates_note(client):
    """Admin can post a public note; it appears on the submission."""
    school = _school_with_flag(True)
    sub = SubmissionFactory(school=school, public_notes="")
    user = _admin_for(school)
    client.force_login(user)

    resp = client.post(_post_note_url(school, sub), {"public_note": "Application received!"})
    assert resp.status_code in (302, 200)

    sub.refresh_from_db()
    assert "Application received!" in sub.public_notes


@pytest.mark.django_db
def test_post_public_note_prepends_to_existing(client):
    """Second note is prepended, original note is preserved."""
    school = _school_with_flag(True)
    sub = SubmissionFactory(school=school, public_notes="[old] First note.")
    user = _admin_for(school)
    client.force_login(user)

    client.post(_post_note_url(school, sub), {"public_note": "Newer update."})

    sub.refresh_from_db()
    content = sub.public_notes
    # Newer comes first
    assert content.index("Newer update.") < content.index("First note.")


@pytest.mark.django_db
def test_post_public_note_empty_does_nothing(client):
    """Blank note field → no change, no audit log entry."""
    school = _school_with_flag(True)
    sub = SubmissionFactory(school=school, public_notes="Existing note.")
    user = _admin_for(school)
    client.force_login(user)

    before_count = AdminAuditLog.objects.filter(
        model_label="core.submission", object_id=str(sub.pk)
    ).count()
    client.post(_post_note_url(school, sub), {"public_note": "   "})

    sub.refresh_from_db()
    assert sub.public_notes == "Existing note."
    assert AdminAuditLog.objects.filter(
        model_label="core.submission", object_id=str(sub.pk)
    ).count() == before_count


@pytest.mark.django_db
def test_post_public_note_creates_audit_log(client):
    """Posting a note creates a 'post_public_note' audit log entry."""
    school = _school_with_flag(True)
    sub = SubmissionFactory(school=school, public_notes="")
    user = _admin_for(school)
    client.force_login(user)

    client.post(_post_note_url(school, sub), {"public_note": "Status update for family."})

    log = AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(sub.pk),
        extra__name="post_public_note",
    )
    assert log.exists()


# ── submission detail context ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_submission_detail_contains_family_portal_context(client):
    """Detail view includes family_portal_enabled and family_status_url in context."""
    school = _school_with_flag(True)
    sub = SubmissionFactory(school=school)
    user = _admin_for(school)
    client.force_login(user)

    resp = client.get(_detail_url(school, sub))
    assert resp.status_code == 200
    assert resp.context["family_portal_enabled"] is True
    status_url = resp.context["family_status_url"]
    assert sub.status_token in status_url


@pytest.mark.django_db
def test_submission_detail_family_portal_disabled_hides_url(client):
    """When flag is off, family_status_url is empty string."""
    school = _school_with_flag(flag_on=False)
    sub = SubmissionFactory(school=school)
    user = _admin_for(school)
    client.force_login(user)

    resp = client.get(_detail_url(school, sub))
    assert resp.status_code == 200
    assert resp.context["family_portal_enabled"] is False
    assert resp.context["family_status_url"] == ""


# ── notification builder ───────────────────────────────────────────────────────


def test_confirmation_email_includes_status_url():
    """status_url is included in both plain-text and HTML email bodies."""
    status_url = "https://example.com/schools/test/status/abc123/"
    text, html = _build_confirmation_email_bodies(
        school_name="Test School",
        student_name="Alice",
        submission_public_id="APP-001",
        response_time="",
        custom_message="",
        status_url=status_url,
    )
    assert status_url in text
    assert status_url in html


def test_confirmation_email_no_status_url_omits_link():
    """When status_url is empty, no status link appears in the body."""
    text, html = _build_confirmation_email_bodies(
        school_name="Test School",
        student_name="Alice",
        submission_public_id="APP-001",
        response_time="",
        custom_message="",
        status_url="",
    )
    assert "Track your application" not in text
    assert "Track your application" not in html
