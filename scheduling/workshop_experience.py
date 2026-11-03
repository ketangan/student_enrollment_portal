"""School-scoped configuration for the shared customer workshop screens.

The legacy demo adapter is used only for demo schools; real schools read the
ordinary school configuration and their own published catalog records.
"""
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404

from core.services.config_loader import load_school_config
from .capabilities import has_capability
from .models import SchedulingProfile


def settings_for(school):
    config = load_school_config(school.slug)
    value = (config.raw if config else {}).get("scheduling", {}).get("customer_experience", {})
    if not isinstance(value, dict) or type(value.get("enabled", False)) is not bool:
        raise ValidationError("Customer experience needs a valid enabled setting.")
    return value


def available(profile, *, require_intake=False):
    if profile.school.is_demo:
        from .experience_demo.config import available as demo_available
        return demo_available(profile, require_intake=require_intake)
    return bool(profile.enabled and profile.school.is_active
                and settings_for(profile.school).get("enabled", False)
                and (profile.native_reservations_enabled or not require_intake)
                and all(has_capability(profile, key) for key in ("workshops", "reservations")))


def context(request, slug):
    profile = get_object_or_404(SchedulingProfile.objects.select_related("school"), school__slug=slug)
    if profile.school.is_demo:
        from .experience_demo.config import context as demo_context
        return demo_context(request, slug)
    if not available(profile):
        raise Http404
    copy = settings_for(profile.school)
    return profile, {"name": str(profile.school), "logo": copy.get("logo") or profile.school.logo_url,
        "photo": copy.get("photo", ""), "locations": list(profile.locations.values("name")),
        "start_title": copy.get("start_title", "Book a workshop"),
        "start_description": copy.get("start_description", "Choose a studio, workshop and time."),
        "choose_title": copy.get("choose_title", "Your participants"),
        "participant_heading": copy.get("participant_heading", "Your participants"),
        "confirmation_title": copy.get("confirmation_title", "Your workshop is confirmed."),
        "policy_revision": "school-policies-v1", "stripe_checkout": True,
        "payment_provider": profile.payment_provider, "designs": [], "colors": [],
        "design_image": "", "design_group": "", "color_group": "", "age_group": ""}


def offering_config(profile, config, offering):
    if offering.location.profile_id != profile.pk:
        raise Http404
    if profile.school.is_demo:
        return config
    from .requests import catalog_for
    groups = catalog_for(offering)
    # Inventory Setup already defines the first two stock dimensions as design/color.
    dimensions = [group for group in groups if group.tracks_stock]
    if not offering.inventory_catalog_id:
        dimensions = [group for group in groups if not group.is_participant_age][:2]
    roles = dict(zip(("design_group", "color_group"), dimensions))
    age = next((group for group in groups if group.is_participant_age), None)
    if age:
        roles["age_group"] = age
    result = {**config, "group_ids": {key: group.pk for key, group in roles.items()}}
    result.update({key: group.name for key, group in roles.items()})
    if dimensions:
        result["design_image"] = dimensions[0].reference_image
        result["designs"] = [{"name": option.label, "image_url": option.image_url}
                             for option in dimensions[0].active_options]
    if len(dimensions) > 1:
        result["colors"] = [{"name": option.label, "hex": option.swatch}
                            for option in dimensions[1].active_options]
    return result


def payment_problem(profile, location):
    """Fail closed before intake without exposing payment configuration to customers."""
    if profile.school.is_demo:
        return ""
    try:
        live_account(profile, location)
    except ValidationError:
        return "Online booking is temporarily unavailable for this studio. Please contact the studio."
    return ""


def live_account(profile, location):
    from .reservations import booking_account
    account = booking_account(profile, True, location)
    if account.school_id != profile.school_id or account.environment != "live":
        raise ValidationError("A live payment account is required for this studio.")
    return account


def snapshot_key(profile):
    return "experience_demo" if profile.school.is_demo else "customer_experience"


def is_workshop_experience(source):
    return bool(source and any(key in source.session_snapshot
                               for key in ("experience_demo", "customer_experience")))
