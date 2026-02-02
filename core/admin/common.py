# core/admin/common.py
from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.contrib.auth.models import Group

from core.services.config_loader import get_forms, load_school_config


# ----------------------------
# Admin UI simplification
# ----------------------------
admin.site.site_url = None

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


# ----------------------------
# Helpers / constants
# ----------------------------
DYN_PREFIX = "dyn__"


def _is_superuser(user) -> bool:
    return bool(user and user.is_active and user.is_superuser)


def _membership_school_id(user):
    m = getattr(user, "school_membership", None)
    return getattr(m, "school_id", None) if m else None


def _has_school_membership(user) -> bool:
    return _membership_school_id(user) is not None


def _bytes_to_mb(size: int) -> str:
    try:
        b = int(size or 0)
    except Exception:
        b = 0

    if b <= 0:
        return ""

    kb = b / 1024
    if kb < 1024:
        return f"{kb:.0f} KB"

    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"

    gb = mb / 1024
    return f"{gb:.1f} GB"


def _build_field_label_map(school_slug: str) -> dict[str, str]:
    cfg = load_school_config(school_slug)
    if not cfg:
        return {}
    label_map: dict[str, str] = {}
    for section in cfg.form.get("sections", []):
        for field in section.get("fields", []):
            key = field.get("key")
            label = field.get("label")
            if key and label:
                label_map[str(key)] = str(label)
    return label_map


def _dyn_key(key: str) -> str:
    return f"{DYN_PREFIX}{key}"


def _orig_key(dyn_key: str) -> str:
    return dyn_key[len(DYN_PREFIX):] if dyn_key.startswith(DYN_PREFIX) else dyn_key


def _resolve_submission_form_cfg_and_labels(
    cfg: Any,
    submission_form_key: str | None,
) -> tuple[dict, dict[str, str]]:
    """
    Resolve the YAML form config and label map for a submission in admin.

    Rules:
    - If the school is single-form (no `forms` dict), use `cfg.form`.
    - If multi-form:
        - submission_form_key matches a real key => that form only
        - submission_form_key == "multi" => all forms combined (stable YAML order)
        - otherwise => cfg.form (which is already a safe multi-form default)
    Label map rules:
    - Prefer explicit YAML field.label
    - Fallback: key.replace("_", " ").title()
    """

    if not cfg:
        return {}, {}

    raw = getattr(cfg, "raw", None) or {}
    raw_forms = raw.get("forms")
    is_multi_form_school = isinstance(raw_forms, dict) and bool(raw_forms)

    fk = (submission_form_key or "").strip()

    if not is_multi_form_school:
        form_cfg = getattr(cfg, "form", None) or {}
    else:
        forms = get_forms(cfg) or {}

        if fk and fk != "multi" and fk in forms:
            form_cfg = (forms[fk] or {}).get("form") or {}
        elif fk == "multi":
            combined_sections: list[dict] = []
            for _k, meta in raw_forms.items():
                if not isinstance(meta, dict):
                    continue
                form = meta.get("form")
                if not isinstance(form, dict):
                    continue
                combined_sections.extend(form.get("sections") or [])

            form_cfg = {
                "title": "All Forms",
                "description": "Combined view of all steps",
                "sections": combined_sections,
            }
        else:
            # Safe default (prefers "default" if present, else first YAML form)
            form_cfg = getattr(cfg, "form", None) or {}

    label_map: dict[str, str] = {}
    for section in (form_cfg.get("sections") or []) if isinstance(form_cfg, dict) else []:
        if not isinstance(section, dict):
            continue
        for field in section.get("fields") or []:
            if not isinstance(field, dict):
                continue
            key = field.get("key")
            if not key:
                continue
            key = str(key)
            if key in label_map:
                continue

            label = field.get("label")
            if label:
                label_map[key] = str(label)
            else:
                label_map[key] = key.replace("_", " ").title()

    return form_cfg if isinstance(form_cfg, dict) else {}, label_map
