from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
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
    if action == "edit" and session.series_id:
        return schedule(request, school_slug, session.offering_id, action, seed_pk=str(session.pk))
    calendar_editor = editor_request(request, profile)
    with timezone.override(ZoneInfo(session.offering.location.timezone_name)):
        form = session_changes.build_form(request.user, profile, session, action,
            request.POST if request.method == "POST" else None)
        preview = None
        saved = False
        preflight = request.method == "POST" and request.headers.get("X-Session-Preflight") == "1" and action == "edit"
        if request.method == "POST":
            try:
                saved, form, preview = session_changes.save(request.user, school_slug, pk, action, request.POST,
                    preview_only=preflight)
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
            else:
                if saved and not preflight:
                    if calendar_editor:
                        return JsonResponse({"saved": True})
                    messages.success(request, "Session canceled." if action == "cancel" else "Session updated.")
                    return redirect("scheduling:workshop_detail", school_slug=school_slug, pk=session.offering_id)
        if preflight:
            if saved:
                return JsonResponse({"valid": False, "errors": {"__all__": [{"message": "This change was already saved. Reopen the session."}]}}, status=400)
            return JsonResponse({"valid": not bool(form.errors), "errors": form.errors.get_json_data(),
                "review_token": form["review_token"].value() if not form.errors else "",
                "review_html": render_to_string("scheduling/session_change_review.html", {"preview": preview}) if not form.errors else ""},
                status=400 if form.errors else 200)
        context = _context(request, profile, role, "workshops")
        context.update(form=form, session=session, preview=preview, canceling=action == "cancel", scope_action=action,
            calendar_editor=calendar_editor, hide_setup_tabs=True,
            customers=session_changes.preview_rows(session_changes.affected(profile, session)))
        return render(request, "scheduling/session_change.html", context,
            status=400 if form.errors else 200)


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def schedule(request, school_slug, offering_pk, action, *, session_editor=False, seed_pk=None):
    profile, role, offering = workshop_schedule.offering_for(request.user, school_slug, offering_pk, action)
    calendar_editor = editor_request(request, profile)
    with timezone.override(ZoneInfo(offering.location.timezone_name)):
        initial = {"first_date": timezone.localdate(), "interval": 1, "mode": "once", "visibility": "published"}
        if session_editor:
            initial["creation_token"] = workshop_schedule.creation_token(request.user, offering)
        if session_editor and request.GET.get("starts_at"):
            from django.utils.dateparse import parse_datetime
            try:
                start = parse_datetime(request.GET["starts_at"])
            except ValueError:
                start = None
            if start:
                initial.update(first_date=start.date(), start_time=start.time())
        seed_pk = seed_pk or request.GET.get("session", "")
        seed = offering.sessions.filter(pk=int(seed_pk) if seed_pk.isdigit() else None).first()
        series_seed = seed if seed and action != "create" and (seed.series_id or request.method == "POST" and request.POST.get("preview_token")) else None
        if seed_pk and action != "create" and not series_seed:
            raise Http404
        if seed:
            local = timezone.localtime(seed.starts_at)
            initial.update(first_date=max(timezone.localdate(), local.date() + timedelta(days=7 if action == "create" else 0)), weekday=local.weekday(),
                current_time=local.time(), start_time=local.time(), room=seed.room_id, capacity=seed.capacity,
                duration_minutes=round((seed.ends_at - seed.starts_at).total_seconds() / 60))
        form_class = workshop_schedule.NewSessionForm if session_editor else workshop_schedule.ScheduleForm
        form = form_class(request.POST if request.method == "POST" else None,
            offering=offering, action=action, initial=initial, seed=series_seed)
        rows = []
        recurring_edit = bool(series_seed and action == "edit")
        preflight = recurring_edit and request.headers.get("X-Session-Preflight") == "1"
        if recurring_edit and request.method == "GET":
            from django.http import QueryDict
            initial_data = QueryDict(mutable=True)
            initial_data.update({field.name: field.value() or "" for field in form})
            try:
                form, rows, _ = workshop_schedule.save(request.user, school_slug, offering_pk, action,
                    initial_data, seed=series_seed, preview_only=True)
            except ValidationError as exc:
                form = form_class(initial_data, offering=offering, action=action, seed=series_seed)
                form.is_valid()
                form.add_error(None, " ".join(exc.messages))
        if request.method == "POST":
            try:
                form, rows, saved = workshop_schedule.save(request.user, school_slug, offering_pk, action, request.POST,
                    seed=series_seed, session_editor=session_editor, preview_only=preflight)
            except ValidationError as exc:
                form.add_error(None, " ".join(exc.messages))
                form.data = form.data.copy()
                form.data["preview_token"] = ""
            else:
                if saved and not preflight:
                    if calendar_editor:
                        return JsonResponse({"saved": True, "session_created": True}) if session_editor else JsonResponse({"saved": True})
                    messages.success(request, "Schedule updated.")
                    return redirect("scheduling:workshop_detail", school_slug=school_slug, pk=offering.pk)
        context = _context(request, profile, role, "workshops")
        conflicts = sum(bool(row.get("conflicts")) for row in rows)
        recipients = {}
        for row in rows:
            row["seats"] = sum(customer["quantity"] for customer in row["rows"])
            for customer in row["rows"]:
                email = customer["email"].strip().lower()
                if email:
                    recipients.setdefault(email, {"name": customer["name"], "email": email})
        impact = {"rows": rows, "conflict_count": conflicts, "recipients": list(recipients.values())}
        if preflight:
            valid = not form.errors and not conflicts and bool(form["preview_token"].value())
            return JsonResponse({"valid": valid, "errors": form.errors.get_json_data(),
                "review_token": form["preview_token"].value() if valid else "",
                "review_html": render_to_string("scheduling/recurring_session_review.html", impact)}, status=200 if valid else 400)
        context.update(form=form, offering=offering, rows=rows, schedule_action=action,
            setup_return_url=reverse("scheduling:workshop_detail", kwargs={"school_slug": school_slug, "pk": offering.pk}),
            schedule_seed=seed.pk if seed else None, session=seed, multiple_sessions=True,
            canceling=action == "cancel", scope_action=action, session_editor=session_editor,
            booking_count=sum(row["count"] for row in rows), calendar_editor=calendar_editor, hide_setup_tabs=True)
        context.update(impact)
        if recurring_edit:
            context["editor_action"] = reverse("scheduling:workshop_schedule", kwargs={"school_slug":school_slug,
                "offering_pk":offering.pk, "action":"edit"}) + f"?session={seed.pk}"
        return render(request, "scheduling/recurring_session_edit.html" if recurring_edit else "scheduling/workshop_schedule.html",
            context, status=400 if form.errors else 200)
