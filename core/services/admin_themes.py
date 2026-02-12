# core/services/admin_themes.py
"""
Admin theme registry.

Adding a new theme = one entry in ADMIN_THEMES.
Each theme defines a label, icon, description, and a set of Jazzmin UI tweaks.
The tweaks are merged on top of _JAZZMIN_UI_DEFAULTS so every theme is a
complete, self-contained config — no partial overrides to debug.
"""
from __future__ import annotations

from typing import Any


# ── Jazzmin UI tweaks baseline ───────────────────────────────────────────
# Mirrors jazzmin's own defaults so we never depend on upstream internals.
_JAZZMIN_UI_DEFAULTS: dict[str, Any] = {
    "navbar_small_text": False,
    "footer_small_text": False,
    "body_small_text": False,
    "brand_small_text": False,
    "brand_colour": False,
    "accent": "accent-primary",
    "navbar": "navbar-white navbar-light",
    "no_navbar_border": False,
    "navbar_fixed": False,
    "layout_boxed": False,
    "footer_fixed": False,
    "sidebar_fixed": False,
    "sidebar": "sidebar-dark-primary",
    "sidebar_nav_small_text": False,
    "sidebar_disable_expand": False,
    "sidebar_nav_child_indent": False,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_flat_style": False,
    "theme": "default",
    "dark_mode_theme": None,
    "button_classes": {
        "primary": "btn-outline-primary",
        "secondary": "btn-outline-secondary",
        "info": "btn-outline-info",
        "warning": "btn-outline-warning",
        "danger": "btn-outline-danger",
        "success": "btn-outline-success",
    },
    "actions_sticky_top": False,
}


DEFAULT_THEME_KEY = "midnight"


# ── Theme registry ───────────────────────────────────────────────────────
# To add a 4th theme: add one entry here, done.
# "ui_tweaks" keys override _JAZZMIN_UI_DEFAULTS.

ADMIN_THEMES: dict[str, dict[str, Any]] = {
    "midnight": {
        "label": "Midnight",
        "icon": "fas fa-moon",
        "description": "Dark, high-contrast — inspired by GitHub & Linear",
        "ui_tweaks": {
            "theme": "darkly",
            "dark_mode_theme": "darkly",
            "navbar": "navbar-dark",
            "no_navbar_border": True,
            "navbar_fixed": True,
            "sidebar": "sidebar-dark-primary",
            "sidebar_fixed": True,
            "sidebar_nav_child_indent": True,
            "accent": "accent-primary",
        },
    },
    "clean": {
        "label": "Clean",
        "icon": "fas fa-sun",
        "description": "Light & minimal — inspired by Stripe & Shopify",
        "ui_tweaks": {
            "theme": "flatly",
            "dark_mode_theme": None,
            "navbar": "navbar-white navbar-light",
            "no_navbar_border": False,
            "navbar_fixed": True,
            "sidebar": "sidebar-light-primary",
            "sidebar_fixed": True,
            "sidebar_nav_child_indent": True,
            "accent": "accent-primary",
        },
    },
    "minty": {
        "label": "Minty",
        "icon": "fas fa-leaf",
        "description": "Fresh mint-green — clean & modern",
        "ui_tweaks": {
            "theme": "minty",
            "dark_mode_theme": None,
            "navbar": "navbar-light navbar-white",
            "no_navbar_border": False,
            "navbar_fixed": True,
            "sidebar": "sidebar-light-success",
            "sidebar_fixed": True,
            "sidebar_nav_child_indent": True,
            "accent": "accent-success",
        },
    },
}


THEME_CHOICES = [(key, cfg["label"]) for key, cfg in ADMIN_THEMES.items()]


def get_theme_ui_tweaks(theme_key: str) -> dict[str, Any]:
    """Return a complete Jazzmin UI tweaks dict for *theme_key*.

    Unknown keys fall back to DEFAULT_THEME_KEY so the admin never breaks.
    """
    cfg = ADMIN_THEMES.get(theme_key or DEFAULT_THEME_KEY)
    if cfg is None:
        cfg = ADMIN_THEMES[DEFAULT_THEME_KEY]

    tweaks: dict[str, Any] = _JAZZMIN_UI_DEFAULTS.copy()
    # Deep-copy button_classes so per-theme overrides don't mutate the default
    tweaks["button_classes"] = _JAZZMIN_UI_DEFAULTS["button_classes"].copy()
    tweaks.update(cfg["ui_tweaks"])
    return tweaks


def get_themes_for_api() -> list[dict[str, str]]:
    """Serialisable list of themes for the JS theme picker."""
    return [
        {
            "key": key,
            "label": cfg["label"],
            "icon": cfg["icon"],
            "description": cfg["description"],
        }
        for key, cfg in ADMIN_THEMES.items()
    ]
