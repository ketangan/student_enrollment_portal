from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.http import Http404
from django.test import Client
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import PaymentOrder
from scheduling import lesson_changes as changes
from scheduling.lesson_change_forms import RescheduleForm
from scheduling.lesson_models import LessonAvailability, LessonClosure, LessonOccurrence, LessonSeries, LessonTeacher
from scheduling.lessons import cancel_lessons, validate_lesson_room
from scheduling.models import Location, Offering, Room, SchedulingProfile, Session
from scheduling.setup import set_publication
from scheduling.tests.test_lessons import book, music, url

pytestmark = pytest.mark.django_db


def change_data(item, **overrides):
    return {"scope": "one", "first_date": "2030-01-08", "start_time": "11:00", "teacher": str(item.series.teacher_id),
            "location": str(item.series.location_id), "room": str(item.series.room_id), "reason": "Family requested a change", **overrides}


def preview(m, item, data):
    form = RescheduleForm(data, occurrence=item)
    assert form.is_valid(), form.errors
    rows, revision = changes.plan_change(item, form.cleaned_data)
    return rows, changes.change_token(m["user"], item, data, revision)


def move(m, item, data=None):
    data = data or change_data(item)
    rows, token = preview(m, item, data)
    return changes.reschedule(m["user"], m["slug"], item.pk, data, token)


def test_one_move_preserves_history_duration_and_exact_retry(music):
    m = music
    series = book(m)
    item = series.occurrences.first()
    old_start = item.starts_at
    m["service"].duration_minutes = 60
    m["service"].save()
    item = changes.get_occurrence(m["profile"], item.pk)
    data = change_data(item)
    rows, token = preview(m, item, data)
    assert len(rows) == 1 and LessonSeries.objects.count() == 1
    new = changes.reschedule(m["user"], m["slug"], item.pk, data, token)
    assert changes.reschedule(m["user"], m["slug"], item.pk, data, token).pk == new.pk
    item.refresh_from_db()
    assert item.state == "rescheduled" and item.starts_at == old_start
    assert item.replacement.series_id == new.pk and new.predecessor_id == series.pk
    assert new.occurrences.get().ends_at - new.occurrences.get().starts_at == timedelta(minutes=30)
    assert series.occurrences.filter(state="scheduled").count() == 2
    assert new.snapshot["service"] == "Piano" and new.snapshot["duration_minutes"] == 30
    validate_lesson_room(m["room"].pk, old_start, item.ends_at)
    with pytest.raises(ValidationError):
        validate_lesson_room(m["room"].pk, item.replacement.starts_at, item.replacement.ends_at)
    assert not PaymentOrder.objects.exists()
    assert AdminAuditLog.objects.filter(changes__action="reschedule").count() == 1


def test_future_move_keeps_past_closed_canceled_and_separate_exceptions(music, monkeypatch):
    m = music
    LessonClosure.objects.create(location=m["location"], first_date=date(2030, 1, 21), last_date=date(2030, 1, 21), reason="Closure")
    series = book(m, {**m["data"], "last_date": "2030-02-04"})
    dates = list(series.occurrences.all())
    cancel_lessons(m["user"], m["slug"], dates[3].pk, "one", "Away")
    exception = move(m, dates[4], change_data(dates[4], first_date="2030-02-05"))
    monkeypatch.setattr(timezone, "now", lambda: dates[0].ends_at)
    result = move(m, dates[1], change_data(dates[1], scope="future", first_date="2030-01-15"))
    assert result.occurrences.count() == 1
    assert list(series.occurrences.values_list("state", flat=True)) == ["scheduled", "rescheduled", "skipped", "canceled", "rescheduled"]
    assert exception.occurrences.get().state == "scheduled"


def test_future_dates_shift_by_local_calendar_days_across_dst(music):
    m = music
    source = book(m, {**m["data"], "first_date": "2030-03-03", "last_date": "2030-03-17"})
    result = move(m, source.occurrences.first(), change_data(source.occurrences.first(), scope="future", first_date="2030-03-04", start_time="10:00"))
    times = list(result.occurrences.values_list("starts_at", flat=True))
    assert [value.hour for value in times] == [18, 17, 17]
    assert [value.astimezone(ZoneInfo("America/Los_Angeles")).hour for value in times] == [10, 10, 10]


def test_substitute_at_another_location_and_no_room(music):
    m = music
    source = book(m)
    teacher = LessonTeacher.objects.create(profile=m["profile"], name="Substitute", buffer_minutes=10, travel_minutes=45)
    teacher.services.add(m["service"])
    location = Location.objects.create(profile=m["profile"], name="East", timezone_name="America/New_York")
    LessonAvailability.objects.create(teacher=teacher, location=location, weekday=1, start_time=time(9), end_time=time(15))
    item = source.occurrences.first()
    result = move(m, item, change_data(item, teacher=str(teacher.pk), location=str(location.pk), room=""))
    assert result.teacher == teacher and result.location == location and result.room is None
    assert result.snapshot["teacher"] == "Substitute" and result.snapshot["timezone"] == "America/New_York"
    assert result.snapshot["buffer_minutes"] == 10 and source.snapshot["teacher"] == "Alex"


@pytest.mark.parametrize("field,value", [("scope", "all"), ("first_date", "bad"), ("teacher", "9999"),
    ("location", "9999"), ("room", "9999"), ("reason", ""), ("start_time", "bad")])
def test_change_form_invalid_values(music, field, value):
    item = book(music).occurrences.first()
    assert not RescheduleForm(change_data(item, **{field: value}), occurrence=item).is_valid()


def test_change_form_scopes_resources_and_teacher_qualification(music):
    m = music
    item = book(m).occurrences.first()
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="other-lessons"), enabled=True)
    outside = Location.objects.create(profile=other, name="Private")
    local = Location.objects.create(profile=m["profile"], name="West")
    room = Room.objects.create(location=local, name="Wrong location", capacity=3)
    for data in [change_data(item, location=outside.pk), change_data(item, room=room.pk)]:
        assert not RescheduleForm(data, occurrence=item).is_valid()
    m["teacher"].services.clear()
    assert not RescheduleForm(change_data(item), occurrence=item).is_valid()
    m["teacher"].is_active = False
    m["teacher"].save()
    assert not RescheduleForm(change_data(item), occurrence=item).is_valid()


@pytest.mark.parametrize("day", ["2029-12-01", "9999-12-31", "2030-06-30"])
def test_out_of_bounds_change_rejected_without_any_writes(music, day):
    item = book(music).occurrences.first()
    with pytest.raises(ValidationError, match="180 days"):
        preview(music, item, change_data(item, scope="future", first_date=day))
    assert LessonSeries.objects.count() == 1


def test_noop_past_canceled_and_closed_sources_rejected(music, monkeypatch):
    m = music
    item = book(m).occurrences.first()
    with pytest.raises(ValidationError, match="different"):
        preview(m, item, change_data(item, first_date="2030-01-07", start_time="10:00"))
    for state in ["canceled", "skipped"]:
        item.state = state
        with pytest.raises(ValidationError, match="Only future"):
            preview(m, item, change_data(item))
    item.state = "scheduled"
    monkeypatch.setattr(timezone, "now", lambda: item.starts_at)
    with pytest.raises(ValidationError, match="Only future"):
        preview(m, item, change_data(item))


@pytest.mark.parametrize("blocker", ["lesson", "closure", "availability", "workshop"])
def test_one_conflict_blocks_entire_future_move_and_keeps_originals(music, blocker):
    m = music
    source = book(m)
    item = source.occurrences.first()
    data = change_data(item, scope="future")
    rows, _ = preview(m, item, data)
    if blocker == "lesson":
        book(m, {**m["data"], "first_date": "2030-01-15", "last_date": "2030-01-15", "start_time": "11:00"})
    elif blocker == "closure":
        LessonClosure.objects.create(location=m["location"], first_date=date(2030, 1, 15), last_date=date(2030, 1, 15), reason="Closed")
    elif blocker == "availability":
        LessonAvailability.objects.filter(teacher=m["teacher"], weekday=1).delete()
    else:
        offering = Offering.objects.create(location=m["location"], name="Workshop")
        Session.objects.create(offering=offering, room=m["room"], starts_at=rows[1]["starts_at"], ends_at=rows[1]["ends_at"], capacity=4, is_published=True)
    rows, token = preview(m, item, data)
    assert any(row["state"] == "conflict" for row in rows)
    with pytest.raises(ValidationError, match="Nothing was moved"):
        changes.reschedule(m["user"], m["slug"], item.pk, data, token)
    assert source.occurrences.filter(state="scheduled").count() == 3
    assert not source.replacements.exists()


def test_replacement_batch_teacher_gap_conflict(music):
    m = music
    series = book(m)
    first, second, third = list(series.occurrences.all())
    second.starts_at = first.starts_at + timedelta(minutes=35)
    second.ends_at = first.ends_at + timedelta(minutes=35)
    second.save()
    rows, _ = preview(m, first, change_data(first, scope="future"))
    assert rows[1]["state"] == "conflict"


@pytest.mark.parametrize("mutation", ["teacher", "source", "payload", "eligibility", "actor", "expired", "route", "missing_revision"])
def test_stale_or_mismatched_preview_cannot_commit(music, mutation):
    m = music
    source = book(m)
    item = source.occurrences.first()
    data = change_data(item, scope="future")
    _, token = preview(m, item, data)
    actor, pk = m["user"], item.pk
    if mutation == "teacher":
        m["teacher"].name = "Renamed"
        m["teacher"].save()
    elif mutation == "source":
        cancel_lessons(actor, m["slug"], source.occurrences.last().pk, "one", "Away")
    elif mutation == "payload":
        data["start_time"] = "12:00"
    elif mutation == "eligibility":
        m["teacher"].services.clear()
    elif mutation == "actor":
        signed = signing.loads(token, salt=changes.CHANGE_SALT)
        signed["user"] += 100
        token = signing.dumps(signed, salt=changes.CHANGE_SALT)
    elif mutation == "expired":
        with patch("django.core.signing.time.time", return_value=1):
            _, token = preview(m, item, data)
    elif mutation == "missing_revision":
        signed = signing.loads(token, salt=changes.CHANGE_SALT)
        del signed["revision"]
        token = signing.dumps(signed, salt=changes.CHANGE_SALT)
    else:
        pk = source.occurrences.last().pk
    with pytest.raises(ValidationError):
        changes.reschedule(actor, m["slug"], pk, data, token)
    assert not source.replacements.exists()


def test_conflict_added_after_preview_blocks_commit(music):
    m = music
    source = book(m)
    item = source.occurrences.first()
    data = change_data(item, scope="future")
    _, token = preview(m, item, data)
    book(m, {**m["data"], "first_date": "2030-01-15", "last_date": "2030-01-15", "start_time": "11:00"})
    with pytest.raises(ValidationError, match="Nothing was moved"):
        changes.reschedule(m["user"], m["slug"], item.pk, data, token)
    assert source.occurrences.filter(state="scheduled").count() == 3
    assert not source.replacements.exists()


def test_stale_cancel_cannot_cancel_later_lessons_after_source_moves(music):
    m = music
    source = book(m)
    item = source.occurrences.first()
    replacement = move(m, item)
    with pytest.raises(ValidationError, match="Refresh"):
        cancel_lessons(m["user"], m["slug"], item.pk, "future", "Old cancellation page")
    assert source.occurrences.filter(state="scheduled").count() == 2
    assert replacement.occurrences.get().state == "scheduled"


def test_cancel_retry_does_not_expand_original_scope(music):
    m = music
    source = book(m)
    item = source.occurrences.first()
    cancel_lessons(m["user"], m["slug"], item.pk, "one", "Away")
    cancel_lessons(m["user"], m["slug"], item.pk, "future", "Stale cancellation page")
    assert source.occurrences.filter(state="scheduled").count() == 2


@pytest.mark.parametrize("token", ["", "forged", signing.dumps({"key": "bad"}, salt=changes.CHANGE_SALT)])
def test_invalid_change_token(music, token):
    item = book(music).occurrences.first()
    with pytest.raises(ValidationError, match="preview"):
        changes.reschedule(music["user"], music["slug"], item.pk, change_data(item), token)


def test_change_audit_failure_rolls_back_both_sides(music):
    item = book(music).occurrences.first()
    with patch("scheduling.lesson_changes.audit", side_effect=RuntimeError), pytest.raises(RuntimeError):
        move(music, item)
    item.refresh_from_db()
    assert item.state == "scheduled" and item.replacement_id is None and item.version == 0
    assert LessonSeries.objects.count() == 1


def attendance_data(m, item, **overrides):
    return {"attendance": "present", "reason": "Teacher confirmed attendance", "revision": changes.attendance_token(m["user"], item), **overrides}


def test_attendance_after_end_corrections_and_idempotent_retry(music, monkeypatch):
    m = music
    item = book(m).occurrences.first()
    monkeypatch.setattr(timezone, "now", lambda: item.ends_at)
    data = attendance_data(m, item)
    changes.record_attendance(m["user"], m["slug"], item.pk, data)
    changes.record_attendance(m["user"], m["slug"], item.pk, data)
    item.refresh_from_db()
    assert item.attendance == "present" and item.version == 1
    assert AdminAuditLog.objects.filter(changes__action="attendance").count() == 1
    for outcome in ["absent", "no_show", "unmarked"]:
        changes.record_attendance(m["user"], m["slug"], item.pk, attendance_data(m, item, attendance=outcome, reason="Correction"))
        item.refresh_from_db()
        assert item.attendance == outcome
    assert AdminAuditLog.objects.filter(changes__before__attendance="present", changes__after__attendance="absent").exists()
    assert not PaymentOrder.objects.exists() and item.state == "scheduled"


@pytest.mark.parametrize("case", ["future", "canceled", "closed", "bad_status", "missing_reason", "forged", "actor", "expired", "stale"])
def test_attendance_guardrails(music, monkeypatch, case):
    m = music
    item = book(m).occurrences.first()
    data = attendance_data(m, item)
    if case != "future":
        monkeypatch.setattr(timezone, "now", lambda: item.ends_at)
    if case in ["canceled", "closed"]:
        item.state = "canceled" if case == "canceled" else "skipped"
        item.save()
    elif case == "bad_status":
        data["attendance"] = "enrolled"
    elif case == "missing_reason":
        data["reason"] = ""
    elif case == "forged":
        data["revision"] = "forged"
    elif case == "actor":
        data["revision"] = signing.dumps({"user": 9999, "occurrence": item.pk, "version": 0}, salt=changes.ATTENDANCE_SALT)
    elif case == "expired":
        with patch("django.core.signing.time.time", return_value=1):
            data = attendance_data(m, item)
    elif case == "stale":
        changes.record_attendance(m["user"], m["slug"], item.pk, {**data, "attendance": "absent"})
    with pytest.raises(ValidationError):
        changes.record_attendance(m["user"], m["slug"], item.pk, data)


def test_attendance_audit_failure_rolls_back(music, monkeypatch):
    m = music
    item = book(m).occurrences.first()
    monkeypatch.setattr(timezone, "now", lambda: item.ends_at)
    with patch("scheduling.lesson_changes.audit", side_effect=RuntimeError), pytest.raises(RuntimeError):
        changes.record_attendance(m["user"], m["slug"], item.pk, attendance_data(m, item))
    item.refresh_from_db()
    assert item.attendance == "unmarked" and item.version == 0


@pytest.mark.parametrize("fields", [{"attendance": "other"}, {"state": "rescheduled"}, {"state": "canceled", "attendance": "present"}])
def test_database_guards(music, fields):
    item = book(music).occurrences.first()
    with pytest.raises(IntegrityError), transaction.atomic():
        LessonOccurrence.objects.filter(pk=item.pk).update(**fields)


def test_change_view_full_flow_and_history(client, music):
    m = music
    item = book(m).occurrences.first()
    client.force_login(m["user"])
    target = url(m, "lesson_change", pk=item.pk)
    assert client.get(target).status_code == 200
    data = change_data(item)
    response = client.post(target, data)
    assert response.status_code == 200 and response.context["can_confirm"]
    data.update(action="confirm", preview_token=response.context["token"])
    response = client.post(target, data)
    assert response.status_code == 302
    assert client.post(target, data).url == response.url
    detail = client.get(response.url)
    assert b"Original schedule" in detail.content
    item.refresh_from_db()
    old = client.get(url(m, "lesson_series", public_id=item.series.public_id))
    assert b"Rescheduled" in old.content and b"Moved to Jan 8, 2030" in old.content
    listing = client.get(url(m, "lessons"))
    assert len(listing.context["items"]) == 3
    assert all(i.state != "rescheduled" for i in listing.context["items"])


def test_change_view_invalid_and_conflict_states(client, music):
    m = music
    item = book(m).occurrences.first()
    target = url(m, "lesson_change", pk=item.pk)
    client.force_login(m["user"])
    assert client.post(target, {}).status_code == 400
    assert client.post(target, change_data(item, first_date="2030-01-07", start_time="10:00")).status_code == 400
    LessonAvailability.objects.all().delete()
    response = client.post(target, change_data(item))
    assert response.status_code == 200 and not response.context["can_confirm"] and b"Confirm changes" not in response.content
    assert client.post(target, {**change_data(item), "action": "confirm"}).status_code == 400


def test_attendance_view_round_trip_and_failure(client, music, monkeypatch):
    m = music
    item = book(m).occurrences.first()
    target = url(m, "lesson_attendance", pk=item.pk)
    client.force_login(m["user"])
    page = client.get(target)
    assert page.status_code == 200
    assert b'type="hidden" name="revision"' in page.content
    assert b'<label for="id_revision">' not in page.content
    assert client.post(target, {}).status_code == 400
    assert client.post(target, attendance_data(m, item)).status_code == 400
    monkeypatch.setattr(timezone, "now", lambda: item.ends_at)
    response = client.post(target, attendance_data(m, item))
    assert response.status_code == 302
    detail = client.get(response.url)
    assert b"Present" in detail.content and b"Teacher confirmed attendance" in detail.content


@pytest.mark.parametrize("role,code", [("owner", 200), ("scheduler", 200), ("viewer", 404)])
def test_change_access_roles_csrf_disabled_and_tenant_boundaries(client, music, role, code):
    m = music
    item = book(m).occurrences.first()
    m["member"].role = role
    m["member"].save()
    client.force_login(m["user"])
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(m["user"])
    for name in ["lesson_change", "lesson_attendance"]:
        target = url(m, name, pk=item.pk)
        assert client.get(target).status_code == code
        assert strict.post(target, {}).status_code == 403
        if role == "viewer":
            assert client.post(target, {}).status_code == 404
    if role == "viewer":
        page = client.get(url(m, "lesson_series", public_id=item.series.public_id))
        assert b">Reschedule</a>" not in page.content
    with pytest.raises(Http404):
        changes.get_occurrence(m["profile"], 99999)
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="outside"), enabled=True)
    with pytest.raises(Http404):
        changes.get_occurrence(other, item.pk)
    m["profile"].lessons_enabled = False
    m["profile"].save()
    assert client.get(url(m, "lesson_change", pk=item.pk)).status_code == 404
    client.logout()
    assert client.get(url(m, "lesson_attendance", pk=item.pk)).status_code == 302


@pytest.mark.django_db(transaction=True)
def test_concurrent_reschedules_have_one_winner(music):
    m = music
    item = book(m).occurrences.first()
    data = change_data(item)
    tokens = [preview(m, item, data)[1] for _ in range(2)]

    def run(token):
        close_old_connections()
        try:
            return changes.reschedule(m["user"], m["slug"], item.pk, data, token).pk
        except ValidationError:
            return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, tokens))
    assert sum(value is not None for value in results) == 1
    assert item.series.replacements.count() == 1


@pytest.mark.django_db(transaction=True)
def test_reschedule_and_workshop_publish_compete_for_same_room(music):
    m = music
    item = book(m).occurrences.first()
    data = change_data(item)
    rows, token = preview(m, item, data)
    offering = Offering.objects.create(location=m["location"], name="Workshop")
    session = Session.objects.create(offering=offering, room=m["room"], starts_at=rows[0]["starts_at"], ends_at=rows[0]["ends_at"], capacity=4)

    def run(kind):
        close_old_connections()
        try:
            if kind == "lesson":
                changes.reschedule(m["user"], m["slug"], item.pk, data, token)
            else:
                set_publication(m["user"], m["slug"], "sessions", session.pk, True)
            return True
        except ValidationError:
            return False
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["lesson", "workshop"]))
    assert sorted(results) == [False, True]
    item.refresh_from_db()
    session.refresh_from_db()
    assert (item.state == "rescheduled") != session.is_published
