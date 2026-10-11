from datetime import date, timedelta
from unittest.mock import patch
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import Client
from django.utils import timezone

from core.models import School
from core.payments.models import PaymentOrder
from scheduling import credits
from scheduling.credit_models import LessonCredit, LessonCreditPolicy
from scheduling.lesson_models import LessonClosure, LessonHousehold, LessonOccurrence
from scheduling.models import Location, SchedulingMembership, SchedulingProfile
from scheduling.tests.test_lessons import music, book, url
from scheduling.tests.test_credits import issued, redemption, revoke_data, cancel

pytestmark = pytest.mark.django_db


def test_staff_browser_policy_cancel_credit_redeem_history_flow(client, music):
    m = music
    client.force_login(m["user"])
    policy_url = url(m, "lesson_credit_policy")
    response = client.get(policy_url)
    assert response.status_code == 200 and not LessonCreditPolicy.objects.exists()
    assert client.post(policy_url, {}).status_code == 400
    policy_data = {"enabled":"on", "family_cancellations":"on", "studio_cancellations":"on",
        "notice_hours":24, "expiry_days":90, "edit_revision":response.context["form"]["edit_revision"].value()}
    assert client.post(policy_url, policy_data).status_code == 302
    assert client.post(policy_url, policy_data).status_code == 400
    series = book(m)
    target = url(m, "lesson_cancel", pk=series.occurrences.first().pk)
    data = {"scope":"one", "origin":"family", "reason":"Family away", "confirm":"on"}
    response = client.post(target, data)
    assert b"One make-up credit" in response.content and not LessonCredit.objects.exists()
    response = client.post(target, {**data,"action":"confirm","preview_token":response.context["token"]})
    assert response.status_code == 302
    assert b"Credit: Available" in client.get(response.url).content
    credit = LessonCredit.objects.get()
    list_url = url(m,"lesson_credits")
    assert b"Riley" in client.get(list_url).content
    assert b"24 hours" in client.get(list_url).content
    assert client.get(list_url+f'?family={m["family"].pk}').status_code == 200
    assert client.get(list_url+'?family=wrong').status_code == 404
    detail = client.get(url(m,"lesson_credit",public_id=credit.public_id))
    assert b"Credit history" in detail.content and b"Family away" in detail.content
    assert "no-store" in detail.headers["Cache-Control"]
    target = url(m,"lesson_credit_book",public_id=credit.public_id)
    assert client.get(target).status_code == 200
    assert client.post(target,{}).status_code == 400
    data, _, _ = redemption(m,credit)
    response = client.post(target,data)
    assert response.context["can_confirm"] and b"uses one lesson credit" in response.content
    confirmed = {**data,"action":"confirm","preview_token":response.context["token"]}
    response = client.post(target,confirmed)
    assert response.status_code == 302 and client.post(target,confirmed).url == response.url
    assert b"Make-up credit history" in client.get(response.url).content
    detail = client.get(url(m,"lesson_credit",public_id=credit.public_id))
    assert b"Booked make-up" in detail.content and b"Redeemed" in detail.content
    assert b"Revoke credit" not in detail.content
    assert not PaymentOrder.objects.exists()


def test_credit_revoke_form_and_errors(client,music):
    m=music; credit=issued(m); client.force_login(m["user"])
    target=url(m,"lesson_credit_revoke",public_id=credit.public_id)
    response=client.get(target)
    assert b"Riley" in response.content
    assert client.post(target,{}).status_code == 400
    data={"confirm":"on","reason":"Correction","revision":response.context["form"]["revision"].value()}
    with patch("scheduling.views_credits.revoke",side_effect=ValidationError("Changed credit")):
        response=client.post(target,data)
        assert response.status_code == 400 and b"Changed credit" in response.content
    assert client.post(target,data).status_code == 302
    assert b"Revoked" in client.get(url(m,"lesson_credit",public_id=credit.public_id)).content


def test_expired_revocation_and_credit_pages_empty_pagination(client,music,monkeypatch):
    m=music; client.force_login(m["user"])
    target=url(m,"lesson_credits")
    assert b"No make-up credits" in client.get(target).content
    assert b"New make-up credits are disabled" in client.get(target).content
    credit=issued(m)
    now=credit.expires_at+timedelta(seconds=1)
    monkeypatch.setattr(timezone,"now",lambda:now)
    client.force_login(m["user"])
    assert b"Expired" in client.get(target+"?page=bad").content
    credits.revoke(m["user"],m["slug"],credit.public_id,revoke_data(m,credit))
    assert LessonCredit.objects.get().status_label=="Revoked"


def test_redemption_conflict_errors_and_inactive_policy(client,music):
    m=music;credit=issued(m);client.force_login(m["user"])
    data,token,_=redemption(m,credit)
    target=url(m,"lesson_credit_book",public_id=credit.public_id)
    LessonClosure.objects.create(location=m["location"],first_date=date(2030,1,8),last_date=date(2030,1,8),reason="Holiday")
    response=client.post(target,data)
    assert not response.context["can_confirm"] and b"Holiday" in response.content
    response=client.post(target,{**data,"action":"confirm","preview_token":token})
    assert response.status_code==400 and b"unavailable" in response.content
    response=client.post(target,{**data,"first_date":"2031-01-08"})
    assert response.status_code==400
    LessonCreditPolicy.objects.filter(profile=m["profile"]).update(family_cancellations=False,studio_cancellations=False)
    assert b"no credit" in client.get(url(m,"lesson_credits")).content


@pytest.mark.parametrize("role,policy,write",[("owner",200,200),("scheduler",404,200),("viewer",404,404)])
def test_roles_navigation_and_post_guards(client,music,role,policy,write):
    m=music;credit=issued(m);m["member"].role=role;m["member"].save();client.force_login(m["user"])
    assert client.get(url(m,"lesson_credits")).status_code==200
    assert client.get(url(m,"lesson_credit",public_id=credit.public_id)).status_code==200
    assert client.get(url(m,"lesson_credit_policy")).status_code==policy
    assert client.get(url(m,"lesson_credit_revoke",public_id=credit.public_id)).status_code==policy
    assert client.get(url(m,"lesson_credit_book",public_id=credit.public_id)).status_code==write
    data,token,_=redemption(m,credit)
    if role!="owner":
        assert client.post(url(m,"lesson_credit_policy"),{}).status_code==404
        with pytest.raises(Http404): credits.revoke(m["user"],m["slug"],credit.public_id,revoke_data(m,credit))
    if role=="viewer":
        assert b"Book make-up" not in client.get(url(m,"lesson_credits")).content
        assert client.post(url(m,"lesson_credit_book",public_id=credit.public_id),data).status_code==404
        with pytest.raises(Http404): credits.redeem(m["user"],m["slug"],credit.public_id,data,token)


@pytest.mark.parametrize("disabled",["profile","lessons","membership","user"])
def test_revoked_access_blocks_prepared_credit_mutations(client,music,disabled):
    m=music;credit=issued(m);client.force_login(m["user"])
    data,token,_=redemption(m,credit)
    if disabled=="profile": m["profile"].enabled=False;m["profile"].save()
    if disabled=="lessons": m["profile"].lessons_enabled=False;m["profile"].save()
    if disabled=="membership": m["member"].is_active=False;m["member"].save()
    if disabled=="user": m["user"].is_active=False;m["user"].save()
    with pytest.raises(Http404): credits.redeem(m["user"],m["slug"],credit.public_id,data,token)
    assert client.get(url(m,"lesson_credits")).status_code in [302,404]
    assert LessonCredit.objects.get().available


def test_cross_tenant_access_tokens_choices_and_no_public_identity(client,music):
    m=music;credit=issued(m)
    assert client.get(url(m,"lesson_credits")).status_code==302
    client.force_login(m["user"])
    school=School.objects.create(slug="other-lessons")
    profile=SchedulingProfile.objects.create(school=school,enabled=True,lessons_enabled=True)
    SchedulingMembership.objects.create(profile=profile,user=m["user"],role="owner")
    other={**m,"slug":school.slug}
    assert client.get(url(other,"lesson_credit",public_id=credit.public_id)).status_code==404
    family=LessonHousehold.objects.create(profile=profile,name="Private family",email="private@example.invalid")
    assert client.get(url(m,"lesson_credits")+f'?family={family.pk}').status_code==404
    assert b"Riley" not in client.get(url(m,"public_schedule")).content
    assert b"Make-up" not in client.get(url(m,"public_schedule")).content
    foreign_location=Location.objects.create(profile=profile,name="Other studio")
    data,_,_=redemption(m,credit)
    from scheduling.credit_forms import RedeemCreditForm
    form=RedeemCreditForm({**data,"location":foreign_location.pk},occurrence=credit.source)
    assert not form.is_valid()
    assert client.get(url(m,"lesson_credit",public_id=uuid.uuid4())).status_code==404


def test_unused_credit_blocks_disabling_lessons(client,music):
    m=music;credit=issued(m);client.force_login(m["user"])
    # Resolve the remaining future dates without creating more credits.
    LessonCreditPolicy.objects.filter(profile=m["profile"]).update(enabled=False)
    for item in credit.source.series.occurrences.filter(state="scheduled"):
        cancel(m,item)
    response=client.post(url(m,"lesson_settings"),{})
    assert response.status_code==400 and b"Resolve unused make-up credits" in response.content
    credits.revoke(m["user"],m["slug"],credit.public_id,revoke_data(m,credit))
    assert client.post(url(m,"lesson_settings"),{}).status_code==302


def test_all_credit_mutations_require_csrf_and_post(music):
    m=music;credit=issued(m)
    client=Client(enforce_csrf_checks=True);client.force_login(m["user"])
    for name in ["lesson_credit_policy","lesson_credit_book","lesson_credit_revoke"]:
        kwargs={} if name=="lesson_credit_policy" else {"public_id":credit.public_id}
        target=url(m,name,**kwargs)
        assert client.post(target,{}).status_code==403
        assert client.delete(target).status_code==403
    client=Client();client.force_login(m["user"])
    assert client.post(url(m,"lesson_credit",public_id=credit.public_id),{}).status_code==405
    assert client.post(url(m,"lesson_credits"),{}).status_code==405
