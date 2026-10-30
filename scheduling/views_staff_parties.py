from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
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
    if request.method == "POST":
        try:
            item, form = staff_parties.create(request.user, school_slug, request.POST)
        except ValidationError as exc:
            item = None
            form = staff_parties.StaffPartyForm(request.POST, profile=profile)
            form.is_valid()
            form.add_error(None, " ".join(exc.messages))
        if item:
            if getattr(item, "reservation", None):
                messages.success(request, "Party booking saved.")
                return redirect("scheduling:reservation_detail", school_slug=school_slug, public_id=item.reservation.public_id)
            messages.success(request, "Party request saved. Review the room and price before creating a reservation.")
            return redirect("scheduling:party_detail", school_slug=school_slug, public_id=item.public_id)
    else:
        form = staff_parties.StaffPartyForm(profile=profile, initial={"offering": request.GET.get("offering")})
        form.initial["request_token"] = staff_parties.token_for(request.user, profile, form.selected_offering)
    context = _context(request, profile, role, "reservations" if form.packages_only else "party_inquiries")
    context["form"] = form
    template = "scheduling/staff_package_party_form.html" if form.packages_only else "scheduling/staff_party_form.html"
    return render(request, template, context, status=400 if form.errors else 200)
