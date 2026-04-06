# core/tests/test_lead_conversion.py
from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Submission
from core.services.lead_conversion import try_convert_lead
from core.tests.factories import LeadFactory, SchoolFactory, SubmissionFactory


# ---------------------------------------------------------------------------
# Minimal config helpers
# ---------------------------------------------------------------------------

def _config_with_email(key="contact_email", required=True):
    """Single-form YAML config with one email field."""
    return {
        "form": {
            "sections": [
                {
                    "title": "Contact",
                    "fields": [
                        {"key": key, "label": "Email", "type": "email", "required": required},
                    ],
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# Service unit tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_try_convert_lead_links_submission_to_lead():
    school = SchoolFactory(plan="pro")
    lead = LeadFactory(school=school, email="alice@example.com")
    submission = SubmissionFactory(school=school, data={"contact_email": "alice@example.com"})

    result = try_convert_lead(
        school=school, submission=submission, config_raw=_config_with_email()
    )

    assert result is not None
    lead.refresh_from_db()
    assert lead.converted_submission_id == submission.id


@pytest.mark.django_db
def test_try_convert_lead_sets_converted_at_timestamp():
    school = SchoolFactory(plan="pro")
    lead = LeadFactory(school=school, email="bob@example.com")
    submission = SubmissionFactory(school=school, data={"contact_email": "bob@example.com"})

    before = timezone.now()
    try_convert_lead(school=school, submission=submission, config_raw=_config_with_email())
    after = timezone.now()

    lead.refresh_from_db()
    assert lead.converted_at is not None
    assert before <= lead.converted_at <= after


@pytest.mark.django_db
def test_try_convert_lead_returns_none_when_no_matching_lead():
    school = SchoolFactory(plan="pro")
    submission = SubmissionFactory(school=school, data={"contact_email": "nobody@example.com"})

    result = try_convert_lead(
        school=school, submission=submission, config_raw=_config_with_email()
    )

    assert result is None


@pytest.mark.django_db
def test_try_convert_lead_skips_when_feature_disabled():
    school = SchoolFactory(plan="starter")
    lead = LeadFactory(school=school, email="carol@example.com")
    submission = SubmissionFactory(school=school, data={"contact_email": "carol@example.com"})

    result = try_convert_lead(
        school=school, submission=submission, config_raw=_config_with_email()
    )

    assert result is None
    lead.refresh_from_db()
    assert lead.converted_submission_id is None


@pytest.mark.django_db
def test_try_convert_lead_skips_already_converted_lead():
    school = SchoolFactory(plan="pro")
    original_submission = SubmissionFactory(school=school)
    lead = LeadFactory(
        school=school,
        email="dave@example.com",
        converted_submission=original_submission,
    )
    new_submission = SubmissionFactory(school=school, data={"contact_email": "dave@example.com"})

    result = try_convert_lead(
        school=school, submission=new_submission, config_raw=_config_with_email()
    )

    assert result is None
    lead.refresh_from_db()
    assert lead.converted_submission_id == original_submission.id  # unchanged


@pytest.mark.django_db
def test_try_convert_lead_case_insensitive_email_match():
    school = SchoolFactory(plan="pro")
    lead = LeadFactory(school=school, email="alice@example.com")
    submission = SubmissionFactory(school=school, data={"contact_email": "ALICE@EXAMPLE.COM"})

    result = try_convert_lead(
        school=school, submission=submission, config_raw=_config_with_email()
    )

    assert result is not None
    lead.refresh_from_db()
    assert lead.converted_submission_id == submission.id


@pytest.mark.django_db
def test_try_convert_lead_does_not_match_different_school():
    """A lead from a different school with the same email must not be converted."""
    school_a = SchoolFactory(plan="pro")
    school_b = SchoolFactory(plan="pro")
    # Lead belongs to school_b only
    LeadFactory(school=school_b, email="eve@example.com")
    submission = SubmissionFactory(school=school_a, data={"contact_email": "eve@example.com"})

    result = try_convert_lead(
        school=school_a, submission=submission, config_raw=_config_with_email()
    )

    assert result is None


# ---------------------------------------------------------------------------
# View integration tests
# ---------------------------------------------------------------------------

def _make_apply_config(school, *, email_field=True):
    """Minimal config object for apply_view tests."""
    fields = [{"key": "first_name", "label": "First Name", "type": "text", "required": True}]
    if email_field:
        fields.append({"key": "contact_email", "label": "Email", "type": "email", "required": True})

    form = {"title": "Apply", "sections": [{"title": "Contact", "fields": fields}]}
    raw = {
        "school": {"slug": school.slug, "display_name": "Test School", "website_url": "", "source_url": ""},
        "form": form,
        "success": {
            "title": "Done",
            "message": "Thanks",
            "notifications": {
                "submission_email": {"to": "a@a.com", "from_email": "a@a.com", "subject": "New"},
                "applicant_confirmation": {"enabled": False},
            },
        },
    }

    class _Cfg:
        display_name = "Test School"
        branding = {}

    _Cfg.form = form
    _Cfg.raw = raw
    return _Cfg()


@pytest.mark.django_db
def test_apply_view_triggers_conversion_on_submit(client, monkeypatch, settings):
    """Single-form POST with a Pro school and matching lead converts the lead."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="pro", slug="conv-test-pro")
    lead = LeadFactory(school=school, email="apply@example.com")
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school))

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"contact_email": "apply@example.com", "first_name": "Apply"},
    )

    lead.refresh_from_db()
    assert lead.converted_submission_id is not None


@pytest.mark.django_db
def test_apply_view_no_conversion_when_no_lead(client, monkeypatch, settings):
    """Pro school with no pre-existing lead: submission created, no error."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="pro", slug="conv-test-no-lead")
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school))

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"contact_email": "newperson@example.com", "first_name": "New"},
    )

    assert Submission.objects.filter(school=school).count() == 1


@pytest.mark.django_db
def test_apply_view_conversion_skipped_for_starter_plan(client, monkeypatch, settings):
    """Starter school: lead exists but conversion is skipped."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="starter", slug="conv-test-starter")
    lead = LeadFactory(school=school, email="starter@example.com")
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school))

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"contact_email": "starter@example.com", "first_name": "Starter"},
    )

    lead.refresh_from_db()
    assert lead.converted_submission_id is None


@pytest.mark.django_db
def test_apply_view_conversion_skipped_when_no_email_in_config(client, monkeypatch, settings):
    """No email field in YAML config: submission created without error, no conversion."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="pro", slug="conv-test-no-email-cfg")
    lead = LeadFactory(school=school, email="noemail@example.com")
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school, email_field=False))

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"first_name": "Test"},
    )

    assert Submission.objects.filter(school=school).count() == 1
    lead.refresh_from_db()
    assert lead.converted_submission_id is None
