from datetime import timedelta
import uuid

import pytest
from django.core.exceptions import ValidationError
from django.http import QueryDict

from core.models import AdminAuditLog
from core.payments.service import totals, record_offline
from scheduling import booking_edits, staff_workshops
from scheduling.models import Reservation, SchedulingMembership, Session, WorkshopRequest, PartyRequest
from scheduling.tests.test_inventory import stock, url
from scheduling.tests.test_staff_workshops import intake, create, data, HEADER

pytestmark = pytest.mark.django_db


def edit_data(s, item, **changes):
    body = QueryDict(mutable=True)
    source = item.workshop_request if item.workshop_request_id else item.party_request
    prefix = "purchaser" if item.workshop_request_id else "organizer"
    body.update({"edit_token": booking_edits.token_for(s.user, item),
        **{"purchaser_" + key: getattr(source, prefix + "_" + key) for key in ("name", "email", "phone")},
        "session": str(item.session_id), "quantity": str(item.quantity), **changes})
    return body


def save(s, item, body):
    return booking_edits.save(s.user, s.school.slug, item.public_id, body)


def target(s, **changes):
    return Session.objects.create(offering=s.offering, room=s.room,
        starts_at=s.session.starts_at + timedelta(days=1), ends_at=s.session.ends_at + timedelta(days=1),
        capacity=changes.get("capacity", 10), is_published=True)


@pytest.mark.parametrize("customer", [False, True])
@pytest.mark.parametrize("publication", [{}, {"publish": ""}])
def test_new_session_from_calendar_with_or_without_customer_and_retry(intake, customer, publication):
    s = intake
    values = data(s, request_token=staff_workshops.token_for(s.user, s.profile, s.offering, new=True),
        starts_at=(s.session.starts_at + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
        duration_minutes="90", room=str(s.room.pk), capacity="5", **publication)
    page = s.client.get(s.path, {"new": "1"}, **HEADER)
    assert b'name="publish"' not in page.content
    assert b"Open for online bookings" not in page.content
    if not customer:
        values.update(purchaser_name="", purchaser_email="", purchaser_phone="", quantity="")
    first = s.client.post(s.path, values, **HEADER)
    assert first.status_code == 200, first.content
    assert s.client.post(s.path, values, **HEADER).status_code == 200
    assert Session.objects.count() == 2
    assert Reservation.objects.count() == int(customer)
    newest = Session.objects.latest("pk")
    assert newest.ends_at - newest.starts_at == timedelta(minutes=90)
    assert newest.is_published
    booking_form = staff_workshops.StaffWorkshopForm(profile=s.profile, offering=s.offering)
    assert booking_form.fields["session"].queryset.filter(pk=newest.pk).exists()
    if customer:
        assert Reservation.objects.get().session_id == newest.pk
    else:
        assert first.json() == {"saved": True, "session_created": True}


def test_new_session_invalid_customer_rolls_back_and_fixed_session_cannot_be_changed(intake):
    s = intake
    values = data(s, request_token=staff_workshops.token_for(s.user, s.profile, s.offering, new=True),
        starts_at=(s.session.starts_at + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
        duration_minutes="60", room=str(s.room.pk), capacity="1")
    assert s.client.post(s.path, values, **HEADER).status_code == 400
    assert Session.objects.count() == 1 and not WorkshopRequest.objects.exists()
    other = target(s)
    values = data(s, request_token=staff_workshops.token_for(s.user, s.profile, s.offering, session=s.session.pk), session=str(other.pk))
    assert create(s, values).session_id == s.session.pk


def test_contact_and_move_preserve_identity_money_and_other_bookings(intake):
    s = intake
    item, other = create(s), create(s)
    order_id, public_id = item.order_id, item.public_id
    people = list(item.workshop_request.participants.values_list("pk", flat=True))
    destination = target(s)
    values = edit_data(s, item, session=str(destination.pk), purchaser_name="Updated", purchaser_phone="3105550199")
    saved, form, preview = save(s, item, values)
    assert not form.errors and preview is None
    assert saved.session_id == destination.pk and saved.order_id == order_id and saved.public_id == public_id
    assert list(saved.workshop_request.participants.values_list("pk", flat=True)) == people
    assert totals(saved.order)["total_cents"] == 13000
    other.refresh_from_db()
    assert other.session_id == s.session.pk
    assert save(s, saved, values)[0].pk == saved.pk
    audit = AdminAuditLog.objects.filter(changes__action="staff_booking_updated").get()
    assert audit.extra["before"]["snapshot"]["name"] == "Phone customer"
    assert audit.extra["after"]["snapshot"]["name"] == "Updated"


def test_count_reduction_requires_review_preserves_payment_and_records_excess(intake):
    s = intake
    item = create(s)
    entry = record_offline(order=item.order, actor=s.user, key=uuid.uuid4(), kind="payment",
        amount_cents=13000, reference="Synthetic fully paid")
    item.refresh_from_db()
    person = item.workshop_request.participants.first()
    values = edit_data(s, item, quantity="1", remove=str(person.pk))
    saved, form, preview = save(s, item, values)
    assert saved is None and preview["total"] == 65 and preview["excess"] == 65
    item.refresh_from_db()
    assert item.quantity == 2 and item.order.total_cents == 13000
    values["review_token"] = form.data["review_token"]
    saved, form, preview = save(s, item, values)
    assert saved.quantity == 1 and totals(saved.order)["net_cents"] == 13000
    entry.refresh_from_db()
    assert entry.amount_cents == 13000 and not saved.order.entries.filter(kind="refund").exists()
    assert not saved.workshop_request.participants.filter(pk=person.pk).exists()
    assert save(s, saved, values)[0].pk == saved.pk


def test_count_increase_uses_agreed_price_not_changed_default_and_blocks_stale_review(intake):
    s = intake
    item = create(s)
    s.offering.price_per_participant = 100
    s.offering.save()
    values = edit_data(s, item, quantity="3")
    _, form, preview = save(s, item, values)
    assert preview["total"] == 195
    values["review_token"] = form.data["review_token"]
    saved, _, _ = save(s, item, edit_data(s, item, purchaser_name="Changed elsewhere"))
    with pytest.raises(ValidationError, match="booking changed"):
        save(s, saved, values)


def test_capacity_failure_and_invalid_participant_removal_are_atomic(intake):
    s = intake
    item = create(s)
    destination = target(s, capacity=1)
    with pytest.raises(ValidationError, match="enough places"):
        save(s, item, edit_data(s, item, session=str(destination.pk), purchaser_name="Must roll back"))
    item.refresh_from_db()
    assert item.session_id == s.session.pk and item.workshop_request.purchaser_name == "Phone customer"
    saved, form, _ = save(s, item, edit_data(s, item, quantity="1"))
    assert not saved and "remove" in form.errors


def test_party_contact_and_time_edit_keep_paid_quote_and_duration(intake):
    s = intake
    source = PartyRequest.objects.create(offering=s.offering, organizer_name="Party", organizer_email="party@example.invalid",
        starts_at=s.session.starts_at + timedelta(days=4), ends_at=s.session.ends_at + timedelta(days=4),
        group_size=2, space="exclusive", room=s.room, proposed_total=100, proposed_deposit=50, status="reviewed",
        offering_snapshot={"workshop": s.offering.name, "timezone": "UTC"},
        submission_key=uuid.uuid4(), submission_digest="party", browser_digest="party")
    from scheduling import reservations
    item = reservations.create_reservation(s.user, s.school.slug, "party", source.public_id)
    reservations.settle_offline(s.user, s.school.slug, item.public_id, key=uuid.uuid4(), kind="payment", amount_cents=5000, reference="Party deposit")
    item.refresh_from_db()
    values = edit_data(s, item, starts_at=(item.starts_at + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"), purchaser_name="New organizer")
    saved, form, _ = save(s, item, values)
    assert not form.errors
    assert saved.ends_at - saved.starts_at == timedelta(hours=1)
    assert saved.party_request.organizer_name == "New organizer"
    assert saved.order.total_cents == 10000 and totals(saved.order)["net_cents"] == 5000


@pytest.mark.parametrize("role,status", [("owner", 200), ("scheduler", 404), ("viewer", 404)])
def test_edit_permissions_and_roster_affordance(intake, role, status):
    s = intake
    item = create(s)
    SchedulingMembership.objects.filter(user=s.user).update(role=role)
    path = url(s, "staff_booking_edit", public_id=item.public_id)
    assert s.client.get(path, **HEADER).status_code == status
    roster = s.client.get(url(s, "calendar_detail", kind="session", pk=s.session.pk)).json()
    assert ("edit_url" in roster["reservations"][0]) == (role == "owner")


def test_removed_participant_releases_only_frozen_materials(intake):
    from scheduling import workshop_selections
    s = intake
    item = create(s)
    people = list(item.workshop_request.participants.all())
    for person in people:
        item = workshop_selections.update(s.user, s.school.slug, item.public_id, person.pk,
            {"name": person.name, f"choice_{s.design.pk}": str(s.cat.pk), f"choice_{s.color.pk}": str(s.blue.pk)})
    original_recipe = item.snapshot["workshop_participant_materials"][str(people[1].pk)]
    original_selections = item.workshop_request.participants.get(pk=people[1].pk).selections
    s.item.requirements.update(quantity=3)
    values = edit_data(s, item, quantity="1", remove=str(people[0].pk))
    _, form, _ = save(s, item, values)
    values["review_token"] = form.data["review_token"]
    saved, _, _ = save(s, item, values)
    assert saved.stock_allocations.get().quantity == 1
    assert saved.snapshot["workshop_participant_materials"] == {str(people[1].pk): original_recipe}
    assert saved.workshop_request.participants.get().selections == original_selections


def test_destination_changed_in_another_tab_requires_fresh_review(intake):
    s = intake
    item = create(s)
    destination = target(s)
    values = edit_data(s, item, session=str(destination.pk))
    destination.starts_at += timedelta(hours=1)
    destination.ends_at += timedelta(hours=1)
    destination.save()
    with pytest.raises(ValidationError, match="workshop time changed"):
        save(s, item, values)
    item.refresh_from_db()
    assert item.session_id == s.session.pk


def test_edit_post_keeps_calendar_return_and_rejects_invalid_contact(intake):
    from urllib.parse import urlencode
    s = intake
    item = create(s)
    calendar = url(s, "workspace") + "?view=calendar&date=2026-12-10"
    path = url(s, "staff_booking_edit", public_id=item.public_id) + "?" + urlencode({"calendar": calendar})
    response = s.client.post(path, edit_data(s, item, purchaser_email="bad"), **HEADER)
    assert response.status_code == 400 and b'Enter a valid email address' in response.content
    response = s.client.post(path, edit_data(s, item, purchaser_name="Renamed"))
    assert response.status_code == 302 and urlencode({"calendar": calendar}) in response.url


def test_calendar_edit_post_handles_stale_token_and_returns_saved_json(intake):
    s = intake
    item = create(s)
    path = url(s, "staff_booking_edit", public_id=item.public_id)
    response = s.client.post(path, edit_data(s, item, edit_token="expired"), **HEADER)
    assert response.status_code == 400 and b"Reopen Edit booking" in response.content
    response = s.client.post(path, edit_data(s, item, purchaser_name="Calendar customer"), **HEADER)
    assert response.json() == {"saved": True}
    item.refresh_from_db()
    assert item.snapshot["name"] == "Calendar customer"


def test_capacity_visible_before_customer_entry_and_blank_cancel_note(intake):
    s = intake
    item = create(s)
    s.session.capacity = item.quantity
    s.session.save()
    response = s.client.get(s.path, {"session": s.session.pk}, **HEADER)
    assert b"This session is full" in response.content
    assert b'name="purchaser_name"' not in response.content
    assert b">Save booking</button>" not in response.content
    cancel = url(s, "reservation_cancel", public_id=item.public_id)
    response = s.client.get(cancel, **HEADER)
    assert b"Payment handling" not in response.content
    assert not response.context["form"].fields["reason"].required
    body = {"key": str(uuid.uuid4()), "reason": ""}
    assert s.client.post(cancel, body, **HEADER).json() == {"saved": True}
    assert s.client.post(cancel, body, **HEADER).json() == {"saved": True}
    item.refresh_from_db()
    assert item.state == "canceled" and not item.order.entries.exists()


def test_calendar_event_counts_bookings_separately_from_participants(intake):
    from django.http import QueryDict
    from scheduling.calendar import calendar_events
    s = intake
    create(s)
    create(s, data(s, quantity=1))
    query = QueryDict(mutable=True)
    query.update(start=(s.session.starts_at - timedelta(days=1)).date().isoformat(),
        end=(s.session.starts_at + timedelta(days=2)).date().isoformat())
    event = next(e for e in calendar_events(s.profile, query) if e["id"] == f"session-{s.session.pk}")
    assert event["extendedProps"]["bookings"] == 2
    assert event["extendedProps"]["seats"] == 3


def test_count_increase_adds_only_new_pending_people_and_keeps_existing_identity(intake):
    s = intake
    item = create(s)
    people = list(item.workshop_request.participants.values_list("pk", flat=True))
    values = edit_data(s, item, quantity="3")
    _, form, preview = save(s, item, values)
    values["review_token"] = form.data["review_token"]
    saved, _, _ = save(s, item, values)
    assert saved.quantity == 3 and saved.order.total_cents == 19500
    assert saved.workshop_request.participants.filter(pk__in=people).count() == 2
    assert saved.workshop_request.participants.exclude(pk__in=people).get().selections_deferred


def test_corrupt_quote_does_not_block_contact_edit_but_cannot_reprice(intake):
    s = intake
    item = create(s)
    item.workshop_request.session_snapshot.pop("price_breakdown")
    item.workshop_request.save()
    item, _, _ = save(s, item, edit_data(s, item, purchaser_name="Contact corrected"))
    with pytest.raises(ValidationError, match="reliable per-participant price"):
        save(s, item, edit_data(s, item, quantity="3"))


def test_wrong_token_closed_booking_and_pending_refund_cannot_edit(intake):
    from core.payments.models import PaymentAccount, PaymentAttempt, Refund
    s = intake
    item = create(s)
    with pytest.raises(ValidationError, match="booking changed"):
        save(s, item, edit_data(s, item, edit_token="invalid"))
    account = PaymentAccount.objects.create(school=s.school, reference="acct_refund_guard", enabled=True)
    attempt = PaymentAttempt.objects.create(order=item.order, account=account, amount_cents=13000,
        state="paid", expires_at=item.starts_at, success_url="https://example.invalid/success", cancel_url="https://example.invalid/cancel")
    Refund.objects.create(attempt=attempt, key=uuid.uuid4(), amount_cents=100, reason="Synthetic refund", state="pending", actor=s.user)
    with pytest.raises(ValidationError, match="still processing"):
        save(s, item, edit_data(s, item))
    item.state = "canceled"
    item.save()
    with pytest.raises(ValidationError, match="Only upcoming"):
        save(s, item, edit_data(s, item))


@pytest.mark.parametrize("state", ["pending", "open", "unknown"])
def test_active_payment_blocks_amendment(intake, state):
    from core.payments.models import PaymentAccount, PaymentAttempt
    s = intake
    item = create(s)
    account = PaymentAccount.objects.create(school=s.school, reference="acct_edit_test", enabled=True)
    PaymentAttempt.objects.create(order=item.order, account=account, amount_cents=13000,
        state=state, expires_at=item.starts_at, success_url="https://example.invalid/success", cancel_url="https://example.invalid/cancel")
    with pytest.raises(ValidationError, match="still processing"):
        save(s, item, edit_data(s, item, purchaser_name="Not saved"))
    assert item.workshop_request.purchaser_name == "Phone customer"


def test_location_scope_foreign_session_and_csrf(intake):
    from django.test import Client
    from scheduling.models import Location, Offering
    s = intake
    item = create(s)
    other = Location.objects.create(profile=s.profile, name="Other studio")
    offering = Offering.objects.create(location=other, name="Other workshop")
    session = Session.objects.create(offering=offering, starts_at=s.session.starts_at,
        ends_at=s.session.ends_at, capacity=10)
    _, form, _ = save(s, item, edit_data(s, item, session=str(session.pk)))
    assert "session" in form.errors
    path = url(s, "staff_booking_edit", public_id=item.public_id)
    client = Client(enforce_csrf_checks=True)
    client.force_login(s.user)
    assert client.post(path, edit_data(s, item), **HEADER).status_code == 403
    membership = SchedulingMembership.objects.get(user=s.user)
    membership.all_locations = False
    membership.save()
    membership.locations.add(other)
    assert s.client.get(path, **HEADER).status_code == 404
    assert s.client.post(path, edit_data(s, item), **HEADER).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_concurrent_moves_to_last_place_have_one_winner(intake):
    from concurrent.futures import ThreadPoolExecutor
    from django.db import close_old_connections
    s = intake
    first, second = create(s, data(s, quantity="1")), create(s, data(s, quantity="1"))
    destination = target(s, capacity=1)
    jobs = [(item, edit_data(s, item, session=str(destination.pk))) for item in (first, second)]
    def move(job):
        close_old_connections()
        try:
            return bool(save(s, job[0], job[1])[0])
        except ValidationError:
            return False
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(move, jobs)) == [False, True]
    assert Reservation.objects.filter(session=destination).count() == 1
