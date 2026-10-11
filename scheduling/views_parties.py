from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods
from django_ratelimit.decorators import ratelimit

from .access import require_access
from .models import PartyRequest
from .parties import inquiry_token, offering_for_inquiry, review_inquiry, room_overlaps, scoped_parties, submit_inquiry
from .party_forms import PartyInquiryForm, PartyReviewForm
from .requests import browser_key
from .views import _context


@never_cache
@require_http_methods(["GET", "POST"])
@ratelimit(key="ip", rate="10/m", method="POST", block=True)
def party_inquiry(request, school_slug, pk):
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
                    return redirect("scheduling:party_received", school_slug=school_slug, public_id=item.public_id)
        else:
            if not offering.is_published or not offering.accepts_party_requests:
                raise Http404("This workshop is not accepting party inquiries.")
            form = PartyInquiryForm(offering=offering)
        return render(request, "scheduling/party_inquiry.html", {
            "business": offering.location.profile.school, "offering": offering, "form": form, "error": error,
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
    return render(request, "scheduling/party_received.html", {"business": item.offering.location.profile.school, "reference": item.public_id, "has_reservation": hasattr(item, "reservation")})


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
