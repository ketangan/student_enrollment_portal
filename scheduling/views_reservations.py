from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from django.utils import timezone

from core.payments.service import totals
from core.payments.models import PaymentAttempt, Refund
from .access import get_staff_reservation, require_location_access, staff_locations
from .models import PartyRequest
from .reservation_forms import CancellationForm, QuoteForm, ReservationFilterForm
from .reservation_queries import ATTENTION, staff_reservations, filter_reservations, reservation_location, preparation_queue
from .reservations import create_reservation, locked_profile
from .views import _context
from . import preparation, reservation_notes
from .reservation_workspace import detail_context, prep_context, inline_selections
from .reservation_audit import audit_page


@never_cache
@login_required
@require_GET
def reservation_list(request, school_slug):
    profile, role = require_location_access(request.user, school_slug, capability="reservations")
    context = _context(request, profile, role, "reservations")
    form = ReservationFilterForm(context["location_query"], profile=profile)
    scoped = staff_reservations(profile)
    if not context["location_filter"].is_valid():
        scoped = scoped.none()
    elif context["selected_location"]:
        scoped = scoped.filter(reservation_location(context["selected_location"]))
    counts = scoped.aggregate(total=Count("pk"), attention=Count("pk", filter=ATTENTION))
    context.update(attention_count=counts["attention"], total_count=counts["total"])
    items = filter_reservations(scoped, form, profile)
    context["attention_only"] = request.GET.get("attention") == "1"
    if context["attention_only"]:
        items = items.filter(ATTENTION)
    context["preparation_only"] = request.GET.get("preparation") == "1"
    if context["preparation_only"]:
        items = preparation_queue(items, timezone.now())
    ordering = {"name": "snapshot__name", "-name": "-snapshot__name", "scheduled": "starts_at", "-scheduled": "-starts_at"}
    sort = request.GET.get("sort", "")
    if sort in ordering:
        items = items.order_by(ordering[sort], "pk")
    context["items"] = Paginator(items, 50).get_page(request.GET.get("page"))
    filters = context["location_query"].copy()
    filters.pop("page", None)
    filters.pop("attention", None)
    if not filters.get("location"):
        filters.pop("location", None)
    tab_filters = filters.copy()
    tab_filters.pop("preparation", None)
    sort_urls = {}
    for key in ("name", "scheduled"):
        query = context["location_query"].copy()
        query.pop("page", None)
        query["sort"] = "-" + key if sort == key else key
        sort_urls[key] = "?" + query.urlencode()
    context.update(filter_form=form, filters=filters.urlencode(),
        tab_filters=tab_filters.urlencode(), sort_urls=sort_urls, sort=sort,
        has_filters=any(value for key, value in filters.items() if key not in {"location", "sort"}))
    return render(request, "scheduling/reservations.html", context)


@never_cache
@login_required
@require_POST
@transaction.atomic
def reservation_settings(request, school_slug):
    from .access import require_access
    require_access(request.user, school_slug, "setup.edit")
    profile = locked_profile(request.user, school_slug)
    require_access(request.user, school_slug, "setup.edit")
    action = request.POST.get("action")
    if action not in {"enable", "disable"}:
        return HttpResponseBadRequest("Choose enable or disable.")
    profile.native_reservations_enabled = action == "enable"
    profile.save(update_fields=["native_reservations_enabled"])
    from core.models import AdminAuditLog
    AdminAuditLog.objects.create(actor=request.user, action="change", model_label="scheduling.schedulingprofile",
        object_id=str(profile.pk), object_repr=str(profile.pk), changes={"native_reservations_enabled": profile.native_reservations_enabled})
    return redirect("scheduling:reservations", school_slug=school_slug)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def reservation_create(request, school_slug, kind, public_id):
    profile, role = require_location_access(request.user, school_slug, "setup.edit", capability="reservations")
    initial = {}
    if kind == "party":
        source = get_object_or_404(PartyRequest, public_id=public_id, offering__location__in=staff_locations(profile))
        initial = {"total": source.proposed_total, "deposit": source.proposed_deposit, "source_revision": source.revision}
    else:
        from .models import WorkshopRequest
        source = get_object_or_404(WorkshopRequest, public_id=public_id, session__offering__location__in=staff_locations(profile))
        initial["deposit"] = source.session.offering.deposit_per_participant * source.participants.count()
    form = QuoteForm(request.POST if request.method == "POST" else None, initial=initial)
    form.fields["online"].label = f"Accept online payment with {profile.get_payment_provider_display()}"
    if kind == "party":
        form.fields["total"].disabled = form.fields["deposit"].disabled = True
        form.fields["source_revision"].required = True
    if request.method == "POST" and form.is_valid():
        try:
            item = create_reservation(request.user, school_slug, kind, public_id,
                int(form.cleaned_data["total"] * 100), int(form.cleaned_data["deposit"] * 100),
                source_revision=form.cleaned_data["source_revision"], online=form.cleaned_data["online"])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            return redirect("scheduling:reservation_detail", school_slug=school_slug, public_id=item.public_id)
    context = _context(request, profile, role, "reservations")
    context.update(form=form, source=source)
    return render(request, "scheduling/reservation_create.html", context, status=400 if request.method == "POST" else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def reservation_detail(request, school_slug, public_id):
    return _reservation_page(request, school_slug, public_id)


def _reservation_page(request, school_slug, public_id, tab="details"):
    from . import terminal_payments
    profile, role = require_location_access(request.user, school_slug, capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    error = ""
    action = request.POST.get("action") if request.method == "POST" else None
    if tab == "preparation" and request.method == "POST" and action not in {"save_notes", "save_selections"}:
        return HttpResponseBadRequest("Choose a preparation action.")
    notes_form = None
    if action == "save_notes":
        notes_form = reservation_notes.update(request.user, school_slug, public_id, request.POST)
        if notes_form.is_valid():
            messages.success(request, "Note added.")
            query = request.GET.copy()
            query.pop("notes_page", None)
            return redirect(request.path + ("?" + query.urlencode() if query else "") + "#internal-notes")
    elif action == "save_selections":
        from . import workshop_selections
        try:
            participant_id = int(request.POST.get("participant_id", ""))
        except (ValueError, TypeError):
            return HttpResponseBadRequest("Choose a participant.")
        try:
            workshop_selections.update(request.user, school_slug, public_id, participant_id, request.POST)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, "Participant selections saved.")
            query = request.GET.copy()
            query.pop("edit_participant", None)
            return redirect(request.path + ("?" + query.urlencode() if query else "") + "#participants-title")
    elif request.method == "POST":
        require_location_access(request.user, school_slug, "setup.edit", capability="reservations")
        try:
            if action == "record_terminal":
                terminal_payments.record(request.user, school_slug, public_id, request.POST)
            elif action == "void_terminal":
                try:
                    entry_id = int(request.POST.get("entry_id", ""))
                except (ValueError, TypeError):
                    return HttpResponseBadRequest("Choose a recorded payment.")
                terminal_payments.void(request.user, school_slug, public_id, entry_id, request.POST)
            else:
                return HttpResponseBadRequest("Choose a payment action.")
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, "Terminal payment recorded. No card was charged." if action == "record_terminal"
                else "Incorrect entry voided. No money was refunded.")
            return redirect("scheduling:reservation_detail", school_slug=school_slug, public_id=public_id)
    context = detail_context(request, _context(request, profile, role, "reservations"), item, tab)
    context["selection_error"] = error if action == "save_selections" else ""
    if context["selection_error"]:
        from .requests import catalog_for
        for entry in context.get("selection_forms", []):
            if entry["person"].pk == participant_id:
                entry["form"] = workshop_selections.form_for(entry["person"],
                    catalog_for(item.workshop_request.session.offering), request.POST)
                entry["error"] = error
    context["notes_form"] = notes_form if notes_form is not None else reservation_notes.form_for(item, profile)
    context.update(reservation_notes.context_for(item, request.GET.get("notes_page")))
    context["audit_open"] = "audit_page" in request.GET
    if tab == "preparation":
        context.update(prep_context(item, profile))
    inline_selections(request, context)
    if tab == "preparation":
        return render(request, "scheduling/reservation_preparation.html", context,
            status=400 if error or (notes_form is not None and notes_form.errors) else 200)
    context.update(amounts=totals(item.order), audit=audit_page(item, request.GET.get("audit_page")))
    if item.order.account_id:
        from .checkout import payment_token
        from django.urls import reverse
        context["payment_url"] = reverse("scheduling:customer_payment", kwargs={"school_slug": school_slug, "token": payment_token(item)})
        if item.party_request_id and item.state in ("held", "confirmed") and context["amounts"]["outstanding_cents"]:
            context["share_payment_url"] = request.build_absolute_uri(context["payment_url"])
        context["attempts"] = item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).order_by("-created_at")
        context["checkout_pending"] = item.order.attempts.filter(state__in=PaymentAttempt.ACTIVE).exists()
        context["overpaid"] = context["amounts"]["net_cents"] > context["amounts"]["total_cents"]
        context["refunds"] = Refund.objects.filter(attempt__order=item.order, state__in=Refund.ACTIVE).order_by("-created_at")
    context.update(terminal_error=error if action != "save_selections" else "",
        terminal_form=terminal_payments.TerminalPaymentForm(request.POST if action == "record_terminal" else None,
            initial={"amount": context["amounts"]["outstanding"], "expected_balance": context["amounts"]["outstanding_cents"],
                     "expected_total": context["amounts"]["total_cents"], "paid_on": terminal_payments.studio_today(item)}),
        terminal_entries=item.order.entries.filter(channel="terminal", kind="payment").select_related("actor", "reversal"))
    return render(request, "scheduling/reservation_detail.html", context,
        status=400 if error or (notes_form is not None and notes_form.errors) else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def reservation_preparation(request, school_slug, public_id):
    return _reservation_page(request, school_slug, public_id, "preparation")


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def reservation_complete(request, school_slug, public_id):
    from .completion import complete
    profile, role = require_location_access(request.user, school_slug, "sessions.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") != "complete":
                raise ValidationError("Confirm completion to update this reservation and its materials.")
            complete(request.user, school_slug, public_id)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, "Reservation completed. Materials recorded as used.")
            return redirect("scheduling:reservation_detail", school_slug=school_slug, public_id=public_id)
    context = detail_context(request, _context(request, profile, role, "reservations"), item)
    context.update(error=error, completion_review=True)
    return render(request, "scheduling/reservation_complete.html", context, status=400 if error else 200)


@never_cache
@login_required
@require_POST
def preparation_update(request, school_slug, public_id):
    profile, _ = require_location_access(request.user, school_slug, "sessions.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    try:
        preparation.update(request.user, school_slug, public_id, request.POST)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Preparation saved. Stock and payments are unchanged.")
    if request.POST.get("return_to") == "preparation" and item.workshop_request_id:
        from .experience_demo.config import available
        if available(profile):
            from django.urls import reverse
            return redirect(f'{reverse("scheduling:experience_preparation", kwargs={"school_slug": school_slug})}?session={item.session_id}')
    return redirect("scheduling:reservation_preparation", school_slug=school_slug, public_id=public_id)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def reservation_cancel(request, school_slug, public_id):
    from .cancellation import cancel
    from .views_recovery import process_refund
    profile, role = require_location_access(request.user, school_slug, "setup.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    form = CancellationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        try:
            _, approved = cancel(request.user, school_slug, public_id, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            for refund in approved:
                process_refund(request.user, item, refund)
            if form.cleaned_data["resolution"] == "with_refund":
                messages.success(request, "Reservation canceled. Refund requests saved; check their status below." if approved else
                    "Reservation canceled and simulated payment refunded.")
            else:
                messages.success(request, "Reservation canceled without a refund. Payments remain recorded.")
            return redirect("scheduling:reservation_detail", school_slug=school_slug, public_id=public_id)
    context = detail_context(request, _context(request, profile, role, "reservations"), item)
    context.update(form=form, amounts=totals(item.order), cancellation_review=True)
    return render(request, "scheduling/reservation_cancel.html", context, status=400 if request.method == "POST" else 200)
