from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from zoneinfo import ZoneInfo

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import close_old_connections, IntegrityError, transaction
from django.http import Http404, QueryDict
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import Lead, School, SchoolAdminMembership, Submission
from core.payments.models import PaymentOrder
from scheduling import visits
from scheduling.capabilities import validate_removal
from scheduling.models import Location, Offering, Reservation, Room, SchedulingMembership, SchedulingProfile, Session, VisitBooking, VisitProgram, VisitSlot
from scheduling.reservations import cancel_reservation, settle_offline
from scheduling.setup import records
from scheduling.workspace import overview

pytestmark = pytest.mark.django_db


@pytest.fixture
def tour(booking_member):
    profile = booking_member.profile
    profile.capabilities = ["visits"]
    profile.save()
    location = Location.objects.create(profile=profile, name="Preschool", address="123 Example Street")
    room = Room.objects.create(location=location, name="Garden room", capacity=8)
    offering = Offering.objects.create(location=location, name="Meet our preschool", is_published=True)
    program = VisitProgram.objects.create(offering=offering)
    start = (timezone.now() + timedelta(days=3)).replace(second=0, microsecond=0)
    session = Session.objects.create(offering=offering, room=room, capacity=4, starts_at=start,
        ends_at=start + timedelta(minutes=30), is_published=True)
    return VisitSlot.objects.create(program=program, session=session, host=booking_member)


def url(tour, name, **kwargs):
    return reverse("scheduling:" + name, kwargs={"school_slug": tour.host.profile.school.slug, **kwargs})


def booking_data(tour, browser="browser-a", **changes):
    values = {"contact_name": "Taylor Example", "contact_email": "taylor@example.invalid", "child_name": "Sam",
        "adults": "1", "children": "1", "authority": "on", "booking_token": visits.booking_token(tour, browser), **changes}
    data = QueryDict(mutable=True)
    data.update(values)
    return data


def book(tour, **changes):
    return visits.book(tour.host.profile.school.slug, tour.pk, booking_data(tour, **changes), "browser-a")[0]


def post_data(form, **changes):
    data = {field.name: field.value() for field in form if field.value() is not None}
    data.update(changes)
    return data


def slot_data(tour, **changes):
    return post_data(visits.slot_form(tour.host.profile, tour.program_id, pk=tour.pk), **changes)


def test_standalone_booking_is_atomic_free_idempotent_and_uses_shared_capacity(tour):
    slug = tour.host.profile.school.slug
    data = booking_data(tour)
    item, form = visits.book(slug, tour.pk, data, "browser-a")
    assert form.is_valid() and item.state == "booked"
    assert item.reservation.state == "confirmed" and item.reservation.quantity == 2
    assert item.reservation.order.total_cents == 0 and item.reservation.order.account is None
    assert not Lead.objects.exists() and not Submission.objects.exists()
    assert visits.book(slug, tour.pk, data, "browser-a")[0] == item
    assert visits.places_left(tour) == 2 and PaymentOrder.objects.count() == 1
    data["child_name"] = "Different child"
    with pytest.raises(ValidationError, match="different details"):
        visits.book(slug, tour.pk, data, "browser-a")
    with pytest.raises(ValidationError, match="already exists"):
        book(tour)
    book(tour, contact_email="second@example.invalid")
    with pytest.raises(ValidationError, match="not enough places"):
        book(tour, contact_email="third@example.invalid")
    assert VisitBooking.objects.count() == Reservation.objects.count() == PaymentOrder.objects.count() == 2


def test_local_timezone_object_and_database_snapshot_match(tour):
    tour.session.starts_at = tour.session.starts_at.astimezone(ZoneInfo("America/Los_Angeles"))
    tour.session.ends_at = tour.session.ends_at.astimezone(ZoneInfo("America/Los_Angeles"))
    assert book(tour).state == "booked"


@pytest.mark.parametrize("changes", [{"adults": "0"}, {"children": "-1"}, {"adults": "11"},
    {"contact_email": "invalid"}, {"authority": ""}, {"child_name": ""}])
def test_invalid_family_details_cannot_claim_capacity(tour, changes):
    item, form = visits.book(tour.host.profile.school.slug, tour.pk, booking_data(tour, **changes), "browser-a")
    assert item is None and form.errors
    assert not Reservation.objects.exists()


@pytest.mark.parametrize("case", ["bad-signature", "wrong-browser", "wrong-slot", "missing-key", "expired"])
def test_signed_form_boundaries(tour, case, monkeypatch):
    data = booking_data(tour)
    token = signing.loads(data["booking_token"], salt=visits.SALT)
    browser = "browser-a"
    if case == "wrong-browser":
        browser = ""
    elif case == "wrong-slot":
        token["slot"] += 1
    elif case == "missing-key":
        del token["key"]
    data["booking_token"] = signing.dumps(token, salt=visits.SALT) if case != "bad-signature" else "bad"
    if case == "expired":
        monkeypatch.setattr(signing.time, "time", lambda: timezone.now().timestamp() + 7201)
    with pytest.raises(ValidationError, match="expired"):
        visits.book(tour.host.profile.school.slug, tour.pk, data, browser)
    assert not Reservation.objects.exists()


@pytest.mark.parametrize("change", ["program", "slot", "host", "host-user", "host-role", "capacity", "past"])
def test_changed_or_closed_slot_rejected_at_commit(tour, change):
    data = booking_data(tour)
    if change == "program":
        Offering.objects.filter(pk=tour.program.offering_id).update(is_published=False)
    elif change == "slot":
        Session.objects.filter(pk=tour.session_id).update(is_published=False)
    elif change == "host":
        SchedulingMembership.objects.filter(pk=tour.host_id).update(is_active=False)
    elif change == "host-user":
        get_user_model().objects.filter(pk=tour.host.user_id).update(is_active=False)
    elif change == "host-role":
        SchedulingMembership.objects.filter(pk=tour.host_id).update(role="viewer")
    elif change == "past":
        Session.objects.filter(pk=tour.session_id).update(starts_at=timezone.now()-timedelta(minutes=1))
    else:
        Session.objects.filter(pk=tour.session_id).update(capacity=3)
    with pytest.raises(ValidationError, match="changed or is no longer"):
        visits.book(tour.host.profile.school.slug, tour.pk, data, "browser-a")


def test_program_bridge_requires_both_roles_and_preserves_stale_edits(tour):
    profile, user = tour.host.profile, tour.host.user
    form = visits.program_form(profile, pk=tour.program_id)
    data = post_data(form, capture_leads=True)
    denied = visits.save_program(user, profile.school.slug, data, tour.program_id)
    assert "capture_leads" in denied.errors
    SchoolAdminMembership.objects.create(school=profile.school, user=user, role="owner")
    assert "capture_leads" in visits.save_program(user, profile.school.slug, data, tour.program_id).errors
    user.is_staff = True
    user.save()
    assert visits.save_program(user, profile.school.slug, data, tour.program_id).is_valid()
    assert not visits.save_program(user, profile.school.slug, data, tour.program_id).is_valid()
    form = visits.program_form(profile)
    created = visits.save_program(user, profile.school.slug, {"name": "Second tour", "location": tour.program.offering.location_id})
    assert created.is_valid() and created.program.pk
    assert not visits.save_program(user, profile.school.slug, {"name": ""}).is_valid()


def test_lead_creation_and_existing_identity_preservation(tour):
    tour.program.capture_leads = True
    tour.program.save()
    item = book(tour)
    assert item.lead.status == "trial_scheduled" and item.lead.data["student_name"] == "Sam"
    lead = item.lead
    lead.name, lead.status = "Already known", "lost"
    lead.save()
    other = Session.objects.create(offering=tour.program.offering, room=tour.session.room, capacity=4,
        starts_at=tour.session.ends_at, ends_at=tour.session.ends_at+timedelta(minutes=30), is_published=True)
    second_slot = VisitSlot.objects.create(program=tour.program, session=other, host=tour.host)
    second = book(second_slot)
    lead.refresh_from_db()
    assert second.lead_id != lead.pk and lead.name == "Already known" and lead.status == "lost"
    assert second.lead.status == "trial_scheduled" and Lead.objects.count() == 2


@pytest.mark.parametrize("state,lead_state", [("attended", "trial_completed"), ("no_show", "new"), ("canceled", "new")])
def test_attendance_and_cancellation_update_only_owned_pending_lead(tour, state, lead_state):
    tour.program.capture_leads = True
    tour.program.save()
    item = book(tour)
    profile = tour.host.profile
    if state != "canceled":
        with pytest.raises(ValidationError, match="once the tour has started"):
            visits.change_state(tour.host.user, profile.school.slug, item.public_id, state, "booked")
        Session.objects.filter(pk=tour.session_id).update(starts_at=timezone.now()-timedelta(hours=1))
    changed = visits.change_state(tour.host.user, profile.school.slug, item.public_id, state, "booked")
    assert changed.state == state
    item.lead.refresh_from_db()
    assert item.lead.status == lead_state
    if state == "canceled":
        assert visits.places_left(tour) == 4
        assert "tour_slot" not in item.lead.data["form_fields"]
    assert visits.change_state(tour.host.user, profile.school.slug, item.public_id, state, "booked") == item
    with pytest.raises(ValidationError, match="changed"):
        visits.change_state(tour.host.user, profile.school.slug, item.public_id,
            "canceled" if state != "canceled" else "attended", state)


def test_attendance_does_not_rewind_manual_lead_status(tour):
    tour.program.capture_leads = True
    tour.program.save()
    item = book(tour)
    Lead.objects.filter(pk=item.lead_id).update(status="enrolled")
    Session.objects.filter(pk=tour.session_id).update(starts_at=timezone.now()-timedelta(hours=1))
    visits.change_state(tour.host.user, tour.host.profile.school.slug, item.public_id, "attended", "booked")
    item.lead.refresh_from_db()
    assert item.lead.status == "enrolled"


def test_enrollment_lead_page_and_prefill_use_existing_contract(tour, client):
    from core.views_school_common import _build_lead_prefill_data
    school = tour.host.profile.school
    school.slug, school.plan = "preschool-demo", "custom"
    school.save()
    tour.host.user.is_staff = True
    tour.host.user.save()
    SchoolAdminMembership.objects.create(school=school, user=tour.host.user, role="owner")
    tour.program.capture_leads = True
    tour.program.save()
    item = book(tour)
    client.force_login(tour.host.user)
    page = client.get(reverse("school_lead_detail", kwargs={"school_slug": school.slug, "lead_id": item.lead_id}))
    assert page.status_code == 200
    fields = {field["key"]: field["value"] for field in page.context["form_fields_editable"]}
    assert fields["student_name"] == "Sam" and fields["tour_slot"]
    raw = {"form": {"sections": [{"fields": [{"key": "guardian_name", "type": "text"}, {"key": "contact_email", "type": "email"}]}]}}
    prefill = _build_lead_prefill_data(item.lead, raw)
    assert prefill["student_name"] == "Sam" and prefill["guardian_name"] == "Taylor Example"
    assert prefill["contact_email"] == "taylor@example.invalid"
    data = booking_data(tour, contact_email="another@example.invalid")
    item2, _ = visits.book(school.slug, tour.pk, data, "browser-a")
    assert visits.book(school.slug, tour.pk, data, "browser-a")[0].lead_id == item2.lead_id
    assert school.leads.count() == 2


def test_enrollment_connection_respects_existing_access_gate(tour, client):
    from django.contrib.auth.models import AnonymousUser
    profile, user = tour.host.profile, tour.host.user
    tour.program.capture_leads = True
    tour.program.save()
    item = book(tour)
    member = SchoolAdminMembership.objects.create(school=profile.school, user=user, role="viewer")
    assert not visits.enrollment_access(AnonymousUser(), profile)
    assert not visits.enrollment_access(user, profile)
    client.force_login(user)
    assert b"View enrollment lead" not in client.get(url(tour, "visit_detail", public_id=item.public_id)).content
    user.is_staff = True
    user.save()
    assert visits.enrollment_access(user, profile) and not visits.enrollment_access(user, profile, owner=True)
    member.is_active = False
    member.save()
    assert not visits.enrollment_access(user, profile)
    member.is_active = True
    member.save()
    profile.school.is_active = False
    assert not visits.enrollment_access(user, profile)
    profile.school.is_active = True
    profile.school.trial_end_date = timezone.localdate() - timedelta(days=1)
    assert not visits.enrollment_access(user, profile)
    profile.school.is_demo = True
    assert visits.enrollment_access(user, profile)
    user.is_superuser = True
    assert visits.enrollment_access(user, profile, owner=True)


def test_staff_slot_status_matches_real_availability(tour, client):
    client.force_login(tour.host.user)
    path = url(tour, "visits")
    assert client.get(path).context["slots"][0].booking_status == "Open"
    book(tour, adults="3", children="1")
    page = client.get(path)
    assert page.context["slots"][0].booking_status == "Full" and b"4 / 4" in page.content
    Offering.objects.filter(pk=tour.program.offering_id).update(is_published=False)
    assert client.get(path).context["slots"][0].booking_status == "Closed"


def test_public_projection_has_bounded_queries_and_escapes_names(tour, client, django_assert_num_queries):
    from django.test.utils import CaptureQueriesContext
    from django.db import connection
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url(tour, "visit_catalogue"))
    count = len(queries)
    for offset in range(1, 5):
        start = tour.session.starts_at + timedelta(days=offset)
        session = Session.objects.create(offering=tour.program.offering, room=tour.session.room, capacity=4,
            starts_at=start, ends_at=start+timedelta(minutes=30), is_published=True)
        VisitSlot.objects.create(program=tour.program, session=session, host=tour.host)
    with django_assert_num_queries(count):
        response = client.get(url(tour, "visit_catalogue"))
    assert len(response.context["slots"]) == 5
    assert all(slot.places_left == 4 for slot in response.context["slots"])
    item = book(tour, child_name="<script>alert(1)</script>")
    client.force_login(tour.host.user)
    html = client.get(url(tour, "visit_detail", public_id=item.public_id)).content
    assert b"<script>alert(1)</script>" not in html and b"&lt;script&gt;" in html


def test_payment_order_failure_rolls_back_booking_and_lead(tour, monkeypatch):
    tour.program.capture_leads = True
    tour.program.save()
    def fail(**kwargs):
        raise ValidationError("Ledger unavailable")
    monkeypatch.setattr(visits.payments, "create_order", fail)
    with pytest.raises(ValidationError, match="Ledger unavailable"):
        book(tour)
    assert not VisitBooking.objects.exists() and not Lead.objects.exists() and not Reservation.objects.exists()


def test_invalid_and_stale_attendance_actions(tour):
    item = book(tour)
    for state, previous in [("invalid", "booked"), ("canceled", "attended")]:
        with pytest.raises(ValidationError):
            visits.change_state(tour.host.user, tour.host.profile.school.slug, item.public_id, state, previous)


def test_setup_checks_room_capacity_host_overlap_and_optimistic_edits(tour):
    profile, user, program = tour.host.profile, tour.host.user, tour.program
    values = slot_data(tour, capacity=3)
    assert visits.save_slot(user, profile.school.slug, program.pk, values, tour.pk).is_valid()
    assert not visits.save_slot(user, profile.school.slug, program.pk, values, tour.pk).is_valid()
    bad = visits.save_slot(user, profile.school.slug, program.pk, slot_data(tour, capacity=9), tour.pk)
    assert "capacity" in bad.errors
    other_room = Room.objects.create(location=program.offering.location, name="Second room", capacity=8)
    values = slot_data(tour, room=other_room.pk)
    values.pop("edit_revision")
    values.pop("host_revision")
    conflict = visits.save_slot(user, profile.school.slug, program.pk, values)
    assert "host already" in str(conflict.errors)
    later = tour.session.starts_at + timedelta(days=1)
    values.update(starts_at=later, ends_at=later+timedelta(minutes=30))
    assert visits.save_slot(user, profile.school.slug, program.pk, values).is_valid()
    assert VisitSlot.objects.count() == 2
    tour.session.refresh_from_db()
    book(tour)
    rejected = visits.save_slot(user, profile.school.slug, program.pk, slot_data(tour, capacity=1), tour.pk)
    assert rejected.errors
    visits.change_state(user, profile.school.slug, VisitBooking.objects.get().public_id, "canceled", "booked")
    rejected = visits.save_slot(user, profile.school.slug, program.pk, slot_data(tour, starts_at=later, ends_at=later+timedelta(minutes=30)), tour.pk)
    assert rejected.errors


def test_booked_tour_keeps_host_even_after_cancel_and_bridge_off_stops_sync(tour):
    tour.program.capture_leads = True
    tour.program.save()
    item = book(tour)
    new_user = get_user_model().objects.create_user("second-host")
    member = SchedulingMembership.objects.create(profile=tour.host.profile, user=new_user, role="scheduler")
    changed = visits.save_slot(tour.host.user, tour.host.profile.school.slug, tour.program_id,
        slot_data(tour, host=member.pk), tour.pk)
    assert "booking history" in str(changed.errors)
    tour.program.capture_leads = False
    tour.program.save()
    visits.change_state(tour.host.user, tour.host.profile.school.slug, item.public_id, "canceled", "booked")
    item.lead.refresh_from_db()
    assert item.lead.status == "trial_scheduled"


def test_room_conflicts_both_directions_and_history_blocks_feature_removal(tour):
    offering = Offering.objects.create(location=tour.program.offering.location, name="Workshop", is_published=True)
    conflict = Session(offering=offering, room=tour.session.room, capacity=2,
        starts_at=tour.session.starts_at, ends_at=tour.session.ends_at, is_published=True)
    with pytest.raises(ValidationError, match="already has a published"):
        conflict.full_clean()
    with pytest.raises(ValidationError, match="saved setup"):
        validate_removal(tour.host.profile, [])
    profile = tour.host.profile
    profile.capabilities = ["visits", "workshops", "reservations"]
    profile.save()
    assert not records(profile, "sessions").filter(pk=tour.session_id).exists()
    assert not records(profile, "workshops").filter(pk=tour.program.offering_id).exists()
    offering.delete()
    validate_removal(profile, ["visits"])


def test_visits_are_not_workshop_requests_or_payment_actions(tour, client):
    profile = tour.host.profile
    profile.capabilities = ["visits", "workshops", "reservations"]
    profile.save()
    item = book(tour)
    assert client.get(url(tour, "workshop_request", pk=tour.session_id)).status_code == 404
    assert b"Meet our preschool" not in client.get(url(tour, "public_schedule")).content
    client.force_login(tour.host.user)
    assert client.get(url(tour, "reservation_detail", public_id=item.reservation.public_id)).status_code == 404
    for call in [lambda: cancel_reservation(tour.host.user, profile.school.slug, item.reservation.public_id),
                 lambda: settle_offline(tour.host.user, profile.school.slug, item.reservation.public_id,
                     key="unused", kind="payment", amount_cents=100, reference="unused")]:
        with pytest.raises(Http404):
            call()


def test_public_browser_flow_and_receipt_isolation(tour, client):
    assert client.get(url(tour, "visit_catalogue")).status_code == 200
    path = url(tour, "visit_book", pk=tour.pk)
    first = client.get(path)
    assert first.status_code == 200
    data = booking_data(tour, booking_token=first.context["booking_token"])
    result = client.post(path, data)
    assert result.status_code == 302
    receipt = client.get(result.url)
    assert b"Your tour is confirmed" in receipt.content and "no-store" in receipt["Cache-Control"]
    assert Client().get(result.url).status_code == 404
    assert client.post(path, data).url == result.url
    assert client.get(url(tour, "visit_receipt", public_id=VisitBooking.objects.get().public_id)).status_code == 200
    data["contact_email"] = "invalid"
    assert client.post(path, data).status_code == 400
    fresh = client.get(path)
    assert client.post(path, booking_data(tour, contact_email="invalid", booking_token=fresh.context["booking_token"])).status_code == 400
    Offering.objects.filter(pk=tour.program.offering_id).update(is_published=False)
    assert client.get(path).status_code == 404
    assert b"No tour times" in client.get(url(tour, "visit_catalogue")).content


def test_staff_pages_roles_and_state_actions(tour, client):
    item = book(tour)
    detail = url(tour, "visit_detail", public_id=item.public_id)
    assert client.get(detail).status_code == 302
    client.force_login(tour.host.user)
    for page, kwargs in [("visits", {}), ("visit_program_new", {}), ("visit_program_edit", {"pk": tour.program_id}),
        ("visit_slot_new", {"program_pk": tour.program_id}), ("visit_slot_edit", {"program_pk": tour.program_id, "pk": tour.pk})]:
        assert client.get(url(tour, page, **kwargs)).status_code == 200
    assert b"Attendance opens" in client.get(detail).content
    state_url = url(tour, "visit_update", public_id=item.public_id)
    assert client.get(state_url).status_code == 405
    assert b"Choose an attendance" in client.post(state_url, {"state": "bad"}, follow=True).content
    assert b"Tour booking updated" in client.post(state_url, {"state": "canceled", "previous": "booked"}, follow=True).content
    tour.host.role = "viewer"
    tour.host.save()
    assert client.get(detail).status_code == 200
    assert client.post(state_url, {"state": "canceled", "previous": "booked"}).status_code == 404
    assert client.get(url(tour, "visit_program_new")).status_code == 404


def test_setup_view_posts_and_csrf(tour, client):
    client.force_login(tour.host.user)
    path = url(tour, "visit_program_new")
    assert client.post(path, {}).status_code == 400
    assert client.post(path, {"name": "New tour", "location": tour.program.offering.location_id}).status_code == 302
    path = url(tour, "visit_slot_new", program_pk=tour.program_id)
    assert client.post(path, {}).status_code == 400
    later = tour.session.starts_at + timedelta(days=1)
    assert client.post(path, {"room": tour.session.room_id, "capacity": 4, "host": tour.host_id,
        "starts_at": later.isoformat(), "ends_at": (later+timedelta(minutes=30)).isoformat(), "is_published": True}).status_code == 302
    csrf_client = Client(enforce_csrf_checks=True)
    assert csrf_client.post(url(tour, "visit_book", pk=tour.pk), booking_data(tour)).status_code == 403


def test_tenant_and_default_off_boundaries(tour, client):
    other = School.objects.create(slug="other-preschool", display_name="Other")
    profile = SchedulingProfile.objects.create(school=other, enabled=True, capabilities=["visits"])
    SchedulingMembership.objects.create(user=tour.host.user, profile=profile, role="owner")
    client.force_login(tour.host.user)
    for name, kwargs in [("visit_program_edit", {"pk": tour.program_id}), ("visit_book", {"pk": tour.pk}),
        ("visit_slot_new", {"program_pk": tour.program_id})]:
        assert client.get(reverse("scheduling:"+name, kwargs={"school_slug": other.slug, **kwargs})).status_code == 404
    profile = tour.host.profile
    for enabled, caps in [(True, []), (False, ["visits"])]:
        profile.enabled, profile.capabilities = enabled, caps
        profile.save()
        assert client.get(url(tour, "visits")).status_code == 404
        assert client.get(url(tour, "visit_catalogue")).status_code == 404
        with pytest.raises(Http404):
            book(tour)


def test_overview_includes_tours_and_db_source_constraint(tour):
    view = overview(tour.host.profile)
    assert len(view["agenda"]) == 1 and view["agenda"][0]["kind"] == "Tour"
    item = book(tour)
    with pytest.raises(IntegrityError), transaction.atomic():
        Reservation.objects.filter(pk=item.reservation.pk).update(visit_booking=None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_submission", [True, False])
def test_concurrent_last_places_and_duplicate_retry(tour, same_submission):
    Session.objects.filter(pk=tour.session_id).update(capacity=2)
    tour.session.refresh_from_db()
    first = booking_data(tour)
    second = first if same_submission else booking_data(tour, contact_email="second@example.invalid")
    barrier = Barrier(2)
    slug = tour.host.profile.school.slug

    def run(data):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return visits.book(slug, tour.pk, data, "browser-a")[0].pk
        except ValidationError:
            return None
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [first, second]))
    assert len({value for value in results if value}) == 1
    assert results.count(None) == (0 if same_submission else 1)
    assert VisitBooking.objects.count() == Reservation.objects.count() == PaymentOrder.objects.count() == 1
