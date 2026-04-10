"""
Tests for generic YAML-configured CSV export profiles.

Covers:
  - core/services/integrations.py  (get_export_configs, slugify_export_name,
                                     normalize_csv_value, resolve_export_row)
  - core/admin/submissions.py       (get_actions, _make_integration_export_action,
                                     _do_integration_export)
"""
import csv
import io
import pytest

from django.contrib.admin.sites import site as admin_site
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory

from core.admin import SubmissionAdmin
from core.models import Submission
from core.services.integrations import (
    get_export_configs,
    normalize_csv_value,
    resolve_export_row,
    slugify_export_name,
)
from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_request(user, method="get"):
    rf = RequestFactory()
    req = getattr(rf, method)("/")
    req.user = user
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    setattr(req, "_messages", FallbackStorage(req))
    return req


def _messages_list(req):
    return [str(m) for m in get_messages(req)]


# ── get_export_configs ────────────────────────────────────────────────────────


def test_get_export_configs_empty_when_no_exports_key():
    assert get_export_configs({}) == {}
    assert get_export_configs({"other": {}}) == {}
    assert get_export_configs(None) == {}  # type: ignore[arg-type]


def test_get_export_configs_empty_when_field_map_missing_or_empty():
    raw = {
        "exports": {
            "no_field_map": {"something": "else"},
            "empty_field_map": {"field_map": {}},
        }
    }
    assert get_export_configs(raw) == {}


def test_get_export_configs_returns_all_profiles():
    raw = {
        "exports": {
            "brightwheel": {
                "field_map": {
                    "first_name": {"source": "student_first_name"},
                    "homeroom": {"value": "Dance Academy"},
                }
            },
            "procare": {
                "field_map": {
                    "child_first": {"source": "student_first_name"},
                }
            },
        }
    }
    result = get_export_configs(raw)
    assert list(result.keys()) == ["brightwheel", "procare"]
    assert "first_name" in result["brightwheel"]
    assert "child_first" in result["procare"]


# ── slugify_export_name ───────────────────────────────────────────────────────


def test_slugify_handles_spaces_and_special_chars():
    assert slugify_export_name("Brightwheel CSV") == "brightwheel_csv"
    assert slugify_export_name("Pro-Care  2.0") == "pro_care_2_0"
    assert slugify_export_name("UPPER") == "upper"


def test_slugify_garbage_input_falls_back_to_export():
    # Non-empty inputs that strip to nothing fall back to "export"
    # Collision safety is the caller's responsibility (used_names set), not slugify's
    assert slugify_export_name("!!!") == "export"
    assert slugify_export_name("   ") == "export"
    assert slugify_export_name("") == "export"


# ── normalize_csv_value ───────────────────────────────────────────────────────


def test_normalize_none_returns_empty_string():
    assert normalize_csv_value(None) == ""


def test_normalize_bool_returns_yes_no():
    assert normalize_csv_value(True) == "Yes"
    assert normalize_csv_value(False) == "No"


def test_normalize_list_returns_comma_joined():
    assert normalize_csv_value(["mon", "wed", "fri"]) == "mon, wed, fri"
    assert normalize_csv_value([]) == ""


def test_normalize_dict_returns_json():
    val = {"key": "value", "n": 1}
    result = normalize_csv_value(val)
    import json
    assert json.loads(result) == val


# ── resolve_export_row ────────────────────────────────────────────────────────


def test_resolve_source_uses_submission_value():
    field_map = {"first_name": {"source": "student_first_name"}}
    row, warnings = resolve_export_row({"student_first_name": "Alice"}, field_map)
    assert row["first_name"] == "Alice"
    assert warnings == []


def test_resolve_source_missing_returns_empty_and_warns():
    field_map = {"first_name": {"source": "student_first_name"}}
    row, warnings = resolve_export_row({}, field_map)
    assert row["first_name"] == ""
    assert len(warnings) == 1
    assert "student_first_name" in warnings[0]


def test_resolve_value_is_literal():
    field_map = {"homeroom": {"value": "Dance Academy"}}
    row, warnings = resolve_export_row({}, field_map)
    assert row["homeroom"] == "Dance Academy"
    assert warnings == []


def test_resolve_source_any_uses_first_found():
    field_map = {
        "email": {"source_any": ["contact_email", "guardian_email", "email"]}
    }
    data = {"guardian_email": "parent@example.com", "email": "other@example.com"}
    row, warnings = resolve_export_row(data, field_map)
    assert row["email"] == "parent@example.com"
    assert warnings == []


def test_resolve_source_any_warns_when_none_found():
    field_map = {"email": {"source_any": ["contact_email", "guardian_email"]}}
    row, warnings = resolve_export_row({}, field_map)
    assert row["email"] == ""
    assert len(warnings) == 1
    assert "contact_email" in warnings[0]
    assert "guardian_email" in warnings[0]


def test_resolve_source_any_invalid_non_list_warns():
    field_map = {"email": {"source_any": "not_a_list"}}
    row, warnings = resolve_export_row({"not_a_list": "x"}, field_map)
    assert row["email"] == ""
    assert any("list of strings" in w for w in warnings)


def test_resolve_legacy_string_source_only_not_literal():
    # Bare string treated as source: only — missing → "" + warning, NOT used as literal
    field_map = {"first_name": "student_first_name"}
    row, warnings = resolve_export_row({}, field_map)
    assert row["first_name"] == ""
    assert len(warnings) == 1

    # If the key IS present, use its value (not the string "student_first_name" literally)
    row2, warnings2 = resolve_export_row({"student_first_name": "Bob"}, field_map)
    assert row2["first_name"] == "Bob"
    assert warnings2 == []


def test_resolve_invalid_spec_returns_empty_and_warns():
    field_map = {"bad": {"unknown_key": "whatever"}}
    row, warnings = resolve_export_row({}, field_map)
    assert row["bad"] == ""
    assert any("invalid spec" in w for w in warnings)


def test_resolve_source_any_skips_empty_value_and_uses_next():
    # Key present but empty — should skip and fall through to next key
    field_map = {"email": {"source_any": ["contact_email", "guardian_email", "email"]}}
    data = {
        "contact_email": "",            # present but empty — skip
        "guardian_email": "parent@example.com",  # non-empty — use this
        "email": "other@example.com",
    }
    row, warnings = resolve_export_row(data, field_map)
    assert row["email"] == "parent@example.com"
    assert warnings == []


# ── Admin action: get_actions ─────────────────────────────────────────────────


@pytest.mark.django_db
def test_get_actions_adds_profile_action_for_staff_with_export_config(monkeypatch):
    school = SchoolFactory.create(plan="starter")
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    class _Cfg:
        raw = {
            "exports": {
                "brightwheel": {
                    "field_map": {"first_name": {"source": "student_first_name"}}
                }
            }
        }

    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: _Cfg())

    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(staff)
    actions = ma.get_actions(req)

    assert "export_brightwheel_csv" in actions
    label = actions["export_brightwheel_csv"][2]
    assert "brightwheel" in label  # raw profile_name, no title-casing
    assert "[" not in label  # no school name in brackets for staff


@pytest.mark.django_db
def test_get_actions_no_profile_action_when_no_exports_in_yaml(monkeypatch):
    school = SchoolFactory.create(plan="starter")
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    class _Cfg:
        raw = {}  # no exports section

    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: _Cfg())

    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(staff)
    actions = ma.get_actions(req)

    assert not any(k.startswith("export_") and k != "export_csv" for k in actions)


@pytest.mark.django_db
def test_get_actions_superuser_does_not_get_profile_actions(monkeypatch):
    """Superusers see only the built-in export_csv action — no per-profile actions.

    get_actions() has no queryset context, so there's no safe way to know which
    school's config applies. Enumerating all schools on every page load is expensive
    and produces a polluted dropdown.
    """
    SchoolFactory.create(display_name="School A")
    SchoolFactory.create(display_name="School B")
    su = UserFactory.create(is_superuser=True, is_staff=True)

    def _cfg(slug):
        class _C:
            raw = {
                "exports": {
                    "brightwheel": {
                        "field_map": {"first_name": {"source": "student_first_name"}}
                    }
                }
            }
        return _C()

    monkeypatch.setattr("core.admin.submissions.load_school_config", _cfg)

    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(su)
    actions = ma.get_actions(req)

    # Superuser early-returns before profile injection — no profile actions
    profile_actions = {k: v for k, v in actions.items() if k.startswith("export_") and k != "export_csv"}
    assert len(profile_actions) == 0


@pytest.mark.django_db
def test_get_actions_collision_disambiguation(monkeypatch):
    """Two profiles that slugify to the same name must produce distinct action names."""
    school = SchoolFactory.create(plan="starter")
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    class _Cfg:
        raw = {
            "exports": {
                # Both slugify to "brightwheel_csv" → should produce export_brightwheel_csv_csv
                # and export_brightwheel_csv_csv_2
                "Brightwheel CSV": {
                    "field_map": {"a": {"source": "x"}}
                },
                "brightwheel_csv": {
                    "field_map": {"b": {"source": "y"}}
                },
            }
        }

    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: _Cfg())

    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(staff)
    actions = ma.get_actions(req)

    profile_actions = [k for k in actions if k.startswith("export_") and k != "export_csv"]
    # Two profiles → two distinct action names
    assert len(profile_actions) == 2
    assert len(set(profile_actions)) == 2


@pytest.mark.django_db
def test_get_actions_distinct_labels_for_similarly_named_profiles(monkeypatch):
    """Two profiles with similar names must produce visibly distinct labels.

    Action name collision is already handled (export_x_csv / export_x_csv_2),
    but the visible label must also let the admin tell them apart.
    Labels use the raw profile_name from YAML, so "Brightwheel" vs "brightwheel_v2"
    produce unambiguous labels even if their slugs are close.
    """
    school = SchoolFactory.create(plan="starter")
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    class _Cfg:
        raw = {
            "exports": {
                "brightwheel": {
                    "field_map": {"a": {"source": "x"}}
                },
                "brightwheel v2": {
                    "field_map": {"b": {"source": "y"}}
                },
            }
        }

    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: _Cfg())

    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(staff)
    actions = ma.get_actions(req)

    profile_actions = {k: v for k, v in actions.items() if k.startswith("export_") and k != "export_csv"}
    assert len(profile_actions) == 2

    labels = [v[2] for v in profile_actions.values()]
    # Labels must be distinct — admin can tell them apart at a glance
    assert len(set(labels)) == 2
    assert any("brightwheel" in lbl and "v2" not in lbl for lbl in labels)
    assert any("brightwheel v2" in lbl for lbl in labels)


# ── Admin action: _do_integration_export ────────────────────────────────────


@pytest.mark.django_db
def test_integration_export_correct_headers_and_values(monkeypatch):
    school = SchoolFactory.create(plan="starter", feature_flags={"audit_log_enabled": False})
    sub = SubmissionFactory.create(
        school=school,
        data={"student_first_name": "Alice", "student_last_name": "Smith"},
    )
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    field_map = {
        "first_name": {"source": "student_first_name"},
        "last_name": {"source": "student_last_name"},
        "homeroom": {"value": "Dance Academy"},
    }
    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(staff)
    qs = Submission.objects.filter(id=sub.id)

    resp = ma._do_integration_export(
        req, qs, "brightwheel", "export_brightwheel_csv", field_map, expected_school_id=school.id
    )
    assert resp is not None
    assert resp.status_code == 200

    content = resp.content.decode()
    reader = list(csv.reader(io.StringIO(content)))
    assert reader[0] == ["first_name", "last_name", "homeroom"]
    assert reader[1] == ["Alice", "Smith", "Dance Academy"]


@pytest.mark.django_db
def test_integration_export_literal_value_in_output(monkeypatch):
    school = SchoolFactory.create(plan="starter", feature_flags={"audit_log_enabled": False})
    sub = SubmissionFactory.create(school=school, data={})
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    field_map = {"status": {"value": "Applied"}}
    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(staff)
    qs = Submission.objects.filter(id=sub.id)

    resp = ma._do_integration_export(
        req, qs, "test", "export_test_csv", field_map, expected_school_id=school.id
    )
    content = resp.content.decode()
    reader = list(csv.reader(io.StringIO(content)))
    assert reader[1][0] == "Applied"


@pytest.mark.django_db
def test_integration_export_filename_uses_profile_name(monkeypatch):
    """Filename is derived from profile_name, not the internal action __name__."""
    school = SchoolFactory.create(plan="starter", feature_flags={"audit_log_enabled": False})
    sub = SubmissionFactory.create(school=school, data={})
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    field_map = {"col": {"value": "x"}}
    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(staff)
    qs = Submission.objects.filter(id=sub.id)

    resp = ma._do_integration_export(
        req, qs, "my profile", "export_my_profile_csv", field_map, expected_school_id=school.id
    )
    # slugify("my profile") → "my_profile" → filename "my_profile_export.csv"
    assert "my_profile_export.csv" in resp["Content-Disposition"]
    assert "export_my_profile_csv.csv" not in resp["Content-Disposition"]


@pytest.mark.django_db
def test_integration_export_blocks_multi_school_queryset():
    school_a = SchoolFactory.create(plan="starter")
    school_b = SchoolFactory.create(plan="starter")
    sub_a = SubmissionFactory.create(school=school_a, data={})
    sub_b = SubmissionFactory.create(school=school_b, data={})
    su = UserFactory.create(is_superuser=True, is_staff=True)

    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(su)
    qs = Submission.objects.filter(id__in=[sub_a.id, sub_b.id])

    result = ma._do_integration_export(
        req, qs, "brightwheel", "export_brightwheel_csv", {"col": {"value": "x"}},
        expected_school_id=school_a.id
    )
    assert result is None
    msgs = _messages_list(req)
    assert any("multiple schools" in m for m in msgs)


@pytest.mark.django_db
def test_integration_export_rejects_wrong_expected_school_id():
    """Superuser action registered for School A must block School B's submissions."""
    school_a = SchoolFactory.create(plan="starter")
    school_b = SchoolFactory.create(plan="starter")
    sub_b = SubmissionFactory.create(school=school_b, data={})
    su = UserFactory.create(is_superuser=True, is_staff=True)

    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(su)
    qs = Submission.objects.filter(id=sub_b.id)  # School B's submission

    result = ma._do_integration_export(
        req, qs, "brightwheel", "export_brightwheel_csv", {"col": {"value": "x"}},
        expected_school_id=school_a.id  # Action was registered for School A
    )
    assert result is None
    msgs = _messages_list(req)
    assert any("not belong to the school" in m for m in msgs)


@pytest.mark.django_db
def test_integration_export_empty_queryset_returns_none_with_message():
    school = SchoolFactory.create(plan="starter")
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    req = _make_request(staff)
    qs = Submission.objects.none()

    result = ma._do_integration_export(
        req, qs, "brightwheel", "export_brightwheel_csv", {"col": {"value": "x"}},
        expected_school_id=school.id
    )
    assert result is None
    msgs = _messages_list(req)
    assert any("No submissions selected" in m for m in msgs)
