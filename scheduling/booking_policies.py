"""School-configured published notices; never payment or cancellation rules."""
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from core.services.config_loader import load_school_config


def notice(school, kind):
    if kind not in {"workshop", "party"}:
        return {}
    config = load_school_config(school.slug)
    raw = (config.raw if config else {}).get("scheduling", {}).get("booking_policies") or {}
    url = raw.get("source_url")
    try:
        URLValidator(schemes=["https"])(url or "")
    except ValidationError:
        return {}
    return {"revision": raw.get("revision", ""), "source_url": url,
            "text": raw.get(f"{kind}_text") or ""}
