from django.http import Http404
from django.shortcuts import get_object_or_404

from .models import SchedulingMembership, SchedulingProfile
from .capabilities import require_capability


ROLE_PERMISSIONS = {
    SchedulingMembership.Role.OWNER: frozenset({"workspace.view", "access.view", "setup.edit", "sessions.edit"}),
    SchedulingMembership.Role.SCHEDULER: frozenset({"workspace.view", "sessions.edit"}),
    SchedulingMembership.Role.VIEWER: frozenset({"workspace.view"}),
}


def has_permission(role, permission):
    return permission in ROLE_PERMISSIONS.get(role, ())


def accessible_profiles(user):
    profiles = SchedulingProfile.objects.select_related("school")
    if not user.is_authenticated or not user.is_active:
        return profiles.none()
    # Enrollment trial/billing state is not the Bookings entitlement.
    profiles = profiles.filter(enabled=True)
    if not user.is_superuser:
        profiles = profiles.filter(
            memberships__user=user, memberships__is_active=True,
            memberships__role__in=ROLE_PERMISSIONS,
        )
    return profiles.order_by("school__display_name", "school__slug")


def require_access(user, school_slug, permission="workspace.view", *, capability=None):
    profile = get_object_or_404(accessible_profiles(user), school__slug=school_slug)
    if user.is_superuser:
        role = SchedulingMembership.Role.OWNER
    else:
        membership = get_object_or_404(
            SchedulingMembership, profile=profile, user=user, is_active=True
        )
        role = membership.role
    if not has_permission(role, permission):
        raise Http404("Page not found")
    if capability:
        require_capability(profile, capability)
    return profile, role
