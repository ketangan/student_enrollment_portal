# core/services/capacity.py
"""
Enrollment capacity limits — YAML-gated, per-program soft caps.

Activated by adding a `capacity:` block to the school's YAML config:

    capacity:
      waitlist_message: "Our primary enrollment is full — you've been added to our waitlist."
      excluded_statuses:          # statuses that don't occupy a spot (default: Declined, Archived)
        - Declined
        - Archived
      programs:                   # program field VALUE → integer max
        ballet: 15
        jazz: 20

No feature flag — presence of the `capacity.programs` block activates the feature.
Capacity is a soft cap: submissions are always accepted; families see a waitlist
message on the success page when their program is at or over the limit.
"""
from __future__ import annotations

from typing import Optional

from django.db.models import Q

from core.services.config_loader import PROGRAM_FIELD_KEYS

_DEFAULT_EXCLUDED_STATUSES = ["Declined", "Archived"]
_DEFAULT_WAITLIST_MESSAGE = (
    "Our primary enrollment is currently full. "
    "You've been added to our waitlist and we'll be in touch."
)


# ── Config parsing ─────────────────────────────────────────────────────────────


def get_capacity_config(config_raw: dict) -> Optional[dict]:
    """
    Return the parsed capacity config or None if not configured.
    Requires `capacity.programs` to be a non-empty dict of {program_value: int}.
    """
    cap = (config_raw or {}).get("capacity")
    if not isinstance(cap, dict):
        return None
    programs = cap.get("programs")
    if not isinstance(programs, dict) or not programs:
        return None
    return cap


def get_excluded_statuses(capacity_cfg: dict) -> list[str]:
    excl = capacity_cfg.get("excluded_statuses")
    if isinstance(excl, list):
        return [str(s) for s in excl if str(s).strip()]
    return list(_DEFAULT_EXCLUDED_STATUSES)


def get_waitlist_message(capacity_cfg: dict) -> str:
    msg = (capacity_cfg.get("waitlist_message") or "").strip()
    return msg or _DEFAULT_WAITLIST_MESSAGE


# ── Program field resolution ───────────────────────────────────────────────────


def get_program_field_key(config_raw: dict) -> Optional[str]:
    """
    Return the form field key used for program selection, or None.
    Mirrors the heuristic in config_loader.get_program_options().
    """
    raw = config_raw or {}
    explicit_key = (raw.get("leads") or {}).get("program_field_key")

    sections: list[dict] = []
    if isinstance(raw.get("form"), dict):
        sections = raw["form"].get("sections") or []
    elif isinstance(raw.get("forms"), dict):
        for form_data in raw["forms"].values():
            if isinstance(form_data, dict) and isinstance(form_data.get("form"), dict):
                sections.extend(form_data["form"].get("sections") or [])

    for section in sections:
        for field in (section.get("fields") or []):
            key = field.get("key", "")
            ftype = (field.get("type") or "").strip().lower()
            if ftype != "select":
                continue
            if explicit_key and key == explicit_key:
                return key
            if not explicit_key and key in PROGRAM_FIELD_KEYS:
                return key
    return None


def get_program_value(submission_data: dict, field_key: Optional[str]) -> str:
    """Return the program field value from submission data, or ''."""
    if not field_key:
        for k in PROGRAM_FIELD_KEYS:
            val = (submission_data or {}).get(k)
            if val:
                return str(val).strip()
        return ""
    return str((submission_data or {}).get(field_key, "")).strip()


# ── Counting ───────────────────────────────────────────────────────────────────


def count_active_submissions(school, program_value: str, field_key: Optional[str], excluded_statuses: list[str]) -> int:
    """
    Count submissions for `school` that occupy a capacity slot:
      - Belong to the given program (if field_key + program_value are known)
      - Are NOT in excluded_statuses
    """
    from core.models import Submission

    qs = Submission.objects.filter(school=school)
    if excluded_statuses:
        qs = qs.exclude(status__in=excluded_statuses)

    if program_value and field_key:
        qs = qs.filter(**{f"data__{field_key}": program_value})
    elif program_value:
        # No explicit key — try all PROGRAM_FIELD_KEYS
        q = Q()
        for k in PROGRAM_FIELD_KEYS:
            q |= Q(**{f"data__{k}": program_value})
        qs = qs.filter(q)

    return qs.count()


# ── Public API ─────────────────────────────────────────────────────────────────


def check_waitlist(school, submission_data: dict, config_raw: dict) -> bool:
    """
    Return True if the program chosen in submission_data is at or over its
    configured capacity limit (checked AFTER the submission has been saved).
    Returns False if capacity is not configured or program is not recognised.
    """
    cap_cfg = get_capacity_config(config_raw)
    if not cap_cfg:
        return False

    programs = cap_cfg["programs"]
    field_key = get_program_field_key(config_raw)
    program_value = get_program_value(submission_data, field_key)
    if not program_value:
        return False

    max_cap = programs.get(program_value)
    if not isinstance(max_cap, int) or max_cap <= 0:
        return False

    excluded = get_excluded_statuses(cap_cfg)
    current = count_active_submissions(school, program_value, field_key, excluded)
    return current >= max_cap


def get_capacity_summary(school, config_raw: dict) -> dict[str, dict]:
    """
    Return a dict of {program_value: {max, current, at_capacity, near_capacity}}
    for every program that has a configured capacity limit.
    Returns {} if capacity is not configured.

    `near_capacity` is True when current >= 80% of max.
    Used by the admin submissions list to show capacity badges.
    """
    cap_cfg = get_capacity_config(config_raw)
    if not cap_cfg:
        return {}

    programs = cap_cfg["programs"]
    excluded = get_excluded_statuses(cap_cfg)
    field_key = get_program_field_key(config_raw)

    result: dict[str, dict] = {}
    for program_value, max_cap in programs.items():
        if not isinstance(max_cap, int) or max_cap <= 0:
            continue
        pv = str(program_value)
        current = count_active_submissions(school, pv, field_key, excluded)
        result[pv] = {
            "max": max_cap,
            "current": current,
            "at_capacity": current >= max_cap,
            "near_capacity": current >= int(max_cap * 0.8),
        }
    return result
