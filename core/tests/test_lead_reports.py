# core/tests/test_lead_reports.py
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Lead
from core.tests.factories import LeadFactory, SchoolAdminMembershipFactory, SchoolFactory, SubmissionFactory


def _login(client, school):
    membership = SchoolAdminMembershipFactory(school=school)
    client.force_login(membership.user)
    return membership.user


def _reports_url(school):
    return reverse("school_reports", kwargs={"school_slug": school.slug})


# ---------------------------------------------------------------------------
# lead_stats absent when leads disabled (trial plan)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_stats_absent_for_trial_school(client):
    school = SchoolFactory(plan="trial", slug="lr-trial")
    _login(client, school)
    resp = client.get(_reports_url(school))
    # Trial schools have reports disabled — expect 403 or feature_disabled render
    assert resp.status_code in (200, 403)
    if resp.status_code == 200:
        assert resp.context.get("lead_stats") is None


# ---------------------------------------------------------------------------
# lead_stats present for starter plan
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_stats_present_for_starter(client):
    school = SchoolFactory(plan="starter", slug="lr-starter")
    _login(client, school)
    LeadFactory(school=school, status="new")
    LeadFactory(school=school, status="contacted")
    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    stats = resp.context["lead_stats"]
    assert stats is not None
    assert stats["total"] == 2


@pytest.mark.django_db
def test_lead_total_in_period_respects_date_range(client):
    from datetime import timedelta
    from django.utils import timezone

    school = SchoolFactory(plan="starter", slug="lr-date-range")
    _login(client, school)

    # Old lead (outside 30-day window)
    old_lead = LeadFactory(school=school)
    Lead.objects.filter(pk=old_lead.pk).update(created_at=timezone.now() - timedelta(days=60))

    # Recent lead (inside window)
    LeadFactory(school=school)

    resp = client.get(_reports_url(school) + "?range=30")
    assert resp.status_code == 200
    stats = resp.context["lead_stats"]
    assert stats["total"] == 2            # all-time total includes old lead
    assert stats["total_in_period"] == 1  # only the recent one is in period


# ---------------------------------------------------------------------------
# Funnel
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_funnel_counts_each_status(client):
    school = SchoolFactory(plan="starter", slug="lr-funnel")
    _login(client, school)
    LeadFactory(school=school, status="new")
    LeadFactory(school=school, status="new")
    LeadFactory(school=school, status="contacted")
    LeadFactory(school=school, status="enrolled")

    resp = client.get(_reports_url(school))
    funnel = {row["status"]: row["count"] for row in resp.context["lead_stats"]["funnel"]}
    assert funnel["new"] == 2
    assert funnel["contacted"] == 1
    assert funnel["enrolled"] == 1
    assert funnel["trial_scheduled"] == 0
    assert funnel["lost"] == 0


@pytest.mark.django_db
def test_funnel_pct_sums_to_100(client):
    school = SchoolFactory(plan="starter", slug="lr-funnel-pct")
    _login(client, school)
    LeadFactory(school=school, status="new")
    LeadFactory(school=school, status="contacted")
    LeadFactory(school=school, status="lost")
    LeadFactory(school=school, status="lost")

    resp = client.get(_reports_url(school))
    funnel = resp.context["lead_stats"]["funnel"]
    total_pct = sum(row["pct"] for row in funnel)
    assert abs(total_pct - 100.0) < 0.2  # rounding tolerance


# ---------------------------------------------------------------------------
# Source breakdown
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_source_breakdown_counts(client):
    school = SchoolFactory(plan="starter", slug="lr-source")
    _login(client, school)
    LeadFactory(school=school, source="website")
    LeadFactory(school=school, source="website")
    LeadFactory(school=school, source="referral")

    resp = client.get(_reports_url(school))
    sources = {row["label"]: row["count"] for row in resp.context["lead_stats"]["sources"]}
    assert sources["Website"] == 2
    assert sources["Referral"] == 1


@pytest.mark.django_db
def test_source_conversion_hidden_for_starter(client):
    """Starter plan: converted/rate columns are None (not shown)."""
    school = SchoolFactory(plan="starter", slug="lr-source-starter")
    _login(client, school)
    LeadFactory(school=school, source="website")

    resp = client.get(_reports_url(school))
    stats = resp.context["lead_stats"]
    assert stats["conversion_enabled"] is False
    for row in stats["sources"]:
        assert row["converted"] is None
        assert row["rate"] is None


@pytest.mark.django_db
def test_source_conversion_shown_for_pro(client):
    """Pro plan: converted count and rate included per source."""
    school = SchoolFactory(plan="pro", slug="lr-source-pro")
    _login(client, school)
    sub = SubmissionFactory(school=school)
    converted = LeadFactory(school=school, source="referral", converted_submission=sub)
    LeadFactory(school=school, source="referral")  # unconverted

    resp = client.get(_reports_url(school))
    stats = resp.context["lead_stats"]
    assert stats["conversion_enabled"] is True
    referral = next(r for r in stats["sources"] if r["label"] == "Referral")
    assert referral["count"] == 2
    assert referral["converted"] == 1
    assert referral["rate"] == 50.0


# ---------------------------------------------------------------------------
# Overall conversion rate
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_overall_conversion_rate_calculated(client):
    school = SchoolFactory(plan="pro", slug="lr-overall-rate")
    _login(client, school)
    sub = SubmissionFactory(school=school)
    LeadFactory(school=school, converted_submission=sub)  # converted
    LeadFactory(school=school)  # unconverted
    LeadFactory(school=school)  # unconverted

    resp = client.get(_reports_url(school))
    stats = resp.context["lead_stats"]
    assert stats["total_converted"] == 1
    assert stats["overall_rate"] == round(1 / 3 * 100, 1)


@pytest.mark.django_db
def test_overall_rate_none_for_starter(client):
    """Starter: overall_rate is None (conversion not shown)."""
    school = SchoolFactory(plan="starter", slug="lr-rate-starter")
    _login(client, school)
    LeadFactory(school=school)

    resp = client.get(_reports_url(school))
    stats = resp.context["lead_stats"]
    assert stats["overall_rate"] is None
    assert stats["total_converted"] is None


@pytest.mark.django_db
def test_no_leads_shows_zero_not_error(client):
    """School with no leads renders without error, total=0."""
    school = SchoolFactory(plan="starter", slug="lr-zero-leads")
    _login(client, school)

    resp = client.get(_reports_url(school))
    assert resp.status_code == 200
    stats = resp.context["lead_stats"]
    assert stats["total"] == 0
    assert stats["total_in_period"] == 0
    assert all(row["count"] == 0 for row in stats["funnel"])
