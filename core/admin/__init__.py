# core/admin/__init__.py

# ✅ Provide backward-compatible imports for older tests/modules

from django.contrib import admin as admin  # so tests can do core_admin.admin.site

# Export helpers that tests import from core.admin
from .common import _is_superuser, _membership_school_id, _has_school_membership  # adjust names if needed

from .audit import log_admin_audit  # noqa: F401 (ensures AdminAuditLog admin registers)

# Existing exports you already added
from .reports import admin_reports_hub_view
from .submissions import SubmissionAdmin, PrettyJSONWidget
from .users import SchoolScopedUserAdmin, UserSuperuserForm, UserSuperuserAddForm
from .schools import SchoolAdmin
from .memberships import SchoolAdminMembershipAdmin
from .preferences import AdminPreferenceAdmin  # noqa: F401

# ---------------------------------------------------------------------------
# Monkeypatch admin.site.has_permission to enforce School.is_active
# ---------------------------------------------------------------------------

from django.http import HttpResponseForbidden

_original_has_permission = admin.site.has_permission


def _school_aware_has_permission(request):
    """
    Override admin.site.has_permission to block inactive school admins.

    Rules:
    - Superusers: always allow
    - Non-staff: always deny
    - School admins with inactive school: deny EXCEPT billing pages and logout
    """
    # Always run original permission check first
    if not _original_has_permission(request):
        return False

    # Superusers bypass all school checks
    if _is_superuser(request.user):
        return True

    # Non-superuser staff: check if they have a school membership
    school_id = _membership_school_id(request.user)
    if not school_id:
        # Staff without school membership (shouldn't happen, but allow Django's default)
        return True

    # Resolve the school
    from core.models import School
    try:
        school = School.objects.get(id=school_id)
    except School.DoesNotExist:
        return False

    # If school is inactive, block access EXCEPT billing and logout
    if not school.is_active:
        path = request.path
        # Allow billing pages (so they can re-subscribe) and logout
        if path.startswith('/admin/billing') or path.startswith('/admin/logout'):
            return True
        # Block everything else
        return False

    # School is active, allow access
    return True


# Install the monkeypatch
admin.site.has_permission = _school_aware_has_permission

__all__ = [
    # django admin module shim
    "admin",

    # helper shims
    "_is_superuser",
    "_membership_school_id",
    "_has_school_membership",

    # views/admin classes
    "admin_reports_hub_view",
    "SubmissionAdmin",
    "PrettyJSONWidget",
    "SchoolScopedUserAdmin",
    "UserSuperuserForm",
    "UserSuperuserAddForm",
    "SchoolAdmin",
    "SchoolAdminMembershipAdmin",
]