# core/admin/leads.py
from __future__ import annotations

from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Case, IntegerField, Q, Value, When
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from core.admin.common import _has_school_membership, _is_superuser, _membership_school_id
from core.models import (
    Lead,
    LEAD_STATUS_CONTACTED,
    LEAD_STATUS_ENROLLED,
    LEAD_STATUS_LOST,
    LEAD_STATUS_NEW,
    LEAD_STATUS_TRIAL_SCHEDULED,
)

_STATUS_COLORS = {
    "new": "#16a34a",             # green
    "contacted": "#2563eb",       # blue
    "trial_scheduled": "#d97706", # amber
    "enrolled": "#7c3aed",        # purple
    "lost": "#dc2626",            # red
}


def _get_day_bounds():
    """Returns (now, today_start, today_end) in the current timezone."""
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    return now, today_start, today_end


class ConvertedFilter(admin.SimpleListFilter):
    title = "Conversion"
    parameter_name = "converted"

    def lookups(self, request, model_admin):
        return [
            ("no", "Unconverted only"),
            ("yes", "Converted only"),
        ]

    def queryset(self, request, queryset):
        if self.value() == "no":
            return queryset.filter(converted_submission__isnull=True)
        if self.value() == "yes":
            return queryset.filter(converted_submission__isnull=False)
        return queryset


class FollowUpFilter(admin.SimpleListFilter):
    title = "Follow-up"
    parameter_name = "follow_up"

    def lookups(self, request, model_admin):
        return [
            ("attention", "Needs attention"),
            ("overdue",   "Overdue"),
            ("today",     "Due today"),
            ("upcoming",  "Upcoming"),
            ("none",      "Not scheduled"),
        ]

    def queryset(self, request, queryset):
        now, today_start, today_end = _get_day_bounds()

        if self.value() == "attention":
            return queryset.filter(
                Q(next_follow_up_at__lt=now) & ~Q(status=LEAD_STATUS_LOST)
                | Q(next_follow_up_at__gte=today_start, next_follow_up_at__lt=today_end)
                | Q(next_follow_up_at__isnull=True, status__in=[LEAD_STATUS_NEW, LEAD_STATUS_CONTACTED])
            )
        if self.value() == "overdue":
            return queryset.filter(next_follow_up_at__lt=now).exclude(status=LEAD_STATUS_LOST)
        if self.value() == "today":
            return queryset.filter(
                next_follow_up_at__gte=today_start,
                next_follow_up_at__lt=today_end,
            )
        if self.value() == "upcoming":
            return queryset.filter(next_follow_up_at__gte=today_end)
        if self.value() == "none":
            return queryset.filter(next_follow_up_at__isnull=True)
        return queryset


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "email",
        "status_badge",
        "status",
        "next_follow_up_display",
        "converted_badge",
        "interested_in_label",
        "notes_preview",
        "source",
        "created_at",
    )
    list_editable = ("status",)
    list_filter = ("status", "source", FollowUpFilter, ConvertedFilter)
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
    actions = [
        "action_mark_contacted",
        "action_mark_trial_scheduled",
        "action_mark_lost",
        "action_schedule_tomorrow",
        "action_schedule_next_week",
        "action_clear_follow_up",
    ]

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

    # ------------------------------------------------------------------
    # Custom URLs + quick-add view
    # ------------------------------------------------------------------

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom = [
            path(
                "quick_add/",
                self.admin_site.admin_view(self.quick_add_view),
                name="core_lead_quick_add",
            ),
        ]
        return custom + urls

    def quick_add_view(self, request):
        from django.db import IntegrityError, transaction
        from django.http import HttpResponseForbidden
        from django.shortcuts import redirect

        if request.method != "POST":
            return redirect("../")

        if not self.has_module_permission(request):
            return HttpResponseForbidden()

        if _is_superuser(request.user):
            # Superusers must use the full admin form; quick-add is staff-only.
            messages.error(request, "Superusers cannot use quick-add. Use the full admin add form.")
            return redirect("../")

        school_id = _membership_school_id(request.user)
        if not school_id:
            messages.error(request, "No school associated with your account.")
            return redirect("../")

        from core.models import School
        try:
            school = School.objects.get(pk=school_id)
        except School.DoesNotExist:
            messages.error(request, "School not found.")
            return redirect("../")

        name = (request.POST.get("name") or "").strip()
        email = (request.POST.get("email") or "").strip()

        source = (request.POST.get("source") or "").strip()

        if not name or not email or not source:
            messages.error(request, "Name, email, and source are required.")
            return redirect("../")

        try:
            with transaction.atomic():
                lead = Lead.objects.create(
                    school=school,
                    name=name,
                    email=email,
                    phone=(request.POST.get("phone") or "").strip(),
                    interested_in_label=(request.POST.get("interested_in_label") or "").strip(),
                    source=source,
                    notes=(request.POST.get("notes") or "").strip(),
                    status=LEAD_STATUS_NEW,
                )
            messages.success(request, f"Lead '{lead.name}' added.")
        except IntegrityError:
            messages.error(request, f"A lead with email '{email}' already exists for this school.")

        return redirect("../")

    # ------------------------------------------------------------------
    # Bulk actions
    # ------------------------------------------------------------------

    @admin.action(description="Mark as Contacted")
    def action_mark_contacted(self, request, queryset):
        updated = queryset.update(
            status=LEAD_STATUS_CONTACTED,
            last_contacted_at=timezone.now(),
            next_follow_up_at=timezone.now() + timedelta(days=1),
        )
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) marked as Contacted, follow-up scheduled for tomorrow.")

    @admin.action(description="Mark as Trial Scheduled")
    def action_mark_trial_scheduled(self, request, queryset):
        updated = queryset.update(status=LEAD_STATUS_TRIAL_SCHEDULED)
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) marked as Trial Scheduled.")

    @admin.action(description="Mark as Lost")
    def action_mark_lost(self, request, queryset):
        updated = queryset.update(status=LEAD_STATUS_LOST, next_follow_up_at=None)
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) marked as Lost.")

    @admin.action(description="Schedule follow-up: tomorrow")
    def action_schedule_tomorrow(self, request, queryset):
        updated = queryset.update(next_follow_up_at=timezone.now() + timedelta(days=1))
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) scheduled for tomorrow.")

    @admin.action(description="Schedule follow-up: next week")
    def action_schedule_next_week(self, request, queryset):
        updated = queryset.update(next_follow_up_at=timezone.now() + timedelta(days=7))
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) scheduled for next week.")

    @admin.action(description="Clear follow-up date")
    def action_clear_follow_up(self, request, queryset):
        updated = queryset.update(next_follow_up_at=None)
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) follow-up date cleared.")

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

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

    def next_follow_up_display(self, obj: Lead) -> str:
        if not obj.next_follow_up_at:
            return "—"
        now, today_start, today_end = _get_day_bounds()

        if obj.next_follow_up_at < now and obj.status != LEAD_STATUS_LOST:
            color = "#ef4444"
            label = f"Overdue ({obj.next_follow_up_at.strftime('%b %d')})"
        elif today_start <= obj.next_follow_up_at < today_end:
            color = "#d97706"
            label = f"Today {obj.next_follow_up_at.strftime('%H:%M')}"
        else:
            color = "#16a34a"
            label = obj.next_follow_up_at.strftime("%b %d")

        return format_html('<span style="color:{};font-weight:600">{}</span>', color, label)

    next_follow_up_display.short_description = "Follow-up"
    next_follow_up_display.admin_order_field = "next_follow_up_at"

    def notes_preview(self, obj: Lead) -> str:
        if not obj.notes:
            return "—"
        return obj.notes[:60] + ("…" if len(obj.notes) > 60 else "")

    notes_preview.short_description = "Notes"

    def converted_badge(self, obj: Lead) -> str:
        if not obj.converted_submission_id:
            return "—"
        url = reverse("admin:core_submission_change", args=[obj.converted_submission_id])
        return format_html(
            '<a href="{}" style="display:inline-block;padding:2px 8px;border-radius:999px;'
            'background:#16a34a;color:#fff;font-size:12px;font-weight:600;text-decoration:none;">'
            "Converted</a>",
            url,
        )

    converted_badge.short_description = "Converted"
    converted_badge.admin_order_field = "converted_at"

    # ------------------------------------------------------------------
    # Permissions
    # ------------------------------------------------------------------

    def has_module_permission(self, request):
        return _is_superuser(request.user) or (
            _has_school_membership(request.user) and request.user.is_staff
        )

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return _is_superuser(request.user)

    # ------------------------------------------------------------------
    # Queryset — inbox ordering + school scoping
    # ------------------------------------------------------------------

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        # School scoping
        if not _is_superuser(request.user):
            school_id = _membership_school_id(request.user)
            if not school_id:
                return qs.none()
            qs = qs.filter(school_id=school_id)

        # Inbox ordering unless user clicked a column header (?o= present)
        if not request.GET.get("o"):
            now, today_start, today_end = _get_day_bounds()
            qs = qs.annotate(
                _inbox_priority=Case(
                    # 1 — overdue, non-lost
                    When(
                        Q(next_follow_up_at__lt=now) & ~Q(status=LEAD_STATUS_LOST),
                        then=Value(1),
                    ),
                    # 2 — due today
                    When(
                        Q(next_follow_up_at__gte=today_start) & Q(next_follow_up_at__lt=today_end),
                        then=Value(2),
                    ),
                    # 3 — stale: new/contacted with no follow-up date
                    When(
                        Q(status__in=[LEAD_STATUS_NEW, LEAD_STATUS_CONTACTED])
                        & Q(next_follow_up_at__isnull=True),
                        then=Value(3),
                    ),
                    # 4 — trial scheduled OR explicitly future follow-up
                    When(
                        Q(next_follow_up_at__gte=today_end) | Q(status=LEAD_STATUS_TRIAL_SCHEDULED),
                        then=Value(4),
                    ),
                    # 5 — enrolled
                    When(status=LEAD_STATUS_ENROLLED, then=Value(5)),
                    # 6 — lost (sink to bottom)
                    When(status=LEAD_STATUS_LOST, then=Value(6)),
                    default=Value(3),
                    output_field=IntegerField(),
                )
            ).order_by("_inbox_priority", "-created_at")

        return qs

    def get_list_filter(self, request):
        filters = list(super().get_list_filter(request))
        if _is_superuser(request.user):
            filters.append("school")
        return filters
