"""
Tests for DB-driven program management (Phase: Program Management).

Run:
  pytest core/tests/test_programs.py -x -q
"""
from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse

from core.models import AdminAuditLog, SchoolProgram, Submission
from core.services.programs import (
    apply_auto_enrollment,
    get_program_options,
    get_programs_summary,
    resolve_submission_program,
)
from core.services.admin_submission_yaml import build_yaml_sections
from core.tests.factories import SchoolFactory, SchoolAdminMembershipFactory, SubmissionFactory, UserFactory
from core.views_school_common import STATUS_ENROLLED, STATUS_NEW, STATUS_WAITLISTED


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_program(school, name="Ballet", code="ballet", is_active=True, **kwargs):
    return SchoolProgram.objects.create(
        school=school, name=name, code=code, is_active=is_active, **kwargs
    )


def _minimal_yaml_cfg(field_key="program", options=None):
    """Return a minimal cfg-like object with a select field."""
    options = options or [{"value": "ballet", "label": "Ballet"}, {"value": "jazz", "label": "Jazz"}]

    class FakeCfg:
        form = {
            "sections": [
                {
                    "title": "Program",
                    "fields": [
                        {
                            "key": field_key,
                            "type": "select",
                            "label": "Program",
                            "required": True,
                            "options": options,
                        }
                    ],
                }
            ]
        }

    return FakeCfg()


# ---------------------------------------------------------------------------
# 1. get_program_options returns active programs only
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_program_options_from_db_when_field_key_set():
    school = SchoolFactory(program_field_key="program")
    _make_program(school, name="Ballet", code="ballet", is_active=True)
    _make_program(school, name="Jazz", code="jazz", is_active=True)
    _make_program(school, name="Inactive", code="inactive", is_active=False)

    opts = get_program_options(school)
    codes = [o["value"] for o in opts]
    assert "ballet" in codes
    assert "jazz" in codes
    assert "inactive" not in codes


# ---------------------------------------------------------------------------
# 2. build_yaml_sections uses YAML options when no program_field_key
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_program_options_from_yaml_when_no_field_key():
    school = SchoolFactory(program_field_key="")
    # Add a DB program — but it should NOT appear in form options
    _make_program(school, name="DB Program", code="db_prog")

    cfg = _minimal_yaml_cfg(field_key="program", options=[
        {"value": "yaml_a", "label": "YAML A"},
        {"value": "yaml_b", "label": "YAML B"},
    ])
    sections = build_yaml_sections(cfg, existing_data={}, school=school)
    assert sections
    field = sections[0]["fields"][0]
    codes = [o["value"] for o in field["options"]]
    assert "yaml_a" in codes
    assert "yaml_b" in codes
    assert "db_prog" not in codes


# ---------------------------------------------------------------------------
# 3. No active programs → no_programs_warning=True on the field
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_no_active_programs_renders_disabled_option_and_blocks_required_field():
    school = SchoolFactory(program_field_key="program")
    # No active programs

    cfg = _minimal_yaml_cfg(field_key="program")
    sections = build_yaml_sections(cfg, existing_data={}, school=school)
    assert sections
    field = sections[0]["fields"][0]
    assert field["no_programs_warning"] is True
    assert field["options"] == []


# ---------------------------------------------------------------------------
# 4. Inactive program not in form options
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_inactive_program_hidden_from_form():
    school = SchoolFactory(program_field_key="program")
    _make_program(school, name="Active", code="active", is_active=True)
    _make_program(school, name="Inactive", code="inactive", is_active=False)

    cfg = _minimal_yaml_cfg(field_key="program")
    sections = build_yaml_sections(cfg, existing_data={}, school=school)
    field = sections[0]["fields"][0]
    codes = [o["value"] for o in field["options"]]
    assert "active" in codes
    assert "inactive" not in codes


# ---------------------------------------------------------------------------
# 5. Auto-enroll when slots available → STATUS_ENROLLED
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_enroll_on_submission_when_slots_available():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(school, code="ballet", auto_enroll=True, capacity=10)
    submission = SubmissionFactory(school=school, status=STATUS_NEW)

    apply_auto_enrollment(school, submission, program)
    submission.refresh_from_db()
    assert submission.status == STATUS_ENROLLED


# ---------------------------------------------------------------------------
# 6. Auto-waitlist when at capacity and waitlist_enabled=True
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_waitlist_on_submission_when_at_capacity_and_waitlist_enabled():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(school, code="ballet", auto_enroll=True, capacity=1, waitlist_enabled=True)

    # Fill the slot
    existing = SubmissionFactory(school=school, status=STATUS_ENROLLED)
    existing.program = program
    existing.save(update_fields=["program"])

    # New submission should be waitlisted
    new_sub = SubmissionFactory(school=school, status=STATUS_NEW)
    apply_auto_enrollment(school, new_sub, program)
    new_sub.refresh_from_db()
    assert new_sub.status == STATUS_WAITLISTED


# ---------------------------------------------------------------------------
# 7. auto_enroll=False → status stays New (no-op)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_enroll_status_new_when_auto_enroll_off():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(school, code="ballet", auto_enroll=False)
    submission = SubmissionFactory(school=school, status=STATUS_NEW)

    apply_auto_enrollment(school, submission, program)
    submission.refresh_from_db()
    assert submission.status == STATUS_NEW


# ---------------------------------------------------------------------------
# 8. Waitlist does not trigger when auto_enroll=False
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_waitlist_does_not_trigger_when_auto_enroll_false():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(
        school, code="ballet", auto_enroll=False, capacity=1, waitlist_enabled=True
    )

    # Fill the slot
    existing = SubmissionFactory(school=school, status=STATUS_ENROLLED)
    existing.program = program
    existing.save(update_fields=["program"])

    new_sub = SubmissionFactory(school=school, status=STATUS_NEW)
    apply_auto_enrollment(school, new_sub, program)
    new_sub.refresh_from_db()
    # Should remain New — auto_enroll is False
    assert new_sub.status == STATUS_NEW


# ---------------------------------------------------------------------------
# 9. resolve_submission_program matches program FK by code
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_submission_program_fk_set_on_save():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(school, code="ballet")

    result = resolve_submission_program(school, {"program": "ballet"})
    assert result is not None
    assert result.pk == program.pk


# ---------------------------------------------------------------------------
# 10. Admin create program via POST
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_create_program():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_create", kwargs={"school_slug": school.slug})
    resp = client.post(url, {
        "name": "Ballet",
        "code": "ballet",
        "capacity": "20",
        "auto_enroll": "1",
        "waitlist_enabled": "0",
        "display_order": "0",
    })
    assert resp.status_code == 302
    assert SchoolProgram.objects.filter(school=school, code="ballet").exists()


# ---------------------------------------------------------------------------
# 11. Admin edit program logs audit
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_edit_program_logs_audit():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    program = _make_program(school, name="Old Name", code="ballet")
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_edit", kwargs={"school_slug": school.slug, "program_id": program.pk})
    resp = client.post(url, {
        "name": "New Name",
        "code": "ballet",
        "capacity": "",
        "auto_enroll": "0",
        "waitlist_enabled": "0",
        "display_order": "0",
    })
    assert resp.status_code == 302
    program.refresh_from_db()
    assert program.name == "New Name"

    log = AdminAuditLog.objects.filter(
        model_label="core.schoolprogram",
        object_id=str(program.pk),
        action="change",
    ).first()
    assert log is not None
    assert log.extra.get("name") == "program_edited"


# ---------------------------------------------------------------------------
# 12. Code is never changed via the edit form (always preserved as-is)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_program_code_never_changes_via_edit_form():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    program = _make_program(school, name="Ballet", code="ballet")
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_edit", kwargs={"school_slug": school.slug, "program_id": program.pk})
    resp = client.post(url, {
        "name": "Ballet Renamed",
        "capacity": "",
        "auto_enroll": "0",
        "waitlist_enabled": "0",
    })
    assert resp.status_code == 302
    program.refresh_from_db()
    # Code stays the same regardless of what was POSTed
    assert program.code == "ballet"
    # Name was updated
    assert program.name == "Ballet Renamed"


# ---------------------------------------------------------------------------
# 13. Code is auto-generated from name on create
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_program_code_auto_generated_from_name():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_create", kwargs={"school_slug": school.slug})
    resp = client.post(url, {
        "name": "Hip Hop Dance",
        "capacity": "",
        "auto_enroll": "0",
        "waitlist_enabled": "0",
    })
    assert resp.status_code == 302
    program = SchoolProgram.objects.get(school=school, name="Hip Hop Dance")
    assert program.code == "hip_hop_dance"


# ---------------------------------------------------------------------------
# 14. Deactivate always sets is_active=False (never deletes)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_deactivate_program_always_deactivates():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    program = _make_program(school, name="Ballet", code="ballet", is_active=True)
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_deactivate", kwargs={"school_slug": school.slug, "program_id": program.pk})
    resp = client.post(url)
    assert resp.status_code == 302
    program.refresh_from_db()
    assert program.is_active is False
    # Program still exists — deactivate never deletes
    assert SchoolProgram.objects.filter(pk=program.pk).exists()


# ---------------------------------------------------------------------------
# 15. Delete via school_program_delete only when no submissions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_delete_program_without_submissions():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    program = _make_program(school, name="Ballet", code="ballet", is_active=False)
    pk = program.pk
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_delete", kwargs={"school_slug": school.slug, "program_id": pk})
    resp = client.post(url)
    assert resp.status_code == 302
    assert not SchoolProgram.objects.filter(pk=pk).exists()


@pytest.mark.django_db
def test_admin_delete_program_blocked_when_has_submissions():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    program = _make_program(school, name="Ballet", code="ballet", is_active=False)
    sub = SubmissionFactory(school=school)
    sub.program = program
    sub.save(update_fields=["program"])
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_delete", kwargs={"school_slug": school.slug, "program_id": program.pk})
    resp = client.post(url)
    assert resp.status_code == 302
    # Program must still exist — delete was blocked
    assert SchoolProgram.objects.filter(pk=program.pk).exists()


# ---------------------------------------------------------------------------
# 16. Seed command creates programs from mocked YAML
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_seed_command_creates_programs_from_yaml(monkeypatch):
    school = SchoolFactory(slug="test-seed-school", program_field_key="")

    class FakeConfig:
        form = {
            "sections": [
                {
                    "title": "Programs",
                    "fields": [
                        {
                            "key": "program",
                            "type": "select",
                            "options": [
                                {"value": "ballet", "label": "Ballet"},
                                {"value": "jazz", "label": "Jazz"},
                            ],
                        }
                    ],
                }
            ]
        }

    from core.management.commands import seed_school_programs_from_yaml as cmd_module
    monkeypatch.setattr(cmd_module, "load_school_config", lambda slug: FakeConfig())

    from django.core.management import call_command
    call_command(
        "seed_school_programs_from_yaml",
        "--school", school.slug,
        "--field-key", "program",
    )

    assert SchoolProgram.objects.filter(school=school, code="ballet").exists()
    assert SchoolProgram.objects.filter(school=school, code="jazz").exists()
    school.refresh_from_db()
    assert school.program_field_key == "program"


# ---------------------------------------------------------------------------
# 17. Seed command is idempotent
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_seed_command_idempotent(monkeypatch):
    school = SchoolFactory(slug="test-idempotent-school", program_field_key="program")

    class FakeConfig:
        form = {
            "sections": [
                {
                    "title": "Programs",
                    "fields": [
                        {
                            "key": "program",
                            "type": "select",
                            "options": [{"value": "ballet", "label": "Ballet"}],
                        }
                    ],
                }
            ]
        }

    from core.management.commands import seed_school_programs_from_yaml as cmd_module
    monkeypatch.setattr(cmd_module, "load_school_config", lambda slug: FakeConfig())

    from django.core.management import call_command
    call_command(
        "seed_school_programs_from_yaml",
        "--school", school.slug,
        "--field-key", "program",
    )
    call_command(
        "seed_school_programs_from_yaml",
        "--school", school.slug,
        "--field-key", "program",
    )

    assert SchoolProgram.objects.filter(school=school, code="ballet").count() == 1


# ---------------------------------------------------------------------------
# 18. Backfill matches by code then normalized name
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_backfill_matches_code_then_normalized_name(monkeypatch):
    school = SchoolFactory(slug="test-backfill-school", program_field_key="program")
    p_ballet = _make_program(school, name="Ballet Class", code="ballet")
    p_jazz = _make_program(school, name="Jazz Class", code="jazz")

    # Submission with exact code match
    sub_code = SubmissionFactory(school=school, data={"program": "ballet"})
    # Submission with name match (value = program name, not code)
    sub_name = SubmissionFactory(school=school, data={"program": "jazz class"})
    # Submission with no match
    sub_none = SubmissionFactory(school=school, data={"program": "unknown"})

    class FakeConfig:
        form = {
            "sections": [
                {
                    "title": "Programs",
                    "fields": [
                        {
                            "key": "program",
                            "type": "select",
                            "options": [
                                {"value": "ballet", "label": "Ballet Class"},
                                {"value": "jazz", "label": "Jazz Class"},
                            ],
                        }
                    ],
                }
            ]
        }

    from core.management.commands import seed_school_programs_from_yaml as cmd_module
    monkeypatch.setattr(cmd_module, "load_school_config", lambda slug: FakeConfig())

    from django.core.management import call_command
    call_command(
        "seed_school_programs_from_yaml",
        "--school", school.slug,
        "--field-key", "program",
        "--backfill-submissions",
    )

    sub_code.refresh_from_db()
    sub_name.refresh_from_db()
    sub_none.refresh_from_db()

    assert sub_code.program_id == p_ballet.pk
    assert sub_name.program_id == p_jazz.pk
    assert sub_none.program_id is None


# ---------------------------------------------------------------------------
# 19. Backfill skips when no code match
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_backfill_skips_no_match(monkeypatch):
    school = SchoolFactory(slug="test-nomatch-school", program_field_key="program")
    _make_program(school, name="Ballet", code="ballet")

    sub = SubmissionFactory(school=school, data={"program": "does_not_exist"})

    class FakeConfig:
        form = {
            "sections": [
                {
                    "title": "Programs",
                    "fields": [
                        {
                            "key": "program",
                            "type": "select",
                            "options": [{"value": "ballet", "label": "Ballet"}],
                        }
                    ],
                }
            ]
        }

    from core.management.commands import seed_school_programs_from_yaml as cmd_module
    monkeypatch.setattr(cmd_module, "load_school_config", lambda slug: FakeConfig())

    from django.core.management import call_command
    call_command(
        "seed_school_programs_from_yaml",
        "--school", school.slug,
        "--field-key", "program",
        "--backfill-submissions",
    )

    sub.refresh_from_db()
    assert sub.program_id is None


# ---------------------------------------------------------------------------
# 20. Programs list shows enrolled count
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_program_list_shows_enrolled_count():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(school, name="Ballet", code="ballet")

    # Create enrolled submissions
    for _ in range(3):
        sub = SubmissionFactory(school=school, status=STATUS_ENROLLED)
        sub.program = program
        sub.save(update_fields=["program"])

    # One waitlisted
    sub_wait = SubmissionFactory(school=school, status=STATUS_WAITLISTED)
    sub_wait.program = program
    sub_wait.save(update_fields=["program"])

    summary = get_programs_summary(school)
    assert summary["ballet"]["enrolled_count"] == 3
    assert summary["ballet"]["waitlisted_count"] == 1


# ---------------------------------------------------------------------------
# 21. Capacity change audit logged with old/new values
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_capacity_change_audit_logged_with_old_new_values():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    program = _make_program(school, name="Ballet", code="ballet", capacity=10)
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_edit", kwargs={"school_slug": school.slug, "program_id": program.pk})
    resp = client.post(url, {
        "name": "Ballet",
        "code": "ballet",
        "capacity": "25",
        "auto_enroll": "0",
        "waitlist_enabled": "0",
        "display_order": "0",
    })
    assert resp.status_code == 302

    log = AdminAuditLog.objects.filter(
        model_label="core.schoolprogram",
        object_id=str(program.pk),
        action="change",
    ).order_by("-created_at").first()

    assert log is not None
    changed = log.changes
    assert "capacity" in changed
    assert changed["capacity"]["old"] == 10
    assert changed["capacity"]["new"] == 25


# ---------------------------------------------------------------------------
# 22. Programs list redirects to settings (programs now embedded there)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_programs_list_redirects_to_settings():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    school.program_field_key = "program"
    school.save(update_fields=["program_field_key"])
    _make_program(school, name="Ballet", code="ballet")

    client = Client()
    client.force_login(membership.user)

    url = reverse("school_programs_list", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 302
    assert "settings" in resp["Location"]


# ---------------------------------------------------------------------------
# 23. auto_enrolled audit event includes full context
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_enrolled_audit_event_has_full_context():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(school, code="ballet", auto_enroll=True, capacity=10)
    submission = SubmissionFactory(school=school, status=STATUS_NEW)

    apply_auto_enrollment(school, submission, program)
    submission.refresh_from_db()
    assert submission.status == STATUS_ENROLLED

    log = AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(submission.pk),
        action="action",
    ).order_by("-created_at").first()

    assert log is not None
    extra = log.extra
    assert extra["name"] == "auto_enrolled"
    assert extra["submission_id"] == submission.pk
    assert extra["program_code"] == "ballet"
    assert extra["old_status"] == STATUS_NEW
    assert extra["new_status"] == STATUS_ENROLLED
    assert extra["enrolled_count_before"] == 0
    assert extra["enrolled_count_after"] == 1
    assert extra["capacity"] == 10
    assert extra["waitlist_enabled"] is False


# ---------------------------------------------------------------------------
# 24. auto_waitlisted audit event includes full context
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_waitlisted_audit_event_has_full_context():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(school, code="ballet", auto_enroll=True, capacity=1, waitlist_enabled=True)

    # Fill the one slot
    existing = SubmissionFactory(school=school, status=STATUS_ENROLLED)
    existing.program = program
    existing.save(update_fields=["program"])

    new_sub = SubmissionFactory(school=school, status=STATUS_NEW)
    apply_auto_enrollment(school, new_sub, program)
    new_sub.refresh_from_db()
    assert new_sub.status == STATUS_WAITLISTED

    log = AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(new_sub.pk),
        action="action",
    ).order_by("-created_at").first()

    assert log is not None
    extra = log.extra
    assert extra["name"] == "auto_waitlisted"
    assert extra["submission_id"] == new_sub.pk
    assert extra["program_code"] == "ballet"
    assert extra["old_status"] == STATUS_NEW
    assert extra["new_status"] == STATUS_WAITLISTED
    assert extra["enrolled_count_before"] == 1
    assert extra["capacity"] == 1
    assert extra["waitlist_enabled"] is True


# ---------------------------------------------------------------------------
# 25. auto_enroll_skipped does NOT create an audit log
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_auto_enroll_skipped_does_not_create_audit_log():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(school, code="ballet", auto_enroll=True, capacity=1, waitlist_enabled=False)

    # Fill the one slot
    existing = SubmissionFactory(school=school, status=STATUS_ENROLLED)
    existing.program = program
    existing.save(update_fields=["program"])

    new_sub = SubmissionFactory(school=school, status=STATUS_NEW)
    apply_auto_enrollment(school, new_sub, program)
    new_sub.refresh_from_db()
    # Status should stay New — no waitlist, no auto-enroll possible
    assert new_sub.status == STATUS_NEW

    # No audit log should be created for this submission
    count = AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(new_sub.pk),
        action="action",
    ).count()
    assert count == 0


# ---------------------------------------------------------------------------
# 26. program_capacity_changed creates dedicated audit event
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_program_capacity_changed_creates_dedicated_audit_event():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    program = _make_program(school, name="Ballet", code="ballet", capacity=10)
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_edit", kwargs={"school_slug": school.slug, "program_id": program.pk})
    client.post(url, {
        "name": "Ballet",
        "code": "ballet",
        "capacity": "20",
        "auto_enroll": "0",
        "waitlist_enabled": "0",
        "display_order": "0",
    })

    log = AdminAuditLog.objects.filter(
        model_label="core.schoolprogram",
        object_id=str(program.pk),
        action="action",
    ).filter(extra__name="program_capacity_changed").first()

    assert log is not None
    extra = log.extra
    assert extra["old_capacity"] == 10
    assert extra["new_capacity"] == 20
    assert "current_enrolled" in extra


# ---------------------------------------------------------------------------
# 27. program_auto_enroll_changed creates dedicated audit event
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_program_auto_enroll_changed_creates_dedicated_audit_event():
    membership = SchoolAdminMembershipFactory()
    school = membership.school
    program = _make_program(school, name="Ballet", code="ballet", auto_enroll=False)
    client = Client()
    client.force_login(membership.user)

    url = reverse("school_program_edit", kwargs={"school_slug": school.slug, "program_id": program.pk})
    client.post(url, {
        "name": "Ballet",
        "code": "ballet",
        "capacity": "",
        "auto_enroll": "1",
        "waitlist_enabled": "0",
        "display_order": "0",
    })

    log = AdminAuditLog.objects.filter(
        model_label="core.schoolprogram",
        object_id=str(program.pk),
        action="action",
    ).filter(extra__name="program_auto_enroll_changed").first()

    assert log is not None
    extra = log.extra
    assert extra["old"] is False
    assert extra["new"] is True


# ---------------------------------------------------------------------------
# 28. YAML options stripped → DB programs appear in form
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_yaml_field_with_no_options_uses_db_programs():
    """When YAML has no options for the program field key, DB programs are used."""
    school = SchoolFactory(program_field_key="program")
    _make_program(school, name="Ballet", code="ballet", is_active=True)
    _make_program(school, name="Jazz", code="jazz", is_active=True)

    # YAML field has NO options (simulating stripped YAML)
    class FakeCfgNoOptions:
        form = {
            "sections": [
                {
                    "title": "Class",
                    "fields": [{"key": "program", "type": "select", "label": "Class", "required": True}],
                }
            ]
        }

    sections = build_yaml_sections(FakeCfgNoOptions(), existing_data={}, school=school)
    field = sections[0]["fields"][0]
    codes = [o["value"] for o in field["options"]]
    assert "ballet" in codes
    assert "jazz" in codes


# ---------------------------------------------------------------------------
# 29. Inactive program submissions preserved in admin summary
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_inactive_program_submissions_preserved_in_summary():
    school = SchoolFactory(program_field_key="program")
    program = _make_program(school, name="Old Class", code="old_class", is_active=False)

    sub = SubmissionFactory(school=school, status=STATUS_ENROLLED)
    sub.program = program
    sub.save(update_fields=["program"])

    summary = get_programs_summary(school)
    assert "old_class" in summary
    assert summary["old_class"]["enrolled_count"] == 1
    assert summary["old_class"]["is_active"] is False


# ---------------------------------------------------------------------------
# 30. Inactive program not offered on public form but active programs are
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_inactive_hidden_active_shown_in_public_options():
    school = SchoolFactory(program_field_key="program")
    _make_program(school, name="Active A", code="active_a", is_active=True)
    _make_program(school, name="Active B", code="active_b", is_active=True)
    _make_program(school, name="Retired", code="retired", is_active=False)

    opts = get_program_options(school)
    codes = [o["value"] for o in opts]
    assert "active_a" in codes
    assert "active_b" in codes
    assert "retired" not in codes
