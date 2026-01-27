# core/admin/users.py
from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserCreationForm

from core.admin.common import _has_school_membership, _is_superuser, _membership_school_id
from core.models import School, SchoolAdminMembership

UserModel = get_user_model()

try:
    admin.site.unregister(UserModel)
except admin.sites.NotRegistered:
    pass


class UserSuperuserForm(forms.ModelForm):
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

        if "email" in self.fields:
            self.fields["email"].required = True

        if self.instance and self.instance.pk:
            current_school_id = _membership_school_id(self.instance)
            if current_school_id:
                self.fields["school"].initial = School.objects.filter(id=current_school_id).first()


class UserSuperuserAddForm(UserCreationForm):
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
        if "email" in self.fields:
            self.fields["email"].required = True

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = True
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

        if not _is_superuser(request.user):
            return (
                ("User", {"fields": ("username", "first_name", "last_name", "email")}),
                ("Status", {"fields": ("is_active",)}),
            )

        return super().get_fieldsets(request, obj)

    def save_model(self, request, obj, form, change):
        if not change and not obj.is_staff:
            obj.is_staff = True

        super().save_model(request, obj, form, change)

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
    