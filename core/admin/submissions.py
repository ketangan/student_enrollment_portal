# core/admin/submissions.py
from __future__ import annotations

import csv
import json

from django import forms
from django.core.exceptions import PermissionDenied
from django.contrib import admin, messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.safestring import mark_safe
from core.admin.audit import log_admin_audit
from copy import deepcopy

from core.admin.common import (
    DYN_PREFIX,
    _build_field_label_map,
    _bytes_to_mb,
    _has_school_membership,
    _is_superuser,
    _membership_school_id,
)
from core.models import Submission, SubmissionFile
from core.services.admin_submission_yaml import (
    apply_post_to_submission_data,
    build_yaml_sections,
    validate_required_fields,
)
from core.services.config_loader import get_forms, load_school_config
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
    readonly_fields = ("school_display", "created_at_pretty", "yaml_form", "data", "attachments")
    search_fields = ("school__slug", "school__display_name")

    def get_list_display(self, request):
        if _is_superuser(request.user):
            return ("id", "school_display", "student_name", "program_name", "created_at_pretty")
        return ("id", "student_name", "program_name", "created_at_pretty")

    def get_fieldsets(self, request, obj=None):
        return (
            ("General", {"fields": ("school_display", "created_at_pretty", "yaml_form")}),
            ("Raw Data (advanced)", {"fields": ("data",), "classes": ("collapse",)}),
            ("Attachments", {"fields": ("attachments",)}),
        )

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
                label_map = (
                    _build_field_label_map(obj.school.slug)
                    if getattr(obj, "school_id", None)
                    else {}
                )
                pretty = [label_map.get(k, k.replace("_", " ").title()) for k in sorted(changed_keys)]
                message = "Updated: " + ", ".join(pretty)
            else:
                return  # No changes to log

        return super().log_change(request, obj, message)

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

        return super().change_view(request, object_id, form_url, extra_context)

    # ----------------------------
    # Display helpers
    # ----------------------------
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

        forms = get_forms(cfg)
        form_key = getattr(obj, "form_key", "default")
        form_cfg = (forms.get(form_key) or forms.get("default") or {}).get("form") or {}

        # If the user attempted to save and validation failed, re-render with POST values.
        post_data = getattr(self, "_yaml_post_data", None)
        yaml_sections = build_yaml_sections(cfg, obj.data or {}, post_data=post_data, form=form_cfg)

        html = render_to_string(
            "admin/core/submission/_yaml_form.html",
            {"yaml_sections": yaml_sections, "DYN_PREFIX": DYN_PREFIX},
        )
        return mark_safe(html)

    yaml_form.short_description = ""

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
                errors = validate_required_fields(cfg, request.POST)
                if errors:
                    for e in errors:
                        messages.error(request, e)
                    return redirect(request.path)

        return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        if not obj or not obj.school_id:
            return

        cfg = load_school_config(obj.school.slug)
        if not cfg:
            return

        old_data = dict(obj.data or {})

        data = apply_post_to_submission_data(cfg, request.POST, existing_data=dict(obj.data or {}))
        Submission.objects.filter(pk=obj.pk).update(data=data)
        obj.data = data

        new_data = dict(data or {})
        changed = {}

        # Only log keys that actually changed (simple but useful)
        for k in set(old_data.keys()) | set(new_data.keys()):
            if old_data.get(k) != new_data.get(k):
                changed[k] = {"from": old_data.get(k), "to": new_data.get(k)}

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
    def attachments(self, obj):
        qs = obj.files.all().order_by("field_key", "id")
        if not qs.exists():
            return "—"

        label_map = _build_field_label_map(obj.school.slug)

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
