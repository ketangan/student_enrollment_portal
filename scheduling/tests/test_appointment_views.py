import uuid
from datetime import date, time, timedelta
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import School
from scheduling import appointments as ap
from scheduling.appointment_models import LessonBooking, LessonBookingOffer
from scheduling.family_models import FamilyScheduleGrant
from scheduling.lesson_models import LessonStudent, LessonHousehold
from scheduling.models import SchedulingProfile
from scheduling.tests.test_lessons import music, url
from scheduling.tests.test_appointments import offer, CONTACT, prepare, booked, family, offer_data
from scheduling.appointment_forms import OfferForm

pytestmark=pytest.mark.django_db


def path(m, name, **extra):
    return url(m,name,**extra)


def page_token(client, m, offer, family=False, student=None):
    route=path(m,"family_lesson_book" if family else "lesson_trial_book",public_id=offer.public_id)
    query={"date":"2030-01-07"}
    if student: query["student"]=student.pk
    response=client.get(route,query)
    assert response.status_code==200
    return route+"?date=2030-01-07"+(f"&student={student.pk}" if student else ""), response.context["slots"][20]["token"]


def test_public_complete_journey_and_receipt_privacy(music, offer, client, mailoutbox):
    m=music
    catalogue=client.get(path(m,"lesson_trials"))
    assert catalogue.status_code==200 and b"Piano trial" in catalogue.content
    default=client.get(path(m,"lesson_trial_book",public_id=offer.public_id))
    assert default.status_code==200 and default.context["selection"].cleaned_data["date"]==date(2030,1,2)
    route,token=page_token(client,m,offer)
    result=client.post(route,{**CONTACT,"slot":token})
    assert result.status_code==302
    receipt=client.get(result.url)
    assert b"Your lesson is booked" in receipt.content and b"No charge" in receipt.content
    assert b"Taylor Visitor" in receipt.content and b"no email has been sent" in receipt.content
    assert "no-store" in receipt["Cache-Control"] and receipt["X-Robots-Tag"].startswith("noindex")
    assert not mailoutbox
    assert Client().get(result.url).status_code==404
    assert client.post(route,{**CONTACT,"slot":token}).url==result.url
    offer.published=False; offer.save()
    assert client.get(result.url).status_code==200
    assert client.get(path(m,"lesson_trial_book",public_id=offer.public_id)).status_code==404


@pytest.mark.parametrize("changes",[{"date":"bad"},{"date":"2031-01-01"}])
def test_invalid_date(music, offer, client, changes):
    result=client.get(path(music,"lesson_trial_book",public_id=offer.public_id),changes)
    assert result.status_code==400 and not LessonBooking.objects.exists()


def test_contact_slot_and_conflict_errors(music,offer,client):
    route,token=page_token(client,music,offer)
    assert client.post(route,{"slot":token}).status_code==400
    response=client.post(route,{**CONTACT,"slot":"invalid"})
    assert response.status_code==400 and b"Choose a time again" in response.content
    assert not LessonBooking.objects.exists()
    # A price change after a customer selected a slot cannot silently alter their quote.
    offer.fee_cents=9900;offer.save()
    assert client.post(route,{**CONTACT,"slot":token}).status_code==400
    assert not LessonBooking.objects.exists()


def test_closed_date_empty_catalogue_and_free_payment_post(music, offer, client):
    offer.notice_hours=720;offer.save()
    response=client.get(path(music,"lesson_trial_book",public_id=offer.public_id),{"date":"2030-01-07"})
    assert b"No times available" in response.content and b"name=\"slot\"" not in response.content
    offer.public_trials=False;offer.save()
    assert b"No lessons are available" in client.get(path(music,"lesson_trials")).content
    offer.notice_hours=24;offer.public_trials=True;offer.save()
    item,_,_=booked(music,offer)
    client.force_login(music["user"])
    payment=dict(key=uuid.uuid4(),kind="payment",amount="10",reference="free",confirm="on")
    assert client.post(path(music,"lesson_booking_detail",public_id=item.public_id),payment).status_code==404


def test_family_permission_selection_and_receipt_revocation(music,offer,client):
    m=music
    grant,session=family(m)
    client_session=client.session
    client_session.update(dict(session));client_session.save()
    catalogue=client.get(path(m,"family_booking"))
    assert catalogue.status_code==200
    assert b"Book a lesson" in client.get(path(m,"family_schedule")).content
    assert client.get(path(m,"family_lesson_book",public_id=offer.public_id)).status_code==200
    route,token=page_token(client,m,offer,True,m["student"])
    result=client.post(route,{"slot":token,"contact_email":"attacker@example.invalid"})
    assert result.status_code==302
    item=LessonBooking.objects.get()
    assert item.contact_email==grant.recipient_email and item.series.student_id==m["student"].pk
    assert client.get(result.url).status_code==200
    other=LessonStudent.objects.create(household=m["family"],name="Unapproved sibling")
    response=client.get(path(m,"family_lesson_book",public_id=offer.public_id),{"date":"2030-01-08","student":other.pk})
    assert response.status_code==400 and b"Unapproved sibling" not in response.content
    grant.revoked_at=timezone.now();grant.save()
    assert client.get(result.url).status_code==404 and client.get(path(m,"family_booking")).status_code==404


def test_read_only_link_does_not_gain_booking(music,offer,client):
    grant,session=family(music,False)
    state=client.session;state.update(dict(session));state.save()
    assert b"Book a lesson" not in client.get(path(music,"family_schedule")).content
    assert client.get(path(music,"family_booking")).status_code==404


def test_paid_canceled_lesson_shows_refund_review_not_payment_due(music,offer,client):
    from scheduling.lessons import cancel_lessons
    m=music;offer.fee_cents=3000;offer.save();item,_,_=booked(m,offer)
    data=dict(key=uuid.uuid4(),kind="payment",amount="30",reference="cancel-review",confirm="on")
    ap.record_payment(m["user"],m["slug"],item.public_id,data)
    cancel_lessons(m["user"],m["slug"],item.series.occurrences.get().pk,"one","Canceled")
    client.force_login(m["user"])
    route=path(m,"lesson_booking_detail",public_id=item.public_id)
    response=client.get(route)
    assert b"$30.00 is still received" in response.content and b"Outstanding" not in response.content
    assert b'Payment received</option>' not in response.content
    assert client.post(route,{**data,"key":uuid.uuid4()}).status_code==400
    refund={**data,"key":uuid.uuid4(),"kind":"refund","reference":"cancel-refund"}
    assert client.post(route,refund).status_code==302
    assert b"No money is held" in client.get(route).content


@pytest.mark.parametrize("role,expected",[("owner",200),("scheduler",404),("viewer",404)])
def test_offer_owner_permissions_and_stale_save(music,offer,client,role,expected):
    m=music;m["member"].role=role;m["member"].save();client.force_login(m["user"])
    index=path(m,"lesson_booking_setup")
    edit=path(m,"lesson_booking_offer_edit",pk=offer.pk)
    assert client.get(index).status_code==expected
    response=client.get(edit)
    assert response.status_code==expected
    if role=="owner":
        token=response.context["form"].fields["edit_revision"].initial
        data=offer_data(offer,edit_revision=token)
        assert client.post(edit,data).status_code==302
        assert client.post(edit,{**data,"title":"stale"}).status_code==400
        new=path(m,"lesson_booking_offer_new")
        assert client.get(new).status_code==200
        assert client.post(new,offer_data(offer,title="Second trial")).status_code==302
        assert client.post(new,{}).status_code==400
        assert client.get(path(m,"lesson_booking_offer_edit",pk=99999)).status_code==404
    else:
        assert client.post(edit,offer_data(offer)).status_code==404


@pytest.mark.parametrize("name",["lesson_booking_setup","lesson_booking_offer_new","lesson_bookings"])
def test_staff_requires_login(music,offer,client,name):
    assert client.get(path(music,name)).status_code==302


@pytest.mark.parametrize("role",["owner","scheduler","viewer"])
def test_shared_test_payment_ui_role_and_history(music,offer,client,role):
    m=music;offer.fee_cents=3000;offer.save();item,session,token=booked(m,offer)
    m["member"].role=role;m["member"].save();client.force_login(m["user"])
    listing=client.get(path(m,"lesson_bookings"))
    assert b"Taylor Visitor" in listing.content
    detail=path(m,"lesson_booking_detail",public_id=item.public_id)
    response=client.get(detail)
    assert response.status_code==200 and b"Saved, not sent" in response.content and b"$30.00" in response.content
    series=client.get(path(m,"lesson_series",public_id=item.series.public_id))
    assert b"No charges created" not in series.content and b"Test quote" in series.content
    data=dict(key=uuid.uuid4(),kind="payment",amount="30",reference="manual-test",confirm="on")
    result=client.post(detail,data)
    if role=="viewer":
        assert result.status_code==404 and not item.order.entries.exists()
        assert b"Record test payment" not in response.content
    else:
        assert result.status_code==302 and item.order.entries.count()==1
        assert b"$0.00" in client.get(detail).content
        assert client.post(detail,{}).status_code==400
        overpay={**data,"key":uuid.uuid4(),"reference":"overpay"}
        assert client.post(detail,overpay).status_code==400


def test_cross_tenant_urls_and_default_off(music,offer,client,settings):
    m=music; item,_,_=booked(m,offer)
    other=SchedulingProfile.objects.create(school=School.objects.create(slug="another"),enabled=True,lessons_enabled=True)
    foreign=dict(school_slug="another",public_id=offer.public_id)
    assert client.get(reverse("scheduling:lesson_trial_book",kwargs=foreign)).status_code==404
    client.force_login(m["user"])
    assert client.get(reverse("scheduling:lesson_booking_detail",kwargs={"school_slug":"another","public_id":item.public_id})).status_code==404
    settings.BOOKINGS_LESSON_BOOKING_PREVIEW=False
    assert client.get(path(m,"lesson_trials")).status_code==404
    assert client.get(path(m,"lesson_booking_setup")).status_code==200
    data=dict(key=uuid.uuid4(),kind="payment",amount="10",reference="no",confirm="on")
    assert client.post(path(m,"lesson_booking_detail",public_id=item.public_id),data).status_code==404


def test_csrf_protection_for_guest_and_staff(music,offer):
    client=Client(enforce_csrf_checks=True)
    route,token=page_token(client,music,offer)
    assert client.post(route,{**CONTACT,"slot":token}).status_code==403
    csrf=client.cookies["csrftoken"].value
    result=client.post(route,{**CONTACT,"slot":token,"csrfmiddlewaretoken":csrf})
    assert result.status_code==302
    client.force_login(music["user"])
    assert client.post(path(music,"lesson_booking_offer_new"),offer_data(offer)).status_code==403


def test_rate_limits_prevent_repeated_booking_submissions(music,offer,settings):
    from django.core.cache import cache
    settings.RATELIMIT_ENABLE=True
    cache.clear()
    client=Client(REMOTE_ADDR="192.0.2.123")
    route,token=page_token(client,music,offer)
    for _ in range(10): assert client.post(route,{}).status_code==400
    assert client.post(route,{}).status_code==403
    assert not LessonBooking.objects.exists()
    cache.clear()
