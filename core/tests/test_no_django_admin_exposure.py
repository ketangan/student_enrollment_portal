"""
Tests that no school admin is ever redirected to the Django Admin / Jazzmin UI.

Covers the three paths identified in the audit:
  1. trial_expired.html billing_url (apply form, lead form, embed form)
  2. billing.html back link
  3. feature_disabled.html back link (leads, reports)
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import School, SchoolAdminMembership
from core.tests.factories import SchoolAdminMembershipFactory, SchoolFactory, UserFactory


# ── helpers ──────────────────────────────────────────────────────────────────

def _expired_trial_school(slug):
    """School whose trial expired yesterday.

    Must use a slug that has a real YAML config file, because apply_view loads
    config before reaching the trial-expired branch.
    """
    from datetime import timedelta
    school, _ = School.objects.get_or_create(
        slug=slug,
        defaults={
            "display_name": "Expired School",
            "plan": "trial",
            "trial_started_at": timezone.now() - timedelta(days=60),
            "trial_end_date": timezone.now() - timedelta(days=1),
        },
    )
    # Ensure it's in expired state even if the record already existed
    school.plan = "trial"
    school.trial_started_at = timezone.now() - timedelta(days=60)
    school.trial_end_date = timezone.now() - timedelta(days=1)
    school.save(update_fields=["plan", "trial_started_at", "trial_end_date"])
    return school


def _owner(school):
    u = UserFactory()
    SchoolAdminMembershipFactory(user=u, school=school, role="owner")
    return u


# ── 1. trial_expired.html — apply form ───────────────────────────────────────

@pytest.mark.django_db
def test_trial_expired_apply_billing_url_is_school_admin(client):
    """trial_expired page on apply form must link to school billing, not /admin/billing/.

    Uses 'enrollment-request-demo' slug which has a real YAML config — apply_view
    loads config before the trial-expired check so the slug must resolve.
    """
    school = _expired_trial_school("enrollment-request-demo")
    url = reverse("apply", kwargs={"school_slug": school.slug})
    r = client.get(url)
    assert r.status_code == 200
    content = r.content.decode()
    expected = f"/schools/{school.slug}/admin/billing/"
    assert expected in content, "School billing URL must appear in trial-expired page"
    assert "/admin/billing/" not in content.replace(expected, ""), \
        "Old Django Admin billing URL must not appear"


# ── 1b. Canary: no reverse("admin:billing") survives in views_public.py ──────

def test_no_admin_billing_reverse_in_views_public():
    """Canary test — catches if someone re-introduces reverse('admin:billing') in views_public.py."""
    import pathlib
    src = pathlib.Path("core/views_public.py").read_text()
    assert 'reverse("admin:billing")' not in src


# ── 2. billing.html — back link ──────────────────────────────────────────────

@pytest.mark.django_db
def test_billing_page_back_link_is_school_dashboard(client):
    """The back link on the school billing page must point to the school dashboard."""
    school = SchoolFactory(plan="starter")
    user = _owner(school)
    client.force_login(user)
    url = reverse("school_billing", kwargs={"school_slug": school.slug})
    r = client.get(url)
    assert r.status_code == 200
    content = r.content.decode()
    dashboard_url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    assert dashboard_url in content, "Back link must point to school dashboard"
    assert 'href="/admin/"' not in content, "Must not link to /admin/"


# ── 3. feature_disabled.html — leads ─────────────────────────────────────────

@pytest.mark.django_db
def test_leads_feature_disabled_back_link_is_school_dashboard(client):
    """feature_disabled page for leads must link back to school dashboard, not /admin/."""
    school = SchoolFactory(plan="trial", feature_flags={"leads_enabled": False})
    user = _owner(school)
    client.force_login(user)
    url = reverse("school_leads", kwargs={"school_slug": school.slug})
    r = client.get(url)
    assert r.status_code == 403
    content = r.content.decode()
    dashboard_url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    assert dashboard_url in content
    assert 'href="/admin/"' not in content


@pytest.mark.django_db
def test_reports_feature_disabled_back_link_is_school_dashboard(client):
    """feature_disabled page for reports must link back to school dashboard, not /admin/."""
    school = SchoolFactory(plan="trial", feature_flags={"reports_enabled": False})
    user = _owner(school)
    client.force_login(user)
    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    r = client.get(url)
    assert r.status_code == 403
    content = r.content.decode()
    dashboard_url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    assert dashboard_url in content
    assert 'href="/admin/"' not in content


# ── 4. Global: no /admin/ href survives for authenticated school admins ───────

@pytest.mark.django_db
def test_school_dashboard_has_no_raw_admin_href_for_non_superuser(client):
    """School dashboard HTML must not contain href='/admin/' for a non-superuser."""
    school = SchoolFactory(plan="starter")
    user = _owner(school)
    client.force_login(user)
    url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    r = client.get(url)
    assert r.status_code == 200
    assert 'href="/admin/"' not in r.content.decode()


@pytest.mark.django_db
def test_school_billing_page_has_no_raw_admin_href_for_non_superuser(client):
    """School billing page HTML must not contain href='/admin/' or /admin/billing/ for a non-superuser."""
    school = SchoolFactory(plan="starter")
    user = _owner(school)
    client.force_login(user)
    url = reverse("school_billing", kwargs={"school_slug": school.slug})
    r = client.get(url)
    assert r.status_code == 200
    content = r.content.decode()
    assert 'href="/admin/"' not in content
    assert 'href="/admin/billing/"' not in content
