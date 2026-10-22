from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import messaging
from .access import require_access
from .appointment_models import LessonBookingNotice
from .appointments import capture_notice
from .lessons import audit
from .message_forms import MessageSettingsForm, ProcessMessagesForm
from .message_models import BookingMessage
from .views import _context
from .views_family import private_page


@private_page
@login_required
@require_http_methods(["GET", "POST"])
def inbox(request, school_slug):
    messaging.require_preview()
    profile, role = require_access(request.user, school_slug, "setup.edit" if request.method == "POST" else "workspace.view")
    settings_form = MessageSettingsForm(instance=profile)
    process_form = ProcessMessagesForm()
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "settings":
            with transaction.atomic():
                profile, role = messaging.locked_access(request.user, school_slug)
                settings_form = MessageSettingsForm(request.POST, instance=profile)
                if settings_form.is_valid():
                    settings_form.save()
                    audit(request.user, profile, profile, "change", {"message_preview_enabled": profile.message_preview_enabled})
                    messages.success(request, "Local processing settings saved. Older messages are not automatically queued.")
                    return redirect("scheduling:messages", school_slug=school_slug)
        elif action == "process":
            process_form = ProcessMessagesForm(request.POST)
            if process_form.is_valid():
                count = messaging.process_batch(profile.pk, **process_form.cleaned_data)
                messages.info(request, f"Processed {count} local message(s). No email was sent externally.")
                return redirect("scheduling:messages", school_slug=school_slug)
        else:
            process_form = ProcessMessagesForm(request.POST)
            process_form.add_error(None, "Choose a valid action.")
    context = _context(request, profile, role, "messages")
    queue = BookingMessage.objects.filter(profile=profile).annotate(
        blocking_message_id=Subquery(messaging.unresolved_messages(profile, OuterRef("group_key")).values("public_id")[:1])
    ).order_by("-pk")
    context.update(settings_form=settings_form, process_form=process_form,
        queued_messages=Paginator(queue, 25).get_page(request.GET.get("page")))
    return render(request, "scheduling/messages.html", context,
        status=400 if settings_form.errors or process_form.errors else 200)


@private_page
@login_required
@require_http_methods(["GET", "POST"])
def detail(request, school_slug, public_id):
    profile, role = require_access(request.user, school_slug, "setup.edit" if request.method == "POST" else "workspace.view")
    message = get_object_or_404(BookingMessage, profile=profile, public_id=public_id)
    if message.reservation_id:
        require_access(request.user, school_slug, "setup.edit", capability="parties")
    else:
        messaging.require_preview()
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("confirm") != "yes":
                raise ValidationError("Confirm the message action first.")
            messaging.change_message(request.user, school_slug, public_id, request.POST.get("action"))
        except ValidationError as exc:
            error = "; ".join(exc.messages)
        else:
            return redirect("scheduling:message_detail", school_slug=school_slug, public_id=public_id)
    context = _context(request, profile, role, "guest_communication" if message.reservation_id else "messages")
    from .party_notifications import live_enabled
    context["queue_available"] = message.can_queue and (not message.reservation_id
        or messaging.preview_enabled() and profile.message_preview_enabled
        or live_enabled(profile) and message.reservation.order.environment == "live")
    blocker = messaging.unresolved_messages(profile, message.group_key).first() if message.state in {"queued", "retry"} else None
    from .models import VisitAccessGrant
    from .visits import enrollment_access
    grant = VisitAccessGrant.objects.select_related("booking").filter(delivery=message).first()
    private_link = bool(grant)
    may_open = role == "owner" and (not private_link or not (grant.booking.lead_id or grant.booking.admission_case_id)
        or enrollment_access(request.user, profile, owner=True))
    context.update(item=message, attempts=message.delivery_attempts.all(), error=error, blocker=blocker)
    context["message_body"] = message.body if not private_link or may_open else "Private access link hidden. Only the authorized owner can inspect this local email."
    return render(request, "scheduling/message_detail.html", context, status=400 if error else 200)


@private_page
@login_required
@require_POST
def queue_notice(request, school_slug, pk):
    with transaction.atomic():
        profile, _ = messaging.locked_access(request.user, school_slug)
        notice = get_object_or_404(LessonBookingNotice, pk=pk, booking__series__profile=profile)
        try:
            if request.POST.get("confirm") != "yes" or notice.booking.notices.first().pk != notice.pk:
                raise ValidationError("Confirm the latest update. An older confirmation cannot be queued.")
            message = notice.delivery or capture_notice(notice)
            if message.state != "queued":
                messaging.change_message(request.user, school_slug, message.public_id, "queue")
        except ValidationError as exc:
            transaction.set_rollback(True)
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "Latest confirmation queued for local testing.")
    return redirect("scheduling:lesson_booking_detail", school_slug=school_slug, public_id=notice.booking.public_id)
