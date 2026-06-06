from django.contrib import admin
from core.models import SchoolProgram


@admin.register(SchoolProgram)
class SchoolProgramAdmin(admin.ModelAdmin):
    list_display = [
        "school", "name", "code", "capacity", "auto_enroll",
        "waitlist_enabled", "is_active", "display_order", "created_at",
    ]
    list_filter = ["school", "is_active", "auto_enroll", "waitlist_enabled"]
    search_fields = ["name", "code", "school__slug"]
    ordering = ["school__slug", "display_order", "name"]
    readonly_fields = ["created_at", "updated_at"]
