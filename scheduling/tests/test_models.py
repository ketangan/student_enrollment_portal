import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from core.models import SchoolAdminMembership, Submission
from scheduling.models import SchedulingMembership, SchedulingProfile

pytestmark = pytest.mark.django_db


def test_profile_disabled_and_membership_least_privilege_by_default(booking_user, booking_profile):
    profile = SchedulingProfile(school=booking_profile.school)
    assert profile.enabled is False
    member = SchedulingMembership.objects.create(user=booking_user, profile=booking_profile)
    member.refresh_from_db()
    assert member.role == "viewer"
    assert member.is_active is True
    assert not member.user.is_staff
    assert not SchoolAdminMembership.objects.filter(user=booking_user).exists()
    assert not Submission.objects.filter(school=booking_profile.school).exists()
    assert str(booking_profile) == "Workshop Test"
    assert "booking-owner -> Workshop Test (viewer)" == str(member)


def test_one_profile_per_tenant(booking_profile):
    with pytest.raises(IntegrityError), transaction.atomic():
        SchedulingProfile.objects.create(school=booking_profile.school)


def test_one_membership_per_user_and_profile(booking_member):
    with pytest.raises(IntegrityError), transaction.atomic():
        SchedulingMembership.objects.create(user=booking_member.user, profile=booking_member.profile)


def test_invalid_roles_rejected_by_validation_and_database(booking_user, booking_profile):
    member = SchedulingMembership(user=booking_user, profile=booking_profile, role="super-owner")
    with pytest.raises(ValidationError):
        member.full_clean()
    with pytest.raises(IntegrityError), transaction.atomic():
        member.save()


def test_deleting_booking_configuration_preserves_enrollment(booking_member):
    school = booking_member.profile.school
    old_state = (school.plan, school.is_active, school.trial_started_at, school.feature_flags)
    enrollment = SchoolAdminMembership.objects.create(user=booking_member.user, school=school)
    booking_member.profile.delete()
    school.refresh_from_db()
    assert (school.plan, school.is_active, school.trial_started_at, school.feature_flags) == old_state
    assert SchoolAdminMembership.objects.filter(pk=enrollment.pk).exists()
    assert not SchedulingMembership.objects.exists()
