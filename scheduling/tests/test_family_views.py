from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, override_settings
from django.utils import timezone

from scheduling.family_access import SESSION_KEY
from scheduling.lesson_models import LessonHousehold, LessonStudent
from scheduling.tests.test_family_access import issue, invitation
from scheduling.tests.test_credits import issued, redeem
from scheduling.tests.test_lessons import music, book, url

pytestmark = pytest.mark.django_db


def family_client(m):
    grant,token=issue(m)
    client=Client()
    response=client.post(url(m,"family_entry"),{"access_token":token})
    assert response.status_code==302
    return client,grant,token


def private(response):
    assert "no-store" in response["Cache-Control"]
    assert response["Referrer-Policy"]=="same-origin"
    assert response["X-Robots-Tag"]=="noindex, nofollow, noarchive"
    assert "frame-ancestors 'none'" in response["Content-Security-Policy"]


def test_owner_create_preview_replay_and_revoke_ui(music):
    m=music; client=Client(); client.force_login(m["user"])
    book(m)
    assert b"Family access" in client.get(url(m,"lessons")).content
    private(client.get(url(m,"family_access")))
    path=url(m,"family_invite",pk=m["family"].pk)
    response=client.get(path)
    assert m["family"].email.encode() in response.content
    data=invitation(m)
    response=client.post(path,data); private(response)
    assert response.status_code==200
    link=response.context["invite_url"]
    assert link.startswith("http://testserver/bookings/") and "/family/access/#" in link
    token=link.split("#")[1]; grant=response.context["grant"]
    response=client.get(url(m,"family_preview",public_id=grant.public_id)); private(response)
    assert b"Staff preview" in response.content and b"Piano room" in response.content
    assert b"Close private access" not in response.content
    grant.refresh_from_db(); assert grant.redeemed_at is None and SESSION_KEY not in client.session
    response=client.post(path,data)
    assert response.context["invite_url"]=="" and token.encode() not in response.content
    assert b"already created" in response.content
    response=client.post(path,{**invitation(m),"invite_key":"bad"})
    assert response.status_code==400
    assert client.post(path,{}).status_code==400
    path=url(m,"family_revoke",public_id=grant.public_id)
    assert client.get(path).status_code==200
    assert client.post(path,{}).status_code==400
    assert client.post(path,{"reason":"Authorization ended","confirm":"on"}).status_code==302
    grant.refresh_from_db(); assert grant.revoked_at
    assert b"Revoked" in client.get(url(m,"family_access")).content


def test_family_schedule_filters_no_staff_notes_or_other_children(music):
    m=music; series=book(m)
    series.occurrences.update(reason="PRIVATE REASON",attendance_note="PRIVATE STAFF NOTE")
    sibling=LessonStudent.objects.create(household=m["family"],name="Hidden sibling")
    book(m,{**m["data"],"student":str(sibling.pk),"start_time":"12:00"})
    client,grant,token=family_client(m)
    response=client.get(url(m,"family_schedule")); private(response)
    html=response.content.decode()
    assert "Riley" in html and "Piano room" in html and "10:00 AM" in html
    assert all(value not in html for value in ["Hidden sibling","PRIVATE REASON","PRIVATE STAFF NOTE",token,"/admin/","/lessons/new/","Billing"])
    assert client.get(url(m,"family_schedule")+f'?student={m["student"].pk}').status_code==200
    for value in [str(sibling.pk),"9999999999999999999999999999","bad"]:
        response=client.get(url(m,"family_schedule")+f"?student={value}")
        assert response.status_code==404; private(response)
    assert b"No lessons in this view" in client.get(url(m,"family_schedule")+"?period=past").content
    for i,item in enumerate(series.occurrences.all()):
        item.ends_at=timezone.now()-timedelta(days=i+1)
        item.starts_at=item.ends_at-timedelta(minutes=30)
        item.attendance="present"
        item.save()
    response=client.get(url(m,"family_schedule")+"?period=past")
    assert b"Present" in response.content and b"PRIVATE STAFF NOTE" not in response.content
    assert response.context["items"].paginator.count==3
    assert b"No lessons in this view" in client.get(url(m,"family_schedule")).content


def test_family_credits_and_current_makeup_read_only(music):
    m=music; credit=issued(m)
    series=redeem(m,credit)
    client,_,_=family_client(m)
    response=client.get(url(m,"family_schedule")); private(response)
    assert b"Make-up credits" in response.content and b"Booked" in response.content
    assert response.context["credits"].paginator.count==1
    assert b"Book using credit" not in response.content and b"/revoke/" not in response.content
    assert response.context["credits"][0].used_occurrence.series_id==series.pk
    assert client.get(url(m,"lesson_credit",public_id=credit.public_id)).status_code==302
    assert client.post(url(m,"family_schedule"),{"action":"book"}).status_code==405


def test_entry_invalid_used_missing_links_and_logout(music):
    m=music
    client=Client()
    response=client.get(url(m,"family_entry")); private(response)
    assert b'id="open-schedule" disabled' in response.content
    assert client.get(url(m,"family_schedule")).status_code==404
    response=client.post(url(m,"family_entry"),{"access_token":"x"*43}); private(response)
    assert response.status_code==400 and b"x"*43 not in response.content
    client,grant,token=family_client(m)
    response=Client().post(url(m,"family_entry"),{"access_token":token})
    assert response.status_code==400 and token.encode() not in response.content
    assert client.get(url(m,"family_sign_out")).status_code==405
    response=client.post(url(m,"family_sign_out")); private(response)
    assert b"Private access closed" in response.content
    assert client.get(url(m,"family_schedule")).status_code==404
    grant.refresh_from_db(); assert grant.revoked_at


@pytest.mark.parametrize("role",["viewer","scheduler","inactive","anonymous"])
def test_no_accidental_staff_family_authority(music,role):
    m=music; grant,_=issue(m)
    client=Client()
    if role!="anonymous":
        if role=="inactive": m["member"].is_active=False
        else: m["member"].role=role
        m["member"].save(); client.force_login(m["user"])
    status=302 if role=="anonymous" else 404
    for name,kw in [("family_access",{}),("family_invite",{"pk":m["family"].pk}),
        ("family_preview",{"public_id":grant.public_id}),("family_revoke",{"public_id":grant.public_id})]:
        assert client.get(url(m,name,**kw)).status_code==status
    assert client.get(url(m,"family_schedule")).status_code==404


def test_same_email_user_is_not_guardian_and_owner_needs_invite(music):
    m=music
    user=get_user_model().objects.create_user("not-verified",email=m["family"].email,password="x")
    for actor in [user,m["user"]]:
        client=Client(); client.force_login(actor)
        assert client.get(url(m,"family_schedule")).status_code==404
    other=LessonHousehold.objects.create(profile=m["profile"],name="Other family",email=m["family"].email)
    child=LessonStudent.objects.create(household=other,name="Other private child")
    book(m,{**m["data"],"student":str(child.pk),"start_time":"12:00"})
    client,_,_=family_client(m)
    assert b"Other private child" not in client.get(url(m,"family_schedule")).content


def test_disabled_workspace_errors_and_csrf(music):
    m=music
    grant,token=issue(m)
    secure=Client(enforce_csrf_checks=True)
    assert secure.post(url(m,"family_entry"),{"access_token":token}).status_code==403
    assert secure.post(url(m,"family_sign_out")).status_code==403
    secure.force_login(m["user"])
    assert secure.post(url(m,"family_invite",pk=m["family"].pk),invitation(m)).status_code==403
    assert secure.post(url(m,"family_revoke",public_id=grant.public_id),{"reason":"r","confirm":"on"}).status_code==403
    m["profile"].enabled=False; m["profile"].save()
    for name in ["family_entry","family_schedule"]:
        response=secure.get(url(m,name)); assert response.status_code==404; private(response)


def test_https_same_origin_csrf_post_and_persistent_family_session(music):
    m=music; grant,token=issue(m)
    client=Client(enforce_csrf_checks=True)
    path=url(m,"family_entry")
    response=client.get(path,secure=True)
    private(response)
    data={"access_token":token,"csrfmiddlewaretoken":client.cookies["csrftoken"].value}
    assert client.post(path,data,secure=True,HTTP_ORIGIN="null").status_code==403
    assert client.post(path,data,secure=True,HTTP_ORIGIN="https://untrusted.invalid").status_code==403
    assert client.post(path,data,secure=True,HTTP_ORIGIN="https://testserver").status_code==302
    new_client=Client(); new_client.cookies=client.cookies
    assert new_client.get(url(m,"family_schedule")).status_code==200


@override_settings(RATELIMIT_ENABLE=True)
@pytest.mark.usefixtures("rate_limit_clock")
def test_exchange_rate_limited(music):
    cache.clear()
    client=Client()
    for _ in range(10):
        assert client.post(url(music,"family_entry"),{"access_token":"bad"}).status_code==400
    assert client.post(url(music,"family_entry"),{"access_token":"bad"}).status_code==403
    cache.clear()


def test_status_labels_and_empty_or_expired_grants(music,monkeypatch):
    m=music; client=Client(); client.force_login(m["user"])
    assert b"No family access granted" in client.get(url(m,"family_access")).content
    family,grant,token=family_client(m)
    grant.students.clear()
    assert family.get(url(m,"family_schedule")).status_code==404
    grant.students.add(m["student"])
    monkeypatch.setattr(timezone,"now",lambda:grant.expires_at)
    assert family.get(url(m,"family_schedule")).status_code==404
    assert b"Expired" in client.get(url(m,"family_access")).content


def test_family_page_pagination_and_filter_scope(music):
    m=music
    from scheduling.lesson_models import LessonOccurrence
    from scheduling.credit_models import LessonCredit
    series=book(m,{**m["data"],"last_date":"2030-01-07"})
    original=series.occurrences.get()
    for i in range(1,29):
        item=LessonOccurrence.objects.create(series=series,starts_at=original.starts_at+timedelta(days=i),
            ends_at=original.ends_at+timedelta(days=i),state="canceled")
        LessonCredit.objects.create(profile=m["profile"],source=item,snapshot=series.snapshot,
            expires_at=timezone.now()+timedelta(days=30))
    client,_,_=family_client(m)
    response=client.get(url(m,"family_schedule"))
    assert len(response.context["items"])==25 and len(response.context["credits"])==10
    response=client.get(url(m,"family_schedule")+f'?page=2&credits_page=2&student={m["student"].pk}')
    assert len(response.context["items"])==4 and response.context["credits"].number==2
    assert response.context["selected"]==str(m["student"].pk)
    assert b'aria-label="Credit pages"' in response.content


@pytest.mark.parametrize("attendance,label",[("unmarked","Attendance not recorded"),("absent","Absent"),
    ("no_show","No-show"),("present","Present")])
def test_past_lessons_show_attendance_not_future_scheduling_label(music,attendance,label):
    m=music
    series=book(m,{**m["data"],"last_date":"2030-01-07"})
    series.occurrences.update(starts_at=timezone.now()-timedelta(hours=1),
        ends_at=timezone.now()-timedelta(minutes=30),attendance=attendance)
    client,_,_=family_client(m)
    html=client.get(url(m,"family_schedule")+"?period=past").content.decode()
    assert label in html and ">Scheduled</span>" not in html
