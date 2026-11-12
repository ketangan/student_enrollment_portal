import pytest
from django.test import Client

from core.models import AdminAuditLog
from scheduling import reservation_notes
from scheduling.models import Reservation, SchedulingMembership
from .test_inventory import stock, reserve, confirm, url
from .test_guest_lists import booking
from .test_studio_preparation import query
from .test_location_access import two_locations
from .test_reservations import inventory, path

pytestmark = pytest.mark.django_db


def data(item, profile, body, note=None):
    form = reservation_notes.form_for(item, profile, note=note)
    return {"action": "note", "body": body, **{field.name: field.value() or "" for field in form.hidden_fields()}}


@pytest.mark.parametrize("kind", ["workshop", "party"])
def test_inline_notes_phone_and_detail_share_records(stock, booking, kind):
    item = booking if kind == "party" else reserve(stock)
    if kind == "workshop":
        confirm(stock, item)
    source = item.party_request if kind == "party" else item.workshop_request
    field = "organizer_phone" if kind == "party" else "purchaser_phone"
    setattr(source, field, "+1 (310) 555-0100")
    source.save(update_fields=[field])
    target = url(stock, "studio_preparation_update", public_id=item.public_id)
    page = stock.client.get(url(stock, "studio_preparation"), query(item, note=f"{item.public_id}:new"))
    assert b'+1 (310) 555-0100' in page.content
    assert b'href="tel:' not in page.content
    assert b'Add internal note</label>' in page.content
    payload = data(item, stock.profile, "Extra seating <script>bad()</script>")
    for _ in range(2):
        assert stock.client.post(target, payload, HTTP_X_REQUESTED_WITH="XMLHttpRequest").json() == {"saved": True}
    assert item.notes.count() == 1
    note = item.notes.get()
    old = {"key": str(note.pk), "body": note.body}
    changed = data(item, stock.profile, "Seat near entrance", old)
    assert stock.client.post(target, changed).status_code == 302
    assert stock.client.post(target, changed).status_code == 302
    note.refresh_from_db()
    assert note.body == "Seat near entrance"
    assert stock.client.post(target, {**changed, "body": "Stale change"}).status_code == 400
    audit = AdminAuditLog.objects.get(changes__action="internal_notes_updated")
    assert audit.changes["before"] == old["body"] and audit.changes["after"] == note.body
    page = stock.client.get(url(stock, "studio_preparation"), query(item))
    assert b"Seat near entrance" in page.content and b"bad()" not in page.content
    detail = stock.client.get(url(stock, "reservation_detail", public_id=item.public_id))
    assert b"Seat near entrance" in detail.content
    assert b'<dt>Phone</dt><dd>+1 (310) 555-0100</dd>' in detail.content
    assert b"+1 (310) 555-0100" in stock.client.get(url(stock, "reservations"), {"show_completed": "true"}).content
    assert b"+1 (310) 555-0100" in stock.client.get(url(stock, "studio_preparation"), query(item, print="1")).content
    prep = stock.client.get(url(stock, "reservation_preparation", public_id=item.public_id))
    assert b'class="prep-overview prep-overview--contact"' in prep.content
    assert b'<dt>Phone</dt><dd>+1 (310) 555-0100</dd>' in prep.content
    if kind == "workshop":
        request_page = stock.client.get(url(stock, "request_detail", public_id=source.public_id))
        assert b"+1 (310) 555-0100" in request_page.content
    # Clearing a contact must not resurrect the historical snapshot value.
    item.snapshot["organizer_phone"] = "Old phone"
    item.save(update_fields=["snapshot"])
    setattr(source, field, "")
    source.save(update_fields=[field])
    for route in ("reservation_detail", "reservation_preparation"):
        empty = stock.client.get(url(stock, route, public_id=item.public_id))
        assert b"Old phone" not in empty.content and b"<dt>Phone</dt>" not in empty.content


def test_notes_are_below_people_and_edit_replaces_read_only_copy(stock):
    item = reserve(stock); confirm(stock, item)
    note = item.notes.create(body="A single visible note", actor=stock.user)
    page = stock.client.get(url(stock, "studio_preparation"), query(item, note=f"{item.public_id}:{note.pk}"))
    html = page.content.decode()
    assert html.index('class="prep-person') < html.index('class="prep-booking-notes"')
    assert html.count("A single visible note") == 1


@pytest.mark.parametrize("key", ["reservation", "party", "staff"])
def test_legacy_and_inquiry_notes_edit_without_changing_customer_notes(stock, booking, key):
    booking.internal_notes = "Original reservation"
    booking.save(update_fields=["internal_notes"])
    booking.party_request.preparation_notes = "Original party"
    booking.party_request.customer_notes = "Customer original"
    booking.party_request.save()
    note = booking.party_request.staff_notes.create(actor=stock.user, body="Inquiry note")
    old = next(row for row in reservation_notes.editable_notes(booking) if row["key"] == (str(note.pk) if key == "staff" else key))
    target = url(stock, "studio_preparation_update", public_id=booking.public_id)
    assert stock.client.post(target, data(booking, stock.profile, "Edited from roster", old)).status_code == 302
    booking.refresh_from_db()
    assert booking.party_request.customer_notes == "Customer original"
    assert any(row["body"] == "Edited from roster" for row in reservation_notes.editable_notes(booking))
    assert AdminAuditLog.objects.get(changes__action="internal_notes_updated").changes["before"] == old["body"]


def test_notes_remain_editable_after_preparation_and_material_use(stock):
    item = reserve(stock); confirm(stock, item)
    item.stock_allocations.update(consumed_at=item.starts_at)
    page = stock.client.get(url(stock, "studio_preparation"), query(item))
    entry = page.context["studios"][0]["entries"][0]
    assert not entry["writable"] and entry["notes_writable"]
    assert stock.client.post(entry["action_url"], data(item, stock.profile, "Staff update")).status_code == 302


@pytest.mark.parametrize("key", ["999999999999999999999999", "unknown", "party", "0"])
def test_invalid_target_never_changes_other_records(stock, key):
    item = reserve(stock); confirm(stock, item)
    payload = data(item, stock.profile, "Bad change", {"key": key, "body": "Before"})
    assert stock.client.post(url(stock, "studio_preparation_update", public_id=item.public_id), payload).status_code == 400
    assert not item.notes.exists()


def test_other_booking_note_viewer_csrf_and_blank_protection(stock):
    item = reserve(stock); confirm(stock, item)
    other = reserve(stock); confirm(stock, other)
    private = other.notes.create(body="Private", actor=stock.user)
    target = url(stock, "studio_preparation_update", public_id=item.public_id)
    payload = data(item, stock.profile, "Wrong", {"key": str(private.pk), "body": private.body})
    assert stock.client.post(target, payload).status_code == 400
    for body in ("", "x" * 3001):
        assert stock.client.post(target, data(item, stock.profile, body)).status_code == 400
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(stock.user)
    assert strict.post(target, data(item, stock.profile, "CSRF")).status_code == 403
    SchedulingMembership.objects.filter(user=stock.user).update(role="viewer")
    assert stock.client.post(target, data(item, stock.profile, "Viewer")).status_code == 404
    page = stock.client.get(url(stock, "studio_preparation"), query(item))
    assert b'aria-label="Add note"' not in page.content
    private.refresh_from_db()
    assert private.body == "Private"


def test_notes_cannot_cross_location(two_locations, booking_member, client):
    _, _, item, forbidden, _ = two_locations
    client.force_login(booking_member.user)
    target = path(booking_member, "studio_preparation_update", public_id=forbidden.public_id)
    assert client.post(target, data(forbidden, booking_member.profile, "Unauthorized")).status_code == 404


@pytest.mark.django_db(transaction=True)
def test_concurrent_edit_cannot_overwrite_a_saved_note(stock):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    item = reserve(stock); confirm(stock, item)
    note = item.notes.create(body="Before", actor=stock.user)
    old = {"key": str(note.pk), "body": note.body}
    payloads = [data(item, stock.profile, f"Writer {i}", old) for i in range(2)]
    barrier = Barrier(2)
    def save(index):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return reservation_notes.update(stock.user, stock.school.slug, item.public_id, payloads[index]).is_valid()
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [False, True]
    note.refresh_from_db()
    change = AdminAuditLog.objects.get(changes__action="internal_notes_updated")
    assert change.changes["before"] == "Before" and change.changes["after"] == note.body
