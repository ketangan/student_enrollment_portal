# core/tests/test_lead_admin.py
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import AdminAuditLog, Lead
from core.tests.factories import LeadFactory, SchoolAdminMembershipFactory, SchoolFactory, UserFactory


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
