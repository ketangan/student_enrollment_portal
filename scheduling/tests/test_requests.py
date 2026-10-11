from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import uuid

import pytest
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.http import Http404, QueryDict
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School, Submission
from scheduling.models import (ChoiceGroup, ChoiceOption, Location, Offering, RequestParticipant,
                               Room, SchedulingProfile, Session, WorkshopRequest, validate_choice_image)
from scheduling.requests import (TOKEN_SALT, catalog_for, choice_form, preparation_totals, request_token,
                                 save_choice, submit_request, withdraw_request)
from scheduling.setup import save_setup

pytestmark = pytest.mark.django_db


@pytest.fixture
def workshop(booking_member):
    location = Location.objects.create(profile=booking_member.profile, name="Main")
    room = Room.objects.create(location=location, name="Studio", capacity=12)
    offering = Offering.objects.create(location=location, name="Workshop", is_published=True, accepts_requests=True)
    start = timezone.now() + timedelta(days=5)
    session = Session.objects.create(offering=offering, room=room, starts_at=start, ends_at=start+timedelta(hours=1), capacity=10, is_published=True)
    design = ChoiceGroup.objects.create(offering=offering, name="Design", reference_image="/static/demo/plushumi/assets/designs.jpg")
    color = ChoiceGroup.objects.create(offering=offering, name="Color")
    ChoiceOption.objects.create(group=design, label="Cat")
    ChoiceOption.objects.create(group=design, label="Bunny", image_url="https://example.com/bunny.jpg")
    ChoiceOption.objects.create(group=color, label="Blue", swatch="#80B8DB")
    ChoiceOption.objects.create(group=color, label="Pink", swatch="#F0B2CA")
    return session


def route(name, session, **kwargs):
    return reverse("scheduling:"+name, kwargs={"school_slug": session.offering.location.profile.school.slug, **kwargs})


def payload(session, token, count=2):
    data = QueryDict(mutable=True)
    data.update({"request_token": token, "purchaser_name": "Alex Parent", "purchaser_email": "alex@example.com",
                 "participants-TOTAL_FORMS": str(count), "participants-INITIAL_FORMS": "0",
                 "participants-MIN_NUM_FORMS": "1", "participants-MAX_NUM_FORMS": "20"})
    for i in range(count):
        data[f"participants-{i}-name"] = f"Participant {i+1}"
        for group in catalog_for(session.offering):
            data[f"participants-{i}-choice_{group.pk}"] = str(group.active_options[i % len(group.active_options)].pk)
    return data


def begin(client, session, count=2):
    page = client.get(route("workshop_request", session, pk=session.pk))
    assert page.status_code == 200
    return payload(session, page.context["request_token"], count)


def send(client, session, data):
    return client.post(route("workshop_request", session, pk=session.pk), data)


def test_complete_customer_to_preparation_flow(client, booking_member, workshop):
    before_submissions = Submission.objects.count()
    data = begin(client, workshop)
    response = send(client, workshop, data)
    assert response.status_code == 302
    item = WorkshopRequest.objects.get()
    assert item.status == "pending" and item.purchaser_email == "alex@example.com"
    assert str(item) == str(item.public_id)
    assert item.participants.count() == 2
    assert item.session_snapshot["room"] == "Studio"
    assert item.requested_start == workshop.starts_at and item.requested_end == workshop.ends_at
    participants = list(item.participants.all())
    assert [p.name for p in participants] == ["Participant 1", "Participant 2"]
    assert participants[0].selections[0]["option"] == "Cat"
    assert participants[1].selections[1]["option"] == "Pink"
    receipt = client.get(response.url)
    assert b"Request received" in receipt.content and b"No place has been reserved" in receipt.content
    assert b"alex@example.com" not in receipt.content and b"Participant 1" not in receipt.content
    assert "no-store" in receipt["Cache-Control"]
    assert Client().get(response.url).status_code == 404
    outsider = Client()
    begin(outsider, workshop)
    assert outsider.get(response.url).status_code == 404
    staff = Client()
    staff.force_login(booking_member.user)
    listing = staff.get(route("requests", workshop))
    assert b"Alex Parent" in listing.content and b"Pending review" in listing.content
    detail = staff.get(route("request_detail", workshop, public_id=item.public_id))
    assert b"Participant 2" in detail.content and b"Bunny" in detail.content
    roster = staff.get(route("preparation", workshop, pk=workshop.pk))
    assert roster.context["participant_total"] == 2
    assert {(r["group"], r["option"], r["count"]) for r in roster.context["totals"]} == {
        ("Design", "Cat", 1), ("Design", "Bunny", 1), ("Color", "Blue", 1), ("Color", "Pink", 1)}
    assert Submission.objects.count() == before_submissions
    assert AdminAuditLog.objects.filter(model_label="scheduling.workshoprequest", action="add").count() == 1


def test_same_submission_replay_is_one_request_even_after_closing(client, workshop):
    data = begin(client, workshop)
    first = send(client, workshop, data)
    workshop.offering.accepts_requests = False
    workshop.offering.save()
    data["csrfmiddlewaretoken"] = "rotated-token"
    replay = send(client, workshop, data)
    assert replay.status_code == 302 and replay.url == first.url
    assert WorkshopRequest.objects.count() == 1 and RequestParticipant.objects.count() == 2
    data["purchaser_name"] = "Someone else"
    changed = send(client, workshop, data)
    assert changed.status_code == 400 and b"already submitted" in changed.content
    assert changed.context["request_token"] == data["request_token"]
    assert WorkshopRequest.objects.count() == 1


@pytest.mark.parametrize("field,value", [
    ("purchaser_name", ""), ("purchaser_name", "x"*101), ("purchaser_email", "not-email"),
    ("participants-0-name", ""), ("participants-0-name", "x"*101),
    ("participants-TOTAL_FORMS", "0"), ("participants-TOTAL_FORMS", "21"),
    ("participants-TOTAL_FORMS", "100000"), ("participants-TOTAL_FORMS", "invalid"),
])
def test_invalid_form_does_not_write_and_keeps_token(client, workshop, field, value):
    data = begin(client, workshop)
    data[field] = value
    response = send(client, workshop, data)
    assert response.status_code == 400
    assert not WorkshopRequest.objects.exists()
    assert response.context["request_token"] == data["request_token"]
    assert not AdminAuditLog.objects.exists()


def test_choices_required_and_foreign_ids_rejected(client, workshop):
    data = begin(client, workshop)
    group = workshop.offering.choice_groups.first()
    del data[f"participants-0-choice_{group.pk}"]
    assert send(client, workshop, data).status_code == 400
    other_group = ChoiceGroup.objects.create(offering=workshop.offering, name="Hidden", is_active=False)
    foreign = ChoiceOption.objects.create(group=other_group, label="Not allowed")
    data[f"participants-0-choice_{group.pk}"] = str(foreign.pk)
    assert send(client, workshop, data).status_code == 400
    assert not WorkshopRequest.objects.exists()


@pytest.mark.parametrize("change", ["profile", "offering", "session", "closed", "past", "empty-choice"])
def test_no_requests_for_unavailable_sessions(client, workshop, change):
    data = begin(client, workshop)
    if change == "profile":
        SchedulingProfile.objects.filter(pk=workshop.offering.location.profile_id).update(enabled=False)
    elif change == "offering":
        Offering.objects.filter(pk=workshop.offering_id).update(is_published=False)
    elif change == "session":
        Session.objects.filter(pk=workshop.pk).update(is_published=False)
    elif change == "closed":
        Offering.objects.filter(pk=workshop.offering_id).update(accepts_requests=False)
    elif change == "past":
        Session.objects.filter(pk=workshop.pk).update(starts_at=timezone.now()-timedelta(hours=2), ends_at=timezone.now()-timedelta(hours=1))
    else:
        ChoiceGroup.objects.create(offering=workshop.offering, name="Empty")
    assert client.get(route("workshop_request", workshop, pk=workshop.pk)).status_code == 404
    assert send(client, workshop, data).status_code in {400, 404}
    assert not WorkshopRequest.objects.exists()


def test_stale_options_and_time_require_fresh_form(client, workshop):
    data = begin(client, workshop)
    choice = ChoiceOption.objects.first()
    choice.label = "New name"
    choice.save()
    response = send(client, workshop, data)
    assert response.status_code == 400 and b"schedule or choices changed" in response.content
    assert response.context["request_token"] == data["request_token"]
    assert not WorkshopRequest.objects.exists()
    data = begin(client, workshop)
    Session.objects.filter(pk=workshop.pk).update(capacity=8)
    assert send(client, workshop, data).status_code == 400


def test_unsigned_expired_and_wrong_browser_tokens(client, workshop, monkeypatch):
    data = begin(client, workshop)
    original = data["request_token"]
    data["request_token"] = original + "corrupted"
    assert send(client, workshop, data).status_code == 400
    data["request_token"] = original
    other = Client()
    begin(other, workshop)
    assert send(other, workshop, data).status_code == 400
    assert send(Client(), workshop, data).status_code == 400
    with monkeypatch.context() as m:
        now = signing.time.time()
        m.setattr(signing.time, "time", lambda: now + 7201)
        assert send(client, workshop, data).status_code == 400
    wrong = signing.loads(original, salt=TOKEN_SALT)
    wrong["session"] += 100
    data["request_token"] = signing.dumps(wrong, salt=TOKEN_SALT)
    assert send(client, workshop, data).status_code == 400
    data["request_token"] = signing.dumps({}, salt=TOKEN_SALT)
    assert send(client, workshop, data).status_code == 400
    data["request_token"] = signing.dumps({"key": 123}, salt=TOKEN_SALT)
    assert send(client, workshop, data).status_code == 400
    assert not WorkshopRequest.objects.exists()


def test_saved_selections_survive_config_changes_and_withdrawal_updates_totals(client, booking_member, workshop):
    data = begin(client, workshop, 3)
    send(client, workshop, data)
    item = WorkshopRequest.objects.get()
    ChoiceOption.objects.filter(label="Cat").update(label="Renamed", is_active=False)
    ChoiceGroup.objects.filter(name="Color").update(name="Renamed color", is_active=False)
    total, totals = preparation_totals([item])
    assert total == 3
    assert {r["option"]: r["count"] for r in totals} == {"Cat": 2, "Bunny": 1, "Blue": 2, "Pink": 1}
    client.force_login(booking_member.user)
    path = route("request_withdraw", workshop, public_id=item.public_id)
    assert client.get(path).status_code == 405
    assert client.post(path).status_code == 400
    response = client.post(path, {"confirm": "yes"}, follow=True)
    assert response.status_code == 200 and b"Withdrawn" in response.content
    item.refresh_from_db()
    assert preparation_totals([item]) == (0, [])
    client.post(path, {"confirm": "yes"})
    assert AdminAuditLog.objects.filter(model_label="scheduling.workshoprequest", action="change").count() == 1
    assert RequestParticipant.objects.count() == 3


def test_request_with_no_choices_or_room_and_over_capacity_roster(client, booking_member, workshop):
    workshop.offering.choice_groups.update(is_active=False)
    workshop.room = None
    workshop.capacity = 2
    workshop.save()
    for _ in range(2):
        assert send(client, workshop, begin(client, workshop, 2)).status_code == 302
    assert WorkshopRequest.objects.first().session_snapshot["room"] == ""
    assert RequestParticipant.objects.first().selections == []
    client.force_login(booking_member.user)
    response = client.get(route("preparation", workshop, pk=workshop.pk))
    assert response.context["participant_total"] == 4 and b"exceed" in response.content
    assert response.context["totals"] == []


def test_pending_requests_protect_session_times_room_and_group_size(client, booking_member, workshop):
    send(client, workshop, begin(client, workshop, 2))
    workshop.starts_at += timedelta(minutes=5)
    with pytest.raises(ValidationError, match="Withdraw pending"):
        workshop.full_clean()
    workshop.refresh_from_db()
    workshop.capacity = 1
    with pytest.raises(ValidationError, match="group size"):
        workshop.full_clean()
    withdraw_request(booking_member.user, booking_member.profile.school.slug, WorkshopRequest.objects.get().public_id)
    workshop.starts_at += timedelta(minutes=5)
    workshop.full_clean()


@pytest.mark.parametrize("role,can_edit", [("owner", True), ("scheduler", False), ("viewer", False)])
def test_choice_setup_role_guards_and_staff_request_permissions(client, booking_member, workshop, role, can_edit):
    send(client, workshop, begin(client, workshop))
    item = WorkshopRequest.objects.get()
    group = workshop.offering.choice_groups.first()
    option = group.options.first()
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    for name, kwargs in [("choice_new", {}), ("choice_edit", {"group_pk": group.pk}), ("option_new", {"group_pk": group.pk}), ("option_edit", {"group_pk": group.pk, "option_pk": option.pk})]:
        path = route(name, workshop, offering_pk=workshop.offering_id, **kwargs)
        assert client.get(path).status_code == (200 if can_edit else 404)
        assert client.post(path, {}).status_code == (200 if can_edit else 404)
    assert client.get(route("preparation", workshop, pk=workshop.pk)).status_code == 200
    assert client.get(route("request_detail", workshop, public_id=item.public_id)).status_code == 200
    assert client.post(route("request_withdraw", workshop, public_id=item.public_id), {"confirm": "yes"}).status_code == (404 if role == "viewer" else 302)


def test_choice_configuration_is_generic_saved_and_unique(client, booking_member, workshop):
    client.force_login(booking_member.user)
    path = route("choice_new", workshop, offering_pk=workshop.offering_id)
    response = client.post(path, {"name": "Instrument", "position": 3, "is_active": "on", "reference_image": "https://example.com/instruments.jpg"})
    assert response.status_code == 302
    group = ChoiceGroup.objects.get(name="Instrument")
    assert str(group) == "Instrument"
    path = route("option_new", workshop, offering_pk=workshop.offering_id, group_pk=group.pk)
    assert client.post(path, {"label": "Piano", "position": 0, "is_active": "on", "swatch": "#ABCDEF"}).status_code == 302
    option = group.options.get()
    assert str(option) == "Piano"
    assert client.post(path, {"label": "PIANO", "position": 0, "is_active": "on"}).status_code == 200
    assert group.options.count() == 1
    assert client.post(route("choice_new", workshop, offering_pk=workshop.offering_id), {"name": "INSTRUMENT", "position": 0}).status_code == 200
    edit_url = route("option_edit", workshop, offering_pk=workshop.offering_id, group_pk=group.pk, option_pk=option.pk)
    form = client.get(edit_url).context["form"]
    edited = client.post(edit_url, {"label": "Keyboard", "position": 0, "edit_revision": form["edit_revision"].value()})
    assert edited.status_code == 302
    option.refresh_from_db()
    assert option.label == "Keyboard" and not option.is_active
    assert AdminAuditLog.objects.filter(model_label__startswith="scheduling.choice").count() == 3


@pytest.mark.parametrize("value,valid", [
    ("/static/demo/plushumi/assets/designs.jpg", True), ("https://example.com/photo", True),
    ("javascript:alert(1)", False), ("data:image/png;base64,abc", False), ("http://example.com/image.jpg", False),
    ("/static/../private.jpg", False), ("/static/x.svg", False), ("/static/x.png?evil=1", False),
    ("/static/%2e%2e/private.jpg", False), ("//example.com/photo.jpg", False),
])
def test_choice_images_are_restricted_without_fetching(value, valid):
    if valid:
        validate_choice_image(value)
    else:
        with pytest.raises(ValidationError):
            validate_choice_image(value)


def test_invalid_swatch_and_images_never_saved(booking_member, workshop):
    group = workshop.offering.choice_groups.first()
    for changes in [{"swatch": "red;position:fixed"}, {"image_url": "javascript:evil()"}, {"position": -1}]:
        _, form = save_choice(booking_member.user, booking_member.profile.school.slug, workshop.offering_id,
            {"label": "Bad", "position": 0, **changes}, group_pk=group.pk, option=True)
        assert not form.is_valid()
    assert not ChoiceOption.objects.filter(label="Bad").exists()


def test_cross_tenant_ids_and_public_data_remain_private(client, booking_member, workshop):
    send(client, workshop, begin(client, workshop))
    item = WorkshopRequest.objects.get()
    other_profile = SchedulingProfile.objects.create(school=School.objects.create(slug="other-studio"), enabled=True)
    other_location = Location.objects.create(profile=other_profile, name="Private")
    other_offering = Offering.objects.create(location=other_location, name="Private")
    other_group = ChoiceGroup.objects.create(offering=other_offering, name="Secret")
    other_option = ChoiceOption.objects.create(group=other_group, label="Secret")
    with pytest.raises(Http404):
        choice_form(booking_member.profile, other_offering.pk)
    with pytest.raises(Http404):
        choice_form(booking_member.profile, workshop.offering_id, group_pk=other_group.pk)
    with pytest.raises(Http404):
        choice_form(booking_member.profile, workshop.offering_id, group_pk=workshop.offering.choice_groups.first().pk, option_pk=other_option.pk, option=True)
    with pytest.raises(Http404):
        choice_form(booking_member.profile, workshop.offering_id, option=True)
    client.force_login(booking_member.user)
    for suffix in ["requests/", f"requests/{item.public_id}/", f"sessions/{workshop.pk}/preparation/"]:
        assert client.get(f"/bookings/other-studio/{suffix}").status_code == 404
    client.logout()
    for name, kwargs in [("requests", {}), ("request_detail", {"public_id": item.public_id}), ("preparation", {"pk": workshop.pk})]:
        assert client.get(route(name, workshop, **kwargs)).status_code == 302
    assert Client().get(f"/bookings/other-studio/received/{item.public_id}/").status_code == 404
    public = client.get(route("public_schedule", workshop))
    assert b"Alex Parent" not in public.content and b"alex@example.com" not in public.content


@pytest.mark.usefixtures("rate_limit_clock")
def test_csrf_rate_limits_and_empty_request_list(booking_member, workshop):
    client = Client(enforce_csrf_checks=True)
    data = begin(client, workshop)
    assert send(client, workshop, data).status_code == 403
    client.force_login(booking_member.user)
    assert client.post(route("choice_new", workshop, offering_pk=workshop.offering_id), {}).status_code == 403
    assert b"No workshop requests yet" in client.get(route("requests", workshop)).content
    assert b"No pending participants" in client.get(route("preparation", workshop, pk=workshop.pk)).content
    cache.clear()
    with override_settings(RATELIMIT_ENABLE=True):
        public = Client()
        data = begin(public, workshop)
        for _ in range(10):
            assert send(public, workshop, data).status_code == 302
        assert send(public, workshop, data).status_code == 403
    cache.clear()


def test_audit_failure_rolls_back_request_and_participants(client, workshop, monkeypatch):
    data = begin(client, workshop)
    def fail(**kwargs):
        raise RuntimeError("Audit unavailable")
    monkeypatch.setattr(AdminAuditLog.objects, "create", fail)
    with pytest.raises(RuntimeError):
        send(client, workshop, data)
    assert not WorkshopRequest.objects.exists() and not RequestParticipant.objects.exists()


def test_pagination_and_snapshot_times_survive_session_edits(client, booking_member, workshop):
    send(client, workshop, begin(client, workshop))
    item = WorkshopRequest.objects.get()
    original = item.requested_start
    withdraw_request(booking_member.user, booking_member.profile.school.slug, item.public_id)
    workshop.starts_at += timedelta(days=1)
    workshop.ends_at += timedelta(days=1)
    workshop.full_clean()
    workshop.save()
    item.refresh_from_db()
    assert item.requested_start == original
    copies = []
    for i in range(50):
        copies.append(WorkshopRequest(session=workshop, purchaser_name=f"Request {i}", purchaser_email="sample@example.com",
            submission_key=uuid.uuid4(), submission_digest="a"*64, browser_digest="b"*64, session_snapshot=item.session_snapshot))
    WorkshopRequest.objects.bulk_create(copies)
    client.force_login(booking_member.user)
    first = client.get(route("requests", workshop))
    assert len(first.context["items"]) == 50 and b'aria-label="Next page"' in first.content
    second = client.get(route("requests", workshop)+"?page=2")
    assert len(second.context["items"]) == 1 and b'aria-label="Previous page"' in second.content
    detail = client.get(route("request_detail", workshop, public_id=item.public_id))
    assert b"Alex Parent" in detail.content


def test_inactive_options_hidden_and_user_strings_escaped(client, workshop):
    hidden = ChoiceOption.objects.create(group=workshop.offering.choice_groups.first(), label="Secret option", is_active=False)
    page = client.get(route("workshop_request", workshop, pk=workshop.pk))
    assert hidden.label.encode() not in page.content
    data = payload(workshop, page.context["request_token"])
    data["participants-0-name"] = '<script>unsafe()</script>'
    assert send(client, workshop, data).status_code == 302
    member = workshop.offering.location.profile.memberships.get()
    client.force_login(member.user)
    roster = client.get(route("preparation", workshop, pk=workshop.pk))
    assert b"<script>unsafe" not in roster.content and b"&lt;script&gt;unsafe" in roster.content


def test_database_status_and_submission_uniqueness(client, workshop):
    send(client, workshop, begin(client, workshop))
    item = WorkshopRequest.objects.get()
    with pytest.raises(IntegrityError), transaction.atomic():
        WorkshopRequest.objects.filter(pk=item.pk).update(status="confirmed")
    with pytest.raises(IntegrityError), transaction.atomic():
        RequestParticipant.objects.create(request=item, name="Duplicate", position=0)
    item.pk = None
    item.public_id = uuid.uuid4()
    with pytest.raises(IntegrityError), transaction.atomic():
        item.save()


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_submission_creates_one_request(workshop):
    catalog = catalog_for(workshop.offering)
    browser = "b"*64
    data = payload(workshop, request_token(workshop, catalog, browser))
    barrier = Barrier(2)
    slug, pk = workshop.offering.location.profile.school.slug, workshop.pk
    def attempt():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return submit_request(slug, pk, data, browser)[0].pk
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        ids = [future.result(timeout=15) for future in futures]
    assert ids[0] == ids[1]
    assert WorkshopRequest.objects.count() == 1 and RequestParticipant.objects.count() == 2
