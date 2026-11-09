"""Database-free checks for the optional enrollment navigation include."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.template import Context, Template

from scheduling.public_school_context import enrollment_navigation_context
from scheduling.templatetags.public_school_navigation import school_public_navigation


@pytest.mark.parametrize("policy", [
    {"layout": "bookings", "simplified": False},
    {"layout": "bookings", "simplified": True},
    {"layout": "school", "simplified": False},
    {"layout": "school"},
])
def test_default_pages_do_not_query_scheduling(policy):
    with patch("scheduling.public_school_context.navigation_policy", return_value=policy), \
            patch("scheduling.models.SchedulingProfile.objects.select_related") as profiles:
        assert enrollment_navigation_context(SimpleNamespace(slug="example")) == {}
    profiles.assert_not_called()


def test_missing_school_has_no_navigation():
    assert enrollment_navigation_context(None) == {}


@pytest.mark.parametrize("key", ["embed_mode", "embed"])
def test_embedded_forms_do_not_load_navigation(key):
    with patch("scheduling.templatetags.public_school_navigation.enrollment_navigation_context") as context:
        assert school_public_navigation({key: True}, Mock()) == {}
    context.assert_not_called()


def test_opt_in_requires_active_profile_and_reuses_shared_context():
    school = SimpleNamespace(slug="example")
    policy = {"layout": "school", "simplified": True}
    profile = Mock()
    with patch("scheduling.public_school_context.navigation_policy", return_value=policy), \
            patch("scheduling.models.SchedulingProfile.objects.select_related") as profiles, \
            patch("scheduling.public_school_context.public_school_context", return_value={"simplified_school": True}) as shared:
        profiles.return_value.filter.return_value.first.return_value = profile
        assert enrollment_navigation_context(school) == {"business": school, "simplified_school": True}
        profiles.return_value.filter.assert_called_once_with(school=school, enabled=True, school__is_active=True)
        shared.assert_called_once_with(profile, policy=policy)
        profiles.return_value.filter.return_value.first.return_value = None
        assert enrollment_navigation_context(school) == {}


@pytest.mark.parametrize("trials,classes", [(False, False), (True, False), (False, True)])
def test_shared_include_renders_only_enabled_public_links(trials, classes):
    context = {"business": SimpleNamespace(slug="example"), "simplified_school": True,
        "trial_booking_available": trials, "journey_features": {"workshops": classes, "camps": False}}
    with patch("scheduling.templatetags.public_school_navigation.enrollment_navigation_context", return_value=context):
        html = Template("{% load public_school_navigation %}{% school_public_navigation school %}").render(Context({"school": context["business"]}))
    assert "School home" in html and "/bookings/example/school/" in html
    assert "/schools/example/apply/" in html
    assert ("Book a trial" in html) is trials
    assert ("Classes &amp; programs" in html) is classes
    assert "/admin/" not in html


def test_legacy_include_has_no_visible_output():
    with patch("scheduling.templatetags.public_school_navigation.enrollment_navigation_context", return_value={}):
        html = Template("{% load public_school_navigation %}{% school_public_navigation school %}").render(Context({"school": Mock()}))
    assert not html.strip()
