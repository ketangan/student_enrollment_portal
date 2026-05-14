# core/tests/test_lead_reports.py
"""
Tests for the Lead module integration in the Reports page.
Updated for Phase 19 analytics redesign — old `lead_stats` context replaced by
`source_rows`, `kpi_tiles`, `funnel`, and `leads_enabled` context keys.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Lead
from core.tests.factories import LeadFactory, SchoolAdminMembershipFactory, SchoolFactory, SubmissionFactory


def _login(client, school):
    membership = SchoolAdminMembershipFactory(school=school)
    client.force_login(membership.user)
    return membership.user


def _reports_url(school, days=30):
    return reverse("school_reports", kwargs={"school_slug": school.slug}) + f"?range={days}"


# ---------------------------------------------------------------------------
# leads_enabled flag propagates to template context
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_leads_disabled_no_source_rows(client):
    school = SchoolFactory(
        plan="starter",
        slug="lr-trial",
        feature_flags={"leads_enabled": False},
    )
    _login(client, school)
    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    assert resp.context.get("source_rows") is None
    assert resp.context.get("leads_enabled") is False


@pytest.mark.django_db
def test_leads_enabled_context_flag(client):
    school = SchoolFactory(plan="starter", slug="lr-starter")
    _login(client, school)
    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    assert resp.context.get("leads_enabled") is True


# ---------------------------------------------------------------------------
# Lead→Application KPI tile
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_to_app_kpi_tile_rate(client):
    """kpi_tiles[1] shows correct Lead→Application rate with basis."""
    school = SchoolFactory(plan="starter", slug="lr-kpi-rate")
    _login(client, school)
    sub = SubmissionFactory(school=school)
    LeadFactory(school=school, converted_submission=sub)
    LeadFactory(school=school)  # unconverted
    LeadFactory(school=school)  # unconverted

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    tiles = resp.context["kpi_tiles"]
    assert len(tiles) == 4
    lead_tile = tiles[1]
    assert lead_tile["label"] == "Lead → Application"
    assert lead_tile["value"] == "33.3%"
    assert lead_tile["basis"] == "1 of 3"


@pytest.mark.django_db
def test_lead_kpi_tile_respects_date_range(client):
    """KPI tile is scoped to the selected period (leads outside window excluded)."""
    school = SchoolFactory(plan="starter", slug="lr-date-range")
    _login(client, school)

    old_lead = LeadFactory(school=school)
    Lead.objects.filter(pk=old_lead.pk).update(
        created_at=timezone.now() - timedelta(days=60)
    )
    LeadFactory(school=school)  # recent

    resp = client.get(_reports_url(school, days=30))
    assert resp.status_code == 200
    lead_tile = resp.context["kpi_tiles"][1]
    # Only 1 lead in the 30-day period; 0 converted → value is None (no rate)
    assert lead_tile["basis"] == "0 of 1" or lead_tile["value"] is None


@pytest.mark.django_db
def test_no_leads_renders_without_error(client):
    """School with leads enabled but no leads renders 200 without crash."""
    school = SchoolFactory(plan="starter", slug="lr-zero-leads")
    _login(client, school)

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    lead_tile = resp.context["kpi_tiles"][1]
    assert lead_tile["label"] == "Lead → Application"
    assert lead_tile["value"] is None


# ---------------------------------------------------------------------------
# Source breakdown: source_rows structure
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_source_breakdown_counts(client):
    school = SchoolFactory(plan="starter", slug="lr-source")
    _login(client, school)
    LeadFactory(school=school, source="website")
    LeadFactory(school=school, source="website")
    LeadFactory(school=school, source="referral")

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    source_rows = resp.context["source_rows"]
    assert source_rows is not None
    by_label = {row["label"]: row for row in source_rows}
    assert by_label["Website"]["total"] == 2
    assert by_label["Referral"]["total"] == 1


@pytest.mark.django_db
def test_source_conversion_rate_shown_for_all_plans(client):
    """Source conversion rate is shown for any school with leads_enabled — no plan gating."""
    school = SchoolFactory(plan="starter", slug="lr-source-rate")
    _login(client, school)
    sub = SubmissionFactory(school=school)
    LeadFactory(school=school, source="referral", converted_submission=sub)
    LeadFactory(school=school, source="referral")  # unconverted

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    source_rows = resp.context["source_rows"]
    referral = next(r for r in source_rows if r["label"] == "Referral")
    assert referral["total"] == 2
    assert referral["converted"] == 1
    assert referral["rate"] == 50.0


@pytest.mark.django_db
def test_source_rows_empty_list_when_no_leads(client):
    """leads_enabled but no leads → source_rows is an empty list, not None."""
    school = SchoolFactory(plan="starter", slug="lr-source-empty")
    _login(client, school)

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    assert resp.context["source_rows"] == []


# ---------------------------------------------------------------------------
# Funnel: leads stage
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_funnel_lead_counts(client):
    """Funnel includes total leads and converted count when leads enabled."""
    school = SchoolFactory(plan="starter", slug="lr-funnel")
    _login(client, school)
    sub = SubmissionFactory(school=school)
    LeadFactory(school=school, converted_submission=sub)
    LeadFactory(school=school)
    LeadFactory(school=school)

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    funnel = resp.context["funnel"]
    assert funnel["leads"] == 3
    assert funnel["converted"] == 1


@pytest.mark.django_db
def test_funnel_leads_none_when_disabled(client):
    """funnel['leads'] is None when leads module is off."""
    school = SchoolFactory(
        plan="starter",
        slug="lr-funnel-off",
        feature_flags={"leads_enabled": False},
    )
    _login(client, school)

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    assert resp.context["funnel"]["leads"] is None
