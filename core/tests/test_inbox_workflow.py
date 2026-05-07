"""
Phase 6 — Inbox Workflow + Light CRM Layer.

Covers:
  - Leads list: overdue follow-up sorted before new leads
  - Leads list: upcoming follow-up sorted before leads with no follow-up
  - Leads list: metrics context (new / contacted / enrolled counts)
  - Leads list: quick_actions contains only contacted/lost transitions
  - Submissions list: NEW submissions sorted before non-new
  - Submissions list: metrics context (new count + total)
"""
from __future__ import annotations

import pytest
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from core.models import Lead, LEAD_STATUS_NEW
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _school_admin_user(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _leads_url(school):
    return reverse("school_leads", kwargs={"school_slug": school.slug})


def _submissions_url(school):
    return reverse("school_submissions", kwargs={"school_slug": school.slug})


# ── Lead sort tests ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_leads_overdue_sorted_before_new(client):
    """Leads with an overdue follow-up appear before plain new leads in the list."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    # Plain new lead — no follow-up date.
    new_lead = LeadFactory(school=school, status=LEAD_STATUS_NEW, next_follow_up_at=None)
    # Lead with overdue follow-up (3 days ago).
    overdue_lead = LeadFactory(
        school=school,
        status=LEAD_STATUS_NEW,
        next_follow_up_at=timezone.now() - timedelta(days=3),
    )

    response = client.get(_leads_url(school))

    assert response.status_code == 200
    lead_ids = [lead["id"] for lead in response.context["leads"]]
    assert lead_ids.index(overdue_lead.id) < lead_ids.index(new_lead.id)


@pytest.mark.django_db
def test_leads_upcoming_sorted_before_no_followup(client):
    """Lead with a future follow-up date sorts before a new lead with no follow-up."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    no_followup_lead = LeadFactory(school=school, status=LEAD_STATUS_NEW, next_follow_up_at=None)
    upcoming_lead = LeadFactory(
        school=school,
        status=LEAD_STATUS_NEW,
        next_follow_up_at=timezone.now() + timedelta(days=5),
    )

    response = client.get(_leads_url(school))

    assert response.status_code == 200
    lead_ids = [lead["id"] for lead in response.context["leads"]]
    assert lead_ids.index(upcoming_lead.id) < lead_ids.index(no_followup_lead.id)


@pytest.mark.django_db
def test_leads_metrics_in_context(client):
    """Context includes leads_metrics with correct new/contacted/enrolled counts."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    LeadFactory(school=school, status="new")
    LeadFactory(school=school, status="new")
    LeadFactory(school=school, status="contacted")
    LeadFactory(school=school, status="enrolled")

    response = client.get(_leads_url(school))

    assert response.status_code == 200
    metrics = response.context["leads_metrics"]
    assert metrics["new"] == 2
    assert metrics["contacted"] == 1
    assert metrics["enrolled"] == 1


@pytest.mark.django_db
def test_lead_quick_actions_filtered_to_contacted_and_lost(client, monkeypatch):
    """quick_actions in each row contains only transitions targeting 'contacted' or 'lost'."""
    from unittest.mock import MagicMock

    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    # YAML workflow with three transitions: contacted, trial_scheduled, lost.
    mock = MagicMock()
    mock.raw = {
        "admin": {
            "lead_workflow": {
                "transitions": {
                    "new": [
                        {"label": "Mark Contacted", "status": "contacted"},
                        {"label": "Schedule Trial", "status": "trial_scheduled"},
                        {"label": "Lost", "status": "lost"},
                    ]
                }
            }
        }
    }
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: mock)

    LeadFactory(school=school, status=LEAD_STATUS_NEW)

    response = client.get(_leads_url(school))

    assert response.status_code == 200
    row = response.context["leads"][0]
    quick_statuses = {t["status"] for t in row["quick_actions"]}
    # Only contacted and lost — trial_scheduled must be absent.
    assert quick_statuses == {"contacted", "lost"}
    # Full transitions still present (used on detail page).
    assert len(row["transitions"]) == 3


# ── Submission sort + metrics tests ─────────────────────────────────────────


@pytest.mark.django_db
def test_submissions_new_sorted_first(client):
    """NEW submissions appear before non-New submissions in the list."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    reviewed = SubmissionFactory(school=school, status="Reviewed")
    new_sub = SubmissionFactory(school=school, status="New")

    response = client.get(_submissions_url(school))

    assert response.status_code == 200
    sub_ids = [s["id"] for s in response.context["submissions"]]
    assert sub_ids.index(new_sub.id) < sub_ids.index(reviewed.id)


@pytest.mark.django_db
def test_submissions_metrics_in_context(client):
    """Context includes submissions_metrics with correct new count and total."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    SubmissionFactory(school=school, status="New")
    SubmissionFactory(school=school, status="New")
    SubmissionFactory(school=school, status="Reviewed")

    response = client.get(_submissions_url(school))

    assert response.status_code == 200
    metrics = response.context["submissions_metrics"]
    assert metrics["new"] == 2
    assert metrics["total"] == 3
