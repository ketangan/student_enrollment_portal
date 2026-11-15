from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core import signing
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import setup, staff_workshops
from .access import require_location_access
from .calendar import calendar_available
from .views import _context
from .models import Session


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def create(request, school_slug, offering_pk):
    profile, role = require_location_access(request.user, school_slug, "sessions.edit", capability="workshops")
    if not calendar_available(profile):
        raise Http404
    offering = get_object_or_404(setup.records(profile, "workshops"), pk=offering_pk)
    selected = request.GET.get("session", "")
    if selected and not selected.isdecimal():
        raise Http404
    initial = {"session": selected, "starts_at": request.GET.get("starts_at", "")}
    new = request.GET.get("new") == "1" or not staff_workshops.booking_available(profile, offering)
    with timezone.override(ZoneInfo(offering.location.timezone_name)):
        if not initial["session"] and initial["starts_at"]:
            field = setup.build_form(profile, "sessions", offering_pk=offering.pk).fields["starts_at"]
            try:
                start = field.clean(initial["starts_at"])
                matching = offering.sessions.filter(starts_at=start, starts_at__gt=timezone.now(), camps__isnull=True).first()
                initial["session"] = matching.pk if matching else ""
                new = matching is None
            except ValidationError:
                initial["starts_at"] = ""
        fixed = int(initial["session"]) if initial["session"] else None
        if fixed:
            get_object_or_404(offering.sessions.filter(starts_at__gt=timezone.now(), camps__isnull=True), pk=fixed)
            new = False
        initial["request_token"] = staff_workshops.token_for(request.user, profile, offering, new=new, session=fixed)
        if request.method == "POST":
            try:
                token = signing.loads(request.POST.get("request_token", ""), salt=staff_workshops.SALT, max_age=7200)
                new, fixed = token.get("new", False), token.get("session")
            except (signing.BadSignature, AttributeError):
                pass
        form = staff_workshops.StaffWorkshopForm(request.POST if request.method == "POST" else None,
            profile=profile, offering=offering, initial=initial, new=new, fixed_session=fixed)
        if request.method == "POST":
            try:
                item, form = staff_workshops.create(request.user, school_slug, offering_pk, request.POST)
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                if item:
                    if isinstance(item, Session):
                        if request.headers.get("X-Calendar-Editor") == "1":
                            return JsonResponse({"saved": True, "session_created": True})
                        return redirect("scheduling:workspace", school_slug=school_slug)
                    destination = reverse("scheduling:reservation_detail", kwargs={"school_slug": school_slug,
                        "public_id": item.public_id})
                    if request.headers.get("X-Calendar-Editor") == "1":
                        return JsonResponse({"saved": True, "reservation_url": destination})
                    return redirect(destination)
        context = _context(request, profile, role, "overview")
        context.update(form=form, offering=offering,
            calendar_editor=request.headers.get("X-Calendar-Editor") == "1")
        return render(request, "scheduling/staff_workshop_form.html", context,
            status=400 if request.method == "POST" else 200)
