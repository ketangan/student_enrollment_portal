from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from django.conf import settings

DEFAULT_THEME = {
    "primary_color": "#111827",  # slate-ish
    "accent_color": "#2563EB",   # blue-ish
}

DEFAULT_BRANDING = {
    "logo_url": "",
    "theme": DEFAULT_THEME,
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
        branding = self.raw.get("branding") or {}
        theme = branding.get("theme") or {}
        return {
            "logo_url": branding.get("logo_url", DEFAULT_BRANDING["logo_url"]),
            "theme": {
                "primary_color": theme.get("primary_color", DEFAULT_THEME["primary_color"]),
                "accent_color": theme.get("accent_color", DEFAULT_THEME["accent_color"]),
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
