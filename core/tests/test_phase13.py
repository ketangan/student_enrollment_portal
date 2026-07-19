"""
Phase 13 — Hardening & Production Readiness.

Covers:
  - Cross-school 404 for Phase 11/12 views not previously covered by tests
  - Config failure resilience: _safe_load_school_config graceful admin fallback
  - Config=None resilience: admin views render without YAML features
  - mark_contacted 30-second idempotency guard (no duplicate save + audit)
  - Lead create duplicate email: IntegrityError caught, form re-rendered
  - Empty message validation in send-message views
  - DraftSubmission idempotency: select_for_update() prevents duplicate drafts
"""
from __future__ import annotations

import yaml
import pytest

from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, DraftSubmission, LEAD_STATUS_NEW
from core.tests.factories import (
    LeadFactory,
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


def _email_school():
    """School with email_notifications_enabled=True (plan='trial')."""
    return SchoolFactory(plan="trial")


def _raise_yaml_error(slug):
    raise yaml.YAMLError("bad yaml")


# ── URL helpers ───────────────────────────────────────────────────────────────


def _sub_mark_contacted_url(school, submission_id):
    return reverse(
        "school_submission_mark_contacted",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _sub_follow_up_url(school, submission_id):
    return reverse(
        "school_submission_follow_up_set",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _lead_send_msg_url(school, lead_id):
    return reverse(
        "school_lead_send_message",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _sub_send_msg_url(school, submission_id):
    return reverse(
        "school_submission_send_message",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _resend_url(school, submission_id):
    return reverse(
        "school_submission_resend_confirmation",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _lead_mc_url(school, lead_id):
    return reverse(
        "school_lead_mark_contacted",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _lead_create_url(school):
    return reverse("school_lead_create", kwargs={"school_slug": school.slug})


def _submissions_url(school):
    return reverse("school_submissions", kwargs={"school_slug": school.slug})


def _lead_detail_url(school, lead_id):
    return reverse(
        "school_lead_detail",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _start_enrollment_url(school, lead_id):
    return reverse(
        "school_lead_start_enrollment",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


# ── Cross-school 404 for Phase 11/12 views ────────────────────────────────────


@pytest.mark.django_db
def test_submission_mark_contacted_cross_school_404(client):
    """Admin of school_a cannot POST to school_b's submission mark-contacted."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    submission_b = SubmissionFactory(school=school_b)
    client.force_login(user)

    response = client.post(_sub_mark_contacted_url(school_b, submission_b.id))
    assert response.status_code == 404


@pytest.mark.django_db
def test_submission_follow_up_cross_school_404(client):
    """Admin of school_a cannot POST to school_b's follow-up set endpoint."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    submission_b = SubmissionFactory(school=school_b)
    client.force_login(user)

    response = client.post(_sub_follow_up_url(school_b, submission_b.id))
    assert response.status_code == 404


@pytest.mark.django_db
def test_lead_send_message_cross_school_404(client):
    """Admin of school_a cannot POST to school_b's lead send-message endpoint."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    lead_b = LeadFactory(school=school_b)
    client.force_login(user)

    # 404 fires at _get_accessible_school_for_admin — before feature-flag or email checks
    response = client.post(_lead_send_msg_url(school_b, lead_b.id), {"message": "Hi"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_submission_send_message_cross_school_404(client):
    """Admin of school_a cannot POST to school_b's submission send-message endpoint."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    submission_b = SubmissionFactory(school=school_b)
    client.force_login(user)

    response = client.post(_sub_send_msg_url(school_b, submission_b.id), {"message": "Hi"})
    assert response.status_code == 404


@pytest.mark.django_db
def test_resend_confirmation_cross_school_404(client):
    """Admin of school_a cannot POST to school_b's resend-confirmation endpoint."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    submission_b = SubmissionFactory(school=school_b)
    client.force_login(user)

    response = client.post(_resend_url(school_b, submission_b.id))
    assert response.status_code == 404


# ── Config failure resilience — exception path (Part A admin fallback) ─────────


@pytest.mark.django_db
def test_broken_yaml_does_not_crash_submissions_list(client, monkeypatch):
    """YAMLError from load_school_config does not 500 the admin submissions list."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    monkeypatch.setattr("core.views_school_common.load_school_config", _raise_yaml_error)

    response = client.get(_submissions_url(school))
    assert response.status_code == 200


@pytest.mark.django_db
def test_broken_yaml_does_not_crash_lead_detail(client, monkeypatch):
    """YAMLError from load_school_config does not 500 the admin lead detail page."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    client.force_login(user)

    monkeypatch.setattr("core.views_school_common.load_school_config", _raise_yaml_error)

    response = client.get(_lead_detail_url(school, lead.id))
    assert response.status_code == 200


# ── Config returns None — normal "no YAML file" path (Part A) ─────────────────


@pytest.mark.django_db
def test_config_none_does_not_crash_lead_detail(client, monkeypatch):
    """load_school_config returning None (no YAML file) still renders the lead detail page."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    client.force_login(user)

    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: None)

    response = client.get(_lead_detail_url(school, lead.id))
    assert response.status_code == 200


# ── mark_contacted 30-second idempotency guard (Part D) ───────────────────────


@pytest.mark.django_db
def test_mark_contacted_within_30s_skips_duplicate_save(client):
    """
    Second mark-contacted POST within 30 seconds:
    - redirects with success (no error)
    - last_contacted_at does NOT change
    - no second AdminAuditLog entry is created
    """
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    client.force_login(user)

    url = _lead_mc_url(school, lead.id)

    # First POST: sets last_contacted_at
    r1 = client.post(url, {"next": ""})
    assert r1.status_code == 302

    lead.refresh_from_db()
    first_contacted_at = lead.last_contacted_at
    assert first_contacted_at is not None

    audit_count_after_first = AdminAuditLog.objects.filter(
        model_label="core.lead", object_id=str(lead.pk)
    ).count()

    # Second POST immediately — well within 30 seconds
    r2 = client.post(url, {"next": ""})
    assert r2.status_code == 302
    # Guard still returns success flash (no error to the admin)
    assert any("contacted" in str(m).lower() for m in r2.wsgi_request._messages)

    lead.refresh_from_db()

    # Timestamp must be unchanged (guard fired early)
    assert lead.last_contacted_at == first_contacted_at

    # No second audit entry created
    audit_count_after_second = AdminAuditLog.objects.filter(
        model_label="core.lead", object_id=str(lead.pk)
    ).count()
    assert audit_count_after_second == audit_count_after_first


# ── Lead create: IntegrityError now caught + logged (Part B) ──────────────────


@pytest.mark.django_db
def test_lead_create_same_email_creates_second_lead(client):
    """
    POSTing the same email as an existing lead must create a new lead (redirect),
    not block. Multiple leads per guardian email are allowed — different students,
    or the same student in different programs.
    """
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    LeadFactory(school=school, email="dup@example.com", name="Sofia Reyes")

    response = client.post(
        _lead_create_url(school),
        {
            "name": "Lucas Reyes",
            "email": "dup@example.com",
            "phone": "555-9999",
            "notes": "",
        },
    )

    assert response.status_code == 302
    assert school.leads.filter(email="dup@example.com").count() == 2


# ── Empty message validation (existing gap) ───────────────────────────────────


@pytest.mark.django_db
def test_empty_message_blocked_in_send_message(client, monkeypatch):
    """POST send-message with empty body → error flash, send_admin_message never called."""
    school = _email_school()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, email="parent@example.com")
    client.force_login(user)

    send_called = []
    monkeypatch.setattr(
        "core.views_school_leads.send_admin_message",
        lambda **kw: send_called.append(kw) or True,
    )
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: None)

    response = client.post(
        _lead_send_msg_url(school, lead.id),
        {"message": "", "next": ""},
    )

    assert response.status_code == 302
    assert not send_called
    msgs = list(response.wsgi_request._messages)
    assert any(
        "empty" in str(m).lower() or "cannot" in str(m).lower() or "message" in str(m).lower()
        for m in msgs
    )


# ── DraftSubmission idempotency (Part C) ──────────────────────────────────────


@pytest.mark.django_db
def test_draft_creation_is_idempotent(client):
    """
    select_for_update() inside transaction.atomic() guarantees that two sequential
    POSTs to start-enrollment produce exactly one active draft for the same lead.

    First POST: no draft exists → creates one → redirects.
    Second POST: draft found by select_for_update → updates prefill data → redirects.
    Result: exactly one active draft, no 500, no duplicate row.
    """
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    client.force_login(user)

    url = _start_enrollment_url(school, lead.id)

    # First POST — creates the draft
    r1 = client.post(url)
    assert r1.status_code == 302
    assert DraftSubmission.objects.filter(
        school=school, lead=lead, submitted_at__isnull=True
    ).count() == 1

    # Second POST — must reuse the draft, not create another
    r2 = client.post(url)
    assert r2.status_code == 302
    assert DraftSubmission.objects.filter(
        school=school, lead=lead, submitted_at__isnull=True
    ).count() == 1


# ── Enrolled lead: email sending blocked ──────────────────────────────────────


@pytest.mark.django_db
def test_enrolled_lead_send_message_blocked(client, monkeypatch):
    """
    POST to send-message for an enrolled lead (converted_submission set) must be
    rejected with an error flash. send_admin_message must never be called.
    """
    school = _email_school()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school)
    lead = LeadFactory(school=school, email="parent@example.com")
    lead.converted_submission = submission
    lead.save(update_fields=["converted_submission"])
    client.force_login(user)

    send_called = []
    monkeypatch.setattr(
        "core.views_school_leads.send_admin_message",
        lambda **kw: send_called.append(kw) or True,
    )

    response = client.post(
        _lead_send_msg_url(school, lead.id),
        {"message": "Hello", "next": ""},
    )

    assert response.status_code == 302
    assert not send_called
    msgs = list(response.wsgi_request._messages)
    assert any("enrolled" in str(m).lower() or "submission" in str(m).lower() for m in msgs)


@pytest.mark.django_db
def test_enrolled_lead_detail_shows_disabled_email_notice(client, monkeypatch):
    """
    Lead detail page for an enrolled lead shows the disabled email notice instead
    of the send-message form.
    """
    school = _email_school()
    user = _school_admin_user(school)
    submission = SubmissionFactory(school=school)
    lead = LeadFactory(school=school, email="parent@example.com")
    lead.converted_submission = submission
    lead.save(update_fields=["converted_submission"])
    client.force_login(user)

    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: None)

    response = client.get(_lead_detail_url(school, lead.id))
    assert response.status_code == 200
    content = response.content.decode()
    assert "lead-send-msg-form" not in content
    assert "enrolled" in content.lower()
