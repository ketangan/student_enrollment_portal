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


def party_acknowledgments(school):
    config = load_school_config(school.slug)
    raw = (config.raw if config else {}).get("scheduling", {}).get("booking_policies") or {}
    acknowledgments = raw.get("party_acknowledgments")
    if not acknowledgments:
        return {}
    if not isinstance(acknowledgments, dict) or not all(
        isinstance(acknowledgments.get(key), str) and acknowledgments[key].strip()
        for key in ("revision", "waiver", "attendance", "policy")
    ) or not notice(school, "party"):
        raise ValidationError("Party acknowledgments need complete text, a revision and a valid policy URL.")
    return {**acknowledgments, "source_url": notice(school, "party")["source_url"]}
