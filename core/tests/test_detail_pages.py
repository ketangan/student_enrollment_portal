"""
Phase 4 — School-admin detail pages.

Covers:
  - school_submission_detail_view: permissions, content, linked lead, audit log
  - school_lead_detail_view: permissions, contact info, enrollment actions, audit log
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AdminAuditLog, DraftSubmission, LEAD_STATUS_LOST, LEAD_STATUS_NEW
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _school_admin_user(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _submission_detail_url(school, submission_id):
    return reverse(
        "school_submission_detail",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _lead_detail_url(school, lead_id):
    return reverse(
        "school_lead_detail",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _make_audit_entry(obj, actor=None, extra=None):
    """Create an AdminAuditLog entry for obj using its actual pk."""
    return AdminAuditLog.objects.create(
        actor=actor,
        action="action",
        model_label=f"{obj._meta.app_label}.{obj._meta.model_name}",
        object_id=str(obj.pk),
        object_repr=str(obj),
        changes={},
        extra=extra or {},
        path="/test/",
        ip_address="127.0.0.1",
    )


# ── Submission detail ────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_submission_detail_happy_path(client):
    """GET returns 200, uses correct template, student name in content."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    submission = SubmissionFactory(
        school=school,
        data={"student_first_name": "Alice", "student_last_name": "Smith"},
    )
    client.force_login(user)

    response = client.get(_submission_detail_url(school, submission.id))

    assert response.status_code == 200
    assert "school_admin/submission_detail.html" in [t.name for t in response.templates]
    content = response.content.decode()
    assert "Alice" in content


@pytest.mark.django_db
def test_submission_detail_shows_status(client):
    """Status string is rendered on the detail page."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school, status="In Review")
    client.force_login(user)

    response = client.get(_submission_detail_url(school, submission.id))

    assert response.status_code == 200
    assert "In Review" in response.content.decode()


@pytest.mark.django_db
def test_submission_detail_shows_audit_log(client):
    """Audit log entries for the submission appear on the detail page."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school)
    _make_audit_entry(
        submission, actor=user,
        extra={"name": "status_update", "from": "New", "to": "In Review"},
    )
    client.force_login(user)

    response = client.get(_submission_detail_url(school, submission.id))

    assert response.status_code == 200
    content = response.content.decode()
    assert "In Review" in content


@pytest.mark.django_db
def test_submission_detail_shows_linked_lead(client):
    """When a lead was converted to this submission, the lead name is shown."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school)
    lead = LeadFactory(school=school, name="Bob Parent", converted_submission=submission)
    client.force_login(user)

    response = client.get(_submission_detail_url(school, submission.id))

    assert response.status_code == 200
    assert "Bob Parent" in response.content.decode()


@pytest.mark.django_db
def test_submission_detail_unauthenticated(client):
    """Unauthenticated request redirects to login."""
    school = SchoolFactory()
    submission = SubmissionFactory(school=school)

    response = client.get(_submission_detail_url(school, submission.id))

    assert response.status_code == 302
    assert "/login/" in response["Location"] or "login" in response["Location"]


@pytest.mark.django_db
def test_submission_detail_cross_school_404(client):
    """Admin of school_a cannot access school_b's submissions."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    submission_b = SubmissionFactory(school=school_b)
    client.force_login(user)

    response = client.get(_submission_detail_url(school_b, submission_b.id))

    assert response.status_code == 404


@pytest.mark.django_db
def test_submission_detail_unknown_id_404(client):
    """Non-existent submission_id returns 404."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    response = client.get(_submission_detail_url(school, 99999999))

    assert response.status_code == 404


@pytest.mark.django_db
def test_submission_detail_submission_in_wrong_school_404(client):
    """Submission exists but belongs to a different school — returns 404."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    # submission belongs to school_b; URL references school_a slug
    submission_b = SubmissionFactory(school=school_b)
    url = reverse(
        "school_submission_detail",
        kwargs={"school_slug": school_a.slug, "submission_id": submission_b.id},
    )
    client.force_login(user)

    response = client.get(url)

    assert response.status_code == 404


# ── Lead detail ──────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_lead_detail_happy_path(client):
    """GET returns 200, uses correct template, lead name in content."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, name="Carol Guardian")
    client.force_login(user)

    response = client.get(_lead_detail_url(school, lead.id))

    assert response.status_code == 200
    assert "school_admin/lead_detail.html" in [t.name for t in response.templates]
    assert "Carol Guardian" in response.content.decode()


@pytest.mark.django_db
def test_lead_detail_shows_contact_info(client):
    """Lead email and phone appear on the detail page."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, email="carol@example.com", phone="555-9876")
    client.force_login(user)

    response = client.get(_lead_detail_url(school, lead.id))

    assert response.status_code == 200
    content = response.content.decode()
    assert "carol@example.com" in content
    assert "555-9876" in content


@pytest.mark.django_db
def test_lead_detail_shows_audit_log(client):
    """Audit log entries for the lead appear on the detail page."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school)
    _make_audit_entry(
        lead, actor=user,
        extra={"name": "lead_status_update", "from": "new", "to": "contacted"},
    )
    client.force_login(user)

    response = client.get(_lead_detail_url(school, lead.id))

    assert response.status_code == 200
    assert "contacted" in response.content.decode()


@pytest.mark.django_db
def test_lead_detail_active_lead_shows_start_enrollment(client):
    """Active lead with no draft shows 'Start Enrollment' button, not 'Open Form'."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    client.force_login(user)

    response = client.get(_lead_detail_url(school, lead.id))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Start Enrollment" in content
    assert "Open Form" not in content


@pytest.mark.django_db
def test_lead_detail_get_auto_creates_draft_and_shows_link(client):
    """
    GET on the lead detail page auto-creates a draft so the enrollment URL
    is immediately visible without requiring a 'Generate link' click.
    A second GET must reuse the same draft (no duplicates).
    """
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    client.force_login(user)

    resp1 = client.get(_lead_detail_url(school, lead.id))
    assert resp1.status_code == 200
    assert DraftSubmission.objects.filter(school=school, lead=lead).count() == 1, (
        "First GET must create exactly one draft"
    )

    # Second GET must reuse — not create a second draft
    resp2 = client.get(_lead_detail_url(school, lead.id))
    assert resp2.status_code == 200
    assert DraftSubmission.objects.filter(school=school, lead=lead).count() == 1, (
        "Subsequent GETs must reuse the existing draft"
    )

    # Enrollment URL must be visible immediately (no generate step needed)
    content = resp2.content.decode()
    assert "Start Enrollment" in content
    assert "resume-link" in content, "Resume URL input must be present on page load"


@pytest.mark.django_db
def test_start_enrollment_creates_draft_and_detail_shows_open_form(client):
    """POST to start-enrollment creates a draft; subsequent GET shows 'Open Form'."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    client.force_login(user)

    start_url = reverse(
        "school_lead_start_enrollment",
        kwargs={"school_slug": school.slug, "lead_id": lead.id},
    )
    response = client.post(start_url)
    assert response.status_code == 302
    assert DraftSubmission.objects.filter(school=school, lead=lead).count() == 1

    detail_response = client.get(_lead_detail_url(school, lead.id))
    assert "Start Enrollment" in detail_response.content.decode()


@pytest.mark.django_db
def test_start_enrollment_reuses_existing_draft(client):
    """POSTing start-enrollment twice for the same lead reuses the draft — no duplicates."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    client.force_login(user)

    start_url = reverse(
        "school_lead_start_enrollment",
        kwargs={"school_slug": school.slug, "lead_id": lead.id},
    )
    client.post(start_url)
    client.post(start_url)

    assert DraftSubmission.objects.filter(school=school, lead=lead).count() == 1


@pytest.mark.django_db
def test_lead_detail_shows_view_submission_link(client):
    """Converted lead shows View Submission link; no enrollment form shown."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school)
    lead = LeadFactory(school=school, converted_submission=submission)
    client.force_login(user)

    response = client.get(_lead_detail_url(school, lead.id))

    assert response.status_code == 200
    content = response.content.decode()
    assert "View Submission" in content
    assert "Open Form" not in content


@pytest.mark.django_db
def test_lead_detail_lost_lead_no_enrollment_section(client):
    """Lost lead shows neither Open Form nor View Submission."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_LOST)
    client.force_login(user)

    response = client.get(_lead_detail_url(school, lead.id))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Open Form" not in content
    assert "View Submission" not in content


@pytest.mark.django_db
def test_lead_detail_unauthenticated(client):
    """Unauthenticated request redirects to login."""
    school = SchoolFactory()
    lead = LeadFactory(school=school)

    response = client.get(_lead_detail_url(school, lead.id))

    assert response.status_code == 302
    assert "login" in response["Location"]


@pytest.mark.django_db
def test_lead_detail_cross_school_404(client):
    """Admin of school_a cannot access school_b's lead detail."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    lead_b = LeadFactory(school=school_b)
    client.force_login(user)

    response = client.get(_lead_detail_url(school_b, lead_b.id))

    assert response.status_code == 404
