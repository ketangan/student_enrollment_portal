"""Hosted demo opt-in must never weaken existing tenant or staff boundaries."""
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django_ratelimit import core as ratelimit_core

from core.models import School
from scheduling.experience_demo import config
from scheduling.models import SchedulingProfile
from .test_experience_demo import SLUG, browser, demo, form_data, local, url

pytestmark = pytest.mark.django_db
HOST = "demo.mypontora.com"
ROOT = "/demo/plushumi/integrations/"


@pytest.fixture
def hosted(monkeypatch, settings):
    settings.ALLOWED_HOSTS = [HOST, "app.mypontora.com", "localhost"]
    monkeypatch.setattr(config, "settings", SimpleNamespace(DEBUG=False, BASE_DIR=settings.BASE_DIR,
        DATABASES={"default": {"NAME": "shared-production", "HOST": "private-db"}},
        BOOKING_HOSTED_DEMO_SLUGS=[SLUG], BOOKING_HOSTED_DEMO_HOSTS=[HOST]))
    return Client(HTTP_HOST=HOST)


def test_hosted_seed_is_additive_and_preserves_existing_schools(hosted):
    school = School.objects.create(slug="sbmc-protected", display_name="SBMC", feature_flags={"leads": True})
    before = School.objects.filter(pk=school.pk).values().get()
    call_command("seed_booking_experience_demo", SLUG, hosted=True)
    owner = get_user_model().objects.get(username="plushumi-admin")
    assert not owner.has_usable_password() and not owner.is_staff and not owner.is_superuser
    profile = SchedulingProfile.objects.get(school__slug=SLUG)
    assert profile.school.is_demo
    assert not profile.school.feature_flags["email_notifications_enabled"]
    assert profile.reservations.count() == 15
    booking_ids = set(profile.reservations.values_list("pk", flat=True))
    owner.set_password("existing-secure-test-password")
    owner.save()
    original_password = owner.password
    profile.locations.first().rooms.update(name="Keep my studio")
    call_command("seed_booking_experience_demo", SLUG, hosted=True)
    owner.refresh_from_db()
    assert owner.password == original_password
    assert set(profile.reservations.values_list("pk", flat=True)) == booking_ids
    assert School.objects.filter(pk=school.pk).values().get() == before
    assert not SchedulingProfile.objects.filter(school=school).exists()


@pytest.mark.parametrize("options", [{"reset": True}, {"password": "admin"}])
def test_hosted_seed_forbids_reset_and_password(hosted, options):
    with pytest.raises(CommandError):
        call_command("seed_booking_experience_demo", SLUG, hosted=True, **options)
    assert not School.objects.filter(slug=SLUG).exists()


def test_seed_cannot_adopt_existing_unrelated_user(hosted):
    get_user_model().objects.create_user(username="plushumi-admin", password="keep-me")
    with pytest.raises(CommandError, match="existing account"):
        call_command("seed_booking_experience_demo", SLUG, hosted=True)
    assert not School.objects.filter(slug=SLUG).exists()


@pytest.mark.parametrize("field,value", [("is_demo", False), ("stripe_subscription_id", "sub_live"),
    ("feature_flags", {})])
def test_seed_cannot_repurpose_customer(hosted, field, value):
    defaults = {"is_demo": True, "feature_flags": {"booking_experience_demo": True}, field: value}
    school = School.objects.create(slug=SLUG, **defaults)
    before = School.objects.filter(pk=school.pk).values().get()
    with pytest.raises(CommandError):
        call_command("seed_booking_experience_demo", SLUG, hosted=True)
    assert School.objects.filter(pk=school.pk).values().get() == before


def test_hosted_opt_in_required_for_seed_and_routes(hosted):
    config.settings.BOOKING_HOSTED_DEMO_SLUGS = []
    with pytest.raises(CommandError):
        call_command("seed_booking_experience_demo", SLUG, hosted=True)
    assert hosted.get(ROOT, secure=True).status_code == 404


@pytest.mark.parametrize("variant", ["", "index.html", "inline.html", "modal.html"])
def test_real_flow_wrappers_are_guarded_and_same_origin(demo, hosted, variant):
    response = hosted.get(ROOT + variant, secure=True)
    assert response.status_code == (302 if variant == "inline.html" else 200)
    assert response["X-Frame-Options"] == "DENY"
    assert "frame-src 'self'" in response["Content-Security-Policy"]
    assert "noindex" in response["X-Robots-Tag"]
    if variant == "inline.html":
        assert response.url == url("start")
    if variant == "modal.html":
        assert b'data-url="/bookings/plushumi-demo/experience/"' in response.content
        assert b'<form' not in response.content
    assert hosted.post(ROOT + variant, secure=True).status_code == 405


def test_booking_option_navigates_to_real_page_with_one_header(demo, hosted):
    hub = hosted.get(ROOT, secure=True)
    assert b'href="inline.html"' in hub.content and b'href="modal.html"' in hub.content
    assert b"Booking page" in hub.content and b"View inline preview" not in hub.content
    redirect = hosted.get(ROOT + "inline.html", secure=True)
    assert redirect.status_code == 302 and redirect.url == url("start")
    response = hosted.get(redirect.url, secure=True)
    assert response.status_code == 200
    assert response.content.count(b'<header class="brandbar">') == 1
    assert b"<iframe" not in response.content
    for page in (hub, response, hosted.get(ROOT + "modal.html", secure=True)):
        assert b'class="preview-note"' not in page.content
        assert b'class="demo-bar"' not in page.content
        assert b"View assumptions" not in page.content
        assert b"not connected" not in page.content
        assert b"Same workshop and participant selections" not in page.content


def test_only_public_demo_pages_can_be_framed(demo, hosted):
    response = hosted.get(url("start"), secure=True)
    assert response.status_code == 200
    assert response["X-Frame-Options"] == "SAMEORIGIN"
    assert response["Content-Security-Policy"] == "frame-ancestors 'self'"
    hosted.force_login(get_user_model().objects.get(username="plushumi-admin"))
    response = hosted.get(url("preparation"), secure=True)
    assert response.status_code == 200 and response["X-Frame-Options"] == "DENY"
    assert hosted.get(ROOT + "unknown.html", secure=True).status_code == 404


@pytest.mark.parametrize("variant", ["", "inline.html", "modal.html"])
def test_wrong_host_plain_http_and_non_demo_tenant_rejected(demo, hosted, variant):
    path = ROOT + variant
    assert hosted.get(path).status_code == 404
    assert hosted.get(path, secure=True, HTTP_HOST="app.mypontora.com").status_code == 404
    config.settings.BOOKING_HOSTED_DEMO_HOSTS = []
    assert hosted.get(path, secure=True).status_code == 404
    config.settings.BOOKING_HOSTED_DEMO_HOSTS = [HOST]
    demo.school.is_demo = False
    demo.school.save()
    assert hosted.get(path, secure=True).status_code == 404


def test_disabled_school_rejected(demo, hosted):
    demo.school.is_active = False
    demo.school.save()
    assert hosted.get(ROOT, secure=True).status_code == 404


def test_csrf_not_disabled_for_hosted_customer_posts(demo, hosted):
    session = demo.locations.first().offerings.first().sessions.first()
    response = Client(enforce_csrf_checks=True, HTTP_HOST=HOST).post(url("choose", pk=session.pk), {}, secure=True)
    assert response.status_code == 403


@pytest.mark.parametrize("method,endpoint,limit,allowed_status", [
    ("get", "start", 60, 200),
    ("post", "choose", 10, 400),
])
@pytest.mark.parametrize("at_window_end", [False, True])
def test_hosted_get_and_post_abuse_limits(
        demo, hosted, settings, rate_limit_clock, method, endpoint, limit,
        allowed_status, at_window_end):
    settings.RATELIMIT_ENABLE = True
    session = demo.locations.first().offerings.first().sessions.first()
    path = url(endpoint, **({"pk": session.pk} if endpoint == "choose" else {}))
    send = getattr(hosted, method)
    window_end = ratelimit_core._get_window("127.0.0.1", 60)
    if at_window_end:
        rate_limit_clock.now = window_end

    results = [send(path, secure=True).status_code for _ in range(limit)]
    assert results == [allowed_status] * limit
    assert send(path, secure=True).status_code == 403
    assert send(path, secure=True, REMOTE_ADDR="192.0.2.17").status_code == allowed_status

    rate_limit_clock.now = window_end
    assert send(path, secure=True).status_code == 403
    rate_limit_clock.now = window_end + 1
    assert send(path, secure=True).status_code == allowed_status
