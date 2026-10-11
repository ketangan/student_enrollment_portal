import pytest
from django.contrib.auth import get_user_model

from core.models import School
from scheduling.models import SchedulingMembership, SchedulingProfile
from scheduling.capabilities import CAPABILITIES


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
