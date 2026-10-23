from datetime import timedelta
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.http import Http404, QueryDict
from django.test import Client
from django.urls import reverse

from core.models import AdminAuditLog, School
from core.services.billing_access import billing_role
from scheduling.access import require_access, require_location_access, staff_locations
from scheduling.calendar import calendar_events, calendar_detail
from scheduling.models import Location, Offering, Room, Session, SchedulingMembership
from scheduling.reservation_queries import staff_reservations
from scheduling.reservations import settle_offline
from scheduling.team_access import membership_form, save_membership
from .test_reservations import inventory, workshop_request, party_request, reserve, path
from .test_checkout import merchant, online

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_locations(inventory, booking_member):
    profile, offering, room, session = inventory
    location = Location.objects.create(profile=profile, name="Private other location")
    other_room = Room.objects.create(location=location, name="Other room", capacity=12)
    other_offering = Offering.objects.create(location=location, name="Private other workshop", is_published=True)
    other_session = Session.objects.create(offering=other_offering, room=other_room,
        starts_at=session.starts_at, ends_at=session.ends_at, capacity=12, is_published=True)
    other = (profile, other_offering, other_room, other_session)
    mine = reserve(booking_member, workshop_request(inventory))
    theirs = reserve(booking_member, workshop_request(other))
    party = reserve(booking_member, party_request(other))
    profile.calendar_enabled = True
    profile.save()
    booking_member.all_locations = False
    booking_member.save()
    booking_member.locations.set([offering.location])
    return inventory, other, mine, theirs, party


@pytest.mark.parametrize("role", ["owner", "scheduler", "viewer"])
def test_lists_calendar_and_direct_urls_are_scoped(two_locations, booking_member, client, role):
    mine, other, reservation, forbidden, party = two_locations
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    for page in ("workspace", "locations", "workshops", "reservations", "requests", "parties", "party_setup", "camps", "settings"):
        response = client.get(path(booking_member, page))
        assert response.status_code == 200, (page, response.status_code)
        assert b"Private other location" not in response.content
        assert b"Private other workshop" not in response.content
    for name in ("reservation_detail", "reservation_preparation"):
        assert client.get(path(booking_member, name, public_id=reservation.public_id)).status_code == 200
        assert client.get(path(booking_member, name, public_id=forbidden.public_id)).status_code == 404
        assert client.get(path(booking_member, name, public_id=party.public_id)).status_code == 404
    assert client.get(path(booking_member, "workshop_detail", pk=other[1].pk)).status_code == 404
    assert client.get(path(booking_member, "preparation", pk=other[3].pk)).status_code == 404
    assert client.get(path(booking_member, "request_detail", public_id=forbidden.workshop_request.public_id)).status_code == 404
    assert client.get(path(booking_member, "party_detail", public_id=party.party_request.public_id)).status_code == 404
    profile, _ = require_location_access(booking_member.user, booking_member.profile.school.slug)
    assert set(staff_reservations(profile).values_list("pk", flat=True)) == {reservation.pk}
    params = QueryDict(mutable=True)
    params.update(start=(mine[3].starts_at - timedelta(days=1)).date().isoformat(), end=(mine[3].ends_at + timedelta(days=1)).date().isoformat())
    events = calendar_events(profile, params)
    assert {e["extendedProps"]["location"] for e in events} == {mine[1].location.name}
    for kind, pk in [("session", other[3].pk), ("party", party.pk)]:
        with pytest.raises(Http404):
            calendar_detail(profile, kind, str(pk))
    assert calendar_detail(profile, "session", mine[3].pk)["reservations"]
    response = client.get(path(booking_member, "workspace"), {"view": "calendar"})
    assert len(response.context["calendar_config"]["resources"]) == 1


@pytest.mark.parametrize("route", ["access", "integrations", "guest_communication", "catalogs", "messages", "lesson_settings", "visits"])
def test_school_wide_and_unaudited_routes_fail_closed(two_locations, booking_member, client, route):
    client.force_login(booking_member.user)
    assert client.get(path(booking_member, route)).status_code == 404
    assert billing_role(booking_member.user, booking_member.profile.school) is None


def test_location_tampering_empty_assignments_and_revocation(two_locations, booking_member, client):
    mine, other, reservation, _, _ = two_locations
    client.force_login(booking_member.user)
    assert client.get(path(booking_member, "workspace"), {"location": other[1].location_id}).status_code == 400
    session = client.session
    session[f"booking_location:{booking_member.profile_id}"] = str(other[1].location_id)
    session.save()
    assert client.get(path(booking_member, "workspace")).status_code == 400
    assert client.get(path(booking_member, "workspace"), {"location": ""}).status_code == 200
    booking_member.locations.clear()
    assert client.get(path(booking_member, "reservation_detail", public_id=reservation.public_id)).status_code == 404
    response = client.get(path(booking_member, "workspace"))
    assert response.status_code == 200
    assert not response.context["agenda"]
    booking_member.all_locations = True
    booking_member.save()
    assert client.get(path(booking_member, "reservation_detail", public_id=reservation.public_id)).status_code == 200


@pytest.mark.parametrize("action", ["save_notes", "record_terminal", "void_terminal"])
def test_foreign_reservation_writes_blocked_before_validation(two_locations, booking_member, client, action):
    _, _, _, forbidden, _ = two_locations
    client.force_login(booking_member.user)
    assert client.post(path(booking_member, "reservation_detail", public_id=forbidden.public_id), {"action": action}).status_code == 404
    assert not forbidden.order.entries.exists()
    with pytest.raises(Http404):
        settle_offline(booking_member.user, booking_member.profile.school.slug, forbidden.public_id,
            key=uuid.uuid4(), kind="payment", amount_cents=100, reference="test")


@pytest.mark.parametrize("route", ["preparation_update", "reservation_complete", "reservation_cancel", "staff_guest_list", "refund_create", "payment_review"])
def test_other_location_actions_are_denied(two_locations, booking_member, client, route):
    forbidden = two_locations[3]
    client.force_login(booking_member.user)
    assert client.post(path(booking_member, route, public_id=forbidden.public_id), {}).status_code == 404


def form_data(target, **changes):
    form = membership_form(target)
    return {"role": target.role, "is_active": "on", "location_access": "all",
            "edit_revision": form["edit_revision"].value(), **changes}


@pytest.fixture
def manager(booking_member):
    user = get_user_model().objects.create_user("school-manager")
    SchedulingMembership.objects.create(profile=booking_member.profile, user=user, role="owner")
    return user


def test_team_assignments_validated_audited_and_stale_safe(two_locations, booking_member, manager, client):
    mine, other, _, _, _ = two_locations
    slug = booking_member.profile.school.slug
    client.force_login(manager)
    url = path(booking_member, "access")
    response = client.get(url, {"edit": booking_member.pk})
    assert response.status_code == 200
    assert b"Apply" in response.content
    data = form_data(booking_member, team_action="locations", member=booking_member.pk, location_access="selected", locations=[other[1].location_id])
    assert client.post(url, data).status_code == 302
    assert list(booking_member.locations.values_list("pk", flat=True)) == [other[1].location_id]
    assert AdminAuditLog.objects.filter(model_label="scheduling.schedulingmembership", actor=manager).exists()
    assert client.post(url, data).status_code == 400
    booking_member.refresh_from_db()
    empty = save_membership(manager, slug, booking_member.pk, form_data(booking_member, location_access="selected"))
    assert "locations" in empty.errors
    school = School.objects.create(slug="unrelated", display_name="Unrelated")
    from scheduling.models import SchedulingProfile
    location = Location.objects.create(profile=SchedulingProfile.objects.create(school=school), name="Foreign")
    bad = save_membership(manager, slug, booking_member.pk, form_data(booking_member, location_access="selected", locations=[location.pk]))
    assert "locations" in bad.errors
    booking_member.refresh_from_db()
    result = save_membership(manager, slug, booking_member.pk, form_data(booking_member))
    assert result.is_valid()
    booking_member.refresh_from_db()
    assert booking_member.all_locations and not booking_member.locations.exists()
    audit_count = AdminAuditLog.objects.count()
    assert save_membership(manager, slug, booking_member.pk, form_data(booking_member)).is_valid()
    assert AdminAuditLog.objects.count() == audit_count
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(manager)
    assert csrf.post(url, form_data(booking_member, member=booking_member.pk)).status_code == 403


def test_team_management_cannot_escalate_self_or_other_tenant(two_locations, booking_member, manager, client):
    client.force_login(booking_member.user)
    assert client.post(path(booking_member, "access"), form_data(booking_member, member=booking_member.pk)).status_code == 404
    full = SchedulingMembership.objects.get(user=manager)
    assert not save_membership(manager, full.profile.school.slug, full.pk, form_data(full, role="viewer")).is_valid()
    with pytest.raises(Http404):
        require_access(booking_member.user, full.profile.school.slug)
    superuser = get_user_model().objects.create_superuser("scope-super", password="test")
    profile, _ = require_access(superuser, full.profile.school.slug)
    assert staff_locations(profile).count() == 2


def test_restricted_owner_cannot_change_global_settings(two_locations, booking_member, client):
    client.force_login(booking_member.user)
    assert client.post(path(booking_member, "reservation_settings"), {"action": "disable"}).status_code == 404
    assert client.get(path(booking_member, "location_new")).status_code == 404
    assert client.post(path(booking_member, "location_new"), {"name": "New", "timezone_name": "UTC"}).status_code == 404
    booking_member.profile.refresh_from_db()
    assert booking_member.profile.native_reservations_enabled


@pytest.mark.parametrize("role", ["owner", "scheduler", "viewer"])
def test_assigned_location_writes_preserve_existing_roles(two_locations, booking_member, client, role):
    from .test_reservation_notes import payload
    from scheduling.setup import build_form, save_setup
    mine, other, reservation, _, _ = two_locations
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    response = client.post(path(booking_member, "reservation_detail", public_id=reservation.public_id),
        payload(reservation, booking_member, "Assigned location note"))
    assert response.status_code == (404 if role == "viewer" else 302)
    profile, _ = require_location_access(booking_member.user, booking_member.profile.school.slug)
    form = build_form(profile, "rooms", pk=mine[2].pk)
    data = {"name": "Updated room", "location": other[1].location_id, "capacity": 6,
            "edit_revision": form["edit_revision"].value()}
    if role == "owner":
        result = save_setup(booking_member.user, profile.school.slug, "rooms", data, pk=mine[2].pk)
        assert result.is_valid(), result.errors
        mine[2].refresh_from_db()
        assert mine[2].name == "Updated room" and mine[2].location_id == mine[1].location_id
    else:
        with pytest.raises(Http404):
            save_setup(booking_member.user, profile.school.slug, "rooms", data, pk=mine[2].pk)
    with pytest.raises(Http404):
        build_form(profile, "sessions", pk=other[3].pk)
    with pytest.raises(Http404):
        build_form(profile, "rooms", pk=other[2].pk)


def test_multiple_locations_and_foreign_assignments(two_locations, booking_member):
    from scheduling.models import SchedulingProfile
    mine, other, reservation, forbidden, party = two_locations
    foreign = SchedulingProfile.objects.create(school=School.objects.create(slug="foreign-scope", display_name="Foreign"))
    location = Location.objects.create(profile=foreign, name="Foreign location")
    booking_member.locations.add(other[1].location, location)
    profile, _ = require_location_access(booking_member.user, booking_member.profile.school.slug)
    assert set(staff_locations(profile).values_list("pk", flat=True)) == {mine[1].location_id, other[1].location_id}
    assert set(staff_reservations(profile).values_list("pk", flat=True)) == {reservation.pk, forbidden.pk, party.pk}
    booking_member.is_active = False
    booking_member.save()
    with pytest.raises(Http404):
        require_location_access(booking_member.user, profile.school.slug)


def test_revoked_location_rejects_already_open_note_form(two_locations, booking_member, client):
    from .test_reservation_notes import payload
    reservation = two_locations[2]
    data = payload(reservation, booking_member, "Stale access")
    booking_member.locations.clear()
    client.force_login(booking_member.user)
    assert client.post(path(booking_member, "reservation_detail", public_id=reservation.public_id), data).status_code == 404
    reservation.refresh_from_db()
    assert not reservation.internal_notes


def test_stock_and_camps_are_location_scoped(two_locations, booking_member, client):
    from scheduling.inventory import adjust, stock_rows
    from scheduling.models import Material, StockLevel, Camp
    mine, other, _, _, _ = two_locations
    material = Material.objects.create(profile=mine[0], name="Fabric", sku="FAB")
    own = StockLevel.objects.create(material=material, location=mine[1].location, on_hand=10)
    foreign = StockLevel.objects.create(material=material, location=other[1].location, on_hand=20)
    profile, _ = require_location_access(booking_member.user, mine[0].school.slug)
    assert [row["level"].pk for row in stock_rows(profile)] == [own.pk]
    assert adjust(booking_member.user, profile.school.slug, own.pk, on_hand=12, previous=10, reason="Count").on_hand == 12
    with pytest.raises(Http404):
        adjust(booking_member.user, profile.school.slug, foreign.pk, on_hand=22, previous=20, reason="Count")
    camp = Camp.objects.create(offering=other[1], name="Private camp", price=100)
    client.force_login(booking_member.user)
    assert client.get(path(booking_member, "camp_detail", pk=camp.pk)).status_code == 404
    assert b"Private camp" not in client.get(path(booking_member, "camps")).content


def test_missing_or_foreign_team_member_cannot_be_saved(booking_member, manager, client):
    from scheduling.models import SchedulingProfile
    client.force_login(manager)
    url = path(booking_member, "access")
    assert client.post(url, {"role": "owner"}).status_code == 400
    assert client.get(url, {"edit": "invalid"}).status_code == 404
    profile = SchedulingProfile.objects.create(school=School.objects.create(slug="foreign-member", display_name="Foreign"))
    foreign = SchedulingMembership.objects.create(profile=profile, user=manager)
    assert client.get(url, {"edit": foreign.pk}).status_code == 404
    assert client.post(url, form_data(foreign, member=foreign.pk)).status_code == 404


def test_local_payment_and_customer_return_link_respect_scope(two_locations, booking_member, merchant, client):
    from scheduling.checkout import payment_token
    from core.payments.service import totals
    mine, other, own, _, _ = two_locations
    settle_offline(booking_member.user, booking_member.profile.school.slug, own.public_id,
        key=uuid.uuid4(), kind="payment", amount_cents=2500, reference="local receipt")
    assert totals(own.order)["net_cents"] == 2500
    booking_member.all_locations = True
    booking_member.save()
    own = online(booking_member, workshop_request(mine, count=1))
    foreign = online(booking_member, workshop_request(other, count=1))
    booking_member.all_locations = False
    booking_member.save()
    client.force_login(booking_member.user)
    for item, allowed in [(own, True), (foreign, False)]:
        response = client.get(path(booking_member, "customer_payment", token=payment_token(item)))
        assert response.status_code == 200
        assert response.context["can_view_reservation"] is allowed
