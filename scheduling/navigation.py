"""Permission-aware entry points between enrollment and optional Bookings."""
from django.http import Http404
from django.urls import reverse

from .access import has_permission, require_access
from .capabilities import has_capability
from .messaging import preview_enabled
from .visits import enrollment_access


def workspace_navigation(context):
    """Group enabled tools without changing their URLs or access rules."""
    features = context["features"]
    slug = context["business"].slug
    active = context["active_nav"]

    def link(key, label, route):
        return {"key": key, "label": label,
                "url": reverse("scheduling:" + route, kwargs={"school_slug": slug})}

    reservations = []
    if features["reservations"]:
        reservations.append(link("reservations", "Reservations", "reservations"))
    if features["visits"]:
        reservations.append(link("visits", "Tours", "visits"))
    if features["lessons"] and context["profile"].lessons_enabled:
        reservations.append(link("lessons", "Lessons", "lessons"))
    if context["message_preview_available"] and (features["lessons"] or features["visits"]):
        reservations.append(link("messages", "Messages", "messages"))

    setup = [link(key, label, "party_setup" if key == "parties" else key) for key, label in
             (("workshops", "Workshop"), ("parties", "Parties"), ("camps", "Camps")) if features[key]]
    setup.append(link("catalog", "Catalog", "catalogs"))
    setup.append(link("settings", "Materials Inventory", "settings"))
    settings = [link("locations", "Location & rooms", "locations")]
    if features["parties"] and context["can_edit_setup"]:
        settings.append(link("guest_communication", "Guest communication", "guest_communication"))
    if context["can_view_access"]:
        settings.append(link("access", "Team Access", "access"))
    if features["integrations"] and context["can_edit_setup"]:
        settings.append(link("integrations", "Integrations", "integrations"))
    if context.get("can_manage_billing"):
        settings.append({"key": "billing", "label": "Billing",
                         "url": reverse("scheduling:school_billing", kwargs={"school_slug": slug})})

    # Legacy request detail links still work, but requests are not a menu item.
    if active in {"requests", "preparation"}:
        active = "reservations" if features["reservations"] else "workshops"
    sections = [{**link("overview", "Dashboard", "workspace"), "tabs": [], "active": active == "overview"}]
    for key, label, tabs in (("reservations", "Reservations", reservations),
                             ("setup", "Setup", setup), ("settings", "Settings", settings)):
        if tabs:
            for tab in tabs:
                tab["active"] = tab["key"] == active
            sections.append({"key": key, "label": label, "url": tabs[0]["url"],
                             "tabs": tabs, "active": any(tab["active"] for tab in tabs)})
    section = next((item for item in sections if item["active"]), sections[0])
    return {"sections": sections, "section": section}


def school_entry(user, school):
    try:
        profile, role = require_access(user, school.slug)
    except Http404:
        return None
    links = []

    def add(label, name):
        links.append({"label": label, "url": reverse("scheduling:" + name, kwargs={"school_slug": school.slug})})

    add("Open scheduling", "workspace")
    if has_permission(role, "setup.edit"):
        add("Locations and rooms", "locations")
        if has_capability(profile, "visits"):
            add("Tours and playdates", "visits")
            if preview_enabled():
                add("Visit settings and closures", "visit_setup")
            if enrollment_access(user, profile, owner=True):
                add("Tour-to-enrollment rules", "admission_policy")
        if has_capability(profile, "workshops"):
            add("Workshops and participant choices", "workshops")
        if has_capability(profile, "camps"):
            add("Camps", "camps")
        if has_capability(profile, "lessons"):
            add("Lesson scheduling settings", "lesson_settings")
            if profile.lessons_enabled:
                add("Teachers, subjects and availability", "lesson_setup")
                add("Make-up rules", "lesson_credit_policy")
                from .appointments import preview_enabled as booking_preview_enabled
                if booking_preview_enabled():
                    add("Trial and family booking offers", "lesson_booking_setup")
        if has_capability(profile, "reservations"):
            add("Reservation settings", "reservations")
        if has_capability(profile, "integrations"):
            add("Integrations", "integrations")
        add("Scheduling team access", "access")
    return {"url": links[0]["url"], "links": links}


def enrollment_url(user, profile):
    if profile and enrollment_access(user, profile):
        return reverse("school_dashboard", kwargs={"school_slug": profile.school.slug})
    return ""
