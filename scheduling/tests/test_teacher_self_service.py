from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.http import Http404
from django.test import Client, RequestFactory
from django.urls import reverse

from core.models import AdminAuditLog, School, SchoolAdminMembership
from scheduling import lessons, school_navigation
from scheduling.lesson_models import LessonAvailability, LessonClosure, LessonOccurrence, LessonTeacher
from scheduling.models import Location, SchedulingMembership, SchedulingProfile
from scheduling.teacher_availability import (availability_form, save_availability, save_time_off,
    teacher_access, teacher_occurrences)
from scheduling.tests.test_lessons import music, book
from scheduling.tests.test_appointments import offer, prepare, CONTACT

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["legacy", "school", "bookings"])
def teacher_context(music, monkeypatch, request):
    policy = {"layout": "school", "teacher_self_service": True}
    def config(slug):
        if request.param == "legacy":
            return SimpleNamespace(raw={"scheduling": {"navigation": policy}})
        navigation = {"layout": request.param}
        if "features" in policy:
            navigation["features"] = policy["features"]
        return SimpleNamespace(raw={"scheduling": {"navigation": navigation,
            "workflows": {"teacher_self_service": policy["teacher_self_service"]}}})
    monkeypatch.setattr(school_navigation, "load_school_config", config)
    account = get_user_model().objects.create_user(username="teacher-only", password="teacher-password")
    music["teacher"].login_user = account
    music["teacher"].save(update_fields=["login_user"])
    return {**music, "account": account, "policy": policy}


def window(m):
    return LessonAvailability.objects.get(teacher=m["teacher"], location=m["location"], weekday=0, effective_until=None)


def edit_data(m, item=None, **changes):
    item = item or window(m)
    form = availability_form(m["profile"], pk=item.pk, teacher=m["teacher"])
    return {"teacher": m["teacher"].pk, "location": item.location_id, "weekday": item.weekday,
        "start_time": "09:00", "end_time": "12:00", "effective_date": "2030-01-14",
        "edit_revision": form["edit_revision"].value(), **changes}


def route(m, name, **kwargs):
    return reverse("scheduling:teacher_" + name, kwargs={"school_slug": m["slug"], **kwargs})


def test_teacher_binding_requires_owner_and_verification_without_granting_membership(teacher_context):
    m = teacher_context
    account = get_user_model().objects.create_user(username="new-teacher")
    data = {"name": "Another teacher", "services": [m["service"].pk], "buffer_minutes": 0,
        "travel_minutes": 30, "is_active": "on", "login_username": account.username}
    assert not lessons.save_lesson_setup(m["user"], m["slug"], "teachers", data).is_valid()
    result = lessons.save_lesson_setup(m["user"], m["slug"], "teachers", {**data, "login_verified": "on"})
    assert result.is_valid(), result.errors
    assert result.instance.login_user == account
    assert not SchedulingMembership.objects.filter(user=account).exists()
    assert not SchoolAdminMembership.objects.filter(user=account).exists()
    assert AdminAuditLog.objects.filter(changes__login_user__to=account.pk).exists()
    for role in ("viewer", "scheduler"):
        m["member"].role = role
        m["member"].save()
        with pytest.raises(Http404):
            lessons.save_lesson_setup(m["user"], m["slug"], "teachers", data)


def test_teacher_binding_duplicate_and_inactive_accounts_rejected(teacher_context):
    m = teacher_context
    data = {"name": "Duplicate", "services": [m["service"].pk], "buffer_minutes": 0,
        "travel_minutes": 30, "is_active": "on", "login_username": m["account"].username, "login_verified": "on"}
    assert not lessons.save_lesson_setup(m["user"], m["slug"], "teachers", data).is_valid()
    m["account"].is_active = False
    m["account"].save()
    assert not lessons.save_lesson_setup(m["user"], m["slug"], "teachers", data).is_valid()
    with pytest.raises(IntegrityError), transaction.atomic():
        LessonTeacher.objects.create(profile=m["profile"], name="Constraint", login_user=m["account"])


def test_hidden_visit_host_preserves_mapping_and_ignores_posted_replacement(teacher_context):
    m = teacher_context
    m["policy"]["features"] = ["lessons"]
    m["teacher"].booking_host = m["member"]
    m["teacher"].save()
    form = lessons.setup_form(m["profile"], "teachers", pk=m["teacher"].pk)
    assert "booking_host" not in form.fields
    result = lessons.save_lesson_setup(m["user"], m["slug"], "teachers", {
        "name": "Updated teacher", "services": [m["service"].pk], "buffer_minutes": 5,
        "travel_minutes": 30, "is_active": "on", "booking_host": "",
        "login_username": m["account"].username, "edit_revision": form["edit_revision"].value(),
    }, m["teacher"].pk)
    assert result.is_valid(), result.errors
    m["teacher"].refresh_from_db()
    assert m["teacher"].booking_host_id == m["member"].pk


def test_opt_out_keeps_legacy_freeze_and_hides_binding(teacher_context):
    m = teacher_context
    m["policy"]["teacher_self_service"] = False
    book(m)
    form = lessons.setup_form(m["profile"], "availability", pk=window(m).pk)
    assert all(field.field.disabled for field in form.visible_fields())
    assert "login_username" not in lessons.setup_form(m["profile"], "teachers", pk=m["teacher"].pk).fields
    with pytest.raises(Http404):
        teacher_access(m["account"], m["slug"])


@pytest.mark.parametrize("change", ["binding", "teacher", "user", "profile", "school", "lessons", "capability", "opt_in", "membership"])
def test_access_revocation_rechecked_on_save(teacher_context, change):
    m = teacher_context
    item = window(m)
    data = edit_data(m, item)
    if change == "binding":
        LessonTeacher.objects.filter(pk=m["teacher"].pk).update(login_user=None)
    elif change == "teacher":
        LessonTeacher.objects.filter(pk=m["teacher"].pk).update(is_active=False)
    elif change == "user":
        get_user_model().objects.filter(pk=m["account"].pk).update(is_active=False)
    elif change == "school":
        School.objects.filter(pk=m["profile"].school_id).update(is_active=False)
    elif change in {"profile", "lessons", "capability"}:
        fields = {"profile": {"enabled": False}, "lessons": {"lessons_enabled": False}, "capability": {"capabilities": []}}
        SchedulingProfile.objects.filter(pk=m["profile"].pk).update(**fields[change])
    elif change == "opt_in":
        m["policy"]["teacher_self_service"] = False
    else:
        SchedulingMembership.objects.create(profile=m["profile"], user=m["account"], is_active=False)
    with pytest.raises(Http404):
        save_availability(m["account"], m["profile"], data, item.pk, teacher=m["teacher"])
    item.refresh_from_db()
    assert item.effective_until is None


def test_own_scope_and_location_intersection(teacher_context):
    m = teacher_context
    book(m)
    other = Location.objects.create(profile=m["profile"], name="Restricted campus")
    private = LessonAvailability.objects.create(teacher=m["teacher"], location=other, weekday=0,
        start_time=time(9), end_time=time(12))
    member = SchedulingMembership.objects.create(profile=m["profile"], user=m["account"], all_locations=False)
    member.locations.add(other)
    profile, teacher = teacher_access(m["account"], m["slug"])
    assert not teacher_occurrences(profile, teacher).exists()
    with pytest.raises(Http404):
        save_availability(m["account"], profile, edit_data(m), window(m).pk, teacher=teacher)
    result = save_availability(m["account"], profile, edit_data(m, private), private.pk, teacher=teacher)
    assert result.is_valid(), result.errors
    member.locations.clear()
    with pytest.raises(Http404):
        save_availability(m["account"], profile, edit_data(m, private), private.pk, teacher=teacher)


def test_invalid_time_has_readable_field_error(teacher_context):
    m = teacher_context
    item = window(m)
    result = save_availability(m["account"], m["profile"],
        edit_data(m, item, end_time="08:00"), item.pk, teacher=m["teacher"])
    assert result.errors["end_time"] == ["End time must be after start time."]
    assert not result.non_field_errors()
    item.refresh_from_db()
    assert item.effective_until is None


def test_future_split_preserves_occurrences_and_existing_dates(teacher_context):
    m = teacher_context
    book(m)
    before = list(LessonOccurrence.objects.values())
    item = window(m)
    result = save_availability(m["account"], m["profile"], edit_data(m), item.pk, teacher=m["teacher"])
    assert result.is_valid(), result.errors
    item.refresh_from_db()
    assert item.start_time == time(0) and item.effective_until == date(2030, 1, 13)
    new = window(m)
    assert new.pk != item.pk and new.effective_from == date(2030, 1, 14)
    assert list(LessonOccurrence.objects.values()) == before
    assert lessons.availability_covers([item, new], datetime(2030, 1, 7, 13), datetime(2030, 1, 7, 13, 30))
    assert not lessons.availability_covers([item, new], datetime(2030, 1, 14, 13), datetime(2030, 1, 14, 13, 30))
    assert lessons.availability_covers([item, new], datetime(2030, 1, 14, 11), datetime(2030, 1, 14, 11, 30))


def test_conflicting_edit_and_retirement_leave_all_records_unchanged(teacher_context):
    m = teacher_context
    book(m)
    item = window(m)
    before = list(LessonAvailability.objects.values())
    for changes in ({"start_time": "11:00"}, {"retire": "on"}):
        result = save_availability(m["account"], m["profile"], edit_data(m, **changes), item.pk, teacher=m["teacher"])
        assert not result.is_valid()
        assert "Existing lessons" in str(result.errors)
        assert list(LessonAvailability.objects.values()) == before


def test_owner_uses_same_future_edit_service(teacher_context):
    m = teacher_context
    book(m)
    item = window(m)
    form = lessons.save_lesson_setup(m["user"], m["slug"], "availability", edit_data(m), item.pk)
    assert form.is_valid(), form.errors
    item.refresh_from_db()
    assert item.effective_until == date(2030, 1, 13)


def test_stale_forms_cannot_replace_newer_hours(teacher_context):
    m = teacher_context
    item = window(m)
    data = edit_data(m)
    assert save_availability(m["account"], m["profile"], data, item.pk, teacher=m["teacher"]).is_valid()
    before = list(LessonAvailability.objects.values())
    result = save_availability(m["account"], m["profile"], {**data, "start_time": "11:00"}, item.pk, teacher=m["teacher"])
    assert not result.is_valid()
    assert list(LessonAvailability.objects.values()) == before


@pytest.mark.parametrize("changes", [{"effective_date": "2030-01-01"}, {"effective_date": "2029-12-31"},
    {"effective_date": "9999-12-31"}, {"start_time": "15:00", "end_time": "12:00"}, {"weekday": 7}])
def test_invalid_effective_edits_never_write(teacher_context, changes):
    m = teacher_context
    before = list(LessonAvailability.objects.values())
    result = save_availability(m["account"], m["profile"], edit_data(m, **changes), window(m).pk, teacher=m["teacher"])
    assert not result.is_valid()
    assert list(LessonAvailability.objects.values()) == before


def test_future_window_can_be_corrected_at_its_start_and_retired_later(teacher_context):
    m = teacher_context
    assert save_availability(m["account"], m["profile"], edit_data(m), window(m).pk, teacher=m["teacher"]).is_valid()
    item = window(m)
    result = save_availability(m["account"], m["profile"], edit_data(m, start_time="10:00"), item.pk, teacher=m["teacher"])
    assert result.is_valid(), result.errors
    assert window(m).pk == item.pk
    result = save_availability(m["account"], m["profile"], edit_data(m, effective_date="2030-01-21", retire="on"), item.pk, teacher=m["teacher"])
    assert result.is_valid(), result.errors
    item.refresh_from_db()
    assert item.effective_until == date(2030, 1, 20)


@pytest.mark.parametrize("outcome", ["withdraw", "booked", "audit_failure"])
def test_never_effective_window_withdrawal_preserves_history_and_requires_audit(teacher_context, outcome):
    m = teacher_context
    if outcome == "booked":
        book(m)
    historical = window(m)
    result = save_availability(m["account"], m["profile"], edit_data(m), historical.pk, teacher=m["teacher"])
    assert result.is_valid(), result.errors
    item = window(m)
    historical.refresh_from_db()
    before = list(LessonAvailability.objects.order_by("pk").values())
    occurrences = list(LessonOccurrence.objects.order_by("pk").values())
    audit_count = AdminAuditLog.objects.count()
    data = edit_data(m, item, retire="on")
    if outcome == "audit_failure":
        with patch("scheduling.teacher_availability.audit", side_effect=RuntimeError), pytest.raises(RuntimeError):
            save_availability(m["account"], m["profile"], data, item.pk, teacher=m["teacher"])
    else:
        result = save_availability(m["account"], m["profile"], data, item.pk, teacher=m["teacher"])
        if outcome == "withdraw":
            assert result.is_valid(), result.errors
            assert not LessonAvailability.objects.filter(pk=item.pk).exists()
            log = AdminAuditLog.objects.get(model_label="scheduling.lessonavailability", object_id=str(item.pk), action="delete")
            assert log.changes["before"]["effective_from"] == "2030-01-14"
            assert log.changes["after"] is None
            assert AdminAuditLog.objects.count() == audit_count + 1
        else:
            assert not result.is_valid()
            assert "Existing lessons" in str(result.errors)
    if outcome != "withdraw":
        assert list(LessonAvailability.objects.order_by("pk").values()) == before
        assert AdminAuditLog.objects.count() == audit_count
    historical.refresh_from_db()
    assert historical.effective_from is None and historical.effective_until == date(2030, 1, 13)
    assert historical.start_time == time(0) and historical.end_time == time(23, 59)
    assert list(LessonOccurrence.objects.order_by("pk").values()) == occurrences


def test_overlapping_new_window_rejected_and_audit_failure_rolls_back_split(teacher_context):
    m = teacher_context
    data = edit_data(m)
    assert not save_availability(m["account"], m["profile"], data, teacher=m["teacher"]).is_valid()
    before = list(LessonAvailability.objects.values())
    with patch("scheduling.teacher_availability.audit", side_effect=RuntimeError), pytest.raises(RuntimeError):
        save_availability(m["account"], m["profile"], data, window(m).pk, teacher=m["teacher"])
    assert list(LessonAvailability.objects.values()) == before


def test_date_constraint_and_nullable_legacy_defaults(teacher_context):
    item = window(teacher_context)
    assert item.effective_from is None and item.effective_until is None
    with pytest.raises(IntegrityError), transaction.atomic():
        LessonAvailability.objects.filter(pk=item.pk).update(effective_from=date(2030, 1, 3), effective_until=date(2030, 1, 2))


def test_time_off_is_own_only_and_preserves_existing_lessons(teacher_context):
    m = teacher_context
    book(m)
    data = {"teacher": "", "location": m["location"].pk, "first_date": "2030-01-07",
        "last_date": "2030-01-07", "reason": "Away"}
    assert not save_time_off(m["account"], m["slug"], data).is_valid()
    assert not LessonClosure.objects.exists()
    result = save_time_off(m["account"], m["slug"], {**data, "first_date": "2030-01-08", "last_date": "2030-01-08"})
    assert result.is_valid(), result.errors
    assert result.instance.teacher_id == m["teacher"].pk


def test_time_off_never_unpublishes_visits(teacher_context):
    from scheduling.tests.test_visit_resources import visit_for_music
    m = teacher_context
    m["teacher"].booking_host = m["member"]
    m["teacher"].save()
    start = lessons.local_datetime(date(2030, 1, 8), time(10), ZoneInfo(m["location"].timezone_name))
    visit = visit_for_music(m, start)
    data = {"location": m["location"].pk, "first_date": "2030-01-08", "last_date": "2030-01-08", "reason": "Away"}
    assert not save_time_off(m["account"], m["slug"], data).is_valid()
    visit.session.refresh_from_db()
    assert visit.session.is_published
    assert not LessonClosure.objects.exists()


def test_public_slots_and_confirmation_respect_new_effective_hours(teacher_context, offer):
    from scheduling import appointments
    m = teacher_context
    session, token = prepare(m, offer)
    item = window(m)
    result = save_availability(m["account"], m["profile"], edit_data(m, effective_date="2030-01-07", start_time="11:00"), item.pk, teacher=m["teacher"])
    assert result.is_valid(), result.errors
    with pytest.raises(ValidationError):
        appointments.confirm_booking(m["slug"], session, token, CONTACT)
    for row in appointments.day_slots(offer, date(2030, 1, 7)):
        assert row["starts_at"].astimezone(ZoneInfo(offer.location.timezone_name)).time() >= time(11)


def test_teacher_pages_do_not_grant_staff_access_or_expose_family_contact(teacher_context, client):
    m = teacher_context
    book(m)
    client.force_login(m["account"])
    for name in ("schedule", "availability", "availability_new", "time_off"):
        response = client.get(route(m, name))
        assert response.status_code == 200
        assert "no-store" in response.headers["Cache-Control"]
        assert m["family"].email.encode() not in response.content
        assert b"Workspaces" not in response.content and b"Enrollment admin" not in response.content
        assert b"Sign out" in response.content
    for name in ("workspace", "lesson_setup", "lesson_bookings", "access"):
        assert client.get(reverse("scheduling:" + name, kwargs={"school_slug": m["slug"]})).status_code == 404
    other = LessonTeacher.objects.create(profile=m["profile"], name="Other teacher")
    other_window = LessonAvailability.objects.create(teacher=other, location=m["location"], weekday=0,
        start_time=time(9), end_time=time(12))
    assert client.get(route(m, "availability_edit", pk=other_window.pk)).status_code == 404
    assert client.post(route(m, "availability_edit", pk=other_window.pk), edit_data(m)).status_code == 404
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(m["account"])
    assert strict.post(route(m, "availability_new"), edit_data(m)).status_code == 403


def test_other_school_and_unbound_owner_cannot_enter_teacher_pages(teacher_context, client):
    m = teacher_context
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="other-teacher-school"),
        enabled=True, lessons_enabled=True, capabilities=["lessons"])
    with pytest.raises(Http404):
        teacher_access(m["account"], other.school.slug)
    client.force_login(m["user"])
    assert client.get(route(m, "schedule")).status_code == 404


def test_teacher_forms_save_and_report_validation_errors(teacher_context, client):
    m = teacher_context
    client.force_login(m["account"])
    endpoint = route(m, "availability_edit", pk=window(m).pk)
    assert client.post(endpoint, edit_data(m, end_time="08:00")).status_code == 400
    response = client.post(endpoint, edit_data(m))
    assert response.status_code == 302 and response.url == route(m, "availability")
    assert client.post(route(m, "time_off"), {}).status_code == 400
    response = client.post(route(m, "time_off"), {"location": m["location"].pk,
        "first_date": "2030-02-01", "last_date": "2030-02-01", "reason": "Away"})
    assert response.status_code == 302 and response.url == route(m, "availability")
    assert LessonClosure.objects.get().teacher == m["teacher"]
    expired = window(m)
    expired.effective_until = date(2029, 12, 31)
    expired.effective_from = None
    expired.save()
    response = client.get(route(m, "availability"))
    assert all(expired not in day["windows"] for day in response.context["days"])
    assert client.get(route(m, "schedule"), {"period": "past"}).context["period"] == "past"


def test_effective_date_defaults_to_tomorrow_at_location(teacher_context):
    m = teacher_context
    form = availability_form(m["profile"], teacher=m["teacher"])
    assert form.initial["effective_date"] == date(2030, 1, 2)
    assert form.initial["location"] == m["location"].pk
    item = window(m)
    item.effective_from = date(2030, 2, 1)
    item.save()
    form = availability_form(m["profile"], pk=item.pk, teacher=m["teacher"])
    assert form.initial["effective_date"] == date(2030, 2, 1)


def test_teacher_only_login_lands_without_membership_and_keeps_existing_priority(teacher_context, client):
    m = teacher_context
    response = client.post(reverse("login"), {"username": m["account"].username, "password": "teacher-password"})
    assert response.url == route(m, "schedule")
    assert client.get(reverse("login")).url == route(m, "schedule")
    assert client.get(response.url).status_code == 200
    SchedulingMembership.objects.create(profile=m["profile"], user=m["account"], role="viewer")
    assert client.get(reverse("login")).url == reverse("scheduling:index")
    SchoolAdminMembership.objects.create(school=m["profile"].school, user=m["account"], is_active=True, role="viewer")
    assert client.get(reverse("login")).url == reverse("school_dashboard", kwargs={"school_slug": m["slug"]})


@pytest.mark.parametrize("disabled", ["policy", "school", "teacher", "binding"])
def test_teacher_login_rejects_disabled_access(teacher_context, client, disabled):
    m = teacher_context
    if disabled == "policy":
        m["policy"]["teacher_self_service"] = False
    elif disabled == "school":
        School.objects.filter(pk=m["profile"].school_id).update(is_active=False)
    elif disabled == "teacher":
        LessonTeacher.objects.filter(pk=m["teacher"].pk).update(is_active=False)
    else:
        LessonTeacher.objects.filter(pk=m["teacher"].pk).update(login_user=None)
    response = client.post(reverse("login"), {"username": m["account"].username, "password": "teacher-password"})
    assert response.url == reverse("login")
    assert "_auth_user_id" not in client.session


def test_teacher_host_isolation_in_login_views_and_services(teacher_context, client, settings):
    from core.services.portal_environment import current_environment
    from scheduling import views_teacher
    m = teacher_context
    settings.APP_BASE_URL = "https://app.example.com"
    settings.DEMO_BASE_URL = "https://demo.example.com"
    settings.ALLOWED_HOSTS = ["app.example.com", "demo.example.com", "testserver"]
    School.objects.filter(pk=m["profile"].school_id).update(is_demo=True)
    credentials = {"username": m["account"].username, "password": "teacher-password"}
    assert client.post(reverse("login"), credentials, HTTP_HOST="app.example.com").url == reverse("login")
    assert "_auth_user_id" not in client.session
    assert client.post(reverse("login"), credentials, HTTP_HOST="demo.example.com").url == route(m, "schedule")
    assert client.post(route(m, "availability_new"), edit_data(m), HTTP_HOST="app.example.com").status_code == 403
    request = RequestFactory().get(route(m, "schedule"), HTTP_HOST="app.example.com")
    request.user = m["account"]
    with pytest.raises(Http404):
        views_teacher.schedule(request, m["slug"])
    token = current_environment.set("production")
    try:
        with pytest.raises(Http404):
            save_availability(m["account"], m["profile"], edit_data(m), window(m).pk, teacher=m["teacher"])
    finally:
        current_environment.reset(token)


@pytest.mark.django_db(transaction=True)
def test_availability_change_and_booking_serialize_on_same_lock(teacher_context, offer):
    from scheduling import appointments
    m = teacher_context
    session, token = prepare(m, offer)
    item = window(m)
    data = edit_data(m, effective_date="2030-01-07", start_time="11:00")

    def run(kind):
        close_old_connections()
        try:
            if kind == "book":
                appointments.confirm_booking(m["slug"], session, token, CONTACT)
                return True
            return save_availability(m["account"], m["profile"], data, item.pk, teacher=m["teacher"]).is_valid()
        except ValidationError:
            return False
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, ["book", "edit"])).count(True) == 1
