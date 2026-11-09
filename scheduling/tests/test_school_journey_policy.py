from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from scheduling.school_navigation import navigation_policy, workflow_policy
from scheduling.tests.test_integrated_demo import demo_environment, integrated, path


@pytest.mark.parametrize("slug", ["south-bay-music", "plushumi-demo", "unconfigured-school"])
def test_existing_schools_do_not_opt_in(slug):
    policy = navigation_policy(SimpleNamespace(slug=slug))
    assert policy["layout"] == "bookings"
    assert not policy["simplified"] and not policy["single_location"]
    workflow = workflow_policy(SimpleNamespace(slug=slug))
    assert not any(workflow[key] for key in ("teacher_self_service", "direct_enrollment", "enrollment_linking"))
    assert set(workflow["optional_features"]) == {"recurring_lessons", "makeup_credits", "family_booking", "operator_tools", "participant_choices", "inventory", "attendance", "family_management"}


def test_approved_demo_scope_uses_existing_configuration():
    policy = navigation_policy(SimpleNamespace(slug="enrollment-request-demo"))
    workflow = workflow_policy(SimpleNamespace(slug="enrollment-request-demo"))
    assert policy["simplified"] and workflow["direct_enrollment"]
    assert policy["single_location"]
    assert policy["features"] == ["lessons", "workshops", "camps", "reservations"]
    assert workflow["optional_features"] == []
    assert not workflow["enrollment_linking"]


@pytest.mark.django_db
def test_class_request_setting_follows_school_workflow_configuration(integrated, monkeypatch):
    from scheduling.forms import OfferingForm
    from scheduling.setup import save_setup
    profile, user = integrated
    form = OfferingForm(profile=profile)
    assert "accepts_requests" in form.fields
    assert not {"inventory_catalog", "allow_deferred_selections", "payment_at_booking"} & form.fields.keys()
    location = profile.locations.first()
    saved = save_setup(user, profile.school.slug, "workshops", {
        "name": "Community class", "location": location.pk,
        "price_per_participant": "20", "accepts_requests": "on"})
    assert saved.is_valid(), saved.errors
    assert saved.instance.accepts_requests
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: None)
    assert "accepts_requests" not in OfferingForm(profile=profile).fields


@pytest.mark.parametrize("value", [None, "no", 1, [], {}])
@pytest.mark.parametrize("field", ["simplified", "teacher_self_service", "direct_enrollment", "enrollment_linking", "single_location"])
def test_journey_options_are_strict_booleans(monkeypatch, value, field):
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug:
        SimpleNamespace(raw={"scheduling": {"navigation": {"layout": "school", field: value}}}))
    with pytest.raises(ValidationError):
        navigation_policy(SimpleNamespace(slug="generic-school"))


@pytest.mark.django_db
def test_optional_recurring_screen_is_disabled_without_deleting_history(integrated, client):
    from scheduling.models import LessonSeries
    profile, user = integrated
    client.force_login(user)
    before = LessonSeries.objects.filter(profile=profile).count()
    assert client.get(path("lesson_series_new")).status_code == 404
    assert client.post(path("lesson_series_new"), {}).status_code == 404
    assert LessonSeries.objects.filter(profile=profile).count() == before
    series = LessonSeries.objects.filter(profile=profile).first()
    assert client.get(path("lesson_series", public_id=series.public_id)).status_code == 200


@pytest.mark.django_db
def test_opted_school_has_consistent_top_level_destinations(integrated, client):
    from scheduling.tests.test_admin_navigation import NavigationParser
    profile, user = integrated
    client.force_login(user)
    paths = [reverse("school_dashboard", kwargs={"school_slug": profile.school.slug}),
        path("school_schedule"), path("school_programs"), path("lesson_setup"),
        *[reverse(route, kwargs={"school_slug": profile.school.slug}) for route in
          ("school_submissions", "school_leads", "school_reports", "school_settings")]]
    for endpoint in paths:
        page = client.get(endpoint)
        assert page.status_code == 200
        nav = NavigationParser(page.content).groups["School"]
        assert [item["text"] for item in nav] == ["Dashboard", "Enrollment", "Leads", "Schedule", "Reports", "Settings"]
        assert b"Studio Prep" not in page.content and b"Make-up credits" not in page.content
        assert b'class="school-enrollment-tabs"' not in page.content
        assert b'school-shell' in page.content
        assert b'class="dash-date"' not in page.content
        assert b'Public school page' not in page.content
        if endpoint.endswith("/reports/"):
            assert b'class="school-report-scroll"' in page.content


@pytest.mark.django_db
def test_simplified_settings_reuse_admin_components(integrated, client):
    profile, user = integrated
    client.force_login(user)
    response = client.get(reverse("school_settings", kwargs={"school_slug": profile.school.slug}))
    assert response.status_code == 200
    assert b'dash-table school-settings-table' in response.content
    assert b'class="settings-tab" href="' + path("school_programs").encode() in response.content
    assert b'id="programs"' not in response.content
    assert b'Open family booking page' not in response.content
    assert [item["label"] for item in response.context["scheduling_entry"]["links"]] == ["Rooms", "Staff access"]
    for item in response.context["scheduling_entry"]["links"]:
        assert client.get(item["url"]).status_code == 200


@pytest.mark.django_db
def test_child_pages_have_top_back_arrow_and_valid_destination(integrated, client):
    from html.parser import HTMLParser

    class BackLink(HTMLParser):
        def __init__(self, content):
            super().__init__()
            self.href = None
            self.arrow = False
            self.feed(content.decode())

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "a" and "back-link" in attrs.get("class", "").split() and not self.href:
                self.href = attrs["href"]
            if self.href and attrs.get("data-lucide") == "arrow-left":
                self.arrow = True

    profile, user = integrated
    client.force_login(user)
    pages = [(path("school_programs"), reverse("school_settings", kwargs={"school_slug": profile.school.slug})),
        (path("lesson_setup"), path("school_schedule")),
        (path("lesson_setup_new", kind="teachers"), path("lesson_setup") + "?view=list"),
        (path("lesson_booking_offer_new"), path("school_programs")),
        (path("room_new"), path("locations")),
        (path("requests"), path("school_schedule")),
        (path("camps"), path("school_programs"))]
    for endpoint, destination in pages:
        response = client.get(endpoint)
        assert response.status_code == 200
        back = BackLink(response.content)
        assert back.href == destination, endpoint
        assert back.arrow, endpoint
        assert client.get(back.href).status_code == 200


@pytest.mark.django_db
def test_enrollment_program_save_returns_to_settings_programs(integrated, client):
    profile, user = integrated
    client.force_login(user)
    endpoint = reverse("school_program_create", kwargs={"school_slug": profile.school.slug})
    response = client.get(endpoint)
    assert response.context["back_url"] == path("school_programs")
    response = client.post(endpoint, {"name": "New elective", "capacity": 12})
    assert response.status_code == 302
    assert response.url == path("school_programs")


@pytest.mark.django_db
@pytest.mark.parametrize("kind,label", [("availability", "Add hours"), ("closures", "Add time off")])
def test_teacher_forms_return_to_selected_teacher(integrated, client, kind, label):
    from scheduling.lesson_models import LessonTeacher
    profile, user = integrated
    teacher = LessonTeacher.objects.filter(profile=profile).first()
    client.force_login(user)
    response = client.get(path("lesson_setup_new", kind=kind), {"teacher": teacher.pk})
    assert response.status_code == 200
    target = path("lesson_setup") + f"?teacher={teacher.pk}"
    assert response.context["teacher_return_url"] == target
    assert response.context["title"] == label
    assert response.content.count(f'href="{target}"'.encode()) == 2


@pytest.mark.django_db
def test_shared_setup_back_and_cancel_have_same_destination(integrated, client):
    profile, user = integrated
    client.force_login(user)
    from scheduling.models import Offering
    offering = Offering.objects.filter(location__profile=profile, visit_program__isnull=True).first()
    for endpoint, target in (
        (path("workshop_new"), path("school_programs")),
        (path("workshop_edit", pk=offering.pk), path("school_programs")),
        (path("room_new"), path("locations")),
        (path("session_new", offering_pk=offering.pk), path("workshop_detail", pk=offering.pk)),
    ):
        response = client.get(endpoint)
        assert response.status_code == 200
        assert response.context["setup_return_url"] == target
        assert response.content.count(f'href="{target}"'.encode()) == 2
    response = client.get(path("workshop_new"))
    assert b'Save class' in response.content and b'Class details' in response.content
