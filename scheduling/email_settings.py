"""Bookings access to the school email configuration shared with Enrollment."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from core.admin.audit import log_admin_audit
from core.models import School, SchoolEmailTemplate
from core.services.portal_environment import scope_schools
from core.services.email_content import TOKENS, TOKEN, validate_body
from .access import require_access
from .models import SchedulingProfile
from .views import _context


class SchoolEmailSettingsForm(forms.ModelForm):
    smtp_port = forms.IntegerField(label="Port", required=False, min_value=1, max_value=65535,
                                   widget=forms.NumberInput(attrs={"placeholder": "587"}))
    smtp_password = forms.CharField(label="Password", required=False, max_length=255, strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
        help_text="Leave blank to keep the saved password.")

    class Meta:
        model = School
        fields = ("smtp_host", "smtp_port", "smtp_username", "smtp_password", "smtp_from_email", "smtp_use_tls")
        labels = {"smtp_host": "SMTP host", "smtp_username": "Username",
                  "smtp_from_email": "From email", "smtp_use_tls": "Use TLS"}
        widgets = {"smtp_username": forms.TextInput(attrs={"autocomplete": "off"})}

    def clean_smtp_password(self):
        return self.cleaned_data["smtp_password"] or self.instance.smtp_password


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def settings(request, school_slug):
    profile, role = require_access(request.user, school_slug, "setup.edit", capability="workshops")
    get_object_or_404(scope_schools(SchedulingProfile.objects.all(), request, "school__"), pk=profile.pk)
    school = profile.school
    form = SchoolEmailSettingsForm(instance=school)
    if request.method == "POST":
        action = request.POST.get("action")
        if action not in {"save", "clear"}:
            return HttpResponseBadRequest("Choose a valid email settings action.")
        with transaction.atomic():
            school = School.objects.select_for_update().get(pk=school.pk)
            require_access(request.user, school_slug, "setup.edit", capability="workshops")
            if action == "clear":
                school.smtp_host = school.smtp_username = school.smtp_password = school.smtp_from_email = ""
                school.smtp_port = None
                school.smtp_use_tls = True
            else:
                form = SchoolEmailSettingsForm(request.POST, instance=school)
                if form.is_valid():
                    school = form.save(commit=False)
            if action == "clear" or not form.errors:
                school.save(update_fields=SchoolEmailSettingsForm.Meta.fields)
                log_admin_audit(request=request, action="action", obj=school, changes={},
                    extra={"name": "clear_smtp" if action == "clear" else "update_smtp"})
                messages.success(request, "Email settings saved." if action == "save" else "Default email service restored.")
                return redirect(request.path)
    context = _context(request, profile, role, "email_settings")
    context["dashboard_location_form"] = None
    context.update(email_form=form, custom_delivery=bool(profile.school.smtp_host),
                   email_templates=SchoolEmailTemplate.objects.filter(school=profile.school, purpose="bookings").order_by("name", "pk"))
    return render(request, "scheduling/email_settings.html", context, status=400 if form.errors else 200)


class BookingTemplateForm(forms.ModelForm):
    subject = forms.CharField(max_length=200)
    body = forms.CharField(label="Message", max_length=20000, widget=forms.Textarea(attrs={"rows": 10}))

    class Meta:
        model = SchoolEmailTemplate
        fields = ("name", "booking_kind", "booking_link", "subject", "body", "is_active")
        labels = {"booking_kind": "Available for", "booking_link": "Included button", "is_active": "Active"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["booking_link"].required = False
        self.fields["booking_link"].choices = [("none", "None"), ("payment", "Payment"),
            ("guests", "Guest details / participant selections")]

    def clean_booking_link(self):
        return self.cleaned_data["booking_link"] or "none"

    def clean_body(self):
        return validate_body(self.cleaned_data["body"])

    def clean(self):
        data = super().clean()
        for field in ("subject", "body"):
            if any(token not in TOKENS for token in TOKEN.findall(data.get(field, ""))):
                self.add_error(field, "Use the available reservation fields below.")
        return data


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def template_edit(request, school_slug, pk=None):
    profile, role = require_access(request.user, school_slug, "setup.edit", capability="workshops")
    get_object_or_404(scope_schools(SchedulingProfile.objects.all(), request, "school__"), pk=profile.pk)
    template = get_object_or_404(SchoolEmailTemplate, pk=pk, school=profile.school, purpose="bookings") if pk else SchoolEmailTemplate(school=profile.school, purpose="bookings")
    if request.method == "POST" and request.POST.get("action", "save") != "save":
        return HttpResponseBadRequest("Choose a valid template action.")
    form = BookingTemplateForm(request.POST if request.method == "POST" else None, instance=template)
    if request.method == "POST" and form.is_valid():
        form.save()
        log_admin_audit(request=request, action="change" if pk else "add", obj=template, changes={},
            extra={"name": "email_template_updated" if pk else "email_template_created", "template_name": template.name})
        messages.success(request, "Email template saved.")
        return redirect("scheduling:email_settings", school_slug=school_slug)
    context = _context(request, profile, role, "email_settings")
    context.update(template_form=form, dashboard_location_form=None, template_tokens=zip(TOKENS,
        ("Customer name", "First name", "Customer email", "Workshop", "Reservation status", "Business name",
         "Booking date", "Booking time", "Location", "Participant count")),
        template_heading="Edit email template" if pk else "Add email template")
    return render(request, "scheduling/email_template_form.html", context, status=400 if form.errors else 200)
