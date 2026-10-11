from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from core.models import School
from scheduling import lessons
from scheduling.lesson_models import LessonClosure, LessonSeries
from scheduling.models import SchedulingProfile
from scheduling.tests.test_lessons import music, book, preview, url

pytestmark = pytest.mark.django_db


def test_setup_and_schedule_browser_flow(client,music):
    m=music;client.force_login(m["user"])
    for name in ["lessons","lesson_setup","lesson_settings","lesson_series_new"]:
        response=client.get(url(m,name));assert response.status_code==200
        assert "no-store" in response.headers["Cache-Control"]
    for kind in ["services","teachers","households","students","availability","closures"]:
        assert client.get(url(m,"lesson_setup_new",kind=kind)).status_code==200
    create=url(m,"lesson_setup_new",kind="services")
    assert client.post(create,{}).status_code==400
    response=client.post(create,dict(name="Guitar",subject="Guitar",duration_minutes=45,is_active="on"))
    assert response.status_code==302
    form=lessons.setup_form(m["profile"],"services",pk=m["service"].pk)
    assert client.get(url(m,"lesson_setup_edit",kind="services",pk=m["service"].pk)).status_code==200
    assert client.post(url(m,"lesson_setup_edit",kind="services",pk=m["service"].pk),dict(name="Piano",subject="Piano",duration_minutes=30,is_active="on",edit_revision=form["edit_revision"].value())).status_code==302
    target=url(m,"lesson_series_new")
    assert client.post(target,{"action":"preview"}).status_code==400
    response=client.post(target,{**m["data"],"action":"preview"})
    assert response.status_code==200 and response.context["can_confirm"]
    token=response.context["token"]
    assert not LessonSeries.objects.exists()
    confirm={**m["data"],"action":"confirm","preview_token":token}
    response=client.post(target,confirm)
    assert response.status_code==302
    assert client.post(target,confirm).url==response.url
    detail=client.get(response.url)
    assert b"No charges created" in detail.content and b"Riley" in detail.content
    item=LessonSeries.objects.get()
    assert client.get(url(m,"lessons")+f'?family={m["family"].pk}').status_code==200
    assert client.get(url(m,"lessons")+'?family=unknown').status_code==404
    assert client.get(url(m,"lesson_setup_new",kind="bad")).status_code==404
    cancel=url(m,"lesson_cancel",pk=item.occurrences.first().pk)
    assert client.get(cancel).status_code==200
    assert client.post(cancel,{}).status_code==400
    data=dict(scope="one",origin="family",reason="Family request",confirm="on")
    response=client.post(cancel,data)
    assert response.status_code==200 and b"No credit" in response.content
    assert client.post(cancel,{**data,"action":"confirm","preview_token":response.context["token"]}).status_code==302
    assert item.occurrences.filter(state="canceled").count()==1
    public=client.get(url(m,"public_schedule"))
    assert b"Riley" not in public.content and b"Demo family" not in public.content


def test_settings_enable_disable_and_existing_claim_guard(client,music):
    m=music;client.force_login(m["user"])
    target=url(m,"lesson_settings")
    assert client.post(target,{}).status_code==302
    m["profile"].refresh_from_db();assert not m["profile"].lessons_enabled
    assert client.get(url(m,"lessons")).status_code==404
    assert b'>Lessons</a>' not in client.get(url(m,"workspace")).content
    assert client.get(target).status_code==200
    assert client.post(target,{"enabled":"on"}).status_code==302
    book(m)
    response=client.post(target,{})
    assert response.status_code==400 and b"Cancel future lessons" in response.content
    m["profile"].refresh_from_db();assert m["profile"].lessons_enabled


@pytest.mark.parametrize("role,setup,write",[("owner",200,200),("scheduler",404,200),("viewer",404,404)])
def test_role_permissions(client,music,role,setup,write):
    m=music;item=book(m);m["member"].role=role;m["member"].save();client.force_login(m["user"])
    assert client.get(url(m,"lessons")).status_code==200
    assert client.get(url(m,"lesson_setup_new",kind="teachers")).status_code==setup
    assert client.get(url(m,"lesson_series_new")).status_code==write
    assert client.get(url(m,"lesson_cancel",pk=item.occurrences.first().pk)).status_code==write
    assert client.get(url(m,"lesson_settings")).status_code==setup
    if role=="viewer":
        assert b"Schedule lessons" not in client.get(url(m,"lessons")).content
        assert client.post(url(m,"lesson_cancel",pk=item.occurrences.first().pk),dict(scope="one",reason="bad",confirm="on")).status_code==404


def test_access_revocation_wrong_tenant_and_csrf(client,music):
    m=music;item=book(m);_,token=preview(m,{**m["data"],"start_time":"14:00"})
    strict=Client(enforce_csrf_checks=True);strict.force_login(m["user"])
    assert strict.post(url(m,"lesson_settings"),{"enabled":"on"}).status_code==403
    assert strict.post(url(m,"lesson_series_new"),m["data"]).status_code==403
    assert client.get(url(m,"lessons")).status_code==302
    client.force_login(m["user"])
    other=School.objects.create(slug="hidden-studio")
    SchedulingProfile.objects.create(school=other,enabled=True,lessons_enabled=True)
    assert client.get('/bookings/hidden-studio/lessons/').status_code==404
    assert client.get(url(m,"lesson_series",public_id=item.public_id)).status_code==200
    m["member"].is_active=False;m["member"].save()
    assert client.post(url(m,"lesson_series_new"),{**m["data"],"start_time":"14:00","preview_token":token,"action":"confirm"}).status_code==404


def test_confirmation_and_cancel_errors_are_visible(client,music):
    m=music;client.force_login(m["user"])
    target=url(m,"lesson_series_new")
    assert client.post(target,{**m["data"],"action":"confirm","preview_token":"bad"}).status_code==400
    with patch("scheduling.views_lessons.plan_series",side_effect=ValidationError("Unavailable")):
        assert b"Unavailable" in client.post(target,m["data"]).content
    item=book(m)
    response=client.post(target,m["data"])
    assert response.status_code==200 and not response.context["can_confirm"] and b'Confirm schedule' not in response.content
    with patch("scheduling.views_lessons.cancel_lessons",side_effect=ValidationError("Past lesson")):
        response=client.post(url(m,"lesson_cancel",pk=item.occurrences.first().pk),dict(scope="one",origin="family",reason="test",confirm="on",action="confirm"))
        assert response.status_code==400 and b"Past lesson" in response.content


def test_empty_closure_and_pagination_views(client,music):
    m=music;client.force_login(m["user"])
    assert b"No lessons scheduled" in client.get(url(m,"lessons")).content
    LessonClosure.objects.create(location=m["location"],first_date=date(2030,1,1),last_date=date(2030,3,1),reason="Closed")
    response=client.post(url(m,"lesson_series_new"),m["data"])
    assert not response.context["can_confirm"] and b"Closed" in response.content
    response=client.get(url(m,"lessons")+'?page=bad')
    assert response.status_code==200


def test_nonlesson_workspaces_do_not_gain_navigation_or_access(client,music):
    m=music;m["profile"].lessons_enabled=False;m["profile"].save();client.force_login(m["user"])
    for name in ["lessons","lesson_setup","lesson_series_new"]:
        assert client.get(url(m,name)).status_code==404
    assert m["profile"].school.plan=="trial"
