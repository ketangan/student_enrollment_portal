"""
SBMC end-to-end workflow tests.

Covers the full lifecycle for South Bay Music Conservatory — a real school
config — from trial lead submission through parent status page and admin
acknowledge.  Also tests settings mutations and YAML config integrity.

Run with: pytest core/tests/test_sbmc_workflow.py -v

Sections
--------
1.  Lead trial form submission
2.  Lead prefill for enrollment (unit — no HTTP)
3.  Lead pipeline transitions
4.  Start enrollment (draft creation)
5.  Enrollment form submission → Submission created
6.  Submission status workflow (SBMC-specific transitions)
7.  Parent status login
8.  Schedule change request + admin acknowledge
9.  School settings mutations (display name, SMTP, Stripe)
10. YAML config integrity (pure unit — no DB)
"""
from __future__ import annotations

import pytest
import yaml
import pathlib
from django.test import override_settings
from django.urls import reverse

from core.models import (
    AdminAuditLog,
    DraftSubmission,
    Lead,
    School,
    SchoolProgram,
    Submission,
    LEAD_STATUS_NEW,
    LEAD_STATUS_CONTACTED,
    LEAD_STATUS_TRIAL_SCHEDULED,
    LEAD_STATUS_TRIAL_COMPLETED,
    LEAD_STATUS_ENROLLED,
)
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SubmissionFactory,
    UserFactory,
)
from core.views_school_common import _build_lead_prefill_data

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SBMC_SLUG = "south-bay-music"
_YAML_PATH = pathlib.Path("configs/schools/south-bay-music.yaml")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yaml() -> dict:
    return yaml.safe_load(_YAML_PATH.read_text())


def _sbmc_school(plan: str = "trial") -> School:
    """Get-or-create the SBMC school with the correct program_field_key."""
    school, _ = School.objects.get_or_create(
        slug=SBMC_SLUG,
        defaults={
            "display_name": "South Bay Music Conservatory",
            "plan": plan,
            "program_field_key": "instrument",
        },
    )
    if school.plan != plan:
        school.plan = plan
        school.save(update_fields=["plan"])
    if not school.program_field_key:
        school.program_field_key = "instrument"
        school.save(update_fields=["program_field_key"])
    return school


def _sbmc_programs(school: School) -> None:
    """Create minimal set of SchoolProgram rows required by has_enrollment_options."""
    for code, name in [("piano", "Piano"), ("violin", "Violin"), ("viola", "Viola"), ("cello", "Cello")]:
        SchoolProgram.objects.get_or_create(
            school=school, code=code, defaults={"name": name, "is_active": True}
        )


def _owner(school: School):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school, role="owner")
    return user


def _editor(school: School):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school, role="editor")
    return user


def _viewer(school: School):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school, role="viewer")
    return user


def _sbmc_lead(school: School, instrument: str = "piano", student_age: str = "9") -> Lead:
    return LeadFactory(
        school=school,
        name="Liam Chen",
        email="jessica.chen@example.com",
        phone="3105550192",
        interested_in_value=instrument,
        interested_in_label=instrument.title(),
        status=LEAD_STATUS_NEW,
        data={
            "form_fields": {
                "student_name": "Liam Chen",
                "student_age": student_age,
                "instrument": instrument,
            }
        },
    )


def _enrollment_post_data(instrument: str = "piano") -> dict:
    """Minimal valid POST for the SBMC enrollment form (no payment configured)."""
    return {
        "lesson_time_status": "new_student",
        "student_last_name": "Chen",
        "student_first_name": "Liam",
        "student_age": "9",
        "student_birthday": "2015-01-01",
        "instrument": instrument,
        "preferred_communication": "text",
        "main_contact_relationship": "mother",
        "main_contact_last_name": "Chen",
        "main_contact_first_name": "Jessica",
        "guardian_email": "jessica.chen@example.com",
        "guardian_phone": "3105550192",
        "secondary_contact_on_reminders": "no",
        "street_address": "123 Ocean Ave",
        "city": "Manhattan Beach",
        "zipcode": "90266",
        "student_school": "Meadows Elementary",
        "grade_level": "3rd",
        # auto_label: any non-empty value satisfies required check
        "enrollment_fee_acknowledgment": "new_student_fee",
        # checkboxes
        "fee_payment_acknowledgment": "on",
        "monthly_tuition_policy": "on",
        "makeups_cancellations": "on",
        # radio
        "media_release": "grant",
        # final agreement
        "final_agreement": "on",
    }


def _lead_status_url(school: School, lead: Lead) -> str:
    return reverse(
        "school_lead_status_update",
        kwargs={"school_slug": school.slug, "lead_id": lead.id},
    )


def _submission_status_url(school: School, sub: Submission) -> str:
    return reverse(
        "school_submission_status_update",
        kwargs={"school_slug": school.slug, "submission_id": sub.id},
    )


def _start_enrollment_url(school: School, lead: Lead) -> str:
    return reverse(
        "school_lead_start_enrollment",
        kwargs={"school_slug": school.slug, "lead_id": lead.id},
    )


def _apply_url(school: School) -> str:
    return reverse("apply", kwargs={"school_slug": school.slug})


def _resume_url(school: School, token: str) -> str:
    return reverse("apply_resume", kwargs={"school_slug": school.slug, "token": token})


def _status_login_url(school: School) -> str:
    return reverse("school_status_login", kwargs={"school_slug": school.slug})


def _status_page_url(school: School, token: str) -> str:
    return reverse("family_status", kwargs={"school_slug": school.slug, "token": token})


def _change_request_url(school: School, token: str) -> str:
    return reverse(
        "school_status_change_request",
        kwargs={"school_slug": school.slug, "token": token},
    )


def _ack_url(school: School, sub: Submission) -> str:
    return reverse(
        "school_submission_ack_schedule_change",
        kwargs={"school_slug": school.slug, "submission_id": sub.id},
    )


def _settings_url(school: School) -> str:
    return reverse("school_settings", kwargs={"school_slug": school.slug})


# ===========================================================================
# 1. Lead trial form submission
# ===========================================================================


@pytest.mark.django_db
def test_trial_lead_form_creates_lead(client):
    """POST to SBMC trial form creates a Lead with instrument and student_age."""
    school = _sbmc_school()
    url = reverse("school_lead_form", kwargs={"school_slug": SBMC_SLUG})

    resp = client.post(url, {
        "student_name": "Emma Park",
        "student_age": "7",
        "instrument": "violin",
        "email": "parent@example.com",
        "phone": "3105550101",
    })

    # Should redirect (to fons.app or success page) — not re-render the form
    assert resp.status_code in (200, 302), f"Expected redirect or success, got {resp.status_code}"
    if resp.status_code == 302:
        # Redirect goes away from the form page on success
        pass

    lead = Lead.objects.filter(school=school, email="parent@example.com").first()
    assert lead is not None, "Lead was not created"
    assert lead.name == "Emma Park"
    assert lead.interested_in_value == "violin"
    assert lead.data["form_fields"]["student_age"] == "7"
    assert lead.data["form_fields"]["instrument"] == "violin"


@pytest.mark.django_db
def test_trial_lead_form_missing_required_field_rerenders(client):
    """Trial form with missing email re-renders with errors, no Lead created."""
    school = _sbmc_school()
    before = Lead.objects.filter(school=school).count()
    url = reverse("school_lead_form", kwargs={"school_slug": SBMC_SLUG})

    resp = client.post(url, {
        "student_name": "Emma Park",
        "instrument": "violin",
        # email intentionally omitted
        "phone": "3105550101",
    })

    assert resp.status_code == 200
    assert Lead.objects.filter(school=school).count() == before


@pytest.mark.django_db
def test_trial_lead_form_invalid_instrument_still_captures_data(client):
    """An unrecognised instrument value still creates the lead (no server-side enum check on leads)."""
    school = _sbmc_school()
    url = reverse("school_lead_form", kwargs={"school_slug": SBMC_SLUG})

    client.post(url, {
        "student_name": "Test Student",
        "student_age": "10",
        "instrument": "drums",  # not in redirect_url_map
        "email": "test_drums@example.com",
        "phone": "3105550199",
    })

    lead = Lead.objects.filter(school=school, email="test_drums@example.com").first()
    assert lead is not None
    # instrument captured in form_fields even if not in map
    assert lead.data["form_fields"].get("instrument") == "drums"


# ===========================================================================
# 2. Lead prefill for enrollment (unit — no HTTP)
# ===========================================================================


@pytest.mark.django_db
def test_lead_prefill_skips_empty_strings():
    """Empty form_fields values are NOT copied into the prefill dict."""
    school = _sbmc_school()
    lead = LeadFactory(
        school=school,
        name="Liam Chen",
        email="jessica@example.com",
        phone="3105550192",
        interested_in_value="",  # no value — so interested_in_value path doesn't fire
        interested_in_label="",
        data={"form_fields": {"student_name": "Liam Chen", "instrument": "", "student_age": ""}},
    )
    raw = _load_yaml()
    prefill = _build_lead_prefill_data(lead, raw)

    assert "instrument" not in prefill, "Empty instrument should not be in prefill"
    assert "student_age" not in prefill, "Empty student_age should not be in prefill"
    assert prefill.get("student_name") == "Liam Chen"


@pytest.mark.django_db
def test_lead_prefill_copies_real_form_fields():
    """Non-empty form_fields values ARE copied and structured lead fields override email/phone."""
    school = _sbmc_school()
    lead = LeadFactory(
        school=school,
        name="Emma Park",
        email="parent@example.com",
        phone="3105550101",
        interested_in_value="violin",
        data={"form_fields": {"student_name": "Emma Park", "student_age": "7", "instrument": "violin"}},
    )
    raw = _load_yaml()
    prefill = _build_lead_prefill_data(lead, raw)

    assert prefill.get("student_age") == "7"
    # DB-backed program field: bare lead code must be normalized to "program:<code>"
    # so the enrollment form's select option pre-selects correctly.
    assert prefill.get("instrument") == "program:violin"
    assert prefill.get("guardian_email") == "parent@example.com"
    assert prefill.get("guardian_phone") == "3105550101"


@pytest.mark.django_db
def test_lead_prefill_interested_in_value_overwrites_empty_instrument():
    """When form_fields.instrument is empty but interested_in_value is set, the latter wins."""
    school = _sbmc_school()
    lead = LeadFactory(
        school=school,
        name="Sam Lee",
        email="lee@example.com",
        phone="3105550103",
        interested_in_value="cello",
        data={"form_fields": {"student_name": "Sam Lee", "instrument": ""}},
    )
    raw = _load_yaml()
    prefill = _build_lead_prefill_data(lead, raw)

    # interested_in_value provides the instrument, normalized to "program:<code>"
    assert prefill.get("instrument") == "program:cello"


@pytest.mark.django_db
def test_start_enrollment_renders_instrument_preselected(client):
    """
    Regression: Start Enrollment must produce a draft whose prefilled instrument value
    is in "program:<code>" format so the enrollment form's DB-backed select pre-selects it.

    Before the fix, _build_lead_prefill_data stored a bare "piano" value which never
    matched any rendered <option value="program:piano">, so the dropdown appeared blank.
    This test catches that at the HTTP layer by loading the resume URL and checking that
    the correct option is marked selected in the rendered HTML.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    lead = LeadFactory(
        school=school,
        name="Ava Kim",
        email="ava@example.com",
        interested_in_value="piano",
    )

    # Admin triggers Start Enrollment
    client.force_login(admin)
    client.post(_start_enrollment_url(school, lead))
    draft = DraftSubmission.objects.filter(school=school, lead=lead).first()
    assert draft is not None

    # Assert the draft already carries the normalized value
    assert draft.data.get("instrument") == "program:piano", (
        f"Draft instrument must be 'program:piano', got {draft.data.get('instrument')!r}"
    )

    # Load the enrollment form via the resume URL (sets session, then redirects to apply).
    client.logout()
    client.get(_resume_url(school, draft.token))  # sets draft session
    form_resp = client.get(_apply_url(school))
    assert form_resp.status_code == 200

    # The rendered <option value="program:piano" ...> must be selected
    content = form_resp.content.decode()
    assert 'value="program:piano" selected' in content or \
           "program:piano\" selected" in content, (
        "Expected instrument option to be pre-selected in the rendered enrollment form"
    )


@pytest.mark.django_db
def test_lead_prefill_form_fields_instrument_also_normalized(client):
    """
    Regression: instrument in lead.data.form_fields (bare code) must also be normalized
    to 'program:<code>' — not just the interested_in_value path.
    """
    school = _sbmc_school()
    lead = LeadFactory(
        school=school,
        name="Ben Wu",
        email="ben@example.com",
        interested_in_value="",  # not set
        data={"form_fields": {"instrument": "violin"}},
    )
    raw = _load_yaml()
    prefill = _build_lead_prefill_data(lead, raw)
    assert prefill.get("instrument") == "program:violin", (
        "form_fields bare code must be normalized to 'program:<code>' for DB-backed fields"
    )


@pytest.mark.django_db
def test_lead_prefill_name_split_into_first_last():
    """Lead.name is split into student_first_name and student_last_name for the enrollment form."""
    school = _sbmc_school()
    lead = LeadFactory(school=school, name="Mia Rodriguez", email="mia@example.com")
    raw = _load_yaml()
    prefill = _build_lead_prefill_data(lead, raw)

    assert prefill.get("student_first_name") == "Mia"
    assert prefill.get("student_last_name") == "Rodriguez"


# ===========================================================================
# 3. Lead pipeline transitions
# ===========================================================================


@pytest.mark.django_db
def test_lead_transition_new_to_contacted(client):
    """new → contacted is a valid SBMC transition."""
    school = _sbmc_school()
    lead = _sbmc_lead(school)
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_lead_status_url(school, lead), {"new_status": "contacted"})
    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.status == LEAD_STATUS_CONTACTED


@pytest.mark.django_db
def test_lead_transition_full_pipeline(client):
    """Walk through new → contacted → trial_scheduled → trial_completed in sequence."""
    school = _sbmc_school()
    lead = _sbmc_lead(school)
    user = _owner(school)
    client.force_login(user)

    for target in ("contacted", "trial_scheduled", "trial_completed"):
        resp = client.post(_lead_status_url(school, lead), {"new_status": target})
        assert resp.status_code == 302, f"Transition to {target} failed"
        lead.refresh_from_db()
        assert lead.status == target


@pytest.mark.django_db
def test_lead_transition_enrolled_is_blocked_by_yaml(client):
    """Enrolled is a terminal status — no YAML transition reaches it directly from 'new'."""
    school = _sbmc_school()
    lead = _sbmc_lead(school)
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_lead_status_url(school, lead), {"new_status": "enrolled"})
    # View returns 302 on redirect but lead should still be 'new' if transition is blocked
    lead.refresh_from_db()
    assert lead.status == LEAD_STATUS_NEW, "Lead should not be enrolled via direct status update"


@pytest.mark.django_db
def test_lead_transition_bogus_status_rejected(client):
    """An unrecognised status string is rejected — lead status unchanged."""
    school = _sbmc_school()
    lead = _sbmc_lead(school)
    user = _owner(school)
    client.force_login(user)

    client.post(_lead_status_url(school, lead), {"new_status": "promoted"})
    lead.refresh_from_db()
    assert lead.status == LEAD_STATUS_NEW


@pytest.mark.django_db
def test_lead_transition_viewer_cannot_update_status(client):
    """A viewer-role member cannot update lead status (requires editor+)."""
    school = _sbmc_school()
    lead = _sbmc_lead(school)
    user = _viewer(school)
    client.force_login(user)

    resp = client.post(_lead_status_url(school, lead), {"new_status": "contacted"})
    assert resp.status_code == 404
    lead.refresh_from_db()
    assert lead.status == LEAD_STATUS_NEW


# ===========================================================================
# 4. Start enrollment (draft creation)
# ===========================================================================


@pytest.mark.django_db
def test_start_enrollment_creates_draft(client):
    """Admin POST to start-enrollment creates a DraftSubmission linked to the lead."""
    school = _sbmc_school()
    lead = _sbmc_lead(school)
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_start_enrollment_url(school, lead))
    assert resp.status_code == 302

    draft = DraftSubmission.objects.filter(school=school, lead=lead).first()
    assert draft is not None, "DraftSubmission was not created"
    assert not draft.is_expired()
    assert not draft.is_submitted()


@pytest.mark.django_db
def test_start_enrollment_is_idempotent(client):
    """POSTing start-enrollment twice reuses the same draft (no duplicates)."""
    school = _sbmc_school()
    lead = _sbmc_lead(school)
    user = _owner(school)
    client.force_login(user)

    client.post(_start_enrollment_url(school, lead))
    client.post(_start_enrollment_url(school, lead))

    assert DraftSubmission.objects.filter(school=school, lead=lead).count() == 1


@pytest.mark.django_db
def test_start_enrollment_draft_prefills_lead_data(client):
    """The draft created by start-enrollment contains name, email, phone from the lead."""
    school = _sbmc_school()
    lead = _sbmc_lead(school, instrument="violin", student_age="7")
    user = _owner(school)
    client.force_login(user)

    client.post(_start_enrollment_url(school, lead))

    draft = DraftSubmission.objects.filter(school=school, lead=lead).first()
    assert draft is not None
    assert draft.data.get("guardian_email") == lead.email
    assert draft.data.get("guardian_phone") == lead.phone
    # DB-backed program field: bare code normalized to "program:<code>"
    assert draft.data.get("instrument") == "program:violin"
    assert draft.data.get("student_age") == "7"


@pytest.mark.django_db
def test_start_enrollment_viewer_cannot_create_draft(client):
    """Viewer role cannot initiate start-enrollment."""
    school = _sbmc_school()
    lead = _sbmc_lead(school)
    user = _viewer(school)
    client.force_login(user)

    resp = client.post(_start_enrollment_url(school, lead))
    assert resp.status_code == 404
    assert not DraftSubmission.objects.filter(school=school, lead=lead).exists()


# ===========================================================================
# 5. Enrollment form submission → Submission created
# ===========================================================================


@pytest.mark.django_db
@override_settings(DEV_SKIP_PAYMENT=False)
def test_enrollment_form_creates_submission(client):
    """
    Full enrollment: start-enrollment creates a draft, resume URL sets session,
    POST to apply form creates the Submission.
    DEV_SKIP_PAYMENT disabled so fee-enabled form takes the direct-submission path
    (no Stripe key → _stripe_ready=False → submission created immediately).
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    lead = _sbmc_lead(school)
    admin = _owner(school)

    # Step 1: admin starts enrollment
    client.force_login(admin)
    client.post(_start_enrollment_url(school, lead))
    draft = DraftSubmission.objects.filter(school=school, lead=lead).first()
    assert draft is not None

    # Step 2: simulate parent opening resume link (sets session)
    client.logout()
    client.get(_resume_url(school, draft.token))  # sets session → draft PK

    # Step 3: parent submits the enrollment form
    post_data = _enrollment_post_data(instrument="piano")
    resp = client.post(_apply_url(school), data=post_data)

    # Success: redirect to success page
    assert resp.status_code in (302, 303), f"Expected redirect on success, got {resp.status_code}"

    sub = Submission.objects.filter(school=school).order_by("-id").first()
    assert sub is not None, "No Submission was created"
    assert sub.data["guardian_email"] == "jessica.chen@example.com"
    assert sub.data["instrument"] == "piano"
    assert sub.status == "New"  # SBMC default_submission_status


@pytest.mark.django_db
@override_settings(DEV_SKIP_PAYMENT=False)
def test_enrollment_marks_lead_enrolled(client):
    """After a successful enrollment form POST, the lead's status becomes 'enrolled'."""
    school = _sbmc_school()
    _sbmc_programs(school)
    lead = _sbmc_lead(school)
    admin = _owner(school)

    # Admin starts enrollment
    client.force_login(admin)
    client.post(_start_enrollment_url(school, lead))
    draft = DraftSubmission.objects.filter(school=school, lead=lead).first()

    # Parent resumes and submits
    client.logout()
    client.get(_resume_url(school, draft.token))
    client.post(_apply_url(school), data=_enrollment_post_data())

    lead.refresh_from_db()
    assert lead.status == LEAD_STATUS_ENROLLED
    assert lead.converted_submission_id is not None


@pytest.mark.django_db
@override_settings(DEV_SKIP_PAYMENT=False)
def test_enrollment_draft_marked_submitted(client):
    """After enrollment form POST, the DraftSubmission.submitted_at is set."""
    school = _sbmc_school()
    _sbmc_programs(school)
    lead = _sbmc_lead(school)
    admin = _owner(school)

    client.force_login(admin)
    client.post(_start_enrollment_url(school, lead))
    draft = DraftSubmission.objects.filter(school=school, lead=lead).first()

    client.logout()
    client.get(_resume_url(school, draft.token))
    client.post(_apply_url(school), data=_enrollment_post_data())

    draft.refresh_from_db()
    assert draft.submitted_at is not None, "Draft should be marked as submitted"


@pytest.mark.django_db
def test_enrollment_missing_required_field_rerenders(client):
    """POST to apply form with a missing required field re-renders (no Submission created)."""
    school = _sbmc_school()
    _sbmc_programs(school)
    before = Submission.objects.filter(school=school).count()

    post_data = _enrollment_post_data()
    del post_data["guardian_email"]  # remove required field

    resp = client.post(_apply_url(school), data=post_data)

    assert resp.status_code == 200  # re-render with errors
    assert Submission.objects.filter(school=school).count() == before


@pytest.mark.django_db
def test_enrollment_no_programs_configured_blocks_submit(client):
    """When no SchoolProgram rows exist for a school with program_field_key, form submission is blocked."""
    school = _sbmc_school()
    # Do NOT create programs — has_enrollment_options returns False
    SchoolProgram.objects.filter(school=school).delete()
    before = Submission.objects.filter(school=school).count()

    resp = client.post(_apply_url(school), data=_enrollment_post_data())

    assert resp.status_code == 200  # error re-render
    assert Submission.objects.filter(school=school).count() == before


# ===========================================================================
# 6. Submission status workflow (SBMC-specific transitions)
# ===========================================================================


@pytest.mark.django_db
def test_submission_transition_new_to_in_review(client):
    """New → In Review is a valid SBMC transition (added in E2E-catch bug fix)."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, status="New")
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_submission_status_url(school, sub), {"new_status": "In Review"})
    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "In Review"


@pytest.mark.django_db
def test_submission_transition_in_review_to_enrolled(client):
    """In Review → Enrolled is a valid SBMC transition."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, status="In Review")
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_submission_status_url(school, sub), {"new_status": "Enrolled"})
    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "Enrolled"


@pytest.mark.django_db
def test_submission_transition_full_chain(client):
    """Walk New → In Review → Enrolled in two steps."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, status="New")
    user = _owner(school)
    client.force_login(user)

    for target in ("In Review", "Enrolled"):
        resp = client.post(_submission_status_url(school, sub), {"new_status": target})
        assert resp.status_code == 302, f"Transition to '{target}' failed"
        sub.refresh_from_db()
        assert sub.status == target


@pytest.mark.django_db
def test_submission_transition_new_to_enrolled_directly_blocked(client):
    """New → Enrolled directly is NOT a valid SBMC transition (must go through In Review)."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, status="New")
    user = _owner(school)
    client.force_login(user)

    client.post(_submission_status_url(school, sub), {"new_status": "Enrolled"})
    sub.refresh_from_db()
    assert sub.status == "New", "Should not jump New→Enrolled without going through In Review"


@pytest.mark.django_db
def test_submission_transition_bogus_status_rejected(client):
    """An unrecognised status string is rejected — submission status unchanged."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, status="New")
    user = _owner(school)
    client.force_login(user)

    client.post(_submission_status_url(school, sub), {"new_status": "Graduated"})
    sub.refresh_from_db()
    assert sub.status == "New"


@pytest.mark.django_db
def test_submission_transition_viewer_cannot_change_status(client):
    """Viewer role cannot update submission status."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, status="New")
    user = _viewer(school)
    client.force_login(user)

    resp = client.post(_submission_status_url(school, sub), {"new_status": "In Review"})
    assert resp.status_code == 404
    sub.refresh_from_db()
    assert sub.status == "New"


@pytest.mark.django_db
def test_submission_transition_editor_can_change_status(client):
    """Editor role can update submission status."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, status="New")
    user = _editor(school)
    client.force_login(user)

    resp = client.post(_submission_status_url(school, sub), {"new_status": "In Review"})
    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "In Review"


# ===========================================================================
# 7. Parent status login
# ===========================================================================


@pytest.mark.django_db
def test_status_login_get_renders(client):
    """GET on status login page returns 200."""
    school = _sbmc_school()
    resp = client.get(_status_login_url(school))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_status_login_valid_credentials_redirect(client):
    """Valid public_id + last_name → 302 to token URL."""
    school = _sbmc_school()
    sub = SubmissionFactory(
        school=school,
        data={"student_last_name": "Chen", "student_first_name": "Liam"},
    )
    resp = client.post(_status_login_url(school), {
        "application_id": sub.public_id,
        "last_name": "chen",  # case-insensitive
    })
    assert resp.status_code == 302
    assert sub.status_token in resp["Location"]


@pytest.mark.django_db
def test_status_login_wrong_last_name_rejected(client):
    """Wrong last name does not redirect to the status page."""
    school = _sbmc_school()
    sub = SubmissionFactory(
        school=school,
        data={"student_last_name": "Chen"},
    )
    resp = client.post(_status_login_url(school), {
        "application_id": sub.public_id,
        "last_name": "Wong",
    })
    assert resp.status_code != 302


@pytest.mark.django_db
def test_status_login_bad_public_id_rejected(client):
    """Non-existent public_id does not redirect."""
    school = _sbmc_school()
    resp = client.post(_status_login_url(school), {
        "application_id": "INVALID000000000",
        "last_name": "Chen",
    })
    assert resp.status_code != 302


@pytest.mark.django_db
def test_status_page_shows_scheduling_preferences(client):
    """Status page displays sched_* fields from submission data."""
    school = _sbmc_school()
    sub = SubmissionFactory(
        school=school,
        data={
            "student_last_name": "Chen",
            "sched_preferred_timing": "After 5pm",
            "sched_preferred_slot": "Tuesday 4pm",
        },
    )
    resp = client.get(_status_page_url(school, sub.status_token))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "After 5pm" in body
    assert "Tuesday 4pm" in body


# ===========================================================================
# 8. Schedule change request + admin acknowledge
# ===========================================================================


@pytest.mark.django_db
def test_schedule_change_request_sets_flag_and_updates_data(client):
    """Parent POST to change_request sets flag and writes new sched_* values."""
    school = _sbmc_school()
    sub = SubmissionFactory(
        school=school,
        data={"student_last_name": "Chen", "sched_preferred_timing": "Afternoon"},
    )
    assert not sub.schedule_change_requested

    resp = client.post(_change_request_url(school, sub.status_token), {
        "sched_preferred_timing": "Morning",
        "sched_day_preference": "weekday",
        "sched_preferred_slot": "",
        "sched_days_unavailable": "Thursday,Friday",
        "sched_preferred_start_week": "",
    })
    assert resp.status_code == 302
    assert "change=requested" in resp["Location"]

    sub.refresh_from_db()
    assert sub.schedule_change_requested is True
    assert sub.data["sched_preferred_timing"] == "Morning"
    assert sub.data["sched_days_unavailable"] == "Thursday,Friday"


@pytest.mark.django_db
def test_schedule_change_request_overwrites_previous_values(client):
    """A second change request overwrites the previous sched_* values."""
    school = _sbmc_school()
    sub = SubmissionFactory(
        school=school,
        data={"student_last_name": "Chen", "sched_preferred_timing": "Old value"},
        schedule_change_requested=True,
    )
    client.post(_change_request_url(school, sub.status_token), {
        "sched_preferred_timing": "New value",
        "sched_day_preference": "",
        "sched_preferred_slot": "",
        "sched_days_unavailable": "",
        "sched_preferred_start_week": "",
    })
    sub.refresh_from_db()
    assert sub.data["sched_preferred_timing"] == "New value"


@pytest.mark.django_db
def test_schedule_change_request_bad_token_404(client):
    """Change request with a bad token returns 404."""
    school = _sbmc_school()
    resp = client.post(
        reverse("school_status_change_request", kwargs={
            "school_slug": school.slug, "token": "badtoken"
        }),
        {"sched_preferred_timing": "test"},
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_admin_sees_scheduling_badge_in_submissions_list(client):
    """Submissions with schedule_change_requested=True show the 'Scheduling' badge."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, schedule_change_requested=True)
    user = _owner(school)
    client.force_login(user)

    resp = client.get(reverse("school_submissions", kwargs={"school_slug": school.slug}))
    assert resp.status_code == 200
    assert "Scheduling" in resp.content.decode()


@pytest.mark.django_db
def test_admin_acknowledge_clears_flag(client):
    """Admin POST to acknowledge clears schedule_change_requested."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, schedule_change_requested=True)
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_ack_url(school, sub))
    assert resp.status_code == 302

    sub.refresh_from_db()
    assert sub.schedule_change_requested is False


@pytest.mark.django_db
def test_admin_acknowledge_creates_audit_log(client):
    """Acknowledging a schedule change creates an audit log entry."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, schedule_change_requested=True)
    user = _owner(school)
    client.force_login(user)

    client.post(_ack_url(school, sub))

    assert AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(sub.pk),
        extra__name="acknowledge_schedule_change",
    ).exists()


@pytest.mark.django_db
def test_admin_acknowledge_viewer_cannot_acknowledge(client):
    """Viewer cannot acknowledge a schedule change (requires editor+)."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, schedule_change_requested=True)
    user = _viewer(school)
    client.force_login(user)

    resp = client.post(_ack_url(school, sub))
    assert resp.status_code == 404
    sub.refresh_from_db()
    assert sub.schedule_change_requested is True


# ===========================================================================
# 9. School settings mutations
# ===========================================================================


@pytest.mark.django_db
def test_settings_display_name_owner_can_update(client):
    """Owner can update the school display name."""
    school = _sbmc_school()
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {
        "action": "update_display_name",
        "display_name": "SBMC Updated Name",
    })
    assert resp.status_code == 302
    school.refresh_from_db()
    assert school.display_name == "SBMC Updated Name"


@pytest.mark.django_db
def test_settings_display_name_viewer_blocked(client):
    """Viewer cannot update the display name."""
    school = _sbmc_school()
    original_name = school.display_name
    user = _viewer(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {
        "action": "update_display_name",
        "display_name": "Hacked Name",
    })
    assert resp.status_code == 404
    school.refresh_from_db()
    assert school.display_name == original_name


@pytest.mark.django_db
def test_settings_display_name_editor_blocked(client):
    """Editor cannot update the display name (owner-only action)."""
    school = _sbmc_school()
    original_name = school.display_name
    user = _editor(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {
        "action": "update_display_name",
        "display_name": "Editor Attempt",
    })
    assert resp.status_code == 404
    school.refresh_from_db()
    assert school.display_name == original_name


@pytest.mark.django_db
def test_settings_smtp_owner_can_save(client):
    """Owner can save SMTP settings."""
    school = _sbmc_school()
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {
        "action": "update_smtp",
        "smtp_host": "smtp.example.com",
        "smtp_port": "587",
        "smtp_username": "user@example.com",
        "smtp_password": "secret",
        "smtp_from_email": "noreply@example.com",
        "smtp_use_tls": "1",  # view checks == "1", not "on"
    })
    assert resp.status_code == 302
    school.refresh_from_db()
    assert school.smtp_host == "smtp.example.com"
    assert school.smtp_port == 587
    assert school.smtp_use_tls is True


@pytest.mark.django_db
def test_settings_smtp_owner_can_clear(client):
    """Owner can clear SMTP settings."""
    school = _sbmc_school()
    school.smtp_host = "smtp.example.com"
    school.smtp_port = 587
    school.save(update_fields=["smtp_host", "smtp_port"])
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {"action": "clear_smtp"})
    assert resp.status_code == 302
    school.refresh_from_db()
    assert school.smtp_host == ""
    assert school.smtp_port is None


@pytest.mark.django_db
def test_settings_smtp_viewer_blocked(client):
    """Viewer cannot update SMTP settings."""
    school = _sbmc_school()
    user = _viewer(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {
        "action": "update_smtp",
        "smtp_host": "smtp.evil.com",
        "smtp_port": "25",
    })
    assert resp.status_code == 404
    school.refresh_from_db()
    assert school.smtp_host != "smtp.evil.com"


@pytest.mark.django_db
def test_settings_smtp_invalid_port_rejected(client):
    """Invalid SMTP port (out of range) is rejected with an error — no DB update."""
    school = _sbmc_school()
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {
        "action": "update_smtp",
        "smtp_host": "smtp.example.com",
        "smtp_port": "99999",  # out of range
    })
    assert resp.status_code == 302
    school.refresh_from_db()
    assert school.smtp_host == ""  # not saved


@pytest.mark.django_db
def test_settings_stripe_owner_can_save(client):
    """Owner can save Stripe keys."""
    school = _sbmc_school()
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {
        "action": "update_stripe",
        "app_fee_stripe_public_key": "pk_test_abc123456789",
        "app_fee_stripe_secret_key": "sk_test_xyz987654321",
    })
    assert resp.status_code == 302
    school.refresh_from_db()
    assert school.app_fee_stripe_public_key == "pk_test_abc123456789"
    assert school.app_fee_stripe_secret_key == "sk_test_xyz987654321"


@pytest.mark.django_db
def test_settings_stripe_empty_public_key_rejected(client):
    """Empty publishable key is rejected — Stripe keys not saved."""
    school = _sbmc_school()
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {
        "action": "update_stripe",
        "app_fee_stripe_public_key": "",
        "app_fee_stripe_secret_key": "sk_test_xyz",
    })
    assert resp.status_code == 302
    school.refresh_from_db()
    assert school.app_fee_stripe_public_key == ""


@pytest.mark.django_db
def test_settings_stripe_owner_can_clear(client):
    """Owner can clear Stripe keys."""
    school = _sbmc_school()
    school.app_fee_stripe_public_key = "pk_test_existing"
    school.app_fee_stripe_secret_key = "sk_test_existing"
    school.save(update_fields=["app_fee_stripe_public_key", "app_fee_stripe_secret_key"])
    user = _owner(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {"action": "clear_stripe"})
    assert resp.status_code == 302
    school.refresh_from_db()
    assert school.app_fee_stripe_public_key == ""
    assert school.app_fee_stripe_secret_key == ""


@pytest.mark.django_db
def test_settings_stripe_viewer_blocked(client):
    """Viewer cannot save Stripe keys."""
    school = _sbmc_school()
    user = _viewer(school)
    client.force_login(user)

    resp = client.post(_settings_url(school), {
        "action": "update_stripe",
        "app_fee_stripe_public_key": "pk_test_evil",
    })
    assert resp.status_code == 404
    school.refresh_from_db()
    assert school.app_fee_stripe_public_key == ""


# ===========================================================================
# 10. YAML config integrity (no DB — pure unit tests)
# ===========================================================================


def test_yaml_loads_without_error():
    """SBMC YAML parses without error."""
    raw = _load_yaml()
    assert raw is not None
    assert raw.get("school", {}).get("slug") == SBMC_SLUG


def test_yaml_submission_statuses_defined():
    """submission_statuses list is non-empty and contains expected values."""
    raw = _load_yaml()
    statuses = raw["admin"]["submission_statuses"]
    assert "New" in statuses
    assert "In Review" in statuses
    assert "Enrolled" in statuses


def test_yaml_default_submission_status_in_statuses():
    """default_submission_status is in the submission_statuses list."""
    raw = _load_yaml()
    admin = raw["admin"]
    default = admin.get("default_submission_status", "New")
    assert default in admin["submission_statuses"], (
        f"default_submission_status '{default}' not in submission_statuses"
    )


def test_yaml_all_submission_transition_sources_in_statuses():
    """Every 'from' status in submission_workflow.transitions appears in submission_statuses."""
    raw = _load_yaml()
    statuses = set(raw["admin"]["submission_statuses"])
    transitions = raw["admin"]["submission_workflow"].get("transitions", {})
    for from_status in transitions:
        assert from_status in statuses, (
            f"Transition source '{from_status}' not in submission_statuses"
        )


def test_yaml_all_submission_transition_targets_in_statuses():
    """Every 'to' status in submission_workflow.transitions appears in submission_statuses."""
    raw = _load_yaml()
    statuses = set(raw["admin"]["submission_statuses"])
    transitions = raw["admin"]["submission_workflow"].get("transitions", {})
    for from_status, actions in transitions.items():
        for action in actions:
            to_status = action["status"]
            assert to_status in statuses, (
                f"Transition target '{to_status}' (from '{from_status}') not in submission_statuses"
            )


def test_yaml_new_status_has_in_review_transition():
    """'New' has a transition to 'In Review' — the bug that was caught in E2E testing."""
    raw = _load_yaml()
    transitions = raw["admin"]["submission_workflow"]["transitions"]
    new_targets = [t["status"] for t in transitions.get("New", [])]
    assert "In Review" in new_targets, (
        "Missing 'New → In Review' transition — this was caught as a real bug"
    )


def test_yaml_lead_workflow_transitions_reference_valid_statuses():
    """All lead workflow transition targets are valid model-level LEAD_STATUS_CHOICES values."""
    from core.models import LEAD_STATUS_CHOICES
    valid = {c[0] for c in LEAD_STATUS_CHOICES}
    raw = _load_yaml()
    transitions = raw["admin"]["lead_workflow"].get("transitions", {})
    for from_status, actions in transitions.items():
        assert from_status in valid or from_status == "enrolled", (
            f"Lead transition source '{from_status}' not a valid status"
        )
        for action in actions:
            to_status = action["status"]
            assert to_status in valid, (
                f"Lead transition target '{to_status}' (from '{from_status}') not valid"
            )


def test_yaml_lead_filters_reference_known_statuses():
    """All statuses listed in lead_workflow.filters exist in LEAD_STATUS_CHOICES."""
    from core.models import LEAD_STATUS_CHOICES
    valid = {c[0] for c in LEAD_STATUS_CHOICES}
    raw = _load_yaml()
    filters = raw["admin"]["lead_workflow"].get("filters", {})
    for key, fconf in filters.items():
        for status in fconf.get("statuses", []):
            assert status in valid, (
                f"Lead filter '{key}' references unknown status '{status}'"
            )


def test_yaml_submission_filters_reference_known_statuses():
    """All statuses listed in submission_workflow.filters exist in submission_statuses."""
    raw = _load_yaml()
    statuses = set(raw["admin"]["submission_statuses"])
    filters = raw["admin"]["submission_workflow"].get("filters", {})
    for key, fconf in filters.items():
        for status in fconf.get("statuses", []):
            assert status in statuses, (
                f"Submission filter '{key}' references unknown status '{status}'"
            )


def test_yaml_program_field_key_matches_form_field():
    """program_field_key is defined as a field in the enrollment form."""
    raw = _load_yaml()
    prog_key = raw.get("program_field_key", "")
    assert prog_key, "program_field_key must be set"

    all_keys = [
        f["key"]
        for section in raw["form"].get("sections", [])
        for f in section.get("fields", [])
    ]
    assert prog_key in all_keys, (
        f"program_field_key '{prog_key}' not found in any form section field"
    )


def test_yaml_scheduling_fields_defined_in_both_form_and_lead_form():
    """sched_* fields exist in the enrollment form (for display on status page)."""
    raw = _load_yaml()
    all_keys = {
        f["key"]
        for section in raw["form"].get("sections", [])
        for f in section.get("fields", [])
    }
    expected_sched_keys = {
        "sched_day_preference",
        "sched_preferred_timing",
        "sched_days_unavailable",
        "sched_preferred_slot",
        "sched_preferred_start_week",
    }
    missing = expected_sched_keys - all_keys
    assert not missing, f"sched_* keys missing from enrollment form: {missing}"
