# core/admin/__init__.py

# ✅ Provide backward-compatible imports for older tests/modules

from django.contrib import admin as admin  # so tests can do core_admin.admin.site

# Export helpers that tests import from core.admin
from .common import _is_superuser, _membership_school_id, _has_school_membership  # adjust names if needed

# Existing exports you already added
from .reports import admin_reports_hub_view
from .submissions import SubmissionAdmin, PrettyJSONWidget
from .users import SchoolScopedUserAdmin, UserSuperuserForm, UserSuperuserAddForm
from .schools import SchoolAdmin
from .memberships import SchoolAdminMembershipAdmin

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