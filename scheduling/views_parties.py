from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django_ratelimit.decorators import ratelimit

from .access import require_access
from .models import Location, Offering, PartyRequest, SchedulingProfile
from .capabilities import require_capability
from .parties import inquiry_token, offering_for_inquiry, review_inquiry, room_overlaps, scoped_parties, submit_inquiry
from .party_forms import PartyInquiryForm, PartyReviewForm
from .requests import browser_key
from .views import _context
from .setup import build_form, save_setup
from .inventory_forms import party_catalog_data


def _admin_preview(request, school_slug):
    if request.GET.get("preview") != "1" or not request.user.is_authenticated:
        return False
    try:
        require_access(request.user, school_slug)
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
    profile, role = require_access(request.user, school_slug, "setup.edit" if wants_form else "workspace.view")
    require_capability(profile, "parties")
    context = _context(request, profile, role, "parties")
    if wants_form and not context["location_filter"].is_valid():
        raise Http404("Location not found")
    if editing_pk:
        scoped_form = build_form(profile, "party_packages", pk=editing_pk)
        if context["location_filter"].selected and scoped_form.instance.offering.location_id != context["location_filter"].selected.pk:
            raise Http404("Choose a package at the selected location")
    package_form = None
    if request.method == "POST":
        package_form = save_setup(request.user, school_slug, "party_packages", request.POST, pk=editing_pk)
        if package_form.is_valid():
            messages.success(request, "Package saved.")
            return redirect(reverse("scheduling:party_setup", kwargs={"school_slug": school_slug}) + f"?location={package_form.instance.offering.location_id}")
    elif wants_form:
        package_form = build_form(profile, "party_packages", pk=editing_pk)
    if package_form and editing_pk:
        for field in package_form.fields.values():
            field.widget.attrs.update(form="package-edit", **{"aria-label": field.label or "Package name"})
    context.update(package_form=package_form, editing_pk=editing_pk,
                   share_url=request.build_absolute_uri(reverse("scheduling:party_start", kwargs={"school_slug": school_slug})))
    locations = context["location_filter"].filter(Location.objects.filter(profile=profile), "pk")
    context["locations"] = locations.prefetch_related(Prefetch("offerings",
        queryset=Offering.objects.filter(visit_program__isnull=True).filter(
            Q(accepts_party_requests=True) | Q(party_packages__isnull=False)).distinct().prefetch_related("party_packages")))
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
        elif offerings:
            pk = offerings[0].pk
        else:
            return render(request, "scheduling/party_inquiry.html", {
                "business": profile.school, "offerings": offerings, "admin_preview": preview})
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
    profile, role = require_access(request.user, school_slug)
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
    profile, role = require_access(request.user, school_slug, "sessions.edit" if request.method == "POST" else "workspace.view")
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
