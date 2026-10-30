"""School-owned customer copy shared by booking presentations."""
from core.services.config_loader import load_school_config


def customer_copy(school):
    config = load_school_config(school.slug)
    raw = (config.raw if config else {}).get("scheduling", {}).get("customer_experience", {})
    return {key: value for key, value in raw.items()
            if key in {"logo", "photo", "participant_note", "sharing_note", "accessories_note",
                       "party_note", "waiver_note", "base_images_only"}}


def checkout_presentation(item, review_url):
    """Keep signed checkout pages in their customer booking experience."""
    from .party_policy import party_policy
    party = bool(item.party_request_id and party_policy(item.profile.school)["packages_only"])
    workshop = bool(item.workshop_request_id and "experience_demo" in item.workshop_request.session_snapshot)
    if not party and not workshop:
        return {}
    school = item.profile.school
    copy = customer_copy(school)
    duration = int((item.ends_at - item.starts_at).total_seconds() // 60)
    hours, minutes = divmod(duration, 60)
    duration_label = " ".join(part for part in (f"{hours} hr" if hours else "", f"{minutes} min" if minutes else "") if part)
    return {"checkout_base": "scheduling/experience/base.html", "party_experience": party,
            "workshop_experience": workshop,
            "step": 3, "payment_review_url": review_url,
            "party_duration_label": duration_label,
            "offering": item.party_request.offering if party else item.workshop_request.session.offering,
            "demo": {"name": str(school), "logo": copy.get("logo") or school.logo_url},
            "party_quote": item.snapshot.get("party_package", {})}
