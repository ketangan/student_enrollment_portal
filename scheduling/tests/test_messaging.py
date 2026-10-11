from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import close_old_connections, IntegrityError, transaction
from django.http import Http404
from django.utils import timezone

from core.models import School
from scheduling import messaging as msg, appointments as ap
from scheduling.message_models import BookingMessage, BookingMessageAttempt
from scheduling.models import SchedulingProfile
from scheduling.tests.test_lessons import music
from scheduling.tests.test_appointments import offer, booked, family, prepare

pytestmark = pytest.mark.django_db
PAYLOAD = dict(event_key="lesson:one:booked", group_key="lesson:one", recipient="guardian@example.invalid",
    subject="Lesson booked", body="Your lesson is confirmed. No staff-only notes.")


@pytest.fixture
def enabled(booking_profile, settings):
    settings.DEBUG = True
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.EMAIL_BACKEND = msg.LOCAL_BACKEND
    booking_profile.message_preview_enabled = True
    booking_profile.save()
    return booking_profile


def capture(profile, **kwargs):
    return msg.capture(profile, **{**PAYLOAD, **kwargs})


def due(message):
    BookingMessage.objects.filter(pk=message.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))


def test_capture_atomic_idempotent_and_no_enrollment_effect(enabled):
    before = School.objects.values().get(pk=enabled.school_id)
    message = capture(enabled)
    assert message.state == "queued" and not message.delivery_attempts.exists()
    assert capture(enabled).pk == message.pk
    with pytest.raises(ValidationError): capture(enabled, body="Changed")
    with pytest.raises(RuntimeError), transaction.atomic():
        capture(enabled, event_key="rolled-back", group_key="rollback")
        raise RuntimeError
    assert BookingMessage.objects.count() == 1
    assert School.objects.values().get(pk=enabled.school_id) == before


def test_default_off_and_disabled_transport_store_drafts(booking_profile, settings):
    assert not SchedulingProfile().message_preview_enabled
    message = capture(booking_profile)
    assert message.state == "draft"
    settings.DEBUG = True
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.EMAIL_BACKEND = msg.LOCAL_BACKEND
    assert msg.claim(booking_profile.pk) is None


@pytest.mark.parametrize("setting,value", [("DEBUG",False),("BOOKINGS_MESSAGE_PREVIEW",False),
    ("EMAIL_BACKEND","django.core.mail.backends.smtp.EmailBackend")])
def test_production_guards_fail_closed(enabled, settings, setting, value):
    message = capture(enabled)
    setattr(settings, setting, value)
    with patch("scheduling.messaging.get_school_email_connection") as connection:
        with pytest.raises(Http404): msg.process_batch(enabled.pk)
        with pytest.raises(Http404): msg.local_transport(message, "success")
        connection.assert_not_called()
    message.refresh_from_db()
    assert message.state == "queued" and message.attempts == 0


def test_simulator_reuses_shared_mail_factory_without_school_smtp(enabled, mailoutbox):
    enabled.school.smtp_host="do-not-contact.invalid"
    enabled.school.save()
    message = capture(enabled)
    with patch("scheduling.messaging.get_school_email_connection", wraps=msg.get_school_email_connection) as factory:
        assert msg.process_batch(enabled.pk)==1
        factory.assert_called_once_with()
    assert len(mailoutbox)==1 and mailoutbox[0].to==[PAYLOAD["recipient"]]
    assert "example.invalid" in mailoutbox[0].from_email
    assert str(message.public_id) in mailoutbox[0].extra_headers["Message-ID"]
    message.refresh_from_db()
    assert message.state=="simulated" and message.lease_token is None
    attempt=message.delivery_attempts.get()
    assert attempt.finished_at and attempt.outcome_label=="Simulated, not sent"
    assert attempt.result_label=="Accepted by the local simulator"
    assert msg.process_batch(enabled.pk)==0 and len(mailoutbox)==1


def test_retry_backoff_maximum_and_manual_budget(enabled, booking_member, booking_user):
    message=capture(enabled)
    for number in range(1,4):
        assert msg.process_batch(enabled.pk,outcome="retry")==1
        message.refresh_from_db()
        assert message.attempts==number
        assert message.state==("retry" if number<3 else "failed")
        assert msg.process_batch(enabled.pk)==0
        due(message)
    for number in (4,5):
        msg.change_message(booking_user,enabled.school.slug,message.public_id,"queue")
        msg.process_batch(enabled.pk,outcome="retry")
        message.refresh_from_db()
        assert message.attempts==number and message.state=="failed"
    assert not message.can_queue
    with pytest.raises(ValidationError): msg.change_message(booking_user,enabled.school.slug,message.public_id,"queue")


def test_unknown_acceptance_blocks_group_not_other_bookings(enabled, booking_member, booking_user):
    message=capture(enabled)
    msg.process_batch(enabled.pk,outcome="uncertain")
    latest=capture(enabled,event_key="lesson:one:canceled",subject="Lesson canceled")
    other=capture(enabled,event_key="other",group_key="lesson:other")
    assert msg.process_batch(enabled.pk)==1
    message.refresh_from_db(); latest.refresh_from_db();other.refresh_from_db()
    assert (message.state,latest.state,other.state)==("uncertain","queued","simulated")
    with pytest.raises(ValidationError): msg.change_message(booking_user,enabled.school.slug,message.public_id,"queue")
    msg.change_message(booking_user,enabled.school.slug,message.public_id,"suppress")
    assert msg.process_batch(enabled.pk)==1


def test_recover_stalled_claim_and_fence_late_worker(enabled):
    message=capture(enabled)
    claimed=msg.claim(enabled.pk)
    assert msg.claim(enabled.pk) is None
    BookingMessage.objects.filter(pk=message.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
    assert msg.claim(enabled.pk) is None
    message.refresh_from_db()
    assert message.state=="uncertain" and message.result_code=="worker_interrupted"
    assert msg.finish(claimed,"simulated") is False
    assert message.delivery_attempts.get().outcome=="uncertain"


def test_latest_update_supersedes_unsent_and_rejected_inflight(enabled):
    first=capture(enabled)
    latest=capture(enabled,event_key="change",subject="Rescheduled")
    first.refresh_from_db();assert first.state=="superseded"
    inflight=msg.claim(enabled.pk)
    cancellation=capture(enabled,event_key="cancel",subject="Canceled")
    assert msg.claim(enabled.pk) is None
    msg.finish(inflight,"retry")
    latest.refresh_from_db();assert latest.state=="superseded"
    assert msg.process_batch(enabled.pk)==1
    cancellation.refresh_from_db();assert cancellation.state=="simulated"


@pytest.mark.parametrize("kind",["zero","exception","wrong_backend"])
def test_transport_failure_classification_and_redaction(enabled,kind):
    message=capture(enabled)
    target="scheduling.messaging.EmailMessage.send"
    kwargs={"return_value":0} if kind=="zero" else {"side_effect":RuntimeError("private secret@example.com password")}
    if kind=="wrong_backend": target="scheduling.messaging.get_school_email_connection";kwargs={"return_value":object()}
    with patch(target,**kwargs): msg.process_batch(enabled.pk)
    message.refresh_from_db()
    assert message.state==("retry" if kind=="zero" else "uncertain")
    assert "secret" not in str(message.delivery_attempts.values().get())


def test_pause_and_tenant_scope(enabled,booking_member,booking_user):
    message=capture(enabled)
    other=SchedulingProfile.objects.create(school=School.objects.create(slug="other-message"),enabled=True)
    other_message=capture(other)
    with pytest.raises(Http404): msg.change_message(booking_user,enabled.school.slug,other_message.public_id,"queue")
    enabled.message_preview_enabled=False;enabled.save()
    assert msg.process_batch(enabled.pk)==0
    with pytest.raises(Http404): msg.change_message(booking_user,enabled.school.slug,other_message.public_id,"queue")


def test_suppress_draft_and_reject_repeat(enabled,booking_member,booking_user):
    enabled.message_preview_enabled=False;enabled.save()
    message=capture(enabled)
    with pytest.raises(ValidationError): msg.change_message(booking_user,enabled.school.slug,message.public_id,"queue")
    msg.change_message(booking_user,enabled.school.slug,message.public_id,"suppress")
    with pytest.raises(ValidationError): msg.change_message(booking_user,enabled.school.slug,message.public_id,"suppress")
    with pytest.raises(ValidationError): msg.change_message(booking_user,enabled.school.slug,message.public_id,"unknown")


@pytest.mark.parametrize("kwargs",[{"limit":0},{"limit":21},{"limit":True},{"outcome":"live"}])
def test_batch_bounds(enabled,kwargs):
    with pytest.raises(ValidationError): msg.process_batch(enabled.pk,**kwargs)


@pytest.mark.parametrize("change",[{"state":"bad"},{"state":"sending"},{"attempts":6}])
def test_database_constraints(enabled,change):
    message=capture(enabled)
    with pytest.raises(IntegrityError),transaction.atomic(): BookingMessage.objects.filter(pk=message.pk).update(**change)


def test_command_local_only(enabled,settings):
    capture(enabled)
    output=StringIO()
    call_command("process_booking_messages",school=enabled.school.slug,stdout=output)
    assert "Processed 1 local" in output.getvalue()
    for school,limit in [("missing",1),(enabled.school.slug,21)]:
        with pytest.raises(CommandError): call_command("process_booking_messages",school=school,limit=limit)
    settings.DEBUG=False
    with pytest.raises(CommandError): call_command("process_booking_messages",school=enabled.school.slug)


def test_booking_outbox_rollback_and_latest_notice(music,offer,settings):
    settings.BOOKINGS_MESSAGE_PREVIEW=True
    music["profile"].message_preview_enabled=True;music["profile"].save()
    with patch("scheduling.messaging.capture",side_effect=RuntimeError),pytest.raises(RuntimeError): booked(music,offer)
    assert not BookingMessage.objects.exists() and not ap.LessonBooking.objects.exists()
    booking,_,_=booked(music,offer)
    assert booking.notices.get().delivery.state=="queued"
    assert msg.process_batch(music["profile"].pk)==1


@pytest.mark.parametrize("change",["revoke","expire","move","email"])
def test_family_access_rechecked_before_processing(music,offer,settings,change,mailoutbox):
    settings.BOOKINGS_MESSAGE_PREVIEW=True
    music["profile"].message_preview_enabled=True;music["profile"].save()
    grant,session=family(music)
    session,token=prepare(music,offer,session,grant,music["student"])
    booking=ap.confirm_booking(music["slug"],session,token,{},True)
    if change=="revoke": grant.revoked_at=timezone.now();grant.save()
    elif change=="email": grant.recipient_email="changed@example.invalid";grant.save()
    elif change=="move": grant.students.clear()
    else:
        grant.created_at-=timedelta(days=2);grant.expires_at=timezone.now();grant.token_expires_at=grant.expires_at;grant.save()
    assert msg.process_batch(music["profile"].pk)==1
    assert booking.notices.get().delivery.state=="suppressed" and not mailoutbox


@pytest.mark.django_db(transaction=True)
def test_concurrent_workers_claim_each_message_once(enabled,mailoutbox):
    message=capture(enabled)
    def work(_):
        close_old_connections()
        try: return msg.process_batch(enabled.pk)
        finally: close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(work,range(2)))
    assert sum(results)==1 and len(mailoutbox)==1
    assert message.delivery_attempts.count()==1


def test_acknowledgement_failure_does_not_resend_accepted_message(enabled,mailoutbox):
    message=capture(enabled)
    with patch("scheduling.messaging.finish",side_effect=RuntimeError("db unavailable")),pytest.raises(RuntimeError):
        msg.process_batch(enabled.pk)
    assert len(mailoutbox)==1
    assert msg.process_batch(enabled.pk)==0
    BookingMessage.objects.filter(pk=message.pk).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
    assert msg.process_batch(enabled.pk)==0 and len(mailoutbox)==1
    message.refresh_from_db();assert message.state=="uncertain"


def test_claim_and_manual_action_rollback(enabled,booking_member,booking_user):
    message=capture(enabled)
    with patch("scheduling.messaging.BookingMessageAttempt.objects.create",side_effect=RuntimeError),pytest.raises(RuntimeError):
        msg.claim(enabled.pk)
    message.refresh_from_db();assert message.state=="queued" and message.attempts==0
    with patch("scheduling.messaging.audit",side_effect=RuntimeError),pytest.raises(RuntimeError):
        msg.change_message(booking_user,enabled.school.slug,message.public_id,"suppress")
    message.refresh_from_db();assert message.state=="queued"


def test_family_valid_confirmation_and_generic_reusability(music,offer,settings,mailoutbox):
    settings.BOOKINGS_MESSAGE_PREVIEW=True
    music["profile"].message_preview_enabled=True;music["profile"].save()
    grant,session=family(music)
    session,token=prepare(music,offer,session,grant,music["student"])
    ap.confirm_booking(music["slug"],session,token,{},True)
    capture(music["profile"],event_key="workshop:one:confirmed",group_key="workshop:one")
    assert msg.process_batch(music["profile"].pk)==2 and len(mailoutbox)==2


def test_disabled_workspace_never_processes(enabled):
    message=capture(enabled)
    enabled.enabled=False;enabled.save()
    assert msg.process_batch(enabled.pk)==0
    message.refresh_from_db();assert message.state=="queued"
