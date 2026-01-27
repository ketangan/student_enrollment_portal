# core/admin/schools.py
from __future__ import annotations

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from core.admin.common import _has_school_membership, _is_superuser, _membership_school_id
from core.models import School


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("slug", "display_name", "website_url", "created_at", "reports_link")
    search_fields = ("slug", "display_name")
    readonly_fields = ("reports_link",)

    def has_module_permission(self, request):
        return _is_superuser(request.user) or (_has_school_membership(request.user) and request.user.is_staff)

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def has_add_permission(self, request):
        return _is_superuser(request.user)

    def has_delete_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if _is_superuser(request.user):
            return qs
        school_id = _membership_school_id(request.user)
        if not school_id:
            return qs.none()
        return qs.filter(id=school_id)

    def reports_link(self, obj: School):
        if not obj or not obj.slug:
            return ""
        url = reverse("school_reports", kwargs={"school_slug": obj.slug})
        return format_html(
            """
            <a href="{url}" target="_blank"
            style="
                display:inline-block;
                padding:6px 12px;
                border-radius:10px;
                background:#2563eb;
                color:#fff;
                font-weight:600;
                text-decoration:none;
                border:1px solid rgba(255,255,255,0.15);
            ">
            Reports
            </a>
            """,
            url=url,
        )

    reports_link.short_description = "Reports"
    