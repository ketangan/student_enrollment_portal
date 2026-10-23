from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.test import Client

from core.models import AdminAuditLog
from scheduling import reservation_notes
from .test_reservations import inventory, workshop_request, reserve, path, party_request
from .test_camps import camp, register
from .test_checkout import merchant, online

pytestmark = pytest.mark.django_db


def payload(item, member, value):
    form = reservation_notes.form_for(item, member.profile)
    return {"action": "save_notes", form._meta.fields[0]: value,
            "edit_revision": form["edit_revision"].value()}


@pytest.mark.parametrize("kind", ["workshop", "party", "camp"])
def test_staff_notes_reuse_source_and_preserve_booking(inventory, booking_member, camp, client, kind):
    source = party_request(inventory, start=inventory[3].ends_at + timedelta(days=5)) if kind == "party" else workshop_request(inventory)
    item = register(camp).reservation if kind == "camp" else reserve(booking_member, source)
    booking_member.role = "scheduler"
    booking_member.save()
    client.force_login(booking_member.user)
    target = path(booking_member, "reservation_detail", public_id=item.public_id)
    before = (item.state, item.quantity, item.order.total_cents)
    data = payload(item, booking_member, "Prepare extra seating <script>alert(1)</script>")
    assert client.post(target, data).status_code == 302
    item.refresh_from_db()
    assert (item.state, item.quantity, item.order.total_cents) == before
    page = client.get(target)
    assert b"&lt;script&gt;alert(1)&lt;/script&gt;" in page.content
    assert b"Internal notes updated" in page.content
    if kind == "party":
        source.refresh_from_db()
        assert source.preparation_notes == data["preparation_notes"]
        assert not item.internal_notes
        assert source.revision == 1
        assert b"Prepare extra seating" in client.get(path(booking_member, "reservation_preparation", public_id=item.public_id)).content
    else:
        assert item.internal_notes == data["internal_notes"]
        assert b"Prepare extra seating" in client.get(path(booking_member, "reservation_preparation", public_id=item.public_id)).content
    # Stale writes cannot overwrite newer notes; unchanged saves do not add audits.
    assert client.post(target, data).status_code == 400
    field = reservation_notes.form_for(item, booking_member.profile)._meta.fields[0]
    assert client.post(target, payload(item, booking_member, data[field])).status_code == 302
    assert AdminAuditLog.objects.filter(changes__action="internal_notes_updated").count() == 1
    assert client.post(target, payload(item, booking_member, "")).status_code == 302


def test_party_summary_uses_agreed_snapshot(inventory, booking_member, client):
    source = party_request(inventory)
    source.package_snapshot = {"name": "Agreed party", "included_observers": 2}
    source.occasion = "Birthday"
    source.save()
    item = reserve(booking_member, source)
    source.package_snapshot = {"name": "Changed later", "included_observers": 5}
    source.save()
    client.force_login(booking_member.user)
    page = client.get(path(booking_member, "reservation_detail", public_id=item.public_id))
    assert b"Agreed party" in page.content and b"Changed later" not in page.content
    assert b"<dt>Observers included</dt><dd>2</dd>" in page.content
    assert b"<dt>Occasion</dt><dd>Birthday</dd>" in page.content


def test_notes_permissions_validation_and_scope(inventory, booking_member, client):
    item = reserve(booking_member, workshop_request(inventory))
    target = path(booking_member, "reservation_detail", public_id=item.public_id)
    data = payload(item, booking_member, "Private staff note")
    client.force_login(booking_member.user)
    assert client.post(target, {**data, "internal_notes": "x" * 3001}).status_code == 400
    assert client.post(target, {**data, "edit_revision": "tampered"}).status_code == 400
    csrf = Client(enforce_csrf_checks=True)
    csrf.force_login(booking_member.user)
    assert csrf.post(target, data).status_code == 403
    booking_member.role = "viewer"
    booking_member.save()
    assert b"Save notes" not in client.get(target).content
    assert client.post(target, data).status_code == 404
    booking_member.is_active = False
    booking_member.save()
    assert client.get(target).status_code == client.post(target, data).status_code == 404
    assert not AdminAuditLog.objects.filter(changes__action="internal_notes_updated").exists()


@pytest.mark.parametrize("calendar", ["https://evil.example/", "//evil.example/", "//[", "/bookings/another-school/", "x" * 2049])
def test_calendar_return_rejects_external_or_other_school(inventory, booking_member, client, calendar):
    booking_member.profile.calendar_enabled = True
    booking_member.profile.save()
    item = reserve(booking_member, workshop_request(inventory))
    client.force_login(booking_member.user)
    page = client.get(path(booking_member, "reservation_detail", public_id=item.public_id), {"calendar": calendar})
    assert page.status_code == 200
    assert b"Back to calendar" not in page.content


def test_calendar_return_survives_notes_and_prep_tabs(inventory, booking_member, client):
    booking_member.profile.calendar_enabled = True
    booking_member.profile.save()
    item = reserve(booking_member, workshop_request(inventory))
    client.force_login(booking_member.user)
    calendar = path(booking_member, "workspace") + "?view=calendar&range=week&date=2026-10-22"
    target = path(booking_member, "reservation_detail", public_id=item.public_id) + "?" + urlencode({"calendar": calendar})
    page = client.get(target)
    assert page.context["calendar_return"] == calendar
    prep = page.context["booking_navigation"]["section"]["tabs"][1]["url"]
    assert client.get(prep).context["calendar_return"] == calendar
    assert client.post(target, payload(item, booking_member, "A note")).url == target + "#internal-notes"
    booking_member.profile.calendar_enabled = False
    booking_member.profile.save()
    assert b"Back to calendar" not in client.get(target).content


def test_notes_are_not_customer_data_or_cross_tenant(inventory, booking_member, merchant, client):
    from core.models import School
    from scheduling.models import SchedulingProfile, SchedulingMembership
    from scheduling.checkout import payment_token
    item = online(booking_member, workshop_request(inventory))
    reservation_notes.update(booking_member.user, booking_member.profile.school.slug,
        item.public_id, payload(item, booking_member, "Private staff-only instruction"))
    page = client.get(path(booking_member, "customer_payment", token=payment_token(item)))
    assert page.status_code == 200 and b"Private staff-only instruction" not in page.content
    item.refresh_from_db()
    assert "Private staff-only instruction" not in str(item.snapshot)
    school = School.objects.create(slug="other-notes", display_name="Other")
    profile = SchedulingProfile.objects.create(school=school, enabled=True, capabilities=["reservations"])
    SchedulingMembership.objects.create(user=booking_member.user, profile=profile, role="owner")
    client.force_login(booking_member.user)
    target = path(booking_member, "reservation_detail", public_id=item.public_id).replace(booking_member.profile.school.slug, school.slug)
    assert client.get(target).status_code == client.post(target, payload(item, booking_member, "wrong school")).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_concurrent_notes_save_preserves_first_writer(inventory, booking_member):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    item = reserve(booking_member, workshop_request(inventory))
    data = payload(item, booking_member, "")
    barrier = Barrier(2)
    def save(index):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            form = reservation_notes.update(booking_member.user, booking_member.profile.school.slug,
                item.public_id, {**data, "internal_notes": f"Writer {index}"})
            return form.is_valid()
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [False, True]
    assert AdminAuditLog.objects.filter(changes__action="internal_notes_updated").count() == 1
