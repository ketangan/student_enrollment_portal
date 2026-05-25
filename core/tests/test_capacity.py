"""
Enrollment Capacity Limits.

Covers:
  - get_capacity_config: valid config, missing/empty programs → None
  - get_waitlist_message: custom message, default fallback
  - check_waitlist: at/under capacity, excluded statuses ignored, no config → False
  - get_capacity_summary: near/at capacity flags, empty without config
  - Admin submissions list context includes capacity_summary
  - apply_success page shows waitlist banner when session flag set
  - apply_success page shows normal message without session flag
  - Admin edit: re-renders with warning when moving student to at-capacity program
  - Admin edit: capacity_override=1 bypasses check, saves, and writes audit log entry
  - Admin edit: no conflict when destination program is under capacity
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from django.urls import reverse

from core.services.capacity import (
    check_waitlist,
    get_capacity_config,
    get_capacity_summary,
    get_waitlist_message,
)
from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Fixtures / helpers ────────────────────────────────────────────────────────

_CONFIG_WITH_CAPACITY = {
    "capacity": {
        "programs": {"ballet": 5, "jazz": 10},
        "excluded_statuses": ["Declined", "Archived"],
        "waitlist_message": "Sorry, program is full.",
    }
}


def _admin_for(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _submissions_url(school):
    return reverse("school_submissions", kwargs={"school_slug": school.slug})


def _success_url(school):
    return reverse("apply_success", kwargs={"school_slug": school.slug})


def _edit_url(school, submission_id):
    return reverse(
        "school_submission_edit",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


# ── get_capacity_config ───────────────────────────────────────────────────────


def test_get_capacity_config_valid():
    cfg = get_capacity_config(_CONFIG_WITH_CAPACITY)
    assert cfg is not None
    assert cfg["programs"] == {"ballet": 5, "jazz": 10}


def test_get_capacity_config_no_block_returns_none():
    assert get_capacity_config({}) is None


def test_get_capacity_config_no_programs_key_returns_none():
    assert get_capacity_config({"capacity": {"waitlist_message": "full"}}) is None


def test_get_capacity_config_empty_programs_returns_none():
    assert get_capacity_config({"capacity": {"programs": {}}}) is None


# ── get_waitlist_message ──────────────────────────────────────────────────────


def test_get_waitlist_message_returns_custom():
    cfg = _CONFIG_WITH_CAPACITY["capacity"]
    assert get_waitlist_message(cfg) == "Sorry, program is full."


def test_get_waitlist_message_returns_default_when_missing():
    cfg = {"programs": {"ballet": 5}}
    msg = get_waitlist_message(cfg)
    assert msg  # non-empty
    assert isinstance(msg, str)


# ── check_waitlist ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_check_waitlist_at_capacity():
    school = SchoolFactory()
    for _ in range(2):
        SubmissionFactory(school=school, data={"dance_style": "ballet"})
    config_raw = {"capacity": {"programs": {"ballet": 2}}}
    assert check_waitlist(school, {"dance_style": "ballet"}, config_raw) is True


@pytest.mark.django_db
def test_check_waitlist_under_capacity():
    school = SchoolFactory()
    SubmissionFactory(school=school, data={"dance_style": "ballet"})
    config_raw = {"capacity": {"programs": {"ballet": 5}}}
    assert check_waitlist(school, {"dance_style": "ballet"}, config_raw) is False


@pytest.mark.django_db
def test_check_waitlist_excluded_statuses_not_counted():
    """Declined submissions must not occupy a capacity slot."""
    school = SchoolFactory()
    for _ in range(3):
        SubmissionFactory(school=school, data={"dance_style": "ballet"}, status="Declined")
    config_raw = {
        "capacity": {
            "programs": {"ballet": 2},
            "excluded_statuses": ["Declined"],
        }
    }
    # 3 declined subs → still under the cap of 2 for non-excluded slots
    assert check_waitlist(school, {"dance_style": "ballet"}, config_raw) is False


@pytest.mark.django_db
def test_check_waitlist_no_config_returns_false():
    school = SchoolFactory()
    SubmissionFactory(school=school, data={"dance_style": "ballet"})
    assert check_waitlist(school, {"dance_style": "ballet"}, {}) is False


@pytest.mark.django_db
def test_check_waitlist_unknown_program_returns_false():
    """Program not in capacity config is not waitlisted."""
    school = SchoolFactory()
    config_raw = {"capacity": {"programs": {"ballet": 2}}}
    assert check_waitlist(school, {"dance_style": "tap"}, config_raw) is False


# ── get_capacity_summary ──────────────────────────────────────────────────────


@pytest.mark.django_db
def test_get_capacity_summary_near_capacity_flag():
    school = SchoolFactory()
    # 4 of 5 ballet = 80% → near_capacity, not at_capacity
    for _ in range(4):
        SubmissionFactory(school=school, data={"dance_style": "ballet"})
    config_raw = {"capacity": {"programs": {"ballet": 5, "jazz": 10}}}
    summary = get_capacity_summary(school, config_raw)

    assert summary["ballet"]["current"] == 4
    assert summary["ballet"]["max"] == 5
    assert summary["ballet"]["at_capacity"] is False
    assert summary["ballet"]["near_capacity"] is True

    assert summary["jazz"]["current"] == 0
    assert summary["jazz"]["at_capacity"] is False
    assert summary["jazz"]["near_capacity"] is False


@pytest.mark.django_db
def test_get_capacity_summary_at_capacity():
    school = SchoolFactory()
    for _ in range(5):
        SubmissionFactory(school=school, data={"dance_style": "ballet"})
    config_raw = {"capacity": {"programs": {"ballet": 5}}}
    summary = get_capacity_summary(school, config_raw)
    assert summary["ballet"]["at_capacity"] is True
    assert summary["ballet"]["near_capacity"] is True


@pytest.mark.django_db
def test_get_capacity_summary_empty_without_config():
    school = SchoolFactory()
    SubmissionFactory(school=school)
    assert get_capacity_summary(school, {}) == {}


# ── Admin submissions list ────────────────────────────────────────────────────


@pytest.mark.django_db
def test_capacity_summary_always_in_submissions_context(client, monkeypatch):
    """Admin submissions list always has capacity_summary in context (empty dict when unconfigured)."""
    school = SchoolFactory()
    user = _admin_for(school)
    client.force_login(user)

    monkeypatch.setattr(
        "core.views_school_submissions._safe_load_school_config",
        lambda slug: None,
    )

    resp = client.get(_submissions_url(school))
    assert resp.status_code == 200
    assert "capacity_summary" in resp.context
    assert resp.context["capacity_summary"] == {}


@pytest.mark.django_db
def test_capacity_summary_non_empty_when_config_present(client, monkeypatch):
    """When YAML has a capacity block, summary is non-empty in context."""
    school = SchoolFactory()
    user = _admin_for(school)
    client.force_login(user)

    mock_config = MagicMock()
    mock_config.raw = _CONFIG_WITH_CAPACITY
    mock_config.form = None

    monkeypatch.setattr(
        "core.views_school_submissions._safe_load_school_config",
        lambda slug: mock_config,
    )

    resp = client.get(_submissions_url(school))
    assert resp.status_code == 200
    summary = resp.context["capacity_summary"]
    assert "ballet" in summary
    assert "jazz" in summary


# ── apply_success waitlist banner ─────────────────────────────────────────────


@pytest.mark.django_db
def test_success_page_shows_waitlist_banner_from_session(client, monkeypatch):
    """When apply_waitlist session flag is True, success page shows the waitlist message."""
    school = SchoolFactory()

    mock_config = MagicMock()
    mock_config.display_name = school.display_name
    mock_config.branding = None
    mock_config.raw = _CONFIG_WITH_CAPACITY
    mock_config.form = None

    monkeypatch.setattr("core.views_public.load_school_config", lambda slug: mock_config)

    session = client.session
    session["apply_waitlist"] = True
    session.save()

    resp = client.get(_success_url(school))
    assert resp.status_code == 200
    assert resp.context["on_waitlist"] is True
    assert resp.context["waitlist_message"] == "Sorry, program is full."
    assert b"Sorry, program is full." in resp.content


@pytest.mark.django_db
def test_success_page_normal_without_waitlist_session(client, monkeypatch):
    """Without the session flag, success page shows normal success message."""
    school = SchoolFactory()

    mock_config = MagicMock()
    mock_config.display_name = school.display_name
    mock_config.branding = None
    mock_config.raw = {}
    mock_config.form = None

    monkeypatch.setattr("core.views_public.load_school_config", lambda slug: mock_config)

    resp = client.get(_success_url(school))
    assert resp.status_code == 200
    assert resp.context["on_waitlist"] is False
    assert resp.context["waitlist_message"] == ""


# ── Admin edit: capacity override flow ───────────────────────────────────────

# A minimal form config with a dance_style select field — needed so
# validate_submission actually processes the field and no-op detection fires.
_DANCE_STYLE_FORM = {
    "sections": [
        {
            "fields": [
                {
                    "key": "dance_style",
                    "type": "select",
                    "required": False,
                    "options": [
                        {"value": "ballet", "label": "Ballet"},
                        {"value": "jazz", "label": "Jazz"},
                    ],
                }
            ]
        }
    ]
}


def _mock_config_with_capacity(programs: dict):
    mock_config = MagicMock()
    mock_config.raw = {"capacity": {"programs": programs}}
    mock_config.form = _DANCE_STYLE_FORM
    return mock_config


@pytest.mark.django_db
def test_edit_shows_override_warning_when_moving_to_full_program(client, monkeypatch):
    """
    Moving a submission from jazz to ballet (full) without capacity_override
    must re-render the form with the override warning, not save.
    """
    school = SchoolFactory()
    user = _admin_for(school)
    client.force_login(user)

    # Fill ballet to cap of 2
    for _ in range(2):
        SubmissionFactory(school=school, data={"dance_style": "ballet"})

    # Submission currently in jazz
    submission = SubmissionFactory(school=school, data={"dance_style": "jazz"})

    monkeypatch.setattr(
        "core.views_school_submissions._safe_load_school_config",
        lambda slug: _mock_config_with_capacity({"ballet": 2, "jazz": 10}),
    )

    resp = client.post(
        _edit_url(school, submission.id),
        {"dance_style": "ballet"},
    )
    assert resp.status_code == 200
    assert resp.context["capacity_override_pending"] is True
    assert resp.context["capacity_conflict_program"] == "ballet"
    assert resp.context["capacity_conflict_max"] == 2
    # Submission must NOT have been saved
    submission.refresh_from_db()
    assert submission.data["dance_style"] == "jazz"


@pytest.mark.django_db
def test_edit_override_saves_and_writes_audit_log(client, monkeypatch):
    """
    Posting capacity_override=1 with a full-program change saves the
    submission and records the override in the audit log.
    """
    from core.models import AdminAuditLog

    school = SchoolFactory()
    user = _admin_for(school)
    client.force_login(user)

    for _ in range(2):
        SubmissionFactory(school=school, data={"dance_style": "ballet"})

    submission = SubmissionFactory(school=school, data={"dance_style": "jazz"})

    monkeypatch.setattr(
        "core.views_school_submissions._safe_load_school_config",
        lambda slug: _mock_config_with_capacity({"ballet": 2, "jazz": 10}),
    )

    resp = client.post(
        _edit_url(school, submission.id),
        {"dance_style": "ballet", "capacity_override": "1"},
    )
    assert resp.status_code == 302

    submission.refresh_from_db()
    assert submission.data["dance_style"] == "ballet"

    log = AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(submission.pk),
        action="change",
    ).latest("created_at")
    assert "capacity_override" in log.extra
    assert log.extra["capacity_override"]["program"] == "ballet"


@pytest.mark.django_db
def test_edit_no_warning_when_destination_under_capacity(client, monkeypatch):
    """Moving to a program that still has room must save without any confirmation step."""
    school = SchoolFactory()
    user = _admin_for(school)
    client.force_login(user)

    # Only 1 of 10 jazz spots taken
    SubmissionFactory(school=school, data={"dance_style": "jazz"})
    submission = SubmissionFactory(school=school, data={"dance_style": "ballet"})

    monkeypatch.setattr(
        "core.views_school_submissions._safe_load_school_config",
        lambda slug: _mock_config_with_capacity({"ballet": 10, "jazz": 10}),
    )

    resp = client.post(
        _edit_url(school, submission.id),
        {"dance_style": "jazz"},
    )
    assert resp.status_code == 302
    submission.refresh_from_db()
    assert submission.data["dance_style"] == "jazz"
