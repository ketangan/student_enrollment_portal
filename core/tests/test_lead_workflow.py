"""
Phase 2 — Leads Pipeline Workflow tests.

Covers:
  - YAML parser functions (get_lead_workflow_filters, get_lead_workflow_transitions)
  - school_lead_status_update_view (permissions, validation, happy path)
  - school_lead_bulk_status_update_view (permissions, validation, skip-count messaging)
"""

import pytest
from unittest.mock import MagicMock, patch
from django.urls import reverse

from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    UserFactory,
)
from core.services.admin_lead_yaml import (
    get_lead_workflow_filters,
    get_lead_workflow_transitions,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _school_admin_user(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


# Minimal YAML config dict with a lead workflow
_LEAD_WORKFLOW_CONFIG = {
    "admin": {
        "lead_workflow": {
            "filters": {
                "new_leads": {"label": "New", "statuses": ["new"]},
                "pipeline": {"label": "In Pipeline", "statuses": ["contacted", "trial_scheduled"]},
            },
            "transitions": {
                "new": [
                    {"label": "Mark Contacted", "status": "contacted"},
                    {"label": "Lost", "status": "lost"},
                ],
                "contacted": [
                    {"label": "Schedule Trial", "status": "trial_scheduled"},
                    {"label": "Enroll", "status": "enrolled"},
                    {"label": "Lost", "status": "lost"},
                ],
                "trial_scheduled": [
                    {"label": "Enroll", "status": "enrolled"},
                    {"label": "Lost", "status": "lost"},
                ],
            },
        }
    }
}

# Config with no lead_workflow block
_NO_LEAD_WORKFLOW_CONFIG = {
    "admin": {
        "submission_statuses": ["New", "In Review"],
    }
}


def _make_mock_config(config_raw: dict):
    mock = MagicMock()
    mock.raw = config_raw
    mock.form = {}
    return mock


def _status_url(school, lead_id):
    return reverse(
        "school_lead_status_update",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _bulk_url(school):
    return reverse("school_lead_bulk_status_update", kwargs={"school_slug": school.slug})


# ── YAML parser: get_lead_workflow_filters ────────────────────────────────


def test_lead_workflow_filters_empty_when_block_absent():
    assert get_lead_workflow_filters({}) == {}
    assert get_lead_workflow_filters(_NO_LEAD_WORKFLOW_CONFIG) == {}


def test_lead_workflow_filters_parsed_correctly():
    result = get_lead_workflow_filters(_LEAD_WORKFLOW_CONFIG)
    assert "new_leads" in result
    assert result["new_leads"]["label"] == "New"
    assert result["new_leads"]["statuses"] == ["new"]
    assert "pipeline" in result
    assert result["pipeline"]["statuses"] == ["contacted", "trial_scheduled"]


def test_lead_workflow_filters_skips_malformed_entries():
    config = {
        "admin": {
            "lead_workflow": {
                "filters": {
                    "good": {"label": "Good", "statuses": ["new"]},
                    "bad_no_label": {"statuses": ["new"]},
                    "bad_no_statuses": {"label": "Missing"},
                    "bad_empty_statuses": {"label": "Empty", "statuses": []},
                    "not_a_dict": "string_value",
                }
            }
        }
    }
    result = get_lead_workflow_filters(config)
    assert list(result.keys()) == ["good"]


# ── YAML parser: get_lead_workflow_transitions ────────────────────────────


def test_lead_workflow_transitions_empty_when_block_absent():
    assert get_lead_workflow_transitions({}) == {}
    assert get_lead_workflow_transitions(_NO_LEAD_WORKFLOW_CONFIG) == {}


def test_lead_workflow_transitions_parsed_correctly():
    result = get_lead_workflow_transitions(_LEAD_WORKFLOW_CONFIG)
    assert "new" in result
    assert {"label": "Mark Contacted", "status": "contacted"} in result["new"]
    assert "contacted" in result
    # enrolled is stripped as a target by the parser (terminal status, set only by conversion)
    statuses_from_contacted = [a["status"] for a in result["contacted"]]
    assert "enrolled" not in statuses_from_contacted
    assert "trial_scheduled" in statuses_from_contacted
    assert "lost" in statuses_from_contacted


def test_lead_workflow_transitions_skips_malformed_actions():
    config = {
        "admin": {
            "lead_workflow": {
                "transitions": {
                    "new": [
                        {"label": "Valid", "status": "contacted"},
                        {"label": "No status"},       # missing status
                        {"status": "contacted"},       # missing label
                        "not_a_dict",                  # wrong type
                    ],
                }
            }
        }
    }
    result = get_lead_workflow_transitions(config)
    assert result["new"] == [{"label": "Valid", "status": "contacted"}]


# ── Single lead status update ─────────────────────────────────────────────


@pytest.mark.django_db
def test_lead_status_update_happy_path(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="new")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, lead.id),
            {"new_status": "contacted"},
        )

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.status == "contacted"


@pytest.mark.django_db
def test_lead_status_update_invalid_status_rejected(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="new")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, lead.id),
            {"new_status": "garbage_status"},
        )

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.status == "new"  # unchanged


@pytest.mark.django_db
def test_lead_status_update_allows_any_status_when_no_workflow_configured(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="new")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_NO_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, lead.id),
            {"new_status": "contacted"},
        )

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.status == "contacted"  # free-for-all when no workflow configured


@pytest.mark.django_db
def test_lead_status_update_any_valid_status_allowed(client):
    """Transition graph is no longer enforced — any valid status is directly reachable."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="enrolled")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, lead.id),
            {"new_status": "new"},
        )

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.status == "new"  # now allowed


@pytest.mark.django_db
def test_lead_status_update_unauthenticated_redirects(client):
    school = SchoolFactory()
    lead = LeadFactory(school=school, status="new")

    resp = client.post(_status_url(school, lead.id), {"new_status": "contacted"})

    assert resp.status_code == 302
    assert "/login" in resp["Location"] or "/accounts/login" in resp["Location"]
    lead.refresh_from_db()
    assert lead.status == "new"


@pytest.mark.django_db
def test_lead_status_update_get_returns_405(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="new")

    resp = client.get(_status_url(school, lead.id))

    assert resp.status_code == 405


@pytest.mark.django_db
def test_lead_status_update_cross_school_returns_404(client):
    """School A admin cannot update a lead belonging to school B."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    client.force_login(user)

    lead_b = LeadFactory(school=school_b, status="new")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school_b, lead_b.id),
            {"new_status": "contacted"},
        )

    assert resp.status_code in (403, 404)
    lead_b.refresh_from_db()
    assert lead_b.status == "new"  # unchanged


# ── Bulk lead status update ───────────────────────────────────────────────


@pytest.mark.django_db
def test_bulk_lead_update_all_eligible(client):
    """All selected leads in an eligible from-status → all updated."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    lead1 = LeadFactory(school=school, status="new")
    lead2 = LeadFactory(school=school, status="new")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "contacted", "lead_ids": [lead1.id, lead2.id]},
        )

    assert resp.status_code == 302
    lead1.refresh_from_db()
    lead2.refresh_from_db()
    assert lead1.status == "contacted"
    assert lead2.status == "contacted"


@pytest.mark.django_db
def test_bulk_lead_update_updates_all_selected(client):
    """All selected leads are updated regardless of current status — no transition enforcement."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    lead_a = LeadFactory(school=school, status="new")
    lead_b = LeadFactory(school=school, status="enrolled")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "contacted", "lead_ids": [lead_a.id, lead_b.id]},
        )

    assert resp.status_code == 302
    lead_a.refresh_from_db()
    lead_b.refresh_from_db()
    assert lead_a.status == "contacted"
    assert lead_b.status == "contacted"  # now allowed

    msgs = list(resp.wsgi_request._messages)
    assert any("2 leads" in str(m) for m in msgs)


@pytest.mark.django_db
def test_bulk_lead_update_empty_selection_error(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(_bulk_url(school), {"new_status": "contacted"})

    assert resp.status_code == 302


@pytest.mark.django_db
def test_bulk_lead_update_invalid_status_error(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="new")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "garbage_status", "lead_ids": [lead.id]},
        )

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.status == "new"  # unchanged


@pytest.mark.django_db
def test_bulk_lead_update_allows_any_status_when_no_workflow_configured(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="new")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_NO_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "contacted", "lead_ids": [lead.id]},
        )

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.status == "contacted"  # free-for-all when no workflow configured


@pytest.mark.django_db
def test_bulk_lead_update_unauthenticated_redirects(client):
    school = SchoolFactory()
    lead = LeadFactory(school=school, status="new")

    resp = client.post(_bulk_url(school), {"new_status": "contacted", "lead_ids": [lead.id]})

    assert resp.status_code == 302
    assert "/login" in resp["Location"] or "/accounts/login" in resp["Location"]
    lead.refresh_from_db()
    assert lead.status == "new"


@pytest.mark.django_db
def test_bulk_lead_update_cross_school_ids_excluded(client):
    """Lead IDs belonging to another school are silently excluded — error returned."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    client.force_login(user)

    lead_b = LeadFactory(school=school_b, status="new")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school_a),
            {"new_status": "contacted", "lead_ids": [lead_b.id]},
        )

    assert resp.status_code == 302
    lead_b.refresh_from_db()
    assert lead_b.status == "new"  # unchanged — cross-school ID silently excluded


# ── workflow_actions_enabled logic ────────────────────────────────────────


def test_workflow_actions_enabled_requires_only_transitions():
    """workflow_actions_enabled = bool(workflow_transitions): filters are optional."""
    transitions_only_config = {
        "admin": {
            "lead_workflow": {
                "transitions": {
                    "new": [{"label": "Contact", "status": "contacted"}]
                }
                # No filters block — filters and actions are independent.
            }
        }
    }
    filters = get_lead_workflow_filters(transitions_only_config)
    transitions = get_lead_workflow_transitions(transitions_only_config)
    # Filters absent, transitions present.
    assert not filters
    assert transitions
    # The view computes: workflow_actions_enabled = bool(workflow_transitions)
    assert bool(transitions) is True


# ── YAML status validation ─────────────────────────────────────────────────


def test_lead_workflow_filters_strips_invalid_statuses():
    """Statuses not in LEAD_STATUS_CHOICES are silently removed."""
    config = {
        "admin": {
            "lead_workflow": {
                "filters": {
                    "mixed": {"label": "Mixed", "statuses": ["new", "NOT_A_STATUS", "contacted"]},
                    "all_invalid": {"label": "All bad", "statuses": ["BOGUS", "ALSO_BOGUS"]},
                    "valid": {"label": "Valid", "statuses": ["new"]},
                }
            }
        }
    }
    result = get_lead_workflow_filters(config)
    # all_invalid has no valid statuses — entire entry is dropped.
    assert "all_invalid" not in result
    # mixed has 2 valid statuses after stripping the invalid one.
    assert result["mixed"]["statuses"] == ["new", "contacted"]
    assert result["valid"]["statuses"] == ["new"]


def test_lead_workflow_transitions_strips_invalid_statuses():
    """From-statuses and target statuses not in LEAD_STATUS_CHOICES are skipped."""
    config = {
        "admin": {
            "lead_workflow": {
                "transitions": {
                    "new": [
                        {"label": "Valid", "status": "contacted"},
                        {"label": "Bad target", "status": "INVENTED_STATUS"},
                    ],
                    "INVALID_FROM_STATUS": [
                        {"label": "Ignored", "status": "contacted"},
                    ],
                }
            }
        }
    }
    result = get_lead_workflow_transitions(config)
    # INVALID_FROM_STATUS key is skipped entirely.
    assert "INVALID_FROM_STATUS" not in result
    # Only the valid action survives for "new".
    assert result["new"] == [{"label": "Valid", "status": "contacted"}]


# ── Query string preservation ─────────────────────────────────────────────


@pytest.mark.django_db
def test_lead_status_update_preserves_full_query_string(client):
    """next POST param (full local path+query) is used verbatim for redirect."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="new")

    next_url = f"/schools/{school.slug}/admin/leads/?filter=new_leads&q=alice"

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, lead.id),
            {"new_status": "contacted", "next": next_url},
        )

    assert resp.status_code == 302
    assert resp["Location"] == next_url
    lead.refresh_from_db()
    assert lead.status == "contacted"


@pytest.mark.django_db
def test_bulk_lead_update_preserves_full_query_string(client):
    """next POST param is preserved through bulk update redirect."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="new")

    next_url = f"/schools/{school.slug}/admin/leads/?filter=pipeline&q=bob"

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "contacted", "lead_ids": [lead.id], "next": next_url},
        )

    assert resp.status_code == 302
    assert resp["Location"] == next_url
    lead.refresh_from_db()
    assert lead.status == "contacted"


@pytest.mark.django_db
def test_bulk_contacted_sets_last_contacted_at(client):
    """Bulk update to 'contacted' sets last_contacted_at on each eligible lead."""
    from django.utils import timezone

    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    lead = LeadFactory(school=school, status="new", last_contacted_at=None)

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        client.post(
            _bulk_url(school),
            {"new_status": "contacted", "lead_ids": [lead.id]},
        )

    lead.refresh_from_db()
    assert lead.last_contacted_at is not None
    assert (timezone.now() - lead.last_contacted_at).total_seconds() < 5


@pytest.mark.django_db
def test_bulk_contacted_clears_overdue_followup(client):
    """Bulk update to 'contacted' clears a follow-up date that is in the past."""
    from datetime import timedelta
    from django.utils import timezone

    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    past = timezone.now() - timedelta(days=3)
    lead = LeadFactory(school=school, status="new", next_follow_up_at=past)

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_LEAD_WORKFLOW_CONFIG)):
        client.post(
            _bulk_url(school),
            {"new_status": "contacted", "lead_ids": [lead.id]},
        )

    lead.refresh_from_db()
    assert lead.next_follow_up_at is None
