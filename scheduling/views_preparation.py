from zoneinfo import ZoneInfo
from calendar import monthrange
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
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
    context["location_action"] = reverse("scheduling:studio_preparation", kwargs={"school_slug": school_slug})
    location = context["selected_location"]
    query = request.GET.copy()
    zone = ZoneInfo(location.timezone_name) if location else timezone.get_default_timezone()
    query.setdefault("date", timezone.localdate(timezone=zone).isoformat())
    query.setdefault("period", "week")
    form = PreparationRangeForm(query)
    studios = []
    printing = request.GET.get("print") == "1"
    start = end = None
    if form.is_valid() and context["location_filter"].is_valid():
        locations = [location] if location else list(staff_locations(profile))
        start = form.cleaned_data["date"]
        period = form.cleaned_data["period"]
        if period == "month":
            start = start.replace(day=1)
        days = monthrange(start.year, start.month)[1] if period == "month" else 1 if period == "day" else 7
        end = start + timedelta(days=days - 1)
        selected = request.GET.getlist("offering")
        # Unknown/empty filters must never widen the printed scope.
        ids = {int(value) for value in selected if value.isdecimal() and len(value) <= 20} if selected else None
        studios = summary(profile, locations, start, days, for_print=printing, offering_ids=ids)
    context.update(preparation_range=form, studios=studios, preparation_return_path=request.get_full_path(),
        print_period=form.cleaned_data.get("period", "week"), print_start=start, print_end=end,
        printed_at=timezone.now(), print_valid=form.is_valid() and context["location_filter"].is_valid())
    if printing:
        return render(request, "scheduling/studio_preparation_print.html", context,
            status=200 if context["print_valid"] else 400)
    return render(request, "scheduling/studio_preparation.html", context)
