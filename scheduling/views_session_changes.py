from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from . import session_changes
from .access import require_location_access
from .calendar import editor_request
from .views import _context
from . import workshop_schedule


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def change(request, school_slug, pk, action="edit"):
    profile, role = require_location_access(request.user, school_slug,
        "setup.edit" if action == "cancel" else "sessions.edit", capability="workshops")
    session = session_changes.get_session(profile, pk)
    if not session_changes.available(profile, session):
        raise Http404
    calendar_editor = editor_request(request, profile)
    with timezone.override(ZoneInfo(session.offering.location.timezone_name)):
        form = session_changes.build_form(request.user, profile, session, action,
            request.POST if request.method == "POST" else None)
        preview = None
        if request.method == "POST":
            try:
                saved, form, preview = session_changes.save(request.user, school_slug, pk, action, request.POST)
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                if saved:
                    if calendar_editor:
                        return JsonResponse({"saved": True})
                    messages.success(request, "Session canceled." if action == "cancel" else "Session updated.")
                    return redirect("scheduling:workshop_detail", school_slug=school_slug, pk=session.offering_id)
        context = _context(request, profile, role, "workshops")
        context.update(form=form, session=session, preview=preview, canceling=action == "cancel",
            calendar_editor=calendar_editor, hide_setup_tabs=True)
        return render(request, "scheduling/session_change.html", context,
            status=400 if form.errors else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def schedule(request, school_slug, offering_pk, action):
    profile, role, offering = workshop_schedule.offering_for(request.user, school_slug, offering_pk, action)
    calendar_editor = editor_request(request, profile)
    with timezone.override(ZoneInfo(offering.location.timezone_name)):
        initial = {"first_date": timezone.localdate(), "interval": 1}
        seed_pk = request.GET.get("session", "")
        seed = offering.sessions.filter(pk=int(seed_pk) if seed_pk.isdigit() else None, canceled_at__isnull=True).first()
        if seed:
            local = timezone.localtime(seed.starts_at)
            initial.update(first_date=max(timezone.localdate(), local.date() + timedelta(days=7 if action == "create" else 0)), weekday=local.weekday(),
                current_time=local.time(), start_time=local.time(), room=seed.room_id, capacity=seed.capacity,
                duration_minutes=round((seed.ends_at - seed.starts_at).total_seconds() / 60))
        form = workshop_schedule.ScheduleForm(request.POST if request.method == "POST" else None,
            offering=offering, action=action, initial=initial)
        rows = []
        if request.method == "POST":
            try:
                form, rows, saved = workshop_schedule.save(request.user, school_slug, offering_pk, action, request.POST)
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
                form.data = form.data.copy()
                form.data["preview_token"] = ""
            else:
                if saved:
                    if calendar_editor:
                        return JsonResponse({"saved": True})
                    messages.success(request, "Schedule updated.")
                    return redirect("scheduling:workshop_detail", school_slug=school_slug, pk=offering.pk)
        context = _context(request, profile, role, "workshops")
        context.update(form=form, offering=offering, rows=rows, schedule_action=action,
            schedule_seed=seed.pk if seed else None,
            booking_count=sum(row["count"] for row in rows), calendar_editor=calendar_editor, hide_setup_tabs=True)
        return render(request, "scheduling/workshop_schedule.html", context, status=400 if form.errors else 200)
