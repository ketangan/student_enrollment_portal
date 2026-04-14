# core/admin/submissions.py
from __future__ import annotations

import csv
import json
import logging
from collections import Counter

logger = logging.getLogger(__name__)

from django import forms
from django.core.exceptions import PermissionDenied
from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import escape
from django.utils.safestring import mark_safe
from core.admin.audit import log_admin_audit
from copy import deepcopy

from core.admin.common import (
    DYN_PREFIX,
    _bytes_to_mb,
    _has_school_membership,
    _is_superuser,
    _membership_school_id,
    _resolve_submission_form_cfg_and_labels,
)
from core.models import Submission, SubmissionFile
from core.services.admin_submission_yaml import (
    apply_post_to_submission_data,
    build_yaml_sections,
    get_submission_status_choices,
    validate_required_fields,
)
from core.services.ai_summary import generate_ai_summary
from core.services.config_loader import load_school_config
from core.services.form_utils import build_option_label_map


class PrettyJSONWidget(forms.Textarea):
    def format_value(self, value):
        if value in (None, "", {}):
            return ""
        try:
            if isinstance(value, str):
                value = json.loads(value)
            return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
        except Exception:
            return super().format_value(value)


class SubmissionAdminForm(forms.ModelForm):
    class Meta:
        model = Submission
        fields = "__all__"
        widgets = {
            "data": PrettyJSONWidget(
                attrs={
                    "rows": 18,
                    "style": (
                        "font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "
                        "'Liberation Mono', 'Courier New', monospace; white-space: pre;"
                    ),
                }
            )
        }


class SubmissionFileInline(admin.TabularInline):
    model = SubmissionFile
    extra = 0
    fields = ("field_key", "file", "original_name", "content_type", "size_bytes", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    class Media:
        css = {"all": ("admin/submission_yaml_form.css",)}
        js = ("admin_actions.js",)

    form = SubmissionAdminForm
    inlines = [SubmissionFileInline]
    actions = ["export_csv"]
    object_history_template = "admin/core/submission/object_history.html"

    # yaml_form is rendered HTML (readonly method), data is readonly (still shown collapsed)
    readonly_fields = ("application_number", "public_id", "school_display", "created_at_pretty", "yaml_form", "data", "attachments", "ai_summary_display")
    search_fields = ("public_id", "school__slug", "school__display_name")
    list_filter = ("school",)

    def get_list_filter(self, request):
        # Superusers see all schools — school filter is meaningful.
        if _is_superuser(request.user):
            return ("status", "school")

        # Staff are scoped to one school via get_queryset — no school filter needed.
        base = []
        school_id = _membership_school_id(request.user)
        if school_id:
            from core.models import School
            school = School.objects.filter(id=school_id).first()
            if school and school.features.status_enabled:
                base = ["status"]
        return tuple(base)

    def get_list_display(self, request):
        if _is_superuser(request.user):
            return ("application_number", "status", "school_display", "student_name", "program_name", "created_at_pretty")

        cols = ["application_number", "student_name", "program_name", "created_at_pretty"]

        school_id = _membership_school_id(request.user)
        if school_id:
            from core.models import School
            school = School.objects.filter(id=school_id).first()
            if school and school.features.status_enabled:
                cols.insert(1, "status")

        return tuple(cols)

    def get_fieldsets(self, request, obj=None):
        general_fields = ["application_number", "public_id", "school_display", "created_at_pretty", "yaml_form"]

        if _is_superuser(request.user) or (obj and obj.school and obj.school.features.status_enabled):
            general_fields.insert(1, "status")

        fieldsets = [
            ("General", {"fields": tuple(general_fields)}),
            ("Raw Data (advanced)", {"fields": ("data",), "classes": ("collapse",)}),
            ("Attachments", {"fields": ("attachments",)}),
        ]

        if obj and obj.school and obj.school.features.ai_summary_enabled:
            fieldsets.insert(1, ("AI Summary", {"fields": ("ai_summary_display",)}))

        return tuple(fieldsets)

    def log_change(self, request, obj, message):
        old = getattr(request, "_old_submission_data", None)
        new = getattr(obj, "data", None) or {}

        # If we captured a snapshot, summarize the JSON-level changes nicely
        if old is not None:
            changed_keys = []
            for k in set(old.keys()) | set(new.keys()):
                if old.get(k) != new.get(k):
                    changed_keys.append(k)

            if changed_keys:
                label_map = {}
                if getattr(obj, "school_id", None):
                    cfg = load_school_config(obj.school.slug)
                    if cfg:
                        _, label_map = _resolve_submission_form_cfg_and_labels(cfg, getattr(obj, "form_key", None))
                pretty = [label_map.get(k, k.replace("_", " ").title()) for k in sorted(changed_keys)]
                message = "Updated: " + ", ".join(pretty)
            # else:
            #     return  # No changes to log

        return super().log_change(request, obj, message)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        # If status feature is off for this school, remove the field entirely.
        # Superusers always see the full form regardless of the school's plan.
        if not _is_superuser(request.user) and obj and obj.school and not obj.school.features.status_enabled:
            form.base_fields.pop("status", None)
            return form

        # Guard: if status isn't on the form for any reason, don't crash
        if "status" not in form.base_fields:
            return form

        statuses = None
        default_status = None
        if obj and obj.school and (_is_superuser(request.user) or obj.school.features.custom_statuses_enabled):
            cfg = load_school_config(obj.school.slug)
            raw = getattr(cfg, "raw", {}) or {}
            statuses, default_status = get_submission_status_choices(raw)

        statuses = statuses or ["New", "In Review", "Contacted", "Archived"]
        default_status = default_status or "New"

        choices = [(s, s) for s in statuses]

        field = form.base_fields["status"]
        field.choices = choices
        field.widget = forms.Select(choices=choices)

        if not obj or not getattr(obj, "status", ""):
            field.initial = default_status

        return form
    
    # ----------------------------
    # Permissions
    # ----------------------------
    def has_module_permission(self, request):
        return _is_superuser(request.user) or (_has_school_membership(request.user) and request.user.is_staff)

    def has_view_permission(self, request, obj=None):
        # LIST VIEW
        if obj is None:
            return (
                _is_superuser(request.user)
                or (_has_school_membership(request.user) and request.user.is_staff)
            )

        # OBJECT VIEW
        if _is_superuser(request.user):
            return True

        school_id = getattr(request.user.school_membership, "school_id", None)
        return obj.school_id == school_id


    def has_change_permission(self, request, obj=None):
        return self.has_view_permission(request, obj)

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
        return qs.filter(school_id=school_id)
    
    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, object_id)

        if obj is None:
            # Django will turn this into a 404
            raise PermissionDenied

        if not _is_superuser(request.user):
            school_id = _membership_school_id(request.user)
            if obj.school_id != school_id:
                raise PermissionDenied

        extra_context = extra_context or {}
        if obj and obj.school:
            extra_context["ai_summary_enabled"] = obj.school.features.ai_summary_enabled

        return super().change_view(request, object_id, form_url, extra_context)

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom = [
            path(
                "<int:pk>/generate-summary/",
                self.admin_site.admin_view(self.generate_summary_view),
                name="core_submission_generate_summary",
            ),
        ]
        return custom + urls

    def generate_summary_view(self, request, pk):
        if request.method != "POST":
            return redirect(reverse("admin:core_submission_change", args=[pk]))

        obj = get_object_or_404(Submission, pk=pk)

        if not self.has_change_permission(request, obj):
            raise PermissionDenied

        # Mirror change_view's explicit school-scope check (defence-in-depth)
        if not _is_superuser(request.user):
            if obj.school_id != _membership_school_id(request.user):
                raise PermissionDenied

        if not obj.school.features.ai_summary_enabled:
            messages.error(request, "AI summary is not available for this school's plan.")
            return redirect(reverse("admin:core_submission_change", args=[pk]))

        cfg = load_school_config(obj.school.slug)
        form_cfg: dict = {}
        criteria: list = []
        school_name = obj.school.slug

        if cfg:
            school_name = getattr(cfg, "display_name", school_name) or school_name
            raw_cfg = getattr(cfg, "raw", {}) or {}
            ai_cfg = raw_cfg.get("ai_summary") or {}
            criteria = list(ai_cfg.get("criteria") or [])
            resolved, _ = _resolve_submission_form_cfg_and_labels(cfg, obj.form_key)
            form_cfg = resolved or {}

        was_regeneration = bool(obj.ai_summary)
        result, error = generate_ai_summary(
            submission_data=obj.data or {},
            school_name=school_name,
            form_cfg=form_cfg,
            criteria=criteria,
        )

        if result is not None:
            obj.ai_summary = result
            obj.ai_summary_at = timezone.now()
            obj.save(update_fields=["ai_summary", "ai_summary_at"])
            log_admin_audit(
                request=request,
                action="action",
                obj=obj,
                extra={"name": "regenerate_ai_summary" if was_regeneration else "generate_ai_summary"},
            )
            messages.success(request, "AI summary generated.")
        else:
            logger.warning(
                "AI summary generation failed for submission %s: %s", pk, error
            )
            messages.error(request, f"Could not generate summary. {error}")

        return redirect(reverse("admin:core_submission_change", args=[pk]))

    # ----------------------------
    # Display helpers
    # ----------------------------
    def application_number(self, obj: Submission) -> str:
        n = obj.school_submission_number
        return f"#{n}" if n is not None else "—"

    application_number.short_description = "Application #"
    application_number.admin_order_field = "school_submission_number"

    def school_display(self, obj: Submission) -> str:
        if not obj or not obj.school_id:
            return ""
        return obj.school.display_name or obj.school.slug

    school_display.short_description = "School"

    def created_at_pretty(self, obj: Submission) -> str:
        dt = timezone.localtime(obj.created_at)
        return date_format(dt, "N j, Y, P")

    created_at_pretty.short_description = "Created at"
    created_at_pretty.admin_order_field = "created_at"

    def student_name(self, obj: Submission) -> str:
        return obj.student_display_name()

    student_name.short_description = "Student / Applicant"

    def program_name(self, obj: Submission) -> str:
        config = load_school_config(obj.school.slug)
        if not config:
            return obj.program_display_name()
        label_map = build_option_label_map(config.form)
        return obj.program_display_name(label_map=label_map)

    program_name.short_description = "Program"

    # ----------------------------
    # YAML form rendering
    # ----------------------------
    def yaml_form(self, obj):
        if not obj or not obj.school_id:
            return "—"

        cfg = load_school_config(obj.school.slug)
        if not cfg:
            return "No config found for this school."

        form_cfg, _ = _resolve_submission_form_cfg_and_labels(cfg, getattr(obj, "form_key", None))
        if not form_cfg:
            return "No form config found."

        post_data = getattr(self, "_yaml_post_data", None)
        yaml_sections = build_yaml_sections(cfg, obj.data or {}, post_data=post_data, form=form_cfg)

        html = render_to_string(
            "admin/core/submission/_yaml_form.html",
            {"yaml_sections": yaml_sections, "DYN_PREFIX": DYN_PREFIX},
        )
        return mark_safe(html)
    yaml_form.short_description = ""

    def ai_summary_display(self, obj):
        if not obj or not obj.pk or not obj.school or not obj.school.features.ai_summary_enabled:
            return "—"

        if not obj.ai_summary:
            return mark_safe(
                '<span style="color:#6b7280;font-style:italic;">'
                'No summary yet. Click "Generate AI Summary" above to create one.'
                "</span>"
            )

        summary_data = obj.ai_summary
        if not isinstance(summary_data, dict):
            return mark_safe('<span style="color:#6b7280;font-style:italic;">Summary data is malformed.</span>')

        summary_text = escape(str(summary_data.get("summary", "")))
        criteria_scores = summary_data.get("criteria_scores") or []
        if not isinstance(criteria_scores, list):
            criteria_scores = []

        parts = [
            '<div style="font-size:13px;line-height:1.6;">',
            f'<p style="margin:0 0 10px;">{summary_text}</p>',
        ]

        if criteria_scores:
            parts.append(
                '<div style="border-top:1px solid #e5e7eb;padding-top:10px;">'
                '<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;'
                'color:#6b7280;margin-bottom:8px;">Criteria Assessment</div>'
            )
            for score in criteria_scores:
                if not isinstance(score, dict):
                    continue
                criterion = escape(str(score.get("criterion", "")))
                assessment = escape(str(score.get("assessment", "")))
                note = escape(str(score.get("note", "")))
                note_html = (
                    f'<div style="color:#6b7280;font-size:11px;margin-top:2px;">{note}</div>'
                    if note else ""
                )
                parts.append(
                    f'<div style="margin-bottom:6px;padding:8px;background:#f9fafb;border-radius:6px;">'
                    f'<strong style="font-size:12px;">{criterion}:</strong> '
                    f'<span style="color:#374151;">{assessment}</span>'
                    f"{note_html}"
                    f"</div>"
                )
            parts.append("</div>")

        if obj.ai_summary_at:
            at_str = obj.ai_summary_at.strftime("%b %d, %Y").replace(" 0", " ")
            parts.append(
                f'<div style="color:#9ca3af;font-size:11px;margin-top:8px;">Generated {at_str}</div>'
            )

        parts.append("</div>")
        return mark_safe("".join(parts))

    ai_summary_display.short_description = "AI Summary"

    # ----------------------------
    # The save pipeline (fix success message issues)
    # ----------------------------
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        # Snapshot existing JSON before the POST mutates it
        if request.method == "POST" and object_id:
            obj_for_snapshot = self.get_object(request, object_id)
            request._old_submission_data = deepcopy(getattr(obj_for_snapshot, "data", None) or {})

        extra_context = extra_context or {}
        extra_context.update(
            {
                "show_save_and_continue": False,
                "show_save_and_add_another": False,
                "show_save_as_new": False,
            }
        )

        obj = self.get_object(request, object_id) if object_id else None

        if request.method == "POST" and obj and obj.school_id:
            cfg = load_school_config(obj.school.slug)
            if cfg:
                form_cfg, _ = _resolve_submission_form_cfg_and_labels(cfg, getattr(obj, "form_key", None))
                result = validate_required_fields(cfg, request.POST, form=form_cfg)

                if result["blocking"]:
                    for e in result["blocking"]:
                        messages.error(request, e)
                    return redirect(request.path)

                for w in result["warnings"]:
                    messages.warning(request, w)

        return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        if not obj or not obj.school_id:
            return

        cfg = load_school_config(obj.school.slug)
        if not cfg:
            return

        form_cfg, _ = _resolve_submission_form_cfg_and_labels(cfg, getattr(obj, "form_key", None))

        old_data = dict(obj.data or {})

        data = apply_post_to_submission_data(cfg, request.POST, existing_data=dict(obj.data or {}), form=form_cfg)
        Submission.objects.filter(pk=obj.pk).update(data=data)
        obj.data = data

        new_data = dict(data or {})
        changed = {}

        # Only log keys that actually changed (simple but useful)
        for k in set(old_data.keys()) | set(new_data.keys()):
            if old_data.get(k) != new_data.get(k):
                changed[k] = {"from": old_data.get(k), "to": new_data.get(k)}

        if obj.school.features.audit_log_enabled:
            log_admin_audit(
                request=request,
                action="change" if change else "add",
                obj=obj,
                changes=changed,
            )

    def get_search_results(self, request, queryset, search_term):
        base_qs = queryset
        default_qs, use_distinct = super().get_search_results(request, queryset, search_term)

        term = (search_term or "").strip().lower()
        if not term:
            return default_qs, use_distinct

        def _data_matches(data: dict, term: str) -> bool:
            if not isinstance(data, dict):
                return False

            # 🔥 explicit common keys first (keeps behavior stable with tests)
            for k in ("first_name", "last_name", "email", "phone", "program", "class_name"):
                v = data.get(k)
                if isinstance(v, str) and term in v.lower():
                    return True

            # generic scan of any string/list values in the JSON
            for v in data.values():
                if isinstance(v, str) and term in v.lower():
                    return True
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and term in item.lower():
                            return True
            return False

        candidates = list(base_qs.order_by("-created_at")[:5000])
        matched_ids = [
            s.id
            for s in candidates
            if term in (s.student_display_name() or "").lower()
            or term in (s.program_display_name() or "").lower()
            or _data_matches(s.data or {}, term)
        ]

        default_ids = set(default_qs.values_list("id", flat=True))
        all_ids = default_ids.union(matched_ids)

        return base_qs.filter(id__in=all_ids), use_distinct
    
    # ----------------------------
    # Attachments + Export
    # ----------------------------
    def get_actions(self, request):
        from core.models import School
        from core.services.integrations import get_export_configs

        actions = super().get_actions(request)

        if _is_superuser(request.user):
            # Superusers see only the default export_csv action.
            # Profile actions are school-specific: get_actions() runs before the queryset
            # is known, so there is no safe way to know which school's config applies.
            # Enumerating all schools' profiles on every page load is expensive and
            # produces a dropdown with dozens of school-scoped actions — worse UX than
            # the limitation. Superusers can use the Django shell for custom exports.
            return actions

        school_id = _membership_school_id(request.user)
        if school_id:
            school = School.objects.filter(id=school_id).first()
            if school:
                if not school.features.csv_export_enabled:
                    actions.pop("export_csv", None)
                cfg = load_school_config(school.slug)
                config_raw = getattr(cfg, "raw", {}) or {}
                used_names = set(actions.keys())
                for profile_name, field_map in get_export_configs(config_raw).items():
                    fn = self._make_integration_export_action(
                        profile_name, field_map, school_id=school.id, used_names=used_names
                    )
                    label = f"Export selected → {profile_name} CSV"
                    actions[fn.__name__] = (fn, fn.__name__, label)
                    used_names.add(fn.__name__)

        return actions

    def _make_integration_export_action(
        self, profile_name: str, field_map: dict, school_id: int, used_names: set
    ):
        """Builds a Django admin action function for one export profile.

        Action name is collision-disambiguated: if the base name (export_{slug}_csv)
        is already in used_names, appends _2, _3, etc. until unique.
        """
        from core.services.integrations import slugify_export_name

        base = f"export_{slugify_export_name(profile_name)}_csv"
        name = base
        suffix = 2
        while name in used_names:
            name = f"{base}_{suffix}"
            suffix += 1

        def action(modeladmin, request, queryset):
            return modeladmin._do_integration_export(
                request, queryset, profile_name, name, field_map, expected_school_id=school_id
            )

        action.__name__ = name
        return action

    def _do_integration_export(
        self, request, queryset, profile_name, action_name, field_map, expected_school_id=None
    ):
        from core.services.integrations import resolve_export_row, slugify_export_name

        # Re-scope through get_queryset for school isolation — mirrors export_csv pattern.
        # Prevents cross-school data leakage if Django admin ever passes an unscoped queryset.
        queryset = self.get_queryset(request).filter(id__in=queryset.values_list("id", flat=True))

        # Guard: empty + single-school — DISTINCT[:2] avoids a separate exists() call
        school_ids = list(queryset.values_list("school_id", flat=True).distinct()[:2])
        if not school_ids:
            messages.warning(request, "No submissions selected.")
            return None
        if len(school_ids) > 1:
            messages.error(
                request,
                f"Cannot apply '{profile_name}' export mapping across multiple schools. "
                "Select submissions from one school at a time.",
            )
            return None

        # Guard: action was registered for a specific school (defence-in-depth)
        if expected_school_id is not None and school_ids[0] != expected_school_id:
            messages.error(
                request,
                "These submissions do not belong to the school this export profile was configured for.",
            )
            return None

        # Fetch one extra row to detect truncation without a separate COUNT query.
        # select_related("school") avoids a second query for school features below.
        rows = list(queryset.select_related("school").order_by("-created_at")[:5001])
        truncated = len(rows) > 5000
        rows = rows[:5000]
        exported_count = len(rows)
        school = rows[0].school  # safe: school_ids non-empty guarantees rows non-empty

        # COUNT only when needed (truncation warning or audit log)
        needs_total = truncated or school.features.audit_log_enabled
        total_count = queryset.count() if needs_total else exported_count

        if school.features.audit_log_enabled:
            log_admin_audit(
                request=request,
                action="action",
                obj=None,
                changes={},
                extra={
                    "action": action_name,
                    "model": "core.submission",
                    "selected_count": total_count,
                    "exported_count": exported_count,
                },
            )

        if truncated:
            messages.warning(
                request,
                f"Only {exported_count:,} of {total_count:,} selected submissions exported "
                "(5,000 row limit). Re-run with a smaller selection to get the rest.",
            )

        cols = list(field_map.keys())
        warning_counts: Counter = Counter()

        filename = f"{slugify_export_name(profile_name)}_export.csv"
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow(cols)

        for s in rows:
            row, warnings = resolve_export_row(s.data or {}, field_map)
            for w in warnings:
                warning_counts[w] += 1
            writer.writerow([row.get(col, "") for col in cols])

        if warning_counts:
            total_warnings = sum(warning_counts.values())
            summary = "; ".join(
                f'"{msg}" × {count}'
                for msg, count in warning_counts.most_common(10)
            )
            logger.warning("Export '%s' mapping warnings: %s", profile_name, summary)

        return response

    def attachments(self, obj):
        qs = obj.files.all().order_by("field_key", "id")
        if not qs.exists():
            return "—"

        label_map = {}
        cfg = load_school_config(obj.school.slug)
        if cfg:
            _, label_map = _resolve_submission_form_cfg_and_labels(cfg, getattr(obj, "form_key", None))

        rows = []
        for f in qs:
            label = label_map.get(f.field_key, f.field_key.replace("_", " ").title())
            if f.original_name:
                name = f.original_name
            else:
                stored = (getattr(f.file, "name", "") or "").split("/")[-1]
                name = stored.split("__", 1)[-1] if "__" in stored else stored
            size = _bytes_to_mb(f.size_bytes or (getattr(f.file, "size", 0) or 0))

            # If you have a download view name, keep it; otherwise this can be blank.
            try:
                from django.urls import reverse
                view_url = reverse("admin_download_submission_file", args=[f.id]) if f.file else ""
            except Exception:
                view_url = ""

            rows.append((label, name, size, view_url))

        from django.utils.html import format_html, format_html_join
        from django.utils.safestring import mark_safe

        return format_html(
            "<div style='margin-top:6px'>{}</div>",
            format_html_join(
                "",
                "<div style='margin:4px 0;'>"
                "<strong>{}</strong> — {}{}{}{}"
                "</div>",
                (
                    (
                        label,
                        filename,
                        f" ({size})" if size else "",
                        mark_safe(" — ") if url else "",
                        format_html("<a href='{}' target='_blank'>View</a>", url) if url else "",
                    )
                    for (label, filename, size, url) in rows
                ),
            ),
        )

    attachments.short_description = "Attachments"

    def export_csv(self, request, queryset):
        queryset = self.get_queryset(request).filter(id__in=queryset.values_list("id", flat=True))
        
        first = queryset.first()
        if not _is_superuser(request.user) and first and not first.school.features.csv_export_enabled:
            messages.error(request, "CSV export is not enabled for this school.")
            return None

        if first and first.school.features.audit_log_enabled:
            log_admin_audit(
                request=request,
                action="action",
                obj=None,
                changes={},
                extra={"action": "export_csv", "model": "core.submission", "count": queryset.count()},
            )

        rows = list(queryset.order_by("-created_at")[:5000])
        all_keys = set()
        for s in rows:
            all_keys.update((s.data or {}).keys())

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="submissions.csv"'

        writer = csv.writer(response)
        writer.writerow(["created_at", "student_name"] + sorted(all_keys))

        for s in rows:
            data = s.data or {}
            writer.writerow(
                [s.created_at.isoformat(), s.student_display_name()]
                + [data.get(k, "") for k in sorted(all_keys)]
            )

        return response

    export_csv.short_description = "Export selected submissions to CSV"
