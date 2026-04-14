# core/tests/test_lead_admin.py
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AdminAuditLog, Lead, Submission
from core.tests.factories import LeadFactory, SchoolAdminMembershipFactory, SchoolFactory, SubmissionFactory, UserFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _staff_client(client, school):
    """Create a staff user with school membership, log them in, return user."""
    membership = SchoolAdminMembershipFactory(school=school)
    client.force_login(membership.user)
    return membership.user


def _superuser_client(client):
    user = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(user)
    return user


# ---------------------------------------------------------------------------
# 4b: quick_add_view
# ---------------------------------------------------------------------------

QUICK_ADD_URL = "/admin/core/lead/quick_add/"


@pytest.mark.django_db
def test_quick_add_creates_lead(client):
    school = SchoolFactory(plan="starter", slug="qa-school")
    _staff_client(client, school)

    response = client.post(QUICK_ADD_URL, {
        "name": "Jane Caller",
        "email": "jane@example.com",
        "phone": "555-1234",
        "interested_in_label": "Ballet",
        "source": "phone",
        "notes": "Called Monday afternoon",
    })

    assert response.status_code == 302
    lead = Lead.objects.get(school=school, email="jane@example.com")
    assert lead.name == "Jane Caller"
    assert lead.phone == "555-1234"
    assert lead.source == "phone"
    assert lead.notes == "Called Monday afternoon"
    assert lead.interested_in_label == "Ballet"


@pytest.mark.django_db
def test_quick_add_missing_name_no_create(client):
    school = SchoolFactory(plan="starter", slug="qa-missing-name")
    _staff_client(client, school)

    response = client.post(QUICK_ADD_URL, {"email": "x@example.com"})

    assert response.status_code == 302
    assert Lead.objects.filter(school=school).count() == 0


@pytest.mark.django_db
def test_quick_add_missing_email_no_create(client):
    school = SchoolFactory(plan="starter", slug="qa-missing-email")
    _staff_client(client, school)

    response = client.post(QUICK_ADD_URL, {"name": "Bob"})

    assert response.status_code == 302
    assert Lead.objects.filter(school=school).count() == 0


@pytest.mark.django_db
def test_quick_add_duplicate_email_no_create(client):
    school = SchoolFactory(plan="starter", slug="qa-dup-email")
    _staff_client(client, school)
    LeadFactory(school=school, email="dup@example.com")

    response = client.post(QUICK_ADD_URL, {
        "name": "Dupe Person",
        "email": "dup@example.com",
        "source": "referral",
    })

    assert response.status_code == 302
    assert Lead.objects.filter(school=school, email="dup@example.com").count() == 1


@pytest.mark.django_db
def test_quick_add_missing_source_no_create(client):
    school = SchoolFactory(plan="starter", slug="qa-missing-source")
    _staff_client(client, school)

    response = client.post(QUICK_ADD_URL, {"name": "No Source", "email": "nosource@example.com"})

    assert response.status_code == 302
    assert Lead.objects.filter(school=school).count() == 0


@pytest.mark.django_db
def test_quick_add_auto_assigns_school(client):
    """Lead is assigned to the staff user's school, not any other school."""
    school = SchoolFactory(plan="starter", slug="qa-auto-school")
    other_school = SchoolFactory(plan="starter", slug="qa-other-school")
    _staff_client(client, school)

    client.post(QUICK_ADD_URL, {"name": "Auto", "email": "auto@example.com", "source": "walk_in"})

    assert Lead.objects.filter(school=school, email="auto@example.com").exists()
    assert not Lead.objects.filter(school=other_school, email="auto@example.com").exists()


@pytest.mark.django_db
def test_quick_add_superuser_rejected(client):
    """Superusers cannot use quick-add (they have the full admin form)."""
    _superuser_client(client)

    response = client.post(QUICK_ADD_URL, {
        "name": "Super",
        "email": "super@example.com",
    })

    assert response.status_code == 302
    assert Lead.objects.filter(email="super@example.com").count() == 0


@pytest.mark.django_db
def test_quick_add_get_request_redirects(client):
    """GET on quick_add redirects back to changelist without error."""
    school = SchoolFactory(plan="starter", slug="qa-get-req")
    _staff_client(client, school)

    response = client.get(QUICK_ADD_URL)
    assert response.status_code == 302


@pytest.mark.django_db
def test_quick_add_unauthenticated_redirects(client):
    """Unauthenticated request is redirected to login."""
    response = client.post(QUICK_ADD_URL, {"name": "X", "email": "x@x.com"})
    assert response.status_code == 302
    assert "/login/" in response["Location"] or "login" in response["Location"]


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_audit_quick_add_logs_add_entry(client):
    """quick_add_view logs an 'add' audit entry when audit_log_enabled."""
    school = SchoolFactory(plan="starter", slug="qa-audit-add", feature_flags={"audit_log_enabled": True})
    _staff_client(client, school)

    client.post(QUICK_ADD_URL, {
        "name": "Audit Test",
        "email": "audit@example.com",
        "source": "phone",
    })

    lead = Lead.objects.get(email="audit@example.com")
    log = AdminAuditLog.objects.filter(
        action="add",
        model_label="core.lead",
        object_id=str(lead.pk),
    ).first()
    assert log is not None
    assert log.extra["name"] == "quick_add"
    assert log.extra["source"] == "phone"


@pytest.mark.django_db
def test_lead_audit_quick_add_no_log_when_flag_disabled(client):
    """quick_add_view does NOT log when audit_log_enabled is explicitly disabled."""
    school = SchoolFactory(plan="starter", slug="qa-audit-off", feature_flags={"audit_log_enabled": False})
    _staff_client(client, school)

    client.post(QUICK_ADD_URL, {
        "name": "No Audit",
        "email": "noaudit@example.com",
        "source": "website",
    })

    assert AdminAuditLog.objects.filter(action="add", model_label="core.lead").count() == 0


@pytest.mark.django_db
def test_lead_audit_save_model_logs_status_change(client):
    """save_model logs a 'change' audit entry only when status changes."""
    from django.contrib.admin.sites import site as admin_site
    from django.test import RequestFactory
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.contrib.messages.storage.fallback import FallbackStorage
    from core.admin.leads import LeadAdmin

    school = SchoolFactory(plan="starter", feature_flags={"audit_log_enabled": True})
    lead = LeadFactory(school=school, status="new")

    user = UserFactory(is_staff=True, is_superuser=True)
    req = RequestFactory().post("/")
    req.user = user
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    setattr(req, "_messages", FallbackStorage(req))

    lead.status = "contacted"
    ma = LeadAdmin(Lead, admin_site)
    ma.save_model(req, lead, form=None, change=True)

    log = AdminAuditLog.objects.filter(action="change", object_id=str(lead.pk)).first()
    assert log is not None
    assert log.changes == {"status": {"from": "new", "to": "contacted"}}


@pytest.mark.django_db
def test_lead_audit_save_model_no_log_when_status_unchanged(client):
    """save_model does NOT create an audit row when status has not changed."""
    from django.contrib.admin.sites import site as admin_site
    from django.test import RequestFactory
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.contrib.messages.storage.fallback import FallbackStorage
    from core.admin.leads import LeadAdmin

    school = SchoolFactory(plan="starter", feature_flags={"audit_log_enabled": True})
    lead = LeadFactory(school=school, status="new")

    user = UserFactory(is_staff=True, is_superuser=True)
    req = RequestFactory().post("/")
    req.user = user
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    setattr(req, "_messages", FallbackStorage(req))

    # Save with no status change — only notes changed
    lead.notes = "Updated notes"
    ma = LeadAdmin(Lead, admin_site)
    ma.save_model(req, lead, form=None, change=True)

    assert AdminAuditLog.objects.filter(action="change", object_id=str(lead.pk)).count() == 0


@pytest.mark.django_db
def test_lead_audit_bulk_action_logs_updated_count(client):
    """Bulk action logs the actual updated count, not the queryset size."""
    from django.contrib.admin.sites import site as admin_site
    from django.test import RequestFactory
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.contrib.messages.storage.fallback import FallbackStorage
    from core.admin.leads import LeadAdmin

    school = SchoolFactory(plan="starter", feature_flags={"audit_log_enabled": True})
    # 3 new leads — all eligible for mark_contacted
    leads = LeadFactory.create_batch(3, school=school, status="new")

    user = UserFactory(is_staff=True, is_superuser=True)
    req = RequestFactory().post("/")
    req.user = user
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    setattr(req, "_messages", FallbackStorage(req))

    ma = LeadAdmin(Lead, admin_site)
    qs = Lead.objects.filter(school=school)
    ma.action_mark_contacted(req, qs)

    log = AdminAuditLog.objects.filter(action="action").first()
    assert log is not None
    assert log.extra["name"] == "mark_contacted"
    assert log.extra["count"] == 3


# ---------------------------------------------------------------------------
# convert_to_submission_view
# ---------------------------------------------------------------------------

CONVERT_URL = "/admin/core/lead/{lead_id}/convert-to-submission/"


def _make_config_with_email(email_key="contact_email"):
    """Config object (with .raw) declaring a single email field."""
    class _Cfg:
        raw = {
            "form": {
                "sections": [{
                    "title": "Contact",
                    "fields": [{"key": email_key, "label": "Email", "type": "email", "required": True}],
                }]
            }
        }
    return _Cfg()


def _make_config_no_email():
    """Config object with no email field — simulates a misconfigured school YAML."""
    class _Cfg:
        raw = {
            "form": {
                "sections": [{
                    "title": "Info",
                    "fields": [{"key": "first_name", "label": "Name", "type": "text", "required": True}],
                }]
            }
        }
    return _Cfg()


@pytest.mark.django_db
def test_convert_to_submission_happy_path(client, monkeypatch):
    """Successful conversion: submission created, lead linked, redirects to submission change page."""
    school = SchoolFactory(plan="pro", slug="cv-happy")
    lead = LeadFactory(school=school, email="happy@example.com")
    _staff_client(client, school)
    monkeypatch.setattr("core.services.config_loader.load_school_config", lambda slug: _make_config_with_email())

    url = CONVERT_URL.format(lead_id=lead.pk)
    response = client.post(url)

    lead.refresh_from_db()
    assert lead.converted_submission_id is not None

    submission = Submission.objects.get(pk=lead.converted_submission_id)
    assert submission.school == school

    # Redirects to the submission change page
    assert response.status_code == 302
    assert f"/admin/core/submission/{submission.pk}/change/" in response["Location"]


@pytest.mark.django_db
def test_convert_to_submission_uses_yaml_email_key(client, monkeypatch):
    """Submission data uses the YAML-declared email key, not a hardcoded key."""
    school = SchoolFactory(plan="pro", slug="cv-yaml-key")
    lead = LeadFactory(school=school, email="guardian@example.com")
    _staff_client(client, school)
    monkeypatch.setattr(
        "core.services.config_loader.load_school_config",
        lambda slug: _make_config_with_email("guardian_email"),
    )

    url = CONVERT_URL.format(lead_id=lead.pk)
    client.post(url)

    lead.refresh_from_db()
    assert lead.converted_submission_id is not None
    submission = Submission.objects.get(pk=lead.converted_submission_id)
    # Email must be stored under the YAML key, not contact_email
    assert submission.data.get("guardian_email") == "guardian@example.com"
    assert "contact_email" not in submission.data


@pytest.mark.django_db
def test_convert_to_submission_already_converted_is_idempotent(client, monkeypatch):
    """Already-converted lead: warning returned, no new submission created."""
    school = SchoolFactory(plan="pro", slug="cv-idempotent")
    existing_submission = SubmissionFactory(school=school)
    lead = LeadFactory(school=school, email="already@example.com", converted_submission=existing_submission)
    _staff_client(client, school)
    monkeypatch.setattr("core.services.config_loader.load_school_config", lambda slug: _make_config_with_email())

    url = CONVERT_URL.format(lead_id=lead.pk)
    response = client.post(url)

    # No new submission should be created
    assert Submission.objects.filter(school=school).count() == 1
    assert response.status_code == 302
    # Redirects back to lead change page
    assert f"/admin/core/lead/{lead.pk}/change/" in response["Location"]


@pytest.mark.django_db
def test_convert_to_submission_get_returns_405(client, monkeypatch):
    """GET on convert_to_submission returns 405 Method Not Allowed."""
    school = SchoolFactory(plan="pro", slug="cv-get-405")
    lead = LeadFactory(school=school, email="get@example.com")
    _staff_client(client, school)
    monkeypatch.setattr("core.services.config_loader.load_school_config", lambda slug: _make_config_with_email())

    url = CONVERT_URL.format(lead_id=lead.pk)
    response = client.get(url)

    assert response.status_code == 405
    assert Submission.objects.filter(school=school).count() == 0


@pytest.mark.django_db
def test_convert_to_submission_no_email_field_in_yaml(client, monkeypatch):
    """No email field in YAML: error shown, no submission created."""
    school = SchoolFactory(plan="pro", slug="cv-no-email-yaml")
    lead = LeadFactory(school=school, email="noemail@example.com")
    _staff_client(client, school)
    monkeypatch.setattr("core.services.config_loader.load_school_config", lambda slug: _make_config_no_email())

    url = CONVERT_URL.format(lead_id=lead.pk)
    response = client.post(url)

    assert response.status_code == 302
    assert Submission.objects.filter(school=school).count() == 0
    lead.refresh_from_db()
    assert lead.converted_submission_id is None


@pytest.mark.django_db
def test_convert_to_submission_feature_disabled_cleans_up(client, monkeypatch):
    """Starter plan (leads_conversion_enabled=False): try_convert_lead returns None → submission deleted."""
    school = SchoolFactory(plan="starter", slug="cv-disabled")
    # Manually enable leads_enabled so the view is reachable, but not leads_conversion_enabled
    school.feature_flags = {"leads_enabled": True}
    school.save()

    lead = LeadFactory(school=school, email="starter@example.com")
    _staff_client(client, school)
    monkeypatch.setattr("core.services.config_loader.load_school_config", lambda slug: _make_config_with_email())

    url = CONVERT_URL.format(lead_id=lead.pk)
    response = client.post(url)

    assert response.status_code == 302
    # Submission must have been cleaned up
    assert Submission.objects.filter(school=school).count() == 0
    lead.refresh_from_db()
    assert lead.converted_submission_id is None


@pytest.mark.django_db
def test_convert_to_submission_unauthenticated_redirects_to_login(client, monkeypatch):
    """Unauthenticated request redirects to the login page."""
    school = SchoolFactory(plan="pro", slug="cv-unauth")
    lead = LeadFactory(school=school)

    url = CONVERT_URL.format(lead_id=lead.pk)
    response = client.post(url)

    assert response.status_code == 302
    assert "login" in response["Location"]


@pytest.mark.django_db
def test_convert_to_submission_audit_log_created(client, monkeypatch):
    """Successful conversion logs an 'action' audit entry when audit_log_enabled."""
    school = SchoolFactory(plan="pro", slug="cv-audit", feature_flags={"audit_log_enabled": True})
    lead = LeadFactory(school=school, email="audit-cv@example.com")
    _staff_client(client, school)
    monkeypatch.setattr("core.services.config_loader.load_school_config", lambda slug: _make_config_with_email())

    url = CONVERT_URL.format(lead_id=lead.pk)
    client.post(url)

    log = AdminAuditLog.objects.filter(action="action", model_label="core.lead", object_id=str(lead.pk)).first()
    assert log is not None
    assert log.extra["name"] == "convert_to_submission"
