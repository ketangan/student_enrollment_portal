from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django_ratelimit.decorators import ratelimit

from .access import require_location_access
from .models import Location, Offering, PartyPackage, PartyRequest, PartySlot, SchedulingProfile
from .capabilities import require_capability
from .parties import inquiry_token, offering_for_inquiry, review_inquiry, scoped_parties, submit_inquiry
from .party_forms import PartyInquiryForm, PartyReviewForm, PartySlotForm, PartyListFilterForm
from .reservation_queries import filter_local_dates
from .requests import browser_key
from .views import _context
from .setup import build_form, save_setup
from .inventory_forms import party_catalog_data
from .party_policy import party_policy


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="20/m", method="POST", block=True)
def guest_list(request, school_slug, token):
    from . import guest_lists
    item = guest_lists.resolve(school_slug, token)
    return _guest_list_response(request, item, lambda data: guest_lists.save(school_slug, token, data))


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def staff_guest_list(request, school_slug, public_id):
    from . import guest_lists
    item = guest_lists.for_staff(request.user, school_slug, public_id)
    if request.method == "GET":
        return redirect(reverse("scheduling:reservation_detail", kwargs={"school_slug": school_slug, "public_id": public_id}) + "#party-guests")
    return _guest_list_response(request, item,
        lambda data: guest_lists.save_for_staff(request.user, school_slug, public_id, data), staff=True)


def _guest_list_response(request, item, save, *, staff=False):
    from . import guest_lists
    form = guest_lists.GuestListForm(item=item)
    if request.method == "POST":
        try:
            item, form = save(request.POST)
        except ValidationError as exc:
            form = guest_lists.GuestListForm(request.POST, item=item)
            form.is_valid()
            form.add_error(None, exc)
        else:
            if not form.errors:
                return redirect(request.path + ("?saved=1" if "guest_list_draft" in item.snapshot else "?submitted=1"))
    due = guest_lists.due_at(item)
    response = render(request, "scheduling/guest_list.html", {
        "business": item.profile.school, "item": item, "form": form,
        "staff_entry": staff,
        "editable": guest_lists.editable(item), "due_at": due,
        "due_now": due is not None and due <= timezone.now(),
        "saved": request.GET.get("saved") == "1", "submitted": request.GET.get("submitted") == "1",
        "saved_guest_count": len(guest_lists.saved_rows(item)),
        "visual_materials": True, "party_catalog": party_catalog_data(item.party_request.offering),
    }, status=400 if form.errors else 200)
    response["Referrer-Policy"] = "same-origin"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _admin_preview(request, school_slug):
    if request.GET.get("preview") != "1" or not request.user.is_authenticated:
        return False
    try:
        require_location_access(request.user, school_slug)
    except Http404:
        return False
    return True


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def party_setup(request, school_slug):
    editing = request.POST.get("package_id") if request.method == "POST" else request.GET.get("edit")
    editing_pk = None
    if editing:
        try:
            editing_pk = int(editing)
        except (TypeError, ValueError):
            raise Http404("Package not found")
    wants_form = request.method == "POST" or editing_pk is not None or request.GET.get("new") == "1"
    profile, role = require_location_access(request.user, school_slug, "setup.edit" if wants_form else "workspace.view")
    require_capability(profile, "parties")
    context = _context(request, profile, role, "parties")
    context["rules_view"] = request.GET.get("section") == "rules"
    if wants_form and not context["location_filter"].is_valid():
        raise Http404("Location not found")
    if request.method == "GET" and request.GET.get("new") == "1" and not editing_pk:
        destination = reverse("scheduling:party_package_new", kwargs={"school_slug": school_slug})
        if context["selected_location"]:
            destination += f'?location={context["selected_location"].pk}'
        return redirect(destination)
    if editing_pk:
        scoped_form = build_form(profile, "party_packages", pk=editing_pk)
        if context["location_filter"].selected and scoped_form.instance.offering.location_id != context["location_filter"].selected.pk:
            raise Http404("Choose a package at the selected location")
    package_form = None
    slot_form = None
    slot_action = request.POST.get("slot_action") if request.method == "POST" else None
    if slot_action:
        if not editing_pk or not context["rules_view"]:
            raise Http404("Party not found")
        from .setup import _audit
        with transaction.atomic():
            SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
            profile, role = require_location_access(request.user, school_slug, "setup.edit")
            package_form = build_form(profile, "party_packages", pk=editing_pk)
            package = package_form.instance
            if slot_action in ("close", "publish"):
                try:
                    slot_id = int(request.POST.get("slot_id", ""))
                except (TypeError, ValueError):
                    raise Http404("Party time not found")
                slot = get_object_or_404(PartySlot, pk=slot_id, package=package)
                before = {"is_published": slot.is_published}
                slot.is_published = slot_action == "publish"
                try:
                    slot.full_clean()
                except ValidationError as exc:
                    slot_form = PartySlotForm(request.POST)
                    slot_form.is_valid()
                    slot_form.add_error(None, " ".join(exc.messages))
                else:
                    slot.save(update_fields=["is_published"])
                    _audit(request.user, profile, slot, before, "change")
            elif slot_action == "add":
                with timezone.override(ZoneInfo(package.offering.location.timezone_name)):
                    slot_form = PartySlotForm(request.POST, instance=PartySlot(package=package))
                    if slot_form.is_valid():
                        try:
                            slot_form.instance.full_clean()
                        except ValidationError as exc:
                            slot_form.add_error(None, " ".join(exc.messages))
                        else:
                            slot = slot_form.save()
                            _audit(request.user, profile, slot, {}, "add")
            else:
                raise Http404("Action not found")
            if slot_form is None or not slot_form.errors:
                return redirect(request.path + f"?location={package.offering.location_id}&section=rules&edit={package.pk}")
    elif request.method == "POST":
        package_form = save_setup(request.user, school_slug, "party_packages", request.POST, pk=editing_pk)
        if package_form.is_valid():
            messages.success(request, "Package saved.")
            return redirect(reverse("scheduling:party_setup", kwargs={"school_slug": school_slug}) + f"?location={package_form.instance.offering.location_id}#package-{package_form.instance.pk}")
    elif wants_form:
        package_form = build_form(profile, "party_packages", pk=editing_pk)
    if package_form and editing_pk:
        for field in package_form.fields.values():
            field.widget.attrs.update(form="package-edit", **{"aria-label": field.label or "Package name"})
        rule_fields = ("included_observers", "additional_participant_limit", "additional_participant_price", "tax_rate", "space", "room", "booking_mode")
        visible = (set(rule_fields) if context["rules_view"] else {"name", "participant_limit", "duration_minutes", "total", "deposit", "is_published"})
        context["booking_rule_fields"] = [package_form[name] for name in rule_fields if not package_form[name].is_hidden]
        context["preserved_package_fields"] = [package_form[name] for name in package_form.fields if name not in visible and name != "edit_revision"]
        context["party_slots"] = package_form.instance.slots.filter(starts_at__gt=timezone.now())
        context["slot_form"] = slot_form or PartySlotForm()
    if package_form and not editing_pk:
        from .party_forms import package_editor_context
        context.update(form=package_form, hide_setup_tabs=True,
            **package_editor_context(package_form, profile, context.get("selected_location")))
        return render(request, "scheduling/party_package_form.html", context, status=400)
    context.update(package_form=package_form, editing_pk=editing_pk)
    locations = context["location_filter"].filter(Location.objects.filter(profile=profile), "pk")
    context["locations"] = locations.prefetch_related(Prefetch("offerings",
        queryset=Offering.objects.filter(visit_program__isnull=True).filter(
            Q(accepts_party_requests=True) | Q(party_packages__isnull=False)).distinct().prefetch_related(
                Prefetch("party_packages", queryset=PartyPackage.objects.select_related("room")))))
    return render(request, "scheduling/party_setup.html", context,
                  status=400 if request.method == "POST" or not context["location_filter"].is_valid() else 200)


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def party_inquiry(request, school_slug, pk=None):
    profile = get_object_or_404(SchedulingProfile.objects.select_related("school"), school__slug=school_slug, enabled=True)
    require_capability(profile, "parties")
    packages_only = party_policy(profile.school)["packages_only"]
    from .customer_experience import customer_copy
    copy = customer_copy(profile.school)
    customer_template = "scheduling/party_experience.html" if packages_only else "scheduling/party_inquiry.html"
    presentation = {"party_experience": packages_only, "step": 1,
                    "demo": {"name": str(profile.school), "logo": copy.get("logo") or profile.school.logo_url,
                             "photo": copy.get("photo", "")}}
    offerings = Offering.objects.filter(location__profile=profile, is_published=True,
        accepts_party_requests=True, visit_program__isnull=True).select_related("location").order_by("location__name", "name", "pk")
    if packages_only:
        offerings = offerings.filter(party_packages__is_published=True, party_packages__booking_mode="direct").distinct()
    offerings = list(offerings)
    preview = _admin_preview(request, school_slug)
    if pk is None:
        raw = request.GET.get("offering")
        if raw:
            try:
                pk = int(raw)
            except ValueError:
                raise Http404("Location not found")
            if request.method == "GET" and not any(item.pk == pk for item in offerings):
                raise Http404("Location not found")
        elif request.method == "POST":
            raise Http404("Choose a location first")
        else:
            return render(request, customer_template, {**presentation,
                "business": profile.school, "offerings": offerings, "admin_preview": preview, "packages_only": packages_only,
                "form": PartyInquiryForm() if offerings and not packages_only else None, "choose_location": True})
    offering = offering_for_inquiry(school_slug, pk)
    browser = browser_key(request, create=request.method == "GET")
    error = None
    with timezone.override(ZoneInfo(offering.location.timezone_name)):
        if packages_only and request.method == "POST" and request.POST.get("validate_only") == "1":
            form = PartyInquiryForm(request.POST, offering=offering)
            errors = form.errors.get_json_data()
            # Acknowledgments are presented on the review step, after this check.
            for name in form.acknowledgment_names:
                errors.pop(name, None)
            return JsonResponse({"errors": errors})
        if request.method == "POST":
            try:
                item, form = submit_inquiry(school_slug, pk, request.POST, browser)
            except ValidationError as exc:
                error = " ".join(exc.messages)
                form = PartyInquiryForm(request.POST, offering=offering)
            else:
                if item:
                    if hasattr(item, "reservation"):
                        from .checkout import payment_token
                        if packages_only and request.POST.get("customer_flow") == "three-step":
                            from .views_checkout import customer_payment
                            return customer_payment(request, school_slug, payment_token(item.reservation))
                        return redirect("scheduling:customer_payment", school_slug=school_slug, token=payment_token(item.reservation))
                    receipt_url = reverse("scheduling:party_received", kwargs={"school_slug": school_slug, "public_id": item.public_id})
                    return redirect(receipt_url + ("?preview=1" if preview else ""))
        else:
            if not offering.is_published or not offering.accepts_party_requests:
                raise Http404("This workshop is not accepting party inquiries.")
            form = PartyInquiryForm(offering=offering)
        return render(request, customer_template, {**presentation,
            "business": offering.location.profile.school, "offering": offering, "form": form, "error": error,
            "offerings": offerings, "admin_preview": preview, "packages_only": packages_only,
            "party_catalog": party_catalog_data(offering), "visual_materials": True,
            "request_token": request.POST.get("request_token", "") if request.method == "POST" else inquiry_token(offering, browser),
        }, status=400 if request.method == "POST" else 200)


@never_cache
@require_GET
def party_received(request, school_slug, public_id):
    browser = browser_key(request)
    if not browser:
        raise Http404("Page not found")
    item = get_object_or_404(PartyRequest.objects.select_related("offering__location__profile__school"),
        public_id=public_id, browser_digest=browser, offering__location__profile__school__slug=school_slug,
        offering__location__profile__enabled=True)
    # Do not expose internal quotes, preparation notes or staff decisions on this receipt.
    return render(request, "scheduling/party_received.html", {"business": item.offering.location.profile.school,
        "has_reservation": hasattr(item, "reservation"),
        "admin_preview": _admin_preview(request, school_slug)})


@never_cache
@login_required
@require_GET
def party_list(request, school_slug):
    from .party_status import FILTERS, label
    from .reservation_queries import completion_filter_query
    profile, role = require_location_access(request.user, school_slug)
    if party_policy(profile.school)["packages_only"]:
        return redirect("scheduling:reservations", school_slug=school_slug)
    context = _context(request, profile, role, "party_inquiries")
    location_form = context["location_filter"]
    items = location_form.filter(scoped_parties(profile), "offering__location")
    review = Q(status="pending", reservation__isnull=True)
    query = completion_filter_query(context["location_query"])
    form = PartyListFilterForm(query, profile=profile)
    if form.is_valid() and form.cleaned_data["hide_completed"]:
        items = items.exclude(Q(reservation__state="completed") | Q(reservation__ends_at__lte=timezone.now())
            | Q(reservation__isnull=True, ends_at__lte=timezone.now()))
    counts = items.aggregate(total=Count("pk"), review=Count("pk", filter=review))
    if form.is_valid():
        data = form.cleaned_data
        if data["q"]:
            items = items.filter(Q(organizer_name__icontains=data["q"]) | Q(organizer_email__icontains=data["q"])
                | Q(offering_snapshot__workshop__icontains=data["q"]))
        if data["state"]:
            items = items.filter(FILTERS[data["state"]][1])
        items = filter_local_dates(items, data, profile, lambda location: Q(offering__location=location))
    else:
        items = items.none()
    context["review_only"] = request.GET.get("review") == "1"
    if context["review_only"]:
        items = items.filter(review)
    context["items"] = Paginator(items, 50).get_page(request.GET.get("page"))
    for item in context["items"]:
        item.list_status = label(item)
    query.pop("page", None)
    metrics = query.copy()
    for key in list(metrics):
        if key not in {"location", "hide_completed", "show_completed"}:
            metrics.pop(key)
    total_url = "?" + metrics.urlencode()
    metrics["review"] = "1"
    context.update(filter_form=form, total_count=counts["total"], review_count=counts["review"],
        total_url=total_url, review_url="?" + metrics.urlencode(), filters=query.urlencode(),
        has_filters=any(query.get(key) for key in ("q", "state", "date_from", "date_to")))
    return render(request, "scheduling/party_list.html", context, status=200 if form.is_valid() and location_form.is_valid() else 400)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def party_detail(request, school_slug, public_id):
    from . import reservation_notes
    from .party_status import label
    from .reservation_workspace import participant_table, party_participants
    profile, role = require_location_access(request.user, school_slug, "sessions.edit" if request.method == "POST" else "workspace.view")
    item = get_object_or_404(scoped_parties(profile), public_id=public_id)
    if request.method == "GET" and getattr(item, "reservation", None):
        return redirect("scheduling:reservation_detail", school_slug=school_slug, public_id=item.reservation.public_id)
    if party_policy(profile.school)["packages_only"]:
        if request.method == "POST":
            raise Http404("The inquiry workflow is no longer available.")
        return redirect("scheduling:reservations", school_slug=school_slug)
    notes_form = None
    if request.method == "POST" and request.POST.get("action") == "save_notes":
        notes_form = reservation_notes.update(request.user, school_slug, public_id, request.POST, inquiry=True)
        if notes_form.is_valid():
            return redirect(request.path + "#internal-notes")
        form = None
    elif request.method == "POST":
        form = review_inquiry(request.user, school_slug, public_id, request.POST)
        if form.is_valid():
            messages.success(request, "Review saved. No reservation or payment has been created.")
            return redirect("scheduling:party_detail", school_slug=school_slug, public_id=public_id)
    else:
        form = None
    item = get_object_or_404(scoped_parties(profile), public_id=public_id)
    context = _context(request, profile, role, "party_inquiries")
    tab = "preparation" if request.GET.get("tab") == "preparation" else "details"
    if form is not None and ("room" in form.errors or "duration_minutes" in form.errors
            or (form.material_forms is not None and not form.material_forms.is_valid())):
        tab = "preparation"
    context["booking_navigation"]["section"]["tabs"] = [
        {"label": "Party details", "url": request.path, "active": tab == "details"},
        {"label": "Party Prep", "url": request.path + "?tab=preparation", "active": tab == "preparation"},
    ]
    people = party_participants(item)
    context.update(item=item, form=form if form is not None else PartyReviewForm(instance=item),
        party_status=label(item), reservation_tab=tab, participant_table=participant_table(people),
        party_duration=int((item.ends_at - item.starts_at).total_seconds() // 60) if item.ends_at else None,
        notes_form=notes_form if notes_form is not None else reservation_notes.form_for(item, profile))
    context.update(reservation_notes.context_for(item, request.GET.get("notes_page"), inquiry=True))
    return render(request, "scheduling/party_detail.html", context, status=400 if request.method == "POST" else 200)
