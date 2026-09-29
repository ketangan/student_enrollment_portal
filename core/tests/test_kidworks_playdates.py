"""Kid Works admissions scheduling and isolation from SBMC's lesson workflow."""
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from django.urls import reverse

from core.models import AdminAuditLog, SchoolProgram, Submission
from core.services.config_loader import load_school_config
from core.services.playdates import available_slots, get_playdate_config, slot_label
from core.services.programs import apply_auto_enrollment
from core.tests.factories import SchoolFactory, SchoolAdminMembershipFactory, SubmissionFactory

SLUG = "kidworks-childrens-center"
SLOT = "2026-10-12T09:00:00-07:00"
OTHER_SLOT = "2026-10-14T10:00:00-07:00"
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("core.services.playdates.timezone.now", lambda: datetime(2026, 9, 28, 16, tzinfo=timezone.utc))


@pytest.fixture
def notify(monkeypatch):
    mock = Mock()
    monkeypatch.setattr("core.views_public._notify_schedule_change", mock)
    return mock


@pytest.fixture
def school():
    return SchoolFactory(slug=SLUG, plan="pro", program_field_key="program_interest")


@pytest.fixture
def submission(school):
    return SubmissionFactory(school=school, status="New", data={"child_first_name": "Avery", "child_last_name": "Demo", "guardian_email": "parent@example.com"})


def parent_url(submission, action="family_status"):
    return reverse(action, kwargs={"school_slug": submission.school.slug, "token": submission.status_token})


def schedule(client, submission, value=SLOT):
    return client.post(parent_url(submission, "school_status_change_request"), {"playdate_slot": value})


def test_parent_form_is_one_field_with_weekday_slots(client, submission):
    response = client.get(parent_url(submission))
    assert response.status_code == 200
    assert b"Playdate Schedule" in response.content
    assert b"Lesson Scheduling" not in response.content
    assert b"Weekend" not in response.content
    assert b'select id="playdate-slot"' in response.content
    assert b'name="sched_preferred_timing"' not in response.content
    assert len(response.context["playdate"]["options"]) == 3


def test_scheduling_sets_status_and_preserves_application(client, submission, notify):
    original_data = dict(submission.data)
    response = schedule(client, submission)
    assert response.status_code == 302
    assert response.url.endswith("?playdate=saved")
    submission.refresh_from_db()
    assert submission.status == "Playdate Scheduled"
    assert submission.data == {**original_data, "playdate_slot": SLOT}
    assert not submission.schedule_change_requested
    assert submission.schedule_change_requested_at
    notify.assert_not_called()
    assert AdminAuditLog.objects.filter(object_id=str(submission.pk), extra__name="playdate_scheduled").count() == 1
    response = client.get(parent_url(submission))
    assert b"Playdate Scheduled" in response.content
    assert b"Oct 12, 2026: 9-10 AM" in response.content
    assert b"PDT" not in response.content
    assert b"(Pacific)" not in response.content


def test_duplicate_post_is_idempotent(client, submission, notify):
    schedule(client, submission)
    schedule(client, submission)
    assert AdminAuditLog.objects.filter(object_id=str(submission.pk), extra__name="playdate_scheduled").count() == 1
    notify.assert_not_called()


def test_parent_can_reschedule_before_completion(client, submission, notify):
    schedule(client, submission)
    submission.refresh_from_db()
    submission.schedule_change_requested = True  # Legacy playdate awaiting acknowledgment.
    submission.save(update_fields=["schedule_change_requested"])
    schedule(client, submission, OTHER_SLOT)
    submission.refresh_from_db()
    assert submission.data["playdate_slot"] == OTHER_SLOT
    assert submission.status == "Playdate Scheduled"
    assert not submission.schedule_change_requested
    assert AdminAuditLog.objects.filter(object_id=str(submission.pk), extra__name="playdate_scheduled").count() == 2
    notify.assert_not_called()


@pytest.mark.parametrize("value", ["", "invalid", "2026-10-10T09:00:00-07:00", "2026-10-12T18:00:00-07:00", "2026-09-01T09:00:00-07:00"])
def test_invalid_unoffered_or_expired_slot_changes_nothing(client, submission, notify, value):
    response = schedule(client, submission, value)
    assert response.url.endswith("?playdate=invalid")
    submission.refresh_from_db()
    assert submission.status == "New"
    assert "playdate_slot" not in submission.data
    assert not submission.schedule_change_requested
    notify.assert_not_called()


@pytest.mark.parametrize("status", ["Playdate Completed", "Fee Pending", "Enrolled", "Waitlisted", "Declined", "Archived"])
def test_parent_cannot_regress_advanced_or_closed_application(client, submission, notify, status):
    submission.status = status
    submission.save()
    assert b'name="playdate_slot"' not in client.get(parent_url(submission)).content
    assert schedule(client, submission).url.endswith("?playdate=locked")
    submission.refresh_from_db()
    assert submission.status == status
    assert "playdate_slot" not in submission.data
    notify.assert_not_called()


def test_other_school_token_rejected(client, submission, notify):
    other = SchoolFactory(plan="pro")
    response = client.post(reverse("school_status_change_request", kwargs={"school_slug": other.slug, "token": submission.status_token}), {"playdate_slot": SLOT})
    assert response.status_code == 404
    notify.assert_not_called()


def test_csrf_required(submission):
    from django.test import Client
    assert schedule(Client(enforce_csrf_checks=True), submission).status_code == 403


def test_admin_sees_slot_completes_playdate_and_marks_fee_pending(client, submission, notify):
    schedule(client, submission)
    membership = SchoolAdminMembershipFactory(school=submission.school)
    client.force_login(membership.user)
    kwargs = {"school_slug": SLUG, "submission_id": submission.pk}
    response = client.get(reverse("school_submission_detail", kwargs=kwargs))
    assert response.status_code == 200
    assert b"Playdate Schedule" in response.content
    assert b"Oct 12, 2026: 9-10 AM" in response.content
    assert b"Lesson Scheduling" not in response.content
    assert b"Mark Acknowledged" not in response.content
    assert response.context["status_choices"] == ["New", "In Review", "Playdate Scheduled", "Playdate Completed", "Fee Pending", "Enrolled", "Waitlisted", "Declined", "Archived"]
    for status in ["Playdate Completed", "Fee Pending"]:
        response = client.post(reverse("school_submission_status_update", kwargs=kwargs), {"new_status": status})
        assert response.status_code == 302
        submission.refresh_from_db()
        assert submission.status == status
        assert submission.data["playdate_slot"] == SLOT
    assert submission.payment_status == ""


@pytest.mark.parametrize("saved_slot", ["", SLOT])
def test_admin_playdate_is_in_sidebar_without_legacy_acknowledgment(client, submission, saved_slot):
    submission.data["playdate_slot"] = saved_slot
    submission.schedule_change_requested = True
    submission.save()
    client.force_login(SchoolAdminMembershipFactory(school=submission.school).user)
    response = client.get(reverse("school_submission_detail", kwargs={"school_slug": SLUG, "submission_id": submission.pk}))
    html = response.content.decode()
    assert response.status_code == 200
    assert html.count('id="playdate-schedule"') == 1
    assert html.index("/LEFT") < html.index('id="playdate-schedule"') < html.index("/RIGHT")
    assert html.index('>Actions</h2>') < html.index('id="playdate-schedule"') < html.index('>Internal Notes</h2>')
    assert "Mark Acknowledged" not in html
    assert "Schedule Updated" not in html
    if not saved_slot:
        assert "No playdate scheduled yet." in html
    response = client.get(reverse("school_submissions", kwargs={"school_slug": SLUG}))
    assert response.status_code == 200
    assert b"Schedule change requested by family" not in response.content


def test_legacy_status_preserved_but_not_offered(client, submission):
    submission.status = "Contacted"
    submission.save()
    membership = SchoolAdminMembershipFactory(school=submission.school)
    client.force_login(membership.user)
    response = client.get(reverse("school_submission_detail", kwargs={"school_slug": SLUG, "submission_id": submission.pk}))
    assert response.status_code == 200
    assert "Contacted" not in response.context["status_choices"]
    submission.refresh_from_db()
    assert submission.status == "Contacted"


def test_program_label_not_instrument(client, school, submission):
    submission.program = SchoolProgram.objects.create(school=school, code="garden", name="Garden Room")
    submission.save()
    response = client.get(parent_url(submission))
    assert {"label": "Program", "value": "Garden Room"} in response.context["student_info"]


def test_demo_does_not_email_the_real_school(client, submission, notify):
    notify.side_effect = RuntimeError("Email unavailable")
    assert schedule(client, submission).status_code == 302
    submission.refresh_from_db()
    assert submission.status == "Playdate Scheduled"
    notify.assert_not_called()


def test_even_configured_weekend_evening_and_naive_slots_are_excluded():
    config = {"timezone": "America/Los_Angeles", "slots": [SLOT, "2026-10-10T09:00:00-07:00", "2026-10-12T17:00:00-07:00", "2026-10-12T09:00:00", "2026-09-01T09:00:00-07:00"]}
    assert [s["value"] for s in available_slots(config)] == [SLOT]
    assert slot_label(SLOT, config) == "Mon, Oct 12, 2026: 9-10 AM"


def admin_schedule(client, submission, value=SLOT):
    return client.post(reverse("school_submission_schedule_playdate", kwargs={
        "school_slug": submission.school.slug, "submission_id": submission.pk,
    }), {"playdate_slot": value})


def test_admin_schedules_and_reschedules_without_touching_other_data(client, submission, notify):
    membership = SchoolAdminMembershipFactory(school=submission.school, role="owner")
    client.force_login(membership.user)
    old_data = dict(submission.data)
    submission.internal_notes = "Keep this note"
    submission.save()
    response = client.get(reverse("school_submission_detail", kwargs={"school_slug": SLUG, "submission_id": submission.pk}))
    assert b'id="admin-playdate-slot"' in response.content
    assert b"Mark Acknowledged" not in response.content
    assert admin_schedule(client, submission).status_code == 302
    submission.refresh_from_db()
    assert submission.status == "Playdate Scheduled"
    assert submission.data == {**old_data, "playdate_slot": SLOT}
    assert submission.internal_notes == "Keep this note"
    assert not submission.schedule_change_requested
    assert AdminAuditLog.objects.filter(object_id=str(submission.pk), actor=membership.user, extra__name="playdate_scheduled").count() == 1
    admin_schedule(client, submission)
    assert AdminAuditLog.objects.filter(object_id=str(submission.pk), extra__name="playdate_scheduled").count() == 1
    admin_schedule(client, submission, OTHER_SLOT)
    submission.refresh_from_db()
    assert submission.data["playdate_slot"] == OTHER_SLOT
    assert b"Oct 14, 2026: 10-11 AM" in client.get(parent_url(submission)).content
    notify.assert_not_called()


@pytest.mark.parametrize("status", ["Playdate Completed", "Fee Pending", "Enrolled", "Waitlisted", "Declined", "Archived"])
def test_admin_playdate_cannot_regress_status(client, submission, status):
    submission.status = status
    submission.save()
    client.force_login(SchoolAdminMembershipFactory(school=submission.school).user)
    admin_schedule(client, submission)
    submission.refresh_from_db()
    assert submission.status == status
    assert "playdate_slot" not in submission.data
    response = client.get(reverse("school_submission_detail", kwargs={"school_slug": SLUG, "submission_id": submission.pk}))
    assert b'id="admin-playdate-slot"' not in response.content


@pytest.mark.parametrize("value", ["", "2026-10-10T09:00:00-07:00", "2026-09-01T09:00:00-07:00"])
def test_admin_rejects_unoffered_or_expired_slot(client, submission, value):
    client.force_login(SchoolAdminMembershipFactory(school=submission.school).user)
    admin_schedule(client, submission, value)
    submission.refresh_from_db()
    assert submission.status == "New"
    assert "playdate_slot" not in submission.data


def test_admin_playdate_authorization_and_csrf(client, submission):
    from django.test import Client
    viewer = SchoolAdminMembershipFactory(school=submission.school, role="viewer")
    client.force_login(viewer.user)
    assert admin_schedule(client, submission).status_code == 404
    other = SchoolAdminMembershipFactory(school=SchoolFactory())
    client.force_login(other.user)
    assert admin_schedule(client, submission).status_code == 404
    owner = SchoolAdminMembershipFactory(school=submission.school)
    client.force_login(owner.user)
    endpoint = reverse("school_submission_schedule_playdate", kwargs={"school_slug": SLUG, "submission_id": submission.pk})
    assert client.get(endpoint).status_code == 405
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(owner.user)
    assert admin_schedule(csrf_client, submission).status_code == 403


def test_sbmc_admin_cannot_access_playdate_endpoint(client):
    school = SchoolFactory(slug="south-bay-music", plan="pro")
    submission = SubmissionFactory(school=school)
    client.force_login(SchoolAdminMembershipFactory(school=school).user)
    assert admin_schedule(client, submission).status_code == 404


def test_kidworks_confirmation_explains_playdate_not_another_tour():
    raw = load_school_config(SLUG).raw
    assert "schedule a center tour" not in " ".join(raw["success"]["next_steps"])
    assert "does not confirm enrollment" in raw["success"]["notifications"]["applicant_confirmation"]["message"]


def test_sbmc_retains_lesson_preferences_and_status(client, notify):
    school = SchoolFactory(slug="south-bay-music", plan="pro")
    submission = SubmissionFactory(school=school, status="Enrolled")
    response = client.get(parent_url(submission))
    assert b"Lesson Scheduling Preferences" in response.content
    assert b"Weekend" in response.content
    assert b"Playdate Schedule" not in response.content
    client.post(parent_url(submission, "school_status_change_request"), {"sched_preferred_timing": "After 5pm", "sched_day_preference": "weekend"})
    submission.refresh_from_db()
    assert submission.status == "Enrolled"
    assert submission.data["sched_preferred_timing"] == "After 5pm"
    assert submission.data["sched_day_preference"] == "weekend"
    assert submission.schedule_change_requested
    client.force_login(SchoolAdminMembershipFactory(school=school).user)
    response = client.get(reverse("school_submission_detail", kwargs={"school_slug": school.slug, "submission_id": submission.pk}))
    assert b"Mark Acknowledged" in response.content
    assert b'id="playdate-schedule"' not in response.content
    assert get_playdate_config(load_school_config("south-bay-music").raw) is None


def test_approval_flag_blocks_auto_enrollment_only_when_opted_in(school, submission):
    program = SchoolProgram.objects.create(school=school, code="garden", name="Garden", auto_enroll=True)
    apply_auto_enrollment(school, submission, program, config_raw=load_school_config(SLUG).raw)
    submission.refresh_from_db()
    assert submission.status == "New"
    apply_auto_enrollment(school, submission, program, config_raw=load_school_config("south-bay-music").raw)
    submission.refresh_from_db()
    assert submission.status == "Enrolled"


def test_public_kidworks_application_stays_new_despite_program_auto_enroll(client, school, monkeypatch):
    for name in ["send_submission_notification_email", "send_applicant_confirmation_email"]:
        monkeypatch.setattr("core.views_public." + name, Mock())
    SchoolProgram.objects.create(school=school, code="garden", name="Garden Room", auto_enroll=True)
    response = client.post(reverse("apply", kwargs={"school_slug": SLUG}), {
        "child_first_name": "Avery", "child_last_name": "Demo", "date_of_birth": "2023-01-02",
        "program_interest": "program:garden", "guardian_name": "Test Parent", "guardian_email": "parent@example.com",
        "guardian_phone": "3105550123", "enrollment_policies": "on", "photo_release": "deny", "emergency_authorization": "on",
    })
    assert response.status_code == 302
    submission = Submission.objects.get(school=school)
    assert submission.status == "New"
    assert submission.program.code == "garden"
