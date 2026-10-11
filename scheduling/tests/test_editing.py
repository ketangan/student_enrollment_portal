import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from types import SimpleNamespace

import pytest
from django.core import signing
from django.db import close_old_connections
from django.urls import reverse

from core.models import AdminAuditLog
from scheduling import camps, lessons, requests, setup
from scheduling.editing import EDIT_ERROR, EDIT_MAX_AGE, EDIT_SALT
from scheduling.lesson_models import LessonClosure, LessonService
from scheduling.models import ChoiceGroup, ChoiceOption, Offering
from .test_camps import camp
from .test_lessons import music
from .test_reservations import inventory
from .test_setup import catalog, session

pytestmark = pytest.mark.django_db
KINDS = ["locations", "rooms", "workshops", "sessions", "choice", "option", "camp",
         "services", "teachers", "households", "students", "availability", "closures"]


def form_data(form):
    return {field.name: field.value() if field.value() is not None else "" for field in form}


@pytest.fixture
def edit_case(request, booking_member):
    kind = request.param
    member = booking_member
    profile, user, slug = member.profile, member.user, member.profile.school.slug
    field, value = "name", "Updated settings"
    if kind in setup.FORMS:
        loc, room, offering = request.getfixturevalue("catalog")
        instance = {"locations": loc, "rooms": room, "workshops": offering}.get(kind)
        if kind == "sessions":
            instance = request.getfixturevalue("session")
            field, value = "capacity", 9
        form = lambda: setup.build_form(profile, kind, pk=instance.pk)
        save = lambda data: setup.save_setup(user, slug, kind, data, pk=instance.pk)
        route = {"locations": "location_edit", "rooms": "room_edit", "workshops": "workshop_edit", "sessions": "session_edit"}[kind]
        kwargs = {"pk": instance.pk}
    elif kind in ("choice", "option"):
        offering = request.getfixturevalue("catalog")[2]
        group = ChoiceGroup.objects.create(offering=offering, name="Design")
        instance = group
        kwargs = {"offering_pk": offering.pk, "group_pk": group.pk}
        args = {"group_pk": group.pk}
        if kind == "option":
            instance = ChoiceOption.objects.create(group=group, label="Blue")
            kwargs["option_pk"] = instance.pk
            args.update(option=True, option_pk=instance.pk)
            field = "label"
        form = lambda: requests.choice_form(profile, offering.pk, **args)[1]
        save = lambda data: requests.save_choice(user, slug, offering.pk, data, **args)[1]
        route = f"{kind}_edit"
    elif kind == "camp":
        instance = request.getfixturevalue("camp")
        form = lambda: camps.camp_form(profile, instance.offering_id, instance.pk)
        save = lambda data: camps.save_camp(user, slug, instance.offering_id, data, instance.pk)
        route, kwargs = "camp_edit", {"offering_pk": instance.offering_id, "pk": instance.pk}
    else:
        m = request.getfixturevalue("music")
        instance = {"services": m["service"], "teachers": m["teacher"], "households": m["family"],
                    "students": m["student"], "availability": m["teacher"].availability.first()}.get(kind)
        if kind == "availability":
            field, value = "start_time", "01:00"
        if kind == "closures":
            instance = LessonClosure.objects.create(location=m["location"], first_date=date(2030, 2, 1),
                                                   last_date=date(2030, 2, 1), reason="Closed")
            field, value = "reason", "Updated closure"
        form = lambda: lessons.setup_form(profile, kind, pk=instance.pk)
        save = lambda data: lessons.save_lesson_setup(user, slug, kind, data, pk=instance.pk)
        route, kwargs = "lesson_setup_edit", {"kind": kind, "pk": instance.pk}
    return SimpleNamespace(form=form, save=save, instance=instance, field=field, value=value,
                           url=reverse("scheduling:" + route, kwargs={"school_slug": slug, **kwargs}))


@pytest.mark.parametrize("edit_case", KINDS, indirect=True)
def test_two_open_forms_cannot_overwrite_newer_save_and_reload_recovers(edit_case, client, booking_member):
    case = edit_case
    client.force_login(booking_member.user)
    first = client.get(case.url)
    assert first.status_code == 200
    assert b'type="hidden" name="edit_revision"' in first.content
    assert b'for="id_edit_revision"' not in first.content
    data = form_data(first.context["form"])
    second = client.get(case.url)
    stale_data = form_data(second.context["form"])
    before = AdminAuditLog.objects.count()
    response = client.post(case.url, {**data, case.field: case.value})
    assert response.status_code == 302
    case.instance.refresh_from_db()
    saved_value = getattr(case.instance, case.field)
    response = client.post(case.url, stale_data)
    assert response.status_code in (200, 400)
    assert EDIT_ERROR.encode() in response.content
    assert f'href="{case.url}">Reload saved settings</a>'.encode() in response.content
    assert b'role="alert"' in response.content
    assert response.context["form"]["edit_revision"].value() == stale_data["edit_revision"]
    case.instance.refresh_from_db()
    assert getattr(case.instance, case.field) == saved_value
    assert AdminAuditLog.objects.count() == before + 1
    assert "edit_revision" not in str(AdminAuditLog.objects.order_by("pk").last().changes)
    fresh_data = form_data(client.get(case.url).context["form"])
    fresh_data[case.field] = stale_data[case.field]
    assert client.post(case.url, fresh_data).status_code == 302
    assert AdminAuditLog.objects.count() == before + 2


@pytest.mark.parametrize("edit_case", ["workshops"], indirect=True)
@pytest.mark.parametrize("invalid", ["missing", "tampered", "expired", "oversized", "wrong_record", "wrong_tenant", "wrong_model"])
def test_invalid_revision_never_writes(edit_case, invalid, monkeypatch):
    data = form_data(edit_case.form())
    if invalid == "missing":
        data.pop("edit_revision")
    elif invalid == "tampered":
        data["edit_revision"] += "bad"
    elif invalid == "expired":
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now + EDIT_MAX_AGE + 1)
    elif invalid == "oversized":
        data["edit_revision"] = "x" * 2049
    else:
        state = signing.loads(data["edit_revision"], salt=EDIT_SALT)
        state[{"wrong_record": "pk", "wrong_tenant": "profile", "wrong_model": "model"}[invalid]] = "other"
        data["edit_revision"] = signing.dumps(state, salt=EDIT_SALT)
    data[edit_case.field] = edit_case.value
    form = edit_case.save(data)
    assert not form.is_valid() and "edit_revision" in form.errors
    edit_case.instance.refresh_from_db()
    assert getattr(edit_case.instance, edit_case.field) != edit_case.value
    assert not AdminAuditLog.objects.exists()


@pytest.mark.parametrize("edit_case", ["teachers", "camp"], indirect=True)
def test_many_to_many_changes_invalidate_old_form(edit_case, booking_profile):
    data = form_data(edit_case.form())
    instance = edit_case.instance
    if isinstance(instance, camps.Camp):
        relation, field = instance.sessions, "sessions"
        removed = relation.last()
        relation.remove(removed)
    else:
        relation, field = instance.services, "services"
        extra = LessonService.objects.create(profile=booking_profile, name="Guitar", subject="Guitar", duration_minutes=30)
        relation.add(extra)
    expected = set(relation.values_list("pk", flat=True))
    form = edit_case.save({**data, "name": "Stale name"})
    assert "edit_revision" in form.errors
    assert set(relation.values_list("pk", flat=True)) == expected
    fresh = form_data(edit_case.form())
    assert set(map(int, fresh[field])) == expected
    assert edit_case.save(fresh).is_valid()


@pytest.mark.parametrize("edit_case", ["workshops"], indirect=True)
def test_invalid_fields_can_be_corrected_without_reloading(edit_case):
    data = form_data(edit_case.form())
    rejected = edit_case.save({**data, "name": ""})
    assert "name" in rejected.errors and "edit_revision" not in rejected.errors
    assert rejected["edit_revision"].value() == data["edit_revision"]
    assert edit_case.save({**data, "name": "Corrected"}).is_valid()


@pytest.mark.parametrize("edit_case", ["workshops"], indirect=True)
def test_publication_change_also_invalidates_open_form(edit_case, booking_member, session):
    data = form_data(edit_case.form())
    setup.set_publication(booking_member.user, booking_member.profile.school.slug, "sessions", session.pk, True)
    setup.set_publication(booking_member.user, booking_member.profile.school.slug, "workshops", edit_case.instance.pk, True)
    assert "edit_revision" in edit_case.save(data).errors


@pytest.mark.django_db(transaction=True)
def test_concurrent_setup_saves_have_one_winner(booking_member, catalog):
    profile, offering = booking_member.profile, catalog[2]
    data = form_data(setup.build_form(profile, "workshops", pk=offering.pk))
    barrier = Barrier(2)

    def write(name):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            form = setup.save_setup(booking_member.user, profile.school.slug, "workshops",
                                    {**data, "name": name}, pk=offering.pk)
            return form.is_valid(), form.errors
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["First edit", "Second edit"]))
    assert sorted(valid for valid, _ in results) == [False, True]
    assert "edit_revision" in next(errors for valid, errors in results if not valid)
    assert AdminAuditLog.objects.count() == 1
    assert Offering.objects.get(pk=offering.pk).name in ("First edit", "Second edit")
