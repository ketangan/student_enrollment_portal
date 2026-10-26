from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.http import Http404, QueryDict
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments.models import LedgerEntry, PaymentAccount, PaymentOrder
from scheduling import staff_parties
from scheduling.models import Location, Offering, PartyRequest, Reservation, Room, SchedulingProfile
from scheduling.parties import review_inquiry
from scheduling.reservations import create_reservation
from .test_square_connection import setup
from .test_square_locations import mapped

pytestmark = pytest.mark.django_db


@pytest.fixture
def studio(booking_member):
    location = Location.objects.create(profile=booking_member.profile, name="East studio", timezone_name="America/New_York")
    Room.objects.create(location=location, name="Party room", capacity=30)
    return Offering.objects.create(location=location, name="Workshop", accepts_party_requests=True)


def route(member, name="staff_party_create", **kwargs):
    return reverse("scheduling:" + name, kwargs={"school_slug": member.profile.school.slug, **kwargs})


def data_for(member, studio, **changes):
    data = QueryDict(mutable=True)
    data.update({"request_token": staff_parties.token_for(member.user, member.profile),
        "offering": str(studio.pk), "organizer_name": "Phone customer", "organizer_email": "phone@example.com",
        "starts_at": (timezone.now().astimezone(ZoneInfo(studio.location.timezone_name)) + timedelta(days=6)).strftime("%Y-%m-%dT%H:%M"),
        "group_size": "24", "space": "exclusive", **changes})
    return data


def test_participant_count_is_whole_number_with_numeric_control(booking_member, studio):
    form = staff_parties.StaffPartyForm(data_for(booking_member, studio, group_size="1.5"),
        profile=booking_member.profile)
    assert not form.is_valid()
    assert form.errors["group_size"] == ["Enter a whole number of participants."]
    assert form.fields["group_size"].widget.attrs["step"] == "1"
    assert form.fields["group_size"].widget.attrs["inputmode"] == "numeric"


def test_staff_entry_reuses_inquiry_review_and_online_payment_link(booking_member, studio, client, settings):
    profile = booking_member.profile
    profile.native_reservations_enabled = True
    profile.save()
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_local_tests"
    PaymentAccount.objects.create(school=profile.school, reference="acct_local_test", enabled=True)
    client.force_login(booking_member.user)
    response = client.post(route(booking_member), data_for(booking_member, studio))
    assert response.status_code == 302
    item = PartyRequest.objects.get()
    assert item.group_size == 24 and item.status == "pending" and item.ends_at is None
    assert item.room is None and item.proposed_total is None
    assert item.materials == [] and not Reservation.objects.exists() and not PaymentOrder.objects.exists()
    audit = AdminAuditLog.objects.get(model_label="scheduling.partyrequest")
    assert audit.actor == booking_member.user and audit.changes["created_by_staff"]
    assert audit.action == "add"
    assert client.get(response.url).status_code == 200
    review = review_inquiry(booking_member.user, profile.school.slug, item.public_id, {
        "revision": "0", "status": "reviewed", "room": str(studio.location.rooms.get().pk),
        "duration_minutes": "120", "proposed_total": "1560", "payment_at_booking": "deposit",
        "proposed_deposit": "600"})
    assert review.is_valid(), review.errors
    item.refresh_from_db()
    create_url = route(booking_member, "reservation_from_party", public_id=item.public_id)
    assert b"30-minute" not in client.get(create_url).content
    assert client.post(create_url, {"source_revision": item.revision, "online": "on", "confirm": "on"}).status_code == 302
    reservation = Reservation.objects.get(party_request=item)
    assert reservation.quantity == 24 and reservation.state == "confirmed"
    assert reservation.snapshot["staff_managed"]
    assert reservation.order.total_cents == 156000 and reservation.order.deposit_cents == 60000
    assert not LedgerEntry.objects.exists()
    page = client.get(route(booking_member, "reservation_detail", public_id=reservation.public_id))
    assert b"Copy payment link" in page.content
    assert page.context["share_payment_url"].startswith("http")
    from scheduling.checkout import payment_token
    payment_page = Client().get(route(booking_member, "customer_payment", token=payment_token(reservation)))
    assert payment_page.status_code == 200
    assert b"Pay deposit: $600.00" in payment_page.content
    assert b"Pay in full: $1560.00" in payment_page.content
    assert Client().get(route(booking_member, "party_received", public_id=item.public_id)).status_code == 404


def test_retry_is_one_request_and_changed_retry_does_not_overwrite(booking_member, studio):
    data = data_for(booking_member, studio)
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert form.is_valid()
    retry, _ = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert retry.pk == item.pk
    data["group_size"] = "25"
    rejected, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert rejected is None and "already saved" in str(form.errors)
    assert PartyRequest.objects.count() == 1
    assert AdminAuditLog.objects.filter(model_label="scheduling.partyrequest").count() == 1
    item.refresh_from_db()
    assert item.group_size == 24


def test_custom_group_reuses_mapped_square_account_and_defers_materials(mapped, booking_member):
    from scheduling.models import InventoryCatalog
    connection, location, _, _, _ = mapped
    profile = booking_member.profile
    profile.payment_provider = "square"
    profile.native_reservations_enabled = True
    profile.save()
    catalog = InventoryCatalog.objects.create(profile=profile, name="Party materials")
    offering = Offering.objects.create(location=location, name="Custom party", accepts_party_requests=True,
        inventory_catalog=catalog)
    room = Room.objects.create(location=location, name="Large room", capacity=30)
    item, form = staff_parties.create(booking_member.user, profile.school.slug, data_for(booking_member, offering))
    assert item and form.is_valid()
    form = review_inquiry(booking_member.user, profile.school.slug, item.public_id, {
        "revision": "0", "status": "reviewed", "room": str(room.pk), "duration_minutes": "120",
        "proposed_total": "1560", "payment_at_booking": "deposit", "proposed_deposit": "600",
        "materials-TOTAL_FORMS": "0", "materials-INITIAL_FORMS": "0"})
    assert form.is_valid(), form.errors
    item.refresh_from_db()
    reservation = create_reservation(booking_member.user, profile.school.slug, "party", item.public_id,
        source_revision=item.revision, online=True, staff_managed=True)
    assert reservation.state == "confirmed"
    assert reservation.order.account == connection.mappings.get(location=location).account
    assert reservation.order.total_cents == 156000 and reservation.order.deposit_cents == 60000
    assert reservation.snapshot["guest_selections_pending"] and not reservation.stock_allocations.exists()
    assert not LedgerEntry.objects.exists()


@pytest.mark.parametrize("field,value", [("offering", "999999"), ("offering", "bad"),
    ("organizer_email", "not-email"), ("organizer_name", ""), ("organizer_phone", "abc"),
    ("group_size", "0"), ("group_size", "10001"), ("group_size", "1.5"),
    ("group_size", "-1"), ("group_size", "abc"), ("space", "invalid"),
    ("starts_at", "2000-01-01T10:00"), ("starts_at", "bad"), ("request_token", "bad")])
def test_invalid_input_writes_nothing(booking_member, studio, field, value):
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug,
        data_for(booking_member, studio, **{field: value}))
    assert item is None and form.errors
    assert not PartyRequest.objects.exists()


def test_dates_use_studio_timezone_not_server(booking_member, studio):
    data = data_for(booking_member, studio)
    with timezone.override("Asia/Tokyo"):
        item, _ = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert item.starts_at.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%dT%H:%M") == data["starts_at"]
    assert item.offering_snapshot["timezone"] == "America/New_York"


def test_dst_ambiguous_time_rejected(booking_member, studio, monkeypatch):
    from datetime import datetime, timezone as dt_timezone
    monkeypatch.setattr(timezone, "now", lambda: datetime(2026, 10, 26, tzinfo=dt_timezone.utc))
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug,
        data_for(booking_member, studio, starts_at="2026-11-01T01:30"))
    assert item is None and "starts_at" in form.errors


def test_foreign_and_unassigned_locations_are_rejected(booking_member, studio):
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="another-studio"), enabled=True)
    location = Location.objects.create(profile=other, name="Other")
    foreign = Offering.objects.create(location=location, name="Secret", accepts_party_requests=True)
    data = data_for(booking_member, studio, offering=str(foreign.pk))
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert item is None and "offering" in form.errors
    booking_member.all_locations = False
    booking_member.save()
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data_for(booking_member, studio))
    assert item is None and "offering" in form.errors
    booking_member.locations.add(studio.location)
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data_for(booking_member, studio))
    assert item and form.is_valid()


@pytest.mark.parametrize("change", ["viewer", "inactive", "disabled", "capability"])
def test_permission_revocation_blocks_posts(booking_member, studio, change):
    data = data_for(booking_member, studio)
    if change == "viewer":
        booking_member.role = "viewer"
        booking_member.save()
    elif change == "inactive":
        booking_member.is_active = False
        booking_member.save()
    elif change == "disabled":
        booking_member.profile.enabled = False
        booking_member.profile.save()
    else:
        booking_member.profile.capabilities = ["workshops"]
        booking_member.profile.save()
    with pytest.raises(Http404):
        staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert not PartyRequest.objects.exists()


def test_token_cannot_cross_users_or_profiles(booking_member, studio):
    other = get_user_model().objects.create_user("other-staff")
    data = data_for(booking_member, studio, request_token=staff_parties.token_for(other, booking_member.profile))
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert item is None and "expired" in str(form.errors)
    from django.core import signing
    data["request_token"] = signing.dumps({"user": booking_member.user.pk, "profile": -1, "key": "invalid"}, salt=staff_parties.TOKEN_SALT)
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert item is None and form.errors


def test_expired_token_and_paused_offering_are_rejected(booking_member, studio, monkeypatch):
    from django.core import signing
    import time
    now = time.time()
    with monkeypatch.context() as patch:
        patch.setattr(signing.time, "time", lambda: now - 7201)
        data = data_for(booking_member, studio)
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert item is None and "expired" in str(form.errors)
    studio.accepts_party_requests = False
    studio.save()
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data_for(booking_member, studio))
    assert item is None and "offering" in form.errors


def test_scheduler_can_enter_request_but_viewer_cannot_and_csrf_is_required(booking_member, studio):
    booking_member.role = "scheduler"
    booking_member.save()
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data_for(booking_member, studio))
    assert item and form.is_valid()
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(booking_member.user)
    assert secure.post(route(booking_member), data_for(booking_member, studio)).status_code == 403


def test_reused_token_after_location_reassignment_is_not_a_duplicate_key_error(booking_member, studio):
    data = data_for(booking_member, studio)
    staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    location = Location.objects.create(profile=booking_member.profile, name="New assignment")
    offering = Offering.objects.create(location=location, name="Other workshop", accepts_party_requests=True)
    booking_member.all_locations = False
    booking_member.save()
    booking_member.locations.set([location])
    data["offering"] = str(offering.pk)
    item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
    assert item is None and "already saved" in str(form.errors)
    assert PartyRequest.objects.count() == 1


def test_staff_form_permissions_and_required_markers(booking_member, studio, client):
    assert client.get(route(booking_member)).status_code == 302
    client.force_login(booking_member.user)
    response = client.get(route(booking_member))
    assert response.status_code == 200 and "no-store" in response["Cache-Control"]
    assert response.context["booking_navigation"]["section"]["key"] == "reservations"
    assert response.context["form"]["offering"].value() is None
    assert not response.context["form"].fields["age_range"].required
    assert b"Continue to review" in response.content and b'aria-hidden="true"> *</span>' in response.content
    invalid = client.post(route(booking_member), data_for(booking_member, studio, organizer_email="bad"))
    assert invalid.status_code == 400 and b"bad" in invalid.content
    assert b"New party request" in client.get(route(booking_member, "parties")).content
    booking_member.role = "viewer"
    booking_member.save()
    assert client.get(route(booking_member)).status_code == 404
    assert b"New party request" not in client.get(route(booking_member, "parties")).content


@pytest.mark.django_db(transaction=True)
def test_concurrent_retries_create_once(booking_member, studio):
    data = data_for(booking_member, studio)
    barrier = Barrier(2)

    def save(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            item, form = staff_parties.create(booking_member.user, booking_member.profile.school.slug, data)
            assert form.is_valid()
            return item.pk
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert results[0] == results[1]
    assert PartyRequest.objects.count() == 1
