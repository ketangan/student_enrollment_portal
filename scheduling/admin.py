from django.contrib import admin
from core.admin.portal_scope import PortalScopedAdminMixin

from .models import SchedulingMembership, SchedulingProfile


class OperatorOnlyAdmin(PortalScopedAdminMixin, admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return self.has_module_permission(request)


@admin.register(SchedulingProfile)
class SchedulingProfileAdmin(OperatorOnlyAdmin):
    list_display = ("school", "enabled")
    list_filter = ("enabled",)
    search_fields = ("school__slug", "school__display_name")
    list_select_related = ("school",)
    raw_id_fields = ("school",)
    readonly_fields = ("capabilities", "payment_provider", "calendar_enabled", "default_dashboard_view")


@admin.register(SchedulingMembership)
class SchedulingMembershipAdmin(OperatorOnlyAdmin):
    portal_school_lookup = "profile__school__"
    list_display = ("user", "profile", "role", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("user__username", "profile__school__slug")
    list_select_related = ("user", "profile__school")
    raw_id_fields = ("user", "profile")
