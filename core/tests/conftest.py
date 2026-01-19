import pytest

from .factories import (
    SchoolFactory,
    UserFactory,
    SchoolAdminMembershipFactory,
    SubmissionFactory,
)


@pytest.fixture
def school(db):
    """Create and return a School via factory."""
    return SchoolFactory.create()


@pytest.fixture
def user(db):
    """Create and return a user via factory."""
    return UserFactory.create()


@pytest.fixture
def school_admin_membership(db, user, school):
    """Create and return a SchoolAdminMembership via factory."""
    return SchoolAdminMembershipFactory.create(user=user, school=school)


@pytest.fixture
def submission(db, school):
    """Create and return a Submission via factory."""
    return SubmissionFactory.create(school=school)
