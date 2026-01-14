from django.contrib import admin
from .models import School, Submission


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("slug", "display_name", "website_url", "created_at")
    search_fields = ("slug", "display_name")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "school", "created_at")
    list_filter = ("school", "created_at")
    search_fields = ("school__slug", "school__display_name")
    