"""School journeys reuse the established admin presentation components."""
from html.parser import HTMLParser

import pytest
from django import forms

from scheduling.location_filters import LocationFilterForm
from scheduling.models import Location, Offering, Session

from scheduling.tests.test_lessons import music
from scheduling.tests.test_school_workspace import school_workspace, session, camp, url

pytestmark = pytest.mark.django_db


class SelectNames(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.names = []
        self.feed(content.decode())

    def handle_starttag(self, tag, attrs):
        if tag == "select":
            self.names.append(dict(attrs).get("name"))


def test_schedule_uses_shared_table_and_preserves_booking_actions(school_workspace):
    s = school_workspace
    group = session(s)
    response = s.client.get(url(s, "school_schedule"))
    assert response.status_code == 200
    assert b'class="table-wrap"' in response.content
    assert b'class="dash-table school-agenda-table"' in response.content
    assert b'aria-label="Scheduled lessons and classes"' in response.content
    assert group.offering.name.encode() in response.content
    assert url(s, "workshop_detail", pk=group.offering_id).encode() in response.content
    assert b"journey-agenda-row" not in response.content


def test_programs_use_shared_tables_not_a_second_list_theme(school_workspace):
    s = school_workspace
    session(s)
    response = s.client.get(url(s, "school_programs"))
    assert response.status_code == 200
    assert b'class="table-wrap"' in response.content
    assert b'class="dash-table"' in response.content
    assert b'class="journey-records"' not in response.content
    assert url(s, "workshop_new").encode() in response.content
    assert b'aria-label="Program types"' not in response.content
    assert b'Public school page' not in response.content
    assert b'Trial lessons' in response.content
    assert b'One-time classes' in response.content
    assert b'Camps &amp; seasonal programs' in response.content


def test_program_actions_still_respect_read_only_role(school_workspace):
    s = school_workspace
    s.member.role = "viewer"
    s.member.save(update_fields=["role"])
    response = s.client.get(url(s, "school_programs"))
    assert response.status_code == 200
    assert url(s, "workshop_new").encode() not in response.content
    assert url(s, "lesson_booking_offer_new").encode() not in response.content


def test_camp_detail_uses_session_layout_without_studio_prep_link(school_workspace):
    s = school_workspace
    program = camp(s)
    response = s.client.get(url(s, "camp_detail", pk=program.pk))
    assert response.status_code == 200
    assert b'class="workshop-heading"' in response.content
    assert b'aria-label="Program sessions"' in response.content
    assert b'View roster' not in response.content


def test_single_location_presentation_is_opt_in(school_workspace):
    s = school_workspace
    form = LocationFilterForm({}, profile=s.profile)
    assert form.show_location_filter
    assert isinstance(form.fields["location"].widget, forms.Select)
    s.policy["single_location"] = True
    form = LocationFilterForm({}, profile=s.profile)
    assert not form.show_location_filter
    assert isinstance(form.fields["location"].widget, forms.HiddenInput)
    Location.objects.create(profile=s.profile, name="Second campus")
    assert LocationFilterForm({}, profile=s.profile).show_location_filter


def test_program_categories_distinguish_camp_only_from_standalone_classes(school_workspace):
    s = school_workspace
    program = camp(s)
    standalone = session(s)
    draft = Offering.objects.create(location=s.location, name="New draft class")
    response = s.client.get(url(s, "school_programs"))
    class_ids = {item.pk for item in response.context["workshops"]}
    assert {standalone.offering_id, draft.pk} <= class_ids
    assert program.offering_id not in class_ids
    assert program in response.context["camps"]
    Session.objects.create(offering=program.offering, starts_at=standalone.starts_at,
        ends_at=standalone.ends_at, capacity=10)
    response = s.client.get(url(s, "school_programs"))
    assert program.offering_id in {item.pk for item in response.context["workshops"]}


@pytest.mark.parametrize("route", ["school_schedule", "school_programs", "lesson_setup", "requests", "locations"])
def test_single_location_pages_have_no_location_selector(school_workspace, route):
    s = school_workspace
    s.policy["single_location"] = True
    page = s.client.get(url(s, route))
    assert page.status_code == 200
    assert "location" not in SelectNames(page.content).names
    assert page.context["show_location_filter"] is False


def test_hidden_location_filter_still_rejects_foreign_and_stale_ids(school_workspace):
    s = school_workspace
    s.policy["single_location"] = True
    foreign = Location.objects.exclude(profile=s.profile).first()
    invalid_id = foreign.pk if foreign else 999999
    form = LocationFilterForm({"location": invalid_id}, profile=s.profile)
    assert not form.show_location_filter and not form.is_valid()
    assert not form.filter(Location.objects.all(), "pk").exists()
    page = s.client.get(url(s, "school_schedule"), {"location": invalid_id})
    assert page.status_code == 400
    assert b'Correct the filters' in page.content
    assert s.client.get(url(s, "school_schedule"), {"location": ""}).status_code == 200
