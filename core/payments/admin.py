"""Operator-controlled test checkout settings, independent of subscription billing."""
from django import forms
from django.conf import settings
from django.contrib import admin

from core.models import School
from .models import PaymentAccount


class PaymentAccountForm(forms.ModelForm):
    reference = forms.RegexField(
        regex=r"^acct_[A-Za-z0-9]+$", max_length=100, required=False, label="Connected test account ID",
        help_text="Leave blank for Pontora's shared sandbox. A connected merchant requires its acct_ ID.",
    )

    class Meta:
        model = PaymentAccount
        fields = ("school", "scope", "reference", "enabled")
        labels = {"school": "Business", "enabled": "Enable Stripe test checkout"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "school" in self.fields:
            self.fields["school"].queryset = School.objects.filter(is_demo=True)
        if not self.instance.pk:
            self.initial.setdefault("scope", PaymentAccount.Scope.PLATFORM_DEMO)

    def clean(self):
        values = super().clean()
        school = values.get("school") or (self.instance.school if self.instance.pk else None)
        scope = values.get("scope", self.instance.scope)
        reference = values.get("reference", self.instance.reference)
        if scope == PaymentAccount.Scope.PLATFORM_DEMO and (not self.instance.pk or values.get("enabled")):
            configured = settings.PAYMENTS_STRIPE_TEST_ACCOUNT_ID
            if not configured.startswith("acct_") or (reference and reference != configured):
                raise forms.ValidationError("Configure the shared booking sandbox account ID; it cannot be overridden here.")
            if not self.instance.pk:
                values["reference"] = configured
        elif "reference" in self.fields and not reference:
            self.add_error("reference", "Enter the connected test account ID.")
        if school and not school.is_demo:
            raise forms.ValidationError("Test checkout can only be configured for a demo business here.")
        if school and PaymentAccount.objects.filter(school=school, environment="test").exclude(pk=self.instance.pk).exists():
            self.add_error("school" if "school" in self.fields else None,
                "This business already has test checkout settings. Edit its existing payment account.")
        if values.get("enabled") and (
            not settings.PAYMENTS_STRIPE_TEST_SECRET_KEY.startswith("sk_test_")
            or not settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET.startswith("whsec_")
        ):
            self.add_error("enabled", "Configure the booking sandbox key and webhook before enabling checkout.")
        return values


@admin.register(PaymentAccount)
class PaymentAccountAdmin(admin.ModelAdmin):
    form = PaymentAccountForm
    list_display = ("school", "scope", "reference", "enabled")
    list_select_related = ("school",)
    list_filter = ("enabled",)
    search_fields = ("school__slug", "school__display_name", "reference")
    readonly_fields = ("environment",)
    fields = ("school", "scope", "reference", "environment", "enabled")

    def get_readonly_fields(self, request, obj=None):
        # Rebinding an account would invalidate existing orders and verified events.
        return self.readonly_fields + (("school", "scope", "reference") if obj else ())

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False
