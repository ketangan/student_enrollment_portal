# core/services/feature_flags.py
from __future__ import annotations
from typing import Any


PLAN_TRIAL = "trial"
PLAN_STARTER = "starter"
PLAN_PRO = "pro"
PLAN_GROWTH = "growth"

DEFAULT_FLAGS_BY_PLAN: dict[str, dict[str, bool]] = {
    PLAN_TRIAL: {
        "reports_enabled": False,
        "status_enabled": True,
        "csv_export_enabled": True,
        "audit_log_enabled": True,
    },
    PLAN_STARTER: {
        "reports_enabled": True,
        "status_enabled": True,
        "csv_export_enabled": True,
        "audit_log_enabled": True,
    },
    PLAN_PRO: {
        "reports_enabled": True,
        "status_enabled": True,
        "csv_export_enabled": True,
        "audit_log_enabled": True,
    },
    PLAN_GROWTH: {
        "reports_enabled": True,
        "status_enabled": True,
        "csv_export_enabled": True,
        "audit_log_enabled": True,
    },
}

def default_flags_for_plan(plan: str) -> dict[str, bool]:
    return dict(DEFAULT_FLAGS_BY_PLAN.get(plan or PLAN_TRIAL, DEFAULT_FLAGS_BY_PLAN[PLAN_TRIAL]))

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
    flags = merge_flags(plan=getattr(school, "plan", PLAN_TRIAL), overrides=getattr(school, "feature_flags", None))
    return bool(flags.get(key, default))
