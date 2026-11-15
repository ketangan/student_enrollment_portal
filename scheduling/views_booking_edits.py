from zoneinfo import ZoneInfo
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import booking_edits
from .access import get_staff_reservation, require_location_access
from .views import _context


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def edit(request, school_slug, public_id):
    profile, role = require_location_access(request.user, school_slug, "setup.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    if not (item.workshop_request_id or item.party_request_id):
        raise Http404
    destination = reverse("scheduling:reservation_detail", kwargs={"school_slug": school_slug, "public_id": public_id})
    from .calendar import return_url
    calendar_return = return_url(request, profile)
    if calendar_return:
        destination += "?" + urlencode({"calendar": calendar_return})
    with timezone.override(ZoneInfo(booking_edits.offering_for(item).location.timezone_name)):
        form = booking_edits.BookingEditForm(request.POST if request.method == "POST" else None, item=item, user=request.user)
        preview = None
        if request.method == "POST":
            try:
                saved, form, preview = booking_edits.save(request.user, school_slug, public_id, request.POST)
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                if saved:
                    if request.headers.get("X-Calendar-Editor") == "1":
                        return JsonResponse({"saved": True})
                    messages.success(request, "Booking updated.")
                    return redirect(destination)
        context = _context(request, profile, role, "reservations")
        context.update(item=item, form=form, preview=preview, return_url=destination, hide_setup_tabs=True,
            edit_unavailable=booking_edits.unavailable_reason(item),
            can_edit_booking=booking_edits.editable(item), calendar_editor=request.headers.get("X-Calendar-Editor") == "1")
        return render(request, "scheduling/booking_edit.html", context,
            status=400 if request.method == "POST" else 200)
