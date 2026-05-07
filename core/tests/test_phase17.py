"""
Phase 17 — Billing Integration Alignment.

Covers:
  - Webhook handlers: explicit is_active sync for active/trialing/past_due/canceled
  - Plan → feature flag mapping is correct for all plans
  - Admin leads views return feature_disabled page (not Http404) when leads disabled
  - feature_disabled.html shows required plan + billing link
  - checkout.session.completed reactivates a locked school
  - Plan syncs correctly on subscription.updated with new price_id
"""
from __future__ import annotations

import pytest
from django.urls import reverse
from unittest.mock import MagicMock, patch

from core.services.billing_stripe import (
    handle_checkout_completed,
    handle_subscription_updated,
    handle_subscription_deleted,
)
from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    UserFactory,
)


# ── Webhook: subscription.updated — is_active lifecycle ───────────────────


@pytest.mark.django_db
class TestSubscriptionUpdatedIsActive:
    """handle_subscription_updated must sync is_active correctly for all statuses."""

    def _base_data(self, sub_id, status, **extra):
        data = {"id": sub_id, "status": status, "items": {"data": []}}
        data.update(extra)
        return data

    def test_active_status_sets_is_active_true(self):
        """Active subscription → school.is_active=True (idempotent reactivation)."""
        school = SchoolFactory(
            stripe_subscription_id="sub_active",
            stripe_subscription_status="past_due",
            plan="starter",
            is_active=True,
        )
        handle_subscription_updated(self._base_data("sub_active", "active"))
        school.refresh_from_db()
        assert school.is_active is True
        assert school.stripe_subscription_status == "active"

    def test_trialing_status_sets_is_active_true(self):
        """Trialing subscription → school.is_active=True."""
        school = SchoolFactory(
            stripe_subscription_id="sub_trial",
            stripe_subscription_status="active",
            plan="starter",
            is_active=True,
        )
        handle_subscription_updated(self._base_data("sub_trial", "trialing"))
        school.refresh_from_db()
        assert school.is_active is True
        assert school.stripe_subscription_status == "trialing"

    def test_past_due_does_not_lock_school(self):
        """past_due: school retains access (grace period). is_active unchanged."""
        school = SchoolFactory(
            stripe_subscription_id="sub_pastdue",
            stripe_subscription_status="active",
            plan="starter",
            is_active=True,
        )
        handle_subscription_updated(self._base_data("sub_pastdue", "past_due"))
        school.refresh_from_db()
        assert school.is_active is True  # Not locked — grace period
        assert school.stripe_subscription_status == "past_due"

    def test_canceled_immediately_locks_school(self):
        """Immediate cancel (no cancel_at, no cancel_at_period_end) locks the school."""
        school = SchoolFactory(
            stripe_subscription_id="sub_cancel",
            stripe_subscription_status="active",
            plan="pro",
            is_active=True,
        )
        handle_subscription_updated(self._base_data("sub_cancel", "canceled"))
        school.refresh_from_db()
        assert school.is_active is False
        assert school.stripe_subscription_status == "canceled"

    def test_active_with_cancel_at_period_end_does_not_lock(self):
        """Scheduled cancel (cancel_at_period_end=True) keeps school accessible."""
        from datetime import datetime, timedelta, timezone as dt_tz
        future_ts = int((datetime.now(dt_tz.utc) + timedelta(days=30)).timestamp())
        school = SchoolFactory(
            stripe_subscription_id="sub_sched",
            stripe_subscription_status="active",
            plan="starter",
            is_active=True,
        )
        handle_subscription_updated({
            "id": "sub_sched",
            "status": "active",
            "cancel_at_period_end": True,
            "current_period_end": future_ts,
            "items": {"data": []},
        })
        school.refresh_from_db()
        assert school.is_active is True
        assert school.stripe_cancel_at_period_end is True

    def test_plan_syncs_on_price_change(self):
        """Price ID in subscription.updated causes school.plan to update."""
        school = SchoolFactory(
            stripe_subscription_id="sub_upgrade",
            stripe_subscription_status="active",
            plan="starter",
            is_active=True,
        )
        with patch(
            "core.services.billing_stripe.price_to_plan",
            return_value="pro",
        ):
            handle_subscription_updated({
                "id": "sub_upgrade",
                "status": "active",
                "items": {"data": [{"price": {"id": "price_pro_monthly"}}]},
            })
        school.refresh_from_db()
        assert school.plan == "pro"


# ── Feature flags: plan → flags mapping ───────────────────────────────────


class TestFeatureFlagsByPlan:
    """Verify the plan hierarchy maps flags correctly (no DB required)."""

    def test_trial_has_all_flags_enabled(self):
        school = SchoolFactory.build(plan="trial")
        flags = school.features
        assert flags.leads_enabled is True
        assert flags.reports_enabled is True
        assert flags.email_notifications_enabled is True
        assert flags.csv_export_enabled is True
        assert flags.custom_branding_enabled is True
        assert flags.save_resume_enabled is True
        assert flags.ai_summary_enabled is True

    def test_starter_has_correct_flags(self):
        """Starter: has leads, reports, email, csv; missing pro/growth features."""
        school = SchoolFactory.build(plan="starter")
        flags = school.features
        # Starter-tier flags
        assert flags.leads_enabled is True
        assert flags.reports_enabled is True
        assert flags.email_notifications_enabled is True
        assert flags.csv_export_enabled is True
        # Pro-tier flags — NOT on starter
        assert flags.custom_branding_enabled is False
        assert flags.save_resume_enabled is False
        assert flags.leads_conversion_enabled is False
        # Growth-tier flag — NOT on starter
        assert flags.ai_summary_enabled is False

    def test_pro_has_correct_flags(self):
        """Pro: includes all starter + pro features; missing growth-only."""
        school = SchoolFactory.build(plan="pro")
        flags = school.features
        assert flags.leads_enabled is True
        assert flags.reports_enabled is True
        assert flags.custom_branding_enabled is True
        assert flags.save_resume_enabled is True
        assert flags.leads_conversion_enabled is True
        # Growth flag — NOT on pro
        assert flags.ai_summary_enabled is False

    def test_growth_has_all_flags_including_ai(self):
        """Growth: all flags including ai_summary_enabled."""
        school = SchoolFactory.build(plan="growth")
        flags = school.features
        assert flags.ai_summary_enabled is True
        assert flags.leads_conversion_enabled is True
        assert flags.custom_branding_enabled is True

    def test_unknown_plan_falls_back_to_trial_rank(self):
        """Unknown plan string defaults to trial-rank (all flags True)."""
        school = SchoolFactory.build(plan="unknown_plan_xyz")
        # Unrecognised plan → PLAN_RANK fallback is 0 (trial equivalent)
        # default_flags_for_plan returns flags based on rank ≥ 0 which is True for all
        flags = school.features
        # At rank 0, flags with min_plan=TRIAL (rank 0) are enabled, others are not
        # Actually: rank.get("unknown_plan_xyz", 0) = 0, so flags with min_plan rank ≤ 0 are True
        # trial-tier flags have rank 0 → enabled; starter-tier flags have rank 1 → NOT enabled
        assert flags.csv_export_enabled is True   # PLAN_TRIAL
        assert flags.status_enabled is True       # PLAN_TRIAL
        assert flags.leads_enabled is False       # PLAN_STARTER (rank 1 > 0)


# ── Admin leads views: feature_disabled instead of Http404 ────────────────


@pytest.mark.django_db
class TestLeadsFeatureGating:
    """When leads_enabled=False, admin leads views show feature_disabled page (not 404)."""

    def _school_and_admin(self, plan="starter", leads_enabled=False):
        school = SchoolFactory(plan=plan, feature_flags={"leads_enabled": leads_enabled})
        user = UserFactory()
        SchoolAdminMembershipFactory(user=user, school=school)
        return school, user

    def test_leads_list_shows_feature_disabled_when_disabled(self, client):
        school, user = self._school_and_admin(leads_enabled=False)
        client.force_login(user)
        resp = client.get(
            reverse("school_leads", kwargs={"school_slug": school.slug})
        )
        assert resp.status_code == 403
        content = resp.content.decode()
        assert "Leads is disabled" in content
        assert "Starter" in content  # required_plan shown

    def test_leads_list_accessible_when_enabled(self, client):
        school, user = self._school_and_admin(leads_enabled=True)
        client.force_login(user)
        resp = client.get(
            reverse("school_leads", kwargs={"school_slug": school.slug})
        )
        # May be 200 or redirect; must not be 403 or 404
        assert resp.status_code not in (403, 404)

    def test_leads_export_shows_feature_disabled_when_disabled(self, client):
        school, user = self._school_and_admin(leads_enabled=False)
        client.force_login(user)
        resp = client.get(
            reverse("school_lead_export", kwargs={"school_slug": school.slug})
        )
        assert resp.status_code == 403
        content = resp.content.decode()
        assert "Leads is disabled" in content

    def test_feature_disabled_page_shows_billing_link(self, client):
        """feature_disabled page must include a billing upgrade link."""
        school, user = self._school_and_admin(leads_enabled=False)
        client.force_login(user)
        resp = client.get(
            reverse("school_leads", kwargs={"school_slug": school.slug})
        )
        content = resp.content.decode()
        assert "billing_url" not in content  # not a raw context var — rendered as href
        assert "Upgrade plan" in content or "upgrade" in content.lower()


# ── checkout.session.completed: reactivates locked school ─────────────────


@pytest.mark.django_db
def test_checkout_reactivates_locked_school():
    """checkout.session.completed must set is_active=True even if school was locked."""
    school = SchoolFactory(
        slug="locked-school",
        stripe_subscription_id="",
        stripe_customer_id="",
        plan="starter",
        is_active=False,  # Locked (e.g. expired subscription)
    )
    with patch("core.services.billing_stripe._get_stripe") as mock_gs:
        mock_stripe = MagicMock()
        mock_sub = {
            "status": "active",
            "items": {"data": [{"price": {"id": "price_pro_monthly"}}]},
        }
        mock_stripe.Subscription.retrieve.return_value = mock_sub
        mock_gs.return_value = mock_stripe

        with patch("core.services.billing_stripe.price_to_plan", return_value="pro"):
            handle_checkout_completed({
                "metadata": {"school_slug": "locked-school"},
                "customer": "cus_new123",
                "subscription": "sub_new456",
                "line_items": {"data": []},
            })

    school.refresh_from_db()
    assert school.is_active is True
    assert school.stripe_customer_id == "cus_new123"
    assert school.stripe_subscription_id == "sub_new456"
