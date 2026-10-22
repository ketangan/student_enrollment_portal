from django.contrib import messages
from django import forms
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from core.services.portal_environment import scope_schools
from .access import require_access
from .capabilities import require_capability
from .guides import GuideForm, replace, uploads_enabled
from .models import SchedulingProfile
from .views import _context


class NotificationSettingsForm(forms.ModelForm):
    class Meta:
        model = SchedulingProfile
        fields = ["party_notifications_enabled"]
        labels = {"party_notifications_enabled": "Send party confirmations and guest-list reminders"}


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def settings(request, school_slug):
    profile, role = require_access(request.user, school_slug, "setup.edit", capability="parties")
    get_object_or_404(scope_schools(SchedulingProfile.objects.all(), request, "school__"), pk=profile.pk)
    current = profile.design_guides.first()
    notification_action = request.method == "POST" and request.POST.get("action") == "notifications"
    form = GuideForm(request.POST if request.method == "POST" and not notification_action else None, request.FILES or None,
        initial={"current_version": current.pk if current else 0})
    notification_form = NotificationSettingsForm(request.POST if notification_action else None, instance=profile)
    if notification_action and notification_form.is_valid():
        with transaction.atomic():
            SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
            require_access(request.user, school_slug, "setup.edit", capability="parties")
            from .setup import _audit
            before = {"party_notifications_enabled": SchedulingProfile.objects.get(pk=profile.pk).party_notifications_enabled}
            profile.party_notifications_enabled = notification_form.cleaned_data["party_notifications_enabled"]
            profile.save(update_fields=["party_notifications_enabled"])
            _audit(request.user, profile, profile, before, "change")
        messages.success(request, "Notification settings saved.")
        return redirect(request.path)
    if request.method == "POST" and not notification_action and form.is_valid():
        try:
            replace(request.user, school_slug, form)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Design guide updated.")
            return redirect(request.path)
    context = _context(request, profile, role, "guest_communication")
    from .message_models import BookingMessage
    from .party_notifications import live_enabled
    context.update(form=form, notification_form=notification_form, live_delivery=live_enabled(profile),
                   recent_messages=BookingMessage.objects.filter(profile=profile, reservation__isnull=False)[:20],
                   current_guide=current, guide_versions=profile.design_guides.select_related("uploaded_by")[:20],
                   uploads_enabled=uploads_enabled())
    return render(request, "scheduling/guest_communication.html", context, status=400 if form.errors else 200)


@never_cache
@require_GET
def download(request, school_slug, pk=None):
    profile = get_object_or_404(scope_schools(SchedulingProfile.objects.select_related("school"), request, "school__"),
                               school__slug=school_slug, enabled=True)
    require_capability(profile, "parties")
    if pk is not None:
        require_access(request.user, school_slug, "setup.edit", capability="parties")
        guide = get_object_or_404(profile.design_guides, pk=pk)
    else:
        guide = profile.design_guides.first()
    if guide is None:
        raise Http404
    try:
        stream = guide.file.open("rb")
    except FileNotFoundError:
        raise Http404
    response = FileResponse(stream, as_attachment=True, filename="design-guide.pdf", content_type="application/pdf")
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
