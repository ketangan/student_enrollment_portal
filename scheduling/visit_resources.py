"""Explicit staff mapping and location closures shared by visits and lessons."""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from .lesson_models import LessonClosure, LessonOccurrence, LessonTeacher
from .models import VisitSlot


def closure_reason(location, host, start, end):
    zone = ZoneInfo(location.timezone_name)
    first, last = start.astimezone(zone).date(), (end-timedelta(microseconds=1)).astimezone(zone).date()
    teacher = LessonTeacher.objects.filter(profile=location.profile, booking_host=host).first()
    closed = LessonClosure.objects.filter(location=location, first_date__lte=last, last_date__gte=first)
    closed = closed.filter(Q(teacher=None) | Q(teacher=teacher)) if teacher else closed.filter(teacher=None)
    item = closed.first()
    return item.reason if item else ""


def validate_host(profile, host, location, start, end, exclude=None):
    reason = closure_reason(location, host, start, end)
    if reason:
        raise ValidationError(f"Closed: {reason}")
    if VisitSlot.objects.filter(host=host, session__is_published=True,
            session__starts_at__lt=end, session__ends_at__gt=start).exclude(pk=exclude).exists():
        raise ValidationError("This host already has a visit at that time.")
    teacher = LessonTeacher.objects.filter(profile=profile, booking_host=host).first()
    if teacher:
        for lesson in LessonOccurrence.objects.filter(series__teacher=teacher, state="scheduled",
                starts_at__lt=end+timedelta(hours=4), ends_at__gt=start-timedelta(hours=4)).select_related("series"):
            gap = max(teacher.buffer_minutes, lesson.series.snapshot.get("buffer_minutes", 0))
            if lesson.series.location_id != location.pk:
                gap = max(gap, teacher.travel_minutes, lesson.series.snapshot.get("travel_minutes", 0))
            if lesson.starts_at < end+timedelta(minutes=gap) and lesson.ends_at+timedelta(minutes=gap) > start:
                raise ValidationError("This host has a music lesson or travel gap at that time.")


def validate_teacher_mapping(teacher):
    # Check a new link against both existing calendars, not just subsequent bookings.
    for slot in VisitSlot.objects.filter(host=teacher.booking_host, session__is_published=True, session__ends_at__gt=timezone.now()).select_related("session", "program__offering__location"):
        for lesson in LessonOccurrence.objects.filter(series__teacher_id=teacher.pk, state="scheduled",
                starts_at__lt=slot.session.ends_at+timedelta(hours=4),
                ends_at__gt=slot.session.starts_at-timedelta(hours=4)).select_related("series"):
            gap = max(teacher.buffer_minutes, lesson.series.snapshot.get("buffer_minutes", 0))
            if lesson.series.location_id != slot.program.offering.location_id:
                gap = max(gap, teacher.travel_minutes, lesson.series.snapshot.get("travel_minutes", 0))
            if lesson.starts_at < slot.session.ends_at+timedelta(minutes=gap) and lesson.ends_at+timedelta(minutes=gap) > slot.session.starts_at:
                raise ValidationError("Resolve overlapping visits and lessons before linking this host.")


def apply_visit_closure(closure, start, end):
    affected = VisitSlot.objects.filter(program__offering__location=closure.location,
        session__starts_at__lt=end, session__ends_at__gt=start)
    if closure.teacher_id:
        affected = affected.filter(host__lesson_teacher=closure.teacher)
    if affected.filter(bookings__state="booked").exists():
        raise ValidationError("Move or cancel booked visits before adding this closure.")
    from .models import Session
    Session.objects.filter(visit_slot__in=affected).update(is_published=False)
