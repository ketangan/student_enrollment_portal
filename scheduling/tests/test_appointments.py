from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone as utc
from unittest.mock import patch
from zoneinfo import ZoneInfo
import uuid

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import PaymentOrder, LedgerEntry
from scheduling import appointments as ap, lessons, lesson_changes, credits
from scheduling.credit_models import LessonCreditPolicy, LessonCredit
from scheduling.appointment_models import LessonBookingOffer, LessonBooking, LessonBookingNotice
from scheduling.appointment_forms import OfferForm
from scheduling.family_access import issue_invitation, redeem_invitation, revoke_access, authorized_students
from scheduling.family_models import FamilyScheduleGrant
from scheduling.lesson_models import LessonHousehold, LessonStudent, LessonSeries, LessonOccurrence, LessonTeacher, LessonClosure
from scheduling.models import Location, Room, Offering, Session, SchedulingProfile
from scheduling.lesson_change_forms import RescheduleForm
from scheduling.tests.test_lessons import music, book, preview
from scheduling.tests.test_family_access import invitation

pytestmark = pytest.mark.django_db
CONTACT = dict(contact_name="Sam Visitor", contact_email="sam@example.invalid", student_name="Taylor Visitor", authority="on")


@pytest.fixture
def offer(music, settings):
    settings.DEBUG = True
    settings.BOOKINGS_LESSON_BOOKING_PREVIEW = True
    m = music
    return LessonBookingOffer.objects.create(profile=m["profile"], title="Piano trial", service=m["service"],
        teacher=m["teacher"], location=m["location"], room=m["room"], published=True, public_trials=True, family_booking=True)


def prepare(m, offer, session=None, grant=None, student=None, clock=time(10)):
    session = session or SessionStore()
    rows = ap.day_slots(offer, date(2030, 1, 7), student)
    row = next(r for r in rows if r["starts_at"].astimezone(ZoneInfo(offer.location.timezone_name)).time() == clock)
    token = ap.quote(offer, row, student, grant, ap.browser_secret(session))
    return session, token


def booked(m, offer):
    session, token = prepare(m, offer)
    return ap.confirm_booking(m["slug"], session, token, CONTACT), session, token


def family(m, allow=True):
    grant, secret = issue_invitation(m["user"], m["slug"], m["family"].pk, invitation(m, allow_booking=allow))
    session = SessionStore()
    redeem_invitation(m["slug"], secret, session)
    return grant, session


def offer_data(offer, **extra):
    data = dict(title=offer.title, service=offer.service_id, teacher=offer.teacher_id, location=offer.location_id,
        room=offer.room_id or "", published="on", public_trials="on", family_booking="on", notice_hours=24,
        horizon_days=60, interval_minutes=30, price="30.00")
    data.update(extra)
    return data


def test_free_trial_atomic_retry_separate_identity_no_enrollment(music, offer):
    m = music
    before = School.objects.values().get(pk=m["profile"].school_id)
    item, session, token = booked(m, offer)
    assert item.series.occurrences.get().state == "scheduled" and item.order is None
    assert item.series.student.household_id != m["family"].pk
    assert LessonBookingNotice.objects.get().recipient == CONTACT["contact_email"]
    assert "Taylor Visitor" in LessonBookingNotice.objects.get().body
    assert not FamilyScheduleGrant.objects.exists() and not PaymentOrder.objects.exists()
    assert School.objects.values().get(pk=m["profile"].school_id) == before
    counts = (LessonHousehold.objects.count(), LessonStudent.objects.count(), AdminAuditLog.objects.count())
    assert ap.confirm_booking(m["slug"], session, token, CONTACT).pk == item.pk
    assert counts == (LessonHousehold.objects.count(), LessonStudent.objects.count(), AdminAuditLog.objects.count())
    assert ap.customer_booking(m["slug"], session, item.public_id).pk == item.pk
    with pytest.raises(Http404): ap.customer_booking(m["slug"], SessionStore(), item.public_id)
    with pytest.raises(ValidationError): ap.confirm_booking(m["slug"], session, token, {**CONTACT, "student_name": "Different"})


def test_public_matching_email_does_not_join_existing_household(music, offer):
    session, token = prepare(music, offer)
    item = ap.confirm_booking(music["slug"], session, token, {**CONTACT, "contact_email": music["family"].email})
    assert item.series.student.household_id != music["family"].pk
    assert LessonHousehold.objects.filter(email=music["family"].email).count() == 2


def test_paid_trial_reuses_shared_quote_and_ledger(music, offer):
    offer.fee_cents=3000; offer.save()
    item, session, token = booked(music, offer)
    order = item.order
    assert order.purpose == "lesson_booking" and order.total_cents == 3000 and order.environment == "test"
    assert order.school_id == music["profile"].school_id and not order.account_id
    assert not order.attempts.exists() and not order.entries.exists()
    data = dict(key=uuid.uuid4(), kind="payment", amount="30.00", reference="test-receipt", confirm="on")
    entry = ap.record_payment(music["user"], music["slug"], item.public_id, data)
    assert ap.record_payment(music["user"], music["slug"], item.public_id, data).pk == entry.pk
    assert ap.payments.totals(order)["outstanding_cents"] == 0
    with pytest.raises(ValidationError): ap.record_payment(music["user"], music["slug"], item.public_id, {**data, "amount":"31"})
    refund = ap.record_payment(music["user"], music["slug"], item.public_id,
        {**data, "key":uuid.uuid4(), "kind":"refund", "amount":"10", "reference":"test-refund"})
    assert refund.amount_cents == 1000 and ap.payments.totals(order)["net_cents"] == 2000
    assert item.series.occurrences.get().state == "scheduled"
    with pytest.raises(ValidationError): ap.record_payment(music["user"], music["slug"], item.public_id, {})
    lessons.cancel_lessons(music["user"],music["slug"],item.series.occurrences.get().pk,"one","Canceled")
    assert ap.record_payment(music["user"],music["slug"],item.public_id,data).pk == entry.pk
    with pytest.raises(ValidationError,match="canceled"):
        ap.record_payment(music["user"],music["slug"],item.public_id,{**data,"key":uuid.uuid4(),"reference":"late"})


@pytest.mark.parametrize("stage",["order", "notice", "audit"])
def test_failure_rolls_back_entire_booking(music, offer, stage):
    offer.fee_cents=3000; offer.save()
    session, token = prepare(music, offer)
    target={"order":"scheduling.appointments.payments.create_order", "notice":"scheduling.appointments.save_notice", "audit":"scheduling.appointments.audit"}[stage]
    with patch(target, side_effect=RuntimeError), pytest.raises(RuntimeError):
        ap.confirm_booking(music["slug"], session, token, CONTACT)
    assert not LessonSeries.objects.exists() and not LessonBooking.objects.exists() and not PaymentOrder.objects.exists()
    assert LessonHousehold.objects.count() == 1 and not LessonBookingNotice.objects.exists()


@pytest.mark.parametrize("field,value",[("fee_cents",4000),("notice_hours",720),("title","Changed"),("interval_minutes",60)])
def test_stale_offer_rejected(music, offer, field, value):
    session, token = prepare(music, offer)
    setattr(offer, field, value); offer.save()
    with pytest.raises(ValidationError): ap.confirm_booking(music["slug"], session, token, CONTACT)
    assert not LessonBooking.objects.exists()


@pytest.mark.parametrize("target",["offer", "service", "teacher", "qualification", "profile", "lessons", "gate", "debug"])
def test_publish_active_and_local_gates_rechecked(music, offer, settings, target):
    session, token = prepare(music, offer)
    if target=="offer": offer.published=False; offer.save()
    elif target=="qualification": offer.teacher.services.clear()
    elif target in {"service", "teacher"}:
        obj=getattr(offer,target); obj.is_active=False; obj.save()
    elif target in {"profile","lessons"}:
        setattr(music["profile"],"enabled" if target=="profile" else "lessons_enabled",False); music["profile"].save()
    elif target=="debug": settings.DEBUG=False
    else: settings.BOOKINGS_LESSON_BOOKING_PREVIEW=False
    with pytest.raises(Http404): ap.confirm_booking(music["slug"], session, token, CONTACT)
    assert not LessonBooking.objects.exists()


def test_read_only_grants_stay_read_only_and_student_scope(music, offer):
    grant, session = family(music, allow=False)
    with pytest.raises(Http404): ap.booking_scope(music["slug"], session, True)
    grant, session = family(music)
    student = music["student"]
    scoped, token = prepare(music, offer, session, grant, student)
    item = ap.confirm_booking(music["slug"], scoped, token, {}, True)
    assert item.series.student_id == student.pk and item.contact_email == grant.recipient_email
    assert LessonHousehold.objects.count() == 1
    assert ap.customer_booking(music["slug"], scoped, item.public_id).pk == item.pk
    other_grant, other_session = family(music)
    with pytest.raises(Http404): ap.customer_booking(music["slug"], other_session, item.public_id)
    other = LessonStudent.objects.create(household=music["family"], name="Other sibling")
    session2, token2 = prepare(music, offer, session, grant, other, time(11))
    with pytest.raises(Http404): ap.confirm_booking(music["slug"], session2, token2, {}, True)
    revoke_access(music["user"], music["slug"], grant.public_id, {"reason":"Ended", "confirm":"on"})
    with pytest.raises(Http404): ap.customer_booking(music["slug"], scoped, item.public_id)
    with pytest.raises(Http404): ap.confirm_booking(music["slug"], scoped, token, {}, True)


@pytest.mark.parametrize("change",["revoke", "expire", "move", "permission"])
def test_authorization_is_revalidated_at_booking(music, offer, change):
    grant, session = family(music)
    session, token = prepare(music, offer, session, grant, music["student"])
    if change=="move":
        h=LessonHousehold.objects.create(profile=music["profile"],name="Another",email="other@example.invalid")
        music["student"].household=h; music["student"].save()
    else:
        if change=="permission": grant.allow_booking=False
        elif change=="expire": grant.expires_at=timezone.now()
        else: grant.revoked_at=timezone.now()
        # Expiry remains after original created_at for the DB constraint.
        if change=="expire":
            grant.created_at-=timedelta(days=2); grant.token_expires_at=grant.expires_at
        grant.save()
    with pytest.raises(Http404): ap.confirm_booking(music["slug"], session, token, {}, True)
    assert not LessonBooking.objects.exists()


@pytest.mark.parametrize("mode",["bad", "expired", "browser", "family", "route", "contact"])
def test_quote_and_contact_tampering(music, offer, mode):
    session, token = prepare(music, offer)
    args = dict(slug=music["slug"], session=session, token=token, raw=CONTACT)
    if mode=="bad": args["token"]="garbage"
    elif mode=="expired":
        with patch("django.core.signing.time.time",return_value=1):
            args["token"]=signing.dumps(signing.loads(token,salt=ap.SALT),salt=ap.SALT)
    elif mode=="browser": args["session"]=SessionStore()
    elif mode=="route": args["expected_offer"]=uuid.uuid4()
    elif mode=="contact": args["raw"]={}
    else:
        grant, other=family(music); args.update(family=True,session=other)
    with pytest.raises(ValidationError): ap.confirm_booking(**args)
    assert not LessonBooking.objects.exists()


def test_availability_uses_constant_reads_and_never_includes_conflicts(music, offer):
    book(music)
    with CaptureQueriesContext(connection) as queries:
        rows=ap.day_slots(offer,date(2030,1,7),music["student"])
    assert len(queries) <= 8, len(queries)
    clocks=[r["starts_at"].astimezone(ZoneInfo(offer.location.timezone_name)).time() for r in rows]
    assert time(10) not in clocks and time(9,30) not in clocks and time(10,30) not in clocks
    assert time(11) in clocks
    closure=LessonClosure.objects.create(location=offer.location,first_date=date(2030,1,8),last_date=date(2030,1,8),reason="Private reason")
    assert not ap.day_slots(offer,date(2030,1,8))
    for day in [date(2029,12,31),date(2030,4,1)]:
        with pytest.raises(ValidationError): ap.day_slots(offer,day)
    offer.notice_hours=720
    assert not ap.day_slots(offer,date(2030,1,7))


def test_dst_gap_and_fold_are_not_offered(music, offer, monkeypatch):
    monkeypatch.setattr(timezone,"now",lambda:datetime(2030,3,1,tzinfo=utc.utc))
    clocks=[r["starts_at"].astimezone(ZoneInfo(offer.location.timezone_name)).time() for r in ap.day_slots(offer,date(2030,3,10))]
    assert time(2) not in clocks and time(3) in clocks
    monkeypatch.setattr(timezone,"now",lambda:datetime(2030,11,1,tzinfo=utc.utc))
    clocks=[r["starts_at"].astimezone(ZoneInfo(offer.location.timezone_name)).time() for r in ap.day_slots(offer,date(2030,11,3))]
    assert time(1) not in clocks and time(2) in clocks


def test_no_available_room_rechecked_and_no_room_supported(music, offer):
    session, token = prepare(music, offer)
    row = ap.day_slots(offer,date(2030,1,7))[20]
    starts=datetime(2030,1,7,10,tzinfo=ZoneInfo(offer.location.timezone_name))
    workshop=Offering.objects.create(location=offer.location,name="Workshop")
    Session.objects.create(offering=workshop,room=offer.room,starts_at=starts,ends_at=starts+timedelta(hours=1),capacity=2,is_published=True)
    with pytest.raises(ValidationError): ap.confirm_booking(music["slug"],session,token,CONTACT)
    offer.room=None;offer.save()
    item, _, _=booked(music,offer)
    assert item.series.room is None


def test_offer_form_scope_defaults_validation_and_stale_edit(music, offer):
    m=music
    assert not LessonBookingOffer().published and not FamilyScheduleGrant().allow_booking
    form=OfferForm(instance=offer,profile=m["profile"])
    revision=form.fields["edit_revision"].initial
    data=offer_data(offer, edit_revision=revision)
    saved=ap.save_offer(m["user"],m["slug"],data,offer.pk)
    assert saved.is_valid(), saved.errors
    offer.refresh_from_db(); assert offer.fee_cents==3000
    assert not ap.save_offer(m["user"],m["slug"],{**data,"title":"stale"},offer.pk).is_valid()
    other=SchedulingProfile.objects.create(school=School.objects.create(slug="elsewhere"),enabled=True)
    place=Location.objects.create(profile=other,name="Private")
    assert not ap.save_offer(m["user"],m["slug"],offer_data(offer,location=place.pk)).is_valid()
    for bad in [{"public_trials":"", "family_booking":""},{"teacher":9999},{"price":"1.234"},{"horizon_days":0}, {"interval_minutes":1}]:
        assert not ap.save_offer(m["user"],m["slug"],offer_data(offer,**bad)).is_valid()
    m["teacher"].services.clear()
    assert not ap.save_offer(m["user"],m["slug"],offer_data(offer)).is_valid()


def test_offer_model_cross_tenant_and_room_checks(music, offer):
    other=SchedulingProfile.objects.create(school=School.objects.create(slug="elsewhere"),enabled=True)
    place=Location.objects.create(profile=other,name="Private")
    room=Room.objects.create(location=place,name="Other room",capacity=2)
    offer.room=room
    with pytest.raises(ValidationError): offer.clean()
    offer.room=None;offer.location=place
    with pytest.raises(ValidationError): offer.clean()
    offer.save()
    with pytest.raises(Http404): ap.get_offer(music["profile"],offer.public_id)


@pytest.mark.parametrize("change",[{"fee_cents":100001},{"interval_minutes":1},{"notice_hours":721},{"horizon_days":0}])
def test_offer_db_constraints(music, offer, change):
    with pytest.raises(IntegrityError),transaction.atomic(): LessonBookingOffer.objects.filter(pk=offer.pk).update(**change)


@pytest.mark.parametrize("eligible", [False, True])
def test_staff_cancel_and_reschedule_saved_confirmation_once(music, offer, eligible):
    offer.makeup_eligible = eligible
    offer.save()
    item, session, token=booked(music,offer)
    occurrence=item.series.occurrences.get()
    data=dict(scope="one", teacher=offer.teacher_id,location=offer.location_id,room=offer.room_id,
        first_date="2030-01-08", start_time="11:00", reason="Private staffing note")
    form=RescheduleForm(data,occurrence=occurrence)
    assert form.is_valid(),form.errors
    rows, revision=lesson_changes.plan_change(occurrence,form.cleaned_data)
    change_token=lesson_changes.change_token(music["user"],occurrence,data,revision)
    replacement=lesson_changes.reschedule(music["user"],music["slug"],occurrence.pk,data,change_token)
    assert ap.current_series(item.series)==replacement and ap.source_booking(replacement)==item
    assert replacement.snapshot["makeup_eligible"] is eligible
    assert item.notices.count()==2 and "Private staffing note" not in item.notices.first().body
    lessons.cancel_lessons(music["user"],music["slug"],replacement.occurrences.get().pk,"one","Private cancellation")
    assert item.notices.count()==3 and "Canceled" in item.notices.first().body
    lessons.cancel_lessons(music["user"],music["slug"],replacement.occurrences.get().pk,"one","Private cancellation")
    assert item.notices.count()==3 and "Private cancellation" not in item.notices.first().body


@pytest.mark.parametrize("eligible,enabled,expected", [
    (False, True, "none"), (True, False, "none"), (True, True, "issued"),
])
def test_booking_credit_policy_is_explicit_and_snapshotted(music, offer, eligible, enabled, expected):
    assert LessonBookingOffer().makeup_eligible is False
    offer.makeup_eligible = eligible
    offer.save()
    policy = LessonCreditPolicy.objects.create(profile=music["profile"], enabled=enabled,
        family_cancellations=True, studio_cancellations=True)
    booking, _, _ = booked(music, offer)
    occurrence = booking.series.occurrences.get()
    offer.makeup_eligible = not eligible
    offer.save()
    assert credits.cancellation_outcome(occurrence, policy, "family")[0] == expected
    data = {"scope": "one", "origin": "family", "reason": "Test cancellation"}
    _, revision = credits.cancellation_plan(occurrence, "one", "family")
    token = credits.cancellation_token(music["user"], occurrence, data, revision)
    lessons.cancel_lessons(music["user"], music["slug"], occurrence.pk, **data, token=token)
    assert LessonCredit.objects.filter(source=occurrence).exists() == (expected == "issued")


def test_preexisting_preview_booking_without_eligibility_does_not_issue_credit(music, offer):
    booking, _, _ = booked(music, offer)
    del booking.series.snapshot["makeup_eligible"]
    booking.series.save(update_fields=["snapshot"])
    policy = LessonCreditPolicy(profile=music["profile"], enabled=True, studio_cancellations=True)
    assert credits.cancellation_outcome(booking.series.occurrences.get(), policy, "studio")[0] == "none"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same",[True,False])
def test_racing_bookings_and_retries(music, offer, same):
    session,token=prepare(music,offer)
    session2,token2=prepare(music,offer)
    def run(args):
        close_old_connections()
        try: return ap.confirm_booking(music["slug"],args[0],args[1],CONTACT).pk
        except ValidationError: return None
        finally: close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,[(session,token),(session,token) if same else (session2,token2)]))
    assert LessonBooking.objects.count()==LessonOccurrence.objects.count()==LessonBookingNotice.objects.count()==1
    assert sum(x is not None for x in results)==(2 if same else 1)


@pytest.mark.django_db(transaction=True)
def test_staff_and_public_booking_share_inventory_lock(music, offer):
    session,token=prepare(music,offer)
    _,staff_token=preview(music)
    def run(kind):
        close_old_connections()
        try:
            if kind=="public": ap.confirm_booking(music["slug"],session,token,CONTACT)
            else: lessons.create_series(music["user"],music["slug"],music["data"],staff_token)
            return True
        except ValidationError:return False
        finally:close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(run,["public","staff"]))
    assert results.count(True)==1 and LessonSeries.objects.count()==1
