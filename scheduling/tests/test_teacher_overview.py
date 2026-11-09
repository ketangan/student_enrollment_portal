from datetime import date, time, timedelta
from types import SimpleNamespace

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from scheduling import lessons
from scheduling.lesson_models import LessonAvailability, LessonClosure, LessonService, LessonTeacher
from scheduling.models import SchedulingProfile
from scheduling.tests.test_lessons import music, book, url

pytestmark = pytest.mark.django_db


@pytest.fixture
def school_music(music, monkeypatch, client):
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"navigation": {"layout": "school", "simplified": True, "single_location": True,
            "features": ["lessons"]}, "workflows": {"teacher_self_service": True}}}))
    client.force_login(music["user"])
    return music


def test_overview_working_hours_booked_and_closures(school_music, client):
    m = school_music
    book(m)
    LessonClosure.objects.create(location=m["location"], first_date=date(2030, 1, 8),
        last_date=date(2030, 1, 8), reason="School closed")
    LessonClosure.objects.create(location=m["location"], teacher=m["teacher"], first_date=date(2030, 1, 9),
        last_date=date(2030, 1, 9), reason="Teacher away")
    response = client.get(url(m, "lesson_setup"), {"week": "2030-01-09"})
    assert response.status_code == 200
    assert response.context["overview_days"][0] == date(2030, 1, 7)
    cells = response.context["overview_rows"][0]["cells"]
    assert {b["kind"] for b in cells[0]["blocks"]} == {"hours", "booked"}
    assert [b["kind"] for b in cells[1]["blocks"]] == ["off"]
    assert cells[1]["blocks"][0]["label"] == "School closed"
    assert cells[2]["blocks"][0]["label"] == "Teacher away"
    assert b'Booked: Lesson' in response.content
    assert b'Working hours' in response.content


def test_effective_dates_and_filters(school_music, client):
    m = school_music
    m["teacher"].availability.all().update(effective_until=date(2030, 1, 6))
    LessonAvailability.objects.create(teacher=m["teacher"], location=m["location"], weekday=0,
        start_time=time(9), end_time=time(12), effective_from=date(2030, 1, 14))
    teacher = LessonTeacher.objects.create(profile=m["profile"], name="Other")
    other_service = LessonService.objects.create(profile=m["profile"], name="Voice", subject="Voice", duration_minutes=30)
    teacher.services.add(other_service)
    page = client.get(url(m, "lesson_setup"), {"week": "2030-01-07", "service": m["service"].pk})
    assert [r["teacher"].pk for r in page.context["overview_rows"]] == [m["teacher"].pk]
    assert all(not cell["blocks"] for cell in page.context["overview_rows"][0]["cells"])
    page = client.get(url(m, "lesson_setup"), {"week": "2030-01-14", "staff": m["teacher"].pk})
    assert page.context["overview_rows"][0]["cells"][0]["blocks"][0]["start"] == time(9)
    assert f'staff={m["teacher"].pk}' in page.context["next_week"]


@pytest.mark.parametrize("data", [{"week": "no"}, {"week": "9999-12-31"}, {"staff": "999999"}, {"service": "999999"}])
def test_invalid_filters_never_broaden_results(school_music, client, data):
    page = client.get(url(school_music, "lesson_setup"), data)
    assert page.context["overview_filter"].errors
    assert page.context["overview_rows"] == []


def test_query_count_does_not_grow_per_teacher(school_music, client):
    m = school_music
    def count():
        with CaptureQueriesContext(connection) as queries:
            assert client.get(url(m, "lesson_setup"), {"week": "2030-01-07"}).status_code == 200
        return len(queries)
    before = count()
    for number in range(12):
        teacher = LessonTeacher.objects.create(profile=m["profile"], name=f"Teacher {number}")
        teacher.services.add(m["service"])
        LessonAvailability.objects.create(teacher=teacher, location=m["location"], weekday=0,
            start_time=time(9), end_time=time(12))
    assert count() == before


def test_lesson_type_creation_assignment_and_teacher_edit(school_music, client):
    m = school_music
    page = client.get(url(m, "lesson_setup"), {"section": "services"})
    assert b'Add lesson type' in page.content and b'Piano' in page.content
    created = client.post(url(m, "lesson_setup_new", kind="services"),
        {"name": "Violin trial", "subject": "Violin", "duration_minutes": 30, "is_active": "on"})
    assert created.status_code == 302 and created.url.endswith("?section=services")
    service = LessonService.objects.get(profile=m["profile"], name="Violin trial")
    form = lessons.setup_form(m["profile"], "teachers", pk=m["teacher"].pk)
    assert form.fields["services"].widget.__class__.__name__ == "CheckboxSelectMultiple"
    data = {"name": "Alex Updated", "services": [m["service"].pk, service.pk], "buffer_minutes": 10,
        "travel_minutes": 20, "is_active": "on", "edit_revision": form["edit_revision"].value()}
    response = client.post(url(m, "lesson_setup_edit", kind="teachers", pk=m["teacher"].pk), data)
    assert response.status_code == 302
    m["teacher"].refresh_from_db()
    assert m["teacher"].name == "Alex Updated"
    assert set(m["teacher"].services.values_list("pk", flat=True)) == {m["service"].pk, service.pk}
    assert m["teacher"].buffer_minutes == 10


def test_existing_booking_keeps_gap_snapshot_when_default_changes(school_music):
    m = school_music
    series = book(m)
    snapshot = dict(series.snapshot)
    form = lessons.setup_form(m["profile"], "teachers", pk=m["teacher"].pk)
    data = {"name": m["teacher"].name, "services": [m["service"].pk], "buffer_minutes": 0,
        "travel_minutes": 0, "is_active": "on", "edit_revision": form["edit_revision"].value()}
    saved = lessons.save_lesson_setup(m["user"], m["slug"], "teachers", data, m["teacher"].pk)
    assert saved.is_valid(), saved.errors
    series.refresh_from_db()
    assert series.snapshot == snapshot
    shifted = dict(m["data"], start_time="10:32")
    form = lessons.SeriesForm(shifted, profile=m["profile"])
    assert form.is_valid()
    assert lessons.plan_series(form.cleaned_data)[0]["state"] == "conflict"


def test_read_only_role_and_foreign_teacher_are_blocked(school_music, client):
    m = school_music
    m["member"].role = "viewer"
    m["member"].save()
    page = client.get(url(m, "lesson_setup"), {"view": "list"})
    assert b'Add teacher' not in page.content and b'aria-label="Edit Alex"' not in page.content
    assert client.get(url(m, "lesson_setup_new", kind="services")).status_code == 404
    from core.models import School
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="unrelated"))
    teacher = LessonTeacher.objects.create(profile=other, name="Hidden")
    page = client.get(url(m, "lesson_setup"), {"teacher": teacher.pk, "view": "week"})
    assert page.status_code == 404


def test_hosted_visits_are_visible_and_canceled_lessons_are_not(school_music, client):
    from scheduling.tests.test_visit_resources import visit_for_music
    m = school_music
    series = book(m)
    occurrence = series.occurrences.first()
    m["teacher"].booking_host = m["member"]
    m["teacher"].save()
    visit_for_music(m, occurrence.starts_at + timedelta(hours=3))
    series.occurrences.update(state="canceled")
    page = client.get(url(m, "lesson_setup"), {"week": "2030-01-07"})
    blocks = page.context["overview_rows"][0]["cells"][0]["blocks"]
    assert [b["label"] for b in blocks if b["kind"] == "booked"] == ["Visit"]


def test_empty_default_week_and_invalid_location(school_music, client):
    m = school_music
    page = client.get(url(m, "lesson_setup"))
    assert len(page.context["overview_days"]) == 7
    page = client.get(url(m, "lesson_setup"), {"location": "99999"})
    assert page.context["overview_rows"] == []
    client.get(url(m, "lesson_setup"), {"location": ""})
    service = LessonService.objects.create(profile=m["profile"], name="Unassigned", subject="Voice", duration_minutes=30)
    page = client.get(url(m, "lesson_setup"), {"service": service.pk})
    assert page.context["overview_rows"] == []


def test_shared_time_off_is_visible_in_teacher_detail(school_music, client):
    m = school_music
    LessonClosure.objects.create(location=m["location"], first_date=date(2030, 1, 8),
        last_date=date(2030, 1, 8), reason="School closed")
    page = client.get(url(m, "lesson_setup"), {"teacher": m["teacher"].pk})
    assert b'School closed (all teachers)' in page.content


def test_service_foreign_selection_and_stale_teacher_edits_rejected(school_music):
    from core.models import School
    m = school_music
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="foreign-service"))
    service = LessonService.objects.create(profile=other, name="Foreign", subject="Voice", duration_minutes=30)
    form = lessons.setup_form(m["profile"], "teachers", pk=m["teacher"].pk)
    data = {"name": m["teacher"].name, "services": [service.pk], "buffer_minutes": 0,
        "travel_minutes": 0, "is_active": "on", "edit_revision": form["edit_revision"].value()}
    saved = lessons.save_lesson_setup(m["user"], m["slug"], "teachers", data, m["teacher"].pk)
    assert "services" in saved.errors
    m["teacher"].name = "Changed elsewhere"
    m["teacher"].save()
    data["services"] = [m["service"].pk]
    saved = lessons.save_lesson_setup(m["user"], m["slug"], "teachers", data, m["teacher"].pk)
    assert "edit_revision" in saved.errors
