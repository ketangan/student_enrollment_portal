from django.contrib import admin
from .models import School, Submission, SchoolAdminMembership
import csv
from django.http import HttpResponse
from core.services.config_loader import load_school_config
from core.services.form_utils import build_option_label_map


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("slug", "display_name", "website_url", "created_at")
    search_fields = ("slug", "display_name")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs

        membership = getattr(request.user, "school_membership", None)
        if not membership:
            return qs.none()

        return qs.filter(id=membership.school_id)
    
    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        membership = getattr(request.user, "school_membership", None)
        return bool(membership and obj and obj.id == membership.school_id)


@admin.register(SchoolAdminMembership)
class SchoolAdminMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "school")
    search_fields = ("user__username", "school__slug", "school__display_name")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "school", "student_name", "program_name", "created_at")
    list_filter = ("school", "created_at")

    # We'll implement search by name using get_search_results (below)
    search_fields = ("school__slug", "school__display_name")

    def student_name(self, obj: Submission) -> str:
        return obj.student_display_name()

    student_name.short_description = "Student / Applicant"

    def program_name(self, obj: Submission) -> str:
        config = load_school_config(obj.school.slug)
        if not config:
            return obj.program_display_name()

        label_map = build_option_label_map(config.form)
        return obj.program_display_name(label_map=label_map)

    def get_search_results(self, request, queryset, search_term):
        """
        Search behavior:
        - Keep default Django search (school fields)
        - ALSO search inside JSON-derived fields (student/applicant + program)
        - Must search against the ORIGINAL scoped queryset, not the already-filtered one
        """
        base_qs = queryset  # this is already scoped by get_queryset()

        # Default Django search (e.g., school name/slug)
        default_qs, use_distinct = super().get_search_results(request, queryset, search_term)

        term = (search_term or "").strip().lower()
        if not term:
            return default_qs, use_distinct

        # Search JSON-derived fields across the full scoped dataset
        candidates = list(base_qs.order_by("-created_at")[:5000])  # MVP cap
        matched_ids = [
            s.id for s in candidates
            if term in (s.student_display_name() or "").lower()
            or term in (s.program_display_name() or "").lower()
        ]

        # Union IDs: default matches OR JSON matches
        default_ids = set(default_qs.values_list("id", flat=True))
        all_ids = default_ids.union(matched_ids)

        return base_qs.filter(id__in=all_ids), use_distinct

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs

        membership = getattr(request.user, "school_membership", None)
        if not membership:
            return qs.none()

        return qs.filter(school=membership.school)
    
    actions = ["export_csv"]

    def export_csv(self, request, queryset):
        """
        Export submissions the user is allowed to see.
        Columns: created_at, student/applicant name, plus all JSON keys unioned.
        """
        # Force scoping by re-applying get_queryset rules:
        queryset = self.get_queryset(request).filter(id__in=queryset.values_list("id", flat=True))

        # Collect union of keys across selected rows
        rows = list(queryset.order_by("-created_at")[:5000])  # MVP limit
        all_keys = set()
        for s in rows:
            all_keys.update((s.data or {}).keys())

        # Put common columns first
        ordered_keys = ["created_at", "student_name"] + sorted(k for k in all_keys)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="submissions.csv"'

        writer = csv.writer(response)
        writer.writerow(ordered_keys)

        for s in rows:
            data = s.data or {}
            writer.writerow(
                [s.created_at.isoformat(), s.student_display_name()]
                + [data.get(k, "") for k in sorted(k for k in all_keys)]
            )

        return response

    export_csv.short_description = "Export selected submissions to CSV"
    