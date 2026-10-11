import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import Http404
from django.db import close_old_connections
from django.test import Client
from django.utils import timezone

from core.models import Lead, Submission
from scheduling import visits, visit_access as access, visit_operations as ops, visit_schedule as schedule, messaging
from scheduling.models import VisitPilotSettings, VisitAccessGrant, VisitSlot, Session, VisitScheduleBatch, VisitBooking
from scheduling.lesson_models import LessonClosure
from scheduling.message_models import BookingMessage
from .test_visits import tour, book, booking_data, post_data, url
from .test_admissions import workflow, completed_tour, eligible_case, reserve, core_url

pytestmark = pytest.mark.django_db


@pytest.fixture
def pilot(tour, settings):
    settings.DEBUG = True
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.EMAIL_BACKEND = messaging.LOCAL_BACKEND
    settings.APP_BASE_URL = "http://127.0.0.1:8002"
    settings.DEMO_BASE_URL = "http://127.0.0.1:8002"
    profile = tour.host.profile
    profile.message_preview_enabled = True
    profile.save()
    return VisitPilotSettings.objects.create(profile=profile, contact_email="office@example.invalid", family_changes=True)


def later(tour, days=1):
    start = tour.session.starts_at+timedelta(days=days)
    session = Session.objects.create(offering=tour.program.offering, room=tour.session.room, capacity=4,
        starts_at=start, ends_at=start+timedelta(minutes=30), is_published=True)
    return VisitSlot.objects.create(program=tour.program, session=session, host=tour.host)


def change(item, **kwargs):
    return ops.change(item.slot.host.profile, item.public_id, version=item.version, user=item.slot.host.user, **kwargs)


def secret(grant):
    return re.search(r"#([A-Za-z0-9_-]{43})", grant.delivery.body).group(1)


def test_return_access_is_single_use_scoped_revocable_and_throttled(tour, pilot):
    item = book(tour)
    assert BookingMessage.objects.count() == 1
    slug = pilot.profile.school.slug
    access.request_access(slug, "TAYLOR@example.invalid")
    grant = VisitAccessGrant.objects.get()
    assert secret(grant) not in grant.token_hash
    access.request_access(slug, "taylor@example.invalid")
    access.request_access(slug, "unknown@example.invalid")
    assert VisitAccessGrant.objects.count() == 1
    assert messaging.recipient_allowed(grant.delivery)
    session = SessionStore()
    assert access.redeem(slug, secret(grant), session) == item
    assert not messaging.recipient_allowed(grant.delivery)
    req = SimpleNamespace(session=session)
    assert access.authorized(req, pilot.profile, item.public_id) == item
    with pytest.raises(ValidationError):
        access.redeem(slug, secret(grant), SessionStore())
    access.revoke(pilot.profile, item.public_id, session)
    with pytest.raises(Http404):
        access.authorized(req, pilot.profile, item.public_id)
    assert not session[access.SESSION_KEY]


@pytest.mark.parametrize("invalid", ["expired", "revoked", "email", "malformed"])
def test_access_rejects_invalid_links(tour, pilot, invalid):
    book(tour)
    access.request_access(pilot.profile.school.slug, "taylor@example.invalid")
    grant = VisitAccessGrant.objects.get()
    token = secret(grant)
    if invalid == "expired":
        grant.token_expires_at = timezone.now()-timedelta(seconds=1)
        grant.save()
    elif invalid == "revoked":
        grant.revoked_at = timezone.now()
        grant.save()
    elif invalid == "email":
        VisitBooking.objects.filter(pk=grant.booking_id).update(contact_email="changed@example.invalid")
    else:
        token = "bad"
    with pytest.raises(ValidationError):
        access.redeem(pilot.profile.school.slug, token, SessionStore())
    if invalid != "malformed":
        assert not messaging.recipient_allowed(grant.delivery)


def test_new_link_revokes_older_unused_link_and_no_default_activation(tour):
    item = book(tour)
    assert not BookingMessage.objects.exists()
    with pytest.raises(Http404):
        access.request_access(tour.host.profile.school.slug, item.contact_email)


def test_superseded_return_links_and_staff_revocation(tour, pilot):
    item = book(tour)
    slug = pilot.profile.school.slug
    access.request_access(slug, item.contact_email)
    first = VisitAccessGrant.objects.get()
    VisitAccessGrant.objects.update(created_at=timezone.now()-timedelta(minutes=3))
    access.request_access(slug, item.contact_email)
    first.refresh_from_db()
    assert first.revoked_at and VisitAccessGrant.objects.count() == 2
    access.revoke(pilot.profile, item.public_id)
    assert not VisitAccessGrant.objects.filter(revoked_at=None).exists()


def test_atomic_move_preserves_identity_and_original_retry(tour, pilot):
    tour.program.capture_leads = True
    tour.program.save()
    data = booking_data(tour)
    item, _ = visits.book(pilot.profile.school.slug, tour.pk, data, "browser-a")
    old_order = item.reservation.order_id
    target = later(tour)
    result = change(item, action="move", target=target.pk)
    assert result.slot == target and result.version == 1
    assert visits.places_left(tour) == 4 and visits.places_left(target) == 2
    assert result.lead_id == item.lead_id and result.reservation.order_id == old_order
    assert visits.book(pilot.profile.school.slug, tour.pk, data, "browser-a")[0] == item
    with pytest.raises(ValidationError, match="changed"):
        change(item, action="cancel")
    with pytest.raises(ValidationError, match="changed"):
        visits.change_state(tour.host.user, pilot.profile.school.slug, item.public_id, "canceled", "booked", 0)


def test_move_full_slot_rolls_back_then_cancel_reopen(tour, pilot):
    item = book(tour)
    target = later(tour)
    book(target, adults="3")
    with pytest.raises(ValidationError):
        change(item, action="move", target=target.pk)
    item.refresh_from_db()
    assert item.slot_id == tour.pk and item.version == 0
    canceled = change(item, action="cancel", family=True)
    assert visits.places_left(tour) == 4
    reopened = change(canceled, action="booked", reason="Canceled by mistake")
    assert reopened.state == "booked" and visits.places_left(tour) == 2


@pytest.mark.parametrize("case", ["deadline", "off", "past", "canceled"])
def test_family_change_policy(tour, pilot, case):
    item = book(tour)
    if case == "deadline":
        pilot.notice_hours = 168
        pilot.save()
    elif case == "off":
        pilot.family_changes = False
        pilot.save()
    elif case == "past":
        Session.objects.filter(pk=tour.session_id).update(starts_at=timezone.now()-timedelta(hours=1))
    else:
        VisitBooking.objects.filter(pk=item.pk).update(state="canceled")
    with pytest.raises(ValidationError, match="contact the school"):
        change(item, action="cancel", family=True)


@pytest.mark.parametrize("action,reason", [("bad", ""), ("attended", ""), ("attended", "Mistake"), ("no_show", "Mistake")])
def test_invalid_or_early_corrections(tour, action, reason):
    with pytest.raises(ValidationError):
        change(book(tour), action=action, reason=reason)


def test_correction_updates_only_owned_lead_status(tour):
    tour.program.capture_leads = True
    tour.program.save()
    item = book(tour)
    Session.objects.filter(pk=tour.session_id).update(starts_at=timezone.now()-timedelta(hours=1), ends_at=timezone.now()-timedelta(minutes=30))
    attended = change(item, action="attended", reason="Checked register")
    assert Lead.objects.get(pk=item.lead_id).status == "trial_completed"
    corrected = change(attended, action="no_show", reason="Wrong family")
    assert Lead.objects.get(pk=item.lead_id).status == "new"
    scheduled = change(corrected, action="booked", reason="Undo correction")
    Lead.objects.filter(pk=item.lead_id).update(status="lost")
    change(scheduled, action="attended", reason="Actually attended")
    assert Lead.objects.get(pk=item.lead_id).status == "lost"


def test_correction_preserves_submitted_tour_and_playdate_status(workflow):
    w = workflow
    case = eligible_case(w)
    with pytest.raises(ValidationError, match="submitted application"):
        change(case.tour, action="no_show", reason="Wrong family")
    item, _ = reserve(w, case)
    Session.objects.filter(pk=w.slot.session_id).update(starts_at=timezone.now()-timedelta(hours=1), ends_at=timezone.now()-timedelta(minutes=30))
    attended = change(item, action="attended", reason="Attended")
    assert Submission.objects.get(pk=case.submission_id).status == "Playdate Completed"
    corrected = change(attended, action="booked", reason="Wrong family")
    assert Submission.objects.get(pk=case.submission_id).status == "Playdate Scheduled"
    Submission.objects.filter(pk=case.submission_id).update(status="Enrolled")
    change(corrected, action="no_show", reason="Correction")
    assert Submission.objects.get(pk=case.submission_id).status == "Enrolled"


def test_playdate_confirmation_supersedes_pending_invitation(workflow, pilot):
    case = eligible_case(workflow)
    invitation = access.notice(case.tour, "Playdate booking available")
    item, _ = reserve(workflow, case)
    invitation.refresh_from_db()
    assert invitation.state == "superseded"
    message = BookingMessage.objects.get(event_key=f"visit:{item.public_id}:0:Visit confirmed")
    assert message.state == "queued" and message.group_key == invitation.group_key


@pytest.mark.parametrize("action", ["booked", "attended"])
def test_cannot_restore_missed_playdate_after_replacement(workflow, action):
    w = workflow
    case = eligible_case(w)
    item, _ = reserve(w, case)
    Session.objects.filter(pk=w.slot.session_id).update(starts_at=timezone.now()-timedelta(hours=1), ends_at=timezone.now()-timedelta(minutes=30))
    missed = change(item, action="no_show", reason="Family missed their visit")
    w.slot = later(w.slot, days=7)
    replacement, _ = reserve(w, case)
    with pytest.raises(ValidationError, match="already has another playdate"):
        change(missed, action=action, reason="Correct old attendance")
    missed.refresh_from_db()
    replacement.refresh_from_db()
    assert missed.state == "no_show" and replacement.state == "booked"
    assert Submission.objects.get(pk=case.submission_id).status == "Playdate Scheduled"


def schedule_data(tour, **updates):
    local = timezone.localtime(tour.session.starts_at, ZoneInfo(tour.program.offering.location.timezone_name)) + timedelta(days=1)
    return {"first_date": local.date(), "last_date": (local+timedelta(days=14)).date(), "start_time": "10:00",
        "duration": 30, "interval": 1, "room": tour.session.room_id, "host": tour.host_id, "capacity": 4, **updates}


def preview(tour, data, token=None):
    return schedule.schedule(tour.host.user, tour.host.profile.school.slug, tour.program_id, data, token)


def test_repeat_schedule_preview_skip_closure_and_duplicate_commit(tour):
    data = schedule_data(tour)
    closure_day = data["first_date"]+timedelta(days=7)
    LessonClosure.objects.create(location=tour.program.offering.location, first_date=closure_day, last_date=closure_day, reason="Closed")
    form, rows, token, done = preview(tour, data)
    assert form.is_valid() and not done and [r["state"] for r in rows] == ["Open", "Skipped", "Open"]
    assert preview(tour, data, token)[3]
    assert VisitSlot.objects.count() == 3 and VisitScheduleBatch.objects.count() == 1
    assert preview(tour, data, token)[3] and VisitSlot.objects.count() == 3
    with pytest.raises(ValidationError, match="different values"):
        preview(tour, {**data, "capacity": 3}, token)


def test_repeat_plan_stale_conflict_and_bad_token(tour):
    data = schedule_data(tour)
    _, _, token, _ = preview(tour, data)
    preview(tour, data, token)
    assert not preview(tour, data)[2]
    with pytest.raises(ValidationError, match="expired"):
        preview(tour, data, "bad")
    changed = schedule_data(tour, start_time="12:00")
    _, _, token, _ = preview(tour, changed)
    with pytest.raises(ValidationError, match="Availability changed"):
        preview(tour, {**changed, "capacity": 3}, token)
    assert VisitSlot.objects.count() == 4


@pytest.mark.parametrize("updates", [{"first_date": "bad"}, {"last_date": "2000-01-01"}, {"capacity": 20}, {"room": 9999}, {"interval": 3}])
def test_invalid_repeat_fields(tour, updates):
    assert not preview(tour, schedule_data(tour, **updates))[0].is_valid()


def test_private_browser_recovery_and_family_changes(tour, pilot, client):
    request_url = url(tour, "visit_book", pk=tour.pk)
    first = client.get(request_url)
    result = client.post(request_url, booking_data(tour, booking_token=first.context["booking_token"]))
    assert result.status_code == 302
    other = Client()
    assert other.get(result.url).status_code == 404
    recover_url = url(tour, "visit_recover")
    assert other.get(recover_url).status_code == 200
    assert other.post(recover_url, {"email": "bad"}).status_code == 400
    known = other.post(recover_url, {"email": "taylor@example.invalid"})
    unknown = other.post(recover_url, {"email": "unknown@example.invalid"})
    assert known.context["done"] == unknown.context["done"] is True
    grant = VisitAccessGrant.objects.get()
    entry_url = url(tour, "visit_entry")
    assert other.get(entry_url).status_code == 200
    assert other.post(entry_url, {"access_token": "bad"}).status_code == 400
    response = other.post(entry_url, {"access_token": secret(grant)})
    page = other.get(response.url)
    assert page.status_code == 200 and "no-store" in page["Cache-Control"]
    assert "frame-ancestors 'none'" in page["Content-Security-Policy"]
    item = grant.booking
    target = later(tour)
    change_url = url(tour, "visit_family_change", public_id=item.public_id)
    assert other.post(change_url, {"action": "move", "version": 0, "target": target.pk}).status_code == 302
    assert other.post(change_url, {"action": "cancel", "version": 0}).status_code == 400
    assert other.post(change_url, {"action": "signout"}).status_code == 302
    assert other.get(result.url).status_code == 404


def test_setup_repeat_closure_staff_ui_and_viewer_denial(tour, pilot, client):
    client.force_login(tour.host.user)
    setup_url = url(tour, "visit_setup")
    page = client.get(setup_url)
    assert page.status_code == 200
    data = post_data(page.context["form"], contact_email="school@example.invalid")
    assert client.post(setup_url, data).status_code == 302
    assert client.post(setup_url, data).status_code == 400
    repeat_url = url(tour, "visit_repeat", program_pk=tour.program_id)
    assert client.get(repeat_url).status_code == 200
    assert client.post(repeat_url, {}).status_code == 400
    data = schedule_data(tour)
    page = client.post(repeat_url, data)
    assert page.status_code == 200 and page.context["preview_token"]
    assert client.post(repeat_url, {**data, "action": "publish", "preview_token": "bad"}).status_code == 400
    assert client.post(repeat_url, {**data, "action": "publish", "preview_token": page.context["preview_token"]}).status_code == 302
    item = book(tour)
    action_url = url(tour, "visit_staff_change", public_id=item.public_id)
    assert b"Enter a reason" in client.post(action_url, {"action": "attended", "version": 0}, follow=True).content
    assert client.post(action_url, {"action": "revoke"}).status_code == 302
    assert client.post(action_url, {"action": "cancel", "version": 0}).status_code == 302
    closure_url = url(tour, "visit_closure")
    assert client.get(closure_url).status_code == 200
    day = data["first_date"]
    assert client.post(closure_url, {"location": tour.program.offering.location_id, "first_date": day, "last_date": day, "reason": "Holiday"}).status_code == 302
    closure = LessonClosure.objects.get()
    edit_url = url(tour, "visit_closure_edit", pk=closure.pk)
    edit_page = client.get(edit_url)
    edit_data = post_data(edit_page.context["form"], reason="Staff training")
    assert client.post(edit_url, edit_data).status_code == 302
    closure.refresh_from_db()
    assert closure.reason == "Staff training"
    assert client.post(edit_url, edit_data).status_code == 400
    assert client.get(url(tour, "visit_closure_edit", pk=999999)).status_code == 404
    tour.host.role = "viewer"
    tour.host.save()
    for path in [setup_url, repeat_url, closure_url, edit_url]:
        assert client.get(path).status_code == 404
    assert client.post(action_url, {"action": "revoke"}).status_code == 404


def test_closure_refuses_booked_visits(tour, pilot, client):
    book(tour)
    client.force_login(tour.host.user)
    day = timezone.localtime(tour.session.starts_at, ZoneInfo(tour.program.offering.location.timezone_name)).date()
    response = client.post(url(tour, "visit_closure"), {"location": tour.program.offering.location_id,
        "first_date": day, "last_date": day, "reason": "Holiday"})
    assert response.status_code == 400 and b"Move or cancel booked visits" in response.content
    assert not LessonClosure.objects.exists()


def test_verified_return_opens_same_application_and_blocks_demo_payment(workflow, pilot, client, monkeypatch):
    w = workflow
    case = completed_tour(w)
    access.request_access(w.school.slug, case.lead.email)
    grant = VisitAccessGrant.objects.get()
    response = client.post(url(w.tour, "visit_entry"), {"access_token": secret(grant)})
    assert response.status_code == 302
    response = client.post(url(w.tour, "visit_application", public_id=case.tour.public_id))
    assert response.status_code == 302
    case.refresh_from_db()
    assert case.draft_id
    assert client.get(response.url, follow=True).status_code == 200
    assert client.get(core_url(w, "apply_payment_confirm", draft_token=case.draft.token), {"payment_intent": "pi_wrong"}).status_code == 403
    assert not Submission.objects.exists()
    from core.services.config_loader import SchoolConfig, load_school_config
    config = load_school_config(w.school.slug)
    monkeypatch.setattr("core.services.config_loader.load_school_config", lambda slug: SchoolConfig(raw={**config.raw,
        "application_fee": {"enabled": True, "amount": 50}}))
    assert client.get(core_url(w, "apply_resume", token=case.draft.token)).status_code == 403


def test_staff_access_link_preview_is_private(tour, pilot, client):
    item = book(tour)
    access.request_access(pilot.profile.school.slug, item.contact_email)
    grant = VisitAccessGrant.objects.get()
    client.force_login(tour.host.user)
    path = url(tour, "message_detail", public_id=grant.delivery.public_id)
    assert secret(grant).encode() in client.get(path).content
    tour.host.role = "viewer"
    tour.host.save()
    assert secret(grant).encode() not in client.get(path).content


@pytest.mark.parametrize("action", ["move", "cancel", "attended"])
def test_canceled_booking_invalid_actions(tour, action):
    item = change(book(tour), action="cancel")
    with pytest.raises(ValidationError):
        change(item, action=action, reason="Correction", target=tour.pk)


def test_invalid_move_and_reopening_closed_slot(tour):
    item = book(tour)
    for target in ["", "bad", tour.pk]:
        with pytest.raises(ValidationError):
            change(item, action="move", target=target)
    item = change(item, action="cancel")
    Session.objects.filter(pk=tour.session_id).update(is_published=False)
    with pytest.raises(ValidationError, match="no longer available"):
        change(item, action="booked", reason="Undo")


def test_repeat_rejects_cross_midnight_and_wrong_actor(tour):
    data = schedule_data(tour, start_time="23:45", duration=60)
    with pytest.raises(ValidationError, match="same day"):
        preview(tour, data)
    data = schedule_data(tour)
    _, _, token, _ = preview(tour, data)
    value = signing.loads(token, salt=schedule.SALT)
    value["actor"] += 100
    with pytest.raises(ValidationError, match="expired"):
        preview(tour, data, signing.dumps(value, salt=schedule.SALT))


@pytest.mark.django_db(transaction=True)
def test_concurrent_reschedule_and_new_booking_cannot_overfill(tour):
    original = book(tour)
    target = later(tour)
    Session.objects.filter(pk=target.session_id).update(capacity=2)
    target.session.refresh_from_db()
    data = booking_data(target, contact_email="second@example.invalid")
    barrier = Barrier(2)
    def attempt(kind):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            if kind == "move":
                change(original, action="move", target=target.pk)
            else:
                visits.book(tour.host.profile.school.slug, target.pk, data, "browser-a")
            return True
        except ValidationError:
            return False
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(attempt, ["move", "book"]))
    assert sorted(result) == [False, True]
    assert visits.places_left(target) == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_repeat_commit_is_idempotent(tour):
    data = schedule_data(tour)
    _, _, token, _ = preview(tour, data)
    barrier = Barrier(2)
    def attempt(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return preview(tour, data, token)[3]
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(attempt, range(2))) == [True, True]
    assert VisitScheduleBatch.objects.count() == 1 and VisitSlot.objects.count() == 4
