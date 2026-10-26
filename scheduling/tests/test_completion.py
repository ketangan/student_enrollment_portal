"""Reservation fulfilment, shared participant tables and payment lifecycle safety."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.http import Http404
from django.test import Client
from django.utils import timezone

from core.models import AdminAuditLog, School
from core.payments import service
from scheduling import completion, inventory as stock_service, preparation
from scheduling.checkout import approve_refund, settle_booking_event
from scheduling.models import Reservation, SchedulingMembership, SchedulingProfile, StockAdjustment, StockLevel, Location
from scheduling.reservation_workspace import participant_table
from scheduling.reservations import active_claims, cancel_reservation
from .test_inventory import stock, reserve, confirm, url
from .test_reservations import inventory, workshop_request, party_request, reserve as plain_reserve, settle, path
from .test_checkout import merchant, prepare, event_for
from .test_payment_recovery import paid
from .test_camps import camp, register
from .test_workshop_polish import command
from .test_visits import tour, book

pytestmark = pytest.mark.django_db


def finish(s, item):
    return completion.complete(s.user, s.school.slug, item.public_id)


def test_completion_consumes_once_preserves_payment_and_location(stock):
    s = stock
    item = reserve(s, 2); confirm(s, item)
    other_location = Location.objects.create(profile=s.profile, name="Other studio")
    other = StockLevel.objects.create(material=s.material, location=other_location, on_hand=20)
    before = list(item.order.entries.values())
    assert finish(s, item).state == "completed"
    assert finish(s, item).state == "completed"
    s.level.refresh_from_db(); other.refresh_from_db(); item.refresh_from_db()
    assert s.level.on_hand == 3 and other.on_hand == 20
    assert item.stock_allocations.get().consumed_at
    assert StockAdjustment.objects.filter(level=s.level, delta=-2).count() == 1
    assert AdminAuditLog.objects.filter(object_id=str(item.pk), changes__action="completed", actor=s.user).count() == 1
    assert list(item.order.entries.values()) == before
    # Finishing early must not make an occupied seat/room available for resale.
    assert active_claims().filter(pk=item.pk).exists()
    assert stock_service.reserved(s.level) == 0
    with pytest.raises(ValidationError, match="cannot be canceled"):
        cancel_reservation(s.user, s.school.slug, item.public_id)
    with pytest.raises(ValidationError, match="confirmed"):
        preparation.update(s.user, s.school.slug, item.public_id, command(s, item, "reopen"))


def test_old_used_materials_can_complete_without_another_deduction(stock):
    s = stock
    item = reserve(s); confirm(s, item)
    stock_service.consume(s.user, s.school.slug, item.public_id)
    finish(s, item)
    s.level.refresh_from_db()
    assert s.level.on_hand == 4 and StockAdjustment.objects.count() == 1


@pytest.mark.parametrize("state", ["held", "review", "canceled"])
def test_only_confirmed_reservations_can_complete(stock, state):
    s = stock; item = reserve(s)
    Reservation.objects.filter(pk=item.pk).update(state=state)
    with pytest.raises(ValidationError, match="Only confirmed"):
        finish(s, item)
    assert not StockAdjustment.objects.exists()
    assert Reservation.objects.get(pk=item.pk).state == state


@pytest.mark.parametrize("fault", ["missing", "shortage", "audit_failure"])
def test_completion_rolls_back_all_effects_on_failure(stock, monkeypatch, fault):
    s = stock; item = reserve(s); confirm(s, item)
    if fault == "missing":
        item.stock_allocations.all().delete()
    elif fault == "shortage":
        StockLevel.objects.filter(pk=s.level.pk).update(on_hand=0)
    else:
        def reject(*args, **kwargs):
            raise ValidationError("Audit unavailable")
        monkeypatch.setattr(completion, "_audit", reject)
    with pytest.raises(ValidationError):
        finish(s, item)
    item.refresh_from_db(); s.level.refresh_from_db()
    assert item.state == "confirmed" and not StockAdjustment.objects.exists()
    assert not item.stock_allocations.filter(consumed_at__isnull=False).exists()
    assert s.level.on_hand == (0 if fault == "shortage" else 5)


@pytest.mark.django_db(transaction=True)
def test_simultaneous_completion_deducts_once(stock):
    s = stock; item = reserve(s, 2); confirm(s, item)
    barrier = Barrier(2)
    def submit(_):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return finish(s, item).state
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(submit, range(2))) == ["completed", "completed"]
    s.level.refresh_from_db()
    assert s.level.on_hand == 3 and StockAdjustment.objects.count() == 1
    assert AdminAuditLog.objects.filter(changes__action="completed").count() == 1


@pytest.mark.parametrize("role,allowed", [("owner", True), ("scheduler", True), ("viewer", False)])
def test_completion_permissions_confirmation_and_csrf(stock, role, allowed):
    s = stock; item = reserve(s); confirm(s, item)
    SchedulingMembership.objects.filter(user=s.user).update(role=role)
    target = url(s, "reservation_complete", public_id=item.public_id)
    detail = url(s, "reservation_detail", public_id=item.public_id)
    assert (b"Workshop completed</a>" in s.client.get(detail).content) == allowed
    csrf = Client(enforce_csrf_checks=True); csrf.force_login(s.user)
    assert csrf.post(target, {"action": "complete"}).status_code == 403
    assert s.client.get(target).status_code == (200 if allowed else 404)
    assert Reservation.objects.get(pk=item.pk).state == "confirmed"
    assert s.client.post(target, {}).status_code == (400 if allowed else 404)
    assert s.client.post(target, {"action": "complete", "next": "https://evil.invalid"}).status_code == (302 if allowed else 404)
    if allowed:
        assert s.client.post(target, {"action": "complete"}).url == detail
        assert b"Confirm once" not in s.client.get(target).content
    assert s.client.post(target.replace(str(item.public_id), str(uuid.uuid4())), {"action": "complete"}).status_code == 404
    alien = SchedulingProfile.objects.create(school=School.objects.create(slug="private-other"), enabled=True)
    with pytest.raises(Http404):
        completion.complete(s.user, alien.school.slug, item.public_id)
    s.profile.capabilities = []; s.profile.save()
    assert s.client.post(target, {"action": "complete"}).status_code == 404
    assert s.client.get(f"/bookings/{s.school.slug}/reservations/{item.public_id}/materials/").status_code == 404


def test_completed_tabs_list_filter_and_audit(stock):
    s = stock; item = reserve(s); confirm(s, item)
    finish(s, item)
    response = s.client.get(url(s, "reservations"), {"state": "completed"})
    assert list(response.context["items"]) == [item]
    detail = s.client.get(url(s, "reservation_detail", public_id=item.public_id))
    assert b"Reservation completed" in detail.content and b"Materials used" in detail.content
    assert b"Cancel reservation</a>" not in detail.content and b"Workshop completed</a>" not in detail.content
    prep = s.client.get(url(s, "reservation_preparation", public_id=item.public_id))
    assert b"Preparation is on hold" not in prep.content and b"Mark prepared" not in prep.content
    assert b"> Used</span>" in prep.content
    assert not list(s.client.get(url(s, "reservations"), {"preparation": "1"}).context["items"])


def test_participant_table_uses_saved_groups_order_and_missing_choices():
    people = [
        {"name": "One", "selections": [{"group_id": 1, "group": "Design", "option": "Cat"}, {"group_id": 2, "group": "Color", "option": "Blue"}]},
        {"name": "Two", "selections": [{"group_id": 2, "group": "Renamed", "option": "Pink"}, {"group": "Size", "option": "Small"}]},
        {"name": "Three", "selections": [{"group_id": 3, "group": "Color", "option": "Green"}, {"group": "Size", "option": "Medium"}, {"group": "Size", "option": "Large"}]},
        {"name": "Four"},
    ]
    result = participant_table(people)
    assert result["columns"] == ["Design", "Color", "Size", "Color"]
    assert [r["values"] for r in result["rows"]] == [["Cat", "Blue", "", ""], ["", "Pink", "Small", ""], ["", "", "Medium, Large", "Green"], ["", "", "", ""]]
    assert participant_table([]) == {"columns": [], "keys": [], "rows": []}


def test_details_and_prep_share_participants_and_summary_without_studio_in_materials(stock):
    from django.template.loader import render_to_string
    s = stock; item = reserve(s, 2); confirm(s, item)
    person = item.workshop_request.participants.first()
    person.name = "<script>unsafe</script>"; person.selections = person.selections[:1]; person.save()
    detail = s.client.get(url(s, "reservation_detail", public_id=item.public_id))
    prep = s.client.get(url(s, "reservation_preparation", public_id=item.public_id))
    for page in (detail, prep):
        values = {key: page.context[key] for key in ("item", "contact_email", "reservation_location", "participant_table", "selection_forms")}
        for partial in ("reservation_summary", "reservation_participants"):
            rendered = render_to_string(f"scheduling/{partial}.html", values).encode()
            assert rendered in page.content
    assert [row["values"] for row in detail.context["participant_table"]["rows"]] == [row["values"] for row in prep.context["participant_table"]["rows"]]
    assert b'<details class="reservation-section reservation-summary"><summary>' in prep.content
    assert detail.context["participant_table"]["columns"] == ["Design", "Color"]
    assert b"Not selected" in prep.content and b"&lt;script&gt;" in prep.content
    assert b"<script>unsafe" not in detail.content
    assert b'<th scope="col">Material</th><th scope="col" class="numeric">Quantity</th><th scope="col">Stock status</th>' in prep.content
    assert b"Mark materials used" not in prep.content and b"Preparation Pending" in prep.content
    assert b"Payment summary" in detail.content and b">Money<" not in detail.content


def test_completed_camp_and_party_without_inventory(inventory, booking_member, camp, client):
    sources = [register(camp).reservation, plain_reserve(booking_member, party_request(inventory, start=inventory[3].ends_at + timedelta(days=5)))]
    client.force_login(booking_member.user)
    for item in sources:
        settle(booking_member, item, item.order.total_cents)
        result = completion.complete(booking_member.user, booking_member.profile.school.slug, item.public_id)
        assert result.state == "completed"
        page = client.get(path(booking_member, "reservation_detail", public_id=item.public_id))
        assert page.status_code == 200 and page.context["reservation_location"]
        assert client.get(path(booking_member, "reservation_preparation", public_id=item.public_id)).status_code == 200


@pytest.mark.parametrize("pending_before_completion", [False, True])
def test_completed_balance_payment_and_refund_never_reopen_booking(inventory, merchant, booking_member, client, pending_before_completion):
    item, deposit = paid(inventory, booking_member)
    attempt = prepare(item) if pending_before_completion else None
    completion.complete(booking_member.user, item.profile.school.slug, item.public_id)
    Reservation.objects.filter(pk=item.pk).update(starts_at=timezone.now()-timedelta(days=1))
    item.refresh_from_db()
    # Collect a remaining balance even after the workshop, with intake paused.
    item.profile.native_reservations_enabled = False; item.profile.save()
    attempt = attempt or prepare(item)
    settle_booking_event(event_for(attempt)); item.refresh_from_db()
    assert item.state == "completed" and service.totals(item.order)["net_cents"] == 10000
    settle_booking_event(event_for(attempt)); item.refresh_from_db()
    assert item.state == "completed"
    with pytest.raises(ValidationError, match="separate billing adjustment"):
        approve_refund(booking_member.user, item.profile.school.slug, item.public_id, deposit.public_id,
            key=uuid.uuid4(), amount_cents=100, reason="Service adjustment")
    client.force_login(booking_member.user)
    assert b"Review refund" not in client.get(path(booking_member, "reservation_detail", public_id=item.public_id)).content


def test_offline_balance_does_not_reopen_completed(inventory, booking_member):
    item = plain_reserve(booking_member, workshop_request(inventory)); settle(booking_member, item)
    completion.complete(booking_member.user, item.profile.school.slug, item.public_id)
    assert settle(booking_member, item, 7500).state == "completed"


def test_visit_cannot_use_workshop_completion(tour, booking_member, client):
    item = book(tour).reservation
    profile = booking_member.profile
    profile.capabilities = ["visits", "reservations"]; profile.save()
    with pytest.raises(Http404):
        completion.complete(booking_member.user, profile.school.slug, item.public_id)
    client.force_login(booking_member.user)
    assert client.post(path(booking_member, "reservation_complete", public_id=item.public_id), {"action": "complete"}).status_code == 404
    item.refresh_from_db()
    assert item.state == "confirmed"


@pytest.mark.django_db(transaction=True)
def test_completed_state_migration_preserves_existing_records(stock):
    from django.db import connection, IntegrityError, transaction
    from django.db.migrations.executor import MigrationExecutor
    s = stock; item = reserve(s); confirm(s, item)
    before = list(School.objects.values())
    entries = list(item.order.entries.values())
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0020_reservation_preparation")]
    try:
        executor.migrate(old)
        MigrationExecutor(connection).migrate(latest)
        item.refresh_from_db()
        assert item.state == "confirmed" and list(item.order.entries.values()) == entries
        assert list(School.objects.values()) == before
        finish(s, item)
        item.refresh_from_db(); assert item.state == "completed"
        with pytest.raises(IntegrityError), transaction.atomic():
            Reservation.objects.filter(pk=item.pk).update(state="invalid")
    finally:
        MigrationExecutor(connection).migrate(latest)
