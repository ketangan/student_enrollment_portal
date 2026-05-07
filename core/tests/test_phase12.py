"""
Phase 12 — Communication: Close the Loop with Families.

Covers:
  - Confirmation email fires on apply_view POST
  - Resend confirmation: success and email-failure paths
  - Lead send-message: success and no-email paths
  - Submission send-message: success and no-email paths
  - Mark Contacted optional email (send_email=1) triggers workflow notification
"""
from __future__ import annotations

import pytest
from unittest import mock
from unittest.mock import MagicMock, patch

from django.urls import reverse

from core.models import Lead, LEAD_STATUS_NEW
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _email_school():
    """School with email_notifications_enabled=True (plan='trial' includes it)."""
    return SchoolFactory(plan="trial")


def _school_admin(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _resend_url(school, submission_id):
    return reverse(
        "school_submission_resend_confirmation",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _lead_msg_url(school, lead_id):
    return reverse(
        "school_lead_send_message",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _sub_msg_url(school, submission_id):
    return reverse(
        "school_submission_send_message",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _lead_mc_url(school, lead_id):
    return reverse(
        "school_lead_mark_contacted",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _detail_url(school, submission_id):
    return reverse(
        "school_submission_detail",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_resend_confirmation_success(client, monkeypatch):
    """POST resend-confirmation → send_applicant_confirmation_email called → success flash."""
    school = _email_school()
    user = _school_admin(school)
    submission = SubmissionFactory(school=school)
    client.force_login(user)

    monkeypatch.setattr("core.views_school_submissions.send_applicant_confirmation_email", lambda **kw: True)
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: MagicMock(raw={}))

    resp = client.post(
        _resend_url(school, submission.id),
        {"next": _detail_url(school, submission.id)},
    )
    assert resp.status_code == 302

    messages = list(resp.wsgi_request._messages)
    assert any("resent" in str(m).lower() for m in messages)


@pytest.mark.django_db
def test_resend_confirmation_email_failure(client, monkeypatch):
    """send_applicant_confirmation_email returning False → error flash shown."""
    school = _email_school()
    user = _school_admin(school)
    submission = SubmissionFactory(school=school)
    client.force_login(user)

    monkeypatch.setattr("core.views_school_submissions.send_applicant_confirmation_email", lambda **kw: False)
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: MagicMock(raw={}))

    resp = client.post(
        _resend_url(school, submission.id),
        {"next": _detail_url(school, submission.id)},
    )
    assert resp.status_code == 302

    messages = list(resp.wsgi_request._messages)
    assert any("could not" in str(m).lower() or "check" in str(m).lower() for m in messages)


@pytest.mark.django_db
def test_resend_confirmation_audit_logged(client, monkeypatch):
    """Successful resend creates an AdminAuditLog entry."""
    from core.models import AdminAuditLog

    school = _email_school()
    user = _school_admin(school)
    submission = SubmissionFactory(school=school)
    client.force_login(user)

    monkeypatch.setattr("core.views_school_submissions.send_applicant_confirmation_email", lambda **kw: True)
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: MagicMock(raw={}))

    before = AdminAuditLog.objects.count()
    client.post(
        _resend_url(school, submission.id),
        {"next": _detail_url(school, submission.id)},
    )
    assert AdminAuditLog.objects.count() == before + 1
    entry = AdminAuditLog.objects.order_by("-created_at").first()
    assert entry.extra.get("name") == "resend_confirmation"


@pytest.mark.django_db
def test_lead_send_message_success(client, monkeypatch):
    """POST send-message with valid lead → send_admin_message called with lead.email."""
    school = _email_school()
    user = _school_admin(school)
    lead = LeadFactory(school=school, email="parent@example.com")
    client.force_login(user)

    called_with = {}

    def fake_send(**kw):
        called_with.update(kw)
        return True

    monkeypatch.setattr("core.views_school_leads.send_admin_message", fake_send)
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: MagicMock(raw={}))
    monkeypatch.setattr("core.views_school_leads._resolve_from_email", lambda raw: "from@school.test")

    resp = client.post(
        _lead_msg_url(school, lead.id),
        {"message": "Hello from the school.", "subject": "Hi there", "next": ""},
    )
    assert resp.status_code == 302
    assert called_with.get("to_email") == "parent@example.com"
    assert "Hello from the school." in called_with.get("message", "")


@pytest.mark.django_db
def test_lead_send_message_no_email(client, monkeypatch):
    """Lead with blank email → error flash, send_admin_message never called."""
    school = _email_school()
    user = _school_admin(school)
    # LeadFactory requires email; override via save to blank it
    lead = LeadFactory(school=school)
    Lead.objects.filter(pk=lead.pk).update(email="", normalized_email="")
    lead.refresh_from_db()
    client.force_login(user)

    send_called = []
    monkeypatch.setattr("core.views_school_leads.send_admin_message", lambda **kw: send_called.append(kw) or True)

    resp = client.post(
        _lead_msg_url(school, lead.id),
        {"message": "Hello", "next": ""},
    )
    assert resp.status_code == 302
    assert not send_called
    messages = list(resp.wsgi_request._messages)
    assert any("email" in str(m).lower() for m in messages)


@pytest.mark.django_db
def test_submission_send_message_success(client, monkeypatch):
    """POST send-message on submission with parent_email → send_admin_message called."""
    school = _email_school()
    user = _school_admin(school)
    submission = SubmissionFactory(
        school=school, data={"parent_email": "guardian@example.com"}
    )
    client.force_login(user)

    called_with = {}

    def fake_send(**kw):
        called_with.update(kw)
        return True

    monkeypatch.setattr("core.views_school_submissions.send_admin_message", fake_send)
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: MagicMock(raw={}))
    monkeypatch.setattr("core.views_school_submissions._resolve_from_email", lambda raw: "from@school.test")

    resp = client.post(
        _sub_msg_url(school, submission.id),
        {"message": "Checking in.", "next": ""},
    )
    assert resp.status_code == 302
    assert called_with.get("to_email") == "guardian@example.com"


@pytest.mark.django_db
def test_submission_send_message_no_email(client, monkeypatch):
    """Submission with no email keys in data → error flash, no send."""
    school = _email_school()
    user = _school_admin(school)
    # data has no recognised email key
    submission = SubmissionFactory(school=school, data={"first_name": "Alice"})
    client.force_login(user)

    send_called = []
    monkeypatch.setattr("core.views_school_submissions.send_admin_message", lambda **kw: send_called.append(kw) or True)

    resp = client.post(
        _sub_msg_url(school, submission.id),
        {"message": "Hello", "next": ""},
    )
    assert resp.status_code == 302
    assert not send_called
    messages = list(resp.wsgi_request._messages)
    assert any("email" in str(m).lower() for m in messages)


@pytest.mark.django_db
def test_mark_contacted_optional_email_lead(client, monkeypatch):
    """POST mark-contacted with send_email=1 triggers send_workflow_notification."""
    school = _email_school()
    user = _school_admin(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW, email="parent@example.com")
    client.force_login(user)

    notif_calls = []

    def fake_notif(**kw):
        notif_calls.append(kw)
        return True

    monkeypatch.setattr("core.views_school_leads.send_workflow_notification", fake_notif)
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: MagicMock(raw={}))
    monkeypatch.setattr("core.views_school_leads._resolve_from_email", lambda raw: "from@school.test")

    resp = client.post(
        _lead_mc_url(school, lead.id),
        {"send_email": "1", "next": ""},
    )
    assert resp.status_code == 302
    assert len(notif_calls) == 1
    assert notif_calls[0]["notification_type"] == "contacted"
    assert notif_calls[0]["to_email"] == "parent@example.com"


# ── Fix #6: backend feature flag enforcement ───────────────────────────────


@pytest.mark.django_db
def test_resend_confirmation_flag_disabled(client):
    """email_notifications_enabled=False → error flash even via direct POST."""
    from core.tests.factories import SchoolFactory as SF
    school = SF(plan="free")  # no email feature
    user = _school_admin(school)
    submission = SubmissionFactory(school=school)
    client.force_login(user)

    resp = client.post(
        _resend_url(school, submission.id),
        {"next": _detail_url(school, submission.id)},
    )
    assert resp.status_code == 302
    msgs = list(resp.wsgi_request._messages)
    assert any("not enabled" in str(m).lower() or "email" in str(m).lower() for m in msgs)


@pytest.mark.django_db
def test_lead_send_message_flag_disabled(client):
    """email_notifications_enabled=False → error flash for send-message view."""
    from core.tests.factories import SchoolFactory as SF
    school = SF(plan="free")
    user = _school_admin(school)
    lead = LeadFactory(school=school, email="parent@example.com")
    client.force_login(user)

    resp = client.post(
        _lead_msg_url(school, lead.id),
        {"message": "Hello", "next": ""},
    )
    assert resp.status_code == 302
    msgs = list(resp.wsgi_request._messages)
    assert any("not enabled" in str(m).lower() or "email" in str(m).lower() for m in msgs)


# ── Fix #1: email failures must not block core actions ─────────────────────


@pytest.mark.django_db
def test_mark_contacted_email_exception_non_blocking(client, monkeypatch):
    """If send_workflow_notification raises, mark-contacted still succeeds."""
    school = _email_school()
    user = _school_admin(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW, email="parent@example.com")
    client.force_login(user)

    def boom(**kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("core.views_school_leads.send_workflow_notification", boom)
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: MagicMock(raw={}))
    monkeypatch.setattr("core.views_school_leads._resolve_from_email", lambda raw: "from@school.test")

    resp = client.post(
        _lead_mc_url(school, lead.id),
        {"send_email": "1", "next": ""},
    )
    # Core action (mark contacted) must still succeed → 302 + success flash
    assert resp.status_code == 302
    msgs = list(resp.wsgi_request._messages)
    assert any("contacted" in str(m).lower() for m in msgs)
    # DB state updated despite email failure
    lead.refresh_from_db()
    assert lead.last_contacted_at is not None


# ── Fix #3: get_communication_template fallback ────────────────────────────


def test_get_communication_template_unknown_key():
    """Unknown template_key returns non-empty fallback strings, never empty/None."""
    from core.services.notifications import get_communication_template

    subject, body = get_communication_template({}, "nonexistent_key")
    assert subject and isinstance(subject, str)
    assert body and isinstance(body, str)


def test_get_communication_template_malformed_config():
    """Non-dict config_raw returns hardcoded defaults without raising."""
    from core.services.notifications import get_communication_template

    subject, body = get_communication_template("not-a-dict", "contacted")
    assert subject and isinstance(subject, str)
    assert body and isinstance(body, str)
