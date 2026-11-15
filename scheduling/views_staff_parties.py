from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from zoneinfo import ZoneInfo
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import staff_parties
from .access import require_location_access
from .views import _context


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def create(request, school_slug):
    profile, role = require_location_access(request.user, school_slug, "sessions.edit", capability="parties")
    from .calendar import return_url, editor_request
    calendar_return = return_url(request, profile)
    calendar_editor = editor_request(request, profile)
    if request.method == "POST":
        try:
            item, form = staff_parties.create(request.user, school_slug, request.POST)
        except ValidationError as exc:
            item = None
            form = staff_parties.StaffPartyForm(request.POST, profile=profile)
            form.is_valid()
            form.add_error(None, " ".join(exc.messages))
        if item:
            if calendar_editor:
                if getattr(item, "reservation", None):
                    return JsonResponse({"saved": True})
                return JsonResponse({"saved": True, "message": "Party request saved. Review its quote before confirming.",
                    "review_url": reverse("scheduling:party_detail", kwargs={"school_slug": school_slug, "public_id": item.public_id})})
            if getattr(item, "reservation", None):
                messages.success(request, "Party booking saved.")
                if calendar_return:
                    return redirect(calendar_return)
                return redirect("scheduling:reservation_detail", school_slug=school_slug, public_id=item.reservation.public_id)
            messages.success(request, "Party request saved. Review the room and price before creating a reservation.")
            return redirect("scheduling:party_detail", school_slug=school_slug, public_id=item.public_id)
    else:
        form = staff_parties.StaffPartyForm(profile=profile, initial={"offering": request.GET.get("offering")})
        form.initial["request_token"] = staff_parties.token_for(request.user, profile, form.selected_offering)
        if calendar_editor and form.selected_offering and request.GET.get("starts_at"):
            with timezone.override(ZoneInfo(form.selected_offering.location.timezone_name)):
                try:
                    start = form.fields["starts_at"].to_python(request.GET["starts_at"])
                except ValidationError:
                    start = None
            if start:
                form.initial["starts_at"] = start
                if form.packages_only:
                    slot = form.fields["party_slot"].queryset.filter(starts_at=start).first()
                    if slot:
                        form.initial.update(party_slot=slot.pk, package=slot.package_id)
                    else:
                        form.calendar_time_notice = "No party is offered at the selected time. Choose an available party time."
    if calendar_editor and form.packages_only and "party_slot" in form.fields:
        from .reservations import party_slot_remaining
        form.slot_capacity = party_slot_remaining(form.fields["party_slot"].queryset)
    context = _context(request, profile, role, "reservations" if form.packages_only else "party_inquiries")
    context["form"] = form
    context["calendar_return"] = calendar_return
    context["calendar_editor"] = calendar_editor
    template = "scheduling/staff_package_party_form.html" if form.packages_only else "scheduling/staff_party_form.html"
    return render(request, template, context, status=400 if form.errors else 200)
