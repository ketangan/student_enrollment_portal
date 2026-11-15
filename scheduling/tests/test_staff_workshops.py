"""Phone bookings share pricing, capacity, participant editing and the email outbox."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.http import QueryDict
from django.test import Client

from core.payments.service import totals
from scheduling import staff_workshops, workshop_followup, workshop_selections
from scheduling.message_models import BookingMessage
from scheduling.models import ChoiceGroup, ChoiceOption, Reservation, SchedulingMembership, Session, WorkshopRequest
from scheduling.requests import catalog_for
from scheduling.tests.test_inventory import stock, url
from scheduling.tests.test_calendar import enable

pytestmark = pytest.mark.django_db
HEADER = {"HTTP_X_CALENDAR_EDITOR": "1"}


@pytest.fixture
def intake(stock):
    s = stock
    s.school.is_demo = True
    s.school.save()
    s.location.timezone_name = "UTC"
    s.location.save()
    enable(s)
    s.offering.price_per_participant = Decimal("65")
    s.offering.allow_deferred_selections = True
    s.offering.save()
    s.profile.workshop_notifications_enabled = True
    s.profile.save()
    s.path = url(s, "staff_workshop_create", offering_pk=s.offering.pk)
    return s


def data(s, **changes):
    return {"request_token": staff_workshops.token_for(s.user, s.profile, s.offering),
        "session": str(s.session.pk), "quantity": "2", "purchaser_name": "Phone customer",
        "purchaser_email": "phone@example.invalid", "purchaser_phone": "3105550100", **changes}


def create(s, values=None):
    body = QueryDict(mutable=True)
    body.update(values or data(s))
    item, form = staff_workshops.create(s.user, s.school.slug, s.offering.pk, body)
    assert not form.errors, form.errors
    return item


def test_phone_booking_reserves_places_without_payment_or_consent(intake):
    s = intake
    s.location.default_tax_rate = Decimal("10")
    s.location.save()
    s.offering.refresh_from_db()
    values = data(s)
    response = s.client.post(s.path, values, **HEADER)
    assert response.status_code == 200, response.content
    item = Reservation.objects.get()
    assert response.json()["reservation_url"] == url(s, "reservation_detail", public_id=item.public_id)
    assert item.state == "confirmed" and item.quantity == 2
    assert totals(item.order)["outstanding_cents"] == 14300
    assert not item.order.entries.exists() and not item.order.attempts.exists()
    assert not item.stock_allocations.exists()
    assert item.workshop_request.participants.filter(selections_deferred=True).count() == 2
    assert not item.snapshot.get("acknowledgments")
    message = BookingMessage.objects.get(reservation=item)
    assert message.recipient == "phone@example.invalid"
    assert "Balance due: USD 143.00" in message.body and "workshop-selections/" in message.body
    assert message.state == "draft"
    # An exact retry cannot create another order or confirmation, even after prices change.
    s.offering.price_per_participant = 70
    s.offering.save()
    assert create(s, values).pk == item.pk
    assert Reservation.objects.count() == BookingMessage.objects.count() == 1
    with pytest.raises(ValidationError, match="different details"):
        create(s, {**values, "quantity": "3"})


@pytest.mark.parametrize("changes", [{"purchaser_email": "bad"}, {"purchaser_name": ""},
    {"purchaser_phone": "hello"}, {"quantity": "0"}, {"quantity": "21"}, {"session": "999999"}, {"session": ""}])
def test_invalid_customer_and_session_never_create_records(intake, changes):
    s = intake
    assert s.client.post(s.path, data(s, **changes), **HEADER).status_code == 400
    assert not Reservation.objects.exists() and not WorkshopRequest.objects.exists()


def test_booking_cannot_create_availability_and_capacity_is_shared(intake):
    s = intake
    start = (s.session.starts_at + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    values = data(s, session="", starts_at=start, duration_minutes="120", room=str(s.room.pk), capacity="1")
    response = s.client.post(s.path, values, **HEADER)
    assert response.status_code == 400
    assert Session.objects.count() == 1 and not WorkshopRequest.objects.exists()
    s.session.capacity = 2
    s.session.save()
    item = create(s)
    with pytest.raises(ValidationError):
        create(s, data(s, session=str(item.workshop_request.session_id), quantity="1"))
    assert Reservation.objects.count() == 1


def test_no_schedule_only_mode_or_price_copy_and_modal_cancel_is_button(intake):
    s = intake
    response = s.client.get(s.path, **HEADER)
    assert b'Schedule time only' not in response.content
    assert b'per participant' not in response.content and b'% tax' not in response.content
    assert b'name="starts_at"' not in response.content
    assert b'<button type="button" data-calendar-close>Cancel</button>' in response.content
    values = data(s, session="", time_only="on", purchaser_name="", purchaser_email="", quantity="",
        starts_at=(s.session.starts_at + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"), duration_minutes="120", capacity="10")
    assert s.client.post(s.path, values, **HEADER).status_code == 400
    assert Session.objects.count() == 1 and not Reservation.objects.exists()


def test_existing_session_accepts_multiple_customer_bookings_without_duplication(intake):
    s = intake
    first = create(s)
    second = create(s, data(s, purchaser_name="Another customer", purchaser_email="another@example.invalid"))
    assert first.session_id == second.session_id == s.session.pk
    assert Session.objects.count() == 1 and Reservation.objects.count() == 2
    roster = s.client.get(url(s, "calendar_detail", kind="session", pk=s.session.pk)).json()
    assert roster["summary"]["booked"] == 4
    assert len(roster["reservations"]) == 2
    assert roster["booking_url"].endswith(f"?session={s.session.pk}")


def test_cancel_one_customer_releases_only_their_places_and_preserves_session(intake):
    import uuid
    s = intake
    s.session.capacity = 4
    s.session.save()
    first, second = create(s), create(s, data(s, purchaser_name="Second customer"))
    path = url(s, "reservation_cancel", public_id=first.public_id)
    response = s.client.post(path, {"key": str(uuid.uuid4()), "resolution": "without_refund", "reason": "Customer called"}, **HEADER)
    assert response.json() == {"saved": True}
    first.refresh_from_db()
    second.refresh_from_db()
    assert first.state == "canceled" and second.state == "confirmed"
    assert Session.objects.count() == 1
    roster = s.client.get(url(s, "calendar_detail", kind="session", pk=s.session.pk)).json()
    assert roster["summary"]["booked"] == 2 and roster["summary"]["open"] == 2
    replacement = create(s, data(s, purchaser_name="Replacement customer"))
    assert replacement.session_id == second.session_id


def test_empty_calendar_time_opens_session_and_existing_time_is_fixed(intake):
    s = intake
    response = s.client.get(s.path, {"starts_at": (s.session.starts_at + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")}, **HEADER)
    assert response.context["form"].new
    assert b'Customer (optional)' in response.content
    response = s.client.get(s.path, {"session": s.session.pk}, **HEADER)
    assert response.context["form"].fields["session"].disabled
    assert response.context["form"].fixed_session == s.session
    s.session.starts_at -= timedelta(days=1000)
    s.session.ends_at -= timedelta(days=1000)
    s.session.save()
    response = s.client.get(s.path, **HEADER)
    assert b'New session' in response.content
    roster = s.client.get(url(s, "calendar_detail", kind="session", pk=s.session.pk)).json()
    assert "booking_url" not in roster


def test_stale_price_time_and_invalid_token_require_review(intake):
    s = intake
    values = data(s)
    s.session.starts_at += timedelta(hours=1)
    s.session.ends_at += timedelta(hours=1)
    s.session.save()
    with pytest.raises(ValidationError, match="time changed"):
        create(s, values)
    values = data(s)
    s.offering.price_per_participant = 70
    s.offering.save()
    with pytest.raises(ValidationError, match="pricing or policies changed"):
        create(s, values)
    assert s.client.post(s.path, data(s, request_token="broken"), **HEADER).status_code == 400
    assert not Reservation.objects.exists()


@pytest.mark.parametrize("role,expected", [("owner", 200), ("scheduler", 200), ("viewer", 404)])
def test_intake_permissions_and_csrf(intake, role, expected):
    s = intake
    SchedulingMembership.objects.filter(user=s.user).update(role=role)
    assert s.client.get(s.path, **HEADER).status_code == expected
    assert s.client.post(s.path, data(s), **HEADER).status_code == expected
    client = Client(enforce_csrf_checks=True)
    client.force_login(s.user)
    assert client.post(s.path, data(s), **HEADER).status_code == 403


def test_disabled_booking_and_disabled_calendar_are_inaccessible(intake):
    s = intake
    s.offering.allow_deferred_selections = False
    s.offering.save()
    response = s.client.get(s.path, **HEADER)
    assert b'New session' in response.content
    assert b'Save booking' not in response.content
    assert s.client.post(s.path, data(s), **HEADER).status_code == 400
    s.profile.calendar_enabled = False
    s.profile.save()
    assert s.client.get(s.path, **HEADER).status_code == 404
    assert s.client.get(s.path).status_code == 404


def test_followup_and_inline_staff_edit_collect_names_and_age_without_repricing(intake):
    s = intake
    age = ChoiceGroup.objects.create(catalog=s.catalog, name="Age", tracks_stock=False)
    seven = ChoiceOption.objects.create(group=age, label="7")
    item = create(s)
    person = item.workshop_request.participants.first()
    response = s.client.get(url(s, "reservation_detail", public_id=item.public_id), {"edit_participant": person.pk})
    assert f'name="choice_{age.pk}"'.encode() in response.content
    token = workshop_followup.token_for(item)
    values = {"selection-design": s.cat.pk, "selection-color": s.blue.pk,
        "selection-guest_name": "Taylor", f"selection-choice_{age.pk}": seven.pk}
    _, form = workshop_followup.save(s.school.slug, token, person.pk, {**values, f"selection-choice_{age.pk}": ""})
    assert form.errors
    _, form = workshop_followup.save(s.school.slug, token, person.pk, values)
    assert not form.errors
    person.refresh_from_db()
    assert person.name == "Taylor" and not person.selections_deferred
    assert any(row["group"] == "Age" and row["option"] == "7" for row in person.selections)
    assert item.order.total_cents == 13000 and not item.order.entries.exists()
    detail = url(s, "reservation_detail", public_id=item.public_id)
    response = s.client.get(detail, {"edit_participant": person.pk})
    assert response.status_code == 200
    assert b'Participant name</label>' in response.content
    assert f'name="choice_{age.pk}"'.encode() in response.content
    fields = {"name": "Taylor Updated", "selection_revision": workshop_selections.revision(person),
        f"choice_{s.design.pk}": str(s.cat.pk), f"choice_{s.color.pk}": str(s.blue.pk), f"choice_{age.pk}": str(seven.pk)}
    workshop_selections.update(s.user, s.school.slug, item.public_id, person.pk, fields)
    person.refresh_from_db()
    assert person.name == "Taylor Updated"
    assert item.stock_allocations.get().quantity == 1


def test_overlapping_other_workshop_room_is_rejected(intake):
    from scheduling.models import Offering
    s = intake
    other = Offering.objects.create(location=s.location, name="Other class", is_published=True)
    Session.objects.create(offering=other, room=s.room, starts_at=s.session.starts_at,
        ends_at=s.session.ends_at, capacity=10, is_published=True)
    with pytest.raises(ValidationError, match="already booked"):
        create(s)
    assert not Reservation.objects.exists()


def test_prefill_existing_time_and_cross_location_access(intake):
    from scheduling.models import Location
    s = intake
    # Exact venue time selects the existing session rather than creating a duplicate.
    s.session.starts_at = s.session.starts_at.replace(second=0, microsecond=0)
    s.session.save()
    response = s.client.get(s.path, {"starts_at":s.session.starts_at.strftime("%Y-%m-%dT%H:%M")}, **HEADER)
    assert response.context["form"]["session"].value() == s.session.pk
    other = Location.objects.create(profile=s.profile, name="Other")
    member = SchedulingMembership.objects.get(user=s.user)
    member.all_locations = False
    member.save()
    member.locations.set([other])
    assert s.client.get(s.path, **HEADER).status_code == 404
    assert s.client.post(s.path, data(s), **HEADER).status_code == 404


def test_existing_public_bookings_cannot_use_unpublished_times(intake):
    from scheduling.tests.test_inventory import reserve
    s = intake
    s.session.is_published = False
    s.session.save()
    with pytest.raises(ValidationError, match="no longer published"):
        reserve(s)
    assert create(s).state == "confirmed"


def test_booking_disabled_while_form_open_never_silently_creates_time(intake):
    s = intake
    values = data(s, session="", starts_at=(s.session.starts_at + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        duration_minutes="60", capacity="5")
    s.profile.native_reservations_enabled = False
    s.profile.save()
    with pytest.raises(ValidationError, match="not enabled"):
        create(s, values)
    assert Session.objects.count() == 1 and not Reservation.objects.exists()


def test_staff_intake_does_not_enable_emails_when_notifications_are_disabled(intake):
    s = intake
    s.profile.workshop_notifications_enabled = False
    s.profile.save()
    assert create(s).state == "confirmed"
    assert not BookingMessage.objects.exists()


def test_real_studio_without_payment_account_cannot_create_test_order(intake):
    s = intake
    s.school.is_demo = False
    s.school.save()
    with pytest.raises(ValidationError):
        create(s)
    assert not Reservation.objects.exists() and not WorkshopRequest.objects.exists()


def test_staff_confirmation_delivered_once_to_customer_with_existing_worker(intake, settings, monkeypatch):
    from django.utils import timezone
    from scheduling import party_notifications
    s = intake
    settings.DEBUG = False
    settings.BOOKING_HOSTED_DEMO_SLUGS = [s.school.slug]
    settings.BOOKING_HOSTED_DEMO_HOSTS = ["demo.example.invalid"]
    s.school.feature_flags = {"booking_experience_demo": True}
    s.school.save()
    s.profile.demo_notifications_since = timezone.now() - timedelta(minutes=1)
    s.profile.save()
    sent = []
    class Transport:
        def send_messages(self, messages):
            sent.extend(messages)
            return 1
    monkeypatch.setattr(party_notifications, "get_school_email_connection", lambda school: Transport())
    item = create(s)
    party_notifications.process(s.profile.pk)
    party_notifications.process(s.profile.pk)
    assert len(sent) == 1
    assert sent[0].to == ["phone@example.invalid"]
    assert "Complete participant details" in sent[0].body
    assert BookingMessage.objects.get(reservation=item).state == "sent"


@pytest.mark.django_db(transaction=True)
def test_concurrent_last_seat_has_one_winner(intake):
    s = intake
    s.session.capacity = 1
    s.session.save()
    submissions = [data(s, quantity="1"), data(s, quantity="1")]
    def attempt(values):
        close_old_connections()
        try:
            return bool(create(s, values))
        except ValidationError:
            return False
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, submissions))
    assert sorted(results) == [False, True]
    assert Reservation.objects.count() == WorkshopRequest.objects.count() == BookingMessage.objects.count() == 1
