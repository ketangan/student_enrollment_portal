from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from core.models import School
from scheduling import lessons, visits
from scheduling.lesson_models import LessonClosure, LessonTeacher
from scheduling.models import Offering, Session, VisitProgram, VisitSlot, SchedulingProfile, SchedulingMembership, Room
from scheduling.visit_resources import validate_host, closure_reason, validate_teacher_mapping
from .test_lessons import music, book as lesson_book
from .test_visits import tour, book, booking_data, post_data, url

pytestmark = pytest.mark.django_db


def visit_for_music(m, start, room=None):
    offering = Offering.objects.create(location=m["location"], name="Music tour", is_published=True)
    program = VisitProgram.objects.create(offering=offering)
    session = Session.objects.create(offering=offering, room=room or m["room"], capacity=4,
        starts_at=start, ends_at=start+timedelta(minutes=30), is_published=True)
    return VisitSlot.objects.create(program=program, session=session, host=m["member"])


def test_explicit_host_mapping_conflicts_both_directions(music):
    m = music
    series = lesson_book(m)
    occurrence = series.occurrences.first()
    teacher = m["teacher"]
    teacher.booking_host = m["member"]
    teacher.full_clean()
    teacher.save()
    with pytest.raises(ValidationError, match="music lesson"):
        validate_host(m["profile"], m["member"], m["location"], occurrence.starts_at, occurrence.ends_at)
    # A visit may use a different room; staff time still must not overlap.
    other_room = Room.objects.create(location=m["location"], name="Tour room", capacity=8)
    slot = visit_for_music(m, occurrence.starts_at+timedelta(days=1), other_room)
    data = dict(teacher=teacher, student=m["student"], room=m["room"], location=m["location"])
    assert "hosting a visit" in lessons.slot_conflict(data, slot.session.starts_at, slot.session.ends_at)
    assert not lessons.slot_conflict(data, slot.session.ends_at+timedelta(hours=1), slot.session.ends_at+timedelta(hours=2))
    day = timezone.localtime(slot.session.starts_at, ZoneInfo(m["location"].timezone_name)).date()
    LessonClosure.objects.create(location=m["location"], teacher=teacher, first_date=day, last_date=day, reason="Host away")
    assert closure_reason(m["location"], m["member"], slot.session.starts_at, slot.session.ends_at) == "Host away"
    with pytest.raises(ValidationError, match="Closed"):
        validate_host(m["profile"], m["member"], m["location"], slot.session.starts_at, slot.session.ends_at, slot.pk)


def test_mapping_detects_existing_conflicts_and_tenant_mismatch(music):
    m = music
    item = lesson_book(m).occurrences.first()
    slot = visit_for_music(m, item.starts_at)
    teacher = m["teacher"]
    teacher.booking_host = m["member"]
    with pytest.raises(ValidationError, match="Resolve overlapping"):
        teacher.full_clean()
    Session.objects.filter(pk=slot.session_id).update(is_published=False)
    teacher.full_clean()
    school = School.objects.create(slug="other-host", display_name="Other")
    profile = SchedulingProfile.objects.create(school=school)
    host = SchedulingMembership.objects.create(profile=profile, user=m["user"], role="owner")
    teacher.booking_host = host
    with pytest.raises(ValidationError, match="this workspace"):
        teacher.full_clean()


def test_travel_and_buffer_between_locations(music):
    from scheduling.models import Location
    m = music
    item = lesson_book(m).occurrences.first()
    teacher = m["teacher"]
    teacher.booking_host = m["member"]
    teacher.save()
    distant = Location.objects.create(profile=m["profile"], name="Second campus")
    with pytest.raises(ValidationError, match="travel gap"):
        validate_host(m["profile"], m["member"], distant, item.ends_at+timedelta(minutes=10), item.ends_at+timedelta(minutes=40))
    validate_host(m["profile"], m["member"], distant, item.ends_at+timedelta(minutes=30), item.ends_at+timedelta(minutes=60))
    slot = visit_for_music(m, item.ends_at+timedelta(minutes=10))
    Offering.objects.filter(pk=slot.program.offering_id).update(location=distant)
    with pytest.raises(ValidationError, match="Resolve overlapping"):
        validate_teacher_mapping(teacher)
    data = dict(teacher=teacher, student=None, room=None, location=m["location"])
    assert "hosting a visit" in lessons.slot_conflict(data, slot.session.ends_at+timedelta(minutes=10), slot.session.ends_at+timedelta(minutes=40))


def test_lesson_closure_closes_empty_visits_and_refuses_bookings(music):
    m = music
    slot = visit_for_music(m, timezone.now()+timedelta(days=4))
    day = timezone.localtime(slot.session.starts_at, ZoneInfo(m["location"].timezone_name)).date()
    teacher = m["teacher"]
    teacher.booking_host = m["member"]
    teacher.save()
    book(slot)
    data = {"location": m["location"].pk, "teacher": teacher.pk, "first_date": day, "last_date": day, "reason": "Closed"}
    assert "Move or cancel booked visits" in str(lessons.save_lesson_setup(m["user"], m["slug"], "closures", data).errors)
    booking = slot.bookings.get()
    visits.change_state(m["user"], m["slug"], booking.public_id, "canceled", "booked")
    assert lessons.save_lesson_setup(m["user"], m["slug"], "closures", data).is_valid()
    slot.session.refresh_from_db()
    assert not slot.session.is_published


def test_visit_closure_does_not_cancel_music_lessons(music, client):
    m = music
    item = lesson_book(m).occurrences.first()
    client.force_login(m["user"])
    slot = visit_for_music(m, item.starts_at+timedelta(days=1))
    day = timezone.localtime(item.starts_at, ZoneInfo(m["location"].timezone_name)).date()
    response = client.post(url(slot, "visit_closure"), {"location": m["location"].pk, "first_date": day, "last_date": day, "reason": "Holiday"})
    assert response.status_code == 400 and b"Move or cancel scheduled lessons" in response.content
    assert not LessonClosure.objects.exists()


def test_stale_booking_token_cannot_book_newly_closed_day(tour):
    data = booking_data(tour)
    day = timezone.localtime(tour.session.starts_at, ZoneInfo(tour.program.offering.location.timezone_name)).date()
    LessonClosure.objects.create(location=tour.program.offering.location, first_date=day, last_date=day, reason="Holiday")
    with pytest.raises(ValidationError, match="Closed"):
        visits.book(tour.host.profile.school.slug, tour.pk, data, "browser-a")
