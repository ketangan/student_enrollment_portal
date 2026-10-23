from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django_ratelimit.decorators import ratelimit

from .access import require_location_access
from .models import Location, Offering, PartyPackage, PartyRequest, PartySlot, SchedulingProfile
from .capabilities import require_capability
from .parties import inquiry_token, offering_for_inquiry, review_inquiry, room_overlaps, scoped_parties, submit_inquiry
from .party_forms import PartyInquiryForm, PartyReviewForm, PartySlotForm
from .requests import browser_key
from .views import _context
from .setup import build_form, save_setup
from .inventory_forms import party_catalog_data


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
                return redirect(request.path + ("?saved=1" if request.POST["action"] == "draft" else "?submitted=1"))
    due = guest_lists.due_at(item)
    response = render(request, "scheduling/guest_list.html", {
        "business": item.profile.school, "item": item, "form": form,
        "staff_entry": staff,
        "editable": guest_lists.editable(item), "due_at": due,
        "due_now": due is not None and due <= timezone.now(),
        "saved": request.GET.get("saved") == "1", "submitted": request.GET.get("submitted") == "1",
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
        context["booking_rule_fields"] = [package_form[name] for name in rule_fields]
        context["preserved_package_fields"] = [package_form[name] for name in package_form.fields if name not in visible and name != "edit_revision"]
        context["party_slots"] = package_form.instance.slots.filter(starts_at__gt=timezone.now())
        context["slot_form"] = slot_form or PartySlotForm()
    context.update(package_form=package_form, editing_pk=editing_pk,
                   share_url=request.build_absolute_uri(reverse("scheduling:party_start", kwargs={"school_slug": school_slug})))
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
    offerings = list(Offering.objects.filter(location__profile=profile, is_published=True,
        accepts_party_requests=True, visit_program__isnull=True).select_related("location").order_by("location__name", "name", "pk"))
    preview = _admin_preview(request, school_slug)
    if pk is None:
        raw = request.GET.get("offering")
        if raw:
            try:
                pk = int(raw)
            except ValueError:
                raise Http404("Location not found")
            if not any(item.pk == pk for item in offerings):
                raise Http404("Location not found")
        elif request.method == "POST":
            raise Http404("Choose a location first")
        else:
            return render(request, "scheduling/party_inquiry.html", {
                "business": profile.school, "offerings": offerings, "admin_preview": preview,
                "form": PartyInquiryForm() if offerings else None, "choose_location": True})
    offering = offering_for_inquiry(school_slug, pk)
    browser = browser_key(request, create=request.method == "GET")
    error = None
    with timezone.override(ZoneInfo(offering.location.timezone_name)):
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
                        return redirect("scheduling:customer_payment", school_slug=school_slug, token=payment_token(item.reservation))
                    receipt_url = reverse("scheduling:party_received", kwargs={"school_slug": school_slug, "public_id": item.public_id})
                    return redirect(receipt_url + ("?preview=1" if preview else ""))
        else:
            if not offering.is_published or not offering.accepts_party_requests:
                raise Http404("This workshop is not accepting party inquiries.")
            form = PartyInquiryForm(offering=offering)
        return render(request, "scheduling/party_inquiry.html", {
            "business": offering.location.profile.school, "offering": offering, "form": form, "error": error,
            "offerings": offerings, "admin_preview": preview,
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
    profile, role = require_location_access(request.user, school_slug)
    context = _context(request, profile, role, "parties")
    form = context["location_filter"]
    context.update(location_filter=form, selected_location=form.selected)
    items = form.filter(scoped_parties(profile), "offering__location")
    context["review_only"] = request.GET.get("review") == "1"
    if context["review_only"]:
        items = items.filter(status="pending", reservation__isnull=True)
    context["items"] = Paginator(items, 50).get_page(request.GET.get("page"))
    return render(request, "scheduling/party_list.html", context, status=200 if form.is_valid() else 400)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def party_detail(request, school_slug, public_id):
    profile, role = require_location_access(request.user, school_slug, "sessions.edit" if request.method == "POST" else "workspace.view")
    if request.method == "POST":
        form = review_inquiry(request.user, school_slug, public_id, request.POST)
        if form.is_valid():
            messages.success(request, "Review saved. No reservation or payment has been created.")
            return redirect("scheduling:party_detail", school_slug=school_slug, public_id=public_id)
    else:
        form = None
    item = get_object_or_404(scoped_parties(profile), public_id=public_id)
    context = _context(request, profile, role, "parties")
    sessions, parties = room_overlaps(item)
    context.update(item=item, form=form or PartyReviewForm(instance=item), overlapping_sessions=sessions, overlapping_parties=parties)
    return render(request, "scheduling/party_detail.html", context, status=400 if request.method == "POST" else 200)
