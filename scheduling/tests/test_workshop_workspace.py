from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.test import Client

from core.models import AdminAuditLog
from scheduling.models import InventoryCatalog, Location, Offering, SchedulingMembership, Session, CatalogVariant, StockLevel
from scheduling.setup import build_form, save_setup
from scheduling.tests.test_inventory import stock, url, reserve, request as workshop_request
from scheduling.tests.test_setup import catalog, other_catalog

pytestmark = pytest.mark.django_db


def inline_data(s, **changes):
    zone = ZoneInfo(s.location.timezone_name)
    page = s.client.get(url(s, "workshop_detail", pk=s.offering.pk), {"edit_session": s.session.pk})
    assert page.status_code == 200
    return {"session_id": s.session.pk, "edit_revision": page.context["session_form"]["edit_revision"].value(),
            "starts_at": s.session.starts_at.astimezone(zone).strftime("%Y-%m-%dT%H:%M"),
            "duration_minutes": round((s.session.ends_at - s.session.starts_at).total_seconds() / 60),
            "room": s.room.pk, "capacity": s.session.capacity, **changes}


def test_workshop_workspace_removes_cross_feature_clutter(stock):
    s = stock
    s.offering.description = "Choose your design and color, then create together."
    s.offering.save()
    listing = s.client.get(url(s, "workshops"))
    assert b"Public schedule" not in listing.content
    assert b'aria-label="Setup sections"' in listing.content
    page = s.client.get(url(s, "workshop_detail", pk=s.offering.pk))
    for unwanted in [s.offering.description, "Participant choices", "Party inquiries", "Requests:",
                     'aria-label="Setup sections"',
                     url(s, "preparation", pk=s.session.pk), url(s, "session_edit", pk=s.session.pk)]:
        assert unwanted.encode() not in page.content
    assert b"View Catalog" in page.content and b"View inventory" not in page.content
    assert url(s, "inventory_catalog", catalog_pk=s.catalog.pk).encode() in page.content
    assert b'class="dash-table"' in page.content
    assert f'?edit_session={s.session.pk}#session-{s.session.pk}'.encode() in page.content


def test_materials_catalog_required_and_removed_controls_not_postable(stock):
    s = stock
    target = url(s, "workshop_edit", pk=s.offering.pk)
    page = s.client.get(target)
    form = page.context["form"]
    assert b'<h1>Edit workshop</h1>' in page.content
    assert b'<h1>Setup</h1>' not in page.content
    assert b'<legend>Workshop details</legend>' in page.content
    assert b'<legend>Booking payments</legend>' in page.content
    assert b'workshop-settings-form' in page.content
    assert b'data-booking-deposit' in page.content
    assert form.fields["inventory_catalog"].required
    assert form.fields["inventory_catalog"].label == "Catalog"
    assert not {"accepts_requests", "accepts_party_requests"} & form.fields.keys()
    assert b'aria-label="Setup sections"' not in page.content
    data = {"name": "Updated workshop", "location": s.location.pk,
            "edit_revision": form["edit_revision"].value(), "accepts_requests": "", "accepts_party_requests": ""}
    invalid = s.client.post(target, data)
    assert invalid.status_code == 200 and "inventory_catalog" in invalid.context["form"].errors
    s.offering.refresh_from_db()
    assert s.offering.name == "Workshop"
    data["inventory_catalog"] = s.catalog.pk
    assert s.client.post(target, data).status_code == 302
    s.offering.refresh_from_db()
    assert s.offering.name == "Updated workshop"
    assert s.offering.accepts_requests and s.offering.accepts_party_requests
    new = s.client.post(url(s, "workshop_new"), {
        "name": "New", "location": s.location.pk, "inventory_catalog": s.catalog.pk,
        "accepts_requests": "on", "accepts_party_requests": "on"})
    assert new.status_code == 302
    created = Offering.objects.get(name="New")
    assert not created.accepts_requests and not created.accepts_party_requests


def test_noninventory_workshops_and_existing_uncatalogued_bookings_remain_editable(stock, booking_member, catalog):
    assert "inventory_catalog" not in build_form(booking_member.profile, "workshops").fields
    s = stock
    s.offering.inventory_catalog = None
    s.offering.save()
    workshop_request(s)
    form = build_form(s.profile, "workshops", pk=s.offering.pk)
    assert "inventory_catalog" not in form.fields
    saved = save_setup(s.user, s.school.slug, "workshops", {
        "name": "Historical workshop", "location": s.location.pk,
        "inventory_catalog": s.catalog.pk, "edit_revision": form["edit_revision"].value()}, pk=s.offering.pk)
    assert saved.is_valid()
    s.offering.refresh_from_db()
    assert s.offering.name == "Historical workshop" and s.offering.inventory_catalog_id is None


def test_catalog_from_other_tenant_rejected(stock, booking_member):
    s = stock
    foreign = InventoryCatalog.objects.create(profile=booking_member.profile, name="Other business catalog")
    form = build_form(s.profile, "workshops", pk=s.offering.pk)
    saved = save_setup(s.user, s.school.slug, "workshops", {
        "name": "Wrong", "location": s.location.pk, "inventory_catalog": foreign.pk,
        "edit_revision": form["edit_revision"].value()}, pk=s.offering.pk)
    assert "inventory_catalog" in saved.errors


@pytest.mark.parametrize("role", ["owner", "scheduler"])
def test_inline_session_edit_persists_and_audits(stock, role):
    s = stock
    SchedulingMembership.objects.filter(user=s.user).update(role=role)
    target = url(s, "workshop_detail", pk=s.offering.pk)
    data = inline_data(s, capacity=80)
    result = s.client.post(target, data)
    assert result.status_code == 302 and result.url == f"{target}#session-{s.session.pk}"
    s.session.refresh_from_db()
    assert s.session.capacity == 80
    log = AdminAuditLog.objects.get(model_label="scheduling.session", object_id=str(s.session.pk))
    assert log.changes["capacity"] == {"from": 100, "to": 80}
    assert log.actor_id == s.user.pk
    stale = s.client.post(target, data)
    assert stale.status_code == 400 and b"may have changed" in stale.content
    assert b'class="session-editor-row"' in stale.content
    assert AdminAuditLog.objects.filter(model_label="scheduling.session").count() == 1


@pytest.mark.parametrize("changes,expected", [
    ({"capacity": 101}, "at most 100"), ({"capacity": 0}, "greater than or equal to 1"),
    ({"starts_at": "invalid"}, "valid date/time"),
    ({"starts_at": "2030-03-10T02:30"}, "couldn"),
    ({"starts_at": "2030-11-03T01:30"}, "couldn"),
    ({"duration_minutes": 0}, "greater than or equal to 1"),
    ({"duration_minutes": ""}, "required"),
    ({"duration_minutes": "not a duration"}, "whole number"),
])
def test_inline_errors_preserve_inputs_and_original_values(stock, changes, expected):
    s = stock
    data = inline_data(s, **changes)
    page = s.client.post(url(s, "workshop_detail", pk=s.offering.pk), data)
    assert page.status_code == 400 and expected.encode() in page.content
    assert b'Session not saved' in page.content and b'role="alert"' in page.content
    assert page.context["session_form"].is_bound
    s.session.refresh_from_db()
    assert s.session.capacity == 100
    assert not AdminAuditLog.objects.filter(model_label="scheduling.session").exists()


def test_inline_room_conflict_and_reserved_capacity_protections(stock):
    s = stock
    s.session.starts_at = s.session.starts_at.replace(second=0, microsecond=0)
    s.session.ends_at = s.session.ends_at.replace(second=0, microsecond=0)
    s.session.save()
    data = inline_data(s)
    conflict = Session.objects.create(offering=s.offering, room=s.room,
        starts_at=s.session.starts_at+timedelta(days=1), ends_at=s.session.ends_at+timedelta(days=1),
        capacity=10, is_published=True)
    zone = ZoneInfo(s.location.timezone_name)
    changed = {**data, "starts_at": conflict.starts_at.astimezone(zone).strftime("%Y-%m-%dT%H:%M")}
    target = url(s, "workshop_detail", pk=s.offering.pk)
    assert b"already has a published session" in s.client.post(target, changed).content
    reserve(s, 2)
    assert b"Capacity cannot be lower" in s.client.post(target, {**data, "capacity": 1}).content
    assert b"This session has reservations" in s.client.post(target, changed).content


def test_inline_new_session_and_invalid_new_session(stock):
    s = stock
    target = url(s, "workshop_detail", pk=s.offering.pk)
    page = s.client.get(target, {"edit_session": "new"})
    assert page.status_code == 200 and page.context["adding_session"]
    data = inline_data(s, session_id="new", starts_at="2030-07-01T16:00", duration_minutes=120)
    data.pop("edit_revision")
    invalid = s.client.post(target, {**data, "capacity": ""})
    assert invalid.status_code == 400 and invalid.context["adding_session"]
    page = s.client.post(target, data)
    assert page.status_code == 302
    created = s.offering.sessions.exclude(pk=s.session.pk).get()
    assert not created.is_published and created.starts_at.hour == 23


def test_inline_permissions_csrf_and_foreign_sessions(stock, other_catalog):
    s = stock
    target = url(s, "workshop_detail", pk=s.offering.pk)
    data = inline_data(s)
    foreign_session = other_catalog[-1]
    other = Offering.objects.create(location=s.location, name="Another workshop")
    other_session = Session.objects.create(offering=other, starts_at=s.session.starts_at, ends_at=s.session.ends_at, capacity=2)
    for item in [foreign_session, other_session]:
        assert s.client.get(target, {"edit_session": item.pk}).status_code == 404
        assert s.client.post(target, {**data, "session_id": item.pk}).status_code == 404
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(s.user)
    assert csrf.post(target, data).status_code == 403
    SchedulingMembership.objects.filter(user=s.user).update(role="viewer")
    assert s.client.get(target).status_code == 200
    assert s.client.get(target, {"edit_session": s.session.pk}).status_code == 404
    assert s.client.post(target, data).status_code == 404
    assert b"Add session" not in s.client.get(target).content
    SchedulingMembership.objects.filter(user=s.user).update(is_active=False)
    assert s.client.post(target, data).status_code == 404


@pytest.mark.parametrize("value", ["", "bad", "-1", "9" * 100, "1.5"])
def test_bad_inline_identifiers_fail_closed(stock, value):
    target = url(stock, "workshop_detail", pk=stock.offering.pk)
    assert stock.client.get(target, {"edit_session": value}).status_code == 400
    assert stock.client.post(target, {"session_id": value}).status_code == 400
    assert stock.client.post(target, {}).status_code == 400
    assert stock.client.put(target).status_code == 405


def test_workshop_inventory_is_scoped_to_catalog_and_location(stock):
    s = stock
    east = Location.objects.create(profile=s.profile, name="East studio")
    StockLevel.objects.create(material=s.material, location=east, on_hand=40)
    catalog = InventoryCatalog.objects.create(profile=s.profile, name="Other materials")
    from scheduling.models import Material
    unrelated = Material.objects.create(profile=s.profile, name="Unrelated stock", sku="OTHER")
    StockLevel.objects.create(material=unrelated, location=s.location, on_hand=30)
    target = url(s, "settings")
    filters = {"location": s.location.pk, "workshop": s.offering.pk}
    page = s.client.get(target, filters)
    assert page.status_code == 200
    assert [row["level"].pk for row in page.context["stock"]] == [s.level.pk]
    assert b"Manage catalog" not in page.content and b"Unrelated stock" not in page.content
    assert f'name="workshop" value="{s.offering.pk}"'.encode() in page.content
    assert "workshop" not in dict(page.context["location_query_fields"])
    assert not list(s.client.get(target, {**filters, "q": "missing"}).context["stock"])
    assert len(s.client.get(target, {**filters, "q": "cat"}).context["stock"]) == 1
    assert s.client.get(target, {**filters, "location": east.pk}).status_code == 400
    assert s.client.get(target, {**filters, "location": ""}).status_code == 400
    s.offering.inventory_catalog = None
    s.offering.save()
    assert not list(s.client.get(target, filters).context["stock"])


@pytest.mark.parametrize("value", ["", "bad", "-1", "9" * 100])
def test_inventory_rejects_malformed_workshop(stock, value):
    assert stock.client.get(url(stock, "settings"), {"workshop": value, "location": stock.location.pk}).status_code == 404


def test_inventory_rejects_foreign_workshop(stock, other_catalog):
    assert stock.client.get(url(stock, "settings"), {"workshop": other_catalog[3].pk, "location": stock.location.pk}).status_code == 404
