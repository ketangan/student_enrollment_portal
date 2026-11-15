from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .access import require_location_access, has_permission
from .calendar import calendar_available, calendar_detail, calendar_events, calendar_summary
from .location_filters import staff_location


def authorized_access(request, school_slug):
    profile, role = require_location_access(request.user, school_slug, capability="workshops")
    if not calendar_available(profile):
        raise Http404
    return profile, role


@never_cache
@login_required
@require_GET
def events(request, school_slug):
    profile, _ = authorized_access(request, school_slug)
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
    profile, role = authorized_access(request, school_slug)
    data = calendar_detail(profile, kind, pk)
    if kind == "session" and has_permission(role, "sessions.edit"):
        data["edit_url"] = reverse("scheduling:session_edit", kwargs={"school_slug": school_slug, "pk": pk})
        from .models import Session
        from .staff_workshops import booking_available
        session = Session.objects.select_related("offering").get(pk=pk)
        from .session_changes import available
        if has_permission(role, "setup.edit") and available(profile, session) and not session.canceled_at and session.starts_at > timezone.now():
            data["cancel_session_url"] = reverse("scheduling:session_cancel", kwargs={"school_slug": school_slug, "pk": pk})
        if session.starts_at > timezone.now() and booking_available(profile, session.offering) and not session.camps.exists():
            data["booking_url"] = reverse("scheduling:staff_workshop_create", kwargs={
                "school_slug": school_slug, "offering_pk": session.offering_id}) + f"?session={pk}"
    if has_permission(role, "setup.edit"):
        for row in data["reservations"]:
            if row["editable"]:
                row["edit_url"] = row["url"] + "edit/"
            if row["cancelable"]:
                row["cancel_url"] = row["url"] + "cancel/"
    else:
        for row in data["reservations"]:
            row.pop("edit_unavailable", None)
    return JsonResponse(data)
