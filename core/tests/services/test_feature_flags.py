import pytest

from core.services.feature_flags import (
    PLAN_TRIAL,
    PLAN_STARTER,
    PLAN_PRO,
    PLAN_GROWTH,
    PLAN_RANK,
    PLAN_CHOICES,
    ALL_PLANS,
    ALL_FLAGS,
    _FEATURE_MIN_PLAN,
    default_flags_for_plan,
    merge_flags,
    is_enabled,
)


# ---------------------------------------------------------------------------
# Module-level constants sanity checks
# ---------------------------------------------------------------------------

def test_plan_constants_are_strings():
    for plan in ALL_PLANS:
        assert isinstance(plan, str) and len(plan) > 0


def test_plan_rank_covers_all_plans():
    for plan in ALL_PLANS:
        assert plan in PLAN_RANK


def test_plan_choices_matches_all_plans():
    choice_values = [v for v, _ in PLAN_CHOICES]
    assert choice_values == ALL_PLANS


def test_all_flags_matches_feature_min_plan_keys():
    assert ALL_FLAGS == list(_FEATURE_MIN_PLAN.keys())


def test_feature_min_plan_values_are_valid_plans():
    for flag, min_plan in _FEATURE_MIN_PLAN.items():
        assert min_plan in ALL_PLANS, f"Flag {flag!r} references unknown plan {min_plan!r}"


# ---------------------------------------------------------------------------
# default_flags_for_plan — cumulative tier logic
# ---------------------------------------------------------------------------

def test_trial_gets_only_trial_tier_flags():
    flags = default_flags_for_plan(PLAN_TRIAL)
    # Trial-tier flags are enabled
    assert flags["status_enabled"] is True
    assert flags["csv_export_enabled"] is True
    assert flags["audit_log_enabled"] is True
    # Starter-tier flags are off
    assert flags["reports_enabled"] is False
    assert flags["email_notifications_enabled"] is False
    assert flags["file_uploads_enabled"] is False
    # Pro-tier flags are off
    assert flags["custom_branding_enabled"] is False
    assert flags["multi_form_enabled"] is False
    assert flags["custom_statuses_enabled"] is False


def test_starter_gets_trial_and_starter_tier_flags():
    flags = default_flags_for_plan(PLAN_STARTER)
    # Trial-tier
    assert flags["status_enabled"] is True
    assert flags["csv_export_enabled"] is True
    assert flags["audit_log_enabled"] is True
    # Starter-tier now unlocked
    assert flags["reports_enabled"] is True
    assert flags["email_notifications_enabled"] is True
    assert flags["file_uploads_enabled"] is True
    # Pro-tier still off
    assert flags["custom_branding_enabled"] is False
    assert flags["multi_form_enabled"] is False
    assert flags["custom_statuses_enabled"] is False


def test_pro_gets_trial_starter_and_pro_tier_flags():
    flags = default_flags_for_plan(PLAN_PRO)
    for flag in ALL_FLAGS:
        assert flags[flag] is True, f"Pro plan should enable {flag}"


def test_growth_gets_all_flags():
    flags = default_flags_for_plan(PLAN_GROWTH)
    for flag in ALL_FLAGS:
        assert flags[flag] is True, f"Growth plan should enable {flag}"


def test_cumulative_tiers_are_monotonically_increasing():
    """Higher plans should always have a superset of lower-plan features."""
    ordered = [PLAN_TRIAL, PLAN_STARTER, PLAN_PRO, PLAN_GROWTH]
    for i in range(len(ordered) - 1):
        lower = default_flags_for_plan(ordered[i])
        higher = default_flags_for_plan(ordered[i + 1])
        for flag, enabled in lower.items():
            if enabled:
                assert higher[flag] is True, (
                    f"{ordered[i+1]} should have {flag}=True since {ordered[i]} has it"
                )


def test_unknown_plan_falls_back_to_trial():
    flags = default_flags_for_plan("unknown-plan")
    assert flags == default_flags_for_plan(PLAN_TRIAL)


def test_empty_string_plan_falls_back_to_trial():
    flags = default_flags_for_plan("")
    assert flags == default_flags_for_plan(PLAN_TRIAL)


def test_none_plan_falls_back_to_trial():
    flags = default_flags_for_plan(None)
    assert flags == default_flags_for_plan(PLAN_TRIAL)


def test_default_flags_returns_fresh_dict():
    """Mutating the returned dict must not corrupt future calls."""
    a = default_flags_for_plan(PLAN_TRIAL)
    a["reports_enabled"] = True
    b = default_flags_for_plan(PLAN_TRIAL)
    assert b["reports_enabled"] is False


def test_default_flags_contains_all_known_flags():
    for plan in ALL_PLANS:
        flags = default_flags_for_plan(plan)
        for flag in ALL_FLAGS:
            assert flag in flags, f"Missing {flag} in {plan} defaults"


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
        "reports_enabled": "yes",
        "some_flag": 1,
        "another": None,
    })
    assert result["reports_enabled"] is False
    assert "some_flag" not in result
    assert "another" not in result


def test_merge_flags_adds_new_boolean_keys():
    result = merge_flags(plan=PLAN_TRIAL, overrides={"custom_feature": True})
    assert result["custom_feature"] is True
    assert "reports_enabled" in result


def test_merge_flags_mixed_overrides_only_bools_survive():
    result = merge_flags(plan=PLAN_GROWTH, overrides={
        "reports_enabled": False,
        "bad_flag": "nope",
        "new_flag": True,
    })
    assert result["reports_enabled"] is False
    assert result["new_flag"] is True
    assert "bad_flag" not in result


def test_merge_flags_override_promotes_trial_to_pro_feature():
    """Per-school override can enable a pro feature on a trial school."""
    result = merge_flags(plan=PLAN_TRIAL, overrides={"custom_branding_enabled": True})
    assert result["custom_branding_enabled"] is True


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
    obj = type("Obj", (), {"feature_flags": {}})()
    assert is_enabled(obj, "reports_enabled") is False


def test_is_enabled_handles_missing_feature_flags_attr():
    obj = type("Obj", (), {"plan": PLAN_STARTER})()
    assert is_enabled(obj, "reports_enabled") is True


# ---------------------------------------------------------------------------
# is_enabled: parametrized across all flags and plans
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag,min_plan", list(_FEATURE_MIN_PLAN.items()))
def test_is_enabled_flag_matches_cumulative_tier(flag, min_plan):
    """Each flag should be disabled below its min_plan and enabled at/above."""
    min_rank = PLAN_RANK[min_plan]
    for plan, rank in PLAN_RANK.items():
        school = _FakeSchool(plan=plan)
        expected = rank >= min_rank
        assert is_enabled(school, flag) is expected, (
            f"{flag} on {plan} (rank {rank}) should be {expected}"
        )


@pytest.mark.parametrize("flag", ALL_FLAGS)
def test_is_enabled_override_can_flip_any_flag(flag):
    """Per-school overrides can enable/disable any flag regardless of plan."""
    school_on = _FakeSchool(plan=PLAN_TRIAL, feature_flags={flag: True})
    assert is_enabled(school_on, flag) is True

    school_off = _FakeSchool(plan=PLAN_GROWTH, feature_flags={flag: False})
    assert is_enabled(school_off, flag) is False
