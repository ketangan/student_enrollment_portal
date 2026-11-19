"""Bookings access to the school email configuration shared with Enrollment."""
from django import forms
from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from django.urls import reverse

from core.admin.audit import log_admin_audit
from core.models import School, SchoolEmailTemplate
from core.services.portal_environment import scope_schools
from core.services.email_content import BOOKING_TOKENS, TOKENS, TOKEN, validate_body
from core.services.notifications import get_school_email_connection
from .access import require_access
from .models import SchedulingProfile
from .views import _context
from . import booking_email_templates as booking_templates

BOOKING_EDITOR_TOKENS = tuple(({'school_name': 'business_name', 'full_name': 'customer_name'}.get(key, key), label)
    for key, label in BOOKING_TOKENS) + booking_templates.DETAIL_TOKENS


class BookingBrandForm(forms.ModelForm):
    class Meta:
        model = SchedulingProfile
        fields = ("branded_booking_email", "email_brand_name", "email_brand_color", "email_signoff")
        labels = {"branded_booking_email": "Use branded booking emails", "email_brand_name": "Studio name",
            "email_brand_color": "Brand color", "email_signoff": "Sign-off"}
        widgets = {"email_brand_color": forms.HiddenInput(),
            "email_signoff": forms.Textarea(attrs={"rows": 3})}


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


def uses_template_workspace(profile):
    return profile.branded_booking_email or SchoolEmailTemplate.objects.filter(
        school=profile.school, purpose='bookings').exclude(booking_event='').exists()


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def settings(request, school_slug):
    profile, role = require_access(request.user, school_slug, "setup.edit", capability="workshops")
    get_object_or_404(scope_schools(SchedulingProfile.objects.all(), request, "school__"), pk=profile.pk)
    school = profile.school
    form = SchoolEmailSettingsForm(instance=school)
    brand_form = BookingBrandForm(instance=profile)
    tab = request.GET.get("tab", "templates")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == 'save_brand_mode':
            mode = request.POST.get('branded_booking_email')
            if mode not in {'true', 'false'}:
                return JsonResponse({'error': 'Choose a valid branding mode.'}, status=400)
            with transaction.atomic():
                profile = SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
                profile.branded_booking_email = mode == 'true'
                profile.save(update_fields=['branded_booking_email'])
                if profile.branded_booking_email:
                    booking_templates.seed_defaults(profile)
                log_admin_audit(request=request, action='change', obj=profile, changes={},
                    extra={'name': 'booking_email_branding_mode_updated'})
            return JsonResponse({'branded': profile.branded_booking_email})
        if action in {"save_brand", "preview_brand"}:
            brand_form = BookingBrandForm(request.POST, instance=profile)
            tab = "branding"
            if brand_form.is_valid():
                if action == "preview_brand":
                    return JsonResponse(booking_templates.brand_preview(profile))
                with transaction.atomic():
                    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
                    require_access(request.user, school_slug, "setup.edit", capability="workshops")
                    profile.save(update_fields=BookingBrandForm.Meta.fields)
                    if profile.branded_booking_email:
                        booking_templates.seed_defaults(profile)
                    log_admin_audit(request=request, action="change", obj=profile, changes={},
                        extra={"name": "booking_email_branding_updated"})
                messages.success(request, "Booking email branding saved.")
                return redirect(request.path + "?tab=branding")
            if action == "preview_brand":
                return JsonResponse({"errors": brand_form.errors}, status=400)
        elif action not in {"save", "clear"}:
            return HttpResponseBadRequest("Choose a valid email settings action.")
        if action in {"save", "clear"}:
            tab = "delivery"
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
                    return redirect(request.path + "?tab=delivery")
    context = _context(request, profile, role, "email_settings")
    context["dashboard_location_form"] = None
    context.update(email_form=form, custom_delivery=bool(profile.school.smtp_host),
                   brand_form=brand_form, email_tab=tab,
                   brand_preview=booking_templates.brand_preview(profile),
                   email_templates=SchoolEmailTemplate.objects.filter(school=profile.school, purpose="bookings").order_by("name", "pk"))
    if tab == 'templates' and uses_template_workspace(profile):
        template_context(context, profile, request.GET.get('template'))
    return render(request, "scheduling/email_settings.html", context, status=400 if form.errors or brand_form.errors else 200)


class BookingTemplateForm(forms.ModelForm):
    booking_links = forms.MultipleChoiceField(label='Email links', required=False,
        choices=booking_templates.LINK_CHOICES, widget=forms.CheckboxSelectMultiple)
    subject = forms.CharField(max_length=200)
    body = forms.CharField(label="Message", max_length=20000, widget=forms.Textarea(attrs={"rows": 10}))

    class Meta:
        model = SchoolEmailTemplate
        fields = ("name", "booking_kind", "booking_link", "subject", "heading", "body", "is_active", "booking_links")
        labels = {"booking_kind": "Available for", "booking_link": "Included button", "is_active": "Active"}

    def __init__(self, *args, **kwargs):
        branded = kwargs.pop("branded", False)
        preview = kwargs.pop('preview', False)
        super().__init__(*args, **kwargs)
        self.branded = branded
        if branded:
            self.initial['body'] = booking_templates.editable_body(self.instance)
            self.initial['booking_links'] = booking_templates.selected_links(self.instance)
            for name in ('subject', 'heading', 'body'):
                self.initial[name] = (self.initial.get(name) or '').replace('{{school_name}}', '{{business_name}}').replace('{{full_name}}', '{{customer_name}}')
        if not branded:
            self.fields.pop("heading")
            self.fields.pop('booking_links')
        if self.instance.booking_event:
            self.fields["booking_kind"].disabled = self.fields["booking_link"].disabled = True
            if branded:
                self.fields['is_active'].initial = True
                self.initial['is_active'] = True
                self.fields['is_active'].disabled = True
                self.fields['is_active'].widget = forms.HiddenInput()
        if preview:
            for field in ('name', 'subject', 'body'):
                self.fields[field].required = False
        self.fields["booking_link"].required = False
        self.fields["booking_link"].choices = [("none", "None"), ("payment", "Payment"),
            ("guests", "Guest details / participant selections"), ("guide", "Design guide (PDF)")]

    def clean_booking_link(self):
        return self.cleaned_data["booking_link"] or "none"

    def clean_body(self):
        if not self.fields['body'].required and not self.cleaned_data['body'].strip():
            return ''
        return validate_body(self.cleaned_data["body"])

    def clean(self):
        data = super().clean()
        for field in ("subject", "heading", "body"):
            if any(token not in (*TOKENS, 'customer_name', 'business_name', *(key for key, _ in booking_templates.DETAIL_TOKENS)) for token in TOKEN.findall(data.get(field, ""))):
                self.add_error(field, "Choose a supported field from Insert details.")
        if "\r" in data.get("subject", "") or "\n" in data.get("subject", ""):
            self.add_error("subject", "Enter a single-line subject.")
        return data

    def _post_clean(self):
        if self.branded:
            self.instance.booking_details_in_body = True
        super()._post_clean()


def template_context(context, profile, selected=None, form=None):
    templates = SchoolEmailTemplate.objects.filter(school=profile.school, purpose='bookings').filter(
        Q(is_active=True) | ~Q(booking_event='')).order_by('name', 'pk')
    if form is not None:
        template = form.instance
    elif selected == 'new':
        template = SchoolEmailTemplate(school=profile.school, purpose='bookings')
    elif selected:
        if not str(selected).isdigit():
            raise Http404
        template = get_object_or_404(SchoolEmailTemplate, pk=selected, school=profile.school, purpose='bookings')
    else:
        template = templates.first()
    context.update(email_tab='templates', email_templates=templates, template_workspace=True)
    groups = [('Customer', {'customer_name', 'first_name', 'email'}),
        ('Booking', {'program', 'package_name', 'participant_count', 'status'}),
        ('When & where', {'booking_date', 'booking_time', 'starts_at', 'ends_at', 'location', 'room'}),
        ('Business', {'business_name'})]
    context['template_token_groups'] = [(label, [(key, title) for key, title in BOOKING_EDITOR_TOKENS if key in keys])
        for label, keys in groups]
    if template is not None:
        context.update(template_form=form or BookingTemplateForm(instance=template, branded=True),
            selected_template=template, template_tokens=BOOKING_EDITOR_TOKENS,
            template_preview=booking_templates.preview(profile, template),
            template_action=reverse('scheduling:email_template_edit', kwargs={'school_slug': profile.school.slug, 'pk': template.pk})
                if template.pk else reverse('scheduling:email_template_new', kwargs={'school_slug': profile.school.slug}))


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def template_edit(request, school_slug, pk=None):
    profile, role = require_access(request.user, school_slug, "setup.edit", capability="workshops")
    get_object_or_404(scope_schools(SchedulingProfile.objects.all(), request, "school__"), pk=profile.pk)
    template = get_object_or_404(SchoolEmailTemplate, pk=pk, school=profile.school, purpose="bookings") if pk else SchoolEmailTemplate(school=profile.school, purpose="bookings")
    workspace = uses_template_workspace(profile)
    action = request.POST.get("action", "save")
    if request.method == "POST" and action not in {"save", "preview", "send_test"}:
        return HttpResponseBadRequest("Choose a valid template action.")
    form = BookingTemplateForm(request.POST if request.method == "POST" else None, instance=template,
        branded=workspace, preview=action == 'preview')
    if request.method == 'POST' and action == 'send_test':
        return send_template_test(request, profile, form)
    if request.method == "POST" and action == "preview":
        if not workspace:
            return HttpResponseBadRequest("Enable booking email branding before previewing.")
        if not form.is_valid():
            return JsonResponse({"errors": form.errors}, status=400)
        return JsonResponse(booking_templates.preview(profile, form.instance))
    if request.method == "POST" and form.is_valid():
        form.save()
        log_admin_audit(request=request, action="change" if pk else "add", obj=template, changes={},
            extra={"name": "email_template_updated" if pk else "email_template_created", "template_name": template.name})
        messages.success(request, "Email template saved.")
        path = reverse('scheduling:email_settings', kwargs={'school_slug': school_slug})
        return redirect(path + f'?tab=templates&template={template.pk}' if workspace else path)
    context = _context(request, profile, role, "email_settings")
    context.update(template_form=form, dashboard_location_form=None, template_tokens=BOOKING_EDITOR_TOKENS if profile.branded_booking_email else BOOKING_TOKENS,
        automatic_template=bool(template.booking_event), template_event=template.get_booking_event_display(),
        template_preview=booking_templates.preview(profile, template),
        template_heading="Edit email template" if pk else "Add email template")
    if workspace:
        template_context(context, profile, form=form)
        return render(request, 'scheduling/email_settings.html', context, status=400 if form.errors else 200)
    return render(request, "scheduling/email_template_form.html", context, status=400 if form.errors else 200)


def send_template_test(request, profile, form):
    """Explicit staff-only sample delivery; never resolve real reservation links."""
    valid = form.is_valid()
    try:
        recipient = forms.EmailField(max_length=254).clean(request.POST.get('test_recipient', ''))
    except forms.ValidationError as exc:
        return JsonResponse({'errors': {'test_recipient': exc.messages}}, status=400)
    if not valid:
        return JsonResponse({'errors': form.errors}, status=400)
    key = f'booking-template-test:{profile.pk}:{request.user.pk}'
    if not cache.add(key, True, timeout=30):
        return JsonResponse({'error': 'Please wait 30 seconds before sending another test.'}, status=429)
    preview = booking_templates.preview(profile, form.instance)
    note = 'Test email with sample details. Links are inactive; use a demo reservation to test booking actions.'
    try:
        email = EmailMultiAlternatives(subject='[Test] ' + preview['subject'],
            body=note + '\n\n' + strip_tags(preview['html']),
            from_email=profile.school.smtp_from_email or django_settings.DEFAULT_FROM_EMAIL,
            to=[recipient], connection=get_school_email_connection(profile.school))
        email.attach_alternative(preview['html'].replace('</body>', '<p>' + note + '</p></body>'), 'text/html')
        accepted = email.send(fail_silently=False)
    except Exception:
        return JsonResponse({'error': 'Delivery could not be confirmed. Check your inbox and delivery settings before retrying.'}, status=502)
    if accepted != 1:
        return JsonResponse({'error': 'The mail service did not accept the test email. Check delivery settings.'}, status=502)
    log_admin_audit(request=request, action='action', obj=profile, changes={},
        extra={'name': 'booking_template_test_sent', 'template_id': form.instance.pk})
    local = email.connection.__class__.__module__ in {
        'django.core.mail.backends.locmem', 'django.core.mail.backends.console',
        'django.core.mail.backends.filebased', 'django.core.mail.backends.dummy'}
    return JsonResponse({'message': 'Test captured locally; no email was sent to an inbox.' if local
        else 'Test email accepted by the mail service. Check your inbox.'})
