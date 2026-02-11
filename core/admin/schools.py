# core/admin/schools.py
from __future__ import annotations
import json

from django import forms
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from core.admin.common import _has_school_membership, _is_superuser, _membership_school_id
from core.models import School


class PrettyJSONWidget(forms.Textarea):
    def format_value(self, value):
        if value in (None, "", {}):
            return ""
        try:
            import json
            if isinstance(value, str):
                value = json.loads(value)
            return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
        except Exception:
            return super().format_value(value)

class SchoolAdminForm(forms.ModelForm):
    class Meta:
        model = School
        fields = "__all__"
        widgets = {
            "feature_flags": PrettyJSONWidget(attrs={"rows": 10}),
        }

    def clean_feature_flags(self):
        v = self.cleaned_data.get("feature_flags")

        if v in (None, "", {}):
            return {}

        # If admin pasted JSON as a string, parse it
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                raise forms.ValidationError("Feature flags must be valid JSON.")

        if not isinstance(v, dict):
            raise forms.ValidationError("Feature flags must be a JSON object (dictionary).")

        # Optional: enforce boolean values
        for k, val in v.items():
            if not isinstance(k, str):
                raise forms.ValidationError("Feature flag keys must be strings.")
            if not isinstance(val, bool):
                raise forms.ValidationError(f'Feature flag "{k}" must be true/false.')

        return v
    
    
@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    form = SchoolAdminForm
    list_display = ("slug", "display_name", "plan", "website_url", "created_at", "reports_link")
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
        return format_html("<a href='{}' target='_blank'>Reports</a>", url)

    reports_link.short_description = "Reports"
