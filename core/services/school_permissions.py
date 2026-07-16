"""
Role-based permission layer for school admin views.

Access resolution (_get_accessible_school_for_admin) answers:
  "Can this user see this school at all?"

This module answers:
  "Does this user have enough role to perform this action?"

Keep these two concerns separate.
"""

from django.http import Http404

from core.models import SchoolAdminMembership


def get_school_membership(user, school) -> SchoolAdminMembership | None:
    """
    Return the active membership for (user, school), or None.
    Superusers always return None — callers must short-circuit on is_superuser
    before using the returned membership for role checks.
    """
    if not user or not user.is_authenticated:
        return None
    try:
        return SchoolAdminMembership.objects.get(
            user=user, school=school, is_active=True
        )
    except SchoolAdminMembership.DoesNotExist:
        return None


def require_school_role(request, school, minimum_role: str) -> SchoolAdminMembership | None:
    """
    Enforce that the requesting user holds at least `minimum_role` for `school`.

    Superusers pass unconditionally (returns None).
    Non-superusers without a qualifying membership get Http404.

    Returns the membership object so callers can inspect .role without a second query.
    """
    if request.user.is_superuser:
        return None

    membership = get_school_membership(request.user, school)
    if membership is None or not membership.has_role(minimum_role):
        raise Http404("Page not found")
    return membership


def active_membership_schools(user):
    """Return queryset of Schools the user has an active membership in."""
    from core.models import School
    school_ids = (
        SchoolAdminMembership.objects
        .filter(user=user, is_active=True)
        .values_list("school_id", flat=True)
    )
    return School.objects.filter(id__in=school_ids)
