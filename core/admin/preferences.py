# core/admin/preferences.py
from __future__ import annotations

from django.contrib import admin

from core.admin.common import _is_superuser
from core.models import AdminPreference


@admin.register(AdminPreference)
class AdminPreferenceAdmin(admin.ModelAdmin):
    """Read-only view for superusers to see/debug user theme choices.

    Hidden from the sidebar via JAZZMIN_SETTINGS["hide_models"],
    but still accessible at /admin/core/adminpreference/.
    """

    list_display = ("user", "theme")
    list_filter = ("theme",)
    search_fields = ("user__username",)
    readonly_fields = ("user",)

    def has_module_permission(self, request):
        return _is_superuser(request.user)

    def has_view_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def has_change_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def has_add_permission(self, request):
        return False  # created automatically via the theme picker

    def has_delete_permission(self, request, obj=None):
        return _is_superuser(request.user)
