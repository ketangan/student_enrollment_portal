import pytest

from core.services.feature_flags import (
    PLAN_TRIAL,
    PLAN_STARTER,
    PLAN_PRO,
    PLAN_GROWTH,
    DEFAULT_FLAGS_BY_PLAN,
    default_flags_for_plan,
    merge_flags,
    is_enabled,
)


# ---------------------------------------------------------------------------
# default_flags_for_plan
# ---------------------------------------------------------------------------

def test_default_flags_for_trial():
    flags = default_flags_for_plan(PLAN_TRIAL)
    assert flags == {
        "reports_enabled": False,
        "status_enabled": True,
        "csv_export_enabled": True,
        "audit_log_enabled": True,
    }


def test_default_flags_for_starter():
    flags = default_flags_for_plan(PLAN_STARTER)
    assert flags["reports_enabled"] is True


def test_default_flags_for_pro():
    flags = default_flags_for_plan(PLAN_PRO)
    assert flags["reports_enabled"] is True


def test_default_flags_for_growth():
    flags = default_flags_for_plan(PLAN_GROWTH)
    assert flags["reports_enabled"] is True


def test_default_flags_for_unknown_plan_falls_back_to_trial():
    flags = default_flags_for_plan("unknown-plan")
    assert flags == default_flags_for_plan(PLAN_TRIAL)


def test_default_flags_for_empty_string_falls_back_to_trial():
    """Empty string plan (e.g. blank CharField) should resolve to trial defaults."""
    flags = default_flags_for_plan("")
    assert flags == default_flags_for_plan(PLAN_TRIAL)


def test_default_flags_for_none_falls_back_to_trial():
    flags = default_flags_for_plan(None)
    assert flags == default_flags_for_plan(PLAN_TRIAL)


def test_default_flags_returns_copy_not_reference():
    """Mutating the returned dict must not corrupt the canonical defaults."""
    a = default_flags_for_plan(PLAN_TRIAL)
    a["reports_enabled"] = True
    b = default_flags_for_plan(PLAN_TRIAL)
    assert b["reports_enabled"] is False


# ---------------------------------------------------------------------------
# merge_flags
# ---------------------------------------------------------------------------

def test_merge_flags_no_overrides_returns_plan_defaults():
    result = merge_flags(plan=PLAN_STARTER, overrides=None)
    assert result == default_flags_for_plan(PLAN_STARTER)


def test_merge_flags_empty_overrides_returns_plan_defaults():
    result = merge_flags(plan=PLAN_PRO, overrides={})
    assert result == default_flags_for_plan(PLAN_PRO)


def test_merge_flags_boolean_override_applied():
    result = merge_flags(plan=PLAN_TRIAL, overrides={"reports_enabled": True})
    assert result["reports_enabled"] is True


def test_merge_flags_override_can_disable_plan_default():
    result = merge_flags(plan=PLAN_STARTER, overrides={"reports_enabled": False})
    assert result["reports_enabled"] is False


def test_merge_flags_ignores_non_boolean_overrides():
    result = merge_flags(plan=PLAN_TRIAL, overrides={
        "reports_enabled": "yes",  # string, not bool
        "some_flag": 1,            # int, not bool
        "another": None,           # None, not bool
    })
    # Should keep plan default (False for trial), non-bool overrides discarded
    assert result["reports_enabled"] is False
    assert "some_flag" not in result
    assert "another" not in result


def test_merge_flags_adds_new_boolean_keys():
    result = merge_flags(plan=PLAN_TRIAL, overrides={"custom_feature": True})
    assert result["custom_feature"] is True
    # plan defaults still present
    assert "reports_enabled" in result


def test_merge_flags_mixed_overrides_only_bools_survive():
    result = merge_flags(plan=PLAN_GROWTH, overrides={
        "reports_enabled": False,   # valid bool
        "bad_flag": "nope",         # non-bool ignored
        "new_flag": True,           # valid bool
    })
    assert result["reports_enabled"] is False
    assert result["new_flag"] is True
    assert "bad_flag" not in result


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------

class _FakeSchool:
    """Minimal stand-in for School to test is_enabled without the DB."""
    def __init__(self, plan="trial", feature_flags=None):
        self.plan = plan
        self.feature_flags = feature_flags or {}


def test_is_enabled_trial_reports_disabled_by_default():
    school = _FakeSchool(plan=PLAN_TRIAL)
    assert is_enabled(school, "reports_enabled") is False


def test_is_enabled_starter_reports_enabled_by_default():
    school = _FakeSchool(plan=PLAN_STARTER)
    assert is_enabled(school, "reports_enabled") is True


def test_is_enabled_respects_override():
    school = _FakeSchool(plan=PLAN_TRIAL, feature_flags={"reports_enabled": True})
    assert is_enabled(school, "reports_enabled") is True


def test_is_enabled_override_can_disable():
    school = _FakeSchool(plan=PLAN_PRO, feature_flags={"reports_enabled": False})
    assert is_enabled(school, "reports_enabled") is False


def test_is_enabled_unknown_key_uses_default_false():
    school = _FakeSchool(plan=PLAN_STARTER)
    assert is_enabled(school, "nonexistent_flag") is False


def test_is_enabled_unknown_key_uses_provided_default():
    school = _FakeSchool(plan=PLAN_STARTER)
    assert is_enabled(school, "nonexistent_flag", default=True) is True


def test_is_enabled_handles_missing_plan_attr():
    """Object without a plan attribute should fall back to trial."""
    obj = type("Obj", (), {"feature_flags": {}})()
    assert is_enabled(obj, "reports_enabled") is False


def test_is_enabled_handles_missing_feature_flags_attr():
    """Object without feature_flags attr should still work."""
    obj = type("Obj", (), {"plan": PLAN_STARTER})()
    assert is_enabled(obj, "reports_enabled") is True


# ---------------------------------------------------------------------------
# Verify DEFAULT_FLAGS_BY_PLAN covers all plans
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# New flags: status_enabled, csv_export_enabled, audit_log_enabled
# ---------------------------------------------------------------------------

def test_all_plans_include_status_csv_and_audit_flags():
    for plan in (PLAN_TRIAL, PLAN_STARTER, PLAN_PRO, PLAN_GROWTH):
        flags = default_flags_for_plan(plan)
        assert "status_enabled" in flags
        assert "csv_export_enabled" in flags
        assert "audit_log_enabled" in flags


def test_status_enabled_true_for_all_plans():
    for plan in (PLAN_TRIAL, PLAN_STARTER, PLAN_PRO, PLAN_GROWTH):
        assert default_flags_for_plan(plan)["status_enabled"] is True


def test_csv_export_enabled_true_for_all_plans():
    for plan in (PLAN_TRIAL, PLAN_STARTER, PLAN_PRO, PLAN_GROWTH):
        assert default_flags_for_plan(plan)["csv_export_enabled"] is True


def test_audit_log_enabled_true_for_all_plans():
    for plan in (PLAN_TRIAL, PLAN_STARTER, PLAN_PRO, PLAN_GROWTH):
        assert default_flags_for_plan(plan)["audit_log_enabled"] is True


def test_is_enabled_status_respects_override():
    school = _FakeSchool(plan=PLAN_STARTER, feature_flags={"status_enabled": False})
    assert is_enabled(school, "status_enabled") is False


def test_is_enabled_csv_export_respects_override():
    school = _FakeSchool(plan=PLAN_TRIAL, feature_flags={"csv_export_enabled": False})
    assert is_enabled(school, "csv_export_enabled") is False


def test_is_enabled_audit_log_respects_override():
    school = _FakeSchool(plan=PLAN_PRO, feature_flags={"audit_log_enabled": False})
    assert is_enabled(school, "audit_log_enabled") is False


# ---------------------------------------------------------------------------
# Verify DEFAULT_FLAGS_BY_PLAN covers all plans
# ---------------------------------------------------------------------------

def test_all_plans_defined_in_defaults():
    for plan in (PLAN_TRIAL, PLAN_STARTER, PLAN_PRO, PLAN_GROWTH):
        assert plan in DEFAULT_FLAGS_BY_PLAN
