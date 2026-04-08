# core/admin/drafts.py
from django.contrib import admin
from django.utils.formats import date_format
from django.utils import timezone

from core.admin.common import _is_superuser
from core.models import DraftSubmission


@admin.register(DraftSubmission)
class DraftSubmissionAdmin(admin.ModelAdmin):
    list_display = (
        "school", "email", "form_key", "last_form_key",
        "created_at_pretty", "updated_at_pretty", "token_expires_at_pretty",
        "submitted_at_pretty",
    )
    list_filter = ("school", "form_key")
    readonly_fields = (
        "school", "form_key", "data", "token", "token_expires_at",
        "email", "last_form_key", "last_email_sent_at",
        "submitted_at", "created_at", "updated_at",
    )
    search_fields = ("email", "school__slug", "token")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def has_module_permission(self, request):
        return _is_superuser(request.user)

    def has_view_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def created_at_pretty(self, obj):
        return date_format(timezone.localtime(obj.created_at), "N j, Y, P")
    created_at_pretty.short_description = "Created"
    created_at_pretty.admin_order_field = "created_at"

    def updated_at_pretty(self, obj):
        return date_format(timezone.localtime(obj.updated_at), "N j, Y, P")
    updated_at_pretty.short_description = "Updated"
    updated_at_pretty.admin_order_field = "updated_at"

    def token_expires_at_pretty(self, obj):
        return date_format(timezone.localtime(obj.token_expires_at), "N j, Y, P")
    token_expires_at_pretty.short_description = "Expires"
    token_expires_at_pretty.admin_order_field = "token_expires_at"

    def submitted_at_pretty(self, obj):
        if not obj.submitted_at:
            return "—"
        return date_format(timezone.localtime(obj.submitted_at), "N j, Y, P")
    submitted_at_pretty.short_description = "Submitted"
    submitted_at_pretty.admin_order_field = "submitted_at"
