"""
Config loader + notification edge cases.

Tests defensive behaviour when YAML is malformed, fields are missing, or
config values fall into their fallback paths.  These tests document the
contracts the system relies on and catch silent regressions.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from core.services.admin_submission_yaml import get_submission_status_choices
from core.services.admin_lead_yaml import get_lead_workflow_transitions
from core.services.notifications import (
    get_submission_email_config,
    send_submission_notification_email,
)
from core.views_school_common import _build_lead_prefill_data
from core.tests.factories import LeadFactory, SchoolFactory


# ---------------------------------------------------------------------------
# default_submission_status coercion
# ---------------------------------------------------------------------------


def test_default_submission_status_as_list_falls_back_to_first_status():
    """
    When default_submission_status is a YAML list (a known authoring mistake),
    str(['New']) = \"['New']\" is not in submission_statuses, so the system falls
    back to the first status in the list.  This test documents that the fallback
    is active and that the result is predictable.
    """
    # This is what yaml.safe_load produces for the block-list form
    raw = {
        "admin": {
            "submission_statuses": ["New", "In Review", "Enrolled"],
            "default_submission_status": ["New"],  # list, not string
        }
    }
    statuses, default = get_submission_status_choices(raw)
    # The list form str-coerces to "['New']" which is NOT in statuses,
    # so the fallback is the first element of the statuses list.
    assert statuses == ["New", "In Review", "Enrolled"]
    assert default == "New", (
        "Fallback should be the first status when default is unrecognised"
    )


def test_default_submission_status_string_used_directly():
    """Correct scalar form is used as-is."""
    raw = {
        "admin": {
            "submission_statuses": ["New", "In Review", "Enrolled"],
            "default_submission_status": "In Review",
        }
    }
    _, default = get_submission_status_choices(raw)
    assert default == "In Review"


def test_missing_admin_block_returns_defaults():
    """No admin block → hardcoded default statuses and 'New' default."""
    raw = {"form": {"sections": []}}
    statuses, default = get_submission_status_choices(raw)
    assert "New" in statuses
    assert default == "New"


def test_empty_submission_statuses_returns_defaults():
    """Empty statuses list → fall back to hardcoded defaults, not empty list."""
    raw = {"admin": {"submission_statuses": []}}
    statuses, default = get_submission_status_choices(raw)
    assert len(statuses) > 0, "Should never return an empty status list"
    assert default in statuses


# ---------------------------------------------------------------------------
# Lead workflow: invalid / missing config handled gracefully
# ---------------------------------------------------------------------------


def test_lead_workflow_missing_block_returns_empty():
    """No lead_workflow block → empty transitions dict (not an exception)."""
    result = get_lead_workflow_transitions({})
    assert result == {}


def test_lead_workflow_enrolled_as_target_is_stripped():
    """Enrolled is terminal — should not appear as a target in transitions."""
    raw = {
        "admin": {
            "lead_workflow": {
                "transitions": {
                    "new": [
                        {"label": "Enroll", "status": "enrolled"},
                        {"label": "Contact", "status": "contacted"},
                    ]
                }
            }
        }
    }
    result = get_lead_workflow_transitions(raw)
    targets = [a["status"] for a in result.get("new", [])]
    assert "enrolled" not in targets
    assert "contacted" in targets


def test_lead_workflow_enrolled_as_from_status_is_stripped():
    """Enrolled must not appear as a from-status in transitions."""
    raw = {
        "admin": {
            "lead_workflow": {
                "transitions": {
                    "enrolled": [{"label": "Re-open", "status": "new"}],
                    "new": [{"label": "Contact", "status": "contacted"}],
                }
            }
        }
    }
    result = get_lead_workflow_transitions(raw)
    assert "enrolled" not in result
    assert "new" in result


# ---------------------------------------------------------------------------
# Notification: submission email config parsing
# ---------------------------------------------------------------------------


def test_submission_email_config_uses_yaml_subject():
    """Custom subject from YAML is parsed and stored on the config object."""
    raw = {
        "success": {
            "notifications": {
                "submission_email": {
                    "to": "admin@school.com",
                    "subject": "Custom: {{student_name}}",
                    "from_email": "noreply@mypontora.com",
                }
            }
        }
    }
    cfg = get_submission_email_config(raw)
    assert cfg is not None
    assert cfg.subject == "Custom: {{student_name}}"


def test_submission_email_config_default_subject_when_not_set():
    """Missing subject in YAML uses the hardcoded default."""
    raw = {
        "success": {
            "notifications": {
                "submission_email": {
                    "to": "admin@school.com",
                    "from_email": "noreply@mypontora.com",
                }
            }
        }
    }
    cfg = get_submission_email_config(raw)
    assert cfg is not None
    assert cfg.subject == "New submission"  # hardcoded fallback in get_submission_email_config


def test_submission_email_config_no_recipients_returns_none():
    """No 'to' recipients → config disabled (returns None)."""
    raw = {
        "success": {
            "notifications": {
                "submission_email": {
                    "to": "",
                    "from_email": "noreply@mypontora.com",
                }
            }
        }
    }
    cfg = get_submission_email_config(raw)
    assert cfg is None


@pytest.mark.django_db
def test_submission_notification_uses_yaml_subject(settings):
    """
    send_submission_notification_email must use cfg.subject from the YAML config,
    not the hardcoded _build_submission_email_subject output.
    """
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from django.core import mail

    raw = {
        "success": {
            "notifications": {
                "submission_email": {
                    "to": "emily@sbmc.com",
                    "from_email": "noreply@mypontora.com",
                    "subject": "New SBMC registration: {{student_name}}",
                }
            }
        }
    }
    send_submission_notification_email(
        request=None,
        config_raw=raw,
        school_name="SBMC",
        submission_id=999,
        submission_public_id="TEST001",
        student_name="Liam Chen",
        submission_data={"instrument": "piano"},
    )
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "New SBMC registration: Liam Chen"


@pytest.mark.django_db
def test_submission_notification_uses_hardcoded_default_when_no_yaml_subject(settings):
    """When YAML subject is not set, falls back to 'New submission: <name>'."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from django.core import mail

    raw = {
        "success": {
            "notifications": {
                "submission_email": {
                    "to": "emily@sbmc.com",
                    "from_email": "noreply@mypontora.com",
                    # no subject key
                }
            }
        }
    }
    send_submission_notification_email(
        request=None,
        config_raw=raw,
        school_name="SBMC",
        submission_id=999,
        submission_public_id="TEST001",
        student_name="Emma Park",
        submission_data={},
    )
    assert len(mail.outbox) == 1
    # Default subject has no template vars so student name does not appear
    assert mail.outbox[0].subject == "New submission"


# ---------------------------------------------------------------------------
# Lead prefill edge cases
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_lead_prefill_with_empty_data():
    """Lead with data={} (no form_fields key) does not crash — email still mapped."""
    school = SchoolFactory()
    lead = LeadFactory(school=school, name="Test User", email="t@x.com", data={})
    # Minimal config with email field key
    raw = {
        "form": {
            "sections": [{
                "title": "Contact",
                "fields": [{"key": "guardian_email", "type": "email", "label": "Email"}]
            }]
        }
    }
    prefill = _build_lead_prefill_data(lead, raw)
    assert prefill.get("guardian_email") == "t@x.com"


@pytest.mark.django_db
def test_lead_prefill_with_none_form_fields():
    """form_fields=None in lead.data doesn't crash — skips that copy step."""
    school = SchoolFactory()
    lead = LeadFactory(school=school, name="Test User", email="t@x.com", data={"form_fields": None})
    raw = {"form": {"sections": []}}
    prefill = _build_lead_prefill_data(lead, raw)
    # Should not raise; email not found since no email field in raw
    assert isinstance(prefill, dict)


@pytest.mark.django_db
def test_lead_prefill_non_string_form_field_values_skipped():
    """Non-string, non-None form_field values (e.g. True, 0) are preserved as-is."""
    school = SchoolFactory()
    lead = LeadFactory(
        school=school,
        data={"form_fields": {"active": True, "count": 0, "name": "Alice"}},
    )
    raw = {"form": {"sections": []}}
    prefill = _build_lead_prefill_data(lead, raw)
    # Boolean True is not "" or None — should be kept
    assert prefill.get("active") is True
    # Integer 0 is not "" or None — should be kept
    assert prefill.get("count") == 0
    assert prefill.get("name") == "Alice"


@pytest.mark.django_db
def test_lead_prefill_empty_string_values_excluded():
    """Empty string form_field values must not appear in prefill output."""
    school = SchoolFactory()
    lead = LeadFactory(
        school=school,
        data={"form_fields": {"instrument": "", "student_age": "", "student_name": "Ali"}},
        interested_in_value="",
    )
    raw = {"form": {"sections": []}}
    prefill = _build_lead_prefill_data(lead, raw)
    assert "instrument" not in prefill
    assert "student_age" not in prefill
    assert prefill.get("student_name") == "Ali"


# ---------------------------------------------------------------------------
# Application fee config: missing field falls back to default amount
# ---------------------------------------------------------------------------


def test_fee_config_amount_from_field_uses_default_when_field_missing_from_form_data():
    """
    If amount_from_field.field is set but not in form_data (e.g. field was renamed),
    the fee uses the default amount — no crash, no silent zero.
    """
    from core.services.config_loader import get_application_fee_config
    raw = {
        "application_fee": {
            "enabled": True,
            "description": "Registration Fee",
            "amount_from_field": {
                "field": "lesson_time_status",
                "amounts": {"new_student": 125, "returning_student": 75},
                "default": 100,
            }
        }
    }
    cfg = get_application_fee_config(raw, "default", form_data={})
    assert cfg["enabled"] is True
    assert cfg["amount"] == 100  # default, because lesson_time_status not in form_data


def test_fee_config_amount_from_field_resolved_correctly():
    """Correct form_data value → correct amount."""
    from core.services.config_loader import get_application_fee_config
    raw = {
        "application_fee": {
            "enabled": True,
            "amount_from_field": {
                "field": "lesson_time_status",
                "amounts": {"new_student": 125, "returning_student": 75},
                "default": 100,
            }
        }
    }
    cfg = get_application_fee_config(raw, "default", form_data={"lesson_time_status": "new_student"})
    assert cfg["amount"] == 125

    cfg2 = get_application_fee_config(raw, "default", form_data={"lesson_time_status": "returning_student"})
    assert cfg2["amount"] == 75
