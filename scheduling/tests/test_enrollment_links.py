from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db.models.deletion import ProtectedError
from django.db import close_old_connections
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import RequestFactory
from django.urls import reverse

from core.models import AdminAuditLog, DraftSubmission, Lead, School, SchoolAdminMembership, Submission
from core.services.lead_conversion import try_convert_lead
from core.services.config_loader import SchoolConfig, load_school_config
from scheduling import admissions, enrollment_links, school_navigation
from scheduling.admission_forms import PolicyForm
from scheduling.models import AdmissionCase
from scheduling.tests.test_admissions import workflow, tour, completed_tour, core_url, submitted_case
from scheduling.tests.test_appointments import music, offer, booked

pytestmark = pytest.mark.django_db


@pytest.fixture
def enable_policy(monkeypatch):
    original = school_navigation.workflow_policy

    def enable(**flags):
        def policy(school):
            return {**original(school), **flags}
        monkeypatch.setattr(school_navigation, "workflow_policy", policy)
        monkeypatch.setattr(enrollment_links, "workflow_policy", policy)
    return enable


def test_direct_entry_keeps_policy_and_does_not_convert_email_match(workflow, client, enable_policy):
    w = workflow
    enable_policy(direct_enrollment=True)
    lead = Lead.objects.create(school=w.school, name="Other child", email="taylor@example.invalid")
    assert client.get(core_url(w, "apply")).status_code == 200
    response = client.post(core_url(w, "apply"), {
        "student_name": "Sam", "guardian_name": "Taylor", "contact_email": lead.email})
    assert response.status_code == 302
    assert Submission.objects.filter(school=w.school).count() == 1
    assert not AdmissionCase.objects.exists()
    lead.refresh_from_db()
    w.policy.refresh_from_db()
    assert lead.converted_submission_id is None
    assert w.policy.enabled and w.policy.require_tour


def test_direct_entry_preserves_case_and_signed_resume(workflow, client, enable_policy):
    w = workflow
    case = submitted_case(w, client)
    enable_policy(direct_enrollment=True)
    response = client.get(core_url(w, "apply_resume", token=case.draft.token))
    assert response.url == core_url(w, "family_status", token=case.submission.status_token)
    assert AdmissionCase.objects.get(pk=case.pk).submission_id == case.submission_id


def test_fresh_direct_entry_does_not_consume_stale_case_draft(workflow, client, enable_policy):
    w = workflow
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    session = client.session
    session[f"apply_draft_id:{w.school.slug}"] = draft.pk
    session.save()
    enable_policy(direct_enrollment=True)
    response = client.get(core_url(w, "apply"))
    assert response.status_code == 200 and not response.context["draft_token"]
    response = client.post(core_url(w, "apply"), {
        "student_name": "Casey Learning", "guardian_name": "New guardian", "contact_email": case.lead.email})
    assert response.status_code == 302
    case.refresh_from_db()
    draft.refresh_from_db()
    assert case.submission_id is None and case.draft_id == draft.pk
    assert draft.submitted_at is None
    submission = Submission.objects.get(school=w.school)
    status = client.get(core_url(w, "family_status", token=submission.status_token))
    assert status.context["direct_application"] is True
    assert status.context["booking_admissions"] is None
    assert b"Playdate" not in status.content
    assert b"Lesson Scheduling Preferences" not in status.content
    resumed = client.get(core_url(w, "apply_resume", token=draft.token), follow=True)
    assert resumed.status_code == 200
    assert resumed.context["draft_token"] == draft.token
    assert resumed.redirect_chain[-1][0].endswith("?resume=1")


def test_status_keeps_historical_case_panel_after_direct_entry_opt_in(workflow, client, enable_policy):
    w = workflow
    case = submitted_case(w, client)
    enable_policy(direct_enrollment=True)
    response = client.get(core_url(w, "family_status", token=case.submission.status_token))
    assert response.context["direct_application"] is False
    assert response.context["booking_admissions"]["case"].pk == case.pk
    assert b"Playdate" in response.content


def test_unbound_direct_status_ignores_legacy_playdate_default(workflow, client, enable_policy):
    w = workflow
    enable_policy(direct_enrollment=True)
    submission = Submission.objects.create(school=w.school, data={"student_name": "Direct student"})
    with patch("core.services.playdates.get_playdate_config", return_value={"enabled": True, "slots": []}):
        response = client.get(core_url(w, "family_status", token=submission.status_token))
    assert response.context["playdate"] is None and response.context["booking_admissions"] is None
    assert b"Playdate" not in response.content


@pytest.mark.parametrize("direct", [True, False])
def test_direct_status_uses_program_and_configured_choice_labels_only_when_opted_in(workflow, client, enable_policy, direct):
    w = workflow
    enable_policy(direct_enrollment=direct)
    submission = Submission.objects.create(school=w.school, data={
        "student_name": "Rowan Learning", "instrument": "Piano", "enrollment_type": "enroll_now"})
    config = load_school_config(w.school.slug)
    config = SchoolConfig(raw={**config.raw, "form": {"sections": [{"fields": [{
        "key": "enrollment_type", "type": "select", "options": [{"value": "enroll_now", "label": "Enroll now"}]
    }]}]}})
    with patch("core.views_public.load_school_config", return_value=config):
        response = client.get(core_url(w, "family_status", token=submission.status_token))
    assert response.status_code == 200
    info = {item["label"]: item["value"] for item in response.context["student_info"]}
    assert info["Program" if direct else "Instrument"] == "Piano"
    assert info["Enrollment Type"] == ("Enroll now" if direct else "enroll_now")
    assert ("Instrument" if direct else "Program") not in info


def test_direct_entry_does_not_release_existing_case(workflow, client, enable_policy):
    w = workflow
    case = completed_tour(w)
    draft = admissions.prepare_draft(case)
    w.policy.application_release = "staff"
    w.policy.save()
    enable_policy(direct_enrollment=True)
    assert client.get(core_url(w, "apply_resume", token=draft.token)).status_code == 403
    assert not admissions.lead_context(case.lead)["allowed"]
    unrelated = DraftSubmission.objects.create(school=w.school)
    session = client.session
    session[f"apply_draft_id:{w.school.slug}"] = draft.pk
    session.save()
    assert client.post(core_url(w, "apply"), {"_draft_token": unrelated.token}).status_code == 403
    assert not Submission.objects.exists()


def test_direct_new_lead_uses_existing_start_enrollment(workflow, client, enable_policy):
    w = workflow
    enable_policy(direct_enrollment=True)
    lead = Lead.objects.create(school=w.school, name="New family", email="new@example.invalid")
    assert admissions.lead_context(lead) is None
    client.force_login(w.user)
    response = client.post(core_url(w, "school_lead_start_enrollment", lead_id=lead.pk))
    assert response.status_code == 302
    assert DraftSubmission.objects.filter(lead=lead).count() == 1
    assert not AdmissionCase.objects.exists()


def test_historical_policy_keeps_saved_status_choices(workflow):
    w = workflow
    completed_tour(w)
    with patch("scheduling.admission_forms.get_submission_status_choices", return_value=(["New"], "New")):
        form = PolicyForm(profile=w.profile, instance=w.policy)
    assert ("Playdate Scheduled", "Playdate Scheduled") in form.fields["scheduled_status"].choices
    assert ("Playdate Completed", "Playdate Completed") in form.fields["completed_status"].choices
    assert ("In Review", "In Review") in form.fields["eligible_statuses"].choices
    assert ("Playdate Scheduled", "Playdate Scheduled") not in form.fields["eligible_statuses"].choices


def test_direct_off_preserves_gate_and_legacy_email_conversion(workflow, client, enable_policy):
    w = workflow
    enable_policy(direct_enrollment=False)
    assert client.get(core_url(w, "apply")).status_code == 403
    lead = Lead.objects.create(school=w.school, name="Legacy", email="legacy@example.invalid")
    submission = Submission.objects.create(school=w.school, data={"contact_email": lead.email})
    config = load_school_config(w.school.slug).raw
    assert try_convert_lead(school=w.school, submission=submission, config_raw=config).pk == lead.pk


@pytest.fixture
def linking(music, offer, enable_policy):
    m = music
    school = m["profile"].school
    school.slug = "preschool-demo"
    school.plan = "custom"
    school.save()
    m["slug"] = school.slug
    m["user"].is_staff = True
    m["user"].save()
    membership = SchoolAdminMembership.objects.create(school=school, user=m["user"], role="editor")
    enable_policy(direct_enrollment=True, enrollment_linking=True)
    booking, _, _ = booked(m, offer)
    submission = Submission.objects.create(school=school, data={"student_name": "Verified student"})
    request = RequestFactory().post("/")
    request.user = m["user"]
    return m, booking, submission, request, membership


def link_data(submission, **overrides):
    return {"application": submission.public_id, "verified": "on", **overrides}


@pytest.mark.parametrize("viewer", [False, True])
def test_application_detail_links_related_trial_for_authorized_readers(linking, client, viewer):
    m, booking, submission, _, membership = linking
    booking.enrollment_submission = submission
    booking.save(update_fields=["enrollment_submission"])
    if viewer:
        membership.role = "viewer"
        membership.save()
        m["member"].role = "viewer"
        m["member"].save()
    client.force_login(m["user"])
    response = client.get(enrollment_links.submission_url(submission))
    trial_url = reverse("scheduling:lesson_booking_detail", kwargs={
        "school_slug": m["slug"], "public_id": booking.public_id})
    assert response.status_code == 200
    assert [trial["url"] for trial in response.context["related_trials"]] == [trial_url]
    assert f'href="{trial_url}"'.encode() in response.content
    assert client.get(trial_url).status_code == 200


@pytest.mark.parametrize("denial", ["unlinked", "flag", "inactive", "location_restricted", "lessons_disabled"])
def test_application_detail_omits_trial_without_linking_and_lesson_access(linking, client, enable_policy, denial):
    m, booking, submission, _, _ = linking
    if denial != "unlinked":
        booking.enrollment_submission = submission
        booking.save(update_fields=["enrollment_submission"])
    if denial == "flag":
        enable_policy(enrollment_linking=False)
    elif denial == "inactive":
        m["member"].is_active = False
        m["member"].save()
    elif denial == "location_restricted":
        m["member"].all_locations = False
        m["member"].save()
    elif denial == "lessons_disabled":
        m["profile"].lessons_enabled = False
        m["profile"].save()
    client.force_login(m["user"])
    response = client.get(enrollment_links.submission_url(submission))
    assert response.status_code == 200
    assert response.context["related_trials"] == []
    assert b"related-trials-heading" not in response.content


def test_related_trials_require_enrollment_access_too(linking, client):
    m, booking, submission, request, membership = linking
    booking.enrollment_submission = submission
    booking.save(update_fields=["enrollment_submission"])
    membership.is_active = False
    membership.save()
    assert enrollment_links.related_trials(request, submission) == []
    client.force_login(m["user"])
    assert client.get(enrollment_links.submission_url(submission)).status_code == 404


@pytest.mark.parametrize("foreign_relation", ["series", "offer", "submission"])
def test_related_trials_exclude_cross_tenant_links(linking, foreign_relation):
    from scheduling.models import SchedulingProfile

    _, booking, submission, request, _ = linking
    foreign_school = School.objects.create(slug="foreign-related-trial")
    foreign_profile = SchedulingProfile.objects.create(school=foreign_school)
    booking.enrollment_submission = submission
    booking.save(update_fields=["enrollment_submission"])
    # Bypass validation to ensure even corrupt stored relationships cannot leak.
    if foreign_relation == "submission":
        foreign_submission = Submission.objects.create(school=foreign_school)
        type(booking).objects.filter(pk=booking.pk).update(enrollment_submission=foreign_submission)
        assert enrollment_links.related_trials(request, foreign_submission) == []
    else:
        related = getattr(booking, foreign_relation)
        type(related).objects.filter(pk=related.pk).update(profile=foreign_profile)
    assert enrollment_links.related_trials(request, submission) == []


def test_explicit_link_is_idempotent_audited_and_protected(linking):
    m, booking, submission, request, _ = linking
    snapshot = dict(booking.series.snapshot)
    people = (booking.series.student_id, booking.series.student.household_id)
    first, form = enrollment_links.link_existing(request, m["slug"], booking.public_id, link_data(submission))
    assert form.is_valid() and first.enrollment_submission_id == submission.pk
    count = AdminAuditLog.objects.count()
    _, repeat = enrollment_links.link_existing(request, m["slug"], booking.public_id, link_data(submission))
    assert repeat.is_valid() and AdminAuditLog.objects.count() == count
    booking.refresh_from_db()
    assert booking.series.snapshot == snapshot
    assert (booking.series.student_id, booking.series.student.household_id) == people
    assert not AdmissionCase.objects.exists()
    with pytest.raises(ProtectedError):
        submission.delete()


def test_link_rejects_unverified_foreign_and_replacement(linking):
    m, booking, submission, request, _ = linking
    other = Submission.objects.create(school=School.objects.create(slug="other-link-school"))
    for data in [link_data(submission, verified=""), link_data(other), {"verified": "on"}]:
        _, form = enrollment_links.link_existing(request, m["slug"], booking.public_id, data)
        assert not form.is_valid()
    enrollment_links.link_existing(request, m["slug"], booking.public_id, link_data(submission))
    replacement = Submission.objects.create(school=submission.school)
    _, form = enrollment_links.link_existing(request, m["slug"], booking.public_id, link_data(replacement))
    assert not form.is_valid()
    booking.refresh_from_db()
    assert booking.enrollment_submission_id == submission.pk


@pytest.mark.parametrize("denial", ["flag", "school_viewer", "school_inactive", "booking_viewer", "booking_inactive", "location_restricted", "nonstaff"])
def test_link_requires_both_existing_permission_boundaries(linking, enable_policy, denial):
    m, booking, submission, request, membership = linking
    if denial == "flag":
        enable_policy(enrollment_linking=False)
    elif denial == "school_viewer":
        membership.role = "viewer"
        membership.save()
    elif denial == "school_inactive":
        membership.is_active = False
        membership.save()
    elif denial == "nonstaff":
        request.user.is_staff = False
        request.user.save()
    else:
        member = m["member"]
        if denial == "booking_viewer":
            member.role = "viewer"
        elif denial == "booking_inactive":
            member.is_active = False
        else:
            member.all_locations = False
        member.save()
    with pytest.raises(Http404):
        enrollment_links.link_existing(request, m["slug"], booking.public_id, link_data(submission))
    booking.refresh_from_db()
    assert booking.enrollment_submission_id is None


def test_link_audit_failure_rolls_back(linking):
    m, booking, submission, request, _ = linking
    with patch("scheduling.enrollment_links.audit", side_effect=RuntimeError), pytest.raises(RuntimeError):
        enrollment_links.link_existing(request, m["slug"], booking.public_id, link_data(submission))
    booking.refresh_from_db()
    assert booking.enrollment_submission_id is None


@pytest.mark.parametrize("revocation", ["inactive", "viewer"])
def test_link_rechecks_enrollment_access_after_waiting_for_lesson_lock(linking, revocation):
    m, booking, submission, request, membership = linking
    original_access = enrollment_links.lesson_access
    before = AdminAuditLog.objects.count()

    def revoke_during_lesson_access(*args, **kwargs):
        assert kwargs["locked"] is True
        result = original_access(*args, **kwargs)
        changes = {"is_active": False} if revocation == "inactive" else {"role": "viewer"}
        SchoolAdminMembership.objects.filter(pk=membership.pk).update(**changes)
        return result

    with patch("scheduling.enrollment_links.lesson_access", side_effect=revoke_during_lesson_access):
        with pytest.raises(Http404):
            enrollment_links.link_existing(request, m["slug"], booking.public_id, link_data(submission))
    booking.refresh_from_db()
    assert booking.enrollment_submission_id is None
    assert AdminAuditLog.objects.count() == before


def test_model_rejects_foreign_application(linking):
    _, booking, _, _, _ = linking
    booking.enrollment_submission = Submission.objects.create(school=School.objects.create(slug="foreign-model-link"))
    with pytest.raises(ValidationError):
        booking.clean()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_target", [True, False])
def test_concurrent_link_writers_serialize(linking, same_target):
    m, booking, submission, _, _ = linking
    other = submission if same_target else Submission.objects.create(school=submission.school)
    barrier = Barrier(2)

    def write(target):
        close_old_connections()
        try:
            request = RequestFactory().post("/")
            request.user = m["user"]
            barrier.wait(timeout=10)
            _, form = enrollment_links.link_existing(request, m["slug"], booking.public_id, link_data(target))
            return form.is_valid()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [submission, other]))
    assert sum(results) == (2 if same_target else 1)
    booking.refresh_from_db()
    assert booking.enrollment_submission_id in {submission.pk, other.pk}
    assert AdminAuditLog.objects.filter(model_label="scheduling.lessonbooking",
        object_id=str(booking.pk), action="change").count() == 1


def test_new_application_reuses_form_requires_confirmation_and_retries(linking, client):
    m, booking, _, _, _ = linking
    client.force_login(m["user"])
    url = reverse("scheduling:lesson_enrollment_create", kwargs={"school_slug": m["slug"], "public_id": booking.public_id})
    response = client.get(url)
    assert response.status_code == 200
    assert response.context["enrollment_booking"].pk == booking.pk
    data = {"student_name": "Trial student", "guardian_name": "Guardian", "contact_email": booking.contact_email}
    before = Submission.objects.count()
    assert client.post(url, data).status_code == 200
    assert Submission.objects.count() == before
    response = client.post(url, {**data, "_booking_identity_verified": "on"})
    assert response.status_code == 302
    booking.refresh_from_db()
    assert booking.enrollment_submission_id
    assert booking.enrollment_submission.data["student_name"] == "Trial student"
    assert Submission.objects.count() == before + 1
    assert client.post(url, data).url == response.url
    assert Submission.objects.count() == before + 1


def test_link_view_is_staff_only_and_has_no_get_side_effect(linking, client):
    m, booking, submission, _, _ = linking
    url = reverse("scheduling:lesson_enrollment_link", kwargs={"school_slug": m["slug"], "public_id": booking.public_id})
    assert client.get(url).status_code == 302
    client.force_login(m["user"])
    response = client.get(url)
    assert response.status_code == 200 and "no-store" in response["Cache-Control"]
    booking.refresh_from_db()
    assert booking.enrollment_submission_id is None
    assert client.post(url, link_data(submission)).status_code == 302


@pytest.mark.django_db(transaction=True)
def test_enrollment_link_migration_preserves_existing_booking(linking):
    _, booking, submission, _, _ = linking
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [("scheduling", "0057_teacher_self_service")]
    target = [("scheduling", "0058_lesson_booking_enrollment_submission")]
    try:
        executor.migrate(previous)
        executor = MigrationExecutor(connection)
        old_apps = executor.loader.project_state(previous).apps
        old_booking = old_apps.get_model("scheduling", "LessonBooking").objects.get(pk=booking.pk)
        assert old_booking.series_id == booking.series_id
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        restored = apps.get_model("scheduling", "LessonBooking").objects.get(pk=booking.pk)
        assert restored.enrollment_submission_id is None
        assert restored.series_id == booking.series_id and restored.order_id == booking.order_id
        assert apps.get_model("core", "Submission").objects.filter(pk=submission.pk).exists()
    finally:
        MigrationExecutor(connection).migrate(latest)
