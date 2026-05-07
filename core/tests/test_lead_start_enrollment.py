"""
Phase 3 — Lead-to-Submission Conversion tests.

Covers:
  - try_convert_lead() status update: sets enrolled, protects lost leads
  - get_lead_workflow_transitions() enforcement of enrolled as terminal
"""
from __future__ import annotations

import pytest

from core.models import (
    LEAD_STATUS_ENROLLED,
    LEAD_STATUS_LOST,
    LEAD_STATUS_NEW,
)
from core.services.admin_lead_yaml import get_lead_workflow_transitions
from core.services.lead_conversion import try_convert_lead
from core.tests.factories import (
    LeadFactory,
    SchoolFactory,
    SubmissionFactory,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _config_with_email(key="contact_email", required=True):
    """Minimal single-form config with one email field."""
    return {
        "form": {
            "sections": [
                {
                    "title": "Contact",
                    "fields": [
                        {"key": key, "label": "Email", "type": "email", "required": required},
                    ],
                }
            ]
        }
    }


# ── 1. try_convert_lead status fix ────────────────────────────────────────


@pytest.mark.django_db
def test_try_convert_lead_sets_enrolled_status():
    """Conversion advances lead.status to 'enrolled' for active pipeline leads."""
    school = SchoolFactory(plan="pro")
    lead = LeadFactory(school=school, email="enroll@example.com", status=LEAD_STATUS_NEW)
    submission = SubmissionFactory(
        school=school, data={"contact_email": "enroll@example.com"}
    )

    result = try_convert_lead(
        school=school, submission=submission, config_raw=_config_with_email()
    )

    assert result is not None
    lead.refresh_from_db()
    assert lead.status == LEAD_STATUS_ENROLLED
    assert lead.converted_submission_id == submission.id


@pytest.mark.django_db
def test_try_convert_lead_does_not_overwrite_lost_status():
    """A lost lead is linked to the submission but its status must stay 'lost'."""
    school = SchoolFactory(plan="pro")
    lead = LeadFactory(school=school, email="lost@example.com", status=LEAD_STATUS_LOST)
    submission = SubmissionFactory(
        school=school, data={"contact_email": "lost@example.com"}
    )

    result = try_convert_lead(
        school=school, submission=submission, config_raw=_config_with_email()
    )

    assert result is not None
    lead.refresh_from_db()
    assert lead.status == LEAD_STATUS_LOST           # not overwritten
    assert lead.converted_submission_id == submission.id  # still linked


# ── 2. enrolled as terminal in parser ─────────────────────────────────────


def test_enrolled_stripped_as_transition_target():
    """get_lead_workflow_transitions strips actions whose target status is 'enrolled'."""
    config_raw = {
        "admin": {
            "lead_workflow": {
                "transitions": {
                    "contacted": [
                        {"label": "Enroll", "status": "enrolled"},  # must be stripped
                        {"label": "Lost", "status": "lost"},
                    ],
                }
            }
        }
    }
    result = get_lead_workflow_transitions(config_raw)

    assert "contacted" in result
    statuses = [a["status"] for a in result["contacted"]]
    assert "enrolled" not in statuses
    assert "lost" in statuses


def test_enrolled_stripped_as_from_status():
    """get_lead_workflow_transitions skips the 'enrolled' key as a from-status."""
    config_raw = {
        "admin": {
            "lead_workflow": {
                "transitions": {
                    "enrolled": [  # entire key must be skipped
                        {"label": "Re-open", "status": "new"},
                    ],
                    "new": [
                        {"label": "Mark Contacted", "status": "contacted"},
                    ],
                }
            }
        }
    }
    result = get_lead_workflow_transitions(config_raw)

    assert "enrolled" not in result
    assert "new" in result
