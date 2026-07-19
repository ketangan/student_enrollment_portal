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


@pytest.mark.django_db
def test_start_enrollment_renders_all_prefill_fields(client):
    """
    HTTP-layer regression: ALL lead fields must appear pre-filled in the rendered enrollment form.

    Covers:
      - student_first_name / student_last_name (split from lead.name)
      - guardian_email (from lead.email)
      - guardian_phone (from lead.phone)
      - student_age (from lead.data.form_fields)
      - instrument (from interested_in_value, normalized to program:<code>)

    This test catches any regression where a field is mapped into draft.data correctly but
    the template fails to render it, or where the mapping silently breaks for a field type.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    lead = LeadFactory(
        school=school,
        name="Clara Osei",
        email="clara.osei@example.com",
        phone="3105559876",
        interested_in_value="violin",
        data={"form_fields": {"student_name": "Clara Osei", "student_age": "11", "instrument": "violin"}},
    )

    client.force_login(admin)
    resp = client.post(_start_enrollment_url(school, lead))
    assert resp.status_code == 302

    draft = DraftSubmission.objects.filter(school=school, lead=lead).first()
    assert draft is not None, "Draft must be created"

    # Verify draft.data before hitting the template
    assert draft.data.get("student_first_name") == "Clara", f"Got {draft.data.get('student_first_name')!r}"
    assert draft.data.get("student_last_name") == "Osei", f"Got {draft.data.get('student_last_name')!r}"
    assert draft.data.get("guardian_email") == "clara.osei@example.com", f"Got {draft.data.get('guardian_email')!r}"
    assert draft.data.get("guardian_phone") == "3105559876", f"Got {draft.data.get('guardian_phone')!r}"
    assert draft.data.get("student_age") == "11", f"Got {draft.data.get('student_age')!r}"
    assert draft.data.get("instrument") == "program:violin", f"Got {draft.data.get('instrument')!r}"

    # Load the rendered form via the resume → apply path
    client.logout()
    client.get(_resume_url(school, draft.token))  # sets draft session
    form_resp = client.get(_apply_url(school))
    assert form_resp.status_code == 200
    content = form_resp.content.decode()

    # Each value must appear somewhere in the rendered HTML
    assert "Clara" in content, "student_first_name not rendered"
    assert "Osei" in content, "student_last_name not rendered"
    assert "clara.osei@example.com" in content, "guardian_email not rendered"
    assert "3105559876" in content, "guardian_phone not rendered"
    assert 'value="11"' in content or ">11<" in content, "student_age not rendered"
    assert 'value="program:violin" selected' in content or 'program:violin" selected' in content, (
        "instrument not pre-selected in enrollment form"
    )


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
def test_lead_transition_to_enrolled_allowed(client):
    """Any valid status is now directly reachable — no transition graph enforcement."""
    school = _sbmc_school()
    lead = _sbmc_lead(school)
    user = _owner(school)
    client.force_login(user)

    client.post(_lead_status_url(school, lead), {"new_status": "enrolled"})
    lead.refresh_from_db()
    assert lead.status == "enrolled"


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
def test_submission_transition_new_to_enrolled_directly_allowed(client):
    """New → Enrolled directly is now allowed — no transition graph enforcement."""
    school = _sbmc_school()
    sub = SubmissionFactory(school=school, status="New")
    user = _owner(school)
    client.force_login(user)

    client.post(_submission_status_url(school, sub), {"new_status": "Enrolled"})
    sub.refresh_from_db()
    assert sub.status == "Enrolled"


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

# ===========================================================================
# 10. Multi-lead isolation — session, data, status, notes must never cross
# ===========================================================================
#
# These tests simulate an admin working multiple leads simultaneously.
# The core risk: the draft session key is per-school, not per-lead.
# Starting enrollment for lead A overwrites the session, so "Open Form" on
# lead B (if it still used the bare apply URL) would have loaded lead A's draft.
# Every test here verifies that isolation holds end-to-end.


def _lead_detail_url(school: School, lead: Lead) -> str:
    return reverse("school_lead_detail", kwargs={"school_slug": school.slug, "lead_id": lead.id})


def _lead_update_url(school: School, lead: Lead) -> str:
    return reverse("school_lead_update", kwargs={"school_slug": school.slug, "lead_id": lead.id})


def _make_lead(school, name, instrument, email=None):
    return LeadFactory(
        school=school,
        name=name,
        email=email or f"{name.split()[0].lower()}@example.com",
        interested_in_value=instrument,
        interested_in_label=instrument.title(),
        status=LEAD_STATUS_NEW,
        data={"form_fields": {"instrument": instrument, "student_name": name}},
    )


@pytest.mark.django_db
def test_open_form_uses_token_not_session(client):
    """
    Regression: 'Open Form' must use the token URL so it is immune to session
    state from a previously started enrollment on a different lead.

    Before the fix, the button linked to /apply/ (session-based). Starting
    enrollment for lead A wrote the session; opening form for lead B then loaded
    lead A's draft — wrong student's data.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    client.force_login(admin)

    lead_a = _make_lead(school, "Alice Wang", "piano")
    lead_b = _make_lead(school, "Ben Kim", "violin")

    # Start enrollment for A — session now holds draft_A
    client.post(_start_enrollment_url(school, lead_a))
    draft_a = DraftSubmission.objects.filter(school=school, lead=lead_a).first()
    assert draft_a is not None

    # Start enrollment for B
    client.post(_start_enrollment_url(school, lead_b))
    draft_b = DraftSubmission.objects.filter(school=school, lead=lead_b).first()
    assert draft_b is not None

    # The session now holds draft_B's PK (last Start Enrollment wins).
    # Visiting lead_a's detail page must show form_url pointing to draft_A's token,
    # not the bare apply URL that would load draft_B from session.
    resp = client.get(_lead_detail_url(school, lead_a))
    assert resp.status_code == 200

    content = resp.content.decode()
    # The "Open Form" href must contain draft_A's token, not draft_B's
    assert draft_a.token in content, "Lead A's detail page must link to draft_A's token"
    assert draft_b.token not in content, "Lead A's detail page must NOT reference draft_B's token"


@pytest.mark.django_db
def test_enrollment_form_shows_correct_student_after_multiple_start_enrollments(client):
    """
    After starting enrollment for leads A then B, opening lead A's form must
    show A's prefilled data (instrument, name), not B's.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    client.force_login(admin)

    lead_a = _make_lead(school, "Alice Wang", "piano", "alice@example.com")
    lead_b = _make_lead(school, "Ben Kim", "violin", "ben@example.com")
    lead_c = _make_lead(school, "Clara Diaz", "cello", "clara@example.com")

    # Start enrollment for all three in sequence
    for lead in (lead_a, lead_b, lead_c):
        client.post(_start_enrollment_url(school, lead))

    # Session now holds lead_c's draft — the last one started.
    draft_a = DraftSubmission.objects.filter(school=school, lead=lead_a).first()
    draft_b = DraftSubmission.objects.filter(school=school, lead=lead_b).first()
    draft_c = DraftSubmission.objects.filter(school=school, lead=lead_c).first()
    assert draft_a and draft_b and draft_c

    # Open lead A's form via the token — must load A's data
    client.logout()
    client.get(_resume_url(school, draft_a.token))
    form_resp = client.get(_apply_url(school))
    assert form_resp.status_code == 200
    content = form_resp.content.decode()
    assert "program:piano" in content, "Lead A's form must prefill piano (A's instrument)"
    assert "program:violin" not in content or \
           'value="program:violin" selected' not in content, \
        "Lead A's form must NOT pre-select violin (B's instrument)"

    # Open lead B's form — independent resume
    client.get(_resume_url(school, draft_b.token))
    form_resp_b = client.get(_apply_url(school))
    content_b = form_resp_b.content.decode()
    assert 'value="program:violin" selected' in content_b, \
        "Lead B's form must prefill violin"

    # Open lead C's form — independent resume
    client.get(_resume_url(school, draft_c.token))
    form_resp_c = client.get(_apply_url(school))
    content_c = form_resp_c.content.decode()
    assert 'value="program:cello" selected' in content_c, \
        "Lead C's form must prefill cello"


@pytest.mark.django_db
def test_status_update_only_affects_target_lead(client):
    """
    Updating status for lead B must not change lead A or lead C.
    """
    school = _sbmc_school()
    admin = _owner(school)
    client.force_login(admin)

    lead_a = _make_lead(school, "Alice Wang", "piano")
    lead_b = _make_lead(school, "Ben Kim", "violin")
    lead_c = _make_lead(school, "Clara Diaz", "cello")

    # Update only lead B to "contacted"
    client.post(
        _lead_status_url(school, lead_b),
        {"new_status": "contacted"},
    )

    lead_a.refresh_from_db()
    lead_b.refresh_from_db()
    lead_c.refresh_from_db()

    assert lead_a.status == LEAD_STATUS_NEW, "Lead A status must not change"
    assert lead_b.status == "contacted", "Lead B status must be updated"
    assert lead_c.status == LEAD_STATUS_NEW, "Lead C status must not change"


@pytest.mark.django_db
def test_notes_update_only_affects_target_lead(client):
    """
    Adding a note to lead B must not modify lead A or lead C.
    """
    school = _sbmc_school()
    admin = _owner(school)
    client.force_login(admin)

    lead_a = _make_lead(school, "Alice Wang", "piano")
    lead_a.notes = "A's existing note"
    lead_a.save(update_fields=["notes"])

    lead_b = _make_lead(school, "Ben Kim", "violin")
    lead_c = _make_lead(school, "Clara Diaz", "cello")

    client.post(
        _lead_update_url(school, lead_b),
        {
            "name": lead_b.name,
            "email": lead_b.email,
            "phone": "",
            "new_note": "B's note — private to Ben",
        },
    )

    lead_a.refresh_from_db()
    lead_b.refresh_from_db()
    lead_c.refresh_from_db()

    assert lead_a.notes == "A's existing note", "Lead A notes must be untouched"
    assert "B's note" in lead_b.notes, "Lead B notes must be updated"
    assert not lead_c.notes, "Lead C notes must remain empty"


@pytest.mark.django_db
def test_draft_data_isolation_between_leads(client):
    """
    Three concurrent drafts must carry independent prefill data.
    Draft A has piano, B has violin, C has cello.
    Each draft's data must match only its own lead.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    client.force_login(admin)

    lead_a = _make_lead(school, "Alice Wang", "piano", "alice@example.com")
    lead_b = _make_lead(school, "Ben Kim", "violin", "ben@example.com")
    lead_c = _make_lead(school, "Clara Diaz", "cello", "clara@example.com")

    for lead in (lead_a, lead_b, lead_c):
        client.post(_start_enrollment_url(school, lead))

    draft_a = DraftSubmission.objects.get(school=school, lead=lead_a, submitted_at__isnull=True)
    draft_b = DraftSubmission.objects.get(school=school, lead=lead_b, submitted_at__isnull=True)
    draft_c = DraftSubmission.objects.get(school=school, lead=lead_c, submitted_at__isnull=True)

    assert draft_a.data.get("instrument") == "program:piano"
    assert draft_b.data.get("instrument") == "program:violin"
    assert draft_c.data.get("instrument") == "program:cello"

    # Emails must also be isolated
    assert draft_a.data.get("guardian_email") == "alice@example.com"
    assert draft_b.data.get("guardian_email") == "ben@example.com"
    assert draft_c.data.get("guardian_email") == "clara@example.com"

    # No cross-contamination
    assert draft_a.data.get("instrument") != draft_b.data.get("instrument")
    assert draft_b.data.get("instrument") != draft_c.data.get("instrument")


@pytest.mark.django_db
def test_lead_detail_page_shows_correct_lead_data(client):
    """
    The lead detail page for lead B must show B's name and email,
    not A's, even if A was accessed immediately before.
    """
    school = _sbmc_school()
    admin = _owner(school)
    client.force_login(admin)

    lead_a = _make_lead(school, "Alice Wang", "piano", "alice@example.com")
    lead_b = _make_lead(school, "Ben Kim", "violin", "ben@example.com")

    # Visit A then immediately B
    client.get(_lead_detail_url(school, lead_a))
    resp_b = client.get(_lead_detail_url(school, lead_b))
    assert resp_b.status_code == 200

    content = resp_b.content.decode()
    assert "Ben Kim" in content, "Lead B detail page must show Ben Kim"
    assert "Alice Wang" not in content, "Lead B detail page must not show Alice Wang"
    assert "ben@example.com" in content
    assert "alice@example.com" not in content


# ===========================================================================
# 11. Admin "New Lead" form must match the YAML leads.fields config
# ===========================================================================
#
# The admin "New Lead" button must render the same fields as the school's
# public-facing trial/lead form (YAML leads.fields), so manually-created leads
# have the same data structure as public-submitted ones — which the enrollment
# form prefill depends on.


def _lead_create_url(school: School) -> str:
    return reverse("school_lead_create", kwargs={"school_slug": school.slug})


@pytest.mark.django_db
def test_admin_new_lead_form_renders_yaml_fields(client):
    """
    GET /schools/<slug>/admin/leads/new/ must render the YAML leads.fields.
    For SBMC: student_name, student_age, instrument — not hardcoded Name/Program.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    client.force_login(admin)

    resp = client.get(_lead_create_url(school))
    assert resp.status_code == 200
    content = resp.content.decode()

    # YAML field labels for SBMC leads.fields
    assert "Student Name" in content, "YAML field 'student_name' label must appear"
    assert "Student Age" in content, "YAML field 'student_age' label must appear"
    assert 'name="student_name"' in content, "input for student_name must be present"
    assert 'name="student_age"' in content, "input for student_age must be present"
    assert 'name="instrument"' in content, "select for instrument must be present"

    # Instrument options from YAML must be present
    assert "Piano" in content
    assert "Violin" in content
    assert "Viola" in content
    assert "Cello" in content

    # Email is always shown
    assert 'name="email"' in content


@pytest.mark.django_db
def test_admin_new_lead_creates_lead_with_form_fields(client):
    """
    POST to New Lead with YAML-driven fields must create a Lead whose
    data.form_fields mirrors what the public lead form would store.
    This is required so 'Start Enrollment' prefill works correctly.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    client.force_login(admin)

    resp = client.post(_lead_create_url(school), {
        "email": "newstudent@example.com",
        "phone": "3105550001",
        "student_name": "Sofia Reyes",
        "student_age": "10",
        "instrument": "cello",
    })
    assert resp.status_code == 302, "Successful create must redirect"

    lead = Lead.objects.filter(school=school, email="newstudent@example.com").first()
    assert lead is not None, "Lead must be created"
    assert lead.name == "Sofia Reyes", f"lead.name must be 'Sofia Reyes', got {lead.name!r}"
    assert lead.interested_in_value == "cello", f"interested_in_value must be 'cello', got {lead.interested_in_value!r}"
    assert lead.phone == "3105550001"

    form_fields = (lead.data or {}).get("form_fields", {})
    assert form_fields.get("student_name") == "Sofia Reyes", "form_fields.student_name must be stored"
    assert form_fields.get("student_age") == "10", "form_fields.student_age must be stored"
    assert form_fields.get("instrument") == "cello", "form_fields.instrument must be stored"


@pytest.mark.django_db
def test_admin_new_lead_then_start_enrollment_prefills_all_fields(client):
    """
    End-to-end: admin creates a lead via the New Lead form, then starts
    enrollment. The enrollment form draft must have all YAML fields prefilled,
    identical to a lead submitted through the public form.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    client.force_login(admin)

    # Create lead via admin form
    client.post(_lead_create_url(school), {
        "email": "endtoend@example.com",
        "phone": "3105550002",
        "student_name": "Lena Park",
        "student_age": "8",
        "instrument": "violin",
    })
    lead = Lead.objects.get(school=school, email="endtoend@example.com")

    # Start enrollment
    client.post(_start_enrollment_url(school, lead))
    draft = DraftSubmission.objects.filter(school=school, lead=lead).first()
    assert draft is not None

    # All enrollment form fields must be prefilled
    assert draft.data.get("student_first_name") == "Lena"
    assert draft.data.get("student_last_name") == "Park"
    assert draft.data.get("guardian_email") == "endtoend@example.com"
    assert draft.data.get("guardian_phone") == "3105550002"
    assert draft.data.get("student_age") == "8"
    assert draft.data.get("instrument") == "program:violin", (
        f"instrument must be normalized to 'program:violin', got {draft.data.get('instrument')!r}"
    )


@pytest.mark.django_db
def test_admin_new_lead_same_student_different_instrument_creates_separate_leads(client):
    """
    Regression: same student name + same guardian email + different instrument must
    create two separate leads — not deduplicate. A student enrolled in piano AND
    violin simultaneously is a real SBMC scenario.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    client.force_login(admin)

    client.post(_lead_create_url(school), {
        "email": "multilesson@example.com",
        "phone": "3105550003",
        "student_name": "Sofia Reyes",
        "student_age": "10",
        "instrument": "piano",
    })
    client.post(_lead_create_url(school), {
        "email": "multilesson@example.com",
        "phone": "3105550003",
        "student_name": "Sofia Reyes",
        "student_age": "10",
        "instrument": "violin",
    })

    leads = Lead.objects.filter(school=school, email="multilesson@example.com").order_by("created_at")
    assert leads.count() == 2, f"Expected 2 leads, got {leads.count()}"
    instruments = {l.interested_in_value for l in leads}
    assert instruments == {"piano", "violin"}, f"Expected piano+violin, got {instruments}"


@pytest.mark.django_db
def test_admin_new_lead_missing_required_yaml_field_blocks_create(client):
    """
    Regression: YAML fields marked required: true must be enforced on the admin
    New Lead form. For SBMC, student_name and instrument are both required.
    Submitting without them must re-render the form (200) and not create a lead.
    """
    school = _sbmc_school()
    _sbmc_programs(school)
    admin = _owner(school)
    client.force_login(admin)

    # Missing student_name (required in SBMC leads.fields)
    resp = client.post(_lead_create_url(school), {
        "email": "missing@example.com",
        "phone": "3105550004",
        "student_age": "9",
        "instrument": "piano",
        # student_name deliberately omitted
    })
    assert resp.status_code == 200, "Missing required field must re-render form, not redirect"
    assert not Lead.objects.filter(school=school, email="missing@example.com").exists(), (
        "No lead must be created when a required YAML field is missing"
    )

    # Missing instrument (required in SBMC leads.fields)
    resp2 = client.post(_lead_create_url(school), {
        "email": "missinginstrument@example.com",
        "phone": "3105550005",
        "student_name": "Clara Park",
        "student_age": "7",
        # instrument deliberately omitted
    })
    assert resp2.status_code == 200, "Missing required instrument must re-render form"
    assert not Lead.objects.filter(school=school, email="missinginstrument@example.com").exists()
