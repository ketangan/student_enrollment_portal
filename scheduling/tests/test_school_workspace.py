"""Journey contracts, independent of production route integration."""
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.http import Http404
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import include, path, reverse
from django.utils import timezone

from config.urls import urlpatterns as root_patterns
from core.models import School, SchoolAdminMembership, SchoolProgram, Submission
from core.services.config_loader import SchoolConfig, load_school_config
from scheduling import school_workspace as workspace
from scheduling.appointment_models import LessonBookingOffer
from scheduling.models import Camp, ChoiceGroup, ChoiceOption, LessonTeacher, Location, Offering, SchedulingProfile, Session
from scheduling.tests.test_lessons import book, music
from scheduling.urls import urlpatterns as scheduling_patterns

pytestmark = pytest.mark.django_db

urlpatterns = [path("bookings/", include(([
    path("<slug:school_slug>/school/schedule/", workspace.school_schedule, name="school_schedule"),
    path("<slug:school_slug>/school/programs/", workspace.school_programs, name="school_programs"),
    path("<slug:school_slug>/school/", workspace.school_public, name="school_public"),
    path("<slug:school_slug>/school/classes/", workspace.school_classes, name="school_classes"),
] + [item for item in scheduling_patterns if getattr(item, "name", None) not in {
    "school_schedule", "school_programs", "school_public", "school_classes"}], "scheduling")))] + [
    item for item in root_patterns if getattr(item, "namespace", None) != "scheduling"]


@pytest.fixture
def school_workspace(music, monkeypatch, settings, client):
    settings.ROOT_URLCONF = __name__
    settings.DEBUG = True
    settings.BOOKINGS_LESSON_BOOKING_PREVIEW = True
    settings.APP_BASE_URL = settings.DEMO_BASE_URL = "http://testserver"
    policy = {"layout": "school", "simplified": True,
              "features": ["lessons", "workshops", "camps"], "optional_features": [],
              "teacher_self_service": False, "direct_enrollment": False, "enrollment_linking": False}
    school = music["profile"].school
    config = SchoolConfig(raw={
        "school": {"slug": school.slug, "display_name": school.display_name},
        "form": {"sections": [{"fields": [{"key": "preferred_time", "type": "select",
            "options": [{"value": "after_school", "label": "After school"}]}]}]},
        "scheduling": {"navigation": policy},
    })

    def scoped_config(slug):
        return config if slug == school.slug else load_school_config(slug)

    monkeypatch.setattr("scheduling.school_navigation.load_school_config", scoped_config)
    music["user"].is_staff = True
    music["user"].save(update_fields=["is_staff"])
    music["profile"].school.is_demo = True
    music["profile"].school.save(update_fields=["is_demo"])
    SchoolAdminMembership.objects.create(school=music["profile"].school, user=music["user"], role="owner")
    client.force_login(music["user"])
    return SimpleNamespace(**music, policy=policy, client=client, config=config)


@pytest.fixture
def school_dashboard(school_workspace, monkeypatch):
    school = school_workspace

    def scoped_config(slug):
        return school.config if slug == school.slug else load_school_config(slug)

    # Exercise the real admin loader wrapper and form-label handling, not a
    # mocked None config that silently bypasses the shared dashboard contract.
    monkeypatch.setattr("core.views_school_common.load_school_config", scoped_config)
    return school


def url(school, route, **kwargs):
    return reverse("scheduling:" + route, kwargs={"school_slug": school.slug, **kwargs})


def session(school, *, name="Family class", location=None, start=None, published=True, requests=True):
    offering = Offering.objects.create(location=location or school.location, name=name,
        is_published=published, accepts_requests=requests)
    start = start or timezone.now() + timedelta(days=2)
    return Session.objects.create(offering=offering, starts_at=start, ends_at=start + timedelta(hours=1),
                                  capacity=12, is_published=published)


def camp(school, *, name="Seasonal program", published=True):
    first = session(school, name=name + " class", published=published)
    second = Session.objects.create(offering=first.offering, starts_at=first.starts_at + timedelta(days=1),
        ends_at=first.ends_at + timedelta(days=1), capacity=12, is_published=published)
    item = Camp.objects.create(offering=first.offering, name=name, price=0, is_open=True)
    item.sessions.set([first, second])
    school.profile.native_reservations_enabled = True
    school.profile.save(update_fields=["native_reservations_enabled"])
    return item


def test_schedule_merges_existing_engines_and_uses_trial_action(school_workspace):
    s = school_workspace
    book(vars(s))
    group = session(s)
    hidden = session(s, name="Past class", start=timezone.now() - timedelta(days=1))
    page = s.client.get(url(s, "school_schedule"))
    assert page.status_code == 200
    rows = list(page.context["agenda"])
    assert [row["starts_at"] for row in rows] == sorted(row["starts_at"] for row in rows)
    assert {row["kind"] for row in rows} == {"Lesson", "Class"}
    assert group.offering.name.encode() in page.content and hidden.offering.name.encode() not in page.content
    assert url(s, "staff_lesson_trials").encode() in page.content
    assert url(s, "lesson_series_new").encode() not in page.content
    assert b"Make-up credits" not in page.content and b"Students &amp; families" not in page.content
    assert "no-store" in page.headers["Cache-Control"]


def test_teacher_filter_is_tenant_scoped_and_excludes_unassigned_classes(school_workspace):
    s = school_workspace
    book(vars(s))
    session(s)
    page = s.client.get(url(s, "school_schedule"), {"teacher": s.teacher.pk})
    assert page.status_code == 200
    assert {row["kind"] for row in page.context["agenda"]} == {"Lesson"}
    other = School.objects.create(slug="other-school")
    profile = SchedulingProfile.objects.create(school=other, enabled=True)
    teacher = LessonTeacher.objects.create(profile=profile, name="Private teacher")
    invalid = s.client.get(url(s, "school_schedule"), {"teacher": teacher.pk})
    assert invalid.status_code == 400
    assert b"Private teacher" not in invalid.content and b"Riley" not in invalid.content


@pytest.mark.parametrize("query", [{"date": "bad"}, {"date": "9999-12-31"}, {"teacher": "bad"}, {"location": "-1"}])
def test_invalid_filters_never_widen_schedule(school_workspace, query):
    s = school_workspace
    session(s, name="Must not leak")
    page = s.client.get(url(s, "school_schedule"), query)
    assert page.status_code == 400
    assert b"Must not leak" not in page.content


def test_date_filter_uses_location_day_not_utc(school_workspace):
    s = school_workspace
    s.location.timezone_name = "America/Los_Angeles"
    s.location.save(update_fields=["timezone_name"])
    session(s, name="Local January second", start=datetime(2030, 1, 3, 3, tzinfo=dt_timezone.utc))
    session(s, name="Local January third", start=datetime(2030, 1, 3, 20, tzinfo=dt_timezone.utc))
    page = s.client.get(url(s, "school_schedule"), {"date": "2030-01-02"})
    assert page.status_code == 200
    assert b"Local January second" in page.content and b"Local January third" not in page.content
    assert b"America/Los_Angeles" in page.content


def test_location_restricted_staff_see_only_assigned_sessions_not_lessons(school_workspace):
    s = school_workspace
    book(vars(s))
    session(s, name="Assigned class")
    other_location = Location.objects.create(profile=s.profile, name="Private location")
    session(s, name="Private class", location=other_location)
    s.member.all_locations = False
    s.member.save(update_fields=["all_locations"])
    s.member.locations.set([s.location])
    response = s.client.get(url(s, "school_schedule"))
    assert response.status_code == 200
    assert b"Assigned class" in response.content
    assert b"Private class" not in response.content and b"Riley" not in response.content
    assert b"Book a trial" not in response.content and b"Teachers &amp; availability" not in response.content
    assert s.client.get(url(s, "school_schedule"), {"location": other_location.pk}).status_code == 400
    assert s.client.get(url(s, "school_schedule"), {"teacher": s.teacher.pk}).status_code == 400
    s.member.locations.clear()
    response = s.client.get(url(s, "school_schedule"), {"location": ""})
    assert not list(response.context["agenda"])


@pytest.mark.parametrize("route", ["school_schedule", "school_programs"])
def test_staff_routes_require_scheduling_membership_and_get(school_workspace, route):
    s = school_workspace
    assert s.client.post(url(s, route), {}).status_code == 405
    s.member.is_active = False
    s.member.save(update_fields=["is_active"])
    assert s.client.get(url(s, route)).status_code == 404
    s.client.logout()
    assert s.client.get(url(s, route)).status_code == 302


@pytest.mark.parametrize("route", ["school_schedule", "school_programs", "school_public", "school_classes"])
@pytest.mark.parametrize("layout,simplified", [("school", False), ("bookings", False)])
def test_non_opted_in_school_does_not_acquire_journey_routes(school_workspace, route, layout, simplified):
    s = school_workspace
    s.policy.update(layout=layout, simplified=simplified)
    assert s.client.get(url(s, route)).status_code == 404


def test_schedule_pagination_and_queries_do_not_grow_per_row(school_workspace):
    s = school_workspace
    first = session(s)
    target = url(s, "school_schedule")
    s.client.get(target)
    with CaptureQueriesContext(connection) as initial:
        assert s.client.get(target).status_code == 200
    Session.objects.bulk_create([Session(offering=first.offering, starts_at=first.starts_at + timedelta(days=i),
        ends_at=first.ends_at + timedelta(days=i), capacity=12, is_published=True) for i in range(1, 55)])
    with CaptureQueriesContext(connection) as expanded:
        response = s.client.get(target)
    assert response.status_code == 200
    assert len(expanded) == len(initial)
    assert len(response.context["agenda"]) == 50
    later = s.client.get(target, {"page": 2})
    assert len(later.context["agenda"]) == 5
    assert later.context["agenda"][0]["starts_at"] > response.context["agenda"][-1]["starts_at"]


def test_programs_use_existing_records_and_independent_enrollment_authorization(school_workspace):
    s = school_workspace
    program = SchoolProgram.objects.create(school=s.profile.school, code="program", name="Enrollment offering")
    SchoolProgram.objects.create(school=s.profile.school, code="removed", name="Deleted offering", is_deleted=True)
    LessonBookingOffer.objects.create(profile=s.profile, title="Trial offer", service=s.service,
        teacher=s.teacher, location=s.location)
    group = session(s)
    item = camp(s)
    page = s.client.get(url(s, "school_programs"))
    assert page.status_code == 200
    assert list(page.context["enrollment_programs"]) == [program]
    assert b"Trial offer" in page.content and group.offering.name.encode() in page.content and item.name.encode() in page.content
    assert b"Deleted offering" not in page.content and b"Materials Inventory" not in page.content
    assert url(s, "lesson_booking_offer_edit", pk=page.context["lesson_offers"][0].pk).encode() in page.content
    SchoolAdminMembership.objects.filter(user=s.user).delete()
    page = s.client.get(url(s, "school_programs"))
    assert page.status_code == 200 and b"Enrollment offering" not in page.content
    assert "enrollment_programs" not in page.context


def test_viewer_has_no_setup_or_enrollment_edit_actions(school_workspace):
    s = school_workspace
    s.member.role = "viewer"
    s.member.save(update_fields=["role"])
    SchoolAdminMembership.objects.filter(user=s.user).update(role="viewer")
    session(s)
    page = s.client.get(url(s, "school_programs"))
    assert page.status_code == 200
    for label in [b"Add class", b"Add enrollment program", b"Add trial lesson", b">Edit</a>"]:
        assert label not in page.content


def test_public_has_three_choices_and_preserves_preview_gate(school_workspace, settings):
    s = school_workspace
    s.client.logout()
    page = s.client.get(url(s, "school_public"))
    assert page.status_code == 200
    assert b"Start an application" in page.content and b"Choose a teacher" in page.content
    assert url(s, "school_classes").encode() in page.content
    assert b"Local booking preview" in page.content
    settings.BOOKINGS_LESSON_BOOKING_PREVIEW = False
    page = s.client.get(url(s, "school_public"))
    assert url(s, "lesson_trials").encode() not in page.content
    assert b"Online trial booking is not currently available" in page.content


def test_public_classes_need_no_inventory_and_requests_are_not_bookings(school_workspace):
    s = school_workspace
    visible = session(s, name="Ordinary class")
    visible.offering.accepts_party_requests = True
    visible.offering.save(update_fields=["accepts_party_requests"])
    session(s, name="Draft class", published=False)
    session(s, name="Old class", start=timezone.now() - timedelta(days=1))
    s.client.logout()
    page = s.client.get(url(s, "school_classes"))
    assert page.status_code == 200 and b"Ordinary class" in page.content
    assert b"Draft class" not in page.content and b"Old class" not in page.content
    assert b"A request is not a confirmed booking" in page.content
    assert url(s, "workshop_request", pk=visible.pk).encode() in page.content
    assert b"Plan a party" not in page.content and b"inventory" not in page.content.lower()
    assert s.client.get(url(s, "workshop_request", pk=visible.pk)).status_code == 200
    ChoiceGroup.objects.create(offering=visible.offering, name="Unavailable choices")
    page = s.client.get(url(s, "school_classes"))
    assert url(s, "workshop_request", pk=visible.pk).encode() not in page.content


def test_public_camps_reuse_date_validation_and_exclude_camp_dates_from_classes(school_workspace):
    s = school_workspace
    valid = camp(s, name="Future program")
    invalid = camp(s, name="Partly past program")
    old = invalid.sessions.first()
    old.starts_at = timezone.now() - timedelta(days=2)
    old.ends_at = old.starts_at + timedelta(hours=1)
    old.save(update_fields=["starts_at", "ends_at"])
    s.client.logout()
    page = s.client.get(url(s, "school_classes"))
    assert page.status_code == 200
    assert [item.pk for item in page.context["public_camps"]] == [valid.pk]
    assert not list(page.context["classes"])
    assert url(s, "camp_register", pk=valid.pk).encode() in page.content
    assert b"Partly past program" not in page.content
    assert s.client.get(url(s, "camp_register", pk=valid.pk)).status_code == 200


def test_public_camps_twenty_expired_programs_do_not_hide_valid_program(school_workspace):
    s = school_workspace
    for index in range(20):
        expired = camp(s, name=f"A expired {index:02d}")
        old = expired.sessions.first()
        old.starts_at = timezone.now() - timedelta(days=1)
        old.ends_at = old.starts_at + timedelta(hours=1)
        old.save(update_fields=["starts_at", "ends_at"])
    valid = camp(s, name="Z available program")
    page = s.client.get(url(s, "school_classes"))
    assert page.status_code == 200
    assert [item.pk for item in page.context["public_camps"]] == [valid.pk]
    assert url(s, "camp_register", pk=valid.pk).encode() in page.content
    assert page.context["camps_pagination"]["next"] is None


def test_public_camps_fill_page_after_engine_rejects_twenty_catalogs(school_workspace):
    s = school_workspace
    for index in range(20):
        unavailable = camp(s, name=f"A unavailable {index:02d}")
        ChoiceGroup.objects.create(offering=unavailable.offering, name="Empty choices")
    valid = camp(s, name="Z available program")
    page = s.client.get(url(s, "school_classes"))
    assert [item.pk for item in page.context["public_camps"]] == [valid.pk]


@pytest.mark.parametrize("kind", ["class", "camp"])
def test_public_catalog_query_count_is_constant_for_distinct_offerings(school_workspace, kind):
    s = school_workspace

    def add(index):
        item = camp(s, name=f"Program {index:02d}") if kind == "camp" else session(s, name=f"Class {index:02d}")
        group = ChoiceGroup.objects.create(offering=item.offering, name="Choice")
        ChoiceOption.objects.create(group=group, label="Available")

    def read():
        with CaptureQueriesContext(connection) as queries:
            page = s.client.get(url(s, "school_classes"))
        assert page.status_code == 200
        return page, queries.captured_queries

    add(0)
    read()  # Warm route/template/configuration caches before comparing counts.
    _, baseline = read()
    for index in range(1, 20):
        add(index)
    page, expanded = read()
    assert len(page.context["public_camps"] if kind == "camp" else page.context["classes"]) == 20
    assert len(expanded) == len(baseline)
    choice_queries = [item for item in expanded if 'FROM "scheduling_choice' in item["sql"]]
    assert len(choice_queries) == 2


def test_public_camp_scan_is_bounded_and_continuation_reaches_valid_program(school_workspace, monkeypatch):
    s = school_workspace
    monkeypatch.setattr(workspace, "PUBLIC_CAMP_SCAN_LIMIT", 2)
    for index in range(2):
        unavailable = camp(s, name=f"A unavailable {index}")
        ChoiceGroup.objects.create(offering=unavailable.offering, name="Empty choices")
    valid = camp(s, name="Z available program")
    checked = []
    original = workspace.check_registration

    def check(item, dates, catalog):
        checked.append(item.pk)
        return original(item, dates, catalog)

    monkeypatch.setattr(workspace, "check_registration", check)
    page = s.client.get(url(s, "school_classes"))
    assert len(checked) == 2
    assert not page.context["public_camps"]
    assert b"Continue to check more programs" in page.content
    continuation = page.context["camps_pagination"]["next"]
    page = s.client.get(url(s, "school_classes") + continuation)
    assert [item.pk for item in page.context["public_camps"]] == [valid.pk]
    assert page.context["camps_pagination"]["next"] is None
    assert page.context["camps_pagination"]["first"] is not None


def test_public_camp_continuation_does_not_skip_or_repeat_available_programs(school_workspace):
    s = school_workspace
    items = [camp(s, name=f"Program {index:02d}") for index in range(21)]
    page = s.client.get(url(s, "school_classes"))
    assert [item.pk for item in page.context["public_camps"]] == [item.pk for item in items[:20]]
    page = s.client.get(url(s, "school_classes") + page.context["camps_pagination"]["next"])
    assert [item.pk for item in page.context["public_camps"]] == [items[-1].pk]
    assert page.context["camps_pagination"]["next"] is None


def test_public_camp_dates_are_bounded_before_engine_validation(school_workspace, monkeypatch):
    s = school_workspace
    oversized = camp(s, name="A oversized program")
    first = oversized.sessions.first()
    dates = Session.objects.bulk_create([
        Session(offering=oversized.offering, starts_at=first.starts_at + timedelta(days=index),
                ends_at=first.ends_at + timedelta(days=index), capacity=12, is_published=True)
        for index in range(2, 40)
    ])
    oversized.sessions.add(*dates)
    valid = camp(s, name="Z available program")
    sizes = {}
    original = workspace.check_registration

    def check(item, dates, catalog):
        sizes[item.pk] = len(dates)
        return original(item, dates, catalog)

    monkeypatch.setattr(workspace, "check_registration", check)
    page = s.client.get(url(s, "school_classes"))
    assert sizes == {oversized.pk: 32, valid.pk: 2}
    assert [item.pk for item in page.context["public_camps"]] == [valid.pk]


def test_feature_policy_and_capabilities_are_both_required(school_workspace):
    s = school_workspace
    session(s)
    s.policy["features"] = ["lessons"]
    assert s.client.get(url(s, "school_classes")).status_code == 404
    assert b"Family class" not in s.client.get(url(s, "school_schedule")).content
    s.policy["features"] = ["lessons", "workshops", "camps"]
    s.profile.capabilities = ["lessons"]
    s.profile.save(update_fields=["capabilities"])
    assert s.client.get(url(s, "school_classes")).status_code == 404


def dashboard_request(school):
    request = RequestFactory().get("/schools/workshop-test/admin/")
    request.user, request.session = school.user, {}
    request.resolver_match = SimpleNamespace(url_name="school_dashboard", kwargs={})
    return request


def test_dashboard_helpers_do_not_grant_scheduling_access(school_dashboard):
    s = school_dashboard
    session(s, name="Staff only class")
    request = dashboard_request(s)
    assert workspace.school_dashboard_agenda(request, s.profile.school)["school_agenda_available"]
    dashboard_url = reverse("school_dashboard", kwargs={"school_slug": s.slug})
    response = s.client.get(dashboard_url)
    assert response.status_code == 200 and b"Staff only class" in response.content
    s.member.is_active = False
    s.member.save(update_fields=["is_active"])
    assert workspace.school_dashboard_agenda(request, s.profile.school) == {}
    response = s.client.get(dashboard_url)
    assert response.status_code == 200
    assert b"Staff only class" not in response.content
    s.policy["simplified"] = False
    with CaptureQueriesContext(connection) as queries:
        assert workspace.school_dashboard_agenda(request, s.profile.school) == {}
    assert not queries.captured_queries


def test_enrollment_only_user_cannot_bypass_scheduling_and_cross_host_gates(school_workspace, settings):
    s = school_workspace
    other = get_user_model().objects.create_user("enrollment-only", is_staff=True)
    SchoolAdminMembership.objects.create(school=s.profile.school, user=other, role="owner")
    s.client.force_login(other)
    assert s.client.get(url(s, "school_schedule")).status_code == 404
    request = RequestFactory().get("/", HTTP_HOST="live.example.test")
    request.user, request.session = s.user, {}
    settings.APP_BASE_URL = "https://live.example.test"
    settings.DEMO_BASE_URL = "https://demo.example.test"
    settings.ALLOWED_HOSTS = ["live.example.test", "demo.example.test"]
    with pytest.raises(Http404):
        workspace.school_schedule(request, s.slug)
    with pytest.raises(Http404):
        workspace.school_public(request, s.slug)


def test_dashboard_application_records_stay_in_the_authorized_school(school_dashboard):
    s = school_dashboard
    Submission.objects.create(school=s.profile.school, status="New", data={"student_name": "Our student"})
    other = School.objects.create(slug="another-enrollment-school")
    Submission.objects.create(school=other, status="New", data={"student_name": "Private student"})
    response = s.client.get(reverse("school_dashboard", kwargs={"school_slug": s.slug}))
    assert response.status_code == 200
    assert response.context["total_submissions"] == 1
    assert b"Our student" in response.content and b"Private student" not in response.content


@pytest.mark.parametrize("layout,simplified", [("school", True), ("school", False), ("bookings", False)])
def test_dashboard_keeps_shared_metrics_workqueue_and_activity(school_dashboard, layout, simplified):
    s = school_dashboard
    s.policy.update(layout=layout, simplified=simplified)
    session(s, name="Upcoming sidebar class")
    Submission.objects.create(school=s.profile.school, status="New",
                              data={"student_name": "Needs review", "preferred_time": "after_school"})
    Submission.objects.create(school=s.profile.school, status="Enrolled", data={"student_name": "Enrolled student"})
    response = s.client.get(reverse("school_dashboard", kwargs={"school_slug": s.slug}))
    assert response.status_code == 200
    rendered = {template.name for template in response.templates}
    assert {"dashboard.html", "dashboard/_needs_attention.html", "dashboard/_submissions_inbox.html",
            "dashboard/_activity.html"} <= rendered
    assert response.context["total_submissions"] == 2
    assert response.context["new_count"] == 1
    assert response.context["approved_count"] == 1
    assert response.context["active_count"] == 1
    assert response.context["top_schedule"]["label"] == "After school"
    for label in (b"New Submissions", b"Work Queue", b"Recent Activity", b"Needs review",
                  b"Tip: Review new submissions daily to keep your pipeline moving."):
        assert label in response.content
    assert ("scheduling/school/dashboard_agenda.html" in rendered) is simplified
    assert (b"Upcoming sidebar class" in response.content) is simplified
    assert b'class="dash-main school-journeys"' not in response.content
    assert b'class="journey-agenda"' not in response.content
    if simplified:
        assert b'<section class="dash-card" aria-labelledby="school-agenda-title">' in response.content
        assert url(s, "school_schedule").encode() in response.content


@pytest.mark.parametrize("simplified", [True, False])
def test_dashboard_empty_state_keeps_optional_agenda(school_dashboard, simplified):
    s = school_dashboard
    s.policy["simplified"] = simplified
    response = s.client.get(reverse("school_dashboard", kwargs={"school_slug": s.slug}))
    assert response.status_code == 200
    assert b"No submissions yet" in response.content
    assert b"Copy application link" in response.content
    assert b"New Submissions" in response.content
    assert (b"Coming up" in response.content) is simplified
    assert (b"No upcoming lessons or classes." in response.content) is simplified
    assert (b'class="dash-grid-side"' in response.content) is simplified
