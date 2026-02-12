# core/admin/audit.py
from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.utils.html import format_html

from core.admin.common import _is_superuser
from core.models import AdminAuditLog


def _get_ip(request) -> str | None:
    # If you later add a proxy/load balancer header, you can extend this safely.
    return request.META.get("REMOTE_ADDR")


def log_admin_audit(
    *,
    request,
    action: str,
    obj=None,
    changes: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
):
    """
    Level 1 audit log:
    - who did it (actor)
    - what model/object
    - add/change/delete/action
    - optional changes diff and metadata
    """
    model_label = ""
    object_id = ""
    object_repr = ""

    if obj is not None:
        model_label = f"{obj._meta.app_label}.{obj._meta.model_name}"
        object_id = str(getattr(obj, "pk", "") or "")
        try:
            object_repr = str(obj)
        except Exception:
            object_repr = ""

    AdminAuditLog.objects.create(
        actor=getattr(request, "user", None) if getattr(request, "user", None) and request.user.is_authenticated else None,
        action=action,
        model_label=model_label,
        object_id=object_id,
        object_repr=object_repr,
        changes=changes or {},
        extra=extra or {},
        path=getattr(request, "path", "") or "",
        ip_address=_get_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "") or "",
    )


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    actions = None
    list_filter = ("action", "model_label", "created_at")
    search_fields = ("object_id", "object_repr", "actor__username", "actor__email", "path")
    list_display = ("created_at", "action", "model_label", "object_id", "actor", "short_path")
    readonly_fields = (
        "created_at",
        "actor",
        "action",
        "model_label",
        "object_id",
        "object_repr",
        "path",
        "ip_address",
        "user_agent",
        "changes",
        "extra",
    )

    def has_module_permission(self, request):
        return _is_superuser(request.user)

    def has_view_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def short_path(self, obj: AdminAuditLog):
        if not obj.path:
            return ""
        p = obj.path
        if len(p) > 42:
            p = p[:39] + "…"
        return format_html("<code>{}</code>", p)

    short_path.short_description = "Path"
    