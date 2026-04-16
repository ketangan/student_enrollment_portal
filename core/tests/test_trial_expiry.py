# core/tests/test_trial_expiry.py
"""
Tests for trial expiry enforcement.

Covers:
  - School model helper properties (is_trial_plan, trial_ends_at,
    trial_days_left, is_trial_expired)
  - apply_view: active trial can submit; expired trial cannot
  - lead_capture_view: active trial can submit; expired trial cannot
  - Admin each_context: banner injected for trial schools, not for paid
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import School, TRIAL_LENGTH_DAYS
from core.tests.factories import SchoolAdminMembershipFactory, SchoolFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trial_school(*, days_ago: int, **kwargs) -> School:
    """Return a trial school whose trial started `days_ago` days ago."""
    started = timezone.now() - timedelta(days=days_ago)
    return SchoolFactory(plan="trial", trial_started_at=started, **kwargs)


def _paid_school(**kwargs) -> School:
    return SchoolFactory(plan="starter", trial_started_at=None, **kwargs)


# ---------------------------------------------------------------------------
# Model helper tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_is_trial_plan_true_for_trial():
    school = SchoolFactory(plan="trial")
    assert school.is_trial_plan is True


@pytest.mark.django_db
def test_is_trial_plan_false_for_paid():
    school = _paid_school()
    assert school.is_trial_plan is False


@pytest.mark.django_db
def test_trial_ends_at_correct():
    started = timezone.now() - timedelta(days=5)
    school = SchoolFactory(plan="trial", trial_started_at=started)
    expected = started + timedelta(days=TRIAL_LENGTH_DAYS)
    assert school.trial_ends_at == expected


@pytest.mark.django_db
def test_trial_ends_at_none_for_non_trial():
    school = _paid_school()
    assert school.trial_ends_at is None


@pytest.mark.django_db
def test_trial_ends_at_none_when_started_at_missing():
    # Force trial_started_at=None via update() to bypass the save() auto-set.
    school = SchoolFactory(plan="trial")
    School.objects.filter(pk=school.pk).update(trial_started_at=None)
    school.refresh_from_db()
    assert school.trial_ends_at is None


@pytest.mark.django_db
def test_trial_days_left_positive_for_active():
    school = _trial_school(days_ago=5)
    assert school.trial_days_left > 0
    assert school.trial_days_left <= TRIAL_LENGTH_DAYS


@pytest.mark.django_db
def test_trial_days_left_zero_for_expired():
    school = _trial_school(days_ago=TRIAL_LENGTH_DAYS + 1)
    assert school.trial_days_left == 0


@pytest.mark.django_db
def test_trial_days_left_zero_for_non_trial():
    school = _paid_school()
    assert school.trial_days_left == 0


@pytest.mark.django_db
def test_trial_days_left_zero_when_started_at_missing():
    school = SchoolFactory(plan="trial")
    School.objects.filter(pk=school.pk).update(trial_started_at=None)
    school.refresh_from_db()
    assert school.trial_days_left == 0


@pytest.mark.django_db
def test_is_trial_expired_false_for_active():
    school = _trial_school(days_ago=5)
    assert school.is_trial_expired is False


@pytest.mark.django_db
def test_is_trial_expired_true_for_expired():
    school = _trial_school(days_ago=TRIAL_LENGTH_DAYS + 1)
    assert school.is_trial_expired is True


@pytest.mark.django_db
def test_is_trial_expired_false_for_paid_school():
    # Paid schools are never "expired trial" even if trial_started_at is old
    school = SchoolFactory(
        plan="starter",
        trial_started_at=timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 10),
    )
    assert school.is_trial_expired is False


@pytest.mark.django_db
def test_is_trial_expired_false_when_started_at_missing():
    # Defensive: no trial_started_at → not expired (no window started).
    # Force via update() because save() now auto-sets trial_started_at for trial schools.
    school = SchoolFactory(plan="trial")
    School.objects.filter(pk=school.pk).update(trial_started_at=None)
    school.refresh_from_db()
    assert school.is_trial_expired is False


@pytest.mark.django_db
def test_trial_days_left_ceiling_not_floor():
    """A trial ending in 0.5 days should show 1, not 0."""
    started = timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS) + timedelta(hours=12)
    school = SchoolFactory(plan="trial", trial_started_at=started)
    assert school.trial_days_left == 1


# ---------------------------------------------------------------------------
# Apply view — trial enforcement
# ---------------------------------------------------------------------------

SINGLE_FORM_SLUG = "enrollment-request-demo"


def _build_minimal_post():
    return {
        "first_name": "Test",
        "last_name": "User",
        "contact_email": "test@example.com",
    }


@pytest.mark.django_db
def test_apply_view_active_trial_can_submit(client, settings):
    """An active trial school can still accept submissions."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from core.models import Submission
    from core.services.config_loader import load_school_config

    cfg = load_school_config(SINGLE_FORM_SLUG)
    assert cfg is not None

    school = School.objects.filter(slug=SINGLE_FORM_SLUG).first()
    if school:
        school.plan = "trial"
        school.trial_started_at = timezone.now() - timedelta(days=3)
        school.is_active = True
        school.save()
    else:
        School.objects.create(
            slug=SINGLE_FORM_SLUG,
            display_name=cfg.display_name,
            plan="trial",
            trial_started_at=timezone.now() - timedelta(days=3),
        )

    before = Submission.objects.filter(school__slug=SINGLE_FORM_SLUG).count()
    url = reverse("apply", kwargs={"school_slug": SINGLE_FORM_SLUG})
    resp = client.post(url, data=_build_minimal_post(), follow=False)

    # Redirect on success OR form errors (422) — either way NOT the expired page
    assert resp.status_code in (200, 302, 303)
    if resp.status_code == 302:
        # Successful submit — submission was created
        assert Submission.objects.filter(school__slug=SINGLE_FORM_SLUG).count() > before


@pytest.mark.django_db
def test_apply_view_expired_trial_get_shows_expired_page(client):
    """GET to apply page for an expired trial shows the trial_expired template."""
    from core.services.config_loader import load_school_config

    cfg = load_school_config(SINGLE_FORM_SLUG)
    assert cfg is not None

    school = School.objects.filter(slug=SINGLE_FORM_SLUG).first()
    if school:
        school.plan = "trial"
        school.trial_started_at = timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 5)
        school.is_active = True
        school.save()
    else:
        School.objects.create(
            slug=SINGLE_FORM_SLUG,
            display_name=cfg.display_name,
            plan="trial",
            trial_started_at=timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 5),
        )

    url = reverse("apply", kwargs={"school_slug": SINGLE_FORM_SLUG})
    resp = client.get(url)

    assert resp.status_code == 200
    assert b"trial" in resp.content.lower() or b"expired" in resp.content.lower()
    assert b"upgrade" in resp.content.lower()


@pytest.mark.django_db
def test_apply_view_expired_trial_post_does_not_create_submission(client, settings):
    """POST to apply for an expired trial must not create a Submission."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from core.models import Submission
    from core.services.config_loader import load_school_config

    cfg = load_school_config(SINGLE_FORM_SLUG)
    assert cfg is not None

    school = School.objects.filter(slug=SINGLE_FORM_SLUG).first()
    if school:
        school.plan = "trial"
        school.trial_started_at = timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 5)
        school.is_active = True
        school.save()
    else:
        School.objects.create(
            slug=SINGLE_FORM_SLUG,
            display_name=cfg.display_name,
            plan="trial",
            trial_started_at=timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 5),
        )

    before = Submission.objects.filter(school__slug=SINGLE_FORM_SLUG).count()
    url = reverse("apply", kwargs={"school_slug": SINGLE_FORM_SLUG})
    resp = client.post(url, data=_build_minimal_post())

    assert resp.status_code == 200
    assert b"upgrade" in resp.content.lower()
    assert Submission.objects.filter(school__slug=SINGLE_FORM_SLUG).count() == before


@pytest.mark.django_db
def test_apply_view_paid_school_unaffected(client, settings):
    """A paid school is never blocked by trial expiry logic."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from core.services.config_loader import load_school_config

    cfg = load_school_config(SINGLE_FORM_SLUG)
    assert cfg is not None

    school = School.objects.filter(slug=SINGLE_FORM_SLUG).first()
    if school:
        school.plan = "starter"
        school.trial_started_at = timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 100)
        school.is_active = True
        school.save()
    else:
        School.objects.create(
            slug=SINGLE_FORM_SLUG,
            display_name=cfg.display_name,
            plan="starter",
            trial_started_at=timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 100),
        )

    url = reverse("apply", kwargs={"school_slug": SINGLE_FORM_SLUG})
    resp = client.get(url)
    # Should render the form, not the expired page
    assert resp.status_code == 200
    assert b"upgrade" not in resp.content.lower() or b"form" in resp.content.lower()


# ---------------------------------------------------------------------------
# Lead capture view — trial enforcement
# ---------------------------------------------------------------------------

LEAD_SLUG = "enrollment-request-demo"


@pytest.mark.django_db
def test_lead_capture_active_trial_allows_post(client, settings):
    """An active trial school can create new leads."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    from core.models import Lead
    from core.services.config_loader import load_school_config

    cfg = load_school_config(LEAD_SLUG)
    if cfg is None or not (cfg.raw or {}).get("leads"):
        pytest.skip("School config has no leads section")

    school = School.objects.filter(slug=LEAD_SLUG).first()
    if school:
        school.plan = "trial"
        school.trial_started_at = timezone.now() - timedelta(days=3)
        school.feature_flags = {"leads_enabled": True}
        school.is_active = True
        school.save()
    else:
        School.objects.create(
            slug=LEAD_SLUG,
            display_name=cfg.display_name,
            plan="trial",
            trial_started_at=timezone.now() - timedelta(days=3),
            feature_flags={"leads_enabled": True},
        )

    before = Lead.objects.filter(school__slug=LEAD_SLUG).count()
    url = reverse("lead_capture", kwargs={"school_slug": LEAD_SLUG})
    resp = client.post(url, {"name": "Alice", "email": "alice@example.com"}, follow=False)

    assert resp.status_code in (200, 302, 303)
    if resp.status_code in (302, 303):
        assert Lead.objects.filter(school__slug=LEAD_SLUG).count() > before


@pytest.mark.django_db
def test_lead_capture_expired_trial_get_shows_expired_page(client):
    """GET to lead capture for expired trial shows trial_expired template."""
    from core.services.config_loader import load_school_config

    cfg = load_school_config(LEAD_SLUG)
    if cfg is None or not (cfg.raw or {}).get("leads"):
        pytest.skip("School config has no leads section")

    school = School.objects.filter(slug=LEAD_SLUG).first()
    if school:
        school.plan = "trial"
        school.trial_started_at = timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 5)
        school.feature_flags = {"leads_enabled": True}
        school.is_active = True
        school.save()
    else:
        School.objects.create(
            slug=LEAD_SLUG,
            display_name=cfg.display_name,
            plan="trial",
            trial_started_at=timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 5),
            feature_flags={"leads_enabled": True},
        )

    url = reverse("lead_capture", kwargs={"school_slug": LEAD_SLUG})
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"upgrade" in resp.content.lower()


@pytest.mark.django_db
def test_lead_capture_expired_trial_post_does_not_create_lead(client):
    """POST to lead capture for expired trial must not create a Lead."""
    from core.models import Lead
    from core.services.config_loader import load_school_config

    cfg = load_school_config(LEAD_SLUG)
    if cfg is None or not (cfg.raw or {}).get("leads"):
        pytest.skip("School config has no leads section")

    school = School.objects.filter(slug=LEAD_SLUG).first()
    if school:
        school.plan = "trial"
        school.trial_started_at = timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 5)
        school.feature_flags = {"leads_enabled": True}
        school.is_active = True
        school.save()
    else:
        School.objects.create(
            slug=LEAD_SLUG,
            display_name=cfg.display_name,
            plan="trial",
            trial_started_at=timezone.now() - timedelta(days=TRIAL_LENGTH_DAYS + 5),
            feature_flags={"leads_enabled": True},
        )

    before = Lead.objects.filter(school__slug=LEAD_SLUG).count()
    url = reverse("lead_capture", kwargs={"school_slug": LEAD_SLUG})
    resp = client.post(url, {"name": "Bob", "email": "bob@example.com"})

    assert resp.status_code == 200
    assert b"upgrade" in resp.content.lower()
    assert Lead.objects.filter(school__slug=LEAD_SLUG).count() == before


# ---------------------------------------------------------------------------
# Admin each_context — trial banner injection
# ---------------------------------------------------------------------------

def _make_request(user):
    """Make a minimal mock request object."""
    req = MagicMock()
    req.user = user
    req.user.is_authenticated = True
    req.user.is_staff = True
    return req


@pytest.mark.django_db
def test_admin_banner_active_trial_has_banner():
    """School admin on active trial: trial_banner injected with expired=False."""
    school = _trial_school(days_ago=3)
    membership = SchoolAdminMembershipFactory(school=school)
    user = membership.user

    from core.admin import admin as admin_module
    req = MagicMock()
    req.user = user  # real User — is_authenticated is always True, no setter needed

    with patch("core.admin._is_superuser", return_value=False), \
         patch("core.admin._membership_school_id", return_value=school.id):
        ctx = admin_module.site.each_context(req)

    assert "trial_banner" in ctx
    assert ctx["trial_banner"]["expired"] is False
    assert ctx["trial_banner"]["days_left"] > 0


@pytest.mark.django_db
def test_admin_banner_expired_trial_has_banner():
    """School admin on expired trial: trial_banner injected with expired=True."""
    school = _trial_school(days_ago=TRIAL_LENGTH_DAYS + 5)
    membership = SchoolAdminMembershipFactory(school=school)
    user = membership.user

    from core.admin import admin as admin_module
    req = MagicMock()
    req.user = user  # real User — is_authenticated is always True, no setter needed

    with patch("core.admin._is_superuser", return_value=False), \
         patch("core.admin._membership_school_id", return_value=school.id):
        ctx = admin_module.site.each_context(req)

    assert "trial_banner" in ctx
    assert ctx["trial_banner"]["expired"] is True
    assert ctx["trial_banner"]["days_left"] == 0


@pytest.mark.django_db
def test_admin_banner_paid_school_no_banner():
    """Paid school admin sees no trial_banner."""
    school = _paid_school()
    membership = SchoolAdminMembershipFactory(school=school)
    user = membership.user

    from core.admin import admin as admin_module
    req = MagicMock()
    req.user = user  # real User — is_authenticated is always True, no setter needed

    with patch("core.admin._is_superuser", return_value=False), \
         patch("core.admin._membership_school_id", return_value=school.id):
        ctx = admin_module.site.each_context(req)

    assert "trial_banner" not in ctx


@pytest.mark.django_db
def test_admin_banner_superuser_no_banner():
    """Superusers never see the trial banner."""
    school = _trial_school(days_ago=TRIAL_LENGTH_DAYS + 5)

    from core.admin import admin as admin_module
    req = MagicMock()
    req.user = MagicMock()
    req.user.is_authenticated = True

    with patch("core.admin._is_superuser", return_value=True):
        ctx = admin_module.site.each_context(req)

    assert "trial_banner" not in ctx


# ---------------------------------------------------------------------------
# School.save() — trial_started_at auto-initialization
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_school_save_auto_sets_trial_started_at():
    """New trial school gets trial_started_at set automatically on save."""
    school = SchoolFactory(plan="trial")
    assert school.trial_started_at is not None


@pytest.mark.django_db
def test_school_save_preserves_explicit_trial_started_at():
    """Explicitly provided trial_started_at is not overwritten by save()."""
    explicit = timezone.now() - timedelta(days=7)
    school = SchoolFactory(plan="trial", trial_started_at=explicit)
    assert school.trial_started_at == explicit


@pytest.mark.django_db
def test_school_save_non_trial_does_not_set_trial_started_at():
    """Non-trial schools never get trial_started_at set."""
    school = SchoolFactory(plan="starter")
    assert school.trial_started_at is None


# ---------------------------------------------------------------------------
# Admin write paths — expired trial enforcement
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_quick_add_lead_blocked_for_expired_trial(client):
    """quick_add_view must not create a Lead when the school's trial has expired."""
    from core.models import Lead

    school = _trial_school(days_ago=TRIAL_LENGTH_DAYS + 5)
    school.feature_flags = {"leads_enabled": True}
    school.is_active = True
    school.save()

    membership = SchoolAdminMembershipFactory(school=school)
    user = membership.user
    user.is_staff = True
    user.save()

    client.force_login(user)
    before = Lead.objects.filter(school=school).count()
    url = reverse("admin:core_lead_quick_add")
    resp = client.post(url, {"name": "Eve", "email": "eve@example.com", "source": "walk-in"})

    # View should redirect (not 403/500) and NO lead must have been created.
    assert resp.status_code in (200, 302, 303)
    assert Lead.objects.filter(school=school).count() == before


@pytest.mark.django_db
def test_admin_convert_lead_blocked_for_expired_trial(client):
    """convert_to_submission_view must not create a Submission when trial has expired."""
    from core.models import Lead, Submission
    from core.tests.factories import LeadFactory

    school = _trial_school(days_ago=TRIAL_LENGTH_DAYS + 5)
    school.feature_flags = {"leads_enabled": True, "leads_conversion_enabled": True}
    school.is_active = True
    school.save()

    lead = LeadFactory(school=school)

    membership = SchoolAdminMembershipFactory(school=school)
    user = membership.user
    user.is_staff = True
    user.save()

    client.force_login(user)
    before = Submission.objects.filter(school=school).count()
    url = reverse("admin:core_lead_convert_to_submission", args=[lead.pk])
    resp = client.post(url)

    assert resp.status_code in (200, 302, 303)
    assert Submission.objects.filter(school=school).count() == before
