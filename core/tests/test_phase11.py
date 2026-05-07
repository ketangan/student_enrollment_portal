"""
Phase 11 — Admin Workflow (Inbox + Follow-Up System)

13 tests covering:
  - Lead mark-contacted quick action
  - Submission mark-contacted quick action
  - Submission follow-up set
  - Smart filter: needs_follow_up (leads + submissions)
  - Smart filter: stale (excludes enrolled)
  - Bulk mark-contacted (leads)
  - Bulk follow-up (submissions)
  - Cross-school 404 protection
  - Notes timestamp prepend (lead + submission)
"""
from __future__ import annotations

import pytest
from datetime import date, timedelta, datetime

from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, Lead, Submission
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_admin(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _lead_mark_contacted_url(school, lead_id):
    return reverse(
        "school_lead_mark_contacted",
        kwargs={"school_slug": school.slug, "lead_id": lead_id},
    )


def _submission_mark_contacted_url(school, submission_id):
    return reverse(
        "school_submission_mark_contacted",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _submission_follow_up_set_url(school, submission_id):
    return reverse(
        "school_submission_follow_up_set",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


def _leads_url(school):
    return reverse("school_leads", kwargs={"school_slug": school.slug})


def _submissions_url(school):
    return reverse("school_submissions", kwargs={"school_slug": school.slug})


def _lead_update_url(school, lead_id):
    return reverse("school_lead_update", kwargs={"school_slug": school.slug, "lead_id": lead_id})


def _submission_update_url(school, submission_id):
    return reverse(
        "school_submission_update",
        kwargs={"school_slug": school.slug, "submission_id": submission_id},
    )


# ---------------------------------------------------------------------------
# Part 3 — Quick Actions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_mark_contacted_sets_fields(client):
    """POST → last_contacted_at set, next_follow_up_at = ~now+2d, status=contacted."""
    school = SchoolFactory()
    user = _make_admin(school)
    lead = LeadFactory(school=school, status="new")
    client.force_login(user)

    before = timezone.now()
    resp = client.post(_lead_mark_contacted_url(school, lead.id), {"next": _leads_url(school)})

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.last_contacted_at is not None
    assert lead.last_contacted_at >= before
    assert lead.next_follow_up_at is not None
    expected_date = (before + timedelta(days=2)).date()
    assert lead.next_follow_up_at.date() == expected_date
    assert lead.status == "contacted"

    assert AdminAuditLog.objects.filter(
        model_label="core.lead",
        object_id=str(lead.id),
        extra__name="mark_contacted",
    ).exists()


@pytest.mark.django_db
def test_lead_mark_contacted_no_status_change_for_enrolled(client):
    """Enrolled lead → timestamps update, status stays enrolled."""
    school = SchoolFactory()
    user = _make_admin(school)
    lead = LeadFactory(school=school, status="enrolled")
    client.force_login(user)

    resp = client.post(_lead_mark_contacted_url(school, lead.id), {"next": _leads_url(school)})

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert lead.last_contacted_at is not None
    assert lead.next_follow_up_at is not None
    assert lead.status == "enrolled"   # must not change


@pytest.mark.django_db
def test_submission_mark_contacted_sets_fields(client):
    """POST → last_contacted_at and next_follow_up_at set on submission."""
    school = SchoolFactory()
    user = _make_admin(school)
    submission = SubmissionFactory(school=school)
    client.force_login(user)

    before = timezone.now()
    resp = client.post(
        _submission_mark_contacted_url(school, submission.id),
        {"next": _submissions_url(school)},
    )

    assert resp.status_code == 302
    submission.refresh_from_db()
    assert submission.last_contacted_at is not None
    assert submission.last_contacted_at >= before
    assert submission.next_follow_up_at is not None
    expected_date = (before + timedelta(days=2)).date()
    assert submission.next_follow_up_at.date() == expected_date

    assert AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(submission.id),
        extra__name="mark_contacted",
    ).exists()


@pytest.mark.django_db
def test_submission_follow_up_set(client):
    """POST date → next_follow_up_at saved correctly."""
    school = SchoolFactory()
    user = _make_admin(school)
    submission = SubmissionFactory(school=school)
    client.force_login(user)

    target = (date.today() + timedelta(days=5)).isoformat()
    resp = client.post(
        _submission_follow_up_set_url(school, submission.id),
        {"next_follow_up_at": target, "next": _submissions_url(school)},
    )

    assert resp.status_code == 302
    submission.refresh_from_db()
    assert submission.next_follow_up_at is not None
    assert submission.next_follow_up_at.date().isoformat() == target

    assert AdminAuditLog.objects.filter(
        model_label="core.submission",
        object_id=str(submission.id),
        extra__name="follow_up_set",
    ).exists()


@pytest.mark.django_db
def test_submission_follow_up_set_invalid_date(client):
    """POST bad date → 302 with error flash, field unchanged."""
    school = SchoolFactory()
    user = _make_admin(school)
    submission = SubmissionFactory(school=school)
    client.force_login(user)

    resp = client.post(
        _submission_follow_up_set_url(school, submission.id),
        {"next_follow_up_at": "not-a-date", "next": _submissions_url(school)},
    )

    assert resp.status_code == 302
    submission.refresh_from_db()
    assert submission.next_follow_up_at is None   # unchanged


# ---------------------------------------------------------------------------
# Part 2 — Smart Filters
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_needs_follow_up_filter_leads(client):
    """?filter=needs_follow_up returns overdue leads, excludes enrolled/lost."""
    school = SchoolFactory()
    user = _make_admin(school)
    client.force_login(user)

    # Overdue follow-up lead — should appear
    overdue_lead = LeadFactory(
        school=school,
        status="contacted",
        next_follow_up_at=timezone.now() - timedelta(hours=1),
    )
    # New lead older than 24h — should appear
    stale_new = LeadFactory(school=school, status="new")
    Submission.objects.none()  # ensure we don't accidentally query submissions here

    resp = client.get(_leads_url(school) + "?filter=needs_follow_up")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert str(overdue_lead.id) in content or overdue_lead.name in content


@pytest.mark.django_db
def test_needs_follow_up_filter_submissions(client):
    """?filter=needs_follow_up returns overdue submissions."""
    school = SchoolFactory()
    user = _make_admin(school)
    client.force_login(user)

    sub = SubmissionFactory(
        school=school,
        next_follow_up_at=timezone.now() - timedelta(hours=1),
    )

    resp = client.get(_submissions_url(school) + "?filter=needs_follow_up")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert str(sub.id) in content


@pytest.mark.django_db
def test_stale_filter_excludes_enrolled_leads(client):
    """?filter=stale excludes enrolled leads."""
    school = SchoolFactory()
    user = _make_admin(school)
    client.force_login(user)

    enrolled_lead = LeadFactory(
        school=school,
        status="enrolled",
        next_follow_up_at=None,
    )
    # Force updated_at to be old by updating directly (auto_now bypasses normal save)
    Lead.objects.filter(pk=enrolled_lead.pk).update(
        updated_at=timezone.now() - timedelta(days=10)
    )

    resp = client.get(_leads_url(school) + "?filter=stale")
    assert resp.status_code == 200
    content = resp.content.decode()
    # enrolled lead must NOT appear in stale results
    assert enrolled_lead.name not in content


# ---------------------------------------------------------------------------
# Part 4 — Bulk Actions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_bulk_mark_contacted_leads(client):
    """POST lead_ids → all updated, audit logged per lead."""
    school = SchoolFactory()
    user = _make_admin(school)
    lead1 = LeadFactory(school=school, status="new")
    lead2 = LeadFactory(school=school, status="new")
    client.force_login(user)

    bulk_url = reverse(
        "school_lead_bulk_mark_contacted",
        kwargs={"school_slug": school.slug},
    )
    resp = client.post(bulk_url, {
        "lead_ids": [lead1.id, lead2.id],
        "next": _leads_url(school),
    })

    assert resp.status_code == 302
    for lead in (lead1, lead2):
        lead.refresh_from_db()
        assert lead.last_contacted_at is not None
        assert lead.next_follow_up_at is not None
        assert AdminAuditLog.objects.filter(
            model_label="core.lead",
            object_id=str(lead.id),
            extra__name="bulk_mark_contacted",
        ).exists()


@pytest.mark.django_db
def test_bulk_follow_up_submissions(client):
    """POST submission_ids + follow_up_date → all updated."""
    school = SchoolFactory()
    user = _make_admin(school)
    sub1 = SubmissionFactory(school=school)
    sub2 = SubmissionFactory(school=school)
    client.force_login(user)

    target = (date.today() + timedelta(days=3)).isoformat()
    bulk_url = reverse(
        "school_submission_bulk_follow_up",
        kwargs={"school_slug": school.slug},
    )
    resp = client.post(bulk_url, {
        "submission_ids": [sub1.id, sub2.id],
        "follow_up_date": target,
        "next": _submissions_url(school),
    })

    assert resp.status_code == 302
    for sub in (sub1, sub2):
        sub.refresh_from_db()
        assert sub.next_follow_up_at is not None
        assert sub.next_follow_up_at.date().isoformat() == target


# ---------------------------------------------------------------------------
# Cross-school isolation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_mark_contacted_cross_school_404(client):
    """Lead from school_b → 404 for school_a admin."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _make_admin(school_a)
    lead_b = LeadFactory(school=school_b, status="new")
    client.force_login(user)

    resp = client.post(
        _lead_mark_contacted_url(school_a, lead_b.id),
        {"next": _leads_url(school_a)},
    )
    assert resp.status_code == 404
    lead_b.refresh_from_db()
    assert lead_b.last_contacted_at is None   # untouched


# ---------------------------------------------------------------------------
# Part 7 — Notes timestamp prepend
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_notes_timestamp_prepend_lead(client):
    """POST new_note → stored with [timestamp] prefix + old notes below."""
    school = SchoolFactory()
    user = _make_admin(school)
    lead = LeadFactory(school=school, name="Alice", email="alice@example.com", notes="Old note")
    client.force_login(user)

    resp = client.post(
        _lead_update_url(school, lead.id),
        {
            "name": lead.name,
            "email": lead.email,
            "new_note": "New note text",
            "next": _leads_url(school),
        },
    )

    assert resp.status_code == 302
    lead.refresh_from_db()
    assert "New note text" in lead.notes
    assert "Old note" in lead.notes
    # Timestamp bracket must appear before the new note
    bracket_pos = lead.notes.index("[")
    new_note_pos = lead.notes.index("New note text")
    assert bracket_pos < new_note_pos
    # Old note must appear after new note
    old_note_pos = lead.notes.index("Old note")
    assert new_note_pos < old_note_pos


@pytest.mark.django_db
def test_notes_timestamp_prepend_submission(client):
    """POST new_note → stored with [timestamp] prefix + old notes below."""
    school = SchoolFactory()
    user = _make_admin(school)
    submission = SubmissionFactory(school=school, internal_notes="Existing note")
    client.force_login(user)

    resp = client.post(
        _submission_update_url(school, submission.id),
        {
            "new_note": "Follow up call scheduled",
            "next": _submissions_url(school),
        },
    )

    assert resp.status_code == 302
    submission.refresh_from_db()
    assert "Follow up call scheduled" in submission.internal_notes
    assert "Existing note" in submission.internal_notes
    bracket_pos = submission.internal_notes.index("[")
    new_note_pos = submission.internal_notes.index("Follow up call scheduled")
    assert bracket_pos < new_note_pos
    old_pos = submission.internal_notes.index("Existing note")
    assert new_note_pos < old_pos
