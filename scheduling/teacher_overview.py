"""Read-only weekly staffing grid; booking eligibility stays in SlotCalendar."""
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django import forms
from django.db.models import Q
from django.utils import timezone

from .lesson_models import LessonOccurrence, LessonService, LessonTeacher
from .models import VisitSlot


class OverviewFilter(forms.Form):
    week = forms.DateField(required=False, label="Week of", widget=forms.DateInput(attrs={"type": "date"}))
    service = forms.ModelChoiceField(required=False, label="Lesson type", empty_label="All lesson types",
        queryset=LessonService.objects.none())
    staff = forms.ModelChoiceField(required=False, label="Teacher", empty_label="All teachers",
        queryset=LessonTeacher.objects.none())

    def __init__(self, data, *, profile):
        super().__init__(data)
        self.fields["service"].queryset = LessonService.objects.filter(profile=profile)
        self.fields["staff"].queryset = LessonTeacher.objects.filter(profile=profile)

    def clean_week(self):
        day = self.cleaned_data["week"]
        if day and not 1900 <= day.year <= 9998:
            raise forms.ValidationError("Choose a year between 1900 and 9998.")
        return day


def weekly_overview(request, profile, context):
    form = OverviewFilter(request.GET, profile=profile)
    if not form.is_valid() or not context["location_filter"].is_valid():
        return {"overview": True, "overview_filter": form, "overview_rows": [], "overview_days": []}
    locations = list(context["location_filter"].filter(profile.locations.all(), "pk"))
    zone = ZoneInfo(locations[0].timezone_name) if locations else timezone.get_default_timezone()
    day = form.cleaned_data["week"] or timezone.localdate(timezone=zone)
    start = day - timedelta(days=day.weekday())
    days = [start + timedelta(days=n) for n in range(7)]
    form.initial["week"] = start
    # Bound forms render submitted values, including the default week's date.
    form.data = form.data.copy()
    form.data["week"] = start.isoformat()
    teachers = context["teachers"].select_related("login_user")
    service, teacher = form.cleaned_data["service"], form.cleaned_data["staff"]
    if service:
        teachers = teachers.filter(services=service)
    if teacher:
        teachers = teachers.filter(pk=teacher.pk)
    teachers = list(teachers)
    ids = [item.pk for item in teachers]
    windows = list(context["availability"].filter(teacher_id__in=ids, location__in=locations)
        .filter(Q(effective_from=None) | Q(effective_from__lte=days[-1]))
        .filter(Q(effective_until=None) | Q(effective_until__gte=start)))
    closures = list(context["closures"].filter(location__in=locations,
        first_date__lte=days[-1], last_date__gte=start).filter(Q(teacher_id__in=ids) | Q(teacher=None)))
    # Fetch once per grid, not per teacher/day. The extra day covers all location
    # time zones; each item is clipped to the correct local calendar day below.
    lower = datetime.combine(start - timedelta(days=1), time.min, tzinfo=zone)
    upper = datetime.combine(days[-1] + timedelta(days=2), time.min, tzinfo=zone)
    lessons = list(LessonOccurrence.objects.filter(series__profile=profile,
        series__teacher_id__in=ids, series__location__in=locations, state="scheduled",
        starts_at__lt=upper, ends_at__gt=lower).select_related("series__location", "series__student"))
    host_teachers = {item.booking_host_id: item.pk for item in teachers if item.booking_host_id}
    visits = list(VisitSlot.objects.filter(host_id__in=host_teachers,
        program__offering__location__in=locations, session__is_published=True,
        session__starts_at__lt=upper, session__ends_at__gt=lower).select_related("session", "program__offering__location")) if host_teachers else []
    bookings = {pk: [] for pk in ids}
    for lesson in lessons:
        bookings[lesson.series.teacher_id].append((lesson.starts_at, lesson.ends_at, lesson.series.location, "Lesson"))
    for visit in visits:
        bookings[host_teachers[visit.host_id]].append((visit.session.starts_at, visit.session.ends_at, visit.program.offering.location, "Visit"))
    rows = []
    for item in teachers:
        cells = []
        for day in days:
            blocks = []
            day_closures = [c for c in closures if c.teacher_id in (None, item.pk) and c.first_date <= day <= c.last_date]
            for window in windows:
                if (window.teacher_id != item.pk or window.weekday != day.weekday()
                        or window.effective_from and window.effective_from > day
                        or window.effective_until and window.effective_until < day):
                    continue
                if not any(c.location_id == window.location_id for c in day_closures):
                    blocks.append({"kind": "hours", "label": "Working hours", "start": window.start_time,
                        "end": window.end_time, "location": window.location})
            for closure in day_closures:
                blocks.append({"kind": "off", "label": closure.reason, "location": closure.location})
            for begins, ends, location, label in bookings[item.pk]:
                local_zone = ZoneInfo(location.timezone_name)
                local_start, local_end = begins.astimezone(local_zone), ends.astimezone(local_zone)
                if local_start.date() <= day <= local_end.date() and not (local_end.date() == day and local_end.time() == time.min):
                    blocks.append({"kind": "booked", "label": label, "location": location,
                        "start": local_start.time() if local_start.date() == day else time.min,
                        "end": local_end.time() if local_end.date() == day else time.max})
            cells.append({"day": day, "blocks": blocks})
        rows.append({"teacher": item, "cells": cells})
    def week_url(day):
        query = request.GET.copy()
        query["week"] = day.isoformat()
        query["view"] = "week"
        return "?" + urlencode(query)
    return {"overview": True, "overview_filter": form, "overview_rows": rows, "overview_days": days,
        "previous_week": week_url(start - timedelta(days=7)), "next_week": week_url(start + timedelta(days=7)),
        "overview_show_location": len(locations) > 1, "overview_zone": locations[0].timezone_name if len(locations) == 1 else "Local times"}
