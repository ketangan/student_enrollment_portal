from zoneinfo import ZoneInfo
from calendar import monthrange
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.http import JsonResponse, Http404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .access import get_staff_reservation, require_location_access, staff_locations
from .studio_preparation import PreparationRangeForm, summary
from .views import _context
from . import preparation
from .models import SchedulingProfile


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
    today = timezone.localdate(timezone=zone)
    query.setdefault("date", (today - timedelta(days=today.weekday())).isoformat())
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
        studios = summary(profile, locations, start, days, for_print=printing, offering_ids=ids,
            include_completed=request.GET.get("completed") == "1")
    context.update(preparation_range=form, studios=studios, preparation_return_path=request.get_full_path(),
        print_period=form.cleaned_data.get("period", "week"), print_start=start, print_end=end,
        printed_at=timezone.now(), print_valid=form.is_valid() and context["location_filter"].is_valid())
    if printing:
        if request.GET.get("hide_prepared") == "1":
            for studio in studios:
                for entry in studio["entries"]:
                    entry["print_rows"] = [row for row in entry["print_rows"] if not row.get("prepared")]
                studio["entries"] = [entry for entry in studio["entries"]
                    if not entry["item"].prepared_at and entry["print_rows"]]
            context["studios"] = [studio for studio in studios if studio["entries"]]
        return render(request, "scheduling/studio_preparation_print.html", context,
            status=200 if context["print_valid"] else 400)
    nav_query = request.GET.copy()
    nav_query.pop("edit", None)
    nav_query.pop("staff", None)
    nav_query.pop("print", None)
    def link(**changes):
        values = nav_query.copy()
        for key, value in changes.items():
            values[key] = value
        return request.path + "?" + values.urlencode()
    context.update(previous_week=link(date=(start - timedelta(days=7)).isoformat(), period="week") if start and start.year > 1 else "",
        next_week=link(date=(start + timedelta(days=7)).isoformat(), period="week") if start else "",
        current_week=link(date=(today - timedelta(days=today.weekday())).isoformat(), period="week"),
        print_url=link(print=1, date=query["date"], period="week", location=context["location_query"]["location"]),
        completed_url=link(completed="0" if request.GET.get("completed") == "1" else "1"),
        show_completed=request.GET.get("completed") == "1", hide_prepared=request.GET.get("hide_prepared") == "1",
        clean_url=link(), hide_setup_tabs=True)
    # Load swatches once, scoped to this school's catalogs, not once per participant.
    from .models import ChoiceOption, CatalogVariant
    options = {option.pk: option for option in ChoiceOption.objects.filter(group__catalog__profile=profile)}
    variant_ids = {row.get("item_id") for studio in studios for entry in studio["entries"]
        if entry["item"].party_request_id for row in entry["item"].party_request.materials}
    variants = {v.pk: v for v in CatalogVariant.objects.filter(catalog__profile=profile, pk__in=variant_ids)}
    for studio in studios:
        for entry in studio["entries"]:
            item = entry["item"]
            entry["writable"] = context.get("can_edit_sessions") and item.state == "confirmed" and not entry["materials_used"]
            entry["revision"] = preparation.form_for(item, profile)["edit_revision"].value() if entry["writable"] else ""
            entry["action_url"] = reverse("scheduling:studio_preparation_update", kwargs={"school_slug": school_slug, "public_id": item.public_id})
            entry["staff_edit_url"] = link(staff=str(item.public_id)) + f'#booking-{item.pk}'
            if entry["writable"] and request.GET.get("staff") == str(item.public_id):
                entry["staff_form"] = preparation.staff_form_for(item, profile)
            party_rows = []
            if item.party_request_id:
                from .guest_lists import saved_rows
                party_rows = saved_rows(item)
            for person in entry["prep_rows"]:
                ids = [s.get("option_id") for s in person.get("selections", [])]
                if person["index"] < len(party_rows):
                    variant = variants.get(party_rows[person["index"]].get("item_id"))
                    ids = variant.option_ids if variant else []
                person["swatch"] = next((options[key].swatch for key in ids if key in options and options[key].swatch), "")
                person["edit_url"] = link(edit=f'{item.public_id}:{person["key"]}') + f'#prep-{item.pk}-{person["key"]}'
                if entry["writable"] and request.GET.get("edit") == f'{item.public_id}:{person["key"]}':
                    person["form"] = participant_form(item, person["key"])
    return render(request, "scheduling/studio_preparation.html", context)


def participant_form(item, key, data=None):
    if item.party_request_id:
        from .guest_lists import StaffGuestForm
        form = StaffGuestForm(data, item=item, index=int(key))
        form.order_fields(["guest_name", "guest_age", "design", "color", "item"])
        return form
    if item.workshop_request_id:
        from .workshop_selections import form_for
        from .requests import catalog_for
        person = next((p for p in item.workshop_request.participants.all() if str(p.pk) == key), None)
        if person is None:
            raise Http404
        form = form_for(person, catalog_for(item.workshop_request.session.offering), data, edit_name=True)
        from .legacy_materials import add_confirmation
        add_confirmation(form, item)
        return form
    return None


@never_cache
@login_required
@require_POST
@transaction.atomic
def studio_preparation_update(request, school_slug, public_id):
    profile, _ = require_location_access(request.user, school_slug, "sessions.edit", capability="reservations")
    SchedulingProfile.objects.select_for_update().get(pk=profile.pk)
    profile, _ = require_location_access(request.user, school_slug, "sessions.edit", capability="reservations")
    item = get_staff_reservation(profile, public_id, exclude_visits=True)
    try:
        revision = preparation.form_for(item, profile, {**request.POST.dict(), "action": "prepare"})
        if not revision.is_valid():
            raise ValidationError("This booking changed in another window. Reload before saving.")
        if item.state != "confirmed" or item.stock_allocations.filter(consumed_at__isnull=False).exists():
            raise ValidationError("Only confirmed bookings with unused materials can be prepared.")
        action, key = request.POST.get("action"), request.POST.get("participant", "")
        from .participant_preparation import rows_for, apply
        person = next((row for row in rows_for(item) if row["key"] == key), None)
        if action == "edit":
            if person is None:
                raise ValidationError("Participant not found.")
            if item.party_request_id:
                from .guest_lists import save_guest
                _, form = save_guest(request.user, school_slug, public_id, int(key), request.POST)
                if form.errors:
                    raise ValidationError([str(message) for errors in form.errors.values() for message in errors])
            elif item.workshop_request_id:
                from .workshop_selections import update
                update(request.user, school_slug, public_id, int(key), request.POST)
            else:
                raise ValidationError("Edit camp participants from their reservation.")
        elif action == "staff":
            preparation.save_staff_names(item, profile, request.user, request.POST)
        elif action in {"ready", "unready"}:
            apply(item, request.user, key, action == "ready")
            from .reservations import _audit
            _audit(request.user, item, "participant_prepared" if action == "ready" else "participant_prep_reopened",
                participant_name=person["name"])
        else:
            raise ValidationError("Choose a preparation action.")
    except ValidationError as exc:
        transaction.set_rollback(True)
        return JsonResponse({"error": " ".join(exc.messages)}, status=400)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"saved": True})
    return redirect("scheduling:studio_preparation", school_slug=school_slug)
