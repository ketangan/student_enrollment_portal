from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_http_methods

from core.payments import refunds
from core.payments.models import Refund
from .access import get_staff_reservation, require_location_access
from .checkout import approve_refund, resolve_review, supports_refund_adjustment
from .reservation_forms import RefundForm
from .views import _context
from .reservations import _audit


def process_refund(user, item, refund, *, retry_submission=False, automatic=False):
    previous = (refund.state, refund.attention)
    refunds.process(refund, force=not automatic or not refund.recovery_failures,
        retry_submission=retry_submission, interactive=supports_refund_adjustment(item))
    refund.refresh_from_db()
    if previous != (refund.state, refund.attention):
        _audit(user, item, "refund_status", refund=str(refund.public_id), status=refund.get_state_display())


def _item(request, slug, public_id):
    profile, role = require_location_access(request.user, slug, "setup.edit", capability="reservations")
    return profile, role, get_staff_reservation(profile, public_id, exclude_visits=True)


def _back(slug, public_id):
    return redirect("scheduling:reservation_detail", school_slug=slug, public_id=public_id)


@never_cache
@login_required
@require_POST
def payment_review(request, school_slug, public_id):
    _item(request, school_slug, public_id)
    if request.POST.get("confirm") != "yes":
        return HttpResponseBadRequest("Confirm the reservation first.")
    try:
        resolve_review(request.user, school_slug, public_id)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        messages.success(request, "Payment and capacity checked. Reservation confirmed.")
    return _back(school_slug, public_id)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def refund_create(request, school_slug, public_id):
    profile, role, item = _item(request, school_slug, public_id)
    if supports_refund_adjustment(item):
        return _back(school_slug, public_id)
    form = RefundForm(request.POST if request.method == "POST" else None, order=item.order)
    if request.method == "POST" and form.is_valid():
        try:
            refund = approve_refund(request.user, school_slug, public_id, form.cleaned_data["attempt"],
                key=form.cleaned_data["key"], amount_cents=int(form.cleaned_data["amount"] * 100), reason=form.cleaned_data["reason"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            process_refund(request.user, item, refund)
            messages.info(request, "Refund request saved. Only a verified successful refund changes the recorded totals.")
            return _back(school_slug, public_id)
    context = _context(request, profile, role, "reservations")
    context.update(item=item, form=form)
    return render(request, "scheduling/refund_create.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_POST
def refund_recover(request, school_slug, public_id, refund_id):
    _, _, item = _item(request, school_slug, public_id)
    refund = get_object_or_404(Refund, public_id=refund_id, attempt__order=item.order)
    action = request.POST.get("action", "check")
    automatic = request.POST.get("automatic") == "yes"
    if action == "check_all" and supports_refund_adjustment(item):
        for pending in Refund.objects.filter(attempt__order=item.order, state__in=Refund.ACTIVE).select_related(
                "attempt__account", "attempt__order").order_by("pk"):
            process_refund(request.user, item, pending, automatic=automatic)
        refund.refresh_from_db()
    elif action not in {"check", "retry"}:
        return HttpResponseBadRequest("Choose check or retry.")
    if action == "retry" and refund.attempt.account.provider != "square":
        return HttpResponseBadRequest("Submission retries are only supported for Square refunds.")
    if action != "check_all":
        process_refund(request.user, item, refund, retry_submission=action == "retry", automatic=automatic)
    if request.headers.get("Accept") == "application/json":
        result = {"state": refund.state, "status": refund.state, "attention": refund.attention}
        if action == "check_all":
            # Include settled parts too: a webhook may have completed one since the page rendered.
            result["refunds"] = list(Refund.objects.filter(attempt__order=item.order).values("public_id", "state", "attention"))
        return JsonResponse(result)
    messages.info(request, "Refund check requested. Review the saved status; recent checks are not repeated immediately.")
    return _back(school_slug, public_id)
