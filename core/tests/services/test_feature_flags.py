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

def test_trial_gets_all_flags():
    """Trial is full-featured — every flag must be True."""
    flags = default_flags_for_plan(PLAN_TRIAL)
    for flag in ALL_FLAGS:
        assert flags[flag] is True, f"Trial plan should enable {flag}"


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
    pro_and_below = {f for f, plan in _FEATURE_MIN_PLAN.items() if PLAN_RANK[plan] <= PLAN_RANK[PLAN_PRO]}
    growth_only = {f for f, plan in _FEATURE_MIN_PLAN.items() if PLAN_RANK[plan] > PLAN_RANK[PLAN_PRO]}
    for flag in pro_and_below:
        assert flags[flag] is True, f"Pro plan should enable {flag}"
    for flag in growth_only:
        assert flags[flag] is False, f"Pro plan should NOT enable growth-only flag {flag}"


def test_growth_gets_all_flags():
    flags = default_flags_for_plan(PLAN_GROWTH)
    for flag in ALL_FLAGS:
        assert flags[flag] is True, f"Growth plan should enable {flag}"


def test_cumulative_tiers_are_monotonically_increasing():
    """Higher paid plans should always have a superset of lower-tier features.

    Trial is excluded: it is a special full-featured tier, not part of the
    rank-based progression (starter < pro < growth).
    """
    ordered = [PLAN_STARTER, PLAN_PRO, PLAN_GROWTH]
    for i in range(len(ordered) - 1):
        lower = default_flags_for_plan(ordered[i])
        higher = default_flags_for_plan(ordered[i + 1])
        for flag, enabled in lower.items():
            if enabled:
                assert higher[flag] is True, (
                    f"{ordered[i+1]} should have {flag}=True since {ordered[i]} has it"
                )


def test_unknown_plan_falls_back_to_rank_zero():
    """Unrecognised plan strings fall back to rank-0 (trial-tier subset only).

    Note: this is NOT the same as PLAN_TRIAL, which returns all-True.
    """
    flags = default_flags_for_plan("unknown-plan")
    # Rank-0 flags (trial-tier) are enabled
    assert flags["status_enabled"] is True
    assert flags["csv_export_enabled"] is True
    # Starter-tier and above are off
    assert flags["reports_enabled"] is False
    assert flags["custom_branding_enabled"] is False
    # Different from PLAN_TRIAL (which is all-True)
    assert flags != default_flags_for_plan(PLAN_TRIAL)


def test_empty_string_plan_falls_back_to_trial():
    flags = default_flags_for_plan("")
    assert flags == default_flags_for_plan(PLAN_TRIAL)


def test_none_plan_falls_back_to_trial():
    flags = default_flags_for_plan(None)
    assert flags == default_flags_for_plan(PLAN_TRIAL)


def test_default_flags_returns_fresh_dict():
    """Mutating the returned dict must not corrupt future calls."""
    a = default_flags_for_plan(PLAN_STARTER)
    a["custom_branding_enabled"] = True  # Pro-only; starter has it False
    b = default_flags_for_plan(PLAN_STARTER)
    assert b["custom_branding_enabled"] is False


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
        "reports_enabled": "yes",  # string — ignored; trial default (True) survives
        "some_flag": 1,
        "another": None,
    })
    assert result["reports_enabled"] is True  # trial default, not the string override
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


def test_merge_flags_override_can_disable_trial_feature():
    """Boolean override can disable a feature even though trial defaults it to True."""
    result = merge_flags(plan=PLAN_TRIAL, overrides={"custom_branding_enabled": False})
    assert result["custom_branding_enabled"] is False

