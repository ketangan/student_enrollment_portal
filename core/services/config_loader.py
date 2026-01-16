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
            },
        }

    @property
    def form(self) -> Dict[str, Any]:
        return self.raw["form"]


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
