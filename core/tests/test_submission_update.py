"""
Phase 7 — Submission Workflow + Notes.

Tests:
  1. submission_update_happy_path      — POST saves notes, creates audit entry, redirects
  2. submission_update_unauthenticated — anonymous POST → 302 to login, no changes saved
  3. submission_notes_persist          — notes survive a second detail-page GET
  4. audit_log_created_on_update       — audit entry has correct name + fields
  5. no_op_skips_save_and_audit        — POST with unchanged notes → no save, no audit entry
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AdminAuditLog, Submission
from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _school_admin_user(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _update_url(school, submission):
    return reverse(
        "school_submission_update",
        kwargs={"school_slug": school.slug, "submission_id": submission.id},
    )


def _detail_url(school, submission):
    return reverse(
        "school_submission_detail",
        kwargs={"school_slug": school.slug, "submission_id": submission.id},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_submission_update_happy_path(client):
    """POST with valid notes saves the field and redirects."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school)
    assert submission.internal_notes == ""

    client.force_login(user)
    resp = client.post(
        _update_url(school, submission),
        {"new_note": "Check transcript docs.", "next": _detail_url(school, submission)},
    )

    # Should redirect (302) — not error out
    assert resp.status_code == 302

    submission.refresh_from_db()
    assert "Check transcript docs." in submission.internal_notes


@pytest.mark.django_db
def test_submission_update_unauthenticated(client):
    """Anonymous POST must not modify notes and must redirect to login."""
    school = SchoolFactory()
    submission = SubmissionFactory(school=school, internal_notes="original")

    resp = client.post(
        _update_url(school, submission),
        {"internal_notes": "hacked"},
    )

    # Redirect to login
    assert resp.status_code == 302
    assert "/login/" in resp["Location"] or "login" in resp["Location"]

    submission.refresh_from_db()
    assert submission.internal_notes == "original"


@pytest.mark.django_db
def test_submission_notes_persist(client):
    """Notes written via update view survive a subsequent detail-page GET."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school)

    client.force_login(user)

    # Write notes
    client.post(
        _update_url(school, submission),
        {"new_note": "Awaiting immunisation records."},
    )

    # Load the detail page and confirm the notes are there
    resp = client.get(_detail_url(school, submission))
    assert resp.status_code == 200
    assert b"Awaiting immunisation records." in resp.content


@pytest.mark.django_db
def test_audit_log_created_on_update(client):
    """A submission_update audit entry is created on every successful save."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school)

    before_count = AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(submission.pk),
    ).count()

    client.force_login(user)
    client.post(
        _update_url(school, submission),
        {"new_note": "Follow up on waitlist."},
    )

    entries = AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(submission.pk),
        action="action",
    )
    assert entries.count() == before_count + 1

    last = entries.order_by("-created_at").first()
    assert last.extra.get("name") == "submission_update"
    assert "internal_notes" in last.extra.get("fields", [])


@pytest.mark.django_db
def test_no_op_skips_save_and_audit(client):
    """POST with the same notes as already stored must not save or create an audit entry."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school, internal_notes="already here")

    before_count = AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(submission.pk),
    ).count()

    client.force_login(user)
    resp = client.post(
        _update_url(school, submission),
        {"internal_notes": "already here"},
    )

    assert resp.status_code == 302

    # Notes unchanged
    submission.refresh_from_db()
    assert submission.internal_notes == "already here"

    # No new audit entry
    assert AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(submission.pk),
    ).count() == before_count
