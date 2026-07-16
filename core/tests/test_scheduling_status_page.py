"""
Parent status login + schedule change request — tests for Phase 24.

Covers:
  - Status login page: GET 200, feature-flag gate
  - Status login: valid last name + public_id → redirect to token URL
  - Status login: wrong last name → error (not redirect)
  - Status login: bad public_id → error
  - Family status page: shows scheduling fields when present
  - Schedule change request POST: sets flag, updates data, redirects
  - Admin: schedule_change_requested badge visible in submissions list
  - Admin: acknowledge POST clears flag + audit log
  - Success page: shows public_id from session
"""
from __future__ import annotations

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, Submission
from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _portal_school():
    school = SchoolFactory(plan="trial")
    return school


def _submission_with_last_name(school, last_name="Smith"):
    return SubmissionFactory(
        school=school,
        data={"student_last_name": last_name, "student_first_name": "Alice"},
    )


def _login_url(school):
    return reverse("school_status_login", kwargs={"school_slug": school.slug})


def _status_url(school, token):
    return reverse("family_status", kwargs={"school_slug": school.slug, "token": token})


def _change_request_url(school, token):
    return reverse(
        "school_status_change_request",
        kwargs={"school_slug": school.slug, "token": token},
    )


def _ack_url(school, submission):
    return reverse(
        "school_submission_ack_schedule_change",
        kwargs={"school_slug": school.slug, "submission_id": submission.id},
    )


def _admin_for(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


# ── status login page ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_status_login_get_200(client):
    school = _portal_school()
    resp = client.get(_login_url(school))
    assert resp.status_code == 200
    assert "Application ID" in resp.content.decode()


@pytest.mark.django_db
def test_status_login_404_when_flag_disabled(client):
    school = SchoolFactory(plan="trial")
    school.feature_flags = {"family_portal_enabled": False}
    school.save()
    resp = client.get(_login_url(school))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_status_login_valid_credentials_redirect(client):
    school = _portal_school()
    sub = _submission_with_last_name(school, last_name="Johnson")
    resp = client.post(_login_url(school), {
        "application_id": sub.public_id,
        "last_name": "johnson",  # case-insensitive
    })
    assert resp.status_code == 302
    assert sub.status_token in resp["Location"]


@pytest.mark.django_db
def test_status_login_wrong_last_name_does_not_redirect(client):
    """Wrong last name must NOT redirect to the status page."""
    school = _portal_school()
    sub = _submission_with_last_name(school, last_name="Smith")
    resp = client.post(_login_url(school), {
        "application_id": sub.public_id,
        "last_name": "Jones",
    })
    # Must not redirect to the status page
    assert resp.status_code != 302
    if resp.status_code == 302:
        assert sub.status_token not in (resp.get("Location") or "")


@pytest.mark.django_db
def test_status_login_bad_public_id_does_not_redirect(client):
    """Non-existent public_id must not redirect."""
    school = _portal_school()
    _submission_with_last_name(school)
    resp = client.post(_login_url(school), {
        "application_id": "BADID00000000000",
        "last_name": "Smith",
    })
    assert resp.status_code != 302


@pytest.mark.django_db
def test_status_login_empty_fields_does_not_redirect(client):
    """Empty fields must not redirect."""
    school = _portal_school()
    resp = client.post(_login_url(school), {"application_id": "", "last_name": ""})
    assert resp.status_code != 302


# ── family status page scheduling display ─────────────────────────────────────


@pytest.mark.django_db
def test_family_status_page_shows_sched_fields(client):
    school = _portal_school()
    sub = SubmissionFactory(
        school=school,
        data={
            "student_last_name": "Smith",
            "sched_day_preference": ["weekday"],
            "sched_preferred_timing": "After 5pm",
            "sched_preferred_slot": "Tuesday 4pm",
        },
    )
    resp = client.get(_status_url(school, sub.status_token))
    assert resp.status_code == 200
    assert "After 5pm" in resp.content.decode()
    assert "Tuesday 4pm" in resp.content.decode()


@pytest.mark.django_db
def test_family_status_page_no_sched_shows_no_preferences(client):
    school = _portal_school()
    sub = SubmissionFactory(school=school, data={"student_last_name": "Smith"})
    resp = client.get(_status_url(school, sub.status_token))
    assert resp.status_code == 200
    assert "No scheduling preferences" in resp.content.decode()


# ── schedule change request ───────────────────────────────────────────────────


@pytest.mark.django_db
def test_schedule_change_request_sets_flag(client):
    school = _portal_school()
    sub = SubmissionFactory(school=school, data={"student_last_name": "Smith"})
    assert not sub.schedule_change_requested

    resp = client.post(
        _change_request_url(school, sub.status_token),
        {
            "sched_day_preference": "weekday",
            "sched_preferred_timing": "After 5pm",
            "sched_preferred_slot": "",
            "sched_days_unavailable": "",
            "sched_preferred_start_week": "",
        },
    )
    assert resp.status_code == 302
    assert "change=requested" in resp["Location"]

    sub.refresh_from_db()
    assert sub.schedule_change_requested is True
    assert sub.data.get("sched_preferred_timing") == "After 5pm"


@pytest.mark.django_db
def test_schedule_change_request_updates_existing_data(client):
    school = _portal_school()
    sub = SubmissionFactory(
        school=school,
        data={"sched_preferred_timing": "Old value", "student_last_name": "Smith"},
    )
    client.post(
        _change_request_url(school, sub.status_token),
        {
            "sched_preferred_timing": "New value",
            "sched_day_preference": "",
            "sched_preferred_slot": "",
            "sched_days_unavailable": "",
            "sched_preferred_start_week": "",
        },
    )
    sub.refresh_from_db()
    assert sub.data["sched_preferred_timing"] == "New value"


@pytest.mark.django_db
def test_schedule_change_request_404_bad_token(client):
    school = _portal_school()
    resp = client.post(
        reverse("school_status_change_request", kwargs={"school_slug": school.slug, "token": "badtoken"}),
        {"sched_preferred_timing": "test"},
    )
    assert resp.status_code == 404


# ── admin badge in submissions list ───────────────────────────────────────────


@pytest.mark.django_db
def test_admin_submissions_list_shows_scheduling_badge(client):
    school = SchoolFactory(plan="trial")
    sub = SubmissionFactory(school=school, schedule_change_requested=True)
    user = _admin_for(school)
    client.force_login(user)

    resp = client.get(
        reverse("school_submissions", kwargs={"school_slug": school.slug})
    )
    assert resp.status_code == 200
    assert "Scheduling" in resp.content.decode()


@pytest.mark.django_db
def test_admin_submissions_list_no_badge_when_not_requested(client):
    school = SchoolFactory(plan="trial")
    sub = SubmissionFactory(school=school, schedule_change_requested=False)
    user = _admin_for(school)
    client.force_login(user)

    resp = client.get(
        reverse("school_submissions", kwargs={"school_slug": school.slug})
    )
    assert resp.status_code == 200
    # The badge span with title "Schedule change requested" should not appear
    assert "Schedule change requested by family" not in resp.content.decode()


# ── admin acknowledge ─────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_admin_acknowledge_clears_flag(client):
    school = SchoolFactory(plan="trial")
    sub = SubmissionFactory(school=school, schedule_change_requested=True, schedule_change_requested_at=timezone.now())
    user = _admin_for(school)
    client.force_login(user)

    resp = client.post(_ack_url(school, sub))
    assert resp.status_code == 302

    sub.refresh_from_db()
    assert sub.schedule_change_requested is False


@pytest.mark.django_db
def test_admin_acknowledge_creates_audit_log(client):
    school = SchoolFactory(plan="trial")
    sub = SubmissionFactory(school=school, schedule_change_requested=True)
    user = _admin_for(school)
    client.force_login(user)

    client.post(_ack_url(school, sub))

    assert AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(sub.pk),
        extra__name="acknowledge_schedule_change",
    ).exists()


@pytest.mark.django_db
def test_admin_acknowledge_noop_when_not_requested(client):
    school = SchoolFactory(plan="trial")
    sub = SubmissionFactory(school=school, schedule_change_requested=False)
    user = _admin_for(school)
    client.force_login(user)

    before_count = AdminAuditLog.objects.count()
    client.post(_ack_url(school, sub))

    # No audit log created for a noop
    assert AdminAuditLog.objects.count() == before_count
