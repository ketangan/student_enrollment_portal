from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from zoneinfo import ZoneInfo
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import Client
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import AdminAuditLog
from core.payments.models import PaymentAccount, PaymentAttempt, Refund
from core.payments.service import totals
from scheduling import messaging, session_changes, workshop_notifications
from scheduling.message_models import BookingMessage
from scheduling.models import Location, Room, SchedulingMembership, Session
from scheduling.tests.test_inventory import stock, reserve, confirm, url, request as workshop_request
from scheduling.tests.test_calendar import enable

pytestmark = pytest.mark.django_db
HEADER = {"HTTP_X_CALENDAR_EDITOR": "1"}


def booked(s, quantity=1):
    item = reserve(s, quantity)
    confirm(s, item)
    item.refresh_from_db()
    return item


def edit_data(s, **changes):
    page = s.client.get(url(s, "session_edit", pk=s.session.pk), **HEADER)
    form = page.context["form"]
    data = {field.name: field.value() or "" for field in form.hidden_fields()}
    data.update(starts_at=timezone.localtime(s.session.starts_at + timedelta(days=1), ZoneInfo(s.location.timezone_name)).strftime("%Y-%m-%dT%H:%M"),
        duration_minutes=60, room=s.room.pk, capacity=100)
    return {**data, **changes}


def cancel_data(s, **changes):
    page = s.client.get(url(s, "session_cancel", pk=s.session.pk), **HEADER)
    data = {field.name: field.value() or "" for field in page.context["form"].hidden_fields()}
    return {**data, "resolution": "without_refund", "reason": "", "email_customers": "on", **changes}


def review(s, action, data):
    path = url(s, "session_cancel" if action == "cancel" else "session_edit", pk=s.session.pk)
    response = s.client.post(path, data, **HEADER)
    assert response.status_code == 200, response.context["form"].errors
    assert response.context["preview"]
    return path, {**data, "review_token": response.context["form"]["review_token"].value()}


@pytest.fixture
def notifications(stock, settings):
    settings.DEBUG = True
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    stock.profile.workshop_notifications_enabled = True
    stock.profile.message_preview_enabled = True
    stock.profile.save()
    enable(stock)
    return stock


def test_move_reviews_every_booking_preserves_money_and_notifies_once(notifications):
    s = notifications
    first, second = booked(s, 2), booked(s)
    workshop_notifications.capture(first)
    original = [(i.starts_at, totals(i.order), list(i.order.entries.values())) for i in (first, second)]
    data = edit_data(s)
    path, accepted = review(s, "edit", data)
    first.refresh_from_db()
    assert first.starts_at == original[0][0]
    assert s.client.post(path, accepted, **HEADER).json() == {"saved": True}
    assert s.client.post(path, accepted, **HEADER).json() == {"saved": True}
    s.session.refresh_from_db()
    for i, before in zip((first, second), original):
        i.refresh_from_db()
        i.workshop_request.refresh_from_db()
        assert i.starts_at == s.session.starts_at != before[0]
        assert datetime.fromisoformat(i.workshop_request.session_snapshot["starts_at"]) == i.starts_at
        assert totals(i.order) == before[1] and list(i.order.entries.values()) == before[2]
        assert workshop_notifications.capture(i) is None
    assert BookingMessage.objects.filter(notice_kind="rescheduled").count() == 2
    assert BookingMessage.objects.get(notice_kind="confirmation").state == "superseded"
    assert messaging.process_batch(s.profile.pk) == 2
    assert BookingMessage.objects.filter(state="simulated").count() == 2
    assert AdminAuditLog.objects.filter(model_label="scheduling.session", extra__session_change_key__isnull=False).count() == 1


def test_availability_preview_blocks_conflict_without_writes(notifications):
    s = notifications
    item = booked(s)
    data = edit_data(s)
    start = s.session.starts_at + timedelta(days=1)
    conflict = Session.objects.create(offering=s.offering, room=s.room,
        starts_at=start, ends_at=start + timedelta(hours=1), capacity=10, is_published=True)
    path = url(s, "session_edit", pk=s.session.pk)
    response = s.client.post(path, data, HTTP_X_SESSION_PREFLIGHT="1")
    assert response.status_code == 400
    assert response.json()["valid"] is False
    assert "This room already has a published session" in str(response.json()["errors"])
    item.refresh_from_db()
    assert item.starts_at == s.session.starts_at
    assert not BookingMessage.objects.filter(notice_kind="rescheduled").exists()
    conflict.delete()
    response = s.client.post(path, data, HTTP_X_SESSION_PREFLIGHT="1")
    result = response.json()
    assert result['valid'] is True and result['errors'] == {}
    assert result['review_token']
    assert '1 booking affected' in result['review_html']
    assert item.workshop_request.purchaser_email in result['review_html']
    s.session.refresh_from_db()
    assert s.session.starts_at == item.starts_at
    assert not AdminAuditLog.objects.filter(extra__session_change_key__isnull=False).exists()
    # The read-only live review authorizes one explicit Save, not another review.
    accepted = {**data, 'review_token': result['review_token']}
    assert s.client.post(path, accepted, **HEADER).json() == {'saved': True}
    item.refresh_from_db()
    assert item.starts_at == start.replace(second=0, microsecond=0)
    assert s.client.post(path, accepted, **HEADER).json() == {'saved': True}


def test_live_review_cannot_authorize_different_values(notifications):
    s = notifications
    item = booked(s)
    data = edit_data(s)
    path = url(s, 'session_edit', pk=s.session.pk)
    result = s.client.post(path, data, HTTP_X_SESSION_PREFLIGHT='1').json()
    response = s.client.post(path, {**data, 'duration_minutes': 90,
        'review_token': result['review_token']}, **HEADER)
    assert response.context['preview']
    item.refresh_from_db()
    assert item.starts_at == s.session.starts_at


def test_notification_can_be_disabled_without_affecting_reservation_move(notifications):
    s = notifications
    item = booked(s)
    path, data = review(s, "edit", edit_data(s, notify_customers="0"))
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    item.refresh_from_db()
    s.session.refresh_from_db()
    assert item.starts_at == s.session.starts_at
    assert not BookingMessage.objects.filter(notice_kind="rescheduled").exists()


def test_unassigned_room_does_not_bypass_overlapping_workshop_check(notifications):
    s = notifications
    start = s.session.starts_at + timedelta(days=1)
    Session.objects.create(offering=s.offering, starts_at=start,
        ends_at=start + timedelta(hours=1), capacity=10, is_published=True)
    response = s.client.post(url(s, 'session_edit', pk=s.session.pk), edit_data(s), HTTP_X_SESSION_PREFLIGHT='1')
    assert response.status_code == 400
    assert 'without a separate room' in str(response.json()['errors'])


@pytest.mark.parametrize("headers", [{}, HEADER])
def test_session_editor_shows_compact_customer_contacts(notifications, headers):
    from django.utils.html import escape

    s = notifications
    item = booked(s, 2)
    source = item.workshop_request
    source.purchaser_name = "<script>Customer</script>"
    source.purchaser_email = "customer@example.invalid"
    source.purchaser_phone = "(626) 555-0100"
    source.save()
    response = s.client.get(url(s, "session_edit", pk=s.session.pk), **headers)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'aria-label="Customers"' in html
    assert escape(source.purchaser_name) in html
    assert source.purchaser_name not in html
    assert source.purchaser_email in html and source.purchaser_phone not in html
    assert 'class="session-customer-seats">2 seats' in html
    assert 'data-notify-customers checked' in html


def test_move_review_shows_contacts_without_payment_or_local_email_noise(notifications):
    s = notifications
    first, second = booked(s, 2), booked(s)
    first.workshop_request.purchaser_phone = "(626) 555-0100"
    first.workshop_request.save()
    second.workshop_request.purchaser_phone = ""
    second.workshop_request.save()
    response = s.client.post(url(s, "session_edit", pk=s.session.pk), edit_data(s), **HEADER)
    assert response.status_code == 200
    html = response.content.decode()
    assert "2 bookings affected" in html and "(626) 555-0100" not in html
    assert 'aria-label="Affected customers"' in html
    assert first.workshop_request.purchaser_email in html
    assert "Payment received" not in html and "Confirmed" not in html
    assert "Email updates unavailable" not in html and "No customer emails" not in html
    assert not response.context["preview"]["show_payments"]
    assert response.context["preview"]["email_unavailable"] == 0
    first.refresh_from_db()
    assert first.starts_at == s.session.starts_at


@pytest.mark.parametrize("paid", [False, True])
def test_cancel_review_only_shows_money_when_relevant(notifications, paid):
    s = notifications
    booked(s) if paid else reserve(s)
    response = s.client.post(url(s, "session_cancel", pk=s.session.pk), cancel_data(s), **HEADER)
    assert response.status_code == 200
    assert (b"Payment received" in response.content) == paid
    assert (b"No refunds will be issued" in response.content) == paid
    assert b'class="booking-danger booking-danger-solid"' in response.content
    assert response.context["preview"]["show_payments"] == paid


@pytest.mark.parametrize("delivery", [False, True])
def test_hosted_review_does_not_hide_unavailable_email(notifications, settings, monkeypatch, delivery):
    from scheduling import session_notifications
    s = notifications
    settings.BOOKINGS_MESSAGE_PREVIEW = False
    monkeypatch.setattr(session_notifications, "delivery_enabled", lambda item: delivery)
    booked(s)
    response = s.client.post(url(s, "session_edit", pk=s.session.pk), edit_data(s), **HEADER)
    assert response.status_code == 200
    assert (b"Email updates unavailable for 1 booking." in response.content) == (not delivery)
    assert b"No customer emails" not in response.content


@pytest.mark.parametrize("change", ["duration", "room"])
def test_review_describes_duration_and_room_changes_without_identical_start_times(notifications, change):
    s = notifications
    booked(s)
    values = {"starts_at": timezone.localtime(s.session.starts_at, ZoneInfo(s.location.timezone_name)).strftime("%Y-%m-%dT%H:%M")}
    if change == "duration":
        values["duration_minutes"] = 90
    else:
        values["room"] = ""
    response = s.client.post(url(s, "session_edit", pk=s.session.pk), edit_data(s, **values), **HEADER)
    assert response.status_code == 200 and response.context["preview"]
    html = response.content.decode().split('data-session-review>', 1)[1]
    assert 'class="session-change-highlight"' in html
    assert '<del>' in html
    assert ('<span class="muted">Time</span>' in html) == (change == "duration")
    assert html.index('class="session-change-highlight"') < html.index('data-notify-customers')
    assert ("<strong>Unassigned</strong>" in html) == (change == "room")


@pytest.mark.parametrize("change", ["payment", "customer", "capacity", "new_booking", "state"])
def test_changed_review_rejects_whole_operation(notifications, change):
    s = notifications
    item = booked(s)
    original = item.starts_at
    path, data = review(s, "edit", edit_data(s))
    if change == "payment":
        from core.payments.service import record_offline
        record_offline(order=item.order, actor=s.user, key=uuid.uuid4(), kind="refund", amount_cents=1, reference="Correction")
    elif change == "customer":
        source = item.workshop_request
        source.purchaser_email = "changed@example.invalid"
        source.save()
    elif change == "capacity":
        Session.objects.filter(pk=s.session.pk).update(capacity=99)
    elif change == "new_booking":
        booked(s)
    else:
        item.state = "canceled"
        item.save()
    response = s.client.post(path, data, **HEADER)
    assert response.status_code == 400 and b"changed" in response.content
    item.refresh_from_db()
    assert item.starts_at == original
    assert not BookingMessage.objects.filter(notice_kind="rescheduled").exists()


@pytest.mark.parametrize("block", ["capacity", "room", "pending_request", "payment", "completed", "past"])
def test_invalid_move_has_no_partial_effect(notifications, block):
    s = notifications
    item = booked(s, 2)
    data = edit_data(s)
    if block == "capacity":
        data["capacity"] = 1
    elif block == "room":
        Session.objects.create(offering=s.offering, room=s.room, capacity=10, is_published=True,
            starts_at=s.session.starts_at + timedelta(days=1), ends_at=s.session.ends_at + timedelta(days=1))
    elif block == "pending_request":
        workshop_request(s)
    elif block == "payment":
        account = PaymentAccount.objects.create(school=s.school, reference="acct_test")
        PaymentAttempt.objects.create(order=item.order, account=account, state="pending", amount_cents=100,
            expires_at=timezone.now() + timedelta(minutes=15))
        data = edit_data(s)
    elif block == "completed":
        item.state = "completed"
        item.save()
        data = edit_data(s)
    else:
        data["starts_at"] = (timezone.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    response = s.client.post(url(s, "session_edit", pk=s.session.pk), data, **HEADER)
    assert response.status_code == 400
    item.refresh_from_db()
    assert item.starts_at == s.session.starts_at
    assert not BookingMessage.objects.exists()


def test_cancel_without_refund_preserves_money_and_hides_session(notifications):
    s = notifications
    item = booked(s)
    before = totals(item.order)
    path, data = review(s, "cancel", cancel_data(s))
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    item.refresh_from_db(); s.session.refresh_from_db()
    assert item.state == "canceled" and s.session.canceled_at and not s.session.is_published
    assert totals(item.order) == before and not Refund.objects.exists()
    assert messaging.process_batch(s.profile.pk) == 1
    assert "refund has been requested" not in BookingMessage.objects.get().body
    from scheduling.setup import set_publication
    from django.http import Http404
    with pytest.raises(Http404):
        set_publication(s.user, s.school.slug, "sessions", s.session.pk, True)
    events = s.client.get(url(s, "calendar_events"), {"start": s.session.starts_at.date().isoformat(),
        "end": (s.session.starts_at + timedelta(days=1)).date().isoformat()}).json()["events"]
    assert not events
    assert s.client.get(url(s, "calendar_detail", kind="session", pk=s.session.pk)).status_code == 404


def test_external_payment_cannot_be_silently_refunded(notifications):
    s = notifications
    item = booked(s)
    response = s.client.post(url(s, "session_cancel", pk=s.session.pk), cancel_data(s, resolution="with_refund"), **HEADER)
    assert response.status_code == 400 and b"outside Pontora" in response.content
    item.refresh_from_db(); s.session.refresh_from_db()
    assert item.state == "confirmed" and not s.session.canceled_at
    assert not Refund.objects.exists() and not BookingMessage.objects.exists()


def provider_paid(s, item, monkeypatch):
    from core.payments import refunds
    monkeypatch.setattr(refunds, "validate_account", lambda *a, **k: None)
    account, _ = PaymentAccount.objects.get_or_create(school=s.school, defaults={"reference": "acct_test", "enabled": True})
    item.order.account = account
    item.order.save()
    return PaymentAttempt.objects.create(order=item.order, account=account, state="paid", amount_cents=100,
        entry=item.order.entries.get(kind="payment"), provider_intent="pi_" + item.public_id.hex,
        expires_at=timezone.now())


def test_cancel_refund_is_approved_not_falsely_completed_and_retry_safe(notifications, monkeypatch):
    s = notifications
    first, second = booked(s), booked(s)
    provider_paid(s, first, monkeypatch); provider_paid(s, second, monkeypatch)
    path, data = review(s, "cancel", cancel_data(s, resolution="with_refund"))
    assert not Refund.objects.exists()
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    assert list(Refund.objects.values_list("amount_cents", flat=True)) == [100, 100]
    for item in (first, second):
        item.refresh_from_db()
        assert item.state == "canceled" and totals(item.order)["refunded_cents"] == 0
    assert all("refund has been requested" in m.body for m in BookingMessage.objects.all())


def test_refund_approval_failure_rolls_back_every_booking(notifications, monkeypatch):
    s = notifications
    first, second = booked(s), booked(s)
    provider_paid(s, first, monkeypatch); provider_paid(s, second, monkeypatch)
    path, data = review(s, "cancel", cancel_data(s, resolution="with_refund"))
    from scheduling import cancellation
    original = cancellation.approve_refund
    def approve(*args, **kwargs):
        if args[2] == second.public_id:
            raise ValidationError("Payment account changed")
        return original(*args, **kwargs)
    monkeypatch.setattr(cancellation, "approve_refund", approve)
    assert s.client.post(path, data, **HEADER).status_code == 400
    first.refresh_from_db(); second.refresh_from_db(); s.session.refresh_from_db()
    assert first.state == second.state == "confirmed" and not s.session.canceled_at
    assert not Refund.objects.exists() and not BookingMessage.objects.exists()


def test_cancel_empty_session_and_unpaid_booking(stock):
    s = stock
    enable(s)
    path, data = review(s, "cancel", cancel_data(s))
    assert s.client.post(path, data, **HEADER).json() == {"saved": True}
    s.session.refresh_from_db()
    assert s.session.canceled_at and not s.session.is_published


@pytest.mark.parametrize("role", ["viewer", "scheduler"])
def test_booked_session_change_permissions(notifications, role):
    s = notifications
    booked(s)
    data = edit_data(s)
    SchedulingMembership.objects.filter(user=s.user).update(role=role)
    assert s.client.post(url(s, "session_edit", pk=s.session.pk), data, **HEADER).status_code == 404
    assert s.client.get(url(s, "session_cancel", pk=s.session.pk), **HEADER).status_code == 404


def test_scope_csrf_and_tampering(notifications):
    s = notifications
    booked(s)
    data = edit_data(s)
    path = url(s, "session_edit", pk=s.session.pk)
    csrf = Client(enforce_csrf_checks=True); csrf.force_login(s.user)
    assert csrf.post(path, data).status_code == 403
    other = Location.objects.create(profile=s.profile, name="Other")
    room = Room.objects.create(location=other, name="Other room", capacity=100)
    assert s.client.post(path, {**data, "room": room.pk}, **HEADER).status_code == 400
    assert s.client.post(path, {**data, "change_token": "forged"}, **HEADER).status_code == 400
    path, accepted = review(s, "edit", data)
    changed = s.client.post(path, {**accepted, "capacity": 99}, **HEADER)
    assert changed.status_code == 200 and changed.context["preview"]
    s.session.refresh_from_db()
    assert s.session.capacity == 100


def test_later_change_suppresses_stale_email(notifications):
    s = notifications
    item = booked(s)
    path, data = review(s, "edit", edit_data(s))
    s.client.post(path, data, **HEADER)
    moved = BookingMessage.objects.get()
    s.session.refresh_from_db()
    path, data = review(s, "cancel", cancel_data(s))
    s.client.post(path, data, **HEADER)
    moved.refresh_from_db()
    assert moved.state == "superseded"
    assert messaging.process_batch(s.profile.pk) == 1
    assert BookingMessage.objects.get(state="simulated").notice_kind == "canceled"


def test_affected_list_query_count_does_not_grow_per_customer(notifications):
    s = notifications
    booked(s); booked(s); booked(s)
    with CaptureQueriesContext(connection) as queries:
        items = session_changes.affected(s.profile, s.session)
        session_changes.preview_rows(items)
        session_changes.revision(s.session, items)
    assert len(queries) == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_confirmation_is_idempotent(notifications):
    s = notifications
    booked(s)
    _, data = review(s, "cancel", cancel_data(s))
    submitted = QueryDict(mutable=True)
    submitted.update(data)
    barrier = Barrier(2)
    def save():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return session_changes.save(s.user, s.school.slug, s.session.pk, "cancel", submitted)[0]
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: save(), range(2))) == [True, True]
    assert BookingMessage.objects.filter(notice_kind="canceled").count() == 1


@pytest.mark.parametrize("state", ["sending", "uncertain"])
def test_inflight_email_blocks_change_until_delivery_is_resolved(notifications, state):
    s = notifications
    item = booked(s)
    message = workshop_notifications.capture(item)
    if state == "sending":
        message = messaging.claim(s.profile.pk)
    message.state = state
    message.save()
    response = s.client.post(url(s, "session_edit", pk=s.session.pk), edit_data(s), **HEADER)
    assert response.status_code == 400 and b"delivery status" in response.content
    item.refresh_from_db()
    assert item.starts_at == s.session.starts_at


def test_unpaid_cancellation_and_noncalendar_redirect(notifications):
    s = notifications
    item = reserve(s)
    path, data = review(s, "cancel", cancel_data(s))
    response = s.client.post(path, data)
    assert response.status_code == 302
    assert response.url == url(s, "workshop_detail", pk=s.offering.pk)
    item.refresh_from_db()
    assert item.state == "canceled" and not BookingMessage.objects.exists()


def test_canceled_session_and_consumed_token_cannot_be_reused(notifications):
    s = notifications
    booked(s)
    old_edit = edit_data(s)
    path, data = review(s, "cancel", cancel_data(s))
    assert s.client.post(path, data, **HEADER).status_code == 200
    assert s.client.post(path, {**data, "reason": "Different request"}, **HEADER).status_code == 400
    assert s.client.post(url(s, "session_edit", pk=s.session.pk), old_edit, **HEADER).status_code == 404


def test_session_notices_respect_disabled_and_demo_delivery(notifications, monkeypatch):
    from scheduling import session_notifications, reservation_email
    s = notifications
    item = booked(s)
    before = {"start": item.starts_at.isoformat()}
    s.profile.workshop_notifications_enabled = False
    s.profile.save()
    item.profile = s.profile
    assert session_notifications.capture(item, "rescheduled", before, uuid.uuid4()) is None
    s.profile.workshop_notifications_enabled = True
    s.profile.save()
    s.school.is_demo = True
    s.school.save()
    item.profile.school = s.school
    monkeypatch.setattr(reservation_email, "demo_confirmation_allowed", lambda item: True)
    key = uuid.uuid4()
    message = session_notifications.capture(item, "rescheduled", before, key)
    assert message.subject.startswith("[Demo]") and "not a real reservation" in message.body
    assert message.state == "queued"
    assert session_notifications.capture(item, "rescheduled", before, key).pk == message.pk
    assert session_notifications.deliverable(message, item)
    item.state = "canceled"
    assert not session_notifications.deliverable(message, item)


def test_workflow_disabled_is_not_available(notifications):
    s = notifications
    booked(s)
    s.profile.native_reservations_enabled = False
    s.profile.save()
    assert s.client.get(url(s, "session_cancel", pk=s.session.pk), **HEADER).status_code == 404
