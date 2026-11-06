import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.payments.models import PaymentAttempt
from . import terminal_checkout
from .access import require_location_access, get_staff_reservation
from .checkout import recover_attempt, request_close
from .integrations import square_connection, square_terminals


@never_cache
@login_required
@require_POST
def collect(request, school_slug, public_id):
    profile, _ = require_location_access(request.user, school_slug, "setup.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    action = request.POST.get("action")
    if action not in {"collect", "check", "cancel", "retry"}:
        return HttpResponseBadRequest("Choose a Terminal action.")
    try:
        if action == "collect":
            try:
                key = uuid.UUID(request.POST.get("key", ""))
                device = uuid.UUID(request.POST.get("device", ""))
                expected_balance = int(request.POST.get("expected_balance", ""))
                expected_due = int(request.POST.get("expected_due", "")) if request.POST.get("payment_option") == "due" else None
            except (ValueError, TypeError):
                raise ValidationError("Reload the payment form before trying again.") from None
            attempt = terminal_checkout.prepare(request.user, school_slug, public_id, device, key,
                request.POST.get("payment_option", "full"), expected_balance, expected_due,
                collection=request.POST if "amount" in request.POST else None)
            terminal_checkout.submit(attempt)
        else:
            try:
                key = uuid.UUID(request.POST.get("attempt", ""))
            except (ValueError, TypeError):
                raise ValidationError("Choose a valid Terminal payment.") from None
            attempt = get_object_or_404(PaymentAttempt, public_id=key, order=item.order, collection_method="terminal")
            if action == "cancel":
                request_close(request.user, school_slug, public_id, key)
            if action == "retry":
                terminal_checkout.retry(attempt)
            else:
                if action != "check" or request.POST.get("automatic") != "yes" or not attempt.recovery_failures:
                    recover_attempt(attempt, force=True, interactive_terminal=True)
                else:
                    recover_attempt(attempt, interactive_terminal=True)
        attempt.refresh_from_db()
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"state": attempt.state, "status": attempt.terminal_status,
                "attention": attempt.attention, "paused": attempt.recovery_failures >= 6,
                "active": attempt.state in PaymentAttempt.ACTIVE})
    except ValidationError as exc:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"error": " ".join(exc.messages)}, status=400)
        messages.error(request, " ".join(exc.messages))
    return redirect("scheduling:reservation_detail", school_slug=school_slug, public_id=public_id)


@never_cache
@login_required
@require_POST
def settings_update(request, school_slug):
    profile, _ = square_connection.access(request, school_slug)
    action = request.POST.get("action")
    if action not in {"add", "check", "disable", "enable", "ping"}:
        return HttpResponseBadRequest("Choose a Terminal action.")
    try:
        if action == "add":
            device = square_terminals.add(request, school_slug, request.POST)
            key = device.pk
        else:
            try:
                key = uuid.UUID(request.POST.get("device", ""))
            except (ValueError, TypeError):
                raise ValidationError("Choose a valid Terminal.") from None
        if action == "ping":
            device = square_terminals.ping(request, school_slug, key, automatic=request.POST.get("automatic") == "yes")
            labels = {"COMPLETED": "Terminal responded. Connection verified.", "CANCELED": "Terminal did not respond. Check its power, Wi-Fi and device-code sign-in."}
            message = labels.get(device.ping_status, "Checking Terminal connection...")
            if request.headers.get("Accept") == "application/json":
                return JsonResponse({"active": device.ping_status in {"PENDING", "IN_PROGRESS"}, "message": message})
            messages.info(request, message)
        elif action == "disable":
            square_terminals.disable(request, school_slug, key)
        else:
            code = square_terminals.check(request, school_slug, key, enable=action == "enable")
            if code:
                from .views import _context
                context = _context(request, profile, "owner", "integrations")
                context["pairing_code"] = code
                response = render(request, "scheduling/integrations/terminal_pairing.html", context)
                response["Referrer-Policy"] = "no-referrer"
                return response
    except ValidationError as exc:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse({"error": " ".join(exc.messages)}, status=400)
        messages.error(request, " ".join(exc.messages))
    return redirect("scheduling:integrations", school_slug=school_slug)
