import json
import csv

from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from django.contrib.auth.forms import UserCreationForm
from django.http import HttpResponse, Http404
from django.utils import timezone
from django.utils.formats import date_format
from django.urls import reverse, path
from django.utils.html import format_html, format_html_join
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils.safestring import mark_safe

from .models import School, Submission, SchoolAdminMembership, SubmissionFile
from core.services.config_loader import load_school_config
from core.services.form_utils import build_option_label_map


# ----------------------------
# Admin UI simplification
# ----------------------------

admin.site.site_url = None

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


# ----------------------------
# Helpers
# ----------------------------
def _is_superuser(user) -> bool:
    return bool(user and user.is_active and user.is_superuser)


def _membership_school_id(user):
    m = getattr(user, "school_membership", None)
    return getattr(m, "school_id", None) if m else None


def _has_school_membership(user) -> bool:
    return _membership_school_id(user) is not None

def _bytes_to_mb(size: int) -> str:
    try:
        b = int(size or 0)
    except Exception:
        b = 0

    if b <= 0:
        return ""

    kb = b / 1024
    if kb < 1024:
        return f"{kb:.0f} KB"

    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"

    gb = mb / 1024
    return f"{gb:.1f} GB"

def _build_field_label_map(school_slug: str) -> dict[str, str]:
    cfg = load_school_config(school_slug)
    if not cfg:
        return {}
    label_map: dict[str, str] = {}
    for section in cfg.form.get("sections", []):
        for field in section.get("fields", []):
            key = field.get("key")
            label = field.get("label")
            if key and label:
                label_map[str(key)] = str(label)
    return label_map

# ----------------------------
# Pretty JSON form/widget for Submission detail page
# ----------------------------
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
                    "rows": 34,
                    "style": (
                        "font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "
                        "'Liberation Mono', 'Courier New', monospace; white-space: pre;"
                    ),
                }
            )
        }


# ----------------------------
# Users Admin (school-scoped, MVP-friendly)
# ----------------------------
UserModel = get_user_model()

try:
    admin.site.unregister(UserModel)
except admin.sites.NotRegistered:
    pass


class UserSuperuserForm(forms.ModelForm):
    """
    Superuser-only EDIT form:
    - lets you pick a School directly on the User change page
    - we DO NOT create membership here (done in admin.save_model to avoid unsaved-user issues)
    """
    school = forms.ModelChoiceField(
        queryset=School.objects.all().order_by("display_name", "slug"),
        required=False,
        help_text="Links this user to a school for school-scoped admin access.",
    )

    class Meta:
        model = UserModel
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "is_staff",
            "is_superuser",
            "school",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # make email required (MVP)
        if "email" in self.fields:
            self.fields["email"].required = True

        if self.instance and self.instance.pk:
            current_school_id = _membership_school_id(self.instance)
            if current_school_id:
                self.fields["school"].initial = School.objects.filter(id=current_school_id).first()


class UserSuperuserAddForm(UserCreationForm):
    """
    Superuser-only ADD form:
    - includes password1/password2 (Django standard)
    - includes School dropdown
    - defaults is_staff=True
    """
    school = forms.ModelChoiceField(
        queryset=School.objects.all().order_by("display_name", "slug"),
        required=False,
        help_text="Assign this user to a school (creates SchoolAdminMembership and sets is_staff=True).",
    )

    class Meta(UserCreationForm.Meta):
        model = UserModel
        fields = ("username", "first_name", "last_name", "email", "school")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # make email required (MVP)
        if "email" in self.fields:
            self.fields["email"].required = True

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = True  # allow admin login by default
        if commit:
            user.save()
        return user


@admin.register(UserModel)
class SchoolScopedUserAdmin(DjangoUserAdmin):
    actions = None
    list_filter = ()
    ordering = ("username",)
    search_fields = ("username", "first_name", "last_name", "email")
    list_display = ("username", "first_name", "last_name", "email", "is_active", "is_staff", "last_login")
    filter_horizontal = ()
    readonly_fields = ("last_login", "date_joined")

    add_form = UserSuperuserAddForm
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "first_name", "last_name", "email", "school", "password1", "password2"),
        }),
    )

    def has_module_permission(self, request):
        return _is_superuser(request.user) or (_has_school_membership(request.user) and request.user.is_staff)

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        if _is_superuser(request.user):
            return qs

        school_id = _membership_school_id(request.user)
        if not school_id:
            return qs.none()

        return qs.filter(
            is_superuser=False,
            school_membership__school_id=school_id,
        )

    def get_form(self, request, obj=None, **kwargs):
        """
        Critical fix:
        - On ADD (obj is None), do NOT override the form (must use add_form)
        - On CHANGE (obj is not None) and superuser, use our superuser edit form
        """
        if _is_superuser(request.user) and obj is not None:
            kwargs["form"] = UserSuperuserForm
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        if _is_superuser(request.user) and obj is not None:
            return (
                ("User", {"fields": ("username", "first_name", "last_name", "email")}),
                ("Status", {"fields": ("is_active", "is_staff", "is_superuser")}),
                ("School", {"fields": ("school",)}),
            )

        # School admin: keep it simple
        if not _is_superuser(request.user):
            return (
                ("User", {"fields": ("username", "first_name", "last_name", "email")}),
                ("Status", {"fields": ("is_active",)}),
            )

        # Superuser on add page uses add_fieldsets above
        return super().get_fieldsets(request, obj)

    def save_model(self, request, obj, form, change):
        # Default: any created user becomes staff so they can log in
        if not change and not obj.is_staff:
            obj.is_staff = True

        super().save_model(request, obj, form, change)

        # Superuser-only: create/update membership AFTER user is saved
        if _is_superuser(request.user):
            school = form.cleaned_data.get("school") if hasattr(form, "cleaned_data") else None
            if school:
                SchoolAdminMembership.objects.update_or_create(
                    user=obj,
                    defaults={"school": school},
                )

    def has_delete_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(
            {
                "show_save_and_add_another": False,
                "show_save_and_continue": False,
                "show_save_as_new": False,
            }
        )
        if not _is_superuser(request.user):
            extra_context["show_delete"] = False
        return super().changeform_view(request, object_id, form_url, extra_context)


# ----------------------------
# MVP-safe Admin "Reports Hub" view
# ----------------------------
def admin_reports_hub_view(request):
    user = request.user
    if not user or not user.is_authenticated or not user.is_staff:
        raise Http404("Page not found")

    if _is_superuser(user):
        schools = School.objects.all().order_by("display_name", "slug")
        context = admin.site.each_context(request)
        context.update({"schools": schools})
        return TemplateResponse(request, "admin/reports_hub.html", context)

    school_id = _membership_school_id(user)
    if not school_id:
        raise Http404("Page not found")

    school = School.objects.filter(id=school_id).first()
    if not school:
        raise Http404("Page not found")

    return redirect(reverse("school_reports", kwargs={"school_slug": school.slug}))


_original_admin_get_urls = admin.site.get_urls


def _admin_get_urls():
    urls = _original_admin_get_urls()
    custom = [
        path("reports/", admin.site.admin_view(admin_reports_hub_view), name="reports_hub"),
    ]
    return custom + urls


admin.site.get_urls = _admin_get_urls


# ----------------------------
# School Admin (Reports link)
# ----------------------------
@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("slug", "display_name", "website_url", "created_at", "reports_link")
    search_fields = ("slug", "display_name")

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
        return format_html(
            """
            <a href="{url}" target="_blank"
            style="
                display:inline-block;
                padding:6px 12px;
                border-radius:10px;
                background:#2563eb;
                color:#fff;
                font-weight:600;
                text-decoration:none;
                border:1px solid rgba(255,255,255,0.15);
            ">
            Reports
            </a>
            """,
            url=url,
        )

    reports_link.short_description = "Reports"
    readonly_fields = ("reports_link",)


# ----------------------------
# Membership Admin (superuser only)
# ----------------------------
@admin.register(SchoolAdminMembership)
class SchoolAdminMembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "school")
    search_fields = ("user__username", "school__slug", "school__display_name")
    actions = None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs if _is_superuser(request.user) else qs.none()

    def has_module_permission(self, request):
        return _is_superuser(request.user)

    def has_add_permission(self, request):
        return _is_superuser(request.user)

    def has_change_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def has_delete_permission(self, request, obj=None):
        return _is_superuser(request.user)


class SubmissionFileInline(admin.TabularInline):
    model = SubmissionFile
    extra = 0
    fields = ("field_key", "file", "original_name", "content_type", "size_bytes", "created_at")
    readonly_fields = ("created_at",)

# ----------------------------
# Submission Admin
# ----------------------------
@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    class Media:
        js = ("admin_actions.js",)

    form = SubmissionAdminForm
    list_filter = ()
    actions = ["export_csv"]
    inlines = [SubmissionFileInline]

    def get_list_display(self, request):
        if _is_superuser(request.user):
            return ("id", "school_display", "student_name", "program_name", "created_at_pretty")
        return ("id", "student_name", "program_name", "created_at_pretty")

    search_fields = ("school__slug", "school__display_name")
    readonly_fields = ("school_display", "created_at_pretty", "attachments")
    fieldsets = (
        ("General", {"fields": ("school_display", "created_at_pretty", "data")}),
        ("Attachments", {"fields": ("attachments",)}),
    )

    def has_module_permission(self, request):
        return _is_superuser(request.user) or (_has_school_membership(request.user) and request.user.is_staff)

    def has_view_permission(self, request, obj=None):
        if _is_superuser(request.user):
            return True
        return _has_school_membership(request.user) and request.user.is_staff

    def has_change_permission(self, request, obj=None):
        if _is_superuser(request.user):
            return True
        return _has_school_membership(request.user) and request.user.is_staff

    def has_add_permission(self, request):
        return _is_superuser(request.user)

    def has_delete_permission(self, request, obj=None):
        return _is_superuser(request.user)

    def school_display(self, obj: Submission) -> str:
        if not obj or not obj.school_id:
            return ""
        return obj.school.display_name or obj.school.slug

    school_display.short_description = "School"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if _is_superuser(request.user):
            return qs
        school_id = _membership_school_id(request.user)
        if not school_id:
            return qs.none()
        return qs.filter(school_id=school_id)

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context.update(
            {
                "show_save_and_add_another": False,
                "show_save_and_continue": False,
                "show_save_as_new": False,
            }
        )
        if not _is_superuser(request.user):
            extra_context["show_delete"] = False
        return super().changeform_view(request, object_id, form_url, extra_context)

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

    def created_at_pretty(self, obj: Submission) -> str:
        dt = timezone.localtime(obj.created_at)
        return date_format(dt, "N j, Y, P")

    created_at_pretty.short_description = "Created at"
    created_at_pretty.admin_order_field = "created_at"

    def get_search_results(self, request, queryset, search_term):
        base_qs = queryset
        default_qs, use_distinct = super().get_search_results(request, queryset, search_term)

        term = (search_term or "").strip().lower()
        if not term:
            return default_qs, use_distinct

        candidates = list(base_qs.order_by("-created_at")[:5000])
        matched_ids = [
            s.id
            for s in candidates
            if term in (s.student_display_name() or "").lower()
            or term in (s.program_display_name() or "").lower()
        ]

        default_ids = set(default_qs.values_list("id", flat=True))
        all_ids = default_ids.union(matched_ids)

        return base_qs.filter(id__in=all_ids), use_distinct
    
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

            try:
                view_url = f.file.url if f.file else ""
            except Exception:
                view_url = ""

            rows.append((label, name, size, view_url))

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
