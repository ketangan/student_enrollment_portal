# core/admin/schools.py
from __future__ import annotations

import json

from django import forms
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html, mark_safe

from core.admin.common import _has_school_membership, _is_superuser, _membership_school_id
from core.models import School
from core.services.feature_flags import default_flags_for_plan, merge_flags


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


class SchoolAdminForm(forms.ModelForm):
    class Meta:
        model = School
        fields = "__all__"
        widgets = {
            "feature_flags": PrettyJSONWidget(attrs={"rows": 10}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # ✅ Only superusers can see/edit plan + flags
        if not self.current_user_is_superuser:
            self.fields.pop("plan", None)
            self.fields.pop("feature_flags", None)
            return

        # ✅ Show EFFECTIVE flags in the single "Feature flags" box
        obj = getattr(self, "instance", None)
        if obj and getattr(obj, "pk", None):
            effective = merge_flags(plan=obj.plan, overrides=obj.feature_flags)
            self.initial["feature_flags"] = effective

    # injected by ModelAdmin.get_form()
    current_user_is_superuser: bool = False

    def clean_feature_flags(self):
        v = self.cleaned_data.get("feature_flags")

        if v in (None, "", {}):
            v = {}

        # allow paste as string
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                raise forms.ValidationError("Feature flags must be valid JSON.")

        if not isinstance(v, dict):
            raise forms.ValidationError("Feature flags must be a JSON object (dictionary).")

        for k, val in v.items():
            if not isinstance(k, str):
                raise forms.ValidationError("Feature flag keys must be strings.")
            if not isinstance(val, bool):
                raise forms.ValidationError(f'Feature flag "{k}" must be true/false.')

        # ✅ Convert the "effective" dict back into OVERRIDES only (diff vs plan defaults)
        plan = self.cleaned_data.get("plan") or getattr(self.instance, "plan", "trial")
        defaults = default_flags_for_plan(plan)

        overrides: dict[str, bool] = {}
        for key, value in v.items():
            if defaults.get(key) != value:
                overrides[key] = value

        # also allow clearing an override by omitting it from JSON (we just won't store it)
        return overrides


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    form = SchoolAdminForm
    list_display = (
        "slug",
        "display_name",
        "plan",
        "stripe_customer_id",
        "stripe_subscription_id",
        "stripe_subscription_status",
        "website_url",
        "created_at",
        "reports_link",
    )
    search_fields = ("slug", "display_name")
    readonly_fields = (
        "reports_link",
        "embed_snippet",
        "stripe_customer_id",
        "stripe_subscription_id",
        "stripe_subscription_status",
        "stripe_cancel_at",
        "stripe_cancel_at_period_end",
        "stripe_current_period_end",
    )
    list_filter = ("plan", "stripe_subscription_status")

    def get_form(self, request, obj=None, **kwargs):
        Form = super().get_form(request, obj, **kwargs)
        # inject flag so the form can hide/show fields
        Form.current_user_is_superuser = _is_superuser(request.user)
        return Form

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

    def embed_snippet(self, obj: School):
        if not obj or not obj.slug:
            return mark_safe("-")
        # slug is alphanumeric + hyphens (Django SlugField) — safe to interpolate
        relative_url = f"/schools/{obj.slug}/apply/"
        uid = obj.slug.replace("-", "_")
        html = (
            f'<div style="max-width:640px">'
            f'<p style="margin-bottom:4px"><strong>Hosted link</strong> '
            f'(share this directly):</p>'
            f'<code id="sep-link-{uid}" style="word-break:break-all"></code>'
            f'<p style="margin:12px 0 4px"><strong>Embed snippet</strong> '
            f'(paste into your website HTML):</p>'
            f'<textarea id="sep-embed-{uid}" readonly rows="3" '
            f'style="width:100%;font-family:monospace;font-size:12px;'
            f'padding:6px;box-sizing:border-box"></textarea>'
            f'<script>'
            f'(function(){{'
            f'  var u = window.location.origin + "{relative_url}";'
            f'  document.getElementById("sep-link-{uid}").textContent = u;'
            f'  document.getElementById("sep-embed-{uid}").value ='
            f'    \'<iframe src="\' + u + \'" width="100%" height="700" \''
            f'    + \'frameborder="0" style="border:none;" \''
            f'    + \'title="Application Form"></iframe>\';'
            f'}})();'
            f'</script>'
            f'</div>'
        )
        return mark_safe(html)

    embed_snippet.short_description = "Embed on your website"
    