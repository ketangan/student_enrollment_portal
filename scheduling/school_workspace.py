"""Opt-in school journeys: read existing records and delegate every write."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode, urlsplit, urlunsplit

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Exists, Max, OuterRef, Prefetch, Q, Value
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core.models import SchoolProgram
from core.services.portal_environment import request_environment, school_environment, scope_schools
from core.services.school_permissions import get_school_membership
from .access import location_restricted, require_access, require_location_access, staff_locations
from .appointment_models import LessonBookingOffer
from .appointments import preview_enabled
from .camps import check_registration, scoped_camps
from .calendar import MAX_EVENTS, date_window, local_times
from .capabilities import has_capability
from .lesson_models import LessonTeacher
from .lessons import scheduled_lessons
from .location_filters import staff_location
from .models import Camp, SchedulingProfile, Session
from .requests import accepts_requests, catalogs_for
from .school_navigation import navigation_policy
from .setup import records
from .views import _context


def _policy(school):
    policy = navigation_policy(school)
    if policy["layout"] != "school" or not policy.get("simplified", False):
        raise Http404("Page not found")
    return policy


def _check_environment(request, school):
    environment = request_environment(request)
    if environment and environment != school_environment(school):
        raise Http404("Page not found")


def _features(profile, policy):
    return {key: key in policy["features"] and has_capability(profile, key)
            for key in ("lessons", "workshops", "camps")}


def _staff_access(request, school_slug):
    profile, role = require_location_access(request.user, school_slug)
    _check_environment(request, profile.school)
    policy = _policy(profile.school)
    features = _features(profile, policy)
    # Lesson detail/setup engines still require unrestricted membership. A
    # location filter or teacher selector must never grant own-teacher access.
    features["lessons"] &= profile.lessons_enabled and not location_restricted(profile)
    if features["lessons"]:
        require_access(request.user, school_slug, capability="lessons")
    return profile, role, features


class ScheduleFilterForm(forms.Form):
    date = forms.DateField(required=False, label="Date", widget=forms.DateInput(attrs={"type": "date"}))
    teacher = forms.ModelChoiceField(queryset=LessonTeacher.objects.none(), required=False, empty_label="All teachers")
    range = forms.ChoiceField(required=False, choices=[("day", "Day"), ("week", "Week"), ("month", "Month")], widget=forms.HiddenInput())

    def __init__(self, data, *, profile, lessons):
        super().__init__(data)
        if lessons:
            self.fields["teacher"].queryset = LessonTeacher.objects.filter(profile=profile)
        else:
            self.fields["teacher"].widget = forms.HiddenInput()

    def clean_date(self):
        day = self.cleaned_data["date"]
        if day and not 1901 <= day.year <= 2199:
            raise forms.ValidationError("Choose a date between 1901 and 2199.")
        return day


def _on_local_date(items, profile, location_form, lookup, day, window=None):
    """A selected date means each location's local day, not the server's day."""
    if not day and not window:
        return items
    predicate = Q(pk__in=[])
    locations = location_form.filter(staff_locations(profile), "pk")
    for location in locations:
        zone = ZoneInfo(location.timezone_name)
        start = datetime.combine(window[0] if window else day, time.min, tzinfo=zone)
        end = datetime.combine(window[1], time.min, tzinfo=zone) if window else start + timedelta(days=1)
        if window:
            predicate |= Q(**{lookup: location.pk}, ends_at__gt=start, starts_at__lt=end)
        else:
            predicate |= Q(**{lookup: location.pk}, starts_at__gte=start, starts_at__lt=end)
    return items.filter(predicate)


def _agenda_sources(profile, features, location_form, *, day=None, teacher=None, window=None):
    lessons = scheduled_lessons().filter(series__profile=profile)
    if not day and not window:
        lessons = lessons.filter(ends_at__gt=timezone.now())
    lessons = location_form.filter(lessons, "series__location") if features["lessons"] else lessons.none()
    if teacher:
        lessons = lessons.filter(series__teacher=teacher)
    lessons = _on_local_date(lessons, profile, location_form, "series__location_id", day, window)
    sessions = records(profile, "sessions") if features["workshops"] or features["camps"] else Session.objects.none()
    if not day and not window:
        sessions = sessions.filter(ends_at__gt=timezone.now())
    sessions = location_form.filter(sessions, "offering__location")
    sessions = sessions.annotate(is_camp=Exists(Camp.objects.filter(sessions=OuterRef("pk"))))
    if not features["camps"]:
        sessions = sessions.filter(is_camp=False)
    if not features["workshops"]:
        sessions = sessions.filter(is_camp=True)
    if teacher:
        # Group sessions have no teacher relationship; do not imply a match.
        sessions = sessions.none()
    sessions = _on_local_date(sessions, profile, location_form, "offering__location_id", day, window)
    return lessons, sessions


def _agenda_page(sources, page=1, per_page=50):
    lessons, sessions = sources
    columns = ("pk", "starts_at", "event_kind")
    lesson_index = lessons.order_by().annotate(event_kind=Value("lesson")).values(*columns)
    session_index = sessions.order_by().annotate(event_kind=Value("session")).values(*columns)
    index = lesson_index.union(session_index, all=True).order_by("starts_at", "event_kind", "pk")
    result = Paginator(index, per_page).get_page(page)
    keys = list(result.object_list)
    lesson_rows = {item.pk: item for item in lessons.filter(pk__in=[key["pk"] for key in keys if key["event_kind"] == "lesson"])
                   .select_related("series__student", "series__teacher", "series__service", "series__location__profile__school", "series__room")}
    session_rows = {item.pk: item for item in sessions.filter(pk__in=[key["pk"] for key in keys if key["event_kind"] == "session"])
                    .select_related("offering__location__profile__school", "room")}
    rows = []
    for key in keys:
        if key["event_kind"] == "lesson":
            item = lesson_rows.get(key["pk"])
            if item is None:
                continue  # A concurrent cancellation can remove an index entry.
            series = item.series
            location = series.location
            row = {"title": series.snapshot.get("service") or series.service.name,
                   "person": series.student.name, "teacher": series.teacher.name,
                   "kind": "Lesson", "status": item.get_state_display(), "room": series.room,
                   "url": reverse("scheduling:lesson_series", kwargs={"school_slug": location.profile.school.slug,
                                                                            "public_id": series.public_id})}
        else:
            item = session_rows.get(key["pk"])
            if item is None:
                continue
            location = item.offering.location
            row = {"title": item.offering.name, "kind": "Program session" if item.is_camp else "Class",
                   "status": "Published" if item.is_published else "Draft", "room": item.room,
                   "url": reverse("scheduling:workshop_detail", kwargs={"school_slug": location.profile.school.slug,
                                                                               "pk": item.offering_id}) + f"#session-{item.pk}"}
        row.update(id=f'{key["event_kind"]}-{item.pk}', starts_at=item.starts_at, ends_at=item.ends_at, location=location)
        rows.append(row)
    result.object_list = rows
    return result


def _pagination(request, page, key="page"):
    def link(number):
        query = request.GET.copy()
        query[key] = number
        return "?" + query.urlencode()
    return {"page": page, "previous": link(page.previous_page_number()) if page.has_previous() else "",
            "next": link(page.next_page_number()) if page.has_next() else ""}


def _schedule_window(day, range_name):
    if range_name == "month":
        start = day.replace(day=1)
        start -= timedelta(days=start.weekday())
        end = start + timedelta(days=42)
    else:
        start = day - timedelta(days=day.weekday()) if range_name == "week" else day
        end = start + timedelta(days=7 if range_name == "week" else 1)
    return date_window({"start": start.isoformat(), "end": end.isoformat()})


def _schedule_views(request, profile, features, form, location):
    available = profile.calendar_enabled and any(features.values())
    selected = "calendar" if available and request.GET.get("view") == "calendar" else "list"
    anchor_location = location.filter(staff_locations(profile), "pk").first()
    zone = ZoneInfo(anchor_location.timezone_name) if anchor_location else timezone.get_current_timezone()
    today = timezone.localdate(timezone=zone)
    day = form.cleaned_data["date"] or today
    range_name = form.cleaned_data["range"] or ("week" if selected == "calendar" else "")
    window = _schedule_window(day, range_name) if range_name else None

    def link(**changes):
        query = request.GET.copy()
        query.pop("page", None)
        for key, value in {"date": day.isoformat(), "range": range_name or "week", **changes}.items():
            query[key] = value
        return "?" + query.urlencode()

    context = {"calendar_available": available, "schedule_view": selected,
        "schedule_list_url": link(view="list") if selected == "calendar" else request.get_full_path(),
        "schedule_calendar_url": link(view="calendar"),
        "schedule_range": range_name, "schedule_day": day,
        "schedule_timezone": str(zone) if not location.show_location_filter or location.selected else ""}
    if window:
        if range_name == "month":
            month_index = day.year * 12 + day.month - 1
            previous = date((month_index - 1) // 12, (month_index - 1) % 12 + 1, 1)
            following = date((month_index + 1) // 12, (month_index + 1) % 12 + 1, 1)
        else:
            step = timedelta(days=7 if range_name == "week" else 1)
            previous, following = day - step, day + step
        context.update(schedule_start=window[0], schedule_end=window[1] - timedelta(days=1),
            schedule_previous=link(date=previous.isoformat()) if previous.year >= 1901 else None,
            schedule_next=link(date=following.isoformat()) if following.year <= 2199 else None,
            schedule_today=link(date=today.isoformat()),
            schedule_ranges=[{"label": label, "url": link(range=key), "active": key == range_name}
                for key, label in ScheduleFilterForm.base_fields["range"].choices])
    return context, window


def _calendar_event(row):
    start, end = local_times(row["starts_at"], row["ends_at"], row["location"])
    return {"id": row["id"], "title": row["title"],
        "start": start.replace(tzinfo=None).isoformat(), "end": end.replace(tzinfo=None).isoformat(),
        "extendedProps": {"url": row["url"], "kind": row["kind"], "status": row["status"],
            "person": row.get("person", ""), "teacher": row.get("teacher", ""),
            "location": row["location"].name, "zone": row["location"].timezone_name}}


@never_cache
@login_required
@require_GET
def school_schedule(request, school_slug):
    profile, role, features = _staff_access(request, school_slug)
    context = _context(request, profile, role, "scheduling")
    form = ScheduleFilterForm(request.GET, profile=profile, lessons=features["lessons"])
    valid = form.is_valid() and context["location_filter"].is_valid()
    context.update(schedule_filter=form, journey_features=features, hide_setup_tabs=True,
                   trial_booking_available=features["lessons"] and preview_enabled(), dashboard_location_form=None)
    if valid:
        view_context, window = _schedule_views(request, profile, features, form, context["location_filter"])
        context.update(view_context)
        sources = _agenda_sources(profile, features, context["location_filter"],
            day=form.cleaned_data["date"], teacher=form.cleaned_data["teacher"], window=window)
        if context["schedule_view"] == "calendar":
            page = _agenda_page(sources, per_page=MAX_EVENTS)
            if page.has_next():
                form.add_error(None, "Too many events. Choose a shorter date range or a teacher.")
                valid = False
            else:
                _agenda_return_links(request, page)
                context["schedule_calendar_config"] = {"date": context["schedule_day"].isoformat(),
                    "range": context["schedule_range"], "events": [_calendar_event(row) for row in page]}
        else:
            page = _agenda_page(sources, request.GET.get("page"))
            _agenda_return_links(request, page)
            context.update(agenda=page, pagination=_pagination(request, page))
    return render(request, "scheduling/school/schedule.html", context, status=200 if valid else 400)


def _agenda_return_links(request, page):
    for row in page:
        parts = urlsplit(row["url"])
        row["url"] = urlunsplit(parts._replace(query=urlencode({"schedule": request.get_full_path()})))


def _enrollment_permissions(request, school):
    from core.views_school_common import _get_accessible_school_for_admin, TrialExpiredError
    try:
        _get_accessible_school_for_admin(request, school.slug)
    except (Http404, TrialExpiredError):
        return False, False
    membership = get_school_membership(request.user, school)
    return True, request.user.is_superuser or bool(membership and membership.has_role("editor"))


@never_cache
@login_required
@require_GET
def school_programs(request, school_slug):
    profile, role, features = _staff_access(request, school_slug)
    context = _context(request, profile, role, "programs")
    location = context["location_filter"]
    enrollment, edit_enrollment = _enrollment_permissions(request, profile.school)
    context.update(journey_features=features, enrollment_available=enrollment,
                   can_edit_enrollment=edit_enrollment, hide_setup_tabs=True,
                   trial_booking_available=features["lessons"] and preview_enabled())
    if location.is_valid():
        groups = []
        if enrollment:
            groups.append(("enrollment_programs", SchoolProgram.objects.filter(school=profile.school, is_deleted=False)))
        if features["lessons"]:
            offers = LessonBookingOffer.objects.filter(profile=profile).select_related("service", "teacher", "location")
            groups.append(("lesson_offers", location.filter(offers, "location")))
        if features["workshops"]:
            workshops = records(profile, "workshops")
            if features["camps"]:
                # Camp-only offerings belong in the program section. Keep new
                # classes and offerings that also have standalone sessions here.
                workshops = workshops.filter(Q(camps__isnull=True) |
                    Q(sessions__isnull=False, sessions__camps__isnull=True)).distinct()
            groups.append(("workshops", location.filter(workshops, "location")))
        if features["camps"]:
            groups.append(("camps", location.filter(scoped_camps(profile).filter(offering__visit_program__isnull=True)
                .annotate(last_session_end=Max("sessions__ends_at")), "offering__location")))
        context["now"] = timezone.now()
        for key, items in groups:
            page = Paginator(items.order_by(*items.model._meta.ordering, "pk"), 30).get_page(request.GET.get(key + "_page"))
            context[key] = page
            context[key + "_pagination"] = _pagination(request, page, key + "_page")
    return render(request, "scheduling/school/programs.html", context, status=200 if location.is_valid() else 400)


def school_dashboard_agenda(request, school, *, limit=6):
    """Safe optional context for an already-authorized school dashboard."""
    try:
        _policy(school)
        profile, _, features = _staff_access(request, school.slug)
    except Http404:
        return {}
    location = staff_location(request, profile)["location_filter"]
    page = _agenda_page(_agenda_sources(profile, features, location), per_page=limit) if location.is_valid() else []
    return {"school_agenda": page, "school_agenda_available": True,
            "school_agenda_location_error": not location.is_valid(),
            "school_schedule_url": reverse("scheduling:school_schedule", kwargs={"school_slug": school.slug})}


def _public_context(request, school_slug):
    profiles = scope_schools(SchedulingProfile.objects.select_related("school"), request, "school__")
    profile = get_object_or_404(profiles, school__slug=school_slug, enabled=True, school__is_active=True)
    features = _features(profile, _policy(profile.school))
    features["lessons"] &= profile.lessons_enabled
    return {"profile": profile, "business": profile.school, "journey_features": features,
            "trial_booking_available": features["lessons"] and preview_enabled()}


@never_cache
@require_GET
def school_public(request, school_slug):
    return render(request, "scheduling/school/public.html", _public_context(request, school_slug))


PUBLIC_CAMP_SCAN_LIMIT = 100


def _camp_continuation(request, start, next_start):
    def link(offset):
        query = request.GET.copy()
        query.pop("programs_page", None)
        query["programs_start"] = offset
        return "?" + query.urlencode() + "#public-programs-title"
    return {"first": link(0) if start else None,
            "next": link(next_start) if next_start is not None else None}


@never_cache
@require_GET
def school_classes(request, school_slug):
    context = _public_context(request, school_slug)
    profile, features = context["profile"], context["journey_features"]
    if not (features["workshops"] or features["camps"]):
        raise Http404("Page not found")
    sessions = Session.objects.filter(offering__location__profile=profile, offering__visit_program__isnull=True,
        offering__is_published=True, is_published=True, starts_at__gt=timezone.now(), camps__isnull=True
    ).select_related("offering__location__profile__school", "offering__inventory_catalog", "room")
    sessions = sessions if features["workshops"] else sessions.none()
    classes = Paginator(sessions, 20).get_page(request.GET.get("classes_page"))
    unavailable_dates = Session.objects.filter(camps=OuterRef("pk")).filter(
        Q(is_published=False) | Q(starts_at__lte=timezone.now()))
    camps = Camp.objects.filter(offering__location__profile=profile, offering__visit_program__isnull=True,
        offering__is_published=True, is_open=True, sessions__is_published=True, sessions__starts_at__gt=timezone.now()
    ).filter(~Exists(unavailable_dates)).distinct().select_related(
        "offering__location__profile__school", "offering__inventory_catalog").prefetch_related(
        # One beyond the engine's 31-date maximum lets it reject oversized camps
        # without loading an unbounded number of related sessions.
        Prefetch("sessions", queryset=Session.objects.select_related("offering__location", "room")[:32],
                 to_attr="journey_dates"))
    camps = camps if features["camps"] and profile.native_reservations_enabled else camps.none()
    try:
        start = int(request.GET.get("programs_start", 0))
        if not 0 <= start <= 2**31 - 1:
            start = 0
    except (TypeError, ValueError):
        start = 0
    # Bound each request, but continue after the last checked candidate rather
    # than paginating first and allowing closed camps to consume visible places.
    candidates = list(camps[start:start + PUBLIC_CAMP_SCAN_LIMIT + 1])
    catalogs = catalogs_for([item.offering for item in classes] +
                           [item.offering for item in candidates[:PUBLIC_CAMP_SCAN_LIMIT]])
    for session in classes:
        session.requests_available = accepts_requests(session, catalogs[session.offering_id])
    available = []
    consumed = 0
    for camp in candidates[:PUBLIC_CAMP_SCAN_LIMIT]:
        consumed += 1
        try:
            check_registration(camp, camp.journey_dates, catalogs[camp.offering_id])
        except ValidationError:
            continue
        available.append(camp)
        if len(available) == 20:
            break
    next_start = start + consumed if consumed < len(candidates) else None
    context.update(classes=classes, public_camps=available,
                   classes_pagination=_pagination(request, classes, "classes_page"),
                   camps_pagination=_camp_continuation(request, start, next_start))
    return render(request, "scheduling/school/classes.html", context)
