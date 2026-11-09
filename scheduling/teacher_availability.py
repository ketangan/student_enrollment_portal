"""Teacher-only access and future availability changes under the booking lock."""
from copy import copy
from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .access import scope_locations, staff_locations
from .capabilities import require_capability
from .editing import protect_edit
from .lesson_forms import AvailabilityForm, ClosureForm
from .lesson_models import LessonAvailability, LessonClosure, LessonOccurrence, LessonTeacher
from .lessons import audit, availability_covers, lesson_access, local_datetime
from .models import SchedulingMembership, SchedulingProfile, VisitSlot
from .school_navigation import workflow_policy


def teacher_access(user, slug, *, locked=False, request=None):
    if not user.is_authenticated or not user.is_active:
        raise Http404
    profiles = SchedulingProfile.objects.select_related("school")
    if locked:
        profiles = profiles.select_for_update(of=("self",))
    profile = get_object_or_404(profiles, school__slug=slug, school__is_active=True,
        enabled=True, lessons_enabled=True)
    from core.services.portal_environment import current_environment, request_environment, school_environment
    environment = request_environment(request) if request is not None else current_environment.get()
    if environment in {"demo", "production"} and environment != school_environment(profile.school):
        raise Http404
    require_capability(profile, "lessons")
    if not workflow_policy(profile.school)["teacher_self_service"]:
        raise Http404
    teacher = get_object_or_404(LessonTeacher, profile=profile, login_user=user,
        login_user__is_active=True, is_active=True)
    membership = SchedulingMembership.objects.filter(profile=profile, user=user).first()
    if membership:
        if not membership.is_active:
            raise Http404
        if not membership.all_locations:
            profile._staff_location_ids = tuple(membership.locations.filter(profile=profile).values_list("pk", flat=True))
    return profile, teacher


def teacher_login_profile(user, request):
    from core.services.portal_environment import scope_schools
    if not user.is_active:
        return None
    teachers = scope_schools(LessonTeacher.objects.filter(login_user=user, is_active=True,
        profile__enabled=True, profile__lessons_enabled=True, profile__school__is_active=True),
        request, "profile__school__").select_related("profile__school")
    for teacher in teachers:
        try:
            profile, _ = teacher_access(user, teacher.profile.school.slug, request=request)
        except (Http404, ValidationError):
            continue
        return profile
    return None


def teacher_occurrences(profile, teacher):
    return scope_locations(LessonOccurrence.objects.filter(series__profile=profile, series__teacher=teacher),
        profile, "series__location_id").select_related("series__location", "series__room")


def teacher_windows(profile, teacher):
    return scope_locations(LessonAvailability.objects.filter(teacher=teacher, location__profile=profile),
        profile, "location_id").select_related("location")


class FutureAvailabilityForm(AvailabilityForm):
    effective_date = forms.DateField(label="Effective from", widget=forms.DateInput(attrs={"type": "date"}))
    retire = forms.BooleanField(required=False, label="Stop offering this window from the effective date")

    def __init__(self, *args, profile, teacher=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teacher"].queryset = LessonTeacher.objects.filter(profile=profile, is_active=True)
        self.fields["location"].queryset = staff_locations(profile)
        if teacher:
            self.fields["teacher"].queryset = self.fields["teacher"].queryset.filter(pk=teacher.pk)
            self.initial["teacher"] = teacher.pk
            self.fields["teacher"].disabled = True
        if self.instance.pk:
            self.fields["teacher"].disabled = True
            self.fields["location"].disabled = True
        else:
            self.fields.pop("retire")
        location = self.instance.location if self.instance.location_id else self.fields["location"].queryset.first()
        if location:
            if not self.initial.get("location"):
                self.initial["location"] = location.pk
            tomorrow = timezone.localdate(timezone=ZoneInfo(location.timezone_name)) + timedelta(days=1)
            self.initial["effective_date"] = max(tomorrow, self.instance.effective_from or tomorrow)

    def clean(self):
        data = super().clean()
        start, end = data.get("start_time"), data.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", "End time must be after start time.")
        effective, location = data.get("effective_date"), data.get("location")
        if not effective or not location:
            return data
        today = timezone.localdate(timezone=ZoneInfo(location.timezone_name))
        if effective <= today or effective == date.max:
            self.add_error("effective_date", "Choose a date after today in this location's time zone.")
        if self.instance.effective_from and effective < self.instance.effective_from:
            self.add_error("effective_date", "Choose a date on or after this window starts.")
        if self.instance.effective_until and effective > self.instance.effective_until:
            self.add_error("effective_date", "This window has already ended before that date.")
        return data


def availability_form(profile, data=None, pk=None, *, teacher=None):
    items = LessonAvailability.objects.filter(teacher__profile=profile, location__profile=profile)
    items = scope_locations(items, profile, "location_id")
    if teacher:
        items = items.filter(teacher=teacher)
    instance = get_object_or_404(items, pk=pk) if pk else LessonAvailability()
    form = FutureAvailabilityForm(data, instance=instance, profile=profile, teacher=teacher)
    return protect_edit(form, profile)


def _window_state(window):
    return {key: str(getattr(window, key)) for key in (
        "teacher_id", "location_id", "weekday", "start_time", "end_time", "effective_from", "effective_until")}


def _overlaps(left, right):
    return (left.weekday == right.weekday and left.start_time < right.end_time and left.end_time > right.start_time
        and (left.effective_from or date.min) <= (right.effective_until or date.max)
        and (right.effective_from or date.min) <= (left.effective_until or date.max))


@transaction.atomic
def save_availability(user, profile, data, pk=None, *, teacher=None):
    if teacher is None:
        profile, _ = lesson_access(user, profile.school.slug, "setup.edit", locked=True)
        if not workflow_policy(profile.school)["teacher_self_service"]:
            raise Http404
    else:
        profile, teacher = teacher_access(user, profile.school.slug, locked=True)
    form = availability_form(profile, data, pk, teacher=teacher)
    # ModelForm validation changes its instance; retain the stored record for splitting.
    original = copy(form.instance) if pk else None
    before = _window_state(original) if original else None
    if not form.is_valid():
        return form
    clean = form.cleaned_data
    effective = clean["effective_date"]
    candidate = form.instance
    candidate.effective_from = effective
    retire = clean.get("retire", False)
    withdraw = bool(retire and original and original.effective_from == effective)
    others = list(LessonAvailability.objects.filter(teacher=candidate.teacher,
        location=candidate.location).exclude(pk=pk))
    if not retire and any(_overlaps(candidate, window) for window in others):
        form.add_error(None, "This window overlaps another availability window. Edit that window instead.")
        return form
    remaining = list(others)
    if original and original.effective_from != effective:
        historical = copy(original)
        historical.effective_until = effective - timedelta(days=1)
        remaining.append(historical)
    if not retire:
        remaining.append(candidate)
    zone = ZoneInfo(candidate.location.timezone_name)
    try:
        start = local_datetime(effective, time.min, zone)
        end = (local_datetime(original.effective_until + timedelta(days=1), time.min, zone)
            if original and original.effective_until and original.effective_until < date.max else None)
    except ValidationError as exc:
        form.add_error("effective_date", "; ".join(exc.messages))
        return form
    affected = LessonOccurrence.objects.filter(series__profile=profile, series__teacher=candidate.teacher,
        series__location=candidate.location, state="scheduled", ends_at__gt=start)
    if end:
        affected = affected.filter(starts_at__lt=end)
    conflicts = []
    for item in affected if original else ():
        wall = item.starts_at.astimezone(zone).replace(tzinfo=None)
        end_wall = item.ends_at.astimezone(zone).replace(tzinfo=None)
        if (withdraw and availability_covers([original], wall, end_wall)
                or not availability_covers(remaining, wall, end_wall)):
            conflicts.append(wall.strftime("%b %d, %Y %I:%M %p"))
    if conflicts:
        form.add_error(None, "Existing lessons would fall outside availability: " + "; ".join(conflicts[:5])
            + (f"; and {len(conflicts) - 5} more" if len(conflicts) > 5 else "")
            + ". Ask the school to move or cancel them explicitly first. Nothing was changed.")
        return form
    try:
        if original and original.effective_from != effective:
            original.effective_until = effective - timedelta(days=1)
            original.full_clean()
            original.save(update_fields=["effective_until"])
            candidate.pk = None
            candidate._state.adding = True
        if not retire:
            candidate.full_clean()
            candidate.save()
        audit(user, profile, original or candidate, "delete" if withdraw else "change" if original else "add", {
            "action": "future_availability", "effective_date": str(effective),
            "before": before,
            "after": None if retire else _window_state(candidate),
        })
        if withdraw:
            # The form permits this only before the window starts; retain its ID in the audit.
            original.delete()
    except ValidationError as exc:
        transaction.set_rollback(True)
        form.add_error(None, "; ".join(exc.messages))
    return form


def time_off_form(profile, teacher, data=None):
    form = ClosureForm(data, instance=LessonClosure(teacher=teacher))
    form.fields["location"].queryset = staff_locations(profile)
    form.fields["teacher"].queryset = LessonTeacher.objects.filter(pk=teacher.pk, profile=profile)
    form.fields["teacher"].disabled = True
    form.fields["teacher"].required = True
    return form


@transaction.atomic
def save_time_off(user, slug, data):
    profile, teacher = teacher_access(user, slug, locked=True)
    form = time_off_form(profile, teacher, data)
    if not form.is_valid():
        return form
    closure = form.instance
    zone = ZoneInfo(closure.location.timezone_name)
    if closure.first_date <= timezone.localdate(timezone=zone):
        form.add_error("first_date", "Choose a date after today in this location's time zone.")
        return form
    try:
        start = local_datetime(closure.first_date, time.min, zone)
        end = local_datetime(closure.last_date + timedelta(days=1), time.min, zone)
        if teacher_occurrences(profile, teacher).filter(state="scheduled", series__location=closure.location,
                starts_at__lt=end, ends_at__gt=start).exists():
            raise ValidationError("Ask the school to move or cancel lessons during this time off first.")
        if teacher.booking_host_id and VisitSlot.objects.filter(host_id=teacher.booking_host_id,
                program__offering__location=closure.location, session__starts_at__lt=end,
                session__ends_at__gt=start).filter(session__is_published=True).exists():
            raise ValidationError("Ask the school to resolve published visits during this time off first.")
        # Unlike an owner closure, teacher time off must not unpublish visit sessions.
        if teacher.booking_host_id and VisitSlot.objects.filter(host_id=teacher.booking_host_id,
                program__offering__location=closure.location, session__starts_at__lt=end,
                session__ends_at__gt=start, bookings__state="booked").exists():
            raise ValidationError("Ask the school to resolve booked visits during this time off first.")
        closure.full_clean()
    except ValidationError as exc:
        form.add_error(None, "; ".join(exc.messages))
    else:
        closure.save()
        audit(user, profile, closure, "add", {"action": "teacher_time_off", "teacher_id": teacher.pk,
            "location_id": closure.location_id, "first_date": str(closure.first_date),
            "last_date": str(closure.last_date), "reason": closure.reason})
    return form
