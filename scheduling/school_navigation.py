"""Opt-in school navigation around the existing scheduling views."""
from urllib.parse import urlsplit
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.services.config_loader import load_school_config
from .capabilities import CAPABILITIES


def schedule_return(request, school):
    value = request.GET.get("schedule", "")
    if not value or len(value) > 2048:
        return ""
    try:
        target = urlsplit(value)
    except ValueError:
        return ""
    expected = reverse("scheduling:school_schedule", kwargs={"school_slug": school.slug})
    return value if not target.scheme and not target.netloc and target.path == expected else ""


OPTIONAL_FEATURES = ("recurring_lessons", "makeup_credits", "family_booking",
                     "operator_tools", "participant_choices", "inventory", "attendance", "family_management")
WORKFLOW_FLAGS = ("teacher_self_service", "direct_enrollment", "enrollment_linking")


def school_policy(school):
    """Normalize operator policy once; database capabilities still grant access.

    Old navigation-only configurations retain their behavior. New workflow
    sections are independent of the visual preset and cannot duplicate old keys.
    """
    config = load_school_config(school.slug)
    scheduling = (config.raw if config else {}).get("scheduling", {})
    if not isinstance(scheduling, dict):
        raise ValidationError("Scheduling configuration must be a mapping.")
    raw = scheduling.get("navigation", {})
    if not isinstance(raw, dict) or raw.get("layout", "bookings") not in ("bookings", "school"):
        raise ValidationError("Scheduling navigation layout must be bookings or school.")
    if set(raw) - {"layout", "features", "simplified", "single_location", "optional_features", *WORKFLOW_FLAGS}:
        raise ValidationError("Choose supported scheduling navigation settings.")
    features = raw.get("features", ["lessons", "visits"] if raw.get("layout") == "school" else list(CAPABILITIES))
    if not isinstance(features, list) or any(not isinstance(key, str) or key not in CAPABILITIES for key in features):
        raise ValidationError("Scheduling navigation features must be a list of supported capabilities.")
    flags = {}
    for key in ("simplified", "single_location", *WORKFLOW_FLAGS):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise ValidationError(f"Scheduling navigation {key} must be true or false.")
        flags[key] = value
    if any(flags.values()) and raw.get("layout") != "school":
        raise ValidationError("School journey options require the school layout.")
    workflows = scheduling.get("workflows", {})
    if not isinstance(workflows, dict):
        raise ValidationError("Scheduling workflows must be a mapping.")
    allowed = {*WORKFLOW_FLAGS, "optional_features", "lesson_changes", "class_setup", "reschedule_reason_required"}
    if set(workflows) - allowed:
        raise ValidationError("Choose supported scheduling workflow settings.")
    if "workflows" in scheduling and allowed.intersection(raw):
        raise ValidationError("Move all workflow settings out of navigation before defining workflows.")
    legacy = "workflows" not in scheduling
    workflow = {}
    reason_required = workflows.get("reschedule_reason_required", True)
    if not isinstance(reason_required, bool):
        raise ValidationError("Scheduling workflow reschedule_reason_required must be true or false.")
    workflow["reschedule_reason_required"] = reason_required
    for key in WORKFLOW_FLAGS:
        value = flags[key] if legacy else workflows.get(key, False)
        if not isinstance(value, bool):
            raise ValidationError(f"Scheduling workflow {key} must be true or false.")
        workflow[key] = value
    optional = (raw if legacy else workflows).get("optional_features", list(OPTIONAL_FEATURES))
    if not isinstance(optional, list) or any(not isinstance(key, str) or key not in
            OPTIONAL_FEATURES for key in optional):
        raise ValidationError("Choose supported optional school features.")
    # Before workflow sections, only recurring creation was restricted outside
    # the simplified preset. Preserve that behavior for existing configurations.
    if legacy and not flags["simplified"]:
        optional = [key for key in OPTIONAL_FEATURES if key != "recurring_lessons" or key in optional]
    elif legacy:
        # These controls were always available before explicit workflow settings.
        optional = list(dict.fromkeys([*optional, "attendance", "family_management"]))
    workflow["optional_features"] = optional
    for key, choices, old_default in (
        ("lesson_changes", ("single", "series"), "single" if flags["simplified"] else "series"),
        ("class_setup", ("requests", "checkout"), "requests" if flags["simplified"] else "checkout"),
    ):
        value = old_default if legacy else workflows.get(key, choices[1])
        if value not in choices:
            raise ValidationError(f"Scheduling workflow {key} must be {' or '.join(choices)}.")
        workflow[key] = value
    return {"navigation": {"layout": raw.get("layout", "bookings"), "features": features,
                           **{key: flags[key] for key in ("simplified", "single_location")}},
            "workflows": workflow}


def navigation_policy(school):
    return school_policy(school)["navigation"]


def workflow_policy(school):
    return school_policy(school)["workflows"]


def require_optional_feature(profile, feature):
    from django.http import Http404
    if feature not in workflow_policy(profile.school)["optional_features"]:
        raise Http404


def school_navigation(request, context):
    """Presentation only: every destination retains its own authorization checks."""
    from .appointments import preview_enabled

    slug = context["business"].slug
    policy = context.get("school_policy") or navigation_policy(context["business"])
    if policy["simplified"]:
        route = request.resolver_match.url_name
        if route in {"locations", "location_new", "location_edit", "room_new", "room_edit", "access", "lesson_settings"}:
            section = {"label": "Settings", "key": "settings", "active": True, "tabs": [],
                "url": reverse("school_settings", kwargs={"school_slug": slug}) + "?tab=scheduling"}
            return {"sections": [section], "section": section}
        program_routes = {"school_programs", "workshops", "workshop_new", "camps", "camp_new", "lesson_booking_setup", "lesson_booking_offer_new", "lesson_booking_offer_edit"}
        programs = route in program_routes
        name = "school_programs" if programs else "school_schedule"
        section = {"label": "Programs" if programs else "Schedule", "key": "programs" if programs else "scheduling",
            "active": True, "url": reverse("scheduling:" + name, kwargs={"school_slug": slug}), "tabs": []}
        return {"sections": [section], "section": section}
    features = context["features"]
    route = request.resolver_match.url_name
    kind = request.resolver_match.kwargs.get("kind", "")
    selected = request.GET.get("section", "teachers")
    active = "schedule"
    if route.startswith("lesson_booking"):
        active = "bookings"
    if route.startswith("lesson_credit"):
        active = "credits"
    if route.startswith("family_") or (route.startswith("lesson_setup") and
            (selected == "students" or kind in {"students", "households"})):
        active = "students"
    elif route.startswith("lesson_setup"):
        active = "teachers"
    if route.startswith("visit") or route.startswith("admission"):
        active = "visits"
    if route in {"lesson_settings", "lesson_credit_policy", "locations", "access", "integrations"}:
        active = "settings"
    tabs = []

    def add(key, label, name, query=""):
        tabs.append({"key": key, "label": label, "url": reverse("scheduling:" + name,
            kwargs={"school_slug": slug}) + query, "active": key == active})

    if features["lessons"] and context["profile"].lessons_enabled:
        add("schedule", "Lessons", "lessons")
        if preview_enabled():
            add("bookings", "Bookings", "lesson_bookings")
        if "family_management" in workflow_policy(context["business"])["optional_features"]:
            add("students", "Students & families", "lesson_setup", "?section=students")
        add("teachers", "Teachers & availability", "lesson_setup", "?section=teachers")
        add("credits", "Make-up credits", "lesson_credits")
    if features["visits"]:
        add("visits", "Tours", "visits")
    if context.get("school_shell", {}).get("is_editor"):
        tabs.append({"key": "settings", "label": "Settings", "active": active == "settings",
            "url": reverse("school_settings", kwargs={"school_slug": slug}) + "?tab=scheduling"})
    elif context["can_edit_setup"] and features["lessons"]:
        add("settings", "Settings", "lesson_settings")
    section = {"label": "Scheduling", "key": "scheduling", "active": True,
        "url": tabs[0]["url"] if tabs else reverse("scheduling:workspace", kwargs={"school_slug": slug}), "tabs": tabs}
    return {"sections": [section], "section": section}
