"""Routine school edits keep their data and the shared workflows underneath."""
from types import SimpleNamespace
from html import escape
from html.parser import HTMLParser

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.templatetags.audit_labels import record_audit_summary
from core.tests.factories import LeadFactory, SubmissionFactory
from scheduling import lessons
from scheduling.tests.test_appointments import offer
from scheduling.tests.test_integrated_demo import integrated, demo_environment
from scheduling.tests.test_lessons import music, book, url
from scheduling.tests.test_session_workspace import school_layout
from scheduling.tests.test_school_workspace import school_workspace, session, camp
from scheduling.tests.test_teacher_overview import school_music

pytestmark = pytest.mark.django_db


class RenderedInputForms(HTMLParser):
    """Read actual input payloads so a missing hidden field breaks the test."""

    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.select_names = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {"action": attrs.get("action"), "data": {}, "buttons": []}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None and attrs.get("name") and "disabled" not in attrs:
            if attrs.get("type") not in {"submit", "button", "checkbox", "radio"}:
                self.current["data"][attrs["name"]] = attrs.get("value", "")
        elif tag == "button" and self.current is not None and attrs.get("name") and "disabled" not in attrs:
            self.current["buttons"].append(attrs)
        elif tag == "select":
            self.select_names.append(attrs.get("name"))

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


@pytest.mark.parametrize("simplified", [True, False])
@pytest.mark.parametrize("kind", ["lead", "submission"])
def test_shared_status_buttons_submit_configured_values_without_changing_other_data(
    integrated, monkeypatch, client, simplified, kind,
):
    profile, user = integrated
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"navigation": {"layout": "school", "simplified": simplified}}}))
    client.force_login(user)
    record = (LeadFactory(school=profile.school, status="new") if kind == "lead"
              else SubmissionFactory(school=profile.school, status="New"))
    detail = reverse(f"school_{kind}_detail", kwargs={
        "school_slug": profile.school.slug, f"{kind}_id": record.pk})
    before = dict(record.data)
    for target in (["contacted", "new"] if kind == "lead" else ["In Review", "New"]):
        page = client.get(detail)
        assert page.status_code == 200
        html = page.content.decode()
        parsed = RenderedInputForms(html)
        assert "new_status" not in parsed.select_names
        form, = [f for f in parsed.forms if any(b["name"] == "new_status" for b in f["buttons"])]
        values = [b["value"] for b in form["buttons"] if b["name"] == "new_status"]
        if kind == "lead":
            current = page.context["lead_status_value"]
            label = page.context["lead_status_label"]
            expected = [step["value"] for step in page.context["lead_pipeline"]
                        if step["value"] not in {current, "enrolled", "application_submitted"}]
        else:
            label = record.status
            expected = [value for value in page.context["status_choices"] if value != record.status]
        assert values == expected
        assert f'aria-current="step">{escape(label)}</span>' in html
        assert target in values and form["data"]["next"] == detail
        assert form["data"]["csrfmiddlewaretoken"]
        response = client.post(form["action"], {**form["data"], "new_status": target}, follow=True)
        assert response.status_code == 200 and response.request["PATH_INFO"] == detail
        record.refresh_from_db()
        assert record.status == target and record.data == before
        updated_label = response.context["lead_status_label"] if kind == "lead" else record.status
        assert f'aria-current="step">{escape(updated_label)}</span>' in response.content.decode()


@pytest.mark.parametrize("simplified", [True, False])
@pytest.mark.parametrize("kind", ["lead", "submission"])
def test_readonly_status_pipeline_has_no_mutation_controls(integrated, monkeypatch, client, simplified, kind):
    from core.models import SchoolAdminMembership
    profile, user = integrated
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"navigation": {"layout": "school", "simplified": simplified}}}))
    SchoolAdminMembership.objects.filter(school=profile.school, user=user).update(role="viewer")
    client.force_login(user)
    record = (LeadFactory(school=profile.school, status="new") if kind == "lead"
              else SubmissionFactory(school=profile.school, status="New"))
    kwargs = {"school_slug": profile.school.slug, f"{kind}_id": record.pk}
    page = client.get(reverse(f"school_{kind}_detail", kwargs=kwargs))
    assert page.status_code == 200 and not page.context["can_mutate"]
    html = page.content.decode()
    assert 'aria-current="step">New</span>' in html
    assert 'name="new_status"' not in html
    target = "contacted" if kind == "lead" else "In Review"
    assert client.post(reverse(f"school_{kind}_status_update", kwargs=kwargs), {"new_status": target}).status_code == 404
    before = record.status
    record.refresh_from_db()
    assert record.status == before


@pytest.mark.parametrize("simplified", [True, False])
@pytest.mark.parametrize("kind", ["lead", "submission"])
def test_rendered_follow_up_form_saves_clears_and_preserves_record(
    integrated, monkeypatch, client, simplified, kind,
):
    profile, user = integrated
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"navigation": {"layout": "school", "simplified": simplified}}}))
    client.force_login(user)
    if kind == "lead":
        record = LeadFactory(school=profile.school, phone="5550101234", notes="Original note",
            data={"form_fields": {"experience": "Beginner"}})
    else:
        record = SubmissionFactory(school=profile.school, internal_notes="Original note")
    detail = reverse(f"school_{kind}_detail", kwargs={
        "school_slug": profile.school.slug, f"{kind}_id": record.pk})
    before = dict(record.data)
    status = record.status
    for value, expected in [("2030-01-15", "2030-01-15"), ("invalid", "2030-01-15"), ("", None)]:
        page = client.get(detail)
        forms = RenderedInputForms(page.content.decode()).forms
        form, = [f for f in forms if "next_follow_up_at" in f["data"]]
        assert form["data"]["next"] == detail
        assert form["data"]["csrfmiddlewaretoken"]
        result = client.post(form["action"], {**form["data"], "next_follow_up_at": value}, follow=True)
        assert result.status_code == 200
        record.refresh_from_db()
        assert (record.next_follow_up_at.date().isoformat() if record.next_follow_up_at else None) == expected
        assert record.data == before and record.status == status
        assert (record.notes if kind == "lead" else record.internal_notes) == "Original note"
        if kind == "lead":
            assert record.phone == "5550101234"


@pytest.mark.parametrize("simplified", [True, False])
def test_record_controls_are_scoped_and_converted_lead_has_one_application_action(
        integrated, monkeypatch, client, simplified):
    profile, user = integrated
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"navigation": {"layout": "school", "simplified": simplified}}}))
    client.force_login(user)
    application = SubmissionFactory(school=profile.school)
    lead = LeadFactory(school=profile.school, converted_submission=application)
    page = client.get(reverse("school_lead_detail", kwargs={"school_slug": profile.school.slug, "lead_id": lead.pk}))
    assert page.status_code == 200
    html = page.content.decode()
    assert 'class="dash-card record-contact"' in html
    assert 'class="dash-card record-header"' in html
    if page.context["admissions_managed"]:
        assert 'name="new_status"' not in html
        assert f'aria-current="step">{escape(page.context["lead_status_label"])}</span>' in html
    assert ('name="follow_up_delta"' in html) is not simplified
    if simplified:
        target = reverse("school_submission_detail", kwargs={
            "school_slug": profile.school.slug, "submission_id": application.pk})
        assert html.count(f'href="{target}"') == 1 and ">View application</a>" in html
        assert '<details class="record-details" open' not in html
    page = client.get(reverse("school_submission_detail", kwargs={"school_slug": profile.school.slug,
        "submission_id": application.pk}))
    assert page.status_code == 200
    assert (b'name="follow_up_delta"' in page.content) is not simplified


@pytest.mark.parametrize("simplified", [True, False])
def test_system_submission_status_remains_visible_without_becoming_a_manual_choice(
    integrated, monkeypatch, client, simplified,
):
    profile, user = integrated
    monkeypatch.setattr("scheduling.school_navigation.load_school_config", lambda slug: SimpleNamespace(raw={
        "scheduling": {"navigation": {"layout": "school", "simplified": simplified}}}))
    client.force_login(user)
    application = SubmissionFactory(school=profile.school, status="Playdate Scheduled")
    page = client.get(reverse("school_submission_detail", kwargs={
        "school_slug": profile.school.slug, "submission_id": application.pk}))
    assert page.status_code == 200 and page.context["status_is_system"]
    html = page.content.decode()
    assert "Playdate Scheduled (system)" in html
    assert html.count('aria-current="step"') == 1
    assert 'value="Playdate Scheduled"' not in html
    assert 'name="new_status" value="In Review"' in html


def test_follow_up_save_preserves_contact_interest_and_notes(integrated, client):
    profile, user = integrated
    client.force_login(user)
    lead = LeadFactory(school=profile.school, phone="5551234567", interested_in_value="piano",
        interested_in_label="Piano", notes="Keep these notes")
    route = reverse("school_lead_update", kwargs={"school_slug": profile.school.slug, "lead_id": lead.pk})
    data = {"name": lead.name, "email": lead.email, "next_follow_up_at": "2030-01-15"}
    assert client.post(route, data).status_code == 302
    lead.refresh_from_db()
    assert lead.next_follow_up_at.date().isoformat() == "2030-01-15"
    assert (lead.phone, lead.interested_in_value, lead.interested_in_label, lead.notes) == (
        "5551234567", "piano", "Piano", "Keep these notes")
    assert client.post(route, {**data, "next_follow_up_at": "invalid"}).status_code == 302
    lead.refresh_from_db()
    assert lead.next_follow_up_at.date().isoformat() == "2030-01-15"
    assert client.post(route, {"name": lead.name, "email": lead.email, "phone": ""}).status_code == 302
    lead.refresh_from_db()
    assert lead.phone == "" and lead.next_follow_up_at.date().isoformat() == "2030-01-15"
    assert client.post(route, {**data, "next_follow_up_at": ""}).status_code == 302
    lead.refresh_from_db()
    assert lead.next_follow_up_at is None


@pytest.mark.parametrize("extra,action,kind,expected", [
    ({"name": "submission_update", "fields": ["internal_notes"]}, "action", "application", "updated internal notes"),
    ({"name": "submission_update", "fields": ["student_name"]}, "action", "application", "updated application details"),
    ({}, "add", "application", "submitted application"),
    (None, "add", "lead", "created lead"),
    ({}, "change", "lead", "updated lead details"),
    ({}, "delete", "lead", "deleted lead"),
    ({"name": "unrecognized_event"}, "action", "lead", "recorded an activity"),
])
def test_audit_summary_does_not_invent_notes_or_expose_storage_keys(extra, action, kind, expected):
    assert record_audit_summary(SimpleNamespace(extra=extra, action=action), kind) == expected


def test_single_location_teacher_edit_preserves_hidden_travel_gap_and_login(school_music, client):
    m = school_music
    teacher = m["teacher"]
    teacher.travel_minutes = 37
    teacher.login_user = User.objects.create_user("existing-teacher")
    teacher.save()
    page = client.get(url(m, "lesson_setup_edit", kind="teachers", pk=teacher.pk))
    assert b'<details class="teacher-access">' in page.content
    assert page.context["form"]["login_username"].value() == "existing-teacher"
    assert b'name="travel_minutes"' not in page.content
    data = {"name": teacher.name, "services": [m["service"].pk], "buffer_minutes": 5,
        "travel_minutes": 0, "is_active": "on", "login_username": "existing-teacher",
        "edit_revision": page.context["form"]["edit_revision"].value()}
    assert client.post(url(m, "lesson_setup_edit", kind="teachers", pk=teacher.pk), data).status_code == 302
    teacher.refresh_from_db()
    assert teacher.travel_minutes == 37 and teacher.login_user.username == "existing-teacher"
    page = client.get(url(m, "lesson_setup_edit", kind="teachers", pk=teacher.pk))
    data.update(login_username="missing", edit_revision=page.context["form"]["edit_revision"].value())
    invalid = client.post(url(m, "lesson_setup_edit", kind="teachers", pk=teacher.pk), data)
    assert b'<details class="teacher-access" open>' in invalid.content
    teacher.refresh_from_db()
    assert teacher.login_user.username == "existing-teacher"


def test_standard_teacher_keeps_travel_field(music):
    assert "travel_minutes" in lessons.setup_form(music["profile"], "teachers", pk=music["teacher"].pk).fields


def test_reschedule_collapses_unchanged_resources_but_submits_them(music, school_layout, client):
    item = book(music).occurrences.first()
    client.force_login(music["user"])
    page = client.get(url(music, "lesson_change", pk=item.pk))
    assert b'<details class="lesson-change-details">' in page.content
    assert str(page.context["form"]["teacher"].value()) == str(music["teacher"].pk)
    assert b'<input type="hidden" name="scope"' in page.content
    school_layout["lesson_changes"] = "series"
    # A simplified presentation must not erase a separately enabled workflow.
    page = client.get(url(music, "lesson_change", pk=item.pk))
    assert b'<select name="scope"' in page.content


def test_staff_trial_has_one_date_control_without_public_date_shortcuts(music, offer, school_layout, client):
    client.force_login(music["user"])
    page = client.get(url(music, "staff_lesson_trial_book", public_id=offer.public_id))
    assert page.status_code == 200
    assert b'class="trial-dates"' not in page.content
    assert page.content.count(b'<input type="date" name="date"') == 1
    public = client.get(url(music, "lesson_trial_book", public_id=offer.public_id))
    assert public.status_code == 200 and b'class="trial-dates"' in public.content


def test_session_details_keep_schedule_active_while_catalogue_stays_settings(school_workspace):
    s = school_workspace
    workshop = session(s)
    program = camp(s)
    for route, pk in [("workshop_detail", workshop.offering_id), ("camp_detail", program.pk)]:
        page = s.client.get(url(s.__dict__, route, pk=pk))
        assert page.status_code == 200
        assert b'aria-current="page">Schedule</a>' in page.content
    page = s.client.get(url(s.__dict__, "school_programs"))
    assert b'aria-current="page">Settings</a>' in page.content
