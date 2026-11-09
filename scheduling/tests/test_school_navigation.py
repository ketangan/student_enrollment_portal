from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import SchoolAdminMembership
from scheduling.models import LessonOccurrence, LessonTeacher, SchedulingMembership
from scheduling.school_navigation import navigation_policy, school_navigation
from scheduling.tests.test_admin_navigation import NavigationParser
from scheduling.tests.test_integrated_demo import demo_environment, integrated, path

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def legacy_school_layout(monkeypatch):
    """Keep coverage of the original opt-in layout alongside the new journeys."""
    from scheduling import school_navigation as module
    original = module.load_school_config

    def load(slug):
        config = original(slug)
        if not config:
            return config
        raw = {**config.raw, "scheduling": {"navigation": {"layout": "school", "features": ["lessons", "visits"]}}}
        return SimpleNamespace(raw=raw)

    monkeypatch.setattr(module, "load_school_config", load)


@pytest.mark.parametrize("route,kwargs", [
    ("lessons", {}), ("lesson_setup", {}), ("lesson_settings", {}),
    ("lesson_series_new", {}), ("lesson_bookings", {}), ("lesson_booking_setup", {}),
    ("family_access", {}), ("lesson_credits", {}), ("lesson_credit_policy", {}),
    ("locations", {}), ("visits", {}), ("visit_setup", {}), ("admission_policy", {}),
    ("lesson_setup_new", {"kind": "services"}), ("lesson_setup_new", {"kind": "teachers"}),
    ("lesson_setup_new", {"kind": "availability"}), ("lesson_setup_new", {"kind": "closures"}),
    ("lesson_setup_new", {"kind": "households"}), ("lesson_setup_new", {"kind": "students"}),
])
def test_school_shell_is_consistent_across_every_scheduling_page(integrated, client, route, kwargs):
    profile, user = integrated
    client.force_login(user)
    home = reverse("school_dashboard", kwargs={"school_slug": profile.school.slug})
    baseline = NavigationParser(client.get(home).content).groups["School"]
    page = client.get(path(route, **kwargs))
    assert page.status_code == 200
    parsed = NavigationParser(page.content)
    nav = parsed.groups["School"]
    assert [(x["text"], x["href"]) for x in nav] == [(x["text"], x["href"]) for x in baseline]
    assert [x["text"] for x in nav if x.get("aria-current")] == ["Scheduling"]
    assert "Studio Prep" not in [x["text"] for x in nav]
    assert b"Materials Inventory" not in page.content
    assert b"school-workspace.css" in page.content


def test_entry_settings_and_setup_are_focused(integrated, client):
    profile, user = integrated
    client.force_login(user)
    response = client.get(path("workspace"))
    assert response.status_code == 302 and response.url == path("lessons")
    response = client.get(reverse("school_settings", kwargs={"school_slug": profile.school.slug}) + "?tab=scheduling")
    entry = response.context["scheduling_entry"]
    assert entry["url"] == path("lessons")
    assert [g["label"] for g in entry["groups"]] == ["Lessons", "Admissions visits", "School resources"]
    assert "Workshops and participant choices" not in str(entry)
    assert "Camps" not in str(entry)
    for group in entry["groups"]:
        for link in group["links"]:
            assert client.get(link["url"], follow=True).status_code == 200
    teachers = client.get(path("lesson_setup") + "?section=teachers").content
    students = client.get(path("lesson_setup") + "?section=students").content
    assert b"Add availability" in teachers and b"Add family" not in teachers
    assert b"Add family" in students and b"Add availability" not in students
    assert b"Manage family access" in students


def test_setup_save_returns_to_correct_section_and_schedule_filters(integrated, client):
    profile, user = integrated
    client.force_login(user)
    saved = client.post(path("lesson_setup_new", kind="households"), {"name": "Another family", "email": "family@example.invalid"})
    assert saved.status_code == 302 and saved.url == path("lesson_setup") + "?section=students"
    occurrence = LessonOccurrence.objects.filter(series__profile=profile).first()
    LessonOccurrence.objects.filter(pk=occurrence.pk).update(starts_at=timezone.now() - timedelta(days=2), ends_at=timezone.now() - timedelta(days=2, minutes=-30))
    upcoming = client.get(path("lessons"))
    assert occurrence.pk not in [item.pk for item in upcoming.context["items"]]
    past = client.get(path("lessons") + "?period=past")
    assert occurrence.pk in [item.pk for item in past.context["items"]]
    assert client.get(path("lessons") + "?period=invalid").context["period"] == "upcoming"


def test_school_shell_does_not_grant_enrollment_or_setup_access(integrated, client):
    profile, user = integrated
    SchoolAdminMembership.objects.filter(user=user).delete()
    client.force_login(user)
    page = client.get(path("lessons"))
    assert page.status_code == 200 and "School" not in NavigationParser(page.content).groups
    SchedulingMembership.objects.filter(user=user).update(role="viewer")
    assert client.get(path("lesson_setup_new", kind="teachers")).status_code == 404
    assert client.get(path("lesson_settings")).status_code == 404
    assert b"Add teacher" not in client.get(path("lesson_setup")).content


@pytest.mark.parametrize("value", [None, "school", {"layout": "invalid"}, {"layout": []}, {"features": "lessons"}, {"features": ["invalid"]}, {"features": [{}]}, {"layout": "school", "simplified": "yes"}, {"simplified": True}, {"optional_features": ["unknown"]}])
def test_invalid_navigation_configuration_is_rejected(monkeypatch, value):
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={"scheduling": {"navigation": value}}))
    with pytest.raises(ValidationError):
        navigation_policy(SimpleNamespace(slug="arbitrary-school"))


def test_default_navigation_is_unchanged_for_other_schools(monkeypatch):
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: None)
    assert navigation_policy(SimpleNamespace(slug="arbitrary-school"))["layout"] == "bookings"


def test_invalid_scheduling_configuration_is_rejected(monkeypatch):
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={"scheduling": []}))
    with pytest.raises(ValidationError):
        navigation_policy(SimpleNamespace(slug="arbitrary-school"))


@pytest.mark.parametrize("route,query,kind,active", [
    ("lesson_booking_detail", {}, "", "bookings"),
    ("lesson_credit", {}, "", "credits"),
    ("family_access", {}, "", "students"),
    ("lesson_setup", {"section": "students"}, "", "students"),
    ("lesson_setup_new", {}, "households", "students"),
    ("lesson_setup", {}, "", "teachers"),
    ("visit_program", {}, "", "visits"),
    ("lesson_settings", {}, "", "settings"),
])
def test_secondary_navigation_selects_current_section(monkeypatch, route, query, kind, active):
    monkeypatch.setattr("scheduling.appointments.preview_enabled", lambda: True)
    context = {"business": SimpleNamespace(slug="another-school"), "features": {"lessons": True, "visits": True},
        "profile": SimpleNamespace(lessons_enabled=True), "can_edit_setup": True}
    request = SimpleNamespace(resolver_match=SimpleNamespace(url_name=route, kwargs={"kind": kind}), GET=query)
    tabs = school_navigation(request, context)["section"]["tabs"]
    assert [tab["key"] for tab in tabs if tab["active"]] == [active]


def test_readonly_and_disabled_navigation(monkeypatch):
    monkeypatch.setattr("scheduling.appointments.preview_enabled", lambda: False)
    context = {"business": SimpleNamespace(slug="another-school"), "features": {"lessons": True, "visits": False},
        "profile": SimpleNamespace(lessons_enabled=True), "can_edit_setup": False}
    request = SimpleNamespace(resolver_match=SimpleNamespace(url_name="lessons", kwargs={}), GET={})
    tabs = school_navigation(request, context)["section"]["tabs"]
    assert {tab["key"] for tab in tabs} == {"schedule", "students", "teachers", "credits"}
    context["profile"].lessons_enabled = False
    assert school_navigation(request, context)["section"]["url"] == reverse("scheduling:workspace", kwargs={"school_slug": "another-school"})


@pytest.mark.parametrize("route,destination", [
    ("lesson_booking_offer_new", "lesson_booking_setup"),
    ("lesson_credit_policy", "lesson_credits"),
    ("lesson_settings", "lessons"),
])
def test_cancel_returns_to_related_section(integrated, client, route, destination):
    _, user = integrated
    client.force_login(user)
    html = client.get(path(route)).content.decode()
    assert f'href="{path(destination)}">Cancel</a>' in html


def test_teacher_setup_query_count_does_not_grow_per_teacher(integrated, client):
    profile, user = integrated
    client.force_login(user)
    url = path("lesson_setup") + "?section=teachers"
    client.get(url)
    with CaptureQueriesContext(connection) as baseline:
        assert client.get(url).status_code == 200
    services = list(LessonTeacher.objects.filter(profile=profile).first().services.all())
    for number in range(5):
        teacher = LessonTeacher.objects.create(profile=profile, name=f"Additional teacher {number}")
        teacher.services.set(services)
    with CaptureQueriesContext(connection) as expanded:
        assert client.get(url).status_code == 200
    assert len(expanded) == len(baseline)
