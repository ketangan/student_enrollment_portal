from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import PaymentOrder
from scheduling import lessons
from scheduling.forms import LocationForm
from scheduling.lesson_forms import SeriesForm
from scheduling.lesson_models import (LessonAvailability, LessonClosure, LessonHousehold, LessonOccurrence,
    LessonSeries, LessonService, LessonStudent, LessonTeacher)
from scheduling.models import Location, Room, SchedulingMembership, SchedulingProfile, Session, Offering
from scheduling.reservations import ensure_capacity
from scheduling.setup import save_setup, set_publication

pytestmark = pytest.mark.django_db


@pytest.fixture
def music(booking_user, booking_profile, booking_member, monkeypatch):
    now = datetime(2030, 1, 1, 12, tzinfo=dt_timezone.utc)
    monkeypatch.setattr(timezone, "now", lambda: now)
    booking_profile.lessons_enabled = True
    booking_profile.save()
    location = Location.objects.create(profile=booking_profile, name="Studio")
    room = Room.objects.create(location=location, name="Piano room", capacity=4)
    service = LessonService.objects.create(profile=booking_profile, name="Piano", subject="Piano", duration_minutes=30)
    teacher = LessonTeacher.objects.create(profile=booking_profile, name="Alex", buffer_minutes=5, travel_minutes=30)
    teacher.services.add(service)
    for weekday in range(7):
        LessonAvailability.objects.create(teacher=teacher, location=location, weekday=weekday, start_time=time(0), end_time=time(23,59))
    family = LessonHousehold.objects.create(profile=booking_profile, name="Demo family", email="family@example.invalid")
    student = LessonStudent.objects.create(household=family, name="Riley")
    data = dict(student=str(student.pk), service=str(service.pk), teacher=str(teacher.pk), location=str(location.pk),
                room=str(room.pk), first_date="2030-01-07", last_date="2030-01-21", start_time="10:00", interval_weeks="1")
    return dict(user=booking_user, profile=booking_profile, member=booking_member, location=location, room=room,
                service=service, teacher=teacher, family=family, student=student, data=data, slug=booking_profile.school.slug)


def preview(m, data=None):
    data = data or m["data"]
    form = SeriesForm(data, profile=m["profile"])
    assert form.is_valid(), form.errors
    rows = lessons.plan_series(form.cleaned_data)
    return rows, lessons.preview_token(m["user"], m["profile"], data, form.cleaned_data, rows)


def book(m, data=None):
    data = data or m["data"]
    _, token = preview(m, data)
    return lessons.create_series(m["user"], m["slug"], data, token)


def url(m, name, **kwargs):
    return reverse("scheduling:"+name, kwargs={"school_slug":m["slug"], **kwargs})


def test_atomic_series_snapshots_idempotency_no_money(music):
    m = music
    rows, token = preview(m)
    assert [r["starts_at"].date() for r in rows] == [date(2030,1,7),date(2030,1,14),date(2030,1,21)]
    assert not LessonSeries.objects.exists()
    item = lessons.create_series(m["user"], m["slug"], m["data"], token)
    assert lessons.create_series(m["user"], m["slug"], m["data"], token).pk == item.pk
    assert item.occurrences.count() == 3 and item.snapshot["duration_minutes"] == 30
    assert not PaymentOrder.objects.exists()
    assert AdminAuditLog.objects.filter(model_label="scheduling.lessonseries").count() == 1
    m["service"].name = "Changed service"
    m["service"].save()
    item.refresh_from_db()
    assert item.snapshot["service"] == "Piano"


@pytest.mark.parametrize("field,value", [("first_date","2029-01-01"),("last_date","2030-01-01"),
    ("last_date","2031-01-01"),("interval_weeks","0"),("interval_weeks","1000000"),("student","9999"),
    ("start_time","bad"),("first_date","bad")])
def test_invalid_series_fields(music, field, value):
    assert not SeriesForm({**music["data"],field:value},profile=music["profile"]).is_valid()


def test_series_foreign_room_teacher_qualification_and_active_flags(music):
    m=music
    other = Location.objects.create(profile=m["profile"], name="Other")
    room = Room.objects.create(location=other,name="Other room",capacity=2)
    assert not SeriesForm({**m["data"],"room":room.pk},profile=m["profile"]).is_valid()
    m["teacher"].services.clear()
    assert not SeriesForm(m["data"],profile=m["profile"]).is_valid()
    m["teacher"].services.add(m["service"])
    m["service"].is_active=False
    m["service"].save()
    assert not SeriesForm(m["data"],profile=m["profile"]).is_valid()


def test_biweekly_single_and_closure_preview(music):
    m=music
    assert len(preview(m,{**m["data"],"interval_weeks":"2"})[0]) == 2
    assert len(preview(m,{**m["data"],"last_date":"2030-01-07"})[0]) == 1
    LessonClosure.objects.create(location=m["location"],first_date=date(2030,1,14),last_date=date(2030,1,14),reason="Holiday")
    item=book(m)
    assert list(item.occurrences.values_list("state",flat=True)) == ["scheduled","skipped","scheduled"]
    assert item.occurrences.get(state="skipped").reason == "Holiday"


def test_teacher_specific_closure_and_all_closed_rejected(music):
    m=music
    teacher=LessonTeacher.objects.create(profile=m["profile"],name="Other teacher")
    closure=LessonClosure.objects.create(location=m["location"],teacher=teacher,first_date=date(2030,1,1),last_date=date(2030,2,1),reason="Away")
    assert all(r["state"]=="scheduled" for r in preview(m)[0])
    closure.teacher=m["teacher"]; closure.save()
    with pytest.raises(ValidationError,match="no available dates"):
        book(m)
    assert not LessonSeries.objects.exists()


@pytest.mark.parametrize("clock",["23:45","02:30"])
def test_availability_and_dst_gap(music,clock):
    m=music
    data={**m["data"],"first_date":"2030-03-10","last_date":"2030-03-10","start_time":clock}
    if clock=="02:30":
        with pytest.raises(ValidationError,match="clock change"):
            preview(m,data)
    else:
        assert preview(m,data)[0][0]["state"]=="conflict"


def test_dst_fold_crossing_gap_and_wall_time_preserved(music,monkeypatch):
    m=music
    with pytest.raises(ValidationError,match="clock change"):
        lessons.local_datetime(date(2030,11,3),time(1,30),ZoneInfo("America/Los_Angeles"))
    m["service"].duration_minutes=120; m["service"].save()
    with pytest.raises(ValidationError,match="cross a clock change"):
        preview(m,{**m["data"],"first_date":"2030-03-10","last_date":"2030-03-10","start_time":"01:30"})
    rows,_=preview(m,{**m["data"],"first_date":"2030-03-03","last_date":"2030-03-17","start_time":"10:00"})
    assert [r["starts_at"].hour for r in rows]==[18,17,17]
    monkeypatch.setattr(timezone,"now",lambda:datetime(2030,1,7,18,tzinfo=dt_timezone.utc))
    with pytest.raises(ValidationError,match="future"):
        preview(m)


def test_no_hours_and_partial_hours_do_not_combine(music):
    m=music
    LessonAvailability.objects.all().delete()
    assert preview(m)[0][0]["state"]=="conflict"
    LessonAvailability.objects.create(teacher=m["teacher"],location=m["location"],weekday=0,start_time=time(10),end_time=time(10,15))
    LessonAvailability.objects.create(teacher=m["teacher"],location=m["location"],weekday=0,start_time=time(10,15),end_time=time(11))
    with pytest.raises(ValidationError,match="conflicts"):
        book(m)


def test_closure_bounds_and_uncharged_optional_room(music):
    m=music
    data=dict(location=m["location"].pk,first_date="2030-02-01",last_date="2030-02-02",reason="School break")
    assert lessons.save_lesson_setup(m["user"],m["slug"],"closures",data).is_valid()
    assert not lessons.save_lesson_setup(m["user"],m["slug"],"closures",{**data,"last_date":"9999-12-31"}).is_valid()
    lessons.validate_lesson_room(None,timezone.now(),timezone.now()+timedelta(hours=1))
    item=book(m,{**m["data"],"room":""})
    assert not item.room_id and not PaymentOrder.objects.exists()


def test_teacher_student_room_and_buffer_conflicts(music):
    m=music
    book(m)
    assert "Teacher" in preview(m)[0][0]["reason"]
    assert "Teacher" in preview(m,{**m["data"],"start_time":"10:30"})[0][0]["reason"]
    assert preview(m,{**m["data"],"start_time":"10:35"})[0][0]["state"]=="scheduled"
    other=LessonTeacher.objects.create(profile=m["profile"],name="Casey")
    other.services.add(m["service"])
    LessonAvailability.objects.create(teacher=other,location=m["location"],weekday=0,start_time=time(9),end_time=time(12))
    data={**m["data"],"teacher":str(other.pk)}
    assert "Student" in preview(m,data)[0][0]["reason"]
    child=LessonStudent.objects.create(household=m["family"],name="Sam")
    assert "Room" in preview(m,{**data,"student":str(child.pk)})[0][0]["reason"]
    assert preview(m,{**data,"student":str(child.pk),"room":""})[0][0]["state"]=="scheduled"


def test_teacher_cross_location_travel_buffer(music):
    m=music
    book(m)
    other=Location.objects.create(profile=m["profile"],name="West")
    LessonAvailability.objects.create(teacher=m["teacher"],location=other,weekday=0,start_time=time(9),end_time=time(12))
    data={**m["data"],"location":str(other.pk),"room":"","start_time":"10:45"}
    assert "Teacher" in preview(m,data)[0][0]["reason"]
    assert preview(m,{**data,"start_time":"11:00"})[0][0]["state"]=="scheduled"


def test_workshop_conflict_both_directions(music):
    m=music
    rows,_=preview(m)
    offering=Offering.objects.create(location=m["location"],name="Workshop")
    session=Session.objects.create(offering=offering,room=m["room"],starts_at=rows[1]["starts_at"],ends_at=rows[1]["ends_at"],capacity=4,is_published=True)
    assert "published" in preview(m)[0][1]["reason"]
    with pytest.raises(ValidationError): book(m)
    assert not LessonSeries.objects.exists()
    session.is_published=False;session.save()
    book(m)
    with pytest.raises(ValidationError,match="scheduled lesson"):
        set_publication(m["user"],m["slug"],"sessions",session.pk,True)
    session.refresh_from_db();assert not session.is_published


def test_full_studio_blocks_lessons_in_other_rooms_and_reverse(music):
    import uuid
    from core.payments import service as payments
    from scheduling.models import PartyRequest, Reservation

    m = music
    rows, _ = preview(m)
    start, end = rows[0]["starts_at"], rows[0]["ends_at"]
    room = Room.objects.create(location=m["location"], name="Party table", capacity=6)
    offering = Offering.objects.create(location=m["location"], name="Craft")
    party = PartyRequest.objects.create(offering=offering, organizer_name="Party", organizer_email="p@example.invalid",
        starts_at=start, ends_at=end, space="studio", group_size=2, submission_key=uuid.uuid4())
    order = payments.create_order(school=m["profile"].school, purpose="booking", source_key="studio-test",
        total_cents=0, deposit_cents=0, description="Party")
    reservation = Reservation.objects.create(profile=m["profile"], party_request=party, room=room,
        starts_at=start, ends_at=end, quantity=2, order=order, state="confirmed", hold_until=start, snapshot={"space": "studio"})
    assert preview(m)[0][0]["state"] == "conflict"
    assert preview(m, {**m["data"], "room": ""})[0][0]["state"] == "conflict"
    reservation.state = "canceled"
    reservation.save()
    book(m)
    with pytest.raises(ValidationError, match="full studio"):
        ensure_capacity(reservation)


def test_party_and_camp_room_claims_both_directions(music):
    from core.payments import service as payments
    from scheduling.models import Camp, CampRegistration, PartyRequest, Reservation, ReservationOccurrence
    import uuid
    m=music
    rows,_=preview(m)
    offering=Offering.objects.create(location=m["location"],name="Craft")
    order=payments.create_order(school=m["profile"].school,purpose="booking",source_key="party-test",total_cents=0,deposit_cents=0,description="party")
    party=PartyRequest.objects.create(offering=offering,organizer_name="Party",organizer_email="p@example.invalid",starts_at=rows[0]["starts_at"],ends_at=rows[0]["ends_at"],space="exclusive",group_size=2,submission_key=uuid.uuid4())
    reservation=Reservation.objects.create(profile=m["profile"],party_request=party,room=m["room"],starts_at=rows[0]["starts_at"],ends_at=rows[0]["ends_at"],quantity=2,order=order,hold_until=rows[0]["starts_at"],state="confirmed")
    assert "reservation" in preview(m)[0][0]["reason"]
    reservation.state="canceled";reservation.save()
    camp=Camp.objects.create(offering=offering,name="Camp",price=0,deposit=0)
    registration=CampRegistration.objects.create(camp=camp,purchaser_name="Camp",purchaser_email="c@example.invalid",submission_key=uuid.uuid4())
    camp_order=payments.create_order(school=m["profile"].school,purpose="booking",source_key="camp-test",total_cents=0,deposit_cents=0,description="camp")
    camp_res=Reservation.objects.create(profile=m["profile"],camp_registration=registration,order=camp_order,starts_at=rows[0]["starts_at"],ends_at=rows[-1]["ends_at"],quantity=1,hold_until=rows[0]["starts_at"],state="confirmed")
    session=Session.objects.create(offering=offering,room=m["room"],starts_at=rows[1]["starts_at"],ends_at=rows[1]["ends_at"],capacity=4)
    ReservationOccurrence.objects.create(reservation=camp_res,session=session,room=m["room"],starts_at=session.starts_at,ends_at=session.ends_at,total_cents=0,deposit_cents=0)
    assert "reservation" in preview(m)[0][1]["reason"]
    camp_res.state="canceled";camp_res.save()
    book(m)
    with pytest.raises(ValidationError,match="scheduled lesson"):
        ensure_capacity(reservation)


@pytest.mark.parametrize("change",["name","duration","closure","payload","eligibility"])
def test_stale_previews_rejected(music,change):
    m=music
    _,token=preview(m)
    data=dict(m["data"])
    if change=="name":m["teacher"].name="Changed";m["teacher"].save()
    elif change=="duration":m["service"].duration_minutes=45;m["service"].save()
    elif change=="closure":LessonClosure.objects.create(location=m["location"],first_date=date(2030,1,14),last_date=date(2030,1,14),reason="Closed")
    elif change=="payload":data["start_time"]="11:00"
    else:m["teacher"].services.clear()
    with pytest.raises(ValidationError):lessons.create_series(m["user"],m["slug"],data,token)
    assert not LessonSeries.objects.exists()


@pytest.mark.parametrize("token",["","bad",signing.dumps({"key":"not-a-uuid"},salt=lessons.TOKEN_SALT)])
def test_invalid_signed_confirmation(music,token):
    with pytest.raises(ValidationError,match="preview"):
        lessons.create_series(music["user"],music["slug"],music["data"],token)


def test_no_partial_writes_when_audit_fails(music):
    m=music
    with patch("scheduling.lessons.audit",side_effect=RuntimeError("audit unavailable")),pytest.raises(RuntimeError):book(m)
    assert not LessonSeries.objects.exists() and not LessonOccurrence.objects.exists()
    with patch("scheduling.lessons.audit",side_effect=RuntimeError),pytest.raises(RuntimeError):
        lessons.save_lesson_setup(m["user"],m["slug"],"households",{"name":"Not saved","email":"x@example.invalid"})
    assert not LessonHousehold.objects.filter(name="Not saved").exists()


def test_setup_creation_validation_and_freeze(music):
    m=music
    forms={"services":{"name":"Violin","subject":"Violin","duration_minutes":45,"is_active":"on"},
        "teachers":{"name":"New teacher","services":[m["service"].pk],"buffer_minutes":0,"travel_minutes":30,"is_active":"on"},
        "households":{"name":"Other family","email":"other@example.invalid"},
        "students":{"household":m["family"].pk,"name":"Other child"},
        "availability":{"teacher":m["teacher"].pk,"location":m["location"].pk,"weekday":0,"start_time":"12:00","end_time":"14:00"},
        "closures":{"location":m["location"].pk,"teacher":m["teacher"].pk,"first_date":"2030-02-01","last_date":"2030-02-02","reason":"Break"}}
    for kind,data in forms.items():
        form=lessons.save_lesson_setup(m["user"],m["slug"],kind,data)
        assert form.is_valid(),form.errors
    book(m)
    assert lessons.setup_form(m["profile"],"teachers",pk=m["teacher"].pk).fields["buffer_minutes"].disabled
    assert lessons.setup_form(m["profile"],"students",pk=m["student"].pk).fields["household"].disabled
    window=LessonAvailability.objects.filter(teacher=m["teacher"]).first()
    assert all(f.field.disabled for f in lessons.setup_form(m["profile"],"availability",pk=window.pk).visible_fields())
    invalid=lessons.save_lesson_setup(m["user"],m["slug"],"closures",{**forms["closures"],"first_date":"2030-01-07","last_date":"2030-01-07"})
    assert not invalid.is_valid() and "explicitly" in str(invalid.errors)
    assert LocationForm(instance=m["location"],profile=m["profile"]).fields["timezone_name"].disabled
    m["location"].timezone_name="UTC"
    with pytest.raises(ValidationError,match="lesson"):m["location"].full_clean()


def test_tenant_scoping_and_unknown_kind(music):
    m=music
    other=SchedulingProfile.objects.create(school=School.objects.create(slug="other"),enabled=True,lessons_enabled=True)
    place=Location.objects.create(profile=other,name="Private")
    for model,fields in [(LessonAvailability,{"weekday":0,"start_time":time(9),"end_time":time(10)}),(LessonClosure,{"first_date":date(2030,1,1),"last_date":date(2030,1,1),"reason":"Away"})]:
        with pytest.raises(ValidationError,match="same workspace"):model(teacher=m["teacher"],location=place,**fields).clean()
    form=lessons.save_lesson_setup(m["user"],m["slug"],"availability",dict(teacher=m["teacher"].pk,location=place.pk,weekday=0,start_time="09:00",end_time="10:00"))
    assert not form.is_valid()
    for fn in [lambda:lessons.lesson_records(m["profile"],"unknown"),lambda:lessons.setup_form(m["profile"],"teachers",pk=9999)]:
        with pytest.raises(Http404):fn()


@pytest.mark.parametrize("scope,expected",[("one",2),("future",1)])
def test_cancel_explicit_history_no_money(music,scope,expected):
    m=music; item=book(m);middle=item.occurrences.all()[1]
    lessons.cancel_lessons(m["user"],m["slug"],middle.pk,scope,"Family request")
    assert item.occurrences.filter(state="scheduled").count()==expected and item.occurrences.count()==3
    audits=AdminAuditLog.objects.count()
    lessons.cancel_lessons(m["user"],m["slug"],middle.pk,scope,"Family request")
    assert AdminAuditLog.objects.count()==audits and not PaymentOrder.objects.exists()
    assert preview(m)[0][1]["state"]=="scheduled"


@pytest.mark.parametrize("scope,reason",[("all","reason"),("one"," "),("one","a"*201)])
def test_cancel_invalid(music,scope,reason):
    m=music;item=book(m).occurrences.first()
    with pytest.raises(ValidationError):lessons.cancel_lessons(m["user"],m["slug"],item.pk,scope,reason)


def test_cancel_past_and_audit_failure(music,monkeypatch):
    m=music;item=book(m).occurrences.first()
    with patch("scheduling.lessons.audit",side_effect=RuntimeError),pytest.raises(RuntimeError):
        lessons.cancel_lessons(m["user"],m["slug"],item.pk,"one","test")
    item.refresh_from_db();assert item.state=="scheduled"
    monkeypatch.setattr(timezone,"now",lambda:item.ends_at)
    with pytest.raises(ValidationError,match="Past"):lessons.cancel_lessons(m["user"],m["slug"],item.pk,"one","test")


@pytest.mark.parametrize("model,fields",[(LessonService,{"duration_minutes":0}), (LessonTeacher,{"buffer_minutes":121}),
    (LessonAvailability,{"weekday":7}), (LessonAvailability,{"end_time":time(0)}),
    (LessonClosure,{"last_date":date(2029,1,1)}), (LessonSeries,{"interval_weeks":0}),
    (LessonOccurrence,{"state":"paid"})])
def test_database_constraints(music,model,fields):
    m=music
    book(m)
    if model==LessonClosure:LessonClosure.objects.create(location=m["location"],first_date=date(2030,1,1),last_date=date(2030,1,1),reason="holiday")
    with pytest.raises(IntegrityError),transaction.atomic():model.objects.all().update(**fields)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("duplicate",[False,True])
def test_competing_series_lock_and_exact_retry(music,duplicate):
    m=music
    _,first=preview(m);_,second=preview(m)
    def run(token):
        close_old_connections()
        try:
            return lessons.create_series(m["user"],m["slug"],m["data"],token).pk
        except ValidationError:return None
        finally:close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,[first,first if duplicate else second]))
    assert LessonSeries.objects.count()==1 and LessonOccurrence.objects.count()==3
    assert sum(x is not None for x in results)==(2 if duplicate else 1)


@pytest.mark.django_db(transaction=True)
def test_lesson_and_workshop_publish_serialize_on_same_room(music):
    m=music;rows,token=preview(m)
    offering=Offering.objects.create(location=m["location"],name="Workshop")
    session=Session.objects.create(offering=offering,room=m["room"],starts_at=rows[0]["starts_at"],ends_at=rows[0]["ends_at"],capacity=2)
    def run(kind):
        close_old_connections()
        try:
            if kind=="lesson":lessons.create_series(m["user"],m["slug"],m["data"],token)
            else:set_publication(m["user"],m["slug"],"sessions",session.pk,True)
            return True
        except ValidationError:return False
        finally:close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,["lesson","workshop"]))
    assert results.count(True)==1
    session.refresh_from_db()
    assert session.is_published != LessonSeries.objects.exists()
