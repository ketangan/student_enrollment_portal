from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .access import require_location_access, staff_locations
from .studio_preparation import PreparationRangeForm, summary
from .views import _context


@never_cache
@login_required
@require_GET
def studio_preparation(request, school_slug):
    profile, role = require_location_access(request.user, school_slug, capability="reservations")
    context = _context(request, profile, role, "studio_preparation")
    location = context["selected_location"]
    query = request.GET.copy()
    zone = ZoneInfo(location.timezone_name) if location else timezone.get_default_timezone()
    query.setdefault("date", timezone.localdate(timezone=zone).isoformat())
    query.setdefault("period", "week")
    form = PreparationRangeForm(query)
    studios = []
    if form.is_valid() and context["location_filter"].is_valid():
        locations = [location] if location else list(staff_locations(profile))
        studios = summary(profile, locations, form.cleaned_data["date"],
            1 if form.cleaned_data["period"] == "day" else 7)
    context.update(preparation_range=form, studios=studios)
    return render(request, "scheduling/studio_preparation.html", context)
