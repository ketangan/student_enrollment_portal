"""Billing recovery is available without granting access to enrollment records."""

from django.apps import apps
from django.http import Http404
from core.services.school_permissions import get_school_membership


def billing_role(user, school):
    if not user or not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        return "owner"
    membership = get_school_membership(user, school) if user.is_staff else None
    if membership and membership.role == "owner":
        return "owner"
    if not apps.is_installed("scheduling"):
        return membership.role if membership else None
    SchedulingMembership = apps.get_model("scheduling", "SchedulingMembership")
    booking_role = SchedulingMembership.objects.filter(
        user=user, profile__school=school, profile__enabled=True, is_active=True,
    ).values_list("role", flat=True).first()
    if booking_role == "owner":
        return "owner"
    return membership.role if membership else booking_role


def require_billing_owner(request, school):
    if billing_role(request.user, school) != "owner":
        raise Http404("Page not found")
