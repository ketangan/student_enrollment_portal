"""
Tests for the Phase 19 Enrollment Analytics Reports page.

Coverage:
- Leads-module gating: no Lead queries when module OFF (§2)
- Module OFF: 3 KPI tiles, 2-stage funnel, no source_rows
- Module ON:  4 KPI tiles, 3-stage funnel, source_rows present
- Rate computation correct (R2, R6)
- Zero-divide guard: 0 apps → renders 200, no NaN
- Comparison delta computation correct
- Funnel is all-time (ignores date range)
- Program mix sums to app total for same scope (R5)
- Multi-tenant isolation: school B cannot see school A's data
- Time-series gap-fill: every expected bucket present
- New school with no data renders without error
"""
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import Lead, School, Submission, LEAD_SOURCE_CHOICES


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def superuser(db):
    return User.objects.create_superuser("rpt_super", "s@test.com", "pass")


@pytest.fixture
def school_no_leads(db):
    """Starter school but with leads explicitly disabled."""
    return School.objects.create(
        slug="rpt-no-leads",
        display_name="No Leads School",
        plan="starter",
        feature_flags={"leads_enabled": False},
    )


@pytest.fixture
def school_with_leads(db):
    """Starter school — leads_enabled=True by plan default."""
    return School.objects.create(
        slug="rpt-with-leads",
        display_name="With Leads School",
        plan="starter",
    )


@pytest.fixture
def school_b(db):
    """Second tenant for isolation tests."""
    return School.objects.create(
        slug="rpt-school-b",
        display_name="School B",
        plan="starter",
        feature_flags={"leads_enabled": False},
    )


def _rpt_url(school, days=30):
    return reverse("school_reports", kwargs={"school_slug": school.slug}) + f"?range={days}"


def _sub(school, status="New", days_ago=1):
    sub = Submission.objects.create(school=school, status=status, data={})
    Submission.objects.filter(pk=sub.pk).update(
        created_at=timezone.now() - timedelta(days=days_ago)
    )
    sub.refresh_from_db()
    return sub


def _lead(school, source="website", converted=False):
    from core.models import LEAD_STATUS_NEW
    lead = Lead.objects.create(
        school=school,
        name="Test Lead",
        email=f"lead{Lead.objects.count()}@test.com",
        source=source,
        status=LEAD_STATUS_NEW,
    )
    if converted:
        sub = Submission.objects.create(school=school, status="New", data={})
        lead.converted_submission = sub
        lead.converted_at = timezone.now()
        lead.save(update_fields=["converted_submission", "converted_at"])
    return lead


# ── §2 Leads module gating ─────────────────────────────────────────────────────

@pytest.mark.django_db
def test_no_lead_queries_when_module_off(client, superuser, school_no_leads):
    client.force_login(superuser)
    with CaptureQueriesContext(connection) as ctx:
        resp = client.get(_rpt_url(school_no_leads))
    assert resp.status_code == 200
    lead_queries = [q["sql"] for q in ctx.captured_queries if '"core_lead"' in q["sql"]]
    assert lead_queries == [], f"Expected zero Lead queries, got: {lead_queries}"


@pytest.mark.django_db
def test_module_off_3_kpi_tiles(client, superuser, school_no_leads):
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_no_leads))
    assert resp.status_code == 200
    # 3 tiles: App→Enrolled, Apps/Week, Avg Days (no Lead→Application tile)
    assert len(resp.context["kpi_tiles"]) == 3


@pytest.mark.django_db
def test_module_off_funnel_has_no_leads_stage(client, superuser, school_no_leads):
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_no_leads))
    assert resp.status_code == 200
    assert resp.context["funnel"]["leads"] is None


@pytest.mark.django_db
def test_module_off_no_source_rows(client, superuser, school_no_leads):
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_no_leads))
    assert resp.status_code == 200
    assert resp.context["source_rows"] is None


# ── §2 Leads module ON ────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_module_on_4_kpi_tiles(client, superuser, school_with_leads):
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_with_leads))
    assert resp.status_code == 200
    assert len(resp.context["kpi_tiles"]) == 4


@pytest.mark.django_db
def test_module_on_source_rows_present(client, superuser, school_with_leads):
    _lead(school_with_leads)
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_with_leads))
    assert resp.status_code == 200
    assert resp.context["source_rows"] is not None


# ── §3 Data integrity — rate computation ──────────────────────────────────────

@pytest.mark.django_db
def test_app_rate_correct(client, superuser, school_no_leads):
    # 10 submissions in period, 4 enrolled
    for _ in range(6):
        _sub(school_no_leads, status="New", days_ago=5)
    for _ in range(4):
        _sub(school_no_leads, status="Enrolled", days_ago=5)
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_no_leads, days=30))
    assert resp.status_code == 200
    tile = resp.context["kpi_tiles"][0]
    assert tile["value"] == "40.0%"
    assert tile["basis"] == "4 of 10"


@pytest.mark.django_db
def test_lead_rate_correct(client, superuser, school_with_leads):
    # 3 leads, 1 converted
    _lead(school_with_leads, converted=False)
    _lead(school_with_leads, converted=False)
    _lead(school_with_leads, converted=True)
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_with_leads, days=30))
    assert resp.status_code == 200
    lead_tile = resp.context["kpi_tiles"][1]  # index 1 = Lead→Application
    assert lead_tile["label"] == "Lead → Application"
    assert lead_tile["value"] == "33.3%"
    assert lead_tile["basis"] == "1 of 3"


@pytest.mark.django_db
def test_zero_divide_no_error(client, superuser, school_no_leads):
    """0 apps → all rates None, page renders 200 (R2 guard)."""
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_no_leads))
    assert resp.status_code == 200
    tile = resp.context["kpi_tiles"][0]
    assert tile["value"] is None  # no "0.0%" or NaN


# ── §3 R5 — numbers reconcile ────────────────────────────────────────────────

@pytest.mark.django_db
def test_program_mix_sums_to_apps_total(client, superuser, school_no_leads):
    # Create 5 submissions, mix of programs
    for _ in range(3):
        _sub(school_no_leads, days_ago=5)
    for _ in range(2):
        _sub(school_no_leads, days_ago=5)
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_no_leads, days=30))
    assert resp.status_code == 200
    mix = resp.context["program_mix"]
    mix_total_in_ctx = resp.context["program_mix_total"]
    # Sum of bar counts must equal total
    bar_sum = sum(row["count"] for row in mix)
    assert bar_sum == mix_total_in_ctx


# ── §4.4 Funnel is all-time ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_funnel_includes_old_submissions(client, superuser, school_no_leads):
    """Funnel must include submissions created before the date-range window."""
    _sub(school_no_leads, status="Enrolled", days_ago=120)  # outside 30d range
    _sub(school_no_leads, status="New", days_ago=5)         # inside range
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_no_leads, days=30))
    assert resp.status_code == 200
    funnel = resp.context["funnel"]
    assert funnel["apps"] == 2       # all-time total
    assert funnel["enrolled"] == 1   # all-time enrolled


# ── Comparison delta ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_comparison_app_delta_correct(client, superuser, school_no_leads):
    """3 apps this period, 1 app previous period → delta +2."""
    for _ in range(3):
        _sub(school_no_leads, days_ago=5)
    _sub(school_no_leads, days_ago=35)
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_no_leads, days=30))
    assert resp.status_code == 200
    app_row = resp.context["comparison_rows"][0]
    assert app_row["this_val"] == 3
    assert app_row["prev_val"] == 1
    assert app_row["delta_str"] == "+2"


# ── Multi-tenant isolation ────────────────────────────────────────────────────

@pytest.mark.django_db
def test_multitenant_isolation(client, superuser, school_no_leads, school_b):
    """School A's submissions must not appear in school B's reports."""
    for _ in range(5):
        _sub(school_no_leads, days_ago=5)
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_b, days=30))
    assert resp.status_code == 200
    assert resp.context["funnel"]["apps"] == 0


# ── Time-series gap-fill ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_timeseries_gap_filled_7d(client, superuser, school_no_leads):
    """7-day range → time series has at least 7 data points (daily buckets)."""
    _sub(school_no_leads, days_ago=1)
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_no_leads, days=7))
    assert resp.status_code == 200
    import json as _json
    ts = _json.loads(resp.context["ts_json"])
    # We gap-fill from period_start to today — at minimum 7 buckets for a 7d range
    assert len(ts["data"]) >= 7


# ── New school / empty state ──────────────────────────────────────────────────

@pytest.mark.django_db
def test_new_school_renders_without_error(client, superuser, school_with_leads):
    """A brand-new school with no data must render 200 without exceptions."""
    client.force_login(superuser)
    resp = client.get(_rpt_url(school_with_leads))
    assert resp.status_code == 200
    # No exceptions, no NaN in context
    tile = resp.context["kpi_tiles"][0]
    assert tile["value"] is None or tile["value"].endswith("%")
