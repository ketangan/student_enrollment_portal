"""
Phase 15 — Demo Readiness + Admin UX Polish.

Covers:
  - Dashboard "Enrolled" label (not "Approved")
  - Dashboard "New Leads" card appears for leads-enabled schools
  - Dashboard new_leads_count context value is correct
  - Reports page renders empty state when no submissions exist
  - Reports page does NOT show empty state when submissions exist
  - Lead detail: lost lead has no enrollment CTA
  - Lead detail: converted lead shows "View Submission" link
  - Lead detail: active lead shows enrollment/start button area
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import LEAD_STATUS_LOST, LEAD_STATUS_NEW, LEAD_STATUS_ENROLLED
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _full_school():
    """Trial school with all features enabled."""
    return SchoolFactory(plan="trial")


def _school_admin(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _dashboard_url(school):
    return reverse("school_dashboard", kwargs={"school_slug": school.slug})


def _reports_url(school):
    return reverse("school_reports", kwargs={"school_slug": school.slug})


def _lead_detail_url(school, lead):
    return reverse("school_lead_detail", kwargs={"school_slug": school.slug, "lead_id": lead.id})


# ── Dashboard label: Enrolled (not Approved) ──────────────────────────────────


@pytest.mark.django_db
def test_dashboard_enrolled_label_not_approved(client):
    """
    Dashboard must say 'Enrolled', never 'Approved'.
    The old label 'Approved' was incorrect terminology.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    SubmissionFactory(school=school, status="Enrolled")

    resp = client.get(_dashboard_url(school))
    assert resp.status_code == 200

    content = resp.content.decode()
    assert "Enrolled" in content
    # "Approved" should not appear as a card label (may appear in status values, so check
    # specifically for the label text that was wrong)
    assert "See approved" not in content.lower()


# ── Dashboard: New Leads card ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_dashboard_new_leads_card_appears(client):
    """
    When leads_enabled and new leads exist, dashboard includes a 'New Leads' card
    with a link to the leads list filtered by status=new.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    LeadFactory(school=school, status=LEAD_STATUS_NEW)
    LeadFactory(school=school, status=LEAD_STATUS_NEW)

    resp = client.get(_dashboard_url(school))
    assert resp.status_code == 200

    content = resp.content.decode()
    assert "New Leads" in content
    assert "?status=new" in content


@pytest.mark.django_db
def test_dashboard_new_leads_count_correct(client):
    """
    new_leads_count in context matches only LEAD_STATUS_NEW leads (not contacted/lost).
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    LeadFactory(school=school, status=LEAD_STATUS_NEW)
    LeadFactory(school=school, status=LEAD_STATUS_NEW)
    LeadFactory(school=school, status=LEAD_STATUS_LOST)  # should not count

    resp = client.get(_dashboard_url(school))
    assert resp.status_code == 200
    assert resp.context["new_leads_count"] == 2


@pytest.mark.django_db
def test_dashboard_new_leads_card_absent_without_leads(client):
    """
    School without leads feature enabled must NOT show the New Leads card.
    """
    school = SchoolFactory(plan="trial", feature_flags={"leads_enabled": False})
    user = _school_admin(school)
    client.force_login(user)

    resp = client.get(_dashboard_url(school))
    assert resp.status_code == 200

    content = resp.content.decode()
    # Card should be absent when leads are disabled
    assert "New Leads" not in content


# ── Reports: empty state ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_reports_empty_state_when_no_submissions(client):
    """
    Reports page renders 200 and shows the 'no data yet' empty state when school has zero submissions.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200

    content = resp.content.decode()
    assert "no data yet" in content  # shown in KPI tiles when value is None


@pytest.mark.django_db
def test_reports_no_empty_state_when_submissions_exist(client):
    """
    Reports page shows a real enrollment-rate value when an enrolled submission exists.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    SubmissionFactory(school=school, status="Enrolled")
    SubmissionFactory(school=school, status="In Review")

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200

    # App→Enrolled rate tile: 1 enrolled of 2 total = 50.0%
    tile = resp.context["kpi_tiles"][0]
    assert tile["value"] == "50.0%"
    assert tile["basis"] == "1 of 2"


# ── Lead detail: primary CTA based on status ─────────────────────────────────


@pytest.mark.django_db
def test_lead_detail_lost_has_no_enrollment_cta(client):
    """
    A lost lead's detail page must not offer an enrollment button.
    It should indicate the lead is lost with no further enrollment action.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    lead = LeadFactory(school=school, status=LEAD_STATUS_LOST)

    resp = client.get(_lead_detail_url(school, lead))
    assert resp.status_code == 200

    content = resp.content.decode()
    # Should NOT show start enrollment or open form
    assert "Start Enrollment" not in content
    assert "Open Form" not in content
    # Should show the lost indicator
    assert "marked as lost" in content.lower()


@pytest.mark.django_db
def test_lead_detail_converted_shows_view_submission(client):
    """
    A converted lead's detail page shows 'View Submission' link, not enrollment form.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    sub = SubmissionFactory(school=school)
    lead = LeadFactory(school=school, converted_submission=sub, status=LEAD_STATUS_ENROLLED)

    resp = client.get(_lead_detail_url(school, lead))
    assert resp.status_code == 200

    content = resp.content.decode()
    assert "View Submission" in content
    assert "Start Enrollment" not in content
    assert "Open Form" not in content


@pytest.mark.django_db
def test_lead_detail_active_lead_shows_enrollment_action(client):
    """
    An active (new) lead's detail page offers an enrollment action — either
    'Start Enrollment' or 'Open Form' (if a draft already exists).
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)

    resp = client.get(_lead_detail_url(school, lead))
    assert resp.status_code == 200

    content = resp.content.decode()
    # Either start enrollment or open form must be present
    assert "Start Enrollment" in content or "Open Form" in content
