"""
Phase 8 — CSV Export.

Tests:
  1. test_submission_export_csv      — GET returns text/csv with correct rows
  2. test_lead_export_csv            — GET returns text/csv with correct rows
  3. test_export_respects_filters    — status filter applied; non-matching rows absent
  4. test_export_is_school_scoped    — cross-school submissions never appear in export
"""
from __future__ import annotations

import csv
import io

import pytest
from django.urls import reverse

from core.models import AdminAuditLog, LEAD_STATUS_NEW
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _school_admin_user(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _submission_export_url(school, **params):
    base = reverse("school_submission_export", kwargs={"school_slug": school.slug})
    if params:
        from urllib.parse import urlencode
        return base + "?" + urlencode(params)
    return base


def _lead_export_url(school, **params):
    base = reverse("school_lead_export", kwargs={"school_slug": school.slug})
    if params:
        from urllib.parse import urlencode
        return base + "?" + urlencode(params)
    return base


def _parse_csv(response):
    """Return list of dicts from a CSV HttpResponse."""
    content = response.content.decode("utf-8")
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_submission_export_csv(client):
    """GET export returns 200 with text/csv and one data row per submission."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    s1 = SubmissionFactory(school=school, internal_notes="note1")
    s2 = SubmissionFactory(school=school, internal_notes="")

    client.force_login(user)
    resp = client.get(_submission_export_url(school))

    assert resp.status_code == 200
    assert "text/csv" in resp["Content-Type"]
    assert "attachment" in resp.get("Content-Disposition", "")

    rows = _parse_csv(resp)
    ids = {row["Submission ID"] for row in rows}
    assert str(s1.public_id) in ids
    assert str(s2.public_id) in ids

    # Fixed columns present
    assert "Status" in rows[0]
    assert "Submitted At" in rows[0]
    assert "Internal Notes" in rows[0]

    # Notes appear correctly
    note_row = next(r for r in rows if r["Submission ID"] == str(s1.public_id))
    assert note_row["Internal Notes"] == "note1"


@pytest.mark.django_db
def test_lead_export_csv(client):
    """GET leads export returns 200 with text/csv and one data row per lead."""
    school = SchoolFactory(feature_flags={"leads_enabled": True})
    user = _school_admin_user(school)

    lead = LeadFactory(
        school=school,
        name="Jane Doe",
        email="jane@example.com",
        status=LEAD_STATUS_NEW,
        notes="call back",
    )

    client.force_login(user)
    resp = client.get(_lead_export_url(school))

    assert resp.status_code == 200
    assert "text/csv" in resp["Content-Type"]
    assert "attachment" in resp.get("Content-Disposition", "")

    rows = _parse_csv(resp)
    assert len(rows) == 1
    row = rows[0]
    assert row["Name"] == "Jane Doe"
    assert row["Email"] == "jane@example.com"
    assert row["Status"] == LEAD_STATUS_NEW
    assert row["Notes"] == "call back"
    # Required columns present
    for col in ("Lead ID", "Phone", "Program Interest", "Created At",
                "Last Contacted At", "Next Follow Up"):
        assert col in row


@pytest.mark.django_db
def test_export_respects_filters(client):
    """?status= filter is applied; rows with non-matching status are absent."""
    school = SchoolFactory()
    user = _school_admin_user(school)

    keep = SubmissionFactory(school=school, status="New")
    drop = SubmissionFactory(school=school, status="Accepted")

    client.force_login(user)
    resp = client.get(_submission_export_url(school, status="New"))

    assert resp.status_code == 200
    rows = _parse_csv(resp)
    ids = {row["Submission ID"] for row in rows}
    assert str(keep.public_id) in ids
    assert str(drop.public_id) not in ids


@pytest.mark.django_db
def test_export_is_school_scoped(client):
    """Submissions belonging to a different school never appear in the export."""
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)

    own = SubmissionFactory(school=school_a)
    other = SubmissionFactory(school=school_b)

    client.force_login(user)
    resp = client.get(_submission_export_url(school_a))

    assert resp.status_code == 200
    rows = _parse_csv(resp)
    ids = {row["Submission ID"] for row in rows}
    assert str(own.public_id) in ids
    assert str(other.public_id) not in ids
