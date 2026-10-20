from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School, SchoolAdminMembership
from scheduling.forms import LocationForm, OfferingForm, RoomForm, SessionForm
from scheduling.models import Location, Offering, Room, SchedulingProfile, Session
from scheduling.setup import build_form, records, save_setup, set_publication

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalog(booking_member):
    location = Location.objects.create(profile=booking_member.profile, name="Main studio")
    room = Room.objects.create(location=location, name="Room 1", capacity=12)
    offering = Offering.objects.create(location=location, name="Design workshop", description="Choose your design.")
    return location, room, offering


@pytest.fixture
def session(catalog):
    _, room, offering = catalog
    start = (timezone.now() + timedelta(days=5)).replace(second=0, microsecond=0)
    return Session.objects.create(offering=offering, room=room, starts_at=start, ends_at=start + timedelta(hours=1), capacity=10)


@pytest.fixture
def other_catalog(db):
    profile = SchedulingProfile.objects.create(school=School.objects.create(slug="private-studio"), enabled=True)
    location = Location.objects.create(profile=profile, name="Private location")
    room = Room.objects.create(location=location, name="Secret room", capacity=8)
    offering = Offering.objects.create(location=location, name="Secret offering")
    session = Session.objects.create(offering=offering, starts_at=timezone.now()+timedelta(days=2), ends_at=timezone.now()+timedelta(days=2,hours=1), capacity=4)
    return profile, location, room, offering, session


def url(name, profile, **kwargs):
    return reverse("scheduling:" + name, kwargs={"school_slug": profile.school.slug, **kwargs})


def session_data(catalog, **changes):
    location, room, _ = catalog
    start = (timezone.now() + timedelta(days=5)).astimezone(ZoneInfo(location.timezone_name)).replace(second=0, microsecond=0)
    data = {"starts_at": start.strftime("%Y-%m-%dT%H:%M"), "duration_minutes": 60, "capacity": "10", "room": str(room.pk)}
    return {**data, **changes}


def save(member, kind, data, **kwargs):
    if kwargs.get("pk"):
        form = build_form(member.profile, kind, pk=kwargs["pk"])
        data = {"edit_revision": form["edit_revision"].value(), **data}
    return save_setup(member.user, member.profile.school.slug, kind, data, **kwargs)


def publish(member, kind, instance, value=True):
    return set_publication(member.user, member.profile.school.slug, kind, instance.pk, value)


def test_complete_setup_through_views_persists_and_audits(client, booking_member):
    profile = booking_member.profile
    client.force_login(booking_member.user)
    response = client.post(url("location_new", profile), {"name": "Downtown", "address": "100 Main St", "timezone_name": "America/Los_Angeles"})
    assert response.status_code == 302
    location = Location.objects.get(profile=profile)
    response = client.post(url("room_new", profile), {"name": "Room A", "location": location.pk, "capacity": 12})
    assert response.status_code == 302
    room = Room.objects.get(location=location)
    response = client.post(url("workshop_new", profile), {"name": "Craft workshop", "location": location.pk, "description": "An afternoon workshop.", "is_published": True})
    assert response.status_code == 302
    offering = Offering.objects.get(location=location)
    assert not offering.is_published
    response = client.post(url("session_new", profile, offering_pk=offering.pk), session_data((location, room, offering), is_published=True))
    assert response.status_code == 302
    session = Session.objects.get(offering=offering)
    assert not session.is_published
    response = client.post(url("session_publish", profile, pk=session.pk), {"action": "publish"}, follow=True)
    assert response.status_code == 200
    response = client.post(url("workshop_publish", profile, pk=offering.pk), {"action": "publish"}, follow=True)
    assert b"Published." in response.content
    public = Client().get(url("public_schedule", profile))
    assert b"Craft workshop" in public.content and b"Room A" in public.content
    assert b"Registration not open" in public.content
    assert b"bookings-owner" not in public.content
    assert AdminAuditLog.objects.filter(actor=booking_member.user).count() == 6
    log = AdminAuditLog.objects.filter(model_label="scheduling.offering", action="change").get()
    assert log.changes["is_published"] == {"from": False, "to": True}
    assert log.extra["school_slug"] == profile.school.slug
    client.logout()
    client.force_login(booking_member.user)
    reopened = client.get(url("session_edit", profile, pk=session.pk))
    assert reopened.context["form"].instance.capacity == 10
    assert reopened.context["form"].instance.room_id == room.pk


@pytest.mark.parametrize("origin", ["parties", "", "https://example.com"])
def test_workshop_edit_preserves_only_known_return_context(client, booking_member, catalog, origin):
    location, _, offering = catalog
    profile = booking_member.profile
    client.force_login(booking_member.user)
    path = url("workshop_edit", profile, pk=offering.pk) + "?return_to=" + origin
    target = (url("party_setup", profile) + f"?location={location.pk}" if origin == "parties"
              else url("workshop_detail", profile, pk=offering.pk))
    page = client.get(path)
    assert f'href="{target}"'.encode() in page.content
    invalid = client.post(path, {"name": "", "location": location.pk})
    assert invalid.status_code == 200 and f'href="{target}"'.encode() in invalid.content
    response = client.post(path, {"name": offering.name, "location": location.pk,
        "edit_revision": page.context["form"]["edit_revision"].value()})
    assert response.status_code == 302 and response.url == target


@pytest.mark.parametrize("kind,data,error", [
    ("locations", {"name": "", "timezone_name": "Europe/London"}, "name"),
    ("locations", {"name": "West", "timezone_name": "Made/Up"}, "timezone_name"),
    ("rooms", {"name": "Room 2", "capacity": 0}, "capacity"),
    ("rooms", {"name": "Room 2", "capacity": 10001}, "capacity"),
    ("rooms", {"name": "Room 2", "capacity": "abc"}, "capacity"),
    ("workshops", {"name": ""}, "name"),
])
def test_invalid_setup_does_not_write(booking_member, catalog, kind, data, error):
    before = records(booking_member.profile, kind).count()
    form = save(booking_member, kind, {"location": catalog[0].pk, **data})
    assert not form.is_valid() and error in form.errors
    assert records(booking_member.profile, kind).count() == before
    assert not AdminAuditLog.objects.exists()


@pytest.mark.parametrize("kind,name", [("locations", "main STUDIO"), ("rooms", "room 1")])
def test_duplicate_names_are_form_errors_not_server_errors(booking_member, catalog, kind, name):
    form = save(booking_member, kind, {"name": name, "timezone_name": "America/Los_Angeles", "location": catalog[0].pk, "capacity": 12})
    assert not form.is_valid()
    assert not AdminAuditLog.objects.exists()


def test_model_timezone_validator_and_display(catalog):
    location, room, offering = catalog
    location.timezone_name = "../invalid"
    with pytest.raises(ValidationError):
        location.full_clean()
    location.timezone_name = "Invalid/Place"
    with pytest.raises(ValidationError):
        location.full_clean()
    assert str(location) == "Main studio"
    assert str(room) == "Room 1 (Main studio)"
    assert str(offering) == "Design workshop"


def test_location_form_defaults_to_configured_zone_not_first_option(client, booking_member, catalog):
    form = LocationForm(profile=booking_member.profile)
    assert form["timezone_name"].value() == "America/Los_Angeles"
    catalog[0].timezone_name = "America/New_York"
    catalog[0].save()
    form = LocationForm(profile=booking_member.profile, instance=catalog[0])
    assert form["timezone_name"].value() == "America/New_York"
    client.force_login(booking_member.user)
    response = client.get(url("location_new", booking_member.profile))
    assert b'value="America/Los_Angeles" selected' in response.content


def test_same_names_allowed_in_other_businesses(booking_member, other_catalog):
    form = save(booking_member, "locations", {"name": "Private location", "timezone_name": "America/New_York"})
    assert form.is_valid()


@pytest.mark.parametrize("kind", ["rooms", "workshops"])
def test_foreign_location_rejected_and_not_offered(booking_member, catalog, other_catalog, kind):
    _, location, _, _, _ = other_catalog
    form = save(booking_member, kind, {"name": "Injected", "location": location.pk, "capacity": 12})
    assert not form.is_valid() and "location" in form.errors
    choices = build_form(booking_member.profile, kind).fields["location"].queryset
    assert list(choices) == [catalog[0]]


def test_foreign_room_offering_and_object_ids_rejected(booking_member, catalog, other_catalog):
    _, other_location, room, offering, other_session = other_catalog
    form = save(booking_member, "sessions", session_data(catalog, room=room.pk), offering_pk=catalog[2].pk)
    assert "room" in form.errors
    with pytest.raises(Http404):
        save(booking_member, "sessions", session_data(catalog), offering_pk=offering.pk)
    for kind, instance in [("locations", other_location), ("rooms", room), ("workshops", offering), ("sessions", other_session)]:
        with pytest.raises(Http404):
            save(booking_member, kind, {}, pk=instance.pk)
    assert not AdminAuditLog.objects.exists()


def test_room_at_other_location_in_same_business_also_rejected(booking_member, catalog):
    location = Location.objects.create(profile=booking_member.profile, name="Branch")
    room = Room.objects.create(location=location, name="Room B", capacity=20)
    form = save(booking_member, "sessions", session_data(catalog, room=room.pk), offering_pk=catalog[2].pk)
    assert "room" in form.errors
    instance = Session(offering=catalog[2], room=room, starts_at=timezone.now()+timedelta(days=1), ends_at=timezone.now()+timedelta(days=1,hours=1), capacity=2)
    with pytest.raises(ValidationError, match="workshop's location"):
        instance.full_clean()


@pytest.mark.parametrize("changes,error", [
    ({"capacity": 0}, "capacity"), ({"capacity": 10001}, "capacity"),
    ({"capacity": 13}, "capacity"), ({"capacity": "no"}, "capacity"),
    ({"starts_at": "garbage"}, "starts_at"), ({"duration_minutes": "garbage"}, "duration_minutes"),
    ({"starts_at": "2000-01-01T10:00"}, "starts_at"),
    ({"duration_minutes": -60}, "duration_minutes"),
    ({"duration_minutes": 0}, "duration_minutes"),
])
def test_session_validation(booking_member, catalog, changes, error):
    form = save(booking_member, "sessions", session_data(catalog, **changes), offering_pk=catalog[2].pk)
    assert not form.is_valid() and error in form.errors
    assert not Session.objects.exists()


def test_no_room_is_allowed_and_capacity_still_validated(booking_member, catalog):
    form = save(booking_member, "sessions", session_data(catalog, room="", capacity=30), offering_pk=catalog[2].pk)
    assert form.is_valid() and form.instance.room is None
    publish(booking_member, "sessions", form.instance)


@pytest.mark.parametrize("start", ["2030-03-10T02:30", "2030-11-03T01:30"])
def test_dst_nonexistent_and_ambiguous_times_rejected(booking_member, catalog, start):
    form = save(booking_member, "sessions", session_data(catalog, starts_at=start, duration_minutes=120), offering_pk=catalog[2].pk)
    assert "starts_at" in form.errors


def test_location_timezone_used_for_storage_and_edit_render(client, booking_member, catalog):
    location, _, offering = catalog
    location.timezone_name = "America/New_York"
    location.save()
    form = save(booking_member, "sessions", session_data(catalog, starts_at="2030-07-01T10:00"), offering_pk=offering.pk)
    assert form.is_valid(), form.errors
    form.instance.refresh_from_db()
    assert form.instance.starts_at.hour == 14
    client.force_login(booking_member.user)
    response = client.get(url("session_edit", booking_member.profile, pk=form.instance.pk))
    assert b'value="2030-07-01T10:00"' in response.content
    publish(booking_member, "sessions", form.instance)
    publish(booking_member, "workshops", offering)
    public = Client().get(url("public_schedule", booking_member.profile))
    assert b"10:00 AM" in public.content and b"America/New_York" in public.content


@pytest.mark.parametrize("start,duration,expected", [
    ("2030-07-01T16:00", 120, "4:00 PM &ndash; 6:00 PM"),
    ("2030-07-01T23:30", 60, "11:30 PM &ndash; Jul 2, 2030 12:30 AM"),
])
def test_workshop_session_date_range_uses_studio_local_dates(client, booking_member, catalog, start, duration, expected):
    form = save(booking_member, "sessions", session_data(catalog, starts_at=start, duration_minutes=duration), offering_pk=catalog[2].pk)
    assert form.is_valid(), form.errors
    client.force_login(booking_member.user)
    page = client.get(url("workshop_detail", booking_member.profile, pk=catalog[2].pk))
    assert page.status_code == 200
    assert expected.encode() in page.content


def test_timezone_and_parent_location_are_protected_after_setup(booking_member, catalog, session, other_catalog):
    location, room, offering = catalog
    form = save(booking_member, "locations", {"name": "Renamed", "address": "New address", "timezone_name": "America/New_York"}, pk=location.pk)
    assert form.is_valid()
    location.refresh_from_db()
    assert location.timezone_name == "America/Los_Angeles" and location.name == "Renamed"
    location.timezone_name = "America/New_York"
    with pytest.raises(ValidationError, match="cannot change"):
        location.full_clean()
    for kind, instance in [("rooms", room), ("workshops", offering)]:
        form = save(booking_member, kind, {"name": "Renamed", "location": other_catalog[1].pk, "capacity": 12}, pk=instance.pk)
        assert form.is_valid(), form.errors
        assert form.instance.location_id == location.pk
        instance.location = other_catalog[1]
        with pytest.raises(ValidationError, match="cannot move"):
            instance.full_clean()


def test_room_cannot_shrink_below_existing_session(booking_member, catalog, session):
    form = save(booking_member, "rooms", {"name": "Room 1", "location": catalog[0].pk, "capacity": 5}, pk=catalog[1].pk)
    assert "capacity" in form.errors
    catalog[1].refresh_from_db()
    assert catalog[1].capacity == 12


def test_session_model_rejects_naive_times(session):
    session.starts_at = datetime(2030, 1, 1, 10)
    session.ends_at = datetime(2030, 1, 1, 11)
    with pytest.raises(ValidationError, match="time zone"):
        session.full_clean()


@pytest.mark.parametrize("model,field,value", [(Room, "capacity", 0), (Room, "capacity", 10001), (Session, "capacity", 0), (Session, "capacity", 10001), (Session, "ends_at", "start")])
def test_database_constraints_are_not_only_form_checks(catalog, session, model, field, value):
    obj = catalog[1] if model == Room else session
    if value == "start":
        value = session.starts_at
    with pytest.raises(IntegrityError), transaction.atomic():
        model.objects.filter(pk=obj.pk).update(**{field: value})


def test_name_uniqueness_enforced_by_database(catalog):
    with pytest.raises(IntegrityError), transaction.atomic():
        Location.objects.create(profile=catalog[0].profile, name="MAIN STUDIO")
    with pytest.raises(IntegrityError), transaction.atomic():
        Room.objects.create(location=catalog[0], name="ROOM 1", capacity=5)


def test_publication_prerequisites_conflicts_and_adjacent_sessions(booking_member, catalog, session):
    offering = catalog[2]
    with pytest.raises(ValidationError, match="upcoming session"):
        publish(booking_member, "workshops", offering)
    publish(booking_member, "sessions", session)
    publish(booking_member, "workshops", offering)
    conflict = Session.objects.create(offering=offering, room=session.room, capacity=5, starts_at=session.starts_at+timedelta(minutes=30), ends_at=session.ends_at+timedelta(minutes=30))
    with pytest.raises(ValidationError, match="already has"):
        publish(booking_member, "sessions", conflict)
    conflict.refresh_from_db()
    assert not conflict.is_published
    conflict.starts_at = session.ends_at
    conflict.ends_at = session.ends_at+timedelta(hours=1)
    conflict.save()
    publish(booking_member, "sessions", conflict)
    conflict.refresh_from_db()
    assert conflict.is_published
    count = AdminAuditLog.objects.count()
    publish(booking_member, "sessions", conflict)
    assert AdminAuditLog.objects.count() == count
    publish(booking_member, "sessions", conflict, False)
    conflict.refresh_from_db()
    assert not conflict.is_published


def test_draft_overlaps_are_allowed_but_published_edits_are_checked(booking_member, catalog, session):
    publish(booking_member, "sessions", session)
    draft = save(booking_member, "sessions", session_data(catalog), offering_pk=catalog[2].pk)
    assert draft.is_valid()
    other = Session.objects.create(offering=catalog[2], room=session.room, starts_at=session.starts_at+timedelta(days=1), ends_at=session.ends_at+timedelta(days=1), capacity=5)
    publish(booking_member, "sessions", other)
    form = save(booking_member, "sessions", session_data(catalog), pk=other.pk)
    assert "room" in form.errors
    other.refresh_from_db()
    assert other.starts_at == session.starts_at+timedelta(days=1)


def test_past_session_cannot_be_published_but_can_be_unpublished(booking_member, session):
    Session.objects.filter(pk=session.pk).update(starts_at=timezone.now()-timedelta(hours=2), ends_at=timezone.now()-timedelta(hours=1))
    with pytest.raises(ValidationError, match="already started"):
        publish(booking_member, "sessions", session)
    Session.objects.filter(pk=session.pk).update(is_published=True)
    publish(booking_member, "sessions", session, False)
    session.refresh_from_db()
    assert not session.is_published


def test_only_both_published_upcoming_records_are_public(client, booking_member, catalog, session, other_catalog):
    profile = booking_member.profile
    path = url("public_schedule", profile)
    assert b"Design workshop" not in client.get(path).content
    publish(booking_member, "sessions", session)
    assert b"Design workshop" not in client.get(path).content
    publish(booking_member, "workshops", catalog[2])
    draft_room = Room.objects.create(location=catalog[0], name="Secret draft room", capacity=2)
    Session.objects.create(offering=catalog[2], room=draft_room, starts_at=session.starts_at, ends_at=session.ends_at, capacity=2)
    response = client.get(path+"?include_drafts=true")
    assert b"Design workshop" in response.content and b"Secret draft room" not in response.content
    assert b"Secret offering" not in response.content
    publish(booking_member, "workshops", catalog[2], False)
    assert b"Design workshop" not in client.get(path).content
    publish(booking_member, "workshops", catalog[2])
    Session.objects.filter(pk=session.pk).update(starts_at=timezone.now()-timedelta(hours=2), ends_at=timezone.now()-timedelta(hours=1))
    assert b"Design workshop" not in client.get(path).content
    profile.enabled = False
    profile.save()
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("role,setup_status,session_status", [("owner", 200, 200), ("scheduler", 404, 200), ("viewer", 404, 404)])
def test_roles_enforced_on_form_and_publication_routes(client, booking_member, catalog, session, role, setup_status, session_status):
    booking_member.role = role
    booking_member.save()
    profile = booking_member.profile
    client.force_login(booking_member.user)
    for page, kwargs in [("location_new", {}), ("room_new", {}), ("workshop_new", {}), ("location_edit", {"pk": catalog[0].pk}), ("room_edit", {"pk": catalog[1].pk}), ("workshop_edit", {"pk": catalog[2].pk})]:
        assert client.get(url(page, profile, **kwargs)).status_code == setup_status
        assert client.post(url(page, profile, **kwargs), {}).status_code == setup_status
    for page, kwargs in [("session_new", {"offering_pk": catalog[2].pk}), ("session_edit", {"pk": session.pk})]:
        assert client.get(url(page, profile, **kwargs)).status_code == session_status
        assert client.post(url(page, profile, **kwargs), {}).status_code == session_status
    assert client.post(url("session_publish", profile, pk=session.pk), {"action": "publish"}).status_code == (302 if session_status == 200 else 404)
    assert client.post(url("workshop_publish", profile, pk=catalog[2].pk), {"action": "publish"}).status_code == (302 if setup_status == 200 else 404)
    listing = client.get(url("locations", profile))
    detail = client.get(url("workshop_detail", profile, pk=catalog[2].pk))
    assert (b"Add location" in listing.content) is (role == "owner")
    assert (b"Add session" in detail.content) is (role != "viewer")


def test_errors_are_visible_in_ui_and_publication_requires_post(client, booking_member, catalog, session):
    client.force_login(booking_member.user)
    profile = booking_member.profile
    response = client.post(url("workshop_publish", profile, pk=catalog[2].pk), {"action": "publish"}, follow=True)
    assert b"Publish at least one upcoming session" in response.content
    assert b'role="alert"' in response.content
    assert client.get(url("session_publish", profile, pk=session.pk)).status_code == 405
    assert client.post(url("session_publish", profile, pk=session.pk), {"action": "surprise"}).status_code == 400
    bad = client.post(url("session_new", profile, offering_pk=catalog[2].pk), session_data(catalog, capacity=13))
    assert b"at most 12" in bad.content and b'aria-invalid="true"' in bad.content


def test_setup_and_publish_require_csrf(booking_member, catalog, session):
    client = Client(enforce_csrf_checks=True)
    client.force_login(booking_member.user)
    for name, kwargs in [("location_new", {}), ("session_edit", {"pk": session.pk}), ("workshop_publish", {"pk": catalog[2].pk})]:
        assert client.post(url(name, booking_member.profile, **kwargs), {}).status_code == 403


def test_cross_tenant_routes_and_membership_revocation(client, booking_member, catalog, other_catalog):
    profile, location, room, offering, session = other_catalog
    client.force_login(booking_member.user)
    for name, kwargs in [("location_edit", {"pk": location.pk}), ("room_edit", {"pk": room.pk}), ("workshop_detail", {"pk": offering.pk}), ("session_edit", {"pk": session.pk})]:
        assert client.get(url(name, booking_member.profile, **kwargs)).status_code == 404
    assert client.post(url("workshop_publish", booking_member.profile, pk=offering.pk), {"action": "publish"}).status_code == 404
    assert client.get(url("workshops", profile)).status_code == 404
    booking_member.is_active = False
    booking_member.save()
    assert client.post(url("location_new", booking_member.profile), {"name": "No", "timezone_name": "UTC"}).status_code == 404


def test_all_staff_routes_require_authentication(client, booking_profile, catalog, session):
    for name, kwargs in [("locations", {}), ("workshops", {}), ("workshop_detail", {"pk": catalog[2].pk}), ("location_new", {}), ("session_new", {"offering_pk": catalog[2].pk}), ("session_publish", {"pk": session.pk})]:
        assert client.get(url(name, booking_profile, **kwargs)).status_code == 302
    assert client.get(url("public_schedule", booking_profile)).status_code == 200


def test_empty_lists_and_escaped_descriptions(client, booking_member, catalog, session):
    client.force_login(booking_member.user)
    assert client.get(url("locations", booking_member.profile)).status_code == 200
    assert client.get(url("workshops", booking_member.profile)).status_code == 200
    offering = catalog[2]
    offering.description = '<script>alert("unsafe")</script>'
    offering.save()
    publish(booking_member, "sessions", session)
    publish(booking_member, "workshops", offering)
    response = client.get(url("public_schedule", booking_member.profile))
    assert b"<script>" not in response.content and b"&lt;script&gt;" in response.content


def test_unknown_kind_is_not_a_generic_model_backdoor(booking_member):
    with pytest.raises(Http404):
        records(booking_member.profile, "users")
    with pytest.raises(Http404):
        set_publication(booking_member.user, booking_member.profile.school.slug, "locations", 1, True)


def test_public_schedule_admin_return_is_tenant_scoped(client, booking_member, other_catalog):
    profile = booking_member.profile
    path = url("public_schedule", profile)
    assert b"Back to admin" not in client.get(path).content
    client.force_login(booking_member.user)
    response = client.get(path)
    assert b"Back to admin" in response.content
    assert f'href="{url("workspace", profile)}"'.encode() in response.content
    assert b"Back to admin" not in client.get(url("public_schedule", other_catalog[0])).content
    booking_member.is_active = False
    booking_member.save()
    assert b"Back to admin" not in client.get(path).content
    booking_member.user.is_superuser = True
    booking_member.user.save()
    assert b"Back to admin" in client.get(path).content


def test_audit_and_setup_write_are_atomic(booking_member, catalog, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("Audit storage unavailable")
    monkeypatch.setattr(AdminAuditLog.objects, "create", fail)
    with pytest.raises(RuntimeError):
        save(booking_member, "locations", {"name": "Must roll back", "timezone_name": "UTC"})
    assert not Location.objects.filter(name="Must roll back").exists()


@pytest.mark.parametrize("slug", ["south-bay-music", "kidworks-childrens-center"])
def test_setup_does_not_mutate_existing_enrollment(client, booking_member, catalog, slug):
    school = School.objects.create(slug=slug, display_name=slug, plan="custom", feature_flags={"unchanged": True})
    member = SchoolAdminMembership.objects.create(school=school, user=booking_member.user)
    before = (school.plan, dict(school.feature_flags), member.role)
    form = save(booking_member, "workshops", {"name": "New workshop", "location": catalog[0].pk})
    assert form.is_valid()
    school.refresh_from_db()
    member.refresh_from_db()
    assert (school.plan, school.feature_flags, member.role) == before
    assert not SchedulingProfile.objects.filter(school=school).exists()
    client.force_login(booking_member.user)
    assert client.get(f"/bookings/{slug}/schedule/").status_code == 404


@pytest.mark.django_db(transaction=True)
def test_concurrent_publishing_cannot_double_book_room(booking_member, catalog, session):
    other = Session.objects.create(offering=catalog[2], room=session.room, starts_at=session.starts_at, ends_at=session.ends_at, capacity=5)
    user_id, slug = booking_member.user.pk, booking_member.profile.school.slug
    barrier = Barrier(2)

    def attempt(pk):
        close_old_connections()
        try:
            user = get_user_model().objects.get(pk=user_id)
            barrier.wait(timeout=10)
            try:
                set_publication(user, slug, "sessions", pk, True)
                return "published"
            except ValidationError:
                return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(attempt, pk) for pk in [session.pk, other.pk]]
        outcomes = [future.result(timeout=15) for future in futures]
    assert sorted(outcomes) == ["conflict", "published"]
    assert Session.objects.filter(is_published=True).count() == 1
