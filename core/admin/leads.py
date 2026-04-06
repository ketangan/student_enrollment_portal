# core/admin/leads.py
from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from core.admin.common import _has_school_membership, _is_superuser, _membership_school_id
from core.models import Lead

_STATUS_COLORS = {
    "new": "#16a34a",            # green
    "contacted": "#2563eb",      # blue
    "trial_scheduled": "#d97706", # amber
    "enrolled": "#7c3aed",       # purple
    "lost": "#dc2626",           # red
}


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "email",
        "phone",
        "interested_in_label",
        "status_badge",
        "source",
        "created_at",
    )
    list_filter = ("status", "source")
    search_fields = ("name", "email", "phone")
    readonly_fields = (
        "public_id",
        "normalized_email",
        "normalized_phone",
        "converted_submission",
        "converted_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    fieldsets = (
        ("Identity", {
            "fields": ("school", "public_id", "name", "email", "phone", "normalized_email", "normalized_phone"),
        }),
        ("Interest", {
            "fields": ("interested_in_label", "interested_in_value"),
        }),
        ("Attribution", {
            "fields": ("source", "utm_source", "utm_medium", "utm_campaign"),
        }),
        ("Pipeline", {
            "fields": ("status", "notes", "last_contacted_at", "next_follow_up_at", "lost_reason"),
        }),
        ("Conversion", {
            "fields": ("converted_submission", "converted_at"),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
        }),
    )

    def status_badge(self, obj: Lead) -> str:
        color = _STATUS_COLORS.get(obj.status, "#6b7280")
        label = obj.get_status_display()
        return format_html(
            '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
            'background:{};color:#fff;font-size:12px;font-weight:600;">{}</span>',
            color,
            label,
        )

    status_badge.short_description = "Status"

    def has_module_permission(self, request):
        return _is_superuser(request.user) or (
            _has_school_membership(request.user) and request.user.is_staff
        )

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        # Leads are created via the capture form only
        return False

    def has_delete_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if _is_superuser(request.user):
            return qs
        school_id = _membership_school_id(request.user)
        if not school_id:
            return qs.none()
        return qs.filter(school_id=school_id)

    def get_list_filter(self, request):
        filters = list(super().get_list_filter(request))
        if _is_superuser(request.user):
            filters.append("school")
        return filters
