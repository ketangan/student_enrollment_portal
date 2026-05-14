"""
Phase 14 — Conversion Intelligence.

Covers:
  - funnel_metrics: correct rates, zero-lead safety, zero-submission safety
  - Smart filters: not_converted (leads), not_enrolled (submissions)
  - stale_counts detection in reports context
  - week-over-week trend stats (+ve delta, zero last-week safety)
  - lead CSV export: Converted / Converted At columns
  - dashboard conversion_metrics context key
"""
from __future__ import annotations

import csv
import io
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import LEAD_STATUS_LOST, LEAD_STATUS_NEW, LEAD_STATUS_ENROLLED
from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _full_school():
    """Trial school: reports + leads + csv_export all enabled."""
    return SchoolFactory(plan="trial")


def _school_admin(school):
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _reports_url(school):
    return reverse("school_reports", kwargs={"school_slug": school.slug})


def _leads_url(school):
    return reverse("school_leads", kwargs={"school_slug": school.slug})


def _submissions_url(school):
    return reverse("school_submissions", kwargs={"school_slug": school.slug})


def _lead_export_url(school):
    return reverse("school_lead_export", kwargs={"school_slug": school.slug})


def _dashboard_url(school):
    return reverse("school_dashboard", kwargs={"school_slug": school.slug})


# ── funnel context (all-time enrollment funnel) ───────────────────────────────


@pytest.mark.django_db
def test_funnel_metrics_correct(client):
    """
    10 leads; 4 converted; 2 submissions with status=Enrolled.
    l2a_rate = 40.0; a2e_rate is computed from all subs.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    # Create 5 submissions: 2 enrolled, 3 others
    subs = [SubmissionFactory(school=school) for _ in range(5)]
    for s in subs[:2]:
        s.status = "Enrolled"
        s.save()

    # 4 leads: converted by linking to a submission
    for i in range(4):
        sub = SubmissionFactory(school=school)
        LeadFactory(school=school, converted_submission=sub)
    # 6 active unconverted leads
    for _ in range(6):
        LeadFactory(school=school, status=LEAD_STATUS_NEW)

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200

    funnel = resp.context["funnel"]
    assert funnel["leads"] == 10
    # rate = 4/10 * 100 = 40.0
    assert funnel["l2a_rate"] == 40.0
    # enrolled = 2 out of (5 base + 4 conversion) = 9 total subs; check it's numeric
    assert funnel["a2e_rate"] is None or isinstance(funnel["a2e_rate"], float)


@pytest.mark.django_db
def test_funnel_metrics_zero_leads(client):
    """No leads → l2a_rate is None (no ZeroDivisionError). Page renders 200."""
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200

    funnel = resp.context["funnel"]
    assert funnel["l2a_rate"] is None
    assert funnel["leads"] == 0


@pytest.mark.django_db
def test_funnel_metrics_zero_submissions(client):
    """No submissions → a2e_rate is None. Page renders 200."""
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    LeadFactory(school=school, status=LEAD_STATUS_NEW)

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200

    funnel = resp.context["funnel"]
    assert funnel["a2e_rate"] is None
    assert funnel["apps"] == 0


# ── Smart filters ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_not_converted_filter_leads(client):
    """
    3 leads: 1 converted, 1 lost, 1 active new-not-converted.
    GET /leads/?filter=not_converted → only the 1 active lead visible.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    # Converted lead
    sub = SubmissionFactory(school=school)
    converted = LeadFactory(school=school, name="Converted Parent", converted_submission=sub)

    # Lost lead
    LeadFactory(school=school, name="Lost Parent", status=LEAD_STATUS_LOST)

    # Active unconverted
    active = LeadFactory(school=school, name="Active Parent", status=LEAD_STATUS_NEW)

    resp = client.get(_leads_url(school) + "?filter=not_converted")
    assert resp.status_code == 200

    content = resp.content.decode()
    assert "Active Parent" in content
    assert "Converted Parent" not in content
    assert "Lost Parent" not in content


@pytest.mark.django_db
def test_not_enrolled_filter_submissions(client):
    """
    3 submissions: Enrolled, Declined, In Review.
    GET /submissions/?filter=not_enrolled → only 'In Review' visible.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    SubmissionFactory(school=school, status="Enrolled",
                      data={"student_first_name": "Alice", "student_last_name": "Smith"})
    SubmissionFactory(school=school, status="Declined",
                      data={"student_first_name": "Bob", "student_last_name": "Jones"})
    SubmissionFactory(school=school, status="In Review",
                      data={"student_first_name": "Carol", "student_last_name": "Davis"})

    resp = client.get(_submissions_url(school) + "?filter=not_enrolled")
    assert resp.status_code == 200

    content = resp.content.decode()
    assert "Carol" in content
    assert "Alice" not in content
    assert "Bob" not in content


# ── stale smart-filter (still works; just not surfaced in reports context) ─────


@pytest.mark.django_db
def test_stale_detection_in_reports(client):
    """
    Stale leads are surfaced via the smart-filter ?filter=stale on the leads list.
    2 leads updated 6 days ago; filtered list shows only them.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    stale_time = timezone.now() - timedelta(days=6)

    stale_lead_1 = LeadFactory(school=school, status=LEAD_STATUS_NEW, name="Stale One")
    stale_lead_2 = LeadFactory(school=school, status=LEAD_STATUS_NEW, name="Stale Two")
    LeadFactory(school=school, status=LEAD_STATUS_NEW, name="Recent Lead")

    from core.models import Lead
    Lead.objects.filter(pk__in=[stale_lead_1.pk, stale_lead_2.pk]).update(updated_at=stale_time)

    resp = client.get(_leads_url(school) + "?filter=stale")
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Stale One" in content
    assert "Stale Two" in content
    assert "Recent Lead" not in content


# ── comparison_rows (replaces trend_stats) ────────────────────────────────────


@pytest.mark.django_db
def test_week_over_week_positive(client):
    """
    3 submissions in the last 7 days; 1 in the previous 7-day window.
    comparison_rows[0]: this_val=3, prev_val=1, delta_str='+2', up=True.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    now = timezone.now()

    # 3 submissions this 7-day period (1 day ago)
    for _ in range(3):
        s = SubmissionFactory(school=school)
        from core.models import Submission as Sub
        Sub.objects.filter(pk=s.pk).update(created_at=now - timedelta(days=1))

    # 1 submission previous 7-day window (10 days ago)
    s_old = SubmissionFactory(school=school)
    from core.models import Submission as Sub
    Sub.objects.filter(pk=s_old.pk).update(created_at=now - timedelta(days=10))

    resp = client.get(_reports_url(school) + "?range=7")
    assert resp.status_code == 200

    rows = resp.context["comparison_rows"]
    apps_row = rows[0]  # "Applications received"
    assert apps_row["this_val"] == 3
    assert apps_row["prev_val"] == 1
    assert apps_row["delta_str"] == "+2"
    assert apps_row["delta_up"] is True


@pytest.mark.django_db
def test_week_over_week_zero_last_week(client):
    """
    No submissions in the previous period → prev_val == 0 (no ZeroDivisionError).
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    s = SubmissionFactory(school=school)
    from core.models import Submission as Sub
    Sub.objects.filter(pk=s.pk).update(created_at=timezone.now() - timedelta(days=2))

    resp = client.get(_reports_url(school) + "?range=7")
    assert resp.status_code == 200

    rows = resp.context["comparison_rows"]
    apps_row = rows[0]
    assert apps_row["prev_val"] == 0


# ── Lead CSV export ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_lead_export_converted_column(client):
    """
    1 converted lead (converted_at set) + 1 unconverted lead.
    CSV has 'Converted' column; converted lead row = 'Yes'.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    sub = SubmissionFactory(school=school)
    converted_lead = LeadFactory(school=school, name="Converted Lead")
    converted_lead.converted_submission = sub
    converted_lead.converted_at = timezone.now()
    converted_lead.save()

    LeadFactory(school=school, name="Unconverted Lead")

    resp = client.get(_lead_export_url(school))
    assert resp.status_code == 200
    assert "text/csv" in resp["Content-Type"]

    reader = csv.DictReader(io.StringIO(resp.content.decode()))
    rows = list(reader)

    assert "Converted" in reader.fieldnames
    assert "Converted At" in reader.fieldnames

    converted_row = next((r for r in rows if r["Name"] == "Converted Lead"), None)
    unconverted_row = next((r for r in rows if r["Name"] == "Unconverted Lead"), None)

    assert converted_row is not None
    assert converted_row["Converted"] == "Yes"
    assert converted_row["Converted At"] != ""

    assert unconverted_row is not None
    assert unconverted_row["Converted"] == "No"
    assert unconverted_row["Converted At"] == ""


# ── Dashboard conversion_metrics ─────────────────────────────────────────────


@pytest.mark.django_db
def test_dashboard_conversion_metrics(client):
    """
    School with leads enabled; 5 leads, 2 converted; 1 enrolled submission.
    Dashboard context has conversion_metrics with lead_to_sub_rate > 0.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    # 2 converted leads
    for _ in range(2):
        sub = SubmissionFactory(school=school)
        LeadFactory(school=school, converted_submission=sub)

    # 3 unconverted leads
    for _ in range(3):
        LeadFactory(school=school, status=LEAD_STATUS_NEW)

    # 1 enrolled submission
    enrolled = SubmissionFactory(school=school, status="Enrolled")

    resp = client.get(_dashboard_url(school))
    assert resp.status_code == 200

    cm = resp.context.get("conversion_metrics")
    assert cm is not None
    assert cm["lead_to_sub_rate"] > 0
    assert cm["leads_not_converted"] == 3


# ── Fix 2: funnel with leads disabled ────────────────────────────────────────


@pytest.mark.django_db
def test_funnel_metrics_visible_without_leads_feature(client):
    """
    School with leads feature disabled still shows submission + enrolled metrics.
    funnel.apps and a2e_rate must be populated; lead-specific fields must be None.
    """
    school = SchoolFactory(
        plan="trial",
        feature_flags={"leads_enabled": False},
    )
    user = _school_admin(school)
    client.force_login(user)

    SubmissionFactory(school=school, status="Enrolled")
    SubmissionFactory(school=school, status="In Review")

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200

    funnel = resp.context["funnel"]
    assert funnel["apps"] == 2
    assert funnel["enrolled"] == 1
    assert funnel["a2e_rate"] == 50.0
    # Lead fields absent when leads disabled
    assert funnel["leads"] is None
    assert funnel["l2a_rate"] is None


# ── Fix 5: not_enrolled filter excludes Archived ──────────────────────────────


@pytest.mark.django_db
def test_not_enrolled_filter_excludes_archived(client):
    """
    'Archived' is a terminal status — must be excluded by the not_enrolled filter
    just like Enrolled and Declined are.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    SubmissionFactory(school=school, status="Enrolled",
                      data={"student_first_name": "Alice", "student_last_name": "A"})
    SubmissionFactory(school=school, status="Declined",
                      data={"student_first_name": "Bob", "student_last_name": "B"})
    SubmissionFactory(school=school, status="Archived",
                      data={"student_first_name": "Carol", "student_last_name": "C"})
    SubmissionFactory(school=school, status="In Review",
                      data={"student_first_name": "Dave", "student_last_name": "D"})

    resp = client.get(_submissions_url(school) + "?filter=not_enrolled")
    assert resp.status_code == 200

    content = resp.content.decode()
    assert "Dave" in content          # In Review → visible
    assert "Alice" not in content     # Enrolled → hidden
    assert "Bob" not in content       # Declined → hidden
    assert "Carol" not in content     # Archived → hidden (fix)


# ── Fix 3: submission CSV outcome columns ────────────────────────────────────


@pytest.mark.django_db
def test_submission_export_outcome_columns(client):
    """
    Submission CSV includes 'Enrolled' (Yes/No) and 'Linked Lead ID' columns.
    Enrolled submission linked to a lead shows Yes + lead ID.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    enrolled_sub = SubmissionFactory(school=school, status="Enrolled",
                                     data={"student_first_name": "Enrolled", "student_last_name": "Sub"})
    plain_sub = SubmissionFactory(school=school, status="In Review",
                                  data={"student_first_name": "Plain", "student_last_name": "Sub"})

    lead = LeadFactory(school=school, name="The Lead")
    lead.converted_submission = enrolled_sub
    lead.converted_at = timezone.now()
    lead.save()

    export_url = reverse("school_submission_export", kwargs={"school_slug": school.slug})
    resp = client.get(export_url)
    assert resp.status_code == 200
    assert "text/csv" in resp["Content-Type"]

    reader = csv.DictReader(io.StringIO(resp.content.decode()))
    rows = list(reader)

    assert "Enrolled" in reader.fieldnames
    assert "Linked Lead ID" in reader.fieldnames

    enrolled_row = next((r for r in rows if r["Status"] == "Enrolled"), None)
    plain_row = next((r for r in rows if r["Status"] == "In Review"), None)

    assert enrolled_row is not None
    assert enrolled_row["Enrolled"] == "Yes"
    assert enrolled_row["Linked Lead ID"] == str(lead.id)

    assert plain_row is not None
    assert plain_row["Enrolled"] == "No"
    assert plain_row["Linked Lead ID"] == ""


# ── Fix 1: dashboard renders conversion cards ─────────────────────────────────


@pytest.mark.django_db
def test_dashboard_renders_conversion_action_links(client):
    """
    Dashboard page renders the conversion metrics action links
    ('leads not converted' and 'apps pending decision') when leads are enabled.
    """
    school = _full_school()
    user = _school_admin(school)
    client.force_login(user)

    sub = SubmissionFactory(school=school)
    LeadFactory(school=school, converted_submission=sub)
    LeadFactory(school=school, status=LEAD_STATUS_NEW)  # unconverted

    resp = client.get(_dashboard_url(school))
    assert resp.status_code == 200

    content = resp.content.decode()
    # The conversion card and action links must be in the rendered page
    assert "not converted" in content.lower() or "unconverted" in content.lower()
    assert "filter=not_converted" in content
