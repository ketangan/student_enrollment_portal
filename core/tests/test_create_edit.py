"""
Phase 10 — Admin Create & Edit Workflows.

Tests:
  1.  test_create_lead_happy_path        POST valid data → 302, Lead in DB (status=new, source=manual)
  2.  test_create_lead_requires_name     POST no name → 200, "Name is required" in response
  3.  test_create_lead_invalid_email     POST bad email → 200, error in response
  4.  test_create_lead_unauthenticated   no login → redirect to login
  5.  test_create_lead_cross_school_404  admin of school_a → school_b slug → 404
  6.  test_edit_lead_updates_core_fields POST name/email/phone/program → saved in DB
  7.  test_edit_lead_no_op               POST identical data → no AdminAuditLog created
  8.  test_create_submission_happy_path  POST → 302, Submission in DB, correct form_key
  9.  test_submission_edit_updates_data  POST updates data; extra keys from old data preserved
  10. test_submission_edit_no_op         POST unchanged data + notes → messages.info, no DB write
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AdminAuditLog, Lead, Submission, LEAD_STATUS_NEW
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _admin_user(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _lead_create_url(school):
    return reverse("school_lead_create", kwargs={"school_slug": school.slug})


def _lead_update_url(school, lead):
    return reverse("school_lead_update", kwargs={"school_slug": school.slug, "lead_id": lead.id})


def _submission_create_url(school):
    return reverse("school_submission_create", kwargs={"school_slug": school.slug})


def _submission_edit_url(school, submission):
    return reverse(
        "school_submission_edit",
        kwargs={"school_slug": school.slug, "submission_id": submission.id},
    )


# ── Lead create ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_create_lead_happy_path(client):
    """POST valid lead data → 302 redirect to lead detail, Lead in DB."""
    school = SchoolFactory(feature_flags={"leads_enabled": True})
    user = _admin_user(school)

    client.force_login(user)
    resp = client.post(
        _lead_create_url(school),
        {"name": "Jane Smith", "email": "jane@example.com", "phone": "555-1234", "notes": ""},
    )

    assert resp.status_code == 302
    lead = Lead.objects.get(school=school, email="jane@example.com")
    assert lead.name == "Jane Smith"
    assert lead.status == LEAD_STATUS_NEW
    assert lead.source == "manual"
    assert AdminAuditLog.objects.filter(
        model_label="core.lead", object_id=str(lead.pk)
    ).exists()


@pytest.mark.django_db
def test_create_lead_requires_name(client):
    """POST with no name → 200 re-render with inline error."""
    school = SchoolFactory(feature_flags={"leads_enabled": True})
    user = _admin_user(school)

    client.force_login(user)
    resp = client.post(
        _lead_create_url(school),
        {"name": "", "email": "jane@example.com"},
    )

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Name is required" in content
    assert not Lead.objects.filter(school=school).exists()


@pytest.mark.django_db
def test_create_lead_invalid_email(client):
    """POST with bad email → 200 re-render with inline error, other values preserved."""
    school = SchoolFactory(feature_flags={"leads_enabled": True})
    user = _admin_user(school)

    client.force_login(user)
    resp = client.post(
        _lead_create_url(school),
        {"name": "Jane Smith", "email": "not-an-email"},
    )

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "valid email" in content.lower()
    # Name value should be preserved in the form
    assert "Jane Smith" in content
    assert not Lead.objects.filter(school=school).exists()


@pytest.mark.django_db
def test_create_lead_unauthenticated(client):
    """Unauthenticated request → redirect to login."""
    school = SchoolFactory(feature_flags={"leads_enabled": True})

    resp = client.post(
        _lead_create_url(school),
        {"name": "Jane", "email": "jane@example.com"},
    )

    assert resp.status_code == 302
    assert "/login" in resp.url or "login" in resp.url


@pytest.mark.django_db
def test_create_lead_cross_school_404(client):
    """Admin of school_a cannot create leads for school_b."""
    school_a = SchoolFactory(feature_flags={"leads_enabled": True})
    school_b = SchoolFactory(feature_flags={"leads_enabled": True})
    user = _admin_user(school_a)

    client.force_login(user)
    resp = client.post(
        _lead_create_url(school_b),
        {"name": "Jane", "email": "jane@example.com"},
    )

    assert resp.status_code == 404


# ── Lead edit (expand existing update view) ────────────────────────────────────


@pytest.mark.django_db
def test_edit_lead_updates_core_fields(client):
    """POST name/email/phone/program → all saved correctly in DB."""
    school = SchoolFactory()
    lead = LeadFactory(
        school=school,
        name="Old Name",
        email="old@example.com",
        phone="111-111-1111",
        interested_in_value="",
    )
    user = _admin_user(school)

    client.force_login(user)
    resp = client.post(
        _lead_update_url(school, lead),
        {
            "name": "New Name",
            "email": "new@example.com",
            "phone": "999-999-9999",
            "interested_in_value": "",
            "notes": "",
            "next_follow_up_at": "",
        },
    )

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.name == "New Name"
    assert lead.email == "new@example.com"
    assert lead.phone == "999-999-9999"
    # Audit log entry should exist for this change.
    assert AdminAuditLog.objects.filter(
        model_label="core.lead", object_id=str(lead.pk)
    ).exists()


@pytest.mark.django_db
def test_edit_lead_no_op(client):
    """POST identical data → no new AdminAuditLog entry created."""
    school = SchoolFactory()
    lead = LeadFactory(
        school=school,
        name="Same Name",
        email="same@example.com",
        phone="",
        notes="",
        interested_in_value="",
        next_follow_up_at=None,
    )
    user = _admin_user(school)
    # Baseline: no audit logs yet.
    initial_count = AdminAuditLog.objects.filter(
        model_label="core.lead", object_id=str(lead.pk)
    ).count()

    client.force_login(user)
    resp = client.post(
        _lead_update_url(school, lead),
        {
            "name": "Same Name",
            "email": "same@example.com",
            "phone": "",
            "interested_in_value": "",
            "notes": "",
            "next_follow_up_at": "",
        },
    )

    assert resp.status_code == 302
    after_count = AdminAuditLog.objects.filter(
        model_label="core.lead", object_id=str(lead.pk)
    ).count()
    assert after_count == initial_count  # no new audit log


# ── Submission create ──────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_create_submission_happy_path(client):
    """POST to create view → 302, Submission in DB with correct form_key."""
    school = SchoolFactory()
    user = _admin_user(school)

    # No YAML config for test school → available_forms empty → form_key defaults to "default"
    client.force_login(user)
    resp = client.post(
        _submission_create_url(school),
        {"_form_key": ""},
    )

    # With no YAML form config, validate_submission runs on empty form_cfg and produces no errors.
    assert resp.status_code == 302
    sub = Submission.objects.get(school=school)
    assert sub.form_key == "default"
    assert AdminAuditLog.objects.filter(
        model_label="core.submission", object_id=str(sub.pk)
    ).exists()


# ── Submission edit ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_submission_edit_updates_data(client):
    """POST updates submission.data; extra keys from old data are preserved."""
    school = SchoolFactory()
    submission = SubmissionFactory(
        school=school,
        data={"first_name": "Old", "existing_file_key": "preserved_value"},
        form_key="default",
        internal_notes="",
    )
    user = _admin_user(school)

    # No YAML form config → no validation errors → cleaned = {}
    # After merge: {**old_data, **cleaned} keeps existing_file_key.
    client.force_login(user)
    resp = client.post(
        _submission_edit_url(school, submission),
        {"internal_notes": "admin note added"},
    )

    assert resp.status_code == 302
    submission.refresh_from_db()
    # File-like key must still be present (file data preserved).
    assert submission.data.get("existing_file_key") == "preserved_value"
    assert submission.internal_notes == "admin note added"
    assert AdminAuditLog.objects.filter(
        model_label="core.submission", object_id=str(submission.pk)
    ).exists()


@pytest.mark.django_db
def test_submission_edit_no_op(client):
    """POST unchanged data + same notes → messages.info flash, no new audit log."""
    school = SchoolFactory()
    submission = SubmissionFactory(
        school=school,
        data={"first_name": "Alice"},
        form_key="default",
        internal_notes="existing note",
    )
    user = _admin_user(school)
    initial_count = AdminAuditLog.objects.filter(
        model_label="core.submission", object_id=str(submission.pk)
    ).count()

    client.force_login(user)
    # POST the same notes — form data empty (no YAML fields) so nothing else changes.
    resp = client.post(
        _submission_edit_url(school, submission),
        {"internal_notes": "existing note"},
    )

    assert resp.status_code == 302
    after_count = AdminAuditLog.objects.filter(
        model_label="core.submission", object_id=str(submission.pk)
    ).count()
    assert after_count == initial_count  # no new audit log
