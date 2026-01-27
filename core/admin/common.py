# core/admin/common.py
from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.models import Group

from core.services.config_loader import load_school_config


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
