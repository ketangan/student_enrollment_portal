# core/admin/memberships.py
from __future__ import annotations

from django.contrib import admin

from core.admin.common import _is_superuser
from core.models import SchoolAdminMembership


@admin.register(SchoolAdminMembership)
class SchoolAdminMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "school")
    search_fields = ("user__username", "school__slug", "school__display_name")
    actions = None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if _is_superuser(request.user) else qs.none()

    def has_module_permission(self, request):
        return _is_superuser(request.user)

    def has_add_permission(self, request):
        return _is_superuser(request.user)

    def has_change_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def has_delete_permission(self, request, obj=None):
        return _is_superuser(request.user)
    