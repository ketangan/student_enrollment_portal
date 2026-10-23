from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .access import require_location_access
from .calendar import calendar_available, calendar_detail, calendar_events, calendar_summary
from .location_filters import staff_location


def authorized_profile(request, school_slug):
    profile, _ = require_location_access(request.user, school_slug, capability="workshops")
    if not calendar_available(profile):
        raise Http404
    return profile


@never_cache
@login_required
@require_GET
def events(request, school_slug):
    profile = authorized_profile(request, school_slug)
    form = staff_location(request, profile)["location_filter"]
    if not form.is_valid():
        return JsonResponse({"error": "Choose a valid location."}, status=400)
    try:
        items = calendar_events(profile, request.GET, form.selected)
        return JsonResponse({"events": items, "summary": calendar_summary(profile, items)})
    except ValidationError as exc:
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)


@never_cache
@login_required
@require_GET
def detail(request, school_slug, kind, pk):
    return JsonResponse(calendar_detail(authorized_profile(request, school_slug), kind, pk))
