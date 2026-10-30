from django import forms
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Prefetch, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from zoneinfo import ZoneInfo

from .access import accessible_profiles, has_permission, require_access, require_location_access, staff_locations, location_restricted
from .capabilities import CAPABILITIES, has_capability, require_capability
from .models import Offering, SchedulingMembership, SchedulingProfile, Session
from .setup import build_form, edit_permission, records, save_setup, set_publication


@never_cache
@login_required
@require_GET
def index(request):
    from core.services.portal_environment import scope_schools
    profiles = list(scope_schools(accessible_profiles(request.user), request, "school__"))
    if len(profiles) == 1:
        return redirect("scheduling:workspace", school_slug=profiles[0].school.slug)
    return render(request, "scheduling/index.html", {"profiles": profiles})


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def password_change(request, school_slug):
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm
    profile, role = require_location_access(request.user, school_slug)
    form = PasswordChangeForm(request.user, data=request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        form.save()
        update_session_auth_hash(request, form.user)
        messages.success(request, "Password updated successfully.")
        return redirect("scheduling:workspace", school_slug=school_slug)
    context = _context(request, profile, role, "overview")
    context["form"] = form
    return render(request, "scheduling/password_change.html", context)


def _context(request, profile, role, active_nav):
    from core.services.billing_access import billing_role
    from .messaging import preview_enabled
    from .experience_demo.config import available, configuration, customer_demo_url
    from .navigation import workspace_navigation
    context = {
        "features": {key: has_capability(profile, key) for key in CAPABILITIES},
        "message_preview_available": preview_enabled(),
        "experience_demo_available": available(profile),
        "profile": profile,
        "business": profile.school,
        "role_label": SchedulingMembership.Role(role).label,
        "can_view_access": has_permission(role, "access.view"),
        "can_manage_billing": billing_role(request.user, profile.school) == "owner",
        "can_edit_setup": has_permission(role, "setup.edit"),
        "can_edit_sessions": has_permission(role, "sessions.edit"),
        "active_nav": active_nav,
    }
    context["school_wide_access"] = not location_restricted(profile)
    context["can_view_access"] &= context["school_wide_access"]
    if location_restricted(profile):
        for key in ("lessons", "visits", "integrations"):
            context["features"][key] = False
    try:
        demo_config = configuration(profile.school.slug) if context["experience_demo_available"] else {}
    except Http404:
        demo_config = {}
    context["branded_admin_demo"] = demo_config.get("admin_branding") is True
    context["demo_admin_logo"] = demo_config.get("logo", "")
    context["booking_navigation"] = workspace_navigation(context)
    if context["experience_demo_available"]:
        context["customer_demo_url"] = customer_demo_url(profile)
    from .location_filters import staff_location
    context.update(staff_location(request, profile))
    section = context["booking_navigation"]["section"]
    target = next((tab["url"] for tab in section["tabs"] if tab["active"]), section["url"])
    context["location_action"] = target
    context["location_query_fields"] = [
        (key, value) for key, values in request.GET.lists() for value in values
        if key not in {"location", "page", "session"} and request.path == target
    ]
    context["workspace_wide"] = active_nav in {"access", "integrations"}
    return context


@never_cache
@login_required
@require_GET
def workspace(request, school_slug):
    profile, role = require_location_access(request.user, school_slug)
    context = _context(request, profile, role, "overview")
    from .calendar import calendar_available, offerings
    context["calendar_available"] = calendar_available(profile)
    selected_view = request.GET.get("view", profile.default_dashboard_view if context["calendar_available"] else "overview")
    if selected_view not in {"overview", "calendar"} or (selected_view == "calendar" and not context["calendar_available"]):
        raise Http404
    if selected_view == "calendar":
        location = context["selected_location"]
        locations = staff_locations(profile).filter(pk=location.pk) if location else staff_locations(profile)
        items = offerings(profile)
        if location:
            items = items.filter(location=location)
        context["calendar_config"] = {
            "eventsUrl": reverse("scheduling:calendar_events", kwargs={"school_slug": school_slug}),
            "printUrl": reverse("scheduling:studio_preparation", kwargs={"school_slug": school_slug}),
            "location": str(location.pk) if location else "",
            "resources": [{"id": str(item.pk), "title": item.name, "zone": item.timezone_name} for item in locations],
            "offerings": [{"id": str(item.pk), "title": f"{item.name} ({item.location.name})"} for item in items],
            "today": timezone.localdate(timezone=ZoneInfo(location.timezone_name) if location else timezone.get_default_timezone()).isoformat(),
        }
        return render(request, "scheduling/calendar.html", context, status=200 if context["location_filter"].is_valid() else 400)
    from .workspace import overview
    form = context["location_filter"]
    if form.is_valid():
        context.update(overview(profile, location=form.selected))
    return render(request, "scheduling/workspace.html", context, status=200 if form.is_valid() else 400)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def feature_settings(request, school_slug):
    from .features import feature_form, operator_profile, save_features
    profile = operator_profile(request.user, school_slug)
    form = save_features(request.user, school_slug, request.POST) if request.method == "POST" else feature_form(profile)
    if request.method == "POST" and form.is_valid():
        messages.success(request, "Available features updated.")
        return redirect("scheduling:workspace", school_slug=school_slug)
    context = _context(request, profile, SchedulingMembership.Role.OWNER, "overview")
    context["form"] = form
    return render(request, "scheduling/features.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def access_list(request, school_slug):
    from .team_access import add_member, membership_form, update_member
    profile, role = require_access(request.user, school_slug, "access.view")
    context = _context(request, profile, role, "access")
    edit = request.POST.get("member") if request.method == "POST" else request.GET.get("edit")
    action = request.POST.get("team_action")
    if request.method == "POST" and action == "add":
        form = add_member(request.user, school_slug, request.POST)
        if form.is_valid():
            messages.success(request, "Team member added.")
            return redirect("scheduling:access", school_slug=school_slug)
        context["member_form"] = form
    elif edit:
        if not edit.isdecimal():
            raise Http404
        member = get_object_or_404(profile.memberships.select_related("user", "profile"), pk=edit)
        if request.method == "POST":
            if action not in ("name", "email", "role", "locations", "remove"):
                return HttpResponseBadRequest("Invalid team action.")
            form = update_member(request.user, school_slug, member.pk, action, request.POST)
        else:
            form = membership_form(member)
        if request.method == "POST" and form.is_valid():
            messages.success(request, "Team access updated.")
            if not request.user.is_superuser and not profile.memberships.filter(
                    user=request.user, role="owner", all_locations=True, is_active=True).exists():
                return redirect("scheduling:workspace", school_slug=school_slug)
            return redirect("scheduling:access", school_slug=school_slug)
        context.update(edit_member=member, member_form=form)
    elif request.method == "POST":
        return HttpResponseBadRequest("Choose a team member.")
    context["memberships"] = list(profile.memberships.select_related("user", "created_by", "profile").prefetch_related("locations").order_by("user__username"))
    for member in context["memberships"]:
        member.access_form = membership_form(member)
    context["team_locations"] = list(profile.locations.order_by("name", "pk"))
    context["team_url"] = request.path
    return render(request, "scheduling/access.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_GET
def locations(request, school_slug):
    profile, role = require_location_access(request.user, school_slug)
    context = _context(request, profile, role, "locations")
    context["locations"] = context["location_filter"].filter(records(profile, "locations"), "pk").prefetch_related("rooms")
    return render(request, "scheduling/locations.html", context)


@never_cache
@login_required
@require_GET
def workshops(request, school_slug):
    profile, role = require_location_access(request.user, school_slug)
    context = _context(request, profile, role, "workshops")
    form = context["location_filter"]
    context["offerings"] = form.filter(records(profile, "workshops"), "location").annotate(
        upcoming_count=Count("sessions", filter=Q(sessions__starts_at__gt=timezone.now())),
    )
    return render(request, "scheduling/workshops.html", context, status=200 if form.is_valid() else 400)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def workshop_detail(request, school_slug, pk):
    profile, role = require_location_access(request.user, school_slug)
    context = _context(request, profile, role, "workshops")
    offering = get_object_or_404(records(profile, "workshops").select_related("inventory_catalog"), pk=pk)
    form, selected = None, None
    editor = request.POST.get("session_id") if request.method == "POST" else request.GET.get("edit_session")
    if editor is not None:
        require_location_access(request.user, school_slug, "sessions.edit")
        if editor != "new":
            try:
                session_pk = forms.IntegerField(min_value=1, max_value=2**63 - 1).clean(editor)
            except ValidationError:
                return HttpResponseBadRequest("Choose a valid session.")
            selected = get_object_or_404(offering.sessions, pk=session_pk)
        if request.method == "POST":
            form = save_setup(request.user, school_slug, "sessions", request.POST,
                              pk=selected.pk if selected else None, offering_pk=offering.pk)
            if form.is_valid():
                messages.success(request, "Session saved.")
                return redirect(f'{reverse("scheduling:workshop_detail", kwargs={"school_slug": school_slug, "pk": pk})}#session-{form.instance.pk}')
        else:
            form = build_form(profile, "sessions", pk=selected.pk if selected else None, offering_pk=offering.pk)
    elif request.method == "POST":
        return HttpResponseBadRequest("Choose a session to save.")
    context.update(offering=offering, sessions=offering.sessions.select_related("room"),
                   hide_setup_tabs=True, session_form=form, editing_session=selected,
                   adding_session=editor == "new")
    if not offering.inventory_catalog_id:
        context["choice_groups"] = offering.choice_groups.prefetch_related("options")
    if request.method == "POST":
        return render(request, "scheduling/workshop_detail.html", context, status=400)
    return render(request, "scheduling/workshop_detail.html", context)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def setup_edit(request, school_slug, kind, pk=None, offering_pk=None):
    profile, role = require_location_access(request.user, school_slug, edit_permission(kind))
    return_to_parties = kind == "workshops" and bool(pk) and request.GET.get("return_to") == "parties"
    parties_url = reverse("scheduling:party_setup", kwargs={"school_slug": school_slug})
    if kind == "party_packages" and pk and request.method == "GET":
        form = build_form(profile, kind, pk=pk)
        query = f"?location={form.instance.offering.location_id}&edit={pk}#package-{pk}" if pk else "?new=1#package-new"
        return redirect(reverse("scheduling:party_setup", kwargs={"school_slug": school_slug}) + query)
    if request.method == "POST":
        form = save_setup(request.user, school_slug, kind, request.POST, pk, offering_pk)
        if form.is_valid():
            messages.success(request, "Changes saved." if pk else "Created successfully.")
            if return_to_parties:
                return redirect(f"{parties_url}?location={form.instance.location_id}")
            if kind == "party_packages":
                return redirect("scheduling:party_setup", school_slug=school_slug)
            if kind in {"workshops", "sessions"}:
                offering_id = form.instance.pk if kind == "workshops" else form.instance.offering_id
                return redirect("scheduling:workshop_detail", school_slug=school_slug, pk=offering_id)
            return redirect("scheduling:locations", school_slug=school_slug)
    else:
        form = build_form(profile, kind, pk=pk, offering_pk=offering_pk)
    active = "parties" if kind == "party_packages" else "locations" if kind in {"locations", "rooms"} else "workshops"
    context = _context(request, profile, role, active)
    if return_to_parties:
        context["parties_return_url"] = f"{parties_url}?location={form.instance.location_id}"
    context.update(form=form, kind=kind, editing=bool(pk), party_setup=kind == "party_packages", entity_label={"locations": "location", "rooms": "room", "workshops": "workshop", "sessions": "session", "party_packages": "party package"}[kind])
    context["hide_setup_tabs"] = kind in {"workshops", "sessions"}
    if kind == "party_packages" and not pk:
        from .party_forms import package_editor_context
        context["hide_setup_tabs"] = True
        context.update(package_editor_context(form, profile, context.get("selected_location")))
        return render(request, "scheduling/party_package_form.html", context,
            status=400 if request.method == "POST" else 200)
    zone = form.instance.offering.location.timezone_name if kind == "sessions" else timezone.get_default_timezone_name()
    context["session_location"] = form.instance.offering.location if kind == "sessions" else None
    with timezone.override(ZoneInfo(zone)):
        return render(request, "scheduling/setup_form.html", context)


@never_cache
@login_required
@require_POST
def publication(request, school_slug, kind, pk):
    profile, _ = require_location_access(request.user, school_slug, edit_permission(kind))
    instance = get_object_or_404(records(profile, kind), pk=pk)
    action = request.POST.get("action")
    if action not in {"publish", "unpublish"}:
        return HttpResponseBadRequest("Choose publish or unpublish.")
    try:
        set_publication(request.user, school_slug, kind, pk, action == "publish")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Published." if action == "publish" else "Unpublished.")
    offering_id = instance.pk if kind == "workshops" else instance.offering_id
    return redirect("scheduling:workshop_detail", school_slug=school_slug, pk=offering_id)


@never_cache
@require_GET
def public_schedule(request, school_slug):
    profile = get_object_or_404(SchedulingProfile.objects.select_related("school"), school__slug=school_slug, enabled=True)
    require_capability(profile, "workshops")
    sessions = Session.objects.filter(is_published=True, starts_at__gt=timezone.now()).select_related("room")
    offerings = Offering.objects.filter(
        location__profile=profile, is_published=True, visit_program__isnull=True,
    ).filter(Q(accepts_party_requests=True) | Q(sessions__is_published=True, sessions__starts_at__gt=timezone.now())).select_related("location").distinct().prefetch_related(Prefetch("sessions", queryset=sessions, to_attr="public_sessions"))
    from .requests import catalog_for
    from .camps import camp_dates, check_registration
    for offering in offerings:
        offering.accepts_party_requests = offering.accepts_party_requests and has_capability(profile, "parties")
        catalog = catalog_for(offering)
        offering.requests_available = offering.accepts_requests and all(g.active_options for g in catalog)
        offering.available_camps = []
        for camp in offering.camps.filter(is_open=True) if profile.native_reservations_enabled and has_capability(profile, "camps") else []:
            try:
                check_registration(camp, camp_dates(camp), catalog)
            except ValidationError:
                continue
            offering.available_camps.append(camp)
    can_return_to_admin = accessible_profiles(request.user).filter(pk=profile.pk).exists()
    return render(request, "scheduling/public_schedule.html", {
        "business": profile.school, "offerings": offerings,
        "can_return_to_admin": can_return_to_admin,
    })
