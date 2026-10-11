from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import Http404

from core.models import School, SchoolAdminMembership
from scheduling.access import accessible_profiles, has_permission, require_access
from scheduling.models import SchedulingMembership, SchedulingProfile


@pytest.mark.parametrize("role,permission,allowed", [
    ("owner", "workspace.view", True), ("owner", "access.view", True),
    ("scheduler", "workspace.view", True), ("scheduler", "access.view", False),
    ("viewer", "workspace.view", True), ("viewer", "access.view", False),
    ("invalid", "workspace.view", False), (None, "access.view", False),
    ("owner", "unknown.permission", False),
])
def test_permissions_fail_closed(role, permission, allowed):
    assert has_permission(role, permission) is allowed


@pytest.mark.parametrize("user", [AnonymousUser(), SimpleNamespace(is_authenticated=True, is_active=False)])
def test_unavailable_users_do_not_query_database(user):
    assert list(accessible_profiles(user)) == []


@pytest.mark.django_db
def test_membership_grants_only_own_profile(booking_member):
    other = SchedulingProfile.objects.create(
        school=School.objects.create(slug="other", display_name="Other"), enabled=True
    )
    assert list(accessible_profiles(booking_member.user)) == [booking_member.profile]
    with pytest.raises(Http404):
        require_access(booking_member.user, other.school.slug)


@pytest.mark.django_db
@pytest.mark.parametrize("permission", ["access.view", "not-a-permission"])
def test_authorization_is_not_just_navigation(booking_member, permission):
    booking_member.role = "viewer"
    booking_member.save()
    with pytest.raises(Http404):
        require_access(booking_member.user, booking_member.profile.school.slug, permission)


@pytest.mark.django_db
def test_unknown_superuser_permission_also_denied(booking_member):
    user = booking_member.user
    user.is_superuser = True
    user.save()
    with pytest.raises(Http404):
        require_access(user, booking_member.profile.school.slug, "unknown")


@pytest.mark.django_db
def test_enrollment_membership_and_plan_do_not_enable_bookings(booking_profile, booking_user):
    school = booking_profile.school
    school.plan = "custom"
    school.feature_flags = {"founder_pricing_enabled": True, "scheduling_enabled": True}
    school.save()
    SchoolAdminMembership.objects.create(user=booking_user, school=school)
    assert list(accessible_profiles(booking_user)) == []
    with pytest.raises(Http404):
        require_access(booking_user, school.slug)
