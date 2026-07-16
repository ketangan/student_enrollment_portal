from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from django.conf import settings

DEFAULT_THEME = {
    # existing
    "primary_color": "#111827",  # slate-ish
    "accent_color": "#2563EB",   # blue-ish

    # Phase 9: used by CSS variables injection (safe defaults)
    "background": "#f7f7fb",
    "card": "#ffffff",
    "text": "#111827",
    "muted": "#6b7280",
    "border": "#e5e7eb",
    "radius": "16px",
}

DEFAULT_BRANDING = {
    "logo_url": "",
    "theme": DEFAULT_THEME,
    # Phase 9: optional per-school overrides
    "custom_css": "",
    "custom_js": "",
}


def prettify_school_name_from_slug(slug: str) -> str:
    # "kimberlas-classical-ballet" -> "Kimberlas Classical Ballet"
    return " ".join([p.capitalize() for p in slug.replace("_", "-").split("-") if p])


def get_forms(config) -> dict:
    """
    Returns a dict like:
    {
      "enrollment": {"title": "...", "description": "...", "form": {...}},
      ...
    }
    """
    raw = getattr(config, "raw", None) or {}
    forms = raw.get("forms")
    if isinstance(forms, dict) and forms:
        return forms

    # Back-compat: single-form YAML
    single_form = getattr(config, "form", None)
    if single_form:
        return {
            "default": {
                "title": "Default",
                "description": "",
                "form": single_form,
            }
        }

    return {}


@dataclass(frozen=True)
class SchoolConfig:
    raw: Dict[str, Any]

    @property
    def schema_version(self) -> str:
        return str(self.raw.get("schema_version", "1.0"))

    @property
    def school_slug(self) -> str:
        return self.raw["school"]["slug"]

    @property
    def display_name(self) -> str:
        display = self.raw.get("school", {}).get("display_name", "")
        if display:
            return display
        return prettify_school_name_from_slug(self.school_slug)

    @property
    def branding(self) -> Dict[str, Any]:
        """
        Returns a normalized branding dict used by templates.
        Keeps backward compatibility while allowing Phase 9 per-school styling.
        """
        branding = self.raw.get("branding") or {}
        theme = branding.get("theme") or {}

        return {
            "logo_url": branding.get("logo_url", DEFAULT_BRANDING["logo_url"]),

            # Optional per-school override files (static-relative paths)
            "custom_css": branding.get("custom_css", DEFAULT_BRANDING["custom_css"]),
            "custom_js": branding.get("custom_js", DEFAULT_BRANDING["custom_js"]),

            # Theme variables injected into CSS :root
            "theme": {
                "primary_color": theme.get("primary_color", DEFAULT_THEME["primary_color"]),
                "accent_color": theme.get("accent_color", DEFAULT_THEME["accent_color"]),
                "background": theme.get("background", DEFAULT_THEME["background"]),
                "card": theme.get("card", DEFAULT_THEME["card"]),
                "text": theme.get("text", DEFAULT_THEME["text"]),
                "muted": theme.get("muted", DEFAULT_THEME["muted"]),
                "border": theme.get("border", DEFAULT_THEME["border"]),
                "radius": theme.get("radius", DEFAULT_THEME["radius"]),
                "font_family": theme.get("font_family", ""),
                "heading_font": theme.get("heading_font", ""),
            },
        }

    @property
    def form(self) -> dict:
        raw = self.raw or {}

        # legacy single-form yaml
        if isinstance(raw.get("form"), dict):
            return raw["form"]

        # multi-form yaml: return the first form as a safe default
        forms = raw.get("forms") or {}
        if isinstance(forms, dict) and forms:
            # prefer "default" if present, else first key in YAML order
            if "default" in forms and isinstance(forms["default"], dict):
                return (forms["default"].get("form") or {}) if isinstance(forms["default"].get("form"), dict) else {}
            first_key = next(iter(forms.keys()))
            first = forms.get(first_key) or {}
            return (first.get("form") or {}) if isinstance(first.get("form"), dict) else {}

        return {}


PROGRAM_FIELD_KEYS = {"interested_in", "program", "program_interest", "dance_style"}


def get_program_options(config: "SchoolConfig") -> list[dict]:
    """
    Returns [{label, value}, ...] for the first program-like select field
    found in the school's form config.

    Lookup order:
    1. Explicit YAML override: leads.program_field_key
    2. Heuristic: first type=select field whose key is in PROGRAM_FIELD_KEYS

    Returns [] if nothing found.
    """
    raw = getattr(config, "raw", None) or {}
    explicit_key = raw.get("leads", {}).get("program_field_key")

    # Gather all form sections across single-form and multi-form configs
    sections: list[dict] = []
    if isinstance(raw.get("form"), dict):
        sections = raw["form"].get("sections") or []
    elif isinstance(raw.get("forms"), dict):
        for form_data in raw["forms"].values():
            if isinstance(form_data, dict) and isinstance(form_data.get("form"), dict):
                sections.extend(form_data["form"].get("sections") or [])

    for section in sections:
        for field in section.get("fields") or []:
            ftype = (field.get("type") or "").strip().lower()
            key = field.get("key", "")
            if ftype != "select":
                continue
            if explicit_key and key != explicit_key:
                continue
            if not explicit_key and key not in PROGRAM_FIELD_KEYS:
                continue
            options = field.get("options") or []
            return [
                {
                    "label": opt.get("label", ""),
                    "value": opt.get("value", opt.get("label", "")),
                }
                for opt in options
                if isinstance(opt, dict)
            ]

    return []


def get_lead_form_config(config_raw: dict, form_key: str | None = None) -> dict | None:
    """
    Returns merged lead form config with safe defaults.

    form_key=None  → reads from top-level `leads:` section (legacy /lead/ route).
    form_key="foo" → reads from `lead_forms.foo`; returns None if the key is not
                     defined in the YAML (caller should 404).

    Keys parsed per variant:
      pipeline_visible — if False, this form's leads are excluded from the
                         default prospect pipeline view (default: True).
      category         — classification tag stored on the lead (default: "lead").
      confirmation_subject — per-variant email subject override (default: "").
    """
    raw = config_raw or {}

    if form_key:
        lead_forms = raw.get("lead_forms") or {}
        if form_key not in lead_forms:
            return None
        leads = lead_forms[form_key] or {}
    else:
        leads = raw.get("leads") or {}

    raw_fields = leads.get("fields") or []
    fields = [f for f in raw_fields if isinstance(f, dict) and f.get("key") and f.get("label")]
    return {
        "form_title": (leads.get("form_title") or "").strip() or "Request Information",
        "form_description": (leads.get("form_description") or "").strip() or "Tell us about your interest and we'll follow up with next steps.",
        "cta_text": (leads.get("cta_text") or "").strip() or "Send My Request",
        "success_message": (leads.get("success_message") or "").strip() or "Thanks for your interest! We'll follow up soon.",
        "confirmation_enabled": bool(leads.get("confirmation_enabled", True)),
        "confirmation_subject": (leads.get("confirmation_subject") or "").strip(),
        "notify_to": (leads.get("notify_to") or "").strip(),
        "redirect_url": (leads.get("redirect_url") or "").strip(),
        "redirect_url_map": {
            str(k): str(v).strip()
            for k, v in (leads.get("redirect_url_map") or {}).items()
            if k and v
        },
        "redirect_url_field": (leads.get("redirect_url_field") or "").strip(),
        "phone_required": bool(leads.get("phone_required", False)),
        "hide_program_field": bool(leads.get("hide_program_field", False)),
        "name_field_key": (leads.get("name_field_key") or "").strip(),
        "pipeline_visible": bool(leads.get("pipeline_visible", True)),
        "category": (leads.get("category") or "lead").strip(),
        "fields": fields,
    }


def find_email_field_key(config_raw: dict) -> Optional[str]:
    """Return the key of the highest-priority email field in the school's YAML form.
    Required email fields are preferred over optional ones.
    Handles both single-form and multi-form YAMLs.
    """
    if not isinstance(config_raw, dict):
        return None
    sections: list[dict] = []
    if isinstance(config_raw.get("form"), dict):
        sections = config_raw["form"].get("sections") or []
    elif isinstance(config_raw.get("forms"), dict):
        for form_data in config_raw["forms"].values():
            if isinstance(form_data, dict) and isinstance(form_data.get("form"), dict):
                sections.extend(form_data["form"].get("sections") or [])
    required_key: Optional[str] = None
    optional_key: Optional[str] = None
    for section in sections:
        for f in section.get("fields") or []:
            if (f.get("type") or "").strip().lower() == "email":
                key = f.get("key")
                if not key:
                    continue
                if f.get("required") and required_key is None:
                    required_key = key
                elif optional_key is None:
                    optional_key = key
    return required_key or optional_key


def get_application_fee_config(config_raw: dict, form_key: str) -> dict:
    """
    Returns fee metadata for the given form_key from the school's YAML config.
    Always safe to call — returns enabled=False when no fee block is configured.
    """
    fee = config_raw.get("application_fee") or {}
    enabled = bool(fee.get("enabled", False))
    waived_for = fee.get("waived_for_forms") or []
    return {
        "enabled": enabled,
        "amount": int(fee.get("amount", 0)) if enabled else 0,
        "description": fee.get("description", "Application fee"),
        "waived": form_key in waived_for,
    }


def load_school_config(school_slug: str) -> Optional[SchoolConfig]:
    """
    Loads configs/schools/<school_slug>.yaml.
    Returns None if file doesn't exist.
    """
    base_dir = Path(settings.BASE_DIR)
    path = base_dir / "configs" / "schools" / f"{school_slug}.yaml"

    if not path.exists():
        return None

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return SchoolConfig(raw=raw)
