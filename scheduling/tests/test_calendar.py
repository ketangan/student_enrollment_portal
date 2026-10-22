from datetime import timedelta
from datetime import datetime, timezone
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.http import Http404, QueryDict
from django.urls import reverse

from scheduling.calendar import calendar_detail, calendar_events, calendar_summary, date_window
from scheduling.calendar_settings import calendar_form, save_calendar
from scheduling.models import Session, Location, Offering, SchedulingProfile, SchedulingMembership, PartyRequest
from scheduling import reservations
from core.models import School
from scheduling.tests.test_inventory import stock, reserve, confirm, url

pytestmark = pytest.mark.django_db


def params(s, **changes):
    data = QueryDict(mutable=True)
    data.update({"start": (s.session.starts_at.date()-timedelta(days=1)).isoformat(),
        "end": (s.session.ends_at.date()+timedelta(days=1)).isoformat(), **changes})
    return data


def enable(s):
    s.profile.calendar_enabled = True
    s.profile.save(update_fields=["calendar_enabled"])


def test_disabled_and_authorized_calendar(stock):
    s=stock
    path=url(s,"calendar_events")
    assert s.client.get(path,params(s)).status_code == 404
    assert s.client.get(url(s,"workspace"),{"view":"calendar"}).status_code == 404
    enable(s)
    response=s.client.get(path,params(s))
    assert response.status_code == 200
    assert response.json()["events"][0]["extendedProps"]["seats"] == 0
    assert s.client.get(url(s,"workspace"),{"view":"calendar"}).status_code == 200
    s.client.logout()
    assert s.client.get(path,params(s)).status_code == 302


def test_counts_holds_expiry_and_roster(stock):
    s=stock
    first=reserve(s,2)
    assert calendar_events(s.profile,params(s))[0]["extendedProps"]["seats"] == 2
    first.hold_until -= timedelta(days=1);first.save(update_fields=["hold_until"])
    assert calendar_events(s.profile,params(s))[0]["extendedProps"]["seats"] == 0
    second=reserve(s,1);confirm(s,second)
    result=calendar_detail(s.profile,"session",str(s.session.pk))
    assert len(result["reservations"]) == 1
    assert str(second.public_id) in result["reservations"][0]["url"]
    assert result["reservations"][0]["received"] == 100
    assert "Parent" not in str(calendar_events(s.profile,params(s)))


def test_filters_and_invalid_ranges(stock):
    s=stock
    assert calendar_events(s.profile,params(s,hide_empty="1")) == []
    with pytest.raises(ValidationError):calendar_events(s.profile,params(s,offering="999999"))
    with pytest.raises(ValidationError):calendar_events(s.profile,params(s,offering="bad"))
    for data in ({},{"start":"bad","end":"bad"},{"start":"2026-01-01","end":"2026-03-01"},
                 {"start":"2026-01-01","end":"2026-01-01"}):
        with pytest.raises(ValidationError):date_window(data)
    with pytest.raises(Http404):calendar_detail(s.profile,"session","bad")
    with pytest.raises(Http404):calendar_detail(s.profile,"other","1")


def test_operator_settings_and_stale_edit(stock):
    s=stock
    admin=get_user_model().objects.create_superuser("calendar-admin",password="test")
    data={"calendar_enabled":"on","default_dashboard_view":"calendar","edit_revision":calendar_form(s.profile)["edit_revision"].value()}
    with pytest.raises(Http404):save_calendar(s.user,s.school.slug,data)
    form=save_calendar(admin,s.school.slug,data)
    assert form.is_valid(),form.errors
    assert not save_calendar(admin,s.school.slug,data).is_valid()
    s.profile.refresh_from_db()
    assert s.profile.calendar_enabled and s.profile.default_dashboard_view=="calendar"
    assert s.client.get(url(s,"workspace")).context["calendar_config"]
    assert s.client.get(url(s,"workspace"),{"view":"overview"}).status_code==200


def test_query_count_bounded(stock,django_assert_max_num_queries):
    s=stock
    Session.objects.bulk_create([Session(offering=s.offering,starts_at=s.session.starts_at,
        ends_at=s.session.ends_at,capacity=10) for _ in range(40)])
    with django_assert_max_num_queries(6):
        assert len(calendar_events(s.profile,params(s))) == 41


def test_tenant_and_capability_guards(stock):
    s=stock;enable(s)
    other=SchedulingProfile.objects.create(school=School.objects.create(slug="other-calendar"),enabled=True,calendar_enabled=True,capabilities=["workshops"])
    loc=Location.objects.create(profile=other,name="Other")
    offering=Offering.objects.create(location=loc,name="Private")
    session=Session.objects.create(offering=offering,starts_at=s.session.starts_at,ends_at=s.session.ends_at,capacity=10)
    assert s.client.get(reverse("scheduling:calendar_events",kwargs={"school_slug":other.school.slug}),params(s)).status_code==404
    assert s.client.get(url(s,"calendar_detail",kind="session",pk=session.pk)).status_code==404
    assert s.client.get(url(s,"calendar_events"),params(s,location=loc.pk)).status_code==400
    assert s.client.get(url(s,"calendar_events"),params(s,offering=offering.pk)).status_code==400
    s.profile.capabilities=[];s.profile.save(update_fields=["capabilities"])
    assert s.client.get(url(s,"calendar_events"),params(s)).status_code==404


@pytest.mark.parametrize("role",["viewer","scheduler","owner"])
def test_workspace_roles_can_read_not_configure(stock,role):
    s=stock;enable(s)
    SchedulingMembership.objects.filter(profile=s.profile,user=s.user).update(role=role)
    assert s.client.get(url(s,"calendar_events"),params(s)).status_code==200
    with pytest.raises(Http404):save_calendar(s.user,s.school.slug,{})
    SchedulingMembership.objects.filter(profile=s.profile,user=s.user).update(is_active=False)
    assert s.client.get(url(s,"calendar_events"),params(s)).status_code==404


def test_location_local_dst_and_midnight(stock):
    s=stock
    s.location.timezone_name="America/New_York";s.location.save(update_fields=["timezone_name"])
    s.session.starts_at=datetime(2026,11,1,5,30,tzinfo=timezone.utc)
    s.session.ends_at=datetime(2026,11,1,7,30,tzinfo=timezone.utc)
    s.session.save(update_fields=["starts_at","ends_at"])
    data=calendar_events(s.profile,params(s))[0]
    assert data["start"]=="2026-11-01T01:30:00" and data["end"]=="2026-11-01T02:30:00"
    detail=calendar_detail(s.profile,"session",s.session.pk)
    assert "EDT" in detail["time"] and "EST" in detail["time"]
    s.session.starts_at=datetime(2026,11,2,4,30,tzinfo=timezone.utc)
    s.session.ends_at=datetime(2026,11,2,6,30,tzinfo=timezone.utc)
    s.session.save(update_fields=["starts_at","ends_at"])
    assert len(calendar_events(s.profile,params(s,start="2026-11-01",end="2026-11-02")))==1
    assert len(calendar_events(s.profile,params(s,start="2026-11-02",end="2026-11-03")))==1
    assert not calendar_events(s.profile,params(s,start="2026-11-03",end="2026-11-04"))


def test_party_inquiry_is_not_an_event_until_reserved(stock):
    s=stock
    party=PartyRequest.objects.create(offering=s.offering,organizer_name="Host",organizer_email="host@example.invalid",
        starts_at=s.session.starts_at+timedelta(hours=3),ends_at=s.session.ends_at+timedelta(hours=3),room=s.room,
        group_size=1,space="shared",status="reviewed",proposed_total=100,proposed_deposit=0,
        materials=[{"item_id":s.item.pk,"name":s.item.name,"quantity":1}],
        offering_snapshot={"workshop":"Party","timezone":"UTC"},submission_key=uuid.uuid4(),submission_digest="d",browser_digest="b")
    assert len(calendar_events(s.profile,params(s)))==1
    item=reservations.create_reservation(s.user,s.school.slug,"party",party.public_id)
    events=calendar_events(s.profile,params(s))
    assert len(events)==2 and events[-1]["extendedProps"]["kind"]=="party"
    detail=calendar_detail(s.profile,"party",str(item.pk))
    assert str(item.public_id) in detail["url"] and detail["reservations"][0]["name"]=="Host"
    item.state="canceled";item.save(update_fields=["state"])
    assert len(calendar_events(s.profile,params(s)))==1


def test_limit_and_invalid_operator_configuration(stock,monkeypatch):
    s=stock
    monkeypatch.setattr("scheduling.calendar.MAX_EVENTS",0)
    with pytest.raises(ValidationError,match="Too many"):calendar_events(s.profile,params(s))
    form=calendar_form(s.profile,{"default_dashboard_view":"calendar","edit_revision":calendar_form(s.profile)["edit_revision"].value()})
    assert not form.is_valid() and "default_dashboard_view" in form.errors
    s.profile.capabilities=[]
    form=calendar_form(s.profile,{"calendar_enabled":"on","default_dashboard_view":"overview","edit_revision":calendar_form(s.profile)["edit_revision"].value()})
    assert not form.is_valid() and "calendar_enabled" in form.errors


def test_summary_and_operator_page(stock):
    s=stock;enable(s)
    item=reserve(s)
    assert calendar_summary(s.profile,calendar_events(s.profile,params(s)))["unpaid"]==1
    confirm(s,item)
    assert calendar_summary(s.profile,calendar_events(s.profile,params(s)))["unpaid"]==0
    admin=get_user_model().objects.create_superuser("calendar-operator",password="test")
    s.client.force_login(admin)
    path=reverse("ops_school_detail",kwargs={"slug":s.school.slug})
    response=s.client.get(path)
    assert response.status_code==200 and b"Booking Dashboard" in response.content
    form=response.context["booking_calendar"]["form"]
    response=s.client.post(path,{"action":"save_booking_calendar","calendar_enabled":"on","default_dashboard_view":"calendar", "edit_revision":form["edit_revision"].value()})
    assert response.status_code==302
    s.profile.refresh_from_db()
    assert s.profile.default_dashboard_view=="calendar"
    s.school.refresh_from_db()
    assert s.school.plan=="trial"


def test_camp_capacity_is_not_duplicated(stock):
    from scheduling.models import Camp
    from scheduling.tests.test_camps import register
    s=stock
    camp=Camp.objects.create(offering=s.offering,name="Two day camp",price=10,deposit=0,is_open=True)
    second=Session.objects.create(offering=s.offering,room=s.room,starts_at=s.session.starts_at+timedelta(days=1),
        ends_at=s.session.ends_at+timedelta(days=1),capacity=12,is_published=True)
    camp.sessions.set([s.session,second])
    registration=register(camp,count=1)
    result=calendar_events(s.profile,params(s,end=(second.ends_at.date()+timedelta(days=1)).isoformat()))
    assert len(result)==2 and all(event["extendedProps"]["seats"]==1 for event in result)
    assert calendar_summary(s.profile,result)["unpaid"]==1
    assert len(calendar_detail(s.profile,"session",s.session.pk)["reservations"])==1


def test_calendar_host_isolation(stock,settings):
    s=stock;enable(s)
    s.school.is_demo=True;s.school.save(update_fields=["is_demo"])
    settings.APP_BASE_URL="https://app.example.com"
    settings.DEMO_BASE_URL="https://demo.example.com"
    settings.ALLOWED_HOSTS=["app.example.com","demo.example.com","testserver"]
    path=url(s,"calendar_events")
    response=s.client.get(path,params(s),HTTP_HOST="app.example.com")
    assert response.status_code==302 and response.url.startswith("https://demo.example.com/")
    assert s.client.get(path,params(s),HTTP_HOST="demo.example.com").status_code==200
    assert s.client.post(path,{},HTTP_HOST="app.example.com").status_code==403


def test_calendar_without_reservations_and_missing_profile(stock):
    from django.test import RequestFactory
    from scheduling.calendar_settings import settings_context
    s=stock
    s.profile.capabilities=["workshops"]
    s.profile.save(update_fields=["capabilities"])
    events=calendar_events(s.profile,params(s,offering=str(s.offering.pk)),s.location)
    assert calendar_summary(s.profile,events)=={"unpaid":0,"balance":0,"empty":1}
    assert calendar_detail(s.profile,"session",s.session.pk)["reservations"]==[]
    with pytest.raises(Http404):calendar_detail(s.profile,"party","999999")
    school=School.objects.create(slug="no-booking-profile")
    factory=RequestFactory()
    assert settings_context(factory.get("/"),school) is None
    with pytest.raises(Http404):settings_context(factory.post("/",{"action":"save_booking_calendar"}),school)
