"""
Phase 5 — Lead Edit (notes + follow-up date).

Covers:
  - school_lead_update_view: field updates, redirect, audit log, permissions
  - Quick follow-up delta buttons (+N days server-side)
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, Lead
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    UserFactory,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _school_admin_user(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _update_url(school, lead_id):
    return reverse(
        "school_lead_update",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _update_data(lead, **overrides):
    """Build a minimal valid POST payload for school_lead_update_view.
    Passes through the lead's current name/email so callers that only want to
    test notes or follow-up don't need to repeat those required fields.
    """
    base = {
        "name": lead.name,
        "email": lead.email or "",
        "phone": lead.phone or "",
        "interested_in_value": lead.interested_in_value or "",
        "new_note": "",
        "next_follow_up_at": "",
    }
    base.update(overrides)
    return base


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_update_notes_success(client):
    """POST with notes updates lead.notes."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, notes="")
    client.force_login(user)

    response = client.post(_update_url(school, lead.id),
        _update_data(lead, new_note="Called on Monday, very interested.")
    )

    assert response.status_code == 302
    lead.refresh_from_db()
    assert "Called on Monday, very interested." in lead.notes


@pytest.mark.django_db
def test_update_followup_success(client):
    """POST with next_follow_up_at stores a datetime matching the given date."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school)
    client.force_login(user)

    response = client.post(_update_url(school, lead.id),
        _update_data(lead, next_follow_up_at="2026-06-15")
    )

    assert response.status_code == 302
    lead.refresh_from_db()
    assert lead.next_follow_up_at is not None
    assert lead.next_follow_up_at.date() == date(2026, 6, 15)


@pytest.mark.django_db
def test_update_both_fields(client):
    """POST with both fields updates both."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, notes="old note")
    client.force_login(user)

    response = client.post(_update_url(school, lead.id),
        _update_data(lead, new_note="new note", next_follow_up_at="2026-07-01")
    )

    assert response.status_code == 302
    lead.refresh_from_db()
    assert "new note" in lead.notes
    assert lead.next_follow_up_at.date() == date(2026, 7, 1)


@pytest.mark.django_db
def test_update_clears_followup_when_blank(client):
    """Submitting a blank next_follow_up_at clears the field."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, next_follow_up_at=timezone.now())
    client.force_login(user)

    client.post(_update_url(school, lead.id),
        _update_data(lead, notes="", next_follow_up_at="")
    )

    lead.refresh_from_db()
    assert lead.next_follow_up_at is None


@pytest.mark.django_db
def test_update_redirects_to_next(client):
    """POST with next= redirects to that URL."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school)
    detail_url = reverse("school_lead_detail", kwargs={"school_slug": school.slug, "lead_id": lead.id})
    client.force_login(user)

    response = client.post(_update_url(school, lead.id),
        _update_data(lead, notes="", next_follow_up_at="", next=detail_url)
    )

    assert response.status_code == 302
    assert response["Location"] == detail_url


@pytest.mark.django_db
def test_quick_followup_delta(client):
    """Submitting follow_up_delta=7 sets next_follow_up_at to today + 7 days."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school)
    client.force_login(user)

    response = client.post(_update_url(school, lead.id),
        _update_data(lead, notes="", next_follow_up_at="", follow_up_delta="7")
    )

    assert response.status_code == 302
    lead.refresh_from_db()
    assert lead.next_follow_up_at is not None
    expected = timezone.now().date() + timedelta(days=7)
    assert lead.next_follow_up_at.date() == expected


@pytest.mark.django_db
def test_audit_log_created(client):
    """Successful POST creates an AdminAuditLog entry with name='lead_update'."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school)
    client.force_login(user)

    client.post(_update_url(school, lead.id),
        _update_data(lead, new_note="audit test")
    )

    log = AdminAuditLog.objects.filter(
        model_label="core.lead",
        object_id=str(lead.pk),
    ).first()
    assert log is not None
    assert log.extra.get("name") == "lead_update"
    assert log.actor == user


@pytest.mark.django_db
def test_cross_school_404(client):
    """Admin of school_a cannot update school_b's lead."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    lead_b = LeadFactory(school=school_b)
    client.force_login(user)

    response = client.post(
        reverse("school_lead_update", kwargs={"school_slug": school_a.slug, "lead_id": lead_b.id}),
        _update_data(lead_b, new_note="hacked"),
    )

    assert response.status_code == 404
    lead_b.refresh_from_db()
    assert "hacked" not in (lead_b.notes or "")


@pytest.mark.django_db
def test_unauthenticated_redirect(client):
    """Unauthenticated POST is redirected to login."""
    school = SchoolFactory()
    lead = LeadFactory(school=school)

    response = client.post(_update_url(school, lead.id), {
        "notes": "anon",
        "next_follow_up_at": "",
    })

    assert response.status_code == 302
    assert "login" in response["Location"]


@pytest.mark.django_db
def test_invalid_date_does_not_save(client):
    """Invalid next_follow_up_at shows error and does not update the lead."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, notes="original")
    client.force_login(user)

    response = client.post(_update_url(school, lead.id),
        _update_data(lead, notes="should not save", next_follow_up_at="not-a-date"),
        follow=True,
    )

    assert response.status_code == 200
    lead.refresh_from_db()
    assert lead.notes == "original"


@pytest.mark.django_db
def test_invalid_delta_does_not_save(client):
    """Invalid follow_up_delta shows error and does not update the lead."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, notes="original")
    client.force_login(user)

    response = client.post(_update_url(school, lead.id),
        _update_data(lead, notes="should not save", next_follow_up_at="", follow_up_delta="badvalue"),
        follow=True,
    )

    assert response.status_code == 200
    lead.refresh_from_db()
    assert lead.notes == "original"
