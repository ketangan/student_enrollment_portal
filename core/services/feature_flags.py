# core/services/feature_flags.py
from __future__ import annotations

from typing import Any


# ── Plan constants (single source of truth) ──────────────────────────────
PLAN_TRIAL = "trial"
PLAN_STARTER = "starter"
PLAN_PRO = "pro"
PLAN_GROWTH = "growth"

PLAN_RANK: dict[str, int] = {
    PLAN_TRIAL: 0,
    PLAN_STARTER: 1,
    PLAN_PRO: 2,
    PLAN_GROWTH: 3,
}

ALL_PLANS = list(PLAN_RANK.keys())

PLAN_CHOICES = [
    (PLAN_TRIAL, "Trial"),
    (PLAN_STARTER, "Starter"),
    (PLAN_PRO, "Pro"),
    (PLAN_GROWTH, "Growth"),
]


# ── Feature flags: minimum plan required ─────────────────────────────────
# Adding a new flag = one line here.  The plan name is the *lowest* tier
# where the feature is enabled by default.
_FEATURE_MIN_PLAN: dict[str, str] = {
    # trial tier (everyone gets these)
    "status_enabled": PLAN_TRIAL,
    "csv_export_enabled": PLAN_TRIAL,
    "audit_log_enabled": PLAN_TRIAL,
    # starter tier
    "reports_enabled": PLAN_STARTER,
    "email_notifications_enabled": PLAN_STARTER,
    "file_uploads_enabled": PLAN_STARTER,
    # pro tier
    "custom_branding_enabled": PLAN_PRO,
    "multi_form_enabled": PLAN_PRO,
    "custom_statuses_enabled": PLAN_PRO,
}

ALL_FLAGS = list(_FEATURE_MIN_PLAN.keys())


def default_flags_for_plan(plan: str) -> dict[str, bool]:
    """Compute default flag values for a plan based on cumulative tier ranks."""
    rank = PLAN_RANK.get(plan or PLAN_TRIAL, PLAN_RANK[PLAN_TRIAL])
    return {
        flag: rank >= PLAN_RANK.get(min_plan, 0)
        for flag, min_plan in _FEATURE_MIN_PLAN.items()
    }


def merge_flags(*, plan: str, overrides: dict[str, Any] | None) -> dict[str, bool]:
    """
    Defaults (by plan) + admin overrides.
    Only accepts boolean overrides; ignores junk to avoid breakage.
    """
    merged = default_flags_for_plan(plan)
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, bool):
                merged[k] = v
    return merged


def is_enabled(school: Any, key: str, default: bool = False) -> bool:
    """Check whether a feature flag is enabled for a school."""
    flags = merge_flags(
        plan=getattr(school, "plan", PLAN_TRIAL),
        overrides=getattr(school, "feature_flags", None),
    )
    return bool(flags.get(key, default))
