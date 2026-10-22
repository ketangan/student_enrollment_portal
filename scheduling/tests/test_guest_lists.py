import uuid
import time
from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from core.payments.models import PaymentOrder
from scheduling import guest_lists
from scheduling.models import PartyRequest, Reservation
from scheduling.tests.test_inventory import stock

pytestmark = pytest.mark.django_db


@pytest.fixture
def booking(stock):
    source = PartyRequest.objects.create(offering=stock.offering, organizer_name="Host",
        organizer_email="host@example.invalid", starts_at=stock.start, ends_at=stock.start + timedelta(hours=2),
        group_size=2, space="exclusive", submission_key=uuid.uuid4(), submission_digest="d", browser_digest="b", room=stock.room)
    order = PaymentOrder.objects.create(school=stock.school, purpose="booking", source_key=str(uuid.uuid4()),
        total_cents=10000, deposit_cents=0)
    return Reservation.objects.create(profile=stock.profile, party_request=source, room=stock.room,
        starts_at=source.starts_at, ends_at=source.ends_at, quantity=2, state="confirmed", order=order,
        hold_until=stock.start, snapshot={"timezone": "UTC", "workshop": "Party", "guest_list_deadline_days": 7})


def payload(stock, count=2, revision=0, action="submit"):
    data = {"revision": revision, "action": action, "materials-TOTAL_FORMS": count,
            "materials-INITIAL_FORMS": 0, "materials-MIN_NUM_FORMS": 0, "materials-MAX_NUM_FORMS": 50}
    for i in range(count):
        data.update({f"materials-{i}-guest_name": f"Guest {i+1}", f"materials-{i}-design": stock.cat.pk,
                     f"materials-{i}-color": stock.blue.pk})
    return data


def url(booking, slug=None):
    return reverse("scheduling:guest_list", kwargs={"school_slug": slug or booking.profile.school.slug,
                                                   "token": guest_lists.token_for(booking)})


def test_draft_submit_and_retry(client, stock, booking):
    path = url(booking)
    assert client.get(path).status_code == 200
    assert client.post(path, payload(stock, 1, action="draft")).status_code == 302
    booking.refresh_from_db()
    assert len(booking.snapshot["guest_list_draft"]) == 1
    assert not booking.stock_allocations.exists()
    assert client.post(path, payload(stock, revision=1)).status_code == 302
    booking.refresh_from_db(); booking.party_request.refresh_from_db()
    assert len(booking.party_request.materials) == 2
    assert "guest_list_draft" not in booking.snapshot
    assert booking.stock_allocations.get().quantity == 2
    assert not booking.snapshot["guest_selections_pending"]
    assert client.post(path, payload(stock, revision=1)).status_code == 400
    assert booking.stock_allocations.get().quantity == 2
    assert client.post(path, payload(stock, revision=2)).status_code == 302
    assert booking.stock_allocations.count() == 1


@pytest.mark.parametrize("state", ["held", "canceled", "completed", "review"])
def test_closed_states(client, stock, booking, state):
    booking.state = state; booking.save()
    assert client.post(url(booking), payload(stock)).status_code == 400
    assert not booking.stock_allocations.exists()


def test_tampering_and_wrong_school(client, stock, booking):
    assert client.get(url(booking, "another-school")).status_code == 404
    assert client.get(url(booking)[:-1] + "bad/").status_code == 404
    stock.profile.enabled = False; stock.profile.save()
    assert client.get(url(booking)).status_code == 404


def test_reservation_guest_list_summary_and_edit_permissions(client, stock, booking):
    from scheduling.models import SchedulingMembership
    client.force_login(stock.user)
    path = reverse("scheduling:reservation_detail", kwargs={"school_slug": stock.school.slug,
                                                            "public_id": booking.public_id})
    booking.snapshot["guest_list_draft"] = [{"guest_name": "Pending guest"}]
    booking.save()
    response = client.get(path)
    assert response.status_code == 200
    assert response.context["guest_list_draft_count"] == 1
    assert response.context["guest_list_due"] == guest_lists.due_at(booking)
    assert response.context["staff_guest_list_url"]
    assert "/guests/" in response.context["guest_list_url"]
    assert not response.context["guest_list_complete"]
    SchedulingMembership.objects.filter(user=stock.user).update(role="viewer")
    response = client.get(path)
    assert response.status_code == 200
    assert "staff_guest_list_url" not in response.context
    assert "guest_list_url" not in response.context
    SchedulingMembership.objects.filter(user=stock.user).update(role="owner")
    booking.state = "canceled"
    booking.save()
    assert "guest_list_url" not in client.get(path).context


def test_counts_invalid_selection_and_stock_rollback(client, stock, booking):
    path = url(booking)
    for count in [0, 1, 3]:
        assert client.post(path, payload(stock, count)).status_code == 400
    data = payload(stock); data["materials-0-design"] = 999999
    assert client.post(path, data).status_code == 400
    stock.level.on_hand = 1; stock.level.save()
    assert client.post(path, payload(stock)).status_code == 400
    booking.refresh_from_db(); booking.party_request.refresh_from_db()
    assert booking.party_request.revision == 0
    assert not booking.party_request.materials
    assert not booking.stock_allocations.exists()


def test_prepared_or_started_lists_cannot_change(client, stock, booking):
    booking.prepared_at = timezone.now(); booking.save()
    assert client.post(url(booking), payload(stock)).status_code == 400
    booking.prepared_at = None; booking.starts_at = timezone.now() - timedelta(hours=1); booking.save()
    assert client.post(url(booking), payload(stock)).status_code == 400


def test_deadline_snapshot_and_private_response(client, booking):
    assert guest_lists.due_at(booking) == booking.starts_at - timedelta(days=7)
    response = client.get(url(booking))
    assert response["Referrer-Policy"] == "same-origin"
    assert "no-store" in response["Cache-Control"]
    assert "noindex" in response["X-Robots-Tag"]


def test_school_deadline_configuration(stock, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(guest_lists, "load_school_config", lambda slug: SimpleNamespace(raw={"scheduling": {"guest_list_deadline_days": 4}}))
    assert guest_lists.deadline_days(stock.school) == 4
    monkeypatch.setattr(guest_lists, "load_school_config", lambda slug: SimpleNamespace(raw={"scheduling": {"guest_list_deadline_days": -1}}))
    with pytest.raises(ValidationError):
        guest_lists.deadline_days(stock.school)


def test_same_choices_preserve_frozen_recipe_and_draft_has_no_extra_row(client, stock, booking):
    path = url(booking)
    assert client.post(path, payload(stock)).status_code == 302
    allocation = booking.stock_allocations.get()
    recipe = stock.item.requirements.get()
    recipe.quantity = 2; recipe.save()
    response = client.get(path)
    assert len(response.context["form"].material_forms.forms) == 2
    assert client.post(path, payload(stock, revision=1)).status_code == 302
    assert booking.stock_allocations.get().pk == allocation.pk
    assert booking.stock_allocations.get().quantity == 2


def test_expired_link_and_no_csrf(client, booking, monkeypatch):
    from django.test import Client
    path = url(booking)
    assert Client(enforce_csrf_checks=True).post(path, {}).status_code == 403
    later = time.time() + 367 * 86400
    monkeypatch.setattr("django.core.signing.time.time", lambda: later)
    assert client.get(path).status_code == 404


def test_past_requested_deadline_does_not_block_short_notice_booking(client, stock, booking):
    booking.starts_at = timezone.now() + timedelta(days=2)
    booking.save()
    assert guest_lists.due_at(booking) < timezone.now()
    assert b"Guest selections are due now." in client.get(url(booking)).content
    assert client.post(url(booking), payload(stock)).status_code == 302


def test_package_review_defers_guests_and_preserves_previous_selections(stock, booking):
    from scheduling.models import PartyPackage
    from scheduling.party_forms import PartyReviewForm
    PartyPackage.objects.create(offering=stock.offering, name="Group", participant_limit=6)
    source = booking.party_request
    source.materials = [{"item_id": stock.item.pk, "name": stock.item.name, "quantity": 2}]
    original = list(source.materials)
    form = PartyReviewForm({"revision": source.revision, "status": "reviewed", "room": stock.room.pk,
        "proposed_total": "100", "payment_at_booking": "full", "duration_minutes": "120"}, instance=source)
    assert form.material_forms is None
    assert form.is_valid(), form.errors
    assert source.materials == original


@pytest.mark.parametrize("age", ["-1", "131", "1.5", "unknown"])
def test_invalid_guest_age(client, stock, booking, age):
    data = payload(stock)
    data["materials-0-guest_age"] = age
    assert client.post(url(booking), data).status_code == 400
    assert not booking.stock_allocations.exists()


def staff_url(booking, slug=None):
    return reverse("scheduling:staff_guest_list", kwargs={
        "school_slug": slug or booking.profile.school.slug, "public_id": booking.public_id})


@pytest.mark.parametrize("role", ["owner", "scheduler"])
def test_staff_can_enter_emailed_guests_with_ages(client, stock, booking, role):
    from core.models import AdminAuditLog
    from scheduling.models import SchedulingMembership
    from scheduling.reservation_workspace import participant_context
    SchedulingMembership.objects.filter(user=stock.user).update(role=role)
    client.force_login(stock.user)
    path = staff_url(booking)
    response = client.get(path)
    assert response.status_code == 200
    assert b"Back to reservation" in response.content
    data = payload(stock, count=1, action="draft")
    data["materials-0-guest_age"] = "0"
    assert client.post(path, data).status_code == 302
    response = client.get(path)
    assert response.context["form"].material_forms.forms[0]["guest_age"].value() == 0
    assert not booking.stock_allocations.exists()
    data = payload(stock, revision=1)
    data["materials-0-guest_age"] = "8"
    assert client.post(path, data).status_code == 302
    booking.party_request.refresh_from_db()
    assert booking.party_request.materials[0]["guest_age"] == 8
    assert booking.party_request.materials[1]["guest_age"] is None
    assert "Age" in participant_context(booking)["participant_table"]["columns"]
    assert AdminAuditLog.objects.filter(actor=stock.user, model_label="scheduling.reservation",
        object_id=str(booking.pk), changes__action="guest_list_submitted").exists()
    # Host and staff edit the same revision, not separate copies of guest data.
    assert client.post(url(booking), payload(stock, revision=1)).status_code == 400
    booking.prepared_at = timezone.now(); booking.save()
    assert client.post(path, payload(stock, revision=2)).status_code == 400


def test_staff_access_is_scoped_and_viewers_cannot_write(client, stock, booking):
    from scheduling.models import SchedulingMembership
    path = staff_url(booking)
    assert client.get(path).status_code == 302
    client.force_login(stock.user)
    assert client.get(staff_url(booking, "another-school")).status_code == 404
    SchedulingMembership.objects.filter(user=stock.user).update(role="viewer")
    assert client.get(path).status_code == 404
    assert client.post(path, payload(stock)).status_code == 404
    SchedulingMembership.objects.filter(user=stock.user).update(is_active=False)
    assert client.post(path, payload(stock)).status_code == 404
    assert not booking.stock_allocations.exists()


def test_customer_payment_exposes_followup_only_after_confirmation(client, stock, booking, settings):
    from core.payments.models import PaymentAccount
    from scheduling.checkout import payment_token
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_local_unit_tests_only"
    booking.order.account = PaymentAccount.objects.create(school=stock.school,
        reference="acct_guest_test", enabled=True)
    booking.order.save()
    path = reverse("scheduling:customer_payment", kwargs={
        "school_slug": stock.school.slug, "token": payment_token(booking)})
    response = client.get(path)
    assert response.status_code == 200
    assert b"Add or update guest details" in response.content
    assert client.get(response.context["guest_list_url"]).status_code == 200
    for state in ("held", "review", "canceled", "completed"):
        booking.state = state; booking.save()
        assert b"Add or update guest details" not in client.get(path).content


@pytest.mark.django_db(transaction=True)
def test_simultaneous_submissions_allocate_once(stock, booking):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    from django.test import Client
    barrier = Barrier(2)
    path, data = url(booking), payload(stock)

    def submit():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return Client().post(path, data).status_code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [302, 400]
    assert booking.stock_allocations.count() == 1
    assert booking.stock_allocations.get().quantity == 2
