from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School
from scheduling.capabilities import CAPABILITIES, has_capability, validate_capabilities, validate_removal
from scheduling.features import feature_form, save_features
from scheduling.models import Camp, LessonClosure, Location, Offering, SchedulingProfile, Session
from scheduling.setup import save_setup
from scheduling.workspace import overview
from scheduling.tests.test_lessons import music, book
from scheduling.tests.test_reservations import inventory, workshop_request, party_request, reserve, settle
from scheduling.tests.test_requests import workshop, begin, send

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator(db):
    return get_user_model().objects.create_superuser("pilot-operator", "operator@example.invalid", "admin")


def url(profile, name, **kwargs):
    return reverse("scheduling:" + name, kwargs={"school_slug": profile.school.slug, **kwargs})


def change(operator, profile, capabilities, **extra):
    profile.refresh_from_db()
    data = {"capabilities": capabilities, "edit_revision": feature_form(profile)["edit_revision"].value(), **extra}
    return save_features(operator, profile.school.slug, data)


@pytest.mark.parametrize("value", [None, "workshops", {}, [None], [True], ["unknown"], ["workshops", "workshops"], ["parties"], ["camps", "workshops"], ["reservations"]])
def test_invalid_grants_fail_closed(booking_profile, value):
    with pytest.raises(ValidationError):
        validate_capabilities(value)
    booking_profile.capabilities = value
    assert not has_capability(booking_profile, "workshops")


def test_new_profiles_default_empty_and_separate_from_enrollment(db):
    school = School.objects.create(slug="pilot-new", plan="custom", feature_flags={"broadcast_enabled": True})
    profile = SchedulingProfile.objects.create(school=school)
    assert profile.capabilities == [] and not profile.enabled
    profile.full_clean()
    assert school.feature_flags == {"broadcast_enabled": True}


def test_operator_saves_ordered_grants_with_audit_and_resets_latent_switches(operator, booking_profile):
    booking_profile.lessons_enabled = booking_profile.native_reservations_enabled = True
    booking_profile.save()
    form = change(operator, booking_profile, ["parties", "workshops"])
    assert form.is_valid(), form.errors
    booking_profile.refresh_from_db()
    assert booking_profile.capabilities == ["workshops", "parties"]
    assert not booking_profile.lessons_enabled and not booking_profile.native_reservations_enabled
    log = AdminAuditLog.objects.get(actor=operator)
    assert log.changes["capabilities"]["to"] == ["workshops", "parties"]
    assert change(operator, booking_profile, ["lessons"]).is_valid()


@pytest.mark.parametrize("role", ["owner", "scheduler", "viewer"])
def test_business_cannot_grant_features(client, booking_member, role):
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    path = url(booking_member.profile, "features")
    assert client.get(path).status_code == 404
    assert client.post(path, {"capabilities": ["lessons"]}).status_code == 404
    with pytest.raises(Http404):
        save_features(booking_member.user, booking_member.profile.school.slug, {})


def test_operator_view_csrf_invalid_stale_and_cross_workspace_token(operator, booking_profile):
    client = Client(enforce_csrf_checks=True)
    client.force_login(operator)
    path = url(booking_profile, "features")
    page = client.get(path)
    token = page.context["form"]["edit_revision"].value()
    assert client.post(path, {"capabilities": []}).status_code == 403
    csrf = client.cookies["csrftoken"].value
    data = {"csrfmiddlewaretoken": csrf, "edit_revision": token, "capabilities": ["camps"]}
    assert client.post(path, data).status_code == 400
    data["capabilities"] = ["lessons"]
    assert client.post(path, data).status_code == 302
    data["capabilities"] = ["workshops"]
    assert client.post(path, data).status_code == 400
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="second-pilot"), enabled=True)
    assert client.post(url(other, "features"), data).status_code == 400
    booking_profile.enabled = False
    booking_profile.save(update_fields=["enabled"])
    assert client.get(path).status_code == 404
    operator.is_active = False
    with pytest.raises(Http404):
        save_features(operator, other.school.slug, {})


def test_feature_write_rolls_back_if_audit_fails(operator, booking_profile, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(AdminAuditLog.objects, "create", fail)
    with pytest.raises(RuntimeError):
        change(operator, booking_profile, ["lessons"])
    booking_profile.refresh_from_db()
    assert booking_profile.capabilities == list(CAPABILITIES)


@pytest.mark.parametrize("feature", ["workshops", "parties", "reservations", "camps"])
def test_history_prevents_stranding_features(operator, inventory, booking_member, feature):
    profile, offering, _, _ = inventory
    if feature == "parties":
        party_request(inventory)
    elif feature == "reservations":
        reserve(booking_member, workshop_request(inventory))
    elif feature == "camps":
        Camp.objects.create(offering=offering, name="Saved camp", price=0)
    with pytest.raises(ValidationError, match="saved setup or records"):
        validate_removal(profile, [key for key in CAPABILITIES if key != feature])


def test_lesson_history_and_party_intake_preserved(music):
    profile = music["profile"]
    with pytest.raises(ValidationError):
        validate_removal(profile, ["workshops", "parties", "reservations", "camps"])
    offering = Offering.objects.create(location=music["location"], name="Workshop")
    offering.accepts_party_requests = True
    offering.save()
    with pytest.raises(ValidationError):
        validate_removal(profile, ["workshops", "reservations", "camps", "lessons"])


def test_closure_alone_keeps_lesson_feature(booking_profile):
    location = Location.objects.create(profile=booking_profile, name="Holiday location")
    LessonClosure.objects.create(location=location, first_date="2030-01-01", last_date="2030-01-01", reason="Closed")
    with pytest.raises(ValidationError):
        validate_removal(booking_profile, [])


@pytest.mark.parametrize("name", ["workshops", "workshop_new", "requests", "parties", "camps", "reservations", "reservation_settings", "lesson_settings", "lessons", "public_schedule", "lesson_trials"])
def test_absent_feature_cannot_be_opened_or_posted(client, booking_member, name, settings):
    settings.BOOKINGS_LESSON_BOOKING_PREVIEW = True
    settings.DEBUG = True
    profile = booking_member.profile
    profile.capabilities = []
    profile.lessons_enabled = profile.native_reservations_enabled = True
    profile.save()
    client.force_login(booking_member.user)
    for method in (client.get, client.post):
        assert method(url(profile, name), {}).status_code in {404, 405}
    response = client.get(url(profile, "workspace"))
    assert response.status_code == 200
    assert b"not been activated" in response.content
    assert b">Workshops</a>" not in response.content
    assert b">Reservations</a>" not in response.content


def test_standalone_music_nav_and_workshop_nav(client, operator, booking_member):
    profile = booking_member.profile
    assert change(operator, profile, ["lessons"]).is_valid()
    profile.refresh_from_db()
    profile.lessons_enabled = True
    profile.save()
    client.force_login(booking_member.user)
    page = client.get(url(profile, "workspace"))
    section = next(item for item in page.context["booking_navigation"]["sections"] if item["label"] == "Reservations")
    assert section["url"] == url(profile, "lessons")
    assert [tab["label"] for tab in section["tabs"]] == ["Lessons"]
    assert b">Lessons</a>" in client.get(section["url"]).content
    assert b">Workshop</a>" not in page.content
    assert b"Add teacher availability" in page.content
    assert change(operator, profile, ["workshops"]).is_valid()
    page = client.get(url(profile, "workspace"))
    section = next(item for item in page.context["booking_navigation"]["sections"] if item["label"] == "Setup")
    assert section["url"] == url(profile, "workshops")
    assert [tab["label"] for tab in section["tabs"]] == ["Workshop", "Catalog", "Materials Inventory"]
    assert b">Workshop</a>" in client.get(section["url"]).content
    assert b">Lessons</a>" not in page.content
    assert b"Lesson scheduling settings" not in page.content
    assert b"Manage available features" not in page.content


def test_stale_public_form_and_direct_service_block_after_feature_loss(client, workshop, booking_member):
    data = begin(client, workshop)
    profile = booking_member.profile
    # Simulate a stale/damaged grant outside the supported operator workflow.
    profile.capabilities = []
    profile.save()
    assert send(client, workshop, data).status_code == 404
    with pytest.raises(Http404):
        save_setup(booking_member.user, profile.school.slug, "workshops", {"name": "Forged"})


def test_optional_party_and_camp_controls_hidden_and_not_postable(client, inventory, booking_member):
    profile, offering, _, _ = inventory
    profile.capabilities = ["workshops"]
    profile.native_reservations_enabled = False
    profile.save()
    client.force_login(booking_member.user)
    page = client.get(url(profile, "workshop_detail", pk=offering.pk))
    assert b"Create camp" not in page.content and b"Party inquiries:" not in page.content
    edit = client.get(url(profile, "workshop_edit", pk=offering.pk))
    assert "accepts_party_requests" not in edit.context["form"].fields
    response = client.post(url(profile, "workshop_edit", pk=offering.pk), {
        "name": offering.name, "location": offering.location_id, "accepts_party_requests": "on",
        "edit_revision": edit.context["form"]["edit_revision"].value(),
    })
    assert response.status_code == 302
    offering.refresh_from_db()
    assert not offering.accepts_party_requests
    assert client.get(url(profile, "party_inquiry", pk=offering.pk)).status_code == 404
    assert client.get(url(profile, "camp_new", offering_pk=offering.pk)).status_code == 404


def test_workbench_scoping_counts_agenda_and_paused_intake(inventory, booking_member):
    profile, offering, room, session = inventory
    request = workshop_request(inventory)
    assert overview(profile)["attention_items"][0]["count"] == 1
    item = reserve(booking_member, request)
    settle(booking_member, item)
    assert overview(profile)["attention_items"][0]["count"] == 0
    party = party_request(inventory)
    reserved_party = reserve(booking_member, party)
    settle(booking_member, reserved_party, 10000)
    profile.native_reservations_enabled = False
    profile.save()
    data = overview(profile)
    assert [row["kind"] for row in data["agenda"]] == ["Published session", "Confirmed party"]
    assert all(row["room"] == room.name for row in data["agenda"])
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="other-home"), enabled=True, capabilities=list(CAPABILITIES))
    assert overview(other)["agenda"] == []
    assert all(row["count"] == 0 for row in overview(other)["attention_items"])
    reserved_party.state = "review"
    reserved_party.save()
    data = overview(profile)
    assert data["attention_items"][2]["count"] == 1 and len(data["agenda"]) == 1


def test_agenda_boundaries_limit_and_drafts(inventory):
    profile, offering, _, first = inventory
    first.is_published = False
    first.save()
    now = timezone.now()
    for i in range(23):
        start = now + timedelta(hours=i)
        Session.objects.create(offering=offering, starts_at=start, ends_at=start+timedelta(minutes=30), capacity=4, is_published=True)
    Session.objects.create(offering=offering, starts_at=now-timedelta(days=1), ends_at=now-timedelta(hours=1), capacity=4, is_published=True)
    Session.objects.create(offering=offering, starts_at=now+timedelta(days=8), ends_at=now+timedelta(days=8,hours=1), capacity=4, is_published=True)
    data = overview(profile)
    assert len(data["agenda"]) == 20 and data["agenda_truncated"]
    assert all(row["room"] == "No room assigned" for row in data["agenda"])
    assert data["agenda"] == sorted(data["agenda"], key=lambda row: row["start"])


def test_lesson_agenda_and_viewer_read_only(client, music):
    series = book(music)
    profile = music["profile"]
    profile.capabilities = ["lessons"]
    profile.save()
    data = overview(profile)
    assert len(data["agenda"]) == 1
    assert data["agenda"][0]["label"] == "Riley - Piano"
    assert data["agenda"][0]["zone"] == "America/Los_Angeles"
    occurrence = series.occurrences.first()
    occurrence.state = "canceled"
    occurrence.save()
    assert not overview(profile)["agenda"]
    music["member"].role = "viewer"
    music["member"].save()
    client.force_login(music["user"])
    page = client.get(url(profile, "workspace"))
    assert b"Setup checklist" not in page.content


def test_keep_native_switch_when_capability_stays(operator, booking_profile):
    booking_profile.native_reservations_enabled = True
    booking_profile.save()
    assert change(operator, booking_profile, ["workshops", "reservations"]).is_valid()
    booking_profile.refresh_from_db()
    assert booking_profile.native_reservations_enabled


@pytest.mark.parametrize("kind", ["workshop", "party"])
def test_attention_links_show_only_pending_unreserved_items(client, inventory, booking_member, kind):
    source = workshop_request if kind == "workshop" else party_request
    pending = source(inventory)
    pending.status = "pending"
    pending.save()
    booked = source(inventory)
    reserve(booking_member, booked)
    closed = source(inventory)
    closed.status = "withdrawn" if kind == "workshop" else "declined"
    closed.save()
    name = "requests" if kind == "workshop" else "parties"
    client.force_login(booking_member.user)
    page = client.get(url(booking_member.profile, name) + "?review=1")
    assert page.status_code == 200
    assert list(page.context["items"].object_list) == [pending]
    assert page.context["review_only"]
    assert len(client.get(url(booking_member.profile, name)).context["items"].object_list) == 3


def test_workbench_queries_bounded(inventory, django_assert_max_num_queries):
    with django_assert_max_num_queries(15):
        overview(inventory[0])


@pytest.mark.django_db(transaction=True)
def test_feature_removal_serializes_with_new_setup(operator, booking_member):
    profile = booking_member.profile
    location = Location.objects.create(profile=profile, name="Pilot setup race")
    token = feature_form(profile)["edit_revision"].value()
    barrier = Barrier(2)
    slug, user_id, operator_id, location_id = profile.school.slug, booking_member.user.pk, operator.pk, location.pk
    def attempt(removal):
        close_old_connections()
        try:
            user = get_user_model().objects.get(pk=operator_id if removal else user_id)
            barrier.wait(timeout=10)
            if removal:
                return "removed" if save_features(user, slug, {"capabilities": [], "edit_revision": token}).is_valid() else "retained"
            try:
                form = save_setup(user, slug, "workshops", {"name": "Concurrent workshop", "location": location_id})
                assert form.is_valid(), form.errors
                return "created"
            except Http404:
                return "denied"
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, removal) for removal in (True, False)]
        outcomes = {f.result(timeout=15) for f in futures}
    assert outcomes in ({"removed", "denied"}, {"retained", "created"})


@pytest.mark.django_db(transaction=True)
def test_parallel_operator_saves_detect_stale_state(operator, booking_profile):
    token = feature_form(booking_profile)["edit_revision"].value()
    barrier = Barrier(2)
    slug, user_id = booking_profile.school.slug, operator.pk
    def attempt(grants):
        close_old_connections()
        try:
            user = get_user_model().objects.get(pk=user_id)
            barrier.wait(timeout=10)
            return save_features(user, slug, {"capabilities": grants, "edit_revision": token}).is_valid()
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, [grant]) for grant in ("lessons", "workshops")]
        assert sorted(f.result(timeout=15) for f in futures) == [False, True]


@pytest.mark.django_db(transaction=True)
def test_capability_migration_preserves_old_access_but_defaults_new_off():
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    old = [("scheduling", "0013_booking_message_outbox")]
    try:
        executor.migrate(old)
        apps = executor.loader.project_state(old).apps
        school = apps.get_model("core", "School").objects.create(slug="legacy-capability", plan="custom")
        profile = apps.get_model("scheduling", "SchedulingProfile").objects.create(school=school, enabled=False, lessons_enabled=False)
        MigrationExecutor(connection).migrate(latest)
        saved = SchedulingProfile.objects.get(pk=profile.pk)
        assert saved.capabilities == ["workshops", "parties", "reservations", "camps", "lessons"]
        assert not has_capability(saved, "integrations")
        assert not saved.enabled and not saved.lessons_enabled
        assert School.objects.get(pk=school.pk).plan == "custom"
        fresh = SchedulingProfile.objects.create(school=School.objects.create(slug="fresh-capability"))
        assert fresh.capabilities == []
    finally:
        MigrationExecutor(connection).migrate(latest)
