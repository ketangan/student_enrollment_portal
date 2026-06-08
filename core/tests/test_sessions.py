"""
Tests for SchoolSession management and the enrollment form session path.

Run:
  pytest core/tests/test_sessions.py -x -q
"""
from __future__ import annotations

import pytest
from unittest.mock import patch
from django.test import Client
from django.urls import reverse

from core.models import SchoolProgram, SchoolSession, Submission
from core.services.programs import (
    _auto_session_code,
    apply_auto_enrollment,
    has_enrollment_options,
    inject_db_program_options,
    resolve_submission_program_and_session,
)
from core.tests.factories import SchoolAdminMembershipFactory, SchoolFactory, SubmissionFactory, UserFactory
from core.views_school_common import STATUS_ENROLLED, STATUS_NEW, STATUS_WAITLISTED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _program(school, name="Ballet", code="ballet", **kw):
    return SchoolProgram.objects.create(school=school, name=name, code=code, **kw)


def _session(program, name="Fall 2025", code="fall_2025", **kw):
    return SchoolSession.objects.create(program=program, name=name, code=code, **kw)


def _minimal_form_cfg(field_key="program"):
    return {
        "sections": [{
            "title": "Program",
            "fields": [{"key": field_key, "type": "select", "label": "Program", "options": []}],
        }]
    }


# ---------------------------------------------------------------------------
# 1. _auto_session_code uniqueness
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_session_code_basic():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    assert _auto_session_code(prog, "Fall 2025") == "fall_2025"


@pytest.mark.django_db
def test_auto_session_code_dedup():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    _session(prog, name="Fall 2025", code="fall_2025")
    assert _auto_session_code(prog, "Fall 2025") == "fall_2025_2"


@pytest.mark.django_db
def test_auto_session_code_unique_per_program():
    """Same code is allowed on a different program."""
    school = SchoolFactory(program_field_key="program")
    p1 = _program(school, name="Ballet", code="ballet")
    p2 = _program(school, name="Jazz", code="jazz")
    _session(p1, name="Fall 2025", code="fall_2025")
    # Should not conflict since it's on a different program.
    assert _auto_session_code(p2, "Fall 2025") == "fall_2025"


# ---------------------------------------------------------------------------
# 2. has_enrollment_options
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_has_enrollment_options_program_without_sessions():
    school = SchoolFactory(program_field_key="program")
    _program(school)
    assert has_enrollment_options(school) is True


@pytest.mark.django_db
def test_has_enrollment_options_program_with_active_session():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    _session(prog, is_active=True)
    assert has_enrollment_options(school) is True


@pytest.mark.django_db
def test_has_enrollment_options_no_programs():
    school = SchoolFactory(program_field_key="program")
    assert has_enrollment_options(school) is False


@pytest.mark.django_db
def test_has_enrollment_options_inactive_program():
    school = SchoolFactory(program_field_key="program")
    _program(school, is_active=False)
    assert has_enrollment_options(school) is False


# ---------------------------------------------------------------------------
# 3. inject_db_program_options — optgroup path
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_inject_creates_option_groups_when_sessions_exist():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, name="Ballet", code="ballet")
    s1 = _session(prog, name="Fall 2025", code="fall_2025")
    s2 = _session(prog, name="Spring 2026", code="spring_2026")

    form_cfg = _minimal_form_cfg()
    result = inject_db_program_options(form_cfg, school)

    field = result["sections"][0]["fields"][0]
    assert "option_groups" in field
    assert "options" not in field
    groups = field["option_groups"]
    # One named group for Ballet.
    named = [g for g in groups if g["label"] == "Ballet"]
    assert len(named) == 1
    values = [o["value"] for o in named[0]["options"]]
    assert f"session:{s1.pk}" in values
    assert f"session:{s2.pk}" in values


@pytest.mark.django_db
def test_inject_flat_options_when_no_sessions():
    school = SchoolFactory(program_field_key="program")
    _program(school, name="Ballet", code="ballet")
    _program(school, name="Jazz", code="jazz")

    form_cfg = _minimal_form_cfg()
    result = inject_db_program_options(form_cfg, school)

    field = result["sections"][0]["fields"][0]
    assert "option_groups" not in field
    values = [o["value"] for o in field["options"]]
    assert "program:ballet" in values
    assert "program:jazz" in values


@pytest.mark.django_db
def test_inject_inactive_session_excluded_from_options():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    active = _session(prog, name="Fall 2025", code="fall_2025", is_active=True)
    inactive = _session(prog, name="Old Session", code="old_session", is_active=False)

    form_cfg = _minimal_form_cfg()
    result = inject_db_program_options(form_cfg, school)

    field = result["sections"][0]["fields"][0]
    all_values = [o["value"] for g in field["option_groups"] for o in g["options"]]
    assert f"session:{active.pk}" in all_values
    assert f"session:{inactive.pk}" not in all_values


@pytest.mark.django_db
def test_inject_no_programs_warning_when_all_inactive():
    school = SchoolFactory(program_field_key="program")
    _program(school, is_active=False)

    form_cfg = _minimal_form_cfg()
    result = inject_db_program_options(form_cfg, school)

    field = result["sections"][0]["fields"][0]
    assert field.get("no_programs_warning") is True


# ---------------------------------------------------------------------------
# 4. resolve_submission_program_and_session
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resolver_session_namespaced():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    sess = _session(prog)

    program, session = resolve_submission_program_and_session(school, {"program": f"session:{sess.pk}"})
    assert program == prog
    assert session == sess


@pytest.mark.django_db
def test_resolver_program_namespaced():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, code="ballet")

    program, session = resolve_submission_program_and_session(school, {"program": "program:ballet"})
    assert program == prog
    assert session is None


@pytest.mark.django_db
def test_resolver_legacy_bare_code():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, code="ballet")

    program, session = resolve_submission_program_and_session(school, {"program": "ballet"})
    assert program == prog
    assert session is None


@pytest.mark.django_db
def test_resolver_deleted_session_returns_none():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    sess = _session(prog, is_deleted=True)

    program, session = resolve_submission_program_and_session(school, {"program": f"session:{sess.pk}"})
    assert program is None
    assert session is None


@pytest.mark.django_db
def test_resolver_wrong_school_session_returns_none():
    school1 = SchoolFactory(program_field_key="program")
    school2 = SchoolFactory(program_field_key="program")
    prog2 = _program(school2, name="Ballet", code="ballet")
    sess2 = _session(prog2)

    # school1 tries to resolve a session belonging to school2.
    program, session = resolve_submission_program_and_session(school1, {"program": f"session:{sess2.pk}"})
    assert program is None
    assert session is None


# ---------------------------------------------------------------------------
# 5. apply_auto_enrollment — session-level
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_session_auto_enroll_enrolls_within_capacity():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, auto_enroll=False)  # program flag off
    sess = _session(prog, auto_enroll=True, capacity=5)
    sub = SubmissionFactory(school=school, status=STATUS_NEW)

    apply_auto_enrollment(school, sub, prog, session=sess)

    sub.refresh_from_db()
    assert sub.status == STATUS_ENROLLED


@pytest.mark.django_db
def test_session_auto_enroll_waitlists_when_full():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, auto_enroll=False)
    sess = _session(prog, auto_enroll=True, capacity=1, waitlist_enabled=True)
    # Fill the seat.
    existing = SubmissionFactory(school=school, status=STATUS_ENROLLED, session=sess, program=prog)
    new_sub = SubmissionFactory(school=school, status=STATUS_NEW)

    apply_auto_enrollment(school, new_sub, prog, session=sess)

    new_sub.refresh_from_db()
    assert new_sub.status == STATUS_WAITLISTED


@pytest.mark.django_db
def test_session_auto_enroll_noop_when_full_no_waitlist():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, auto_enroll=False)
    sess = _session(prog, auto_enroll=True, capacity=1, waitlist_enabled=False)
    SubmissionFactory(school=school, status=STATUS_ENROLLED, session=sess, program=prog)
    new_sub = SubmissionFactory(school=school, status=STATUS_NEW)

    apply_auto_enrollment(school, new_sub, prog, session=sess)

    new_sub.refresh_from_db()
    assert new_sub.status == STATUS_NEW


@pytest.mark.django_db
def test_session_auto_enroll_off_is_noop():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, auto_enroll=False)
    sess = _session(prog, auto_enroll=False, capacity=10)
    sub = SubmissionFactory(school=school, status=STATUS_NEW)

    apply_auto_enrollment(school, sub, prog, session=sess)

    sub.refresh_from_db()
    assert sub.status == STATUS_NEW


@pytest.mark.django_db
def test_session_capacity_independent_from_program_capacity():
    """Session cap=1 should not be affected by program cap=100."""
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, auto_enroll=True, capacity=100)
    sess = _session(prog, auto_enroll=True, capacity=1, waitlist_enabled=True)
    SubmissionFactory(school=school, status=STATUS_ENROLLED, session=sess, program=prog)
    new_sub = SubmissionFactory(school=school, status=STATUS_NEW)

    apply_auto_enrollment(school, new_sub, prog, session=sess)

    new_sub.refresh_from_db()
    assert new_sub.status == STATUS_WAITLISTED


# ---------------------------------------------------------------------------
# 6. Admin CRUD views
# ---------------------------------------------------------------------------

@pytest.fixture
def admin_client_and_school():
    school = SchoolFactory(program_field_key="program")
    membership = SchoolAdminMembershipFactory(school=school)
    client = Client()
    client.force_login(membership.user)
    return client, school


@pytest.mark.django_db
def test_session_create_view(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school, name="Ballet", code="ballet")
    url = reverse("school_session_create", kwargs={"school_slug": school.slug, "program_id": prog.pk})

    resp = client.post(url, {"name": "Fall 2025", "code": "", "capacity": "10", "auto_enroll": "1"})
    assert resp.status_code == 302
    sess = SchoolSession.objects.get(program=prog)
    assert sess.name == "Fall 2025"
    assert sess.code == "fall_2025"
    assert sess.capacity == 10
    assert sess.auto_enroll is True


@pytest.mark.django_db
def test_session_create_auto_generates_code_from_name(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    url = reverse("school_session_create", kwargs={"school_slug": school.slug, "program_id": prog.pk})

    client.post(url, {"name": "Morning Class", "code": "", "capacity": ""})
    sess = SchoolSession.objects.get(program=prog)
    assert sess.code == "morning_class"


@pytest.mark.django_db
def test_session_edit_view(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    sess = _session(prog, name="Fall 2025", code="fall_2025")
    url = reverse("school_session_edit", kwargs={"school_slug": school.slug, "program_id": prog.pk, "session_id": sess.pk})

    resp = client.post(url, {"name": "Fall 2025 Updated", "code": "fall_2025", "capacity": "20"})
    assert resp.status_code == 302
    sess.refresh_from_db()
    assert sess.name == "Fall 2025 Updated"
    assert sess.capacity == 20


@pytest.mark.django_db
def test_session_code_locked_after_submissions(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    sess = _session(prog, code="fall_2025")
    SubmissionFactory(school=school, session=sess, program=prog)
    url = reverse("school_session_edit", kwargs={"school_slug": school.slug, "program_id": prog.pk, "session_id": sess.pk})

    # POST with a different code — should be silently ignored (locked).
    client.post(url, {"name": "Fall 2025", "code": "new_code", "capacity": ""})
    sess.refresh_from_db()
    assert sess.code == "fall_2025"  # unchanged


@pytest.mark.django_db
def test_session_deactivate_view(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    sess = _session(prog, is_active=True)
    url = reverse("school_session_deactivate", kwargs={"school_slug": school.slug, "program_id": prog.pk, "session_id": sess.pk})

    resp = client.post(url)
    assert resp.status_code == 302
    sess.refresh_from_db()
    assert sess.is_active is False


@pytest.mark.django_db
def test_session_activate_view(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    sess = _session(prog, is_active=False)
    url = reverse("school_session_activate", kwargs={"school_slug": school.slug, "program_id": prog.pk, "session_id": sess.pk})

    resp = client.post(url)
    assert resp.status_code == 302
    sess.refresh_from_db()
    assert sess.is_active is True


@pytest.mark.django_db
def test_session_soft_delete(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    sess = _session(prog, is_active=False)
    url = reverse("school_session_delete", kwargs={"school_slug": school.slug, "program_id": prog.pk, "session_id": sess.pk})

    resp = client.post(url)
    assert resp.status_code == 302
    sess.refresh_from_db()
    assert sess.is_deleted is True


@pytest.mark.django_db
def test_session_delete_blocked_when_active(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    sess = _session(prog, is_active=True)
    url = reverse("school_session_delete", kwargs={"school_slug": school.slug, "program_id": prog.pk, "session_id": sess.pk})

    client.post(url)
    sess.refresh_from_db()
    assert sess.is_deleted is False


@pytest.mark.django_db
def test_session_delete_blocked_when_has_submissions(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    sess = _session(prog, is_active=False)
    SubmissionFactory(school=school, session=sess, program=prog)
    url = reverse("school_session_delete", kwargs={"school_slug": school.slug, "program_id": prog.pk, "session_id": sess.pk})

    client.post(url)
    sess.refresh_from_db()
    assert sess.is_deleted is False


# ---------------------------------------------------------------------------
# 7. Public apply form — session FK saved on submission
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_public_submit_sets_session_fk(settings):
    """Submitting session:<pk> value saves Submission.session FK."""
    settings.RATELIMIT_ENABLE = False
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, name="Ballet", code="ballet", auto_enroll=False)
    sess = _session(prog, name="Fall 2025", code="fall_2025", auto_enroll=False)

    # Minimal YAML config with program select field.
    from core.services.config_loader import load_school_config
    from unittest.mock import patch

    raw_cfg = {
        "school": {"display_name": school.display_name},
        "form": {
            "title": "Apply",
            "sections": [
                {
                    "title": "Info",
                    "fields": [
                        {"key": "first_name", "label": "First Name", "type": "text", "required": True},
                        {"key": "program", "label": "Program", "type": "select", "required": False,
                         "options": [{"value": f"session:{sess.pk}", "label": "Ballet — Fall 2025"}]},
                    ],
                }
            ],
        },
    }

    client = Client()
    url = reverse("apply", kwargs={"school_slug": school.slug})
    from core.services.config_loader import SchoolConfig
    with patch("core.views_public.load_school_config") as mock_load:
        mock_load.return_value = SchoolConfig(raw=raw_cfg)
        resp = client.post(url, {
            "first_name": "Alice",
            "program": f"session:{sess.pk}",
        }, follow=False)

    assert resp.status_code == 302
    sub = Submission.objects.filter(school=school).last()
    assert sub is not None
    assert sub.program == prog
    assert sub.session == sess


@pytest.mark.django_db
def test_public_submit_legacy_code_sets_program_no_session(settings):
    """A bare program code (legacy) saves program FK but not session."""
    settings.RATELIMIT_ENABLE = False
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, name="Ballet", code="ballet", auto_enroll=False)

    raw_cfg = {
        "school": {"display_name": school.display_name},
        "form": {
            "title": "Apply",
            "sections": [{"title": "Info", "fields": [
                {"key": "first_name", "label": "First Name", "type": "text", "required": True},
                {"key": "program", "label": "Program", "type": "select", "required": False,
                 "options": [{"value": "program:ballet", "label": "Ballet"}]},
            ]}],
        },
    }

    client = Client()
    url = reverse("apply", kwargs={"school_slug": school.slug})
    from core.services.config_loader import SchoolConfig
    with patch("core.views_public.load_school_config") as mock_load:
        mock_load.return_value = SchoolConfig(raw=raw_cfg)
        client.post(url, {"first_name": "Bob", "program": "program:ballet"}, follow=False)

    sub = Submission.objects.filter(school=school).last()
    assert sub is not None
    assert sub.program == prog
    assert sub.session is None


# ---------------------------------------------------------------------------
# 8. Regression tests for the review-identified bugs
# ---------------------------------------------------------------------------

# Bug 1: submission.session set → submission.program auto-populated
@pytest.mark.django_db
def test_submission_save_auto_sets_program_from_session():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    sess = _session(prog)
    # Create submission with session but no program — invariant should auto-fill.
    sub = SubmissionFactory(school=school, session=sess)
    sub.refresh_from_db()
    assert sub.program == prog


# Bug 4: program with sessions but all inactive → no enrollment options
@pytest.mark.django_db
def test_program_with_only_inactive_sessions_has_no_enrollment_options():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, is_active=True)
    _session(prog, name="Old Session", code="old", is_active=False)
    # All sessions inactive → must not fall back to bare program.
    assert has_enrollment_options(school) is False


@pytest.mark.django_db
def test_inject_program_with_only_inactive_sessions_shows_no_options():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    _session(prog, is_active=False)

    form_cfg = _minimal_form_cfg()
    result = inject_db_program_options(form_cfg, school)

    field = result["sections"][0]["fields"][0]
    assert field.get("no_programs_warning") is True


# Bug 4: program with some active, some inactive sessions — only active shown
@pytest.mark.django_db
def test_inject_mixed_sessions_only_shows_active():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    active = _session(prog, name="Active", code="active", is_active=True)
    _session(prog, name="Inactive", code="inactive", is_active=False)

    form_cfg = _minimal_form_cfg()
    result = inject_db_program_options(form_cfg, school)

    field = result["sections"][0]["fields"][0]
    assert "option_groups" in field
    all_values = [o["value"] for g in field["option_groups"] for o in g["options"]]
    assert f"session:{active.pk}" in all_values
    assert len(all_values) == 1  # inactive excluded


# Bug 5: resolver strict mode rejects inactive program
@pytest.mark.django_db
def test_resolver_strict_rejects_inactive_program():
    school = SchoolFactory(program_field_key="program")
    _program(school, code="ballet", is_active=False)

    program, session = resolve_submission_program_and_session(
        school, {"program": "program:ballet"}, strict=True
    )
    assert program is None


@pytest.mark.django_db
def test_resolver_strict_rejects_inactive_session():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school)
    sess = _session(prog, is_active=False)

    program, session = resolve_submission_program_and_session(
        school, {"program": f"session:{sess.pk}"}, strict=True
    )
    assert program is None
    assert session is None


@pytest.mark.django_db
def test_resolver_non_strict_accepts_inactive_program():
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, code="ballet", is_active=False)

    program, session = resolve_submission_program_and_session(
        school, {"program": "program:ballet"}, strict=False
    )
    assert program == prog


# Bug 6: date order validation
@pytest.mark.django_db
def test_session_create_rejects_end_before_start(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    url = reverse("school_session_create", kwargs={"school_slug": school.slug, "program_id": prog.pk})

    resp = client.post(url, {
        "name": "Bad Dates",
        "code": "",
        "capacity": "",
        "start_date": "2025-09-01",
        "end_date": "2025-08-01",  # before start
    })
    assert resp.status_code == 200  # re-render with error
    assert not SchoolSession.objects.filter(program=prog).exists()


@pytest.mark.django_db
def test_session_edit_rejects_end_before_start(admin_client_and_school):
    client, school = admin_client_and_school
    prog = _program(school)
    sess = _session(prog, code="fall")
    url = reverse("school_session_edit", kwargs={"school_slug": school.slug, "program_id": prog.pk, "session_id": sess.pk})

    resp = client.post(url, {
        "name": "Fall",
        "code": "fall",
        "capacity": "",
        "start_date": "2025-09-01",
        "end_date": "2025-06-01",
    })
    assert resp.status_code == 200
    sess.refresh_from_db()
    assert sess.start_date is None  # unchanged


# Bug 3: get_programs_summary includes program-level counts
@pytest.mark.django_db
def test_programs_summary_includes_program_level_counts():
    from core.services.programs import get_programs_summary
    school = SchoolFactory(program_field_key="program")
    prog = _program(school, code="ballet")
    sess = _session(prog)
    # One submission at session level, one at program level.
    SubmissionFactory(school=school, status=STATUS_ENROLLED, session=sess, program=prog)
    SubmissionFactory(school=school, status=STATUS_ENROLLED, program=prog)

    summary = get_programs_summary(school)
    entry = summary["ballet"]
    assert entry["enrolled_count"] == 2          # total
    assert entry["program_level_enrolled"] == 1  # session=NULL only
