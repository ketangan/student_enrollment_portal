from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from core.models import AdminAuditLog, OnboardingChecklistItem, School
from core.services.config_loader import SchoolConfig
from core.services.go_live import checklist_steps
from core.services.onboarding import (
    checklist_context, get_or_create_checklist, mark_checklist_item,
    validate_checklist_update,
)
from scheduling.models import SchedulingProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def launch(monkeypatch):
    school = School.objects.create(slug="launch-school", plan="starter")
    actor = User.objects.create_superuser("launch-operator", "operator@example.invalid", "test-password")
    config = SchoolConfig({"school": {"slug": school.slug}, "form": {"sections": []}})
    monkeypatch.setattr("core.services.go_live.load_school_config", lambda slug: config)
    return SimpleNamespace(school=school, actor=actor, config=config)


def steps(school):
    return {row[0]: row for _, group in checklist_steps(school) for row in group}


def test_definitions_cover_each_persisted_key_once(launch):
    definitions = [row[0] for _, group in checklist_steps(launch.school) for row in group]
    assert len(definitions) == len(set(definitions))
    assert set(definitions) == set(OnboardingChecklistItem.ITEM_LABELS)


def test_enrollment_only_applicability(launch):
    defined = steps(launch.school)
    assert not defined["workflows_configured"][2]
    assert not defined["lead_capture_configured"][2]
    assert not defined["reports_verified"][2]
    assert defined["trial_configured"][2]
    assert defined["scheduling_verified"][2]
    assert defined["live_payment_verified"][2]
    launch.config.raw["application_fee"] = {"enabled": True}
    assert not steps(launch.school)["live_payment_verified"][2]
    assert "--include-live-recovery" not in steps(launch.school)["recovery_verified"][1]


def test_booking_only_has_no_enrollment_requirements(launch):
    launch.config.raw.pop("form")
    SchedulingProfile.objects.create(school=launch.school, enabled=True,
        capabilities=["workshops", "reservations"], payment_provider="square")
    defined = steps(launch.school)
    for key in ("workflows_configured", "lead_capture_configured", "test_submission_completed", "reports_verified"):
        assert defined[key][2]
    assert not defined["scheduling_verified"][2]
    assert not defined["live_payment_verified"][2]
    assert "--include-live-recovery" in defined["recovery_verified"][1]
    assert "Never enable it in demo" in defined["recovery_verified"][1]


def test_combined_school_keeps_both_journeys(launch):
    SchedulingProfile.objects.create(school=launch.school, enabled=True, capabilities=["lessons"])
    defined = steps(launch.school)
    assert not defined["scheduling_verified"][2]
    assert not defined["workflows_configured"][2]
    assert defined["live_payment_verified"][2]


def test_missing_config_does_not_hide_enrollment_review(launch, monkeypatch):
    monkeypatch.setattr("core.services.go_live.load_school_config", lambda slug: None)
    SchedulingProfile.objects.create(school=launch.school, enabled=True, capabilities=["lessons"])
    assert not steps(launch.school)["workflows_configured"][2]


def test_feature_flags_and_trial_terms_control_optional_steps(launch):
    launch.school.feature_flags = {"leads_enabled": False, "reports_enabled": False}
    launch.school.trial_requires_activation = True
    defined = steps(launch.school)
    assert defined["lead_capture_configured"][2]
    assert defined["reports_verified"][2]
    assert not defined["trial_configured"][2]


def test_preserves_existing_history_without_counting_excluded_steps(launch):
    mark_checklist_item(launch.school, "trial_configured", launch.actor)
    mark_checklist_item(launch.school, "branding_configured", launch.actor)
    before = OnboardingChecklistItem.objects.get(school=launch.school, item="trial_configured").completed_at
    context = checklist_context(launch.school)
    assert context["checklist_done"] == 1
    assert context["checklist_total"] + len(context["checklist_excluded"]) == len(OnboardingChecklistItem.ITEMS)
    assert OnboardingChecklistItem.objects.get(school=launch.school, item="trial_configured").completed_at == before
    assert not OnboardingChecklistItem.objects.get(school=launch.school, item="release_verified").completed_at


def test_query_count_does_not_grow_per_checklist_row(launch, django_assert_num_queries):
    get_or_create_checklist(launch.school)
    with django_assert_num_queries(3):
        checklist_context(launch.school)


@pytest.mark.parametrize("key", ["production_workspace_verified", "live_payment_verified", "release_verified", "customer_approved", "school_marked_live"])
def test_demo_cannot_certify_production(client, launch, key):
    launch.school.is_demo = True
    launch.school.save()
    launch.config.raw["application_fee"] = {"enabled": True}
    client.force_login(launch.actor)
    response = client.post(f"/ops/schools/{launch.school.slug}/checklist/{key}/toggle/")
    assert response.status_code == 302
    assert not OnboardingChecklistItem.objects.filter(school=launch.school, item=key, completed_at__isnull=False).exists()
    assert not AdminAuditLog.objects.filter(extra__name="onboarding_checklist_item_completed").exists()


def test_excluded_step_cannot_be_toggled(client, launch):
    client.force_login(launch.actor)
    response = client.post(f"/ops/schools/{launch.school.slug}/checklist/scheduling_verified/toggle/")
    assert response.status_code == 302
    assert not OnboardingChecklistItem.objects.filter(school=launch.school, item="scheduling_verified").exists()
    with pytest.raises(ValidationError, match="Unknown"):
        validate_checklist_update(launch.school, "not-a-step")


def test_toggle_audited_and_does_not_activate_or_bill(client, launch):
    launch.school.is_active = False
    launch.school.save()
    client.force_login(launch.actor)
    url = f"/ops/schools/{launch.school.slug}/checklist/school_marked_live/toggle/"
    response = client.post(url)
    assert response["Location"].endswith("#go-live-checklist")
    row = OnboardingChecklistItem.objects.get(school=launch.school, item="school_marked_live")
    assert row.completed_by == launch.actor
    launch.school.refresh_from_db()
    assert not launch.school.is_active
    assert launch.school.plan == "starter"
    assert launch.school.trial_started_at is None
    assert AdminAuditLog.objects.filter(extra__name="onboarding_checklist_item_completed", actor=launch.actor).exists()
    client.post(url)
    row.refresh_from_db()
    assert row.completed_at is None
    assert AdminAuditLog.objects.filter(extra__name="onboarding_checklist_item_reopened").exists()


def test_only_superadmin_can_change_checklist(client, launch):
    user = User.objects.create_user("school-staff", is_staff=True)
    client.force_login(user)
    response = client.post(f"/ops/schools/{launch.school.slug}/checklist/school_created/toggle/")
    assert response.status_code in (302, 403)
    assert not OnboardingChecklistItem.objects.filter(school=launch.school).exists()


def test_demo_checklist_visible_with_production_controls_disabled(client, launch):
    launch.school.is_demo = True
    launch.school.save()
    mark_checklist_item(launch.school, "school_marked_live", launch.actor)
    client.force_login(launch.actor)
    response = client.get(f"/ops/schools/{launch.school.slug}/")
    assert response.status_code == 200
    assert b"Go-live checklist" in response.content
    assert b"Demo preparation only" in response.content
    assert b"Not required for this setup" in response.content
    assert response.context["checklist_done"] == 0
    assert b"This checkbox does not activate" in response.content
    assert b"assets/lucide.js" in response.content
    assert b"window.lucide?.createIcons()" in response.content


def test_checklist_and_completion_are_school_scoped(client, launch):
    other = School.objects.create(slug="another-school")
    mark_checklist_item(other, "school_created", launch.actor)
    client.force_login(launch.actor)
    client.post(f"/ops/schools/{launch.school.slug}/checklist/school_created/toggle/")
    assert OnboardingChecklistItem.objects.filter(school=other, completed_at__isnull=False).count() == 1
    assert checklist_context(launch.school)["checklist_done"] == 1
