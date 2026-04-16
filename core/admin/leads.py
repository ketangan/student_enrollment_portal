# core/admin/leads.py
from __future__ import annotations

import logging
from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Case, IntegerField, Q, Value, When
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

logger = logging.getLogger(__name__)

from core.admin.audit import log_admin_audit
from core.admin.common import _has_school_membership, _is_superuser, _membership_school_id
from core.models import (
    Lead,
    LEAD_STATUS_CONTACTED,
    LEAD_STATUS_ENROLLED,
    LEAD_STATUS_LOST,
    LEAD_STATUS_NEW,
    LEAD_STATUS_TRIAL_SCHEDULED,
)


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
        "status",
        "next_follow_up_display",
        "converted_badge",
        "interested_in_display",
        "notes_preview",
        "source",
        "created_at",
    )
    list_editable = ("status",)
    list_filter = ("status",)
    search_fields = ("name", "email", "phone")
    readonly_fields = (
        "school_display",
        "public_id",
        "converted_submission",
        "converted_at",
        "convert_to_submission_button",
        "created_at",
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
        ("Lead Details", {
            "fields": (
                "school_display", "public_id", "name", "email", "phone",
                "interested_in_label", "source",
                "status", "notes", "last_contacted_at", "next_follow_up_at", "lost_reason",
                "converted_submission", "converted_at", "convert_to_submission_button",
                "created_at",
            ),
        }),
    )

    # ------------------------------------------------------------------
    # Form — combine split date/time widget into a single datetime-local input
    # ------------------------------------------------------------------

    def get_form(self, request, obj=None, **kwargs):
        from django.forms import DateTimeField, DateTimeInput
        form = super().get_form(request, obj, **kwargs)
        for fname in ("last_contacted_at", "next_follow_up_at"):
            if fname in form.base_fields:
                existing = form.base_fields[fname]
                # Replace SplitDateTimeField (which expects a [date, time] list) with a
                # plain DateTimeField so a single datetime-local input works correctly.
                form.base_fields[fname] = DateTimeField(
                    required=existing.required,
                    label=existing.label,
                    help_text=existing.help_text,
                    widget=DateTimeInput(
                        attrs={"type": "datetime-local", "style": "width:220px;"},
                        format="%Y-%m-%dT%H:%M",
                    ),
                    input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"],
                )
        return form

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
            path(
                "<int:lead_id>/convert-to-submission/",
                self.admin_site.admin_view(self.convert_to_submission_view),
                name="core_lead_convert_to_submission",
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

        if school.is_trial_expired:
            messages.error(request, "Your trial has expired. Upgrade to continue adding leads.")
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
            # Intentional omission: failed creates (IntegrityError) are NOT logged
            # because no Lead object exists to reference. Future: log to extra with no obj.
            if lead.school.features.audit_log_enabled:
                log_admin_audit(
                    request=request,
                    action="add",
                    obj=lead,
                    extra={"name": "quick_add", "source": lead.source},
                )
        except IntegrityError:
            messages.error(request, f"A lead with email '{email}' already exists for this school.")

        return redirect("../")

    def convert_to_submission_view(self, request, lead_id):
        from django.db import transaction
        from django.http import HttpResponseNotAllowed
        from django.shortcuts import redirect
        from core.models import Submission
        from core.services.config_loader import load_school_config
        from core.services.lead_conversion import try_convert_lead
        from core.services.notifications import _find_email_field_key

        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])

        lead_change_url = reverse("admin:core_lead_change", args=[lead_id])
        changelist_url = reverse("admin:core_lead_changelist")

        # Fast pre-flight load — non-locking, for early UX redirect only.
        try:
            lead = self.get_queryset(request).get(pk=lead_id)
        except Lead.DoesNotExist:
            messages.error(request, "Lead not found.")
            return redirect(changelist_url)

        # Pre-check idempotency (non-authoritative; real check is under the row lock below).
        if lead.converted_submission_id:
            messages.warning(request, "This lead has already been converted to a submission.")
            return redirect(lead_change_url)

        # Block conversion for expired-trial schools.
        if lead.school.is_trial_expired:
            messages.error(request, "Your trial has expired. Upgrade to convert leads to submissions.")
            return redirect(lead_change_url)

        with transaction.atomic():
            # Re-fetch with row lock — authoritative race-safe check.
            try:
                lead = self.get_queryset(request).select_for_update().get(pk=lead_id)
            except Lead.DoesNotExist:
                messages.error(request, "Lead not found.")
                return redirect(changelist_url)

            # Authoritative idempotency check under lock.
            if lead.converted_submission_id:
                messages.warning(request, "This lead has already been converted to a submission.")
                return redirect(lead_change_url)

            cfg = load_school_config(lead.school.slug)
            config_raw = getattr(cfg, "raw", {}) or {}

            # Resolve the YAML email field key before creating the submission.
            # try_convert_lead matches by whichever key is declared as type=email in
            # the YAML — the submission data must use that same key.
            email_key = _find_email_field_key(config_raw)
            if not email_key:
                logger.warning(
                    "convert_to_submission: no type=email field in YAML for school=%s lead=%s",
                    lead.school.slug,
                    lead.pk,
                )
                messages.error(
                    request,
                    "Conversion is not available for this school. Please contact support.",
                )
                return redirect(lead_change_url)

            # Create minimal submission keyed to the school's YAML email field.
            # Phone is seeded under the conventional key as enrichment; extra keys
            # in submission.data are harmless if unused by this school's YAML.
            submission = Submission.objects.create(
                school=lead.school,
                data={
                    email_key: lead.email,
                    "contact_phone": lead.phone or "",
                },
            )

            # Attempt conversion via existing logic — matches by YAML email field.
            matched = try_convert_lead(
                school=lead.school,
                submission=submission,
                config_raw=config_raw,
            )

            # Critical identity validation.
            # try_convert_lead matches by normalized email, not by pk.
            # Returns None if leads_conversion_enabled is off for this school's plan.
            # The matched.pk check is a defensive integrity guard — abort and clean up
            # if conversion returned an unexpected result for any reason.
            # on_delete=SET_NULL: deleting the submission auto-nulls any lead FK already set.
            if not matched or matched.pk != lead.pk:
                logger.warning(
                    "convert_to_submission: failed for lead=%s school=%s matched=%s",
                    lead.pk,
                    lead.school.slug,
                    matched.pk if matched else None,
                )
                submission.delete()
                messages.error(
                    request,
                    "Conversion could not be completed. Please contact support.",
                )
                return redirect(lead_change_url)

            if lead.school.features.audit_log_enabled:
                log_admin_audit(
                    request=request,
                    action="action",
                    obj=lead,
                    changes={},
                    extra={"name": "convert_to_submission"},
                )

        messages.success(
            request,
            f'Lead "{lead.name}" successfully converted. Submission #{submission.pk} created.',
        )
        return redirect(reverse("admin:core_submission_change", args=[submission.pk]))

    # ------------------------------------------------------------------
    # Save pipeline
    # ------------------------------------------------------------------

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["show_save_and_continue"] = False
        return super().changeform_view(request, object_id, form_url, extra_context)

    # ------------------------------------------------------------------

    def save_model(self, request, obj, form, change):
        old_status = None
        if change and obj.pk:
            old_status = Lead.objects.filter(pk=obj.pk).values_list("status", flat=True).first()

        super().save_model(request, obj, form, change)

        if not obj.school.features.audit_log_enabled:
            return

        if not change:
            # has_add_permission is False so this branch is currently unreachable
            # via the normal admin form. Kept for defensive completeness.
            log_admin_audit(request=request, action="add", obj=obj, changes={})
            return

        # Only log status changes — notes/follow-up/lost_reason edits are v1 out of scope.
        # No empty audit rows on saves where status did not change.
        if old_status is not None and old_status != obj.status:
            log_admin_audit(
                request=request,
                action="change",
                obj=obj,
                changes={"status": {"from": old_status, "to": obj.status}},
            )

    # ------------------------------------------------------------------
    # Bulk actions
    # ------------------------------------------------------------------

    @admin.action(description="Mark as Contacted")
    def action_mark_contacted(self, request, queryset):
        updated = queryset.update(
            status=LEAD_STATUS_CONTACTED,
            last_contacted_at=timezone.now(),
        )
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) marked as Contacted.")
        # queryset is already school-scoped via get_queryset(); first() is safe for feature flag check
        first = queryset.first()
        if first and first.school.features.audit_log_enabled:
            log_admin_audit(
                request=request, action="action", obj=None,
                extra={"name": "mark_contacted", "count": updated},
            )

    @admin.action(description="Mark as Trial Scheduled")
    def action_mark_trial_scheduled(self, request, queryset):
        updated = queryset.update(status=LEAD_STATUS_TRIAL_SCHEDULED)
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) marked as Trial Scheduled.")
        first = queryset.first()
        if first and first.school.features.audit_log_enabled:
            log_admin_audit(
                request=request, action="action", obj=None,
                extra={"name": "mark_trial_scheduled", "count": updated},
            )

    @admin.action(description="Mark as Lost")
    def action_mark_lost(self, request, queryset):
        updated = queryset.update(status=LEAD_STATUS_LOST, next_follow_up_at=None)
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) marked as Lost.")
        first = queryset.first()
        if first and first.school.features.audit_log_enabled:
            log_admin_audit(
                request=request, action="action", obj=None,
                extra={"name": "mark_lost", "count": updated},
            )

    @admin.action(description="Schedule follow-up: tomorrow")
    def action_schedule_tomorrow(self, request, queryset):
        updated = queryset.update(next_follow_up_at=timezone.now() + timedelta(days=1))
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) scheduled for tomorrow.")
        first = queryset.first()
        if first and first.school.features.audit_log_enabled:
            log_admin_audit(
                request=request, action="action", obj=None,
                extra={"name": "schedule_tomorrow", "count": updated},
            )

    @admin.action(description="Schedule follow-up: next week")
    def action_schedule_next_week(self, request, queryset):
        updated = queryset.update(next_follow_up_at=timezone.now() + timedelta(days=7))
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) scheduled for next week.")
        first = queryset.first()
        if first and first.school.features.audit_log_enabled:
            log_admin_audit(
                request=request, action="action", obj=None,
                extra={"name": "schedule_next_week", "count": updated},
            )

    @admin.action(description="Clear follow-up date")
    def action_clear_follow_up(self, request, queryset):
        updated = queryset.update(next_follow_up_at=None)
        if not updated:
            self.message_user(request, "No leads were updated.", level=messages.WARNING)
            return
        self.message_user(request, f"{updated} lead(s) follow-up date cleared.")
        first = queryset.first()
        if first and first.school.features.audit_log_enabled:
            log_admin_audit(
                request=request, action="action", obj=None,
                extra={"name": "clear_follow_up", "count": updated},
            )

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------

    def school_display(self, obj: Lead) -> str:
        return obj.school.display_name if obj.school_id else "—"

    school_display.short_description = "School"

    def interested_in_display(self, obj: Lead) -> str:
        return obj.interested_in_label or "—"

    interested_in_display.short_description = "Interested In"
    interested_in_display.admin_order_field = "interested_in_label"

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

    def convert_to_submission_button(self, obj: Lead):
        """
        Shown in the Lead change form.
        - Unconverted lead: POST form button → convert_to_submission_view
        - Already converted: link to existing Submission
        The CSRF token is read from the parent change form's hidden input via JS
        at submit time — the display method has no access to request.
        """
        if not obj or not obj.pk:
            return "—"

        if obj.converted_submission_id:
            url = reverse("admin:core_submission_change", args=[obj.converted_submission_id])
            return format_html(
                '<span style="display:inline-flex;align-items:center;gap:10px;">'
                '<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
                'background:#16a34a;color:#fff;font-size:12px;font-weight:600;">Converted</span>'
                '<a href="{}" style="font-size:13px;color:#2563eb;font-weight:500;'
                'text-decoration:none;">View Submission →</a>'
                '</span>',
                url,
            )

        convert_url = reverse("admin:core_lead_convert_to_submission", args=[obj.pk])
        # Avoid nested <form> (invalid HTML; browsers handle it inconsistently and
        # onsubmit on the inner form never fires reliably). Instead: build a fresh
        # form via JS, read the CSRF token from the csrftoken cookie, and submit.
        return format_html(
            '<button type="button"'
            ' style="padding:4px 12px;background:#2563eb;color:#fff;border:none;'
            'border-radius:4px;cursor:pointer;font-size:13px;font-weight:600;"'
            " onclick=\"var f=document.createElement('form');f.method='POST';f.action='{}';"
            "var i=document.createElement('input');i.type='hidden';i.name='csrfmiddlewaretoken';"
            "var m=document.cookie.match(/(?:^|;\\s*)csrftoken=([^;]*)/);i.value=m?decodeURIComponent(m[1]):'';"
            "f.appendChild(i);document.body.appendChild(f);f.submit();\">"
            "Convert to Submission"
            "</button>",
            convert_url,
        )

    convert_to_submission_button.short_description = "Convert"

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
        if _is_superuser(request.user):
            return True
        if not (_has_school_membership(request.user) and request.user.is_staff):
            return False
        school_id = _membership_school_id(request.user)
        if not school_id:
            return False
        from core.models import School
        school = School.objects.filter(pk=school_id).first()
        return school is not None and school.features.leads_enabled

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        if not self.has_module_permission(request):
            return False
        # Expired-trial school admins get view-only access — no writes.
        if not _is_superuser(request.user) and obj is not None:
            school = getattr(obj, "school", None)
            if school and school.is_trial_expired:
                return False
        return True

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
