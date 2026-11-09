"""Presentation-only school shell for existing public request/camp engines."""

from .appointments import preview_enabled
from .capabilities import has_capability
from .school_navigation import navigation_policy


def public_school_context(profile, *, policy=None):
    policy = navigation_policy(profile.school) if policy is None else policy
    simplified = policy["layout"] == "school" and policy.get("simplified", False)
    context = {"simplified_school": simplified,
               "request_public_base": "scheduling/school/public_base.html" if simplified else "scheduling/public_base.html"}
    if simplified:
        features = {key: key in policy["features"] and has_capability(profile, key)
                    for key in ("lessons", "workshops", "camps")}
        features["lessons"] &= profile.lessons_enabled
        context.update(journey_features=features,
                       trial_booking_available=features["lessons"] and preview_enabled())
    return context


def enrollment_navigation_context(school):
    """Keep legacy enrollment pages query-free unless the school shell is opted in."""
    if school is None:
        return {}
    policy = navigation_policy(school)
    if policy["layout"] != "school" or not policy.get("simplified", False):
        return {}
    from .models import SchedulingProfile

    profile = SchedulingProfile.objects.select_related("school").filter(
        school=school, enabled=True, school__is_active=True).first()
    if profile is None:
        return {}
    return {"business": school, **public_school_context(profile, policy=policy)}
