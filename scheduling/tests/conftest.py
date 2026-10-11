from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.cache.backends import base, locmem
from django_ratelimit import core as ratelimit_core

from core.models import School
from scheduling.models import SchedulingMembership, SchedulingProfile
from scheduling.capabilities import CAPABILITIES


@pytest.fixture
def rate_limit_clock(monkeypatch):
    clock = SimpleNamespace(now=1_800_000_000)
    timer = SimpleNamespace(time=lambda: clock.now)
    # Control rate windows and cache expiry without freezing signing/session clocks.
    for module in (ratelimit_core, base, locmem):
        monkeypatch.setattr(module, "time", timer)
    cache.clear()
    try:
        yield clock
    finally:
        cache.clear()


@pytest.fixture
def booking_user(db):
    return get_user_model().objects.create_user("booking-owner", password="test-bookings")


@pytest.fixture
def booking_profile(db):
    school = School.objects.create(slug="workshop-test", display_name="Workshop Test")
    return SchedulingProfile.objects.create(school=school, enabled=True, capabilities=list(CAPABILITIES))


@pytest.fixture
def booking_member(booking_user, booking_profile):
    return SchedulingMembership.objects.create(
        user=booking_user, profile=booking_profile, role=SchedulingMembership.Role.OWNER
    )
