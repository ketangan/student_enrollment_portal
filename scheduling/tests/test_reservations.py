import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier

import pytest
from pytest_django.asserts import assertContains
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School, Submission
from core.payments.models import LedgerEntry, PaymentOrder
from core.payments.service import create_order, record_offline, totals
from scheduling.models import Location, Offering, PartyRequest, RequestParticipant, Reservation, Room, Session, WorkshopRequest
from scheduling.parties import review_inquiry
from scheduling.requests import withdraw_request
from scheduling.reservations import active_claims, cancel_reservation, create_reservation, peak_quantity, settle_offline

pytestmark = pytest.mark.django_db


def test_existing_reservations_remain_clear_when_new_intake_is_disabled(inventory, booking_member, client):
    reserve(booking_member, workshop_request(inventory))
    party = party_request(inventory)
    reserve(booking_member, party)
    profile = inventory[0]
    profile.native_reservations_enabled = False
    profile.save()
    client.force_login(booking_member.user)
    response = client.get(path(booking_member, "preparation", pk=inventory[3].pk))
    assert b"check Reservations for confirmed bookings" in response.content
    assert b"These are not confirmed seats" not in response.content
    response = client.get(path(booking_member, "party_detail", public_id=party.public_id))
    assert response.status_code == 302
    assert response.url == path(booking_member, "reservation_detail", public_id=party.reservation.public_id)
    assert b"Party details" in client.get(response.url).content
    assert b"Held - awaiting payment" in client.get(path(booking_member, "requests")).content
    assert b"Awaiting payment" in client.get(path(booking_member, "parties")).content


@pytest.fixture
def inventory(booking_member):
    profile = booking_member.profile
    profile.native_reservations_enabled = True
    profile.save()
    location = Location.objects.create(profile=profile, name="Studio")
    room = Room.objects.create(location=location, name="Room 1", capacity=6)
    offering = Offering.objects.create(location=location, name="Workshop", is_published=True)
    start = timezone.now() + timedelta(days=3)
    session = Session.objects.create(offering=offering, room=room, starts_at=start, ends_at=start+timedelta(hours=1), capacity=4, is_published=True)
    return profile, offering, room, session


def workshop_request(inventory, count=2):
    _, offering, _, session = inventory
    item = WorkshopRequest.objects.create(session=session, purchaser_name="Customer", purchaser_email="customer@example.com",
        submission_key=uuid.uuid4(), session_snapshot={"workshop": offering.name, "timezone": "America/Los_Angeles", "location": "Studio"})
    for i in range(count):
        RequestParticipant.objects.create(request=item, name=f"Child {i}", position=i)
    return item


def party_request(inventory, *, size=4, space="exclusive", start=None):
    _, offering, room, session = inventory
    start = start or session.ends_at + timedelta(hours=1)
    return PartyRequest.objects.create(offering=offering, organizer_name="Party", organizer_email="party@example.com",
        starts_at=start, ends_at=start+timedelta(hours=1), room=room, group_size=size, space=space,
        status="reviewed", proposed_total=400, proposed_deposit=100, submission_key=uuid.uuid4(),
        offering_snapshot={"workshop": offering.name, "timezone": "America/Los_Angeles", "location": "Studio"})


def reserve(member, source, total=10000, deposit=2500):
    return create_reservation(member.user, member.profile.school.slug,
        "party" if isinstance(source, PartyRequest) else "workshop", source.public_id, total, deposit)


def test_full_studio_blocks_other_rooms_and_session_publication(inventory, booking_member):
    from scheduling.reservations import validate_session_edit
    _, offering, room, session = inventory
    source = party_request(inventory, space="studio")
    item = reserve(booking_member, source)
    assert item.snapshot["space"] == "studio" and item.exclusive
    second = Room.objects.create(location=room.location, name="Table 2", capacity=6)
    inquiry = party_request(inventory, start=source.starts_at, space="shared")
    inquiry.room = second
    inquiry.save()
    with pytest.raises(ValidationError, match="full-studio"):
        reserve(booking_member, inquiry)
    future = Session(offering=offering, room=second, starts_at=source.starts_at, ends_at=source.ends_at, capacity=4, is_published=True)
    with pytest.raises(ValidationError, match="full-studio"):
        validate_session_edit(future)
    future.starts_at, future.ends_at = source.ends_at, source.ends_at + timedelta(hours=1)
    validate_session_edit(future)
    item.hold_until = timezone.now() - timedelta(minutes=1)
    item.save()
    assert reserve(booking_member, inquiry)


def test_full_studio_rejects_published_session_in_another_room(inventory, booking_member):
    _, offering, room, session = inventory
    second = Room.objects.create(location=room.location, name="Table 2", capacity=6)
    source = party_request(inventory, start=session.starts_at, space="studio")
    source.room = second
    source.save()
    with pytest.raises(ValidationError, match="full studio"):
        reserve(booking_member, source)


def test_private_table_does_not_block_other_tables(inventory, booking_member):
    _, _, room, _ = inventory
    source = party_request(inventory)
    reserve(booking_member, source)
    second = Room.objects.create(location=room.location, name="Table 2", capacity=6)
    other = party_request(inventory, start=source.starts_at)
    other.room = second
    other.save()
    assert reserve(booking_member, other)


@pytest.mark.django_db(transaction=True)
def test_full_studio_and_other_room_compete_for_one_hold(inventory, booking_member):
    _, _, room, _ = inventory
    studio = party_request(inventory, space="studio")
    table = party_request(inventory, start=studio.starts_at)
    table.room = Room.objects.create(location=room.location, name="Table 2", capacity=6)
    table.save()
    barrier = Barrier(2)

    def attempt(source):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                return reserve(booking_member, source).pk
            except ValidationError:
                return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, [studio, table]))
    assert sum(result is not None for result in results) == 1
    assert Reservation.objects.count() == PaymentOrder.objects.count() == 1


def settle(member, item, amount=2500, **kwargs):
    values = dict(key=uuid.uuid4(), kind="payment", amount_cents=amount, reference=str(uuid.uuid4()))
    values.update(kwargs)
    return settle_offline(member.user, member.profile.school.slug, item.public_id, **values)


def path(member, name, **kwargs):
    return reverse(f"scheduling:{name}", kwargs={"school_slug": member.profile.school.slug, **kwargs})


def test_workshop_deposit_balance_cancel_refund_are_distinct(inventory, booking_member):
    source = workshop_request(inventory)
    school = booking_member.profile.school
    before = (school.plan, school.is_active, school.stripe_customer_id)
    item = reserve(booking_member, source)
    assert item.state == "held" and active_claims().count() == 1
    assert item.order.total == Decimal("100.00") and item.order.deposit == Decimal("25.00")
    assert reserve(booking_member, source).pk == item.pk
    assert settle(booking_member, item, 1000).state == "held"
    assert settle(booking_member, item, 1500).state == "confirmed"
    assert totals(item.order)["outstanding"] == Decimal("75")
    Session.objects.filter(pk=item.session_id).update(is_published=False)
    assert settle(booking_member, item, 7500).state == "confirmed"
    assert totals(item.order)["net_cents"] == 10000
    cancel_reservation(booking_member.user, school.slug, item.public_id)
    cancel_reservation(booking_member.user, school.slug, item.public_id)
    assert not active_claims().exists() and totals(item.order)["net_cents"] == 10000
    settle(booking_member, item, 10000, kind="refund")
    assert totals(item.order)["net_cents"] == 0 and LedgerEntry.objects.count() == 4
    school.refresh_from_db()
    assert (school.plan, school.is_active, school.stripe_customer_id) == before
    assert not Submission.objects.exists()


@pytest.mark.parametrize("total,deposit,expected", [(0,0,"confirmed"),(10000,0,"held"),(10000,10000,"held")])
def test_free_and_full_payment(inventory, booking_member, total, deposit, expected):
    item = reserve(booking_member, workshop_request(inventory), total, deposit)
    assert item.state == expected
    if total:
        assert settle(booking_member, item, total-1).state == "held"
        assert settle(booking_member, item, 1).state == "confirmed"


def test_financial_retries_and_receipt_reuse(inventory, booking_member):
    first = reserve(booking_member, workshop_request(inventory))
    second = reserve(booking_member, workshop_request(inventory))
    key = uuid.uuid4()
    settle(booking_member, first, key=key, reference="receipt-1")
    settle(booking_member, first, key=key, reference="receipt-1")
    assert LedgerEntry.objects.count() == 1
    with pytest.raises(ValidationError):
        settle(booking_member, second, key=key, reference="receipt-1")
    with pytest.raises(ValidationError):
        settle(booking_member, second, reference="receipt-1")
    with pytest.raises(ValidationError):
        settle(booking_member, first, 2501, key=key, reference="receipt-1")
    cancel_reservation(booking_member.user, booking_member.profile.school.slug, first.public_id)
    assert settle(booking_member, first, key=key, reference="receipt-1").state == "canceled"
    with pytest.raises(ValidationError):
        settle(booking_member, first, 1)


@pytest.mark.parametrize("amount", [0,-1,10001,1.5,True,"1"])
def test_invalid_settlement_amounts(inventory, booking_member, amount):
    item = reserve(booking_member, workshop_request(inventory))
    with pytest.raises(ValidationError):
        settle(booking_member, item, amount)
    assert LedgerEntry.objects.count() == 0


@pytest.mark.parametrize("total,deposit", [(-1,0),(100,101),(100,-1),(100,None),(None,0),(True,0),(1.25,0),(100000000,0)])
def test_invalid_quotes_are_atomic(inventory, booking_member, total, deposit):
    with pytest.raises(ValidationError):
        reserve(booking_member, workshop_request(inventory), total, deposit)
    assert not PaymentOrder.objects.exists() and not Reservation.objects.exists()


def test_default_off_stale_quote_and_withdrawn_source(inventory, booking_member):
    profile = inventory[0]
    source = workshop_request(inventory)
    profile.native_reservations_enabled = False
    profile.save()
    with pytest.raises(ValidationError):
        reserve(booking_member, source)
    profile.native_reservations_enabled = True
    profile.save()
    item = reserve(booking_member, source)
    with pytest.raises(ValidationError):
        reserve(booking_member, source, 10001)
    with pytest.raises(ValidationError):
        withdraw_request(booking_member.user, profile.school.slug, source.public_id)
    cancel_reservation(booking_member.user, profile.school.slug, item.public_id)
    withdraw_request(booking_member.user, profile.school.slug, source.public_id)
    other = workshop_request(inventory)
    other.status = "withdrawn"
    other.save()
    with pytest.raises(ValidationError):
        reserve(booking_member, other)
    with pytest.raises(ValidationError):
        create_reservation(booking_member.user, profile.school.slug, "bad", uuid.uuid4())


def test_capacity_all_or_nothing_expiry_and_late_payment(inventory, booking_member):
    first = reserve(booking_member, workshop_request(inventory, 3))
    second_source = workshop_request(inventory, 2)
    with pytest.raises(ValidationError):
        reserve(booking_member, second_source)
    assert Reservation.objects.count() == 1 and PaymentOrder.objects.count() == 1
    Reservation.objects.filter(pk=first.pk).update(hold_until=timezone.now()-timedelta(seconds=1))
    first.refresh_from_db()
    assert "expired" in first.state_label
    second = reserve(booking_member, second_source)
    assert settle(booking_member, first).state == "review"
    assert totals(first.order)["net_cents"] == 2500
    assert active_claims().get().pk == second.pk
    cancel_reservation(booking_member.user, booking_member.profile.school.slug, first.public_id)
    settle(booking_member, first, kind="refund")
    assert totals(first.order)["net_cents"] == 0


def test_expired_hold_can_reacquire_if_capacity_remains(inventory, booking_member):
    item = reserve(booking_member, workshop_request(inventory))
    Reservation.objects.filter(pk=item.pk).update(hold_until=timezone.now()-timedelta(seconds=1))
    assert settle(booking_member, item).state == "confirmed"


@pytest.mark.parametrize("change", ["started", "unpublished", "offering_unpublished", "moved"])
def test_late_payment_records_money_without_false_confirmation(inventory, booking_member, change):
    item = reserve(booking_member, workshop_request(inventory))
    if change == "started":
        Reservation.objects.filter(pk=item.pk).update(starts_at=timezone.now()-timedelta(minutes=1))
    elif change == "unpublished":
        Session.objects.filter(pk=item.session_id).update(is_published=False)
    elif change == "offering_unpublished":
        Offering.objects.filter(pk=inventory[1].pk).update(is_published=False)
    else:
        Session.objects.filter(pk=item.session_id).update(starts_at=item.starts_at+timedelta(minutes=1))
    assert settle(booking_member, item).state == "review"
    assert totals(item.order)["net_cents"] == 2500


def test_party_room_conflicts_and_proposal_is_locked(inventory, booking_member):
    source = party_request(inventory)
    first = reserve(booking_member, source)
    assert first.exclusive and first.order.total_cents == 40000 and first.order.deposit_cents == 10000
    assert reserve(booking_member, source).pk == first.pk
    with pytest.raises(ValidationError):
        reserve(booking_member, party_request(inventory, space="shared"))
    with pytest.raises(ValidationError):
        reserve(booking_member, party_request(inventory, start=inventory[3].starts_at))
    changed = review_inquiry(booking_member.user, booking_member.profile.school.slug, source.public_id,
        {"status":"reviewed", "room":source.room_id,"proposed_total":"450", "proposed_deposit":"100", "revision":0})
    assert not changed.is_valid() and "locked" in str(changed.errors)
    source.refresh_from_db()
    assert source.proposed_total == 400
    adjacent = party_request(inventory, start=source.ends_at)
    assert reserve(booking_member, adjacent).pk
    # A published class cannot take the room while this party holds it.
    session = Session(offering=inventory[1], room=inventory[2], starts_at=source.starts_at, ends_at=source.ends_at, capacity=6, is_published=True)
    with pytest.raises(ValidationError):
        session.full_clean()


@pytest.mark.parametrize("field,value", [("status","pending"),("room",None),("proposed_total",None)])
def test_unreviewed_party_cannot_reserve(inventory, booking_member, field, value):
    source = party_request(inventory)
    if field == "proposed_total":
        source.proposed_deposit = None
    setattr(source, field, value)
    source.save()
    with pytest.raises(ValidationError):
        reserve(booking_member, source)


def test_shared_party_capacity_and_half_open_boundaries(inventory, booking_member):
    one = party_request(inventory, size=3, space="shared")
    reserve(booking_member, one)
    two = party_request(inventory, size=3, space="shared")
    reserve(booking_member, two)
    with pytest.raises(ValidationError):
        reserve(booking_member, party_request(inventory, size=1, space="shared"))
    room = inventory[2]
    room.capacity = 5
    with pytest.raises(ValidationError):
        room.full_clean()
    assert peak_quantity([(one.starts_at,one.ends_at,3),(one.ends_at,one.ends_at+timedelta(hours=1),3)]) == 3
    assert reserve(booking_member, party_request(inventory, size=6, space="shared", start=one.ends_at)).pk


def test_confirmed_workshop_keeps_room_even_when_session_unpublished(inventory, booking_member):
    item = reserve(booking_member, workshop_request(inventory))
    settle(booking_member, item)
    Session.objects.filter(pk=inventory[3].pk).update(is_published=False)
    with pytest.raises(ValidationError):
        reserve(booking_member, party_request(inventory, size=1, space="shared", start=item.starts_at))


def test_setup_cannot_move_reserved_session_or_shrink_total_capacity(inventory, booking_member):
    reserve(booking_member, workshop_request(inventory))
    reserve(booking_member, workshop_request(inventory))
    session = inventory[3]
    session.capacity = 3
    with pytest.raises(ValidationError):
        session.full_clean()
    session.capacity = 4
    session.starts_at += timedelta(minutes=1)
    with pytest.raises(ValidationError):
        session.full_clean()


def test_refunds_and_account_mode_guards(inventory, booking_member):
    item = reserve(booking_member, workshop_request(inventory))
    with pytest.raises(ValidationError):
        settle(booking_member, item, kind="refund")
    settle(booking_member, item)
    cancel_reservation(booking_member.user, booking_member.profile.school.slug, item.public_id)
    with pytest.raises(ValidationError):
        settle(booking_member, item, 2501, kind="refund")
    for changes in [{"environment":"live"},{"environment":"test","merchant_reference":"acct_other"}]:
        PaymentOrder.objects.filter(pk=item.order_id).update(**changes)
        with pytest.raises(ValidationError):
            settle(booking_member, item, kind="refund")


def test_shared_ledger_generic_order_idempotency_and_validation(inventory, booking_member):
    school = inventory[0].school
    values = dict(school=school,purpose="application",source_key="future-app",total_cents=5000,deposit_cents=0,description="Fee")
    order = create_order(**values)
    assert create_order(**values).pk == order.pk
    with pytest.raises(ValidationError):
        create_order(**{**values,"description":"changed"})
    with pytest.raises(ValidationError):
        record_offline(order=order,actor=booking_member.user,key=uuid.uuid4(),kind="charge",amount_cents=100,reference="r")


def test_audit_failure_rolls_back_everything(inventory, booking_member, monkeypatch):
    source = workshop_request(inventory)
    original = AdminAuditLog.objects.create
    def fail(**kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(AdminAuditLog.objects,"create",fail)
    with pytest.raises(RuntimeError):
        reserve(booking_member,source)
    assert not Reservation.objects.exists() and not PaymentOrder.objects.exists()
    monkeypatch.setattr(AdminAuditLog.objects,"create",original)
    item = reserve(booking_member,source)
    monkeypatch.setattr(AdminAuditLog.objects,"create",fail)
    with pytest.raises(RuntimeError):
        settle(booking_member,item)
    assert not LedgerEntry.objects.exists()
    item.refresh_from_db()
    assert item.state == "held"


def test_owner_browser_flow_forms_and_cancel(inventory, booking_member, client):
    client.force_login(booking_member.user)
    listing = client.get(path(booking_member, "reservations"))
    assert listing.status_code == 200 and b"Test workspace" not in listing.content
    source = workshop_request(inventory)
    url = path(booking_member,"reservation_from_workshop",public_id=source.public_id)
    quote = client.get(url)
    assert quote.status_code == 200 and b"<h2>Create reservation</h2>" in quote.content
    assert b"This quote is fixed after creation" in quote.content
    assert client.post(url,{"total":"100","deposit":"101","confirm":"on"}).status_code == 400
    response = client.post(url,{"total":"100","deposit":"25","confirm":"on"})
    assert response.status_code == 302
    item = Reservation.objects.get()
    detail = response.url
    page = client.get(detail)
    assert b"Held - awaiting payment" in page.content
    assert b"Test financial records" not in page.content
    assert b"Record offline settlement" not in page.content
    # The old offline-settlement payload is still rejected; Terminal recording
    # requires its explicit action, confirmation, receipt and stale-balance check.
    assert client.post(detail, {"kind": "payment", "amount": "25"}).status_code == 400
    settle(booking_member, item)
    page = client.get(detail)
    assert b"Confirmed" in page.content and b"$75.00" in page.content
    assert LedgerEntry.objects.count() == 1
    cancel = path(booking_member,"reservation_cancel",public_id=item.public_id)
    assert client.post(cancel).status_code == 400
    assert client.get(cancel).status_code == 200
    assert client.post(cancel,{"key": uuid.uuid4(), "resolution": "without_refund", "reason": "Family canceled"}).status_code == 302
    assert b"still received" in client.get(detail).content
    assert b"No pending participants" in client.get(path(booking_member,"preparation",pk=inventory[3].pk)).content
    assert client.post(detail, {"kind": "refund", "amount": "25"}).status_code == 400
    assert b"still received" in client.get(detail).content
    assert b"View reservation" in client.get(path(booking_member,"request_detail",public_id=source.public_id)).content


def test_party_browser_quote_is_server_owned(inventory, booking_member, client):
    client.force_login(booking_member.user)
    source = party_request(inventory)
    url = path(booking_member,"reservation_from_party",public_id=source.public_id)
    assert client.get(url).context["form"].fields["total"].disabled
    response = client.post(url,{"total":"0","deposit":"0","confirm":"on", "source_revision":0})
    assert response.status_code == 302
    assert Reservation.objects.get().order.total_cents == 40000
    detail = client.get(path(booking_member,"party_detail",public_id=source.public_id))
    assert detail.status_code == 302 and detail.url == response.url
    assert b"Party details" in client.get(detail.url).content
    overlapping = party_request(inventory)
    assert client.post(path(booking_member,"reservation_from_party",public_id=overlapping.public_id),{"confirm":"on", "source_revision":0}).status_code == 400


def test_stale_party_quote_cannot_be_approved(inventory, booking_member, client):
    client.force_login(booking_member.user)
    source = party_request(inventory)
    url = path(booking_member,"reservation_from_party",public_id=source.public_id)
    assert client.get(url).context["form"]["source_revision"].value() == 0
    PartyRequest.objects.filter(pk=source.pk).update(revision=1, proposed_total=450)
    response = client.post(url,{"confirm":"on", "source_revision":0})
    assert response.status_code == 400 and b"proposal changed" in response.content
    assert not Reservation.objects.exists() and not PaymentOrder.objects.exists()
    assert client.post(url,{"confirm":"on"}).status_code == 400
    assert client.post(url,{"confirm":"on", "source_revision":1}).status_code == 302
    assert Reservation.objects.get().order.total_cents == 45000


def test_partial_late_money_is_visible_for_review(inventory, booking_member):
    item = reserve(booking_member,workshop_request(inventory))
    Reservation.objects.filter(pk=item.pk).update(hold_until=timezone.now()-timedelta(seconds=1))
    assert settle(booking_member,item,100).state == "review"
    assert totals(item.order)["net_cents"] == 100


def test_reserved_session_can_stay_or_grow_and_new_room_validation(inventory, booking_member):
    reserve(booking_member,workshop_request(inventory))
    session = inventory[3]
    session.capacity = 5
    session.full_clean()
    Room(location=inventory[1].location,name="New",capacity=1).full_clean()


def test_withdrawal_route_shows_reservation_guard(inventory, booking_member, client):
    source = workshop_request(inventory)
    reserve(booking_member,source)
    client.force_login(booking_member.user)
    response = client.post(path(booking_member,"request_withdraw",public_id=source.public_id),{"confirm":"yes"},follow=True)
    assert b"Cancel the reservation before withdrawing" in response.content
    source.refresh_from_db()
    assert source.status == "pending"


def test_receipts_do_not_misrepresent_later_reservations(inventory, booking_member, client):
    from scheduling.requests import browser_key
    from django.test import RequestFactory
    source = workshop_request(inventory)
    party = party_request(inventory)
    # Use the same public browser binding as the existing receipt routes.
    session = client.session
    session["bookings_request_browser"] = "browser-token"
    session.save()
    request = RequestFactory().get("/")
    request.session = session
    browser = browser_key(request,create=True)
    request.session.save()
    client.cookies["sessionid"] = request.session.session_key
    WorkshopRequest.objects.filter(pk=source.pk).update(browser_digest=browser)
    PartyRequest.objects.filter(pk=party.pk).update(browser_digest=browser)
    reserve(booking_member,source)
    reserve(booking_member,party)
    for name,item in [("request_received",source),("party_received",party)]:
        response = client.get(path(booking_member,name,public_id=item.public_id))
        assert response.status_code == 200
        assert b"original" in response.content and b"not a booking or payment confirmation" in response.content
        assert b"$400" not in response.content


def test_disable_stops_new_sales_not_existing_settlement(inventory, booking_member, client):
    client.force_login(booking_member.user)
    item = reserve(booking_member, workshop_request(inventory))
    settings = path(booking_member,"reservation_settings")
    assert client.post(settings,{"action":"bad"}).status_code == 400
    assert client.post(settings,{"action":"disable"}).status_code == 302
    paused = client.get(path(booking_member,"reservations"))
    assert b"New reservations:" not in paused.content
    assert b"Accept new reservations" not in paused.content
    with pytest.raises(ValidationError):
        reserve(booking_member,workshop_request(inventory))
    assert settle(booking_member,item).state == "confirmed"
    # A checked HTML switch submits both the hidden fallback and the enabled value.
    assert client.post(settings,"action=disable&action=enable", content_type="application/x-www-form-urlencoded").status_code == 302
    enabled = client.get(path(booking_member,"reservations"))
    assert b"New reservations:" not in enabled.content
    booking_member.profile.refresh_from_db()
    assert booking_member.profile.native_reservations_enabled
    item.refresh_from_db()
    assert item.state == "confirmed" and Reservation.objects.count() == 1


def test_reservation_filters_have_active_state_and_specific_empty_message(booking_member, client):
    client.force_login(booking_member.user)
    target = path(booking_member, "reservations")
    normal = client.get(target)
    assert b'booking-quick-filters' in normal.content
    assert b'All reservations</a>' not in normal.content
    assert b"No payments need follow-up." not in normal.content
    attention = client.get(target, {"attention": 1})
    assert attention.context['attention_only']
    assert b'Balance due' in attention.content
    assert b'Payment follow-up' not in attention.content
    assertContains(attention, 'No payments need follow-up.')


@pytest.mark.parametrize("role",["viewer","scheduler"])
def test_read_only_money_permissions(inventory, booking_member, client, role):
    source = workshop_request(inventory)
    item = reserve(booking_member,source)
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    detail = path(booking_member,"reservation_detail",public_id=item.public_id)
    assert client.get(detail).status_code == 200
    assert b'New reservations:' not in client.get(path(booking_member,"reservations")).content
    assert b"Record offline entry" not in client.get(detail).content
    assert client.post(detail,{}).status_code == 404
    assert client.get(path(booking_member,"reservation_from_workshop",public_id=source.public_id)).status_code == 404
    assert client.post(path(booking_member,"reservation_settings"),{"action":"enable"}).status_code == 404
    assert client.post(path(booking_member,"reservation_cancel",public_id=item.public_id),{"confirm":"yes"}).status_code == 404


def test_tenant_csrf_and_get_write_denial(inventory, booking_member, client):
    source = workshop_request(inventory)
    item = reserve(booking_member,source)
    detail = path(booking_member,"reservation_detail",public_id=item.public_id)
    assert client.get(detail).status_code == 302
    outsider = get_user_model().objects.create_user("outsider-reservation")
    client.force_login(outsider)
    assert client.get(detail).status_code == 404
    client.force_login(booking_member.user)
    assert client.get(detail.replace(str(item.public_id),str(uuid.uuid4()))).status_code == 404
    assert client.get(path(booking_member,"reservation_cancel",public_id=item.public_id)).status_code == 200
    item.refresh_from_db()
    assert item.state == "held"  # GET only opens the confirmation screen.
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(booking_member.user)
    assert csrf.post(detail,{}).status_code == 403
    # Enrollment permission and subscriptions were never granted to booking-only staff.
    assert client.get(f"/schools/{booking_member.profile.school.slug}/admin/").status_code in {302,403,404}


@pytest.mark.parametrize("changes",[{"state":"paid"},{"quantity":0},{"workshop_request":None},{"ends_at":None}])
def test_reservation_database_constraints(inventory, booking_member, changes):
    item = reserve(booking_member,workshop_request(inventory))
    with pytest.raises(IntegrityError),transaction.atomic():
        Reservation.objects.filter(pk=item.pk).update(**changes)


@pytest.mark.parametrize("changes",[{"total_cents":-1},{"deposit_cents":10001},{"currency":"eur"},{"environment":"bad"}])
def test_order_database_constraints(inventory, booking_member, changes):
    item = reserve(booking_member,workshop_request(inventory))
    with pytest.raises(IntegrityError),transaction.atomic():
        PaymentOrder.objects.filter(pk=item.order_id).update(**changes)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind",["workshop","party"])
def test_competing_groups_cannot_oversell(inventory, booking_member, kind):
    sources = [workshop_request(inventory,3) for _ in range(2)] if kind == "workshop" else [party_request(inventory) for _ in range(2)]
    barrier = Barrier(2)
    def attempt(source):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            try:
                return reserve(booking_member,source).pk
            except ValidationError:
                return None
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt,sources))
    assert sum(result is not None for result in results) == 1
    assert Reservation.objects.count() == PaymentOrder.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_duplicate_offline_writes_are_one_financial_event(inventory, booking_member):
    item = reserve(booking_member,workshop_request(inventory))
    key = uuid.uuid4()
    barrier = Barrier(2)
    def attempt(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return settle(booking_member,item,key=key,reference="same-transfer").state
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(attempt,range(2))) == ["confirmed","confirmed"]
    assert LedgerEntry.objects.count() == 1 and totals(item.order)["net_cents"] == 2500
