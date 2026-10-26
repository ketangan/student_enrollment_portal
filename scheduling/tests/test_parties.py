from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from threading import Barrier
from zoneinfo import ZoneInfo
import uuid

import pytest
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.http import QueryDict
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School, Submission
from scheduling.forms import LocationForm
from scheduling.models import Location, Offering, PartyPackage, PartyRequest, Room, SchedulingProfile, Session, WorkshopRequest
from scheduling.parties import TOKEN_SALT, inquiry_token, review_inquiry, room_overlaps, submit_inquiry
from scheduling.setup import save_setup, set_publication

pytestmark = pytest.mark.django_db


@pytest.fixture
def party_offering(booking_member):
    location = Location.objects.create(profile=booking_member.profile, name="Main Studio")
    Room.objects.create(location=location, name="Room 1", capacity=12)
    return Offering.objects.create(location=location, name="Creative workshop", is_published=True, accepts_party_requests=True)


def route(name, offering, **kwargs):
    return reverse("scheduling:" + name, kwargs={"school_slug": offering.location.profile.school.slug, **kwargs})


def payload(token):
    data = QueryDict(mutable=True)
    data.update({"request_token": token, "organizer_name": "Morgan Demo", "organizer_email": "morgan@example.com",
        "starts_at": (timezone.now().astimezone(ZoneInfo("America/Los_Angeles")) + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M"),
        "duration_minutes": "120", "group_size": "8", "space": "exclusive", "customer_notes": "Birthday group, blue designs preferred."})
    return data


def begin(client, offering):
    response = client.get(route("party_inquiry", offering, pk=offering.pk))
    assert response.status_code == 200
    return payload(response.context["request_token"])


def send(client, offering, data):
    return client.post(route("party_inquiry", offering, pk=offering.pk), data)


def create_inquiry(client, offering):
    assert send(client, offering, begin(client, offering)).status_code == 302
    return PartyRequest.objects.latest("pk")


def review_data(item, **changes):
    return {"revision": str(item.revision), "status": "reviewed", "room": str(item.offering.location.rooms.first().pk),
            "duration_minutes": "120", "proposed_total": "400.00", "proposed_deposit": "100.00", "preparation_notes": "Prepare eight stations.", **changes}


def test_full_party_flow_persists_review_and_preparation_without_booking_or_payment(client, party_offering, booking_member):
    before = (Session.objects.count(), WorkshopRequest.objects.count(), Submission.objects.count())
    data = begin(client, party_offering)
    response = send(client, party_offering, data)
    item = PartyRequest.objects.get()
    assert item.status == "pending" and item.room is None and item.group_size == 8
    assert item.space == "exclusive" and item.proposed_balance is None
    assert item.ends_at is None
    assert str(item) == str(item.public_id)
    receipt = client.get(response.url)
    assert b"Party inquiry received" in receipt.content and b"Your party is not booked yet" in receipt.content
    assert b"no payment has been collected" in receipt.content
    assert b"Reference:" not in receipt.content
    assert str(item.public_id).encode() not in receipt.content
    assert route("public_schedule", party_offering).encode() not in receipt.content
    assert b"You can close this page." in receipt.content
    assert b"morgan@example.com" not in receipt.content and b"Birthday group" not in receipt.content
    assert "no-store" in receipt["Cache-Control"]
    assert Client().get(response.url).status_code == 404
    other = Client()
    begin(other, party_offering)
    assert other.get(response.url).status_code == 404
    staff = Client()
    staff.force_login(booking_member.user)
    detail_url = route("party_detail", party_offering, public_id=item.public_id)
    assert b"No room assigned" in staff.get(detail_url).content
    assert staff.post(detail_url, review_data(item)).status_code == 302
    item.refresh_from_db()
    assert item.status == "reviewed" and item.proposed_balance == Decimal("300.00") and item.revision == 1
    assert item.ends_at - item.starts_at == timedelta(hours=2)
    detail = staff.get(detail_url)
    assert b"$300.00 USD" in detail.content and b"Prepare eight stations." in detail.content
    assert b"Reviewed (not booked)" in detail.content and b"No room is held" in detail.content
    assert b"No overlapping published" in detail.content
    assert b"Morgan Demo" in staff.get(route("parties", party_offering)).content
    public_receipt = client.get(response.url).content.decode().replace(str(item.public_id), "")
    assert "400" not in public_receipt  # A random UUID may legitimately contain these digits.
    audit_text = str(list(AdminAuditLog.objects.filter(model_label="scheduling.partyrequest").values("changes", "object_repr")))
    assert "morgan@example.com" not in audit_text and "Prepare eight" not in audit_text
    assert before == (Session.objects.count(), WorkshopRequest.objects.count(), Submission.objects.count())
    assert not party_offering.location.profile.school.is_demo


def test_duplicate_submission_and_stale_replay_do_not_duplicate(client, party_offering):
    data = begin(client, party_offering)
    first = send(client, party_offering, data)
    party_offering.accepts_party_requests = False
    party_offering.save()
    data["csrfmiddlewaretoken"] = "rotated"
    assert send(client, party_offering, data).url == first.url
    assert PartyRequest.objects.count() == 1
    data["group_size"] = "9"
    response = send(client, party_offering, data)
    assert response.status_code == 400 and b"already submitted" in response.content
    assert response.context["request_token"] == data["request_token"]
    assert PartyRequest.objects.count() == 1


@pytest.mark.parametrize("field,value", [
    ("organizer_name", ""), ("organizer_name", "x" * 101), ("organizer_email", "not-an-email"),
    ("starts_at", "invalid"), ("starts_at", "2000-01-01T10:00"), ("starts_at", "2040-01-01T10:00"),
    ("group_size", "0"), ("group_size", "10001"), ("group_size", "eight"),
    ("space", "confirmed"), ("customer_notes", "a" * 2001),
])
def test_invalid_customer_data_is_rejected(client, party_offering, field, value):
    data = begin(client, party_offering)
    data[field] = value
    response = send(client, party_offering, data)
    assert response.status_code == 400 and response.context["form"].errors
    assert not PartyRequest.objects.exists()


def test_location_timezone_and_dst_ambiguity(client, party_offering, monkeypatch):
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 10, 1, tzinfo=datetime_timezone.utc))
    package = PartyPackage.objects.create(offering=party_offering, name="Party", participant_limit=8,
        duration_minutes=120, total=400, deposit=100, is_published=True)
    data = begin(client, party_offering)
    data.update({"package": str(package.pk), "occasion": "Birthday", "age_range": "Adults"})
    data["starts_at"] = "2026-11-01T01:30"
    assert send(client, party_offering, data).status_code == 400
    data["starts_at"] = "2027-03-14T02:30"
    assert send(client, party_offering, data).status_code == 400
    data["starts_at"] = "2026-11-01T00:30"
    data["duration_minutes"] = "120"
    assert send(client, party_offering, data).status_code == 302
    item = PartyRequest.objects.get()
    assert item.starts_at.hour == 7 and item.ends_at.hour == 9


@pytest.mark.parametrize("change", ["disabled", "draft", "closed", "renamed"])
def test_intake_is_opt_in_and_configuration_changes_invalidate_forms(client, party_offering, change):
    data = begin(client, party_offering)
    if change == "disabled":
        SchedulingProfile.objects.filter(pk=party_offering.location.profile_id).update(enabled=False)
    else:
        field, value = {"draft": ("is_published", False), "closed": ("accepts_party_requests", False), "renamed": ("name", "Changed")}[change]
        Offering.objects.filter(pk=party_offering.pk).update(**{field: value})
    assert send(client, party_offering, data).status_code == (404 if change == "disabled" else 400)
    if change != "renamed":
        assert client.get(route("party_inquiry", party_offering, pk=party_offering.pk)).status_code == 404
    assert not PartyRequest.objects.exists()


def test_bad_wrong_browser_expired_and_wrong_offering_tokens(client, party_offering, monkeypatch):
    data = begin(client, party_offering)
    original = data["request_token"]
    data["request_token"] += "bad"
    assert send(client, party_offering, data).status_code == 400
    data["request_token"] = original
    assert send(Client(), party_offering, data).status_code == 400
    other = Client()
    begin(other, party_offering)
    assert send(other, party_offering, data).status_code == 400
    with monkeypatch.context() as m:
        now = signing.time.time()
        m.setattr(signing.time, "time", lambda: now + 7201)
        assert send(client, party_offering, data).status_code == 400
    token = signing.loads(original, salt=TOKEN_SALT)
    token["offering"] += 1
    for bad in [token, {}, {"key": 12}]:
        data["request_token"] = signing.dumps(bad, salt=TOKEN_SALT)
        assert send(client, party_offering, data).status_code == 400
    assert not PartyRequest.objects.exists()


@pytest.mark.parametrize("changes", [
    {"duration_minutes": ""}, {"duration_minutes": "14"}, {"duration_minutes": "721"}, {"duration_minutes": "1.5"},
    {"proposed_total": "-1"}, {"proposed_deposit": "401"}, {"proposed_deposit": "-1"},
    {"proposed_total": "NaN"}, {"proposed_total": "1000000"}, {"proposed_deposit": "1.001"},
    {"proposed_total": ""}, {"proposed_deposit": ""}, {"status": "confirmed"},
    {"status": "paid"}, {"room": ""}, {"preparation_notes": "x" * 3001}, {"revision": "-1"},
])
def test_invalid_review_does_not_change_saved_request(client, party_offering, booking_member, changes):
    item = create_inquiry(client, party_offering)
    client.force_login(booking_member.user)
    response = client.post(route("party_detail", party_offering, public_id=item.public_id), review_data(item, **changes))
    assert response.status_code == 400
    assert b"scheduling_party_proposal_valid" not in response.content
    assert b"Enter both the proposed total" not in response.content
    item.refresh_from_db()
    assert item.status == "pending" and item.proposed_total is None and item.revision == 0


@pytest.mark.parametrize("changes, field, message", [
    ({"proposed_total": "", "payment_at_booking": "deposit"}, "proposed_total", "Enter the total price for this party."),
    ({"proposed_deposit": ""}, "proposed_deposit", "Enter the deposit amount, or choose full payment."),
    ({"proposed_deposit": "", "payment_at_booking": "deposit"}, "proposed_deposit", "Enter a deposit of at least $0.50 per reservation."),
])
def test_missing_quote_values_have_field_errors(client, party_offering, booking_member, changes, field, message):
    item = create_inquiry(client, party_offering)
    form = review_inquiry(booking_member.user, party_offering.location.profile.school.slug,
        item.public_id, review_data(item, **changes))
    assert not form.is_valid()
    assert message in form.errors[field]
    assert not form.non_field_errors()


def test_stale_staff_save_cannot_overwrite_and_identical_save_is_noop(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    client.force_login(booking_member.user)
    url = route("party_detail", party_offering, public_id=item.public_id)
    data = review_data(item)
    assert client.post(url, data).status_code == 302
    response = client.post(url, {**data, "preparation_notes": "Stale notes"})
    assert response.status_code == 400 and b"Someone changed" in response.content
    assert b"Open saved review" in response.content
    item.refresh_from_db()
    assert item.preparation_notes == "Prepare eight stations."
    assert client.post(url, review_data(item)).status_code == 302
    item.refresh_from_db()
    assert item.revision == 1 and AdminAuditLog.objects.filter(model_label="scheduling.partyrequest").count() == 2


@pytest.mark.parametrize("role,allowed", [("owner", True), ("scheduler", True), ("viewer", False)])
def test_review_permissions_read_and_write(client, party_offering, booking_member, role, allowed):
    item = create_inquiry(client, party_offering)
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    url = route("party_detail", party_offering, public_id=item.public_id)
    assert (b"Save review" in client.get(url).content) == allowed
    assert client.post(url, review_data(item)).status_code == (302 if allowed else 404)
    item.refresh_from_db()
    assert item.revision == int(allowed)


def test_foreign_rooms_and_oversized_rooms_are_rejected(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    different = Location.objects.create(profile=booking_member.profile, name="Second location")
    other_room = Room.objects.create(location=different, name="Different", capacity=20)
    tiny_room = Room.objects.create(location=party_offering.location, name="Tiny", capacity=2)
    client.force_login(booking_member.user)
    for room in [other_room, tiny_room]:
        assert client.post(route("party_detail", party_offering, public_id=item.public_id), review_data(item, room=room.pk)).status_code == 400
    item.refresh_from_db()
    assert item.room is None
    item.room = other_room
    with pytest.raises(ValidationError):
        item.full_clean()


def test_overlaps_are_half_open_scoped_and_never_create_reservations(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    room = party_offering.location.rooms.get()
    item.room = room
    item.ends_at = item.starts_at + timedelta(hours=2)
    item.save()
    published = Session.objects.create(offering=party_offering, room=room, starts_at=item.starts_at,
        ends_at=item.ends_at, capacity=12, is_published=True)
    Session.objects.create(offering=party_offering, room=room, starts_at=item.ends_at,
        ends_at=item.ends_at + timedelta(hours=1), capacity=12, is_published=True)
    Session.objects.create(offering=party_offering, room=room, starts_at=item.starts_at,
        ends_at=item.ends_at, capacity=12, is_published=False)
    another = create_inquiry(client, party_offering)
    another.ends_at = another.starts_at + timedelta(hours=2)
    another.save()
    sessions, parties = room_overlaps(item)
    assert [s.pk for s in sessions] == [published.pk] and [p.pk for p in parties] == [another.pk]
    client.force_login(booking_member.user)
    url = route("party_detail", party_offering, public_id=item.public_id)
    page = client.get(url)
    assert b"private room cannot be promised" in page.content
    assert b"Published sessions" in page.content and b"Other active inquiries" in page.content
    assert client.post(url, review_data(item)).status_code == 302
    item.refresh_from_db()
    assert item.status == "reviewed" and Session.objects.count() == 3
    another.status = "declined"
    another.save()
    assert room_overlaps(item)[1] == []
    item.space = "shared"
    item.save()
    assert b"Shared-space capacity" in client.get(url).content
    assert client.post(url, review_data(item, status="withdrawn")).status_code == 302
    item.refresh_from_db()
    assert item.status == "withdrawn" and item.proposed_balance == Decimal("300")


def test_timezone_room_capacity_and_zero_quote_guards(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    location = party_offering.location
    assert LocationForm(profile=booking_member.profile, instance=location).fields["timezone_name"].disabled
    location.timezone_name = "Europe/London"
    with pytest.raises(ValidationError):
        location.full_clean()
    client.force_login(booking_member.user)
    url = route("party_detail", party_offering, public_id=item.public_id)
    assert client.post(url, review_data(item, proposed_total="0", proposed_deposit="0")).status_code == 302
    item.refresh_from_db()
    assert item.proposed_balance == 0
    room = item.room
    room.capacity = 7
    with pytest.raises(ValidationError):
        room.full_clean()
    assert client.post(url, review_data(item, status="declined", proposed_total="", proposed_deposit="")).status_code == 302
    room.full_clean()
    item.refresh_from_db()
    assert item.proposed_balance is None and b"Not set" in client.get(url).content


def test_party_only_publication_and_existing_workshop_behavior(client, party_offering, booking_member):
    party_offering.is_published = False
    party_offering.save()
    slug = booking_member.profile.school.slug
    set_publication(booking_member.user, slug, "workshops", party_offering.pk, True)
    response = client.get(route("public_schedule", party_offering))
    assert b"Plan a party" in response.content and b"Request places" not in response.content
    assert b"Party inquiries" in response.content and b"Registration not open" not in response.content
    assert not Session.objects.exists()
    assert not Offering.objects.create(location=party_offering.location, name="Untouched").accepts_party_requests
    set_publication(booking_member.user, slug, "workshops", party_offering.pk, False)
    assert b"Plan a party" not in client.get(route("public_schedule", party_offering)).content
    from scheduling.setup import build_form
    revision = build_form(booking_member.profile, "workshops", pk=party_offering.pk)["edit_revision"].value()
    form = save_setup(booking_member.user, slug, "workshops", {"name": "Party only", "location": party_offering.location_id,
        "description": "", "accepts_party_requests": "on", "edit_revision": revision}, pk=party_offering.pk)
    assert form.is_valid() and form.instance.accepts_party_requests


def test_tenant_and_receipt_privacy_escaping_and_injected_fields(client, party_offering, booking_member):
    data = begin(client, party_offering)
    data.update({"organizer_name": "<script>bad()</script>", "status": "paid", "proposed_total": "1", "room": "999"})
    response = send(client, party_offering, data)
    item = PartyRequest.objects.get()
    assert item.status == "pending" and item.room is None and item.proposed_total is None
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="other-party"), enabled=True)
    other_offering = Offering.objects.create(location=Location.objects.create(profile=other, name="Other"), name="Other", accepts_party_requests=True, is_published=True)
    assert client.get(route("party_inquiry", party_offering, pk=other_offering.pk)).status_code == 404
    assert client.get(f"/bookings/other-party/party-received/{item.public_id}/").status_code == 404
    for name, kwargs in [("parties", {}), ("party_detail", {"public_id": item.public_id})]:
        assert Client().get(route(name, party_offering, **kwargs)).status_code == 302
    client.force_login(booking_member.user)
    for path in ["parties/", f"parties/{item.public_id}/"]:
        assert client.get("/bookings/other-party/"+path).status_code == 404
    page = client.get(route("party_detail", party_offering, public_id=item.public_id))
    assert b"&lt;script&gt;" in page.content and b"<script>bad" not in page.content
    public = client.get(route("public_schedule", party_offering))
    assert b"morgan@example.com" not in public.content
    SchedulingProfile.objects.filter(pk=booking_member.profile_id).update(enabled=False)
    assert client.get(response.url).status_code == 404
    assert client.get(route("parties", party_offering)).status_code == 404


@pytest.mark.usefixtures("rate_limit_clock")
def test_csrf_rate_limiting_and_empty_list(party_offering, booking_member):
    client = Client(enforce_csrf_checks=True)
    data = begin(client, party_offering)
    assert send(client, party_offering, data).status_code == 403
    client.force_login(booking_member.user)
    assert b"No party inquiries yet" in client.get(route("parties", party_offering)).content
    item = create_inquiry(Client(), party_offering)
    assert client.post(route("party_detail", party_offering, public_id=item.public_id), review_data(item)).status_code == 403
    cache.clear()
    with override_settings(RATELIMIT_ENABLE=True):
        public = Client()
        data = begin(public, party_offering)
        for _ in range(10):
            assert send(public, party_offering, data).status_code == 302
        assert send(public, party_offering, data).status_code == 403
    cache.clear()


def test_audit_failure_rolls_back_both_create_and_review(client, party_offering, booking_member, monkeypatch):
    data = begin(client, party_offering)
    def fail(**kwargs):
        raise RuntimeError("No audit")
    with monkeypatch.context() as m:
        m.setattr(AdminAuditLog.objects, "create", fail)
        with pytest.raises(RuntimeError):
            send(client, party_offering, data)
    assert not PartyRequest.objects.exists()
    item = create_inquiry(client, party_offering)
    monkeypatch.setattr(AdminAuditLog.objects, "create", fail)
    with pytest.raises(RuntimeError):
        review_inquiry(booking_member.user, booking_member.profile.school.slug, item.public_id, review_data(item))
    item.refresh_from_db()
    assert item.status == "pending" and item.revision == 0 and item.proposed_total is None


@pytest.mark.parametrize("changes", [
    {"status": "confirmed"}, {"space": "other"}, {"group_size": 0},
    {"proposed_total": -1, "proposed_deposit": 0}, {"proposed_total": 1, "proposed_deposit": 2},
    {"proposed_total": 1, "proposed_deposit": None}, {"proposed_total": None, "proposed_deposit": 1},
])
def test_database_constraints_reject_invalid_states(client, party_offering, changes):
    item = create_inquiry(client, party_offering)
    with pytest.raises(IntegrityError), transaction.atomic():
        PartyRequest.objects.filter(pk=item.pk).update(**changes)


def test_database_time_uniqueness_and_paginated_list(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    with pytest.raises(IntegrityError), transaction.atomic():
        PartyRequest.objects.filter(pk=item.pk).update(ends_at=item.starts_at)
    copy = PartyRequest.objects.get(pk=item.pk)
    copy.pk = None
    copy.public_id = uuid.uuid4()
    with pytest.raises(IntegrityError), transaction.atomic():
        copy.save()
    copies = []
    for i in range(50):
        copies.append(PartyRequest(offering=party_offering, organizer_name=f"Group {i}", organizer_email="group@example.com",
            starts_at=item.starts_at, ends_at=item.ends_at, group_size=8, space="shared", submission_key=uuid.uuid4(),
            submission_digest="d"*64, browser_digest="b"*64, offering_snapshot=item.offering_snapshot))
    PartyRequest.objects.bulk_create(copies)
    client.force_login(booking_member.user)
    url = route("parties", party_offering)
    first = client.get(url)
    assert len(first.context["items"]) == 50 and b'aria-label="Next page"' in first.content
    second = client.get(url+"?page=2")
    assert len(second.context["items"]) == 1 and b'aria-label="Previous page"' in second.content


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_inquiries_create_one_record(party_offering):
    browser = "b"*64
    data = payload(inquiry_token(party_offering, browser))
    barrier = Barrier(2)
    def attempt():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return submit_inquiry(party_offering.location.profile.school.slug, party_offering.pk, data, browser)[0].pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: attempt(), range(2)))
    assert result[0] == result[1] and PartyRequest.objects.count() == 1
    assert AdminAuditLog.objects.filter(model_label="scheduling.partyrequest").count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_staff_reviews_only_one_wins(client, party_offering, booking_member):
    item = create_inquiry(client, party_offering)
    barrier = Barrier(2)
    def attempt(index):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            form = review_inquiry(booking_member.user, booking_member.profile.school.slug, item.public_id,
                review_data(item, preparation_notes=f"Writer {index}"))
            return form.is_valid()
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert sorted(results) == [False, True]
    item.refresh_from_db()
    assert item.revision == 1 and item.preparation_notes in {"Writer 0", "Writer 1"}
