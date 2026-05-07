"""
Phase 1 — Submission Status Workflow tests.

Covers:
  - YAML parser functions (get_submission_workflow_filters, get_submission_workflow_transitions)
  - school_submission_status_update_view (permissions, validation, happy path)
  - school_submissions_view filter tab behaviour
"""

import pytest
from unittest.mock import MagicMock, patch
from django.urls import reverse

from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)
from core.services.admin_submission_yaml import (
    get_submission_workflow_filters,
    get_submission_workflow_transitions,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _school_admin_user(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _superuser():
    user = UserFactory()
    user.is_superuser = True
    user.is_staff = True
    user.save()
    return user


# Minimal YAML config dict with a full tour workflow
_WORKFLOW_CONFIG = {
    "admin": {
        "submission_statuses": [
            "New", "Tour Scheduled", "Tour Completed",
            "Waitlisted", "Enrolled", "Declined",
        ],
        "submission_workflow": {
            "filters": {
                "needs_review": {"label": "Needs Review", "statuses": ["New"]},
                "needs_decision": {"label": "Needs Decision", "statuses": ["Tour Completed"]},
            },
            "transitions": {
                "New": [
                    {"label": "Mark Tour Scheduled", "status": "Tour Scheduled"},
                    {"label": "Decline", "status": "Declined"},
                ],
                "Tour Completed": [
                    {"label": "Enroll", "status": "Enrolled"},
                    {"label": "Decline", "status": "Declined"},
                ],
            },
        },
    }
}

# Config with statuses but no workflow block (generic school)
_NO_WORKFLOW_CONFIG = {
    "admin": {
        "submission_statuses": ["New", "In Review", "Archived"],
    }
}


# ── Parser unit tests: get_submission_workflow_filters ────────────────────


def test_get_workflow_filters_empty_when_no_admin_block():
    assert get_submission_workflow_filters({}) == {}


def test_get_workflow_filters_empty_when_no_workflow_key():
    assert get_submission_workflow_filters(_NO_WORKFLOW_CONFIG) == {}


def test_get_workflow_filters_parses_valid_config():
    result = get_submission_workflow_filters(_WORKFLOW_CONFIG)
    assert "needs_review" in result
    assert result["needs_review"]["label"] == "Needs Review"
    assert result["needs_review"]["statuses"] == ["New"]
    assert "needs_decision" in result


def test_get_workflow_filters_skips_malformed_entries():
    config = {
        "admin": {
            "submission_workflow": {
                "filters": {
                    "good": {"label": "Good", "statuses": ["New"]},
                    "bad_no_label": {"statuses": ["New"]},
                    "bad_no_statuses": {"label": "Missing"},
                    "bad_empty_statuses": {"label": "Empty", "statuses": []},
                    "not_a_dict": "string_value",
                }
            }
        }
    }
    result = get_submission_workflow_filters(config)
    assert list(result.keys()) == ["good"]


# ── Parser unit tests: get_submission_workflow_transitions ─────────────────


def test_get_workflow_transitions_empty_when_no_workflow_key():
    assert get_submission_workflow_transitions(_NO_WORKFLOW_CONFIG) == {}


def test_get_workflow_transitions_parses_valid_config():
    result = get_submission_workflow_transitions(_WORKFLOW_CONFIG)
    assert "New" in result
    assert {"label": "Mark Tour Scheduled", "status": "Tour Scheduled"} in result["New"]
    assert "Tour Completed" in result


def test_get_workflow_transitions_skips_malformed_actions():
    config = {
        "admin": {
            "submission_workflow": {
                "transitions": {
                    "New": [
                        {"label": "Valid", "status": "Enrolled"},
                        {"label": "No status"},          # missing status key
                        {"status": "Enrolled"},           # missing label key
                        "not_a_dict",                     # wrong type
                    ],
                }
            }
        }
    }
    result = get_submission_workflow_transitions(config)
    assert result["New"] == [{"label": "Valid", "status": "Enrolled"}]


# ── Status update endpoint tests ───────────────────────────────────────────

def _status_url(school, submission_id):
    return reverse(
        "school_submission_status_update",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _make_mock_config(config_raw: dict):
    """Return a mock config object with .raw set to the given dict."""
    mock = MagicMock()
    mock.raw = config_raw
    mock.form = {}
    return mock


@pytest.mark.django_db
def test_status_update_succeeds_with_valid_transition(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    sub = SubmissionFactory(school=school, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, sub.id),
            {"new_status": "Tour Scheduled", "next_filter": "tour_scheduled"},
        )

    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "Tour Scheduled"


@pytest.mark.django_db
def test_status_update_blocked_for_other_school(client):
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    client.force_login(user)

    sub = SubmissionFactory(school=school_b, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school_b, sub.id),
            {"new_status": "Tour Scheduled"},
        )

    # _get_accessible_school_for_admin enforces membership — school_b returns 403/404
    assert resp.status_code in (403, 404)
    sub.refresh_from_db()
    assert sub.status == "New"


@pytest.mark.django_db
def test_status_update_blocked_for_unauthenticated(client):
    school = SchoolFactory()
    sub = SubmissionFactory(school=school, status="New")

    resp = client.post(_status_url(school, sub.id), {"new_status": "Tour Scheduled"})

    # login_required redirects to login page
    assert resp.status_code == 302
    assert "/login" in resp["Location"] or "/accounts/login" in resp["Location"]
    sub.refresh_from_db()
    assert sub.status == "New"


@pytest.mark.django_db
def test_status_update_get_request_returns_405(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    sub = SubmissionFactory(school=school, status="New")

    resp = client.get(_status_url(school, sub.id))

    assert resp.status_code == 405


@pytest.mark.django_db
def test_status_update_rejects_invalid_status_not_in_statuses(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    sub = SubmissionFactory(school=school, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, sub.id),
            {"new_status": "NonExistentStatus"},
        )

    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "New"  # unchanged


@pytest.mark.django_db
def test_status_update_rejects_invalid_transition_from_current_state(client):
    """Enrolled → Tour Scheduled is not in the workflow transitions."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    sub = SubmissionFactory(school=school, status="Enrolled")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, sub.id),
            {"new_status": "Tour Scheduled"},
        )

    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "Enrolled"  # unchanged


@pytest.mark.django_db
def test_status_update_allows_any_status_when_no_workflow_configured(client):
    """School with statuses but no submission_workflow transitions allows any status freely."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    sub = SubmissionFactory(school=school, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_NO_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, sub.id),
            {"new_status": "In Review"},
        )

    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "In Review"  # free-for-all: allowed without workflow transitions


@pytest.mark.django_db
def test_superuser_can_update_any_school_submission(client):
    school = SchoolFactory()
    user = _superuser()
    client.force_login(user)

    sub = SubmissionFactory(school=school, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, sub.id),
            {"new_status": "Tour Scheduled"},
        )

    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "Tour Scheduled"


@pytest.mark.django_db
def test_status_update_redirects_preserving_full_query_string(client):
    """next POST param (full local path+query) is used verbatim for redirect."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    sub = SubmissionFactory(school=school, status="New")

    next_url = f"/schools/{school.slug}/admin/submissions/?filter=needs_review&q=alice"

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, sub.id),
            {"new_status": "Tour Scheduled", "next": next_url},
        )

    assert resp.status_code == 302
    assert resp["Location"] == next_url


@pytest.mark.django_db
def test_status_update_rejects_absolute_next_url(client):
    """next param with a scheme/host is rejected; fallback to submissions URL."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    sub = SubmissionFactory(school=school, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _status_url(school, sub.id),
            {"new_status": "Tour Scheduled", "next": "https://evil.example.com/phish"},
        )

    assert resp.status_code == 302
    # Falls back to the bare submissions URL, not the attacker URL
    assert "evil.example.com" not in resp["Location"]
    assert f"/schools/{school.slug}/admin/submissions/" in resp["Location"]


# ── Filter tab behaviour in school_submissions_view ────────────────────────


@pytest.mark.django_db
def test_filter_tab_returns_correct_submissions(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    SubmissionFactory(school=school, status="New", data={"first_name": "Alice", "last_name": "A"})
    SubmissionFactory(school=school, status="Tour Completed", data={"first_name": "Bob", "last_name": "B"})

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.get(url + "?filter=needs_review")

    assert resp.status_code == 200
    submissions = resp.context["submissions"]
    assert all(s["status"] == "New" for s in submissions)
    assert len(submissions) == 1


@pytest.mark.django_db
def test_filter_all_returns_all_submissions(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    SubmissionFactory(school=school, status="New", data={"first_name": "A", "last_name": "A"})
    SubmissionFactory(school=school, status="Enrolled", data={"first_name": "B", "last_name": "B"})

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.get(url)

    assert resp.status_code == 200
    assert resp.context["total_count"] == 2


@pytest.mark.django_db
def test_workflow_filter_empty_state_shows_label_specific_message(client):
    """Empty state for a named workflow filter shows label-specific copy, not generic text."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    # No "New" submissions — only an Enrolled one that won't match needs_review
    SubmissionFactory(school=school, status="Enrolled", data={"first_name": "Z", "last_name": "Z"})

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.get(url + "?filter=needs_review")

    assert resp.status_code == 200
    content = resp.content.decode()
    assert "No submissions need Needs Review right now" in content
    assert "No submissions match the current filters" not in content


@pytest.mark.django_db
def test_no_workflow_falls_back_to_status_dropdown_in_context(client):
    """Without a workflow, the template gets empty workflow_filters so it shows the dropdown."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_NO_WORKFLOW_CONFIG)):
        resp = client.get(url)

    assert resp.status_code == 200
    assert resp.context["workflow_filters"] == {}
    # status_choices is always populated (from DB distinct statuses)
    assert "status_choices" in resp.context


# ── Bulk status update endpoint tests ──────────────────────────────────────


def _bulk_url(school):
    return reverse("school_submission_bulk_status_update", kwargs={"school_slug": school.slug})


@pytest.mark.django_db
def test_bulk_update_happy_path_all_eligible(client):
    """All selected submissions in an eligible from-status → all updated."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    sub1 = SubmissionFactory(school=school, status="New")
    sub2 = SubmissionFactory(school=school, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "Tour Scheduled", "submission_ids": [sub1.id, sub2.id]},
        )

    assert resp.status_code == 302
    sub1.refresh_from_db()
    sub2.refresh_from_db()
    assert sub1.status == "Tour Scheduled"
    assert sub2.status == "Tour Scheduled"


@pytest.mark.django_db
def test_bulk_update_partial_skip_ineligible_reports_counts(client):
    """Mix of eligible + ineligible from-statuses: eligible updated, ineligible skipped."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    eligible = SubmissionFactory(school=school, status="New")
    ineligible = SubmissionFactory(school=school, status="Enrolled")  # no transition to Tour Scheduled

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "Tour Scheduled", "submission_ids": [eligible.id, ineligible.id]},
        )

    assert resp.status_code == 302
    eligible.refresh_from_db()
    ineligible.refresh_from_db()
    assert eligible.status == "Tour Scheduled"
    assert ineligible.status == "Enrolled"  # unchanged

    # Flash message should mention 1 skipped
    msgs = list(resp.wsgi_request._messages)
    assert any("1 skipped" in str(m) for m in msgs)


@pytest.mark.django_db
def test_bulk_update_empty_selection_returns_error(client):
    """POST with no submission_ids → error message, no DB changes."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    sub = SubmissionFactory(school=school, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(_bulk_url(school), {"new_status": "Tour Scheduled"})

    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "New"


@pytest.mark.django_db
def test_bulk_update_invalid_status_not_in_yaml(client):
    """new_status not in school's configured statuses → rejected."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    sub = SubmissionFactory(school=school, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "NonExistentStatus", "submission_ids": [sub.id]},
        )

    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "New"


@pytest.mark.django_db
def test_bulk_update_no_workflow_configured(client):
    """School with statuses but no workflow transitions → bulk update allowed freely."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    sub = SubmissionFactory(school=school, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_NO_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "In Review", "submission_ids": [sub.id]},
        )

    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "In Review"  # free-for-all: no transitions configured → all allowed


@pytest.mark.django_db
def test_bulk_update_blocked_for_non_member(client):
    """School admin for school_a cannot bulk-update school_b submissions."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    client.force_login(user)

    sub = SubmissionFactory(school=school_b, status="New")

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school_b),
            {"new_status": "Tour Scheduled", "submission_ids": [sub.id]},
        )

    assert resp.status_code in (403, 404)
    sub.refresh_from_db()
    assert sub.status == "New"


@pytest.mark.django_db
def test_bulk_update_blocked_for_unauthenticated(client):
    """Unauthenticated request → redirect to login."""
    school = SchoolFactory()
    sub = SubmissionFactory(school=school, status="New")

    resp = client.post(
        _bulk_url(school),
        {"new_status": "Tour Scheduled", "submission_ids": [sub.id]},
    )

    assert resp.status_code == 302
    assert "/login" in resp["Location"] or "/accounts/login" in resp["Location"]
    sub.refresh_from_db()
    assert sub.status == "New"


@pytest.mark.django_db
def test_bulk_update_get_request_returns_405(client):
    """GET on the bulk-status endpoint → 405 Method Not Allowed."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    resp = client.get(_bulk_url(school))

    assert resp.status_code == 405


@pytest.mark.django_db
def test_bulk_update_cross_school_ids_silently_excluded(client):
    """Submission IDs from another school are silently excluded (school-scoped queryset)."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    client.force_login(user)

    sub_b = SubmissionFactory(school=school_b, status="New")

    # Post school_b's submission ID to school_a's bulk endpoint
    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school_a),
            {"new_status": "Tour Scheduled", "submission_ids": [sub_b.id]},
        )

    assert resp.status_code == 302
    sub_b.refresh_from_db()
    assert sub_b.status == "New"  # untouched


@pytest.mark.django_db
def test_bulk_update_preserves_full_query_string(client):
    """next POST param (full local path+query) is used verbatim for bulk redirect."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    sub = SubmissionFactory(school=school, status="New")

    next_url = f"/schools/{school.slug}/admin/submissions/?filter=needs_review&q=alice"

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_WORKFLOW_CONFIG)):
        resp = client.post(
            _bulk_url(school),
            {"new_status": "Tour Scheduled", "submission_ids": [sub.id], "next": next_url},
        )

    assert resp.status_code == 302
    assert resp["Location"] == next_url


# ── Filters-only config (no transitions) ───────────────────────────────────

_FILTERS_ONLY_CONFIG = {
    "admin": {
        "submission_statuses": ["New", "Enrolled"],
        "submission_workflow": {
            "filters": {
                "needs_review": {"label": "Needs Review", "statuses": ["New"]},
            }
            # deliberately no transitions key
        },
    }
}


@pytest.mark.django_db
def test_bulk_controls_always_enabled(client):
    """workflow_actions_enabled is always True — download/print work without YAML transitions.
    Filter tabs still render when workflow_filters are configured; status-update dropdown
    is empty but checkboxes are always present."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})

    with patch("core.views_school_common.load_school_config", return_value=_make_mock_config(_FILTERS_ONLY_CONFIG)):
        resp = client.get(url)

    assert resp.status_code == 200
    assert resp.context["workflow_filters"]       # filter tabs still present
    assert resp.context["workflow_actions_enabled"]  # always True now
    # Checkboxes always rendered so download/print bulk actions are accessible
    assert "submission-checkbox" in resp.content.decode()


# ── Django admin: staff without membership ─────────────────────────────────


@pytest.mark.django_db
def test_django_admin_staff_without_membership_no_crash(client):
    """Staff user with no SchoolAdminMembership can load Django admin without error."""
    user = UserFactory()
    user.is_staff = True
    user.save()
    # Confirm no membership exists for this user
    client.force_login(user)

    resp = client.get("/admin/")

    # Django admin index returns 200 for staff users
    assert resp.status_code == 200
