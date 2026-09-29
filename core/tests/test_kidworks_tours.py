"""Opt-in tour scheduling, with explicit SBMC isolation checks."""
from copy import deepcopy

import pytest
from django.urls import reverse

from core.models import AdminAuditLog, Lead, LEAD_STATUS_CHOICES
from core.services.admin_lead_yaml import get_lead_status_choices
from core.services.config_loader import load_school_config
from core.services.lead_appointments import appointment_display
from core.tests.factories import LeadFactory, SchoolFactory, SchoolAdminMembershipFactory


SLUG = "kidworks-childrens-center"
SLOT = "2026-10-06T09:30:00-07:00"
SLOT_LABEL = "Tue, Oct 6, 2026 at 9:30 AM"
NEXT_SLOT = "2026-10-08T10:30:00-07:00"
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    monkeypatch.setattr("core.views_public._send_lead_notifications_async", lambda *a, **kw: None)


@pytest.fixture
def school():
    return SchoolFactory(slug=SLUG, display_name="Kid Works Children's Center", plan="pro")


@pytest.fixture
def admin(client, school):
    membership = SchoolAdminMembershipFactory(school=school, role="owner")
    client.force_login(membership.user)
    return membership.user


@pytest.fixture
def lead(school):
    return LeadFactory(school=school, name="Demo Parent", email="parent@example.com",
                       status="trial_scheduled", data={"form_fields": {"tour_slot": SLOT}})


def url(name, school, lead=None):
    kwargs = {"school_slug": school.slug}
    if lead:
        kwargs["lead_id"] = lead.pk
    return reverse(name, kwargs=kwargs)


def parent_data(**changes):
    return {"name": "Demo Parent", "email": "parent@example.com", "phone": "3105550123",
            "tour_slot": SLOT, **changes}


def edit_data(lead, **changes):
    return {"name": lead.name, "email": lead.email, "phone": lead.phone,
            "interested_in_value": lead.interested_in_value, **changes}


def test_tour_form_and_submission(client, school):
    response = client.get(url("school_lead_form", school))
    assert response.status_code == 200
    assert b"Schedule a Tour" in response.content
    assert SLOT_LABEL.encode() in response.content
    assert b"(Pacific)" not in response.content
    response = client.post(url("school_lead_form", school), parent_data())
    assert response.status_code == 200
    assert b"Your demo tour is scheduled" in response.content
    lead = Lead.objects.get(school=school)
    assert lead.status == "trial_scheduled"
    assert lead.data["form_fields"]["tour_slot"] == SLOT


@pytest.mark.parametrize("slot", ["", "not-a-slot", "2027-01-01T09:30:00-08:00"])
def test_missing_or_unoffered_slot_rejected(client, school, slot):
    response = client.post(url("school_lead_form", school), parent_data(tour_slot=slot))
    assert b"Please select one of the available times." in response.content
    assert not Lead.objects.filter(school=school).exists()


def test_honeypot_does_not_schedule(client, school):
    client.post(url("school_lead_form", school), parent_data(trap_field="bot"))
    assert not Lead.objects.filter(school=school).exists()


def test_auto_confirm_is_explicit_opt_in(client, school, monkeypatch):
    config = deepcopy(load_school_config(SLUG))
    config.raw["leads"].pop("appointment_auto_confirm")
    monkeypatch.setattr("core.views_public.load_school_config", lambda *a, **kw: config)
    client.post(url("school_lead_form", school), parent_data())
    assert Lead.objects.get(school=school).status == "new"


def test_list_shows_tour_column_labels_and_correct_capture_link(client, school, admin, lead):
    response = client.get(url("school_leads", school))
    assert response.status_code == 200
    assert b'data-col="appointment"' in response.content
    assert b"appointment: true" in response.content
    assert SLOT_LABEL.encode() in response.content
    assert b"Tour Scheduled" in response.content
    assert b"Tour Completed" in response.content
    assert b"Trial Scheduled" not in response.content
    assert response.context["lead_capture_url"].endswith(url("school_lead_form", school))


def test_detail_and_complete_tour_preserve_booking(client, school, admin, lead):
    response = client.get(url("school_lead_detail", school, lead))
    assert response.status_code == 200
    assert b'data-testid="lead-appointment"' in response.content
    assert SLOT_LABEL.encode() in response.content
    assert b"Tour Scheduled" in response.content
    assert b'title="Move to Tour Completed"' in response.content
    response = client.post(url("school_lead_status_update", school, lead), {"new_status": "trial_completed"})
    assert response.status_code == 302
    lead.refresh_from_db()
    assert lead.status == "trial_completed"
    assert lead.data["form_fields"]["tour_slot"] == SLOT
    response = client.get(url("school_lead_detail", school, lead))
    assert b"Tour Completed" in response.content


def test_notes_edit_does_not_clear_booking(client, school, admin, lead):
    client.post(url("school_lead_update", school, lead), edit_data(lead, new_note="Tour reminder sent."))
    lead.refresh_from_db()
    assert lead.data["form_fields"]["tour_slot"] == SLOT
    assert "Tour reminder sent." in lead.notes


def test_admin_can_change_to_offered_slot(client, school, admin, lead):
    client.post(url("school_lead_update", school, lead), edit_data(lead, field__tour_slot=NEXT_SLOT))
    lead.refresh_from_db()
    assert lead.data["form_fields"]["tour_slot"] == NEXT_SLOT


@pytest.mark.parametrize("slot", ["", "unoffered"])
def test_invalid_admin_slot_rejects_entire_edit(client, school, admin, lead, slot):
    original_name = lead.name
    client.post(url("school_lead_update", school, lead), edit_data(lead, name="Changed", field__tour_slot=slot))
    lead.refresh_from_db()
    assert lead.name == original_name
    assert lead.data["form_fields"]["tour_slot"] == SLOT


def test_historical_slot_remains_visible_and_editable(client, school, admin, lead):
    historical = "2026-09-01T09:30:00-07:00"
    lead.data["form_fields"]["tour_slot"] = historical
    lead.save()
    response = client.get(url("school_lead_detail", school, lead))
    field = next(f for f in response.context["form_fields_editable"] if f["key"] == "tour_slot")
    assert any(o["value"] == historical for o in field["options"])
    client.post(url("school_lead_update", school, lead), edit_data(lead, field__tour_slot=historical, name="Updated Parent"))
    lead.refresh_from_db()
    assert lead.name == "Updated Parent"
    assert lead.data["form_fields"]["tour_slot"] == historical


def test_older_lead_without_slot_is_not_fabricated(client, school, admin, lead):
    lead.data = {}
    lead.status = "new"
    lead.save()
    for name, record in [("school_leads", None), ("school_lead_detail", lead)]:
        response = client.get(url(name, school, record))
        assert response.status_code == 200
        assert b"No time selected" in response.content
    lead.refresh_from_db()
    assert lead.status == "new"


@pytest.mark.parametrize("initial_status", ["new", "contacted", "trial_scheduled"])
def test_admin_schedules_missing_tour_without_changing_other_fields(client, school, admin, lead, initial_status):
    from django.utils import timezone
    lead.status = initial_status
    lead.data = {"message": "Keep this", "form_fields": {"other": "Keep this too"}}
    lead.notes = "Existing notes"
    lead.next_follow_up_at = timezone.now()
    lead.save()
    before = (lead.name, lead.email, lead.phone, lead.notes, lead.next_follow_up_at)
    response = client.get(url("school_lead_detail", school, lead))
    assert b'id="admin-tour-slot"' in response.content
    assert response.context["lead_appointment_editor"]["options"] == load_school_config(school.slug).raw["leads"]["fields"][0]["options"]
    endpoint = url("school_lead_schedule_tour", school, lead)
    response = client.post(endpoint, {"appointment_slot": SLOT})
    assert response.status_code == 302
    assert response.url == url("school_lead_detail", school, lead)
    lead.refresh_from_db()
    assert lead.status == "trial_scheduled"
    assert lead.data == {"message": "Keep this", "form_fields": {"other": "Keep this too", "tour_slot": SLOT}}
    assert (lead.name, lead.email, lead.phone, lead.notes, lead.next_follow_up_at) == before
    client.post(endpoint, {"appointment_slot": SLOT})
    assert AdminAuditLog.objects.filter(object_id=str(lead.pk), extra__name="lead_tour_scheduled").count() == 1
    client.post(endpoint, {"appointment_slot": NEXT_SLOT})
    lead.refresh_from_db()
    assert lead.data["form_fields"]["tour_slot"] == NEXT_SLOT
    response = client.get(url("school_lead_detail", school, lead))
    assert b"rescheduled a tour" in response.content
    assert b"Lead_Tour_Scheduled" not in response.content
    response = client.get(url("school_leads", school))
    assert b"Thu, Oct 8, 2026 at 10:30 AM" in response.content


def test_edit_details_also_advances_a_new_tour(client, school, admin, lead):
    lead.data = {}
    lead.status = "new"
    lead.save()
    client.post(url("school_lead_update", school, lead), edit_data(lead, field__tour_slot=SLOT))
    lead.refresh_from_db()
    assert lead.status == "trial_scheduled"
    assert lead.data["form_fields"]["tour_slot"] == SLOT


@pytest.mark.parametrize("slot", ["", "not-offered"])
def test_sidebar_rejects_invalid_tour(client, school, admin, lead, slot):
    client.post(url("school_lead_schedule_tour", school, lead), {"appointment_slot": slot})
    lead.refresh_from_db()
    assert lead.data["form_fields"]["tour_slot"] == SLOT
    assert not AdminAuditLog.objects.filter(extra__name="lead_tour_scheduled").exists()


@pytest.mark.parametrize("status", ["trial_completed", "enrolled", "lost"])
def test_tour_changes_cannot_regress_advanced_leads(client, school, admin, lead, status):
    lead.status = status
    lead.save()
    response = client.get(url("school_lead_detail", school, lead))
    assert b'id="admin-tour-slot"' not in response.content
    client.post(url("school_lead_schedule_tour", school, lead), {"appointment_slot": NEXT_SLOT})
    client.post(url("school_lead_update", school, lead), edit_data(lead, field__tour_slot=NEXT_SLOT))
    lead.refresh_from_db()
    assert lead.status == status
    assert lead.data["form_fields"]["tour_slot"] == SLOT


def test_sidebar_tour_permissions_and_csrf(client, school, lead):
    from django.test import Client
    viewer = SchoolAdminMembershipFactory(school=school, role="viewer")
    client.force_login(viewer.user)
    endpoint = url("school_lead_schedule_tour", school, lead)
    assert client.post(endpoint, {"appointment_slot": SLOT}).status_code == 404
    assert b'id="admin-tour-slot"' not in client.get(url("school_lead_detail", school, lead)).content
    other_admin = SchoolAdminMembershipFactory(school=SchoolFactory(), role="owner")
    client.force_login(other_admin.user)
    assert client.post(endpoint, {"appointment_slot": SLOT}).status_code in (403, 404)
    owner = SchoolAdminMembershipFactory(school=school, role="owner")
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner.user)
    assert csrf_client.post(endpoint, {"appointment_slot": SLOT}).status_code == 403
    client.force_login(owner.user)
    assert client.get(endpoint).status_code == 405


def test_sidebar_tour_endpoint_disabled_for_sbmc(client):
    school = SchoolFactory(slug="south-bay-music", plan="pro")
    lead = LeadFactory(school=school)
    client.force_login(SchoolAdminMembershipFactory(school=school, role="owner").user)
    assert client.post(url("school_lead_schedule_tour", school, lead), {"appointment_slot": SLOT}).status_code == 404


def test_converted_lead_cannot_schedule_even_with_stale_status(client, school, admin, lead):
    from core.tests.factories import SubmissionFactory
    lead.converted_submission = SubmissionFactory(school=school)
    lead.status = "new"
    lead.save()
    assert b'id="admin-tour-slot"' not in client.get(url("school_lead_detail", school, lead)).content
    client.post(url("school_lead_schedule_tour", school, lead), {"appointment_slot": NEXT_SLOT})
    lead.refresh_from_db()
    assert lead.status == "new"
    assert lead.data["form_fields"]["tour_slot"] == SLOT


def test_viewer_sees_tour_label_without_edit_controls(client, school, lead):
    membership = SchoolAdminMembershipFactory(school=school, role="viewer")
    client.force_login(membership.user)
    response = client.get(url("school_leads", school))
    assert response.status_code == 200
    assert b"Tour Scheduled" in response.content
    assert b'<select name="new_status"' not in response.content
    response = client.post(url("school_lead_update", school, lead), edit_data(lead, field__tour_slot=NEXT_SLOT))
    assert response.status_code in (403, 404)
    lead.refresh_from_db()
    assert lead.data["form_fields"]["tour_slot"] == SLOT


def test_other_school_admin_cannot_read_or_change_tour(client, school, lead):
    membership = SchoolAdminMembershipFactory()
    client.force_login(membership.user)
    assert client.get(url("school_lead_detail", school, lead)).status_code in (403, 404)
    response = client.post(url("school_lead_update", school, lead), edit_data(lead, field__tour_slot=NEXT_SLOT))
    assert response.status_code in (403, 404)
    lead.refresh_from_db()
    assert lead.data["form_fields"]["tour_slot"] == SLOT


def test_sbmc_has_no_tour_controls_or_status_overrides(client):
    school = SchoolFactory(slug="south-bay-music", plan="pro")
    lead = LeadFactory(school=school, status="trial_scheduled")
    membership = SchoolAdminMembershipFactory(school=school)
    response = client.get(url("school_lead_form", school))
    assert b"Book a Trial Lesson" in response.content
    assert b"tour_slot" not in response.content
    client.force_login(membership.user)
    for name, record in [("school_leads", None), ("school_lead_detail", lead)]:
        response = client.get(url(name, school, record))
        assert response.status_code == 200
        assert b"Trial Scheduled" in response.content
        assert b"Tour Scheduled" not in response.content
        assert b'data-col="appointment"' not in response.content
        assert b'data-testid="lead-appointment"' not in response.content
    assert get_lead_status_choices(load_school_config(school.slug).raw) == list(LEAD_STATUS_CHOICES)


@pytest.mark.parametrize("labels", [None, [], {"trial_scheduled": "", "trial_completed": 123}])
def test_invalid_label_config_preserves_defaults(labels):
    assert get_lead_status_choices({"admin": {"lead_workflow": {"status_labels": labels}}}) == list(LEAD_STATUS_CHOICES)


def test_appointment_display_handles_older_data():
    field = load_school_config(SLUG).raw["leads"]["fields"][0]
    assert appointment_display(field, None) == ""
    assert appointment_display(field, {"form_fields": None}) == ""
