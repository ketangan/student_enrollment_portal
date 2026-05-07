"""
Phase 5.1 — Lead Detail UX improvements.

Covers:
  - Mark Contacted: sets last_contacted_at, clears overdue follow-up
  - Success message after lead update
  - is_followup_overdue context flag on lead detail page
"""
from __future__ import annotations

import pytest
from datetime import timedelta
from unittest.mock import MagicMock

from django.urls import reverse
from django.utils import timezone

from core.models import Lead, LEAD_STATUS_NEW
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


def _make_mock_config():
    """Minimal config that allows new→contacted and contacted→trial_scheduled."""
    mock = MagicMock()
    mock.raw = {
        "admin": {
            "lead_workflow": {
                "transitions": {
                    "new": [{"label": "Mark Contacted", "status": "contacted"}],
                    "contacted": [{"label": "Schedule Trial", "status": "trial_scheduled"}],
                }
            }
        }
    }
    return mock


def _status_url(school, lead_id):
    return reverse(
        "school_lead_status_update",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _lead_detail_url(school, lead_id):
    return reverse(
        "school_lead_detail",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_mark_contacted_sets_last_contacted_at(client, monkeypatch):
    """Transitioning to 'contacted' sets lead.last_contacted_at to now."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW, last_contacted_at=None)
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: _make_mock_config())
    client.force_login(user)

    response = client.post(_status_url(school, lead.id), {"new_status": "contacted"})

    assert response.status_code == 302
    lead.refresh_from_db()
    assert lead.last_contacted_at is not None
    # Should be within the last few seconds
    assert (timezone.now() - lead.last_contacted_at).total_seconds() < 5


@pytest.mark.django_db
def test_mark_contacted_clears_overdue_followup(client, monkeypatch):
    """Transitioning to 'contacted' clears a follow-up date that is in the past."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    past_date = timezone.now() - timedelta(days=3)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW, next_follow_up_at=past_date)
    monkeypatch.setattr("core.views_school_common.load_school_config", lambda slug: _make_mock_config())
    client.force_login(user)

    client.post(_status_url(school, lead.id), {"new_status": "contacted"})

    lead.refresh_from_db()
    assert lead.next_follow_up_at is None


@pytest.mark.django_db
def test_success_message_on_lead_update(client):
    """Saving notes/follow-up shows 'Lead updated successfully' on the next page."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    lead = LeadFactory(school=school)
    update_url = reverse(
        "school_lead_update",
        kwargs={"school_slug": school.slug, "lead_id": lead.id},
    )
    detail_url = _lead_detail_url(school, lead.id)
    client.force_login(user)

    response = client.post(
        update_url,
        {
            "name": lead.name,
            "email": lead.email or "",
            "phone": lead.phone or "",
            "interested_in_value": lead.interested_in_value or "",
            "new_note": "testing message",
            "next_follow_up_at": "",
            "next": detail_url,
        },
        follow=True,
    )

    assert response.status_code == 200
    assert "Lead updated successfully" in response.content.decode()


@pytest.mark.django_db
def test_overdue_flag_true(client):
    """Lead detail context has is_followup_overdue=True when follow-up is in the past."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    past = timezone.now() - timedelta(days=2)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW, next_follow_up_at=past)
    client.force_login(user)

    response = client.get(_lead_detail_url(school, lead.id))

    assert response.status_code == 200
    assert response.context["is_followup_overdue"] is True


@pytest.mark.django_db
def test_overdue_flag_false(client):
    """Lead detail context has is_followup_overdue=False when follow-up is in the future."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    future = timezone.now() + timedelta(days=5)
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW, next_follow_up_at=future)
    client.force_login(user)

    response = client.get(_lead_detail_url(school, lead.id))

    assert response.status_code == 200
    assert response.context["is_followup_overdue"] is False
