from django.http import Http404
from django.db.models import Q
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


def require_access(user, school_slug, permission="workspace.view", *, capability=None, location_scoped=False):
    profile = get_object_or_404(accessible_profiles(user), school__slug=school_slug)
    if user.is_superuser:
        role = SchedulingMembership.Role.OWNER
    else:
        membership = get_object_or_404(
            SchedulingMembership, profile=profile, user=user, is_active=True
        )
        role = membership.role
        if not membership.all_locations:
            if not location_scoped or permission == "access.view":
                raise Http404("Page not found")
            profile._staff_location_ids = tuple(membership.locations.filter(profile=profile).values_list("pk", flat=True))
    if not has_permission(role, permission):
        raise Http404("Page not found")
    if capability:
        require_capability(profile, capability)
    return profile, role


def require_location_access(user, school_slug, permission="workspace.view", *, capability=None):
    """Only use from staff paths whose reads and writes enforce location scope."""
    return require_access(user, school_slug, permission, capability=capability, location_scoped=True)


def location_restricted(profile):
    return getattr(profile, "_staff_location_ids", None) is not None


def scope_locations(items, profile, *lookups):
    """Intersect trusted tenant queries with the authenticated membership's scope."""
    ids = getattr(profile, "_staff_location_ids", None)
    if ids is None:
        return items
    predicate = Q(pk__in=[])
    for lookup in lookups:
        predicate |= Q(**{lookup + "__in": ids})
    return items.filter(predicate)


def staff_locations(profile):
    return scope_locations(profile.locations.all(), profile, "pk")


def scope_reservations(items, profile):
    return scope_locations(items, profile, "workshop_request__session__offering__location_id",
        "party_request__offering__location_id", "camp_registration__camp__offering__location_id")


def get_staff_reservation(profile, public_id, *, exclude_visits=False):
    from .models import Reservation
    items = Reservation.objects.filter(profile=profile).select_related("order", "room", "prepared_by", "party_request")
    if exclude_visits:
        items = items.filter(visit_booking__isnull=True)
    return get_object_or_404(scope_reservations(items, profile), public_id=public_id)
