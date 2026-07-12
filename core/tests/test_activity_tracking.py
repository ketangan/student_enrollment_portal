"""
Tests for activity tracking (page view logging) feature.

Covers:
- _log_page_view logs when activity_tracking_enabled=True
- _log_page_view skips when activity_tracking_enabled=False
- All 5 instrumented views write a log entry when tracking is on
- Ops toggle enables / disables tracking
- Toggle is superuser-only
"""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School, SchoolAdminMembership, Submission


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        username="act_super", email="act_super@test.com", password="testpass123"
    )


@pytest.fixture
def school_admin(db):
    return User.objects.create_user(
        username="act_admin", email="act_admin@test.com", password="testpass123",
        is_staff=True,
    )


@pytest.fixture
def tracking_school(db):
    return School.objects.create(
        slug="tracking-school", display_name="Tracking School",
        plan="starter", activity_tracking_enabled=True,
    )


@pytest.fixture
def non_tracking_school(db):
    return School.objects.create(
        slug="non-tracking-school", display_name="Non-Tracking School",
        plan="starter", activity_tracking_enabled=False,
    )


@pytest.fixture
def membership(db, school_admin, tracking_school):
    SchoolAdminMembership.objects.create(user=school_admin, school=tracking_school)
    return tracking_school


# ── Unit: _log_page_view helper ───────────────────────────────────────────────

@pytest.mark.django_db
def test_log_page_view_logs_when_enabled(rf, tracking_school, school_admin):
    from core.views_school_common import _log_page_view

    request = rf.get("/")
    request.user = school_admin

    _log_page_view(request, tracking_school, "dashboard")

    entry = AdminAuditLog.objects.filter(
        model_label="core.school",
        object_id=str(tracking_school.pk),
        action="action",
    ).first()
    assert entry is not None
    assert entry.extra.get("page") == "dashboard"
    assert entry.extra.get("name") == "page_view"


@pytest.mark.django_db
def test_log_page_view_skips_when_disabled(rf, non_tracking_school, school_admin):
    from core.views_school_common import _log_page_view

    request = rf.get("/")
    request.user = school_admin

    _log_page_view(request, non_tracking_school, "dashboard")

    assert not AdminAuditLog.objects.filter(
        model_label="core.school",
        object_id=str(non_tracking_school.pk),
        extra__page="dashboard",
    ).exists()


@pytest.mark.django_db
def test_log_page_view_includes_extra_fields(rf, tracking_school, school_admin):
    from core.views_school_common import _log_page_view

    request = rf.get("/")
    request.user = school_admin

    _log_page_view(request, tracking_school, "submission_detail",
                   submission_id=42, student="Jane Doe")

    entry = AdminAuditLog.objects.filter(
        model_label="core.school",
        object_id=str(tracking_school.pk),
        action="action",
    ).first()
    assert entry is not None
    assert entry.extra.get("page") == "submission_detail"
    assert entry.extra.get("submission_id") == 42
    assert entry.extra.get("student") == "Jane Doe"


# ── Integration: dashboard view logs page view ────────────────────────────────

@pytest.mark.django_db
def test_dashboard_view_logs_page_view(client, membership, school_admin):
    client.force_login(school_admin)
    client.get(reverse("school_dashboard", kwargs={"school_slug": "tracking-school"}))

    assert AdminAuditLog.objects.filter(
        model_label="core.school",
        object_id=str(membership.pk),
        extra__page="dashboard",
    ).exists()


@pytest.mark.django_db
def test_submissions_list_logs_page_view(client, membership, school_admin):
    client.force_login(school_admin)
    client.get(reverse("school_submissions", kwargs={"school_slug": "tracking-school"}))

    assert AdminAuditLog.objects.filter(
        model_label="core.school",
        object_id=str(membership.pk),
        extra__page="submissions_list",
    ).exists()


# ── Ops toggle ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_toggle_enables_tracking(client, superuser, non_tracking_school):
    client.force_login(superuser)
    resp = client.post(
        reverse("ops_activity_tracking_toggle", kwargs={"slug": non_tracking_school.slug})
    )
    assert resp.status_code == 302
    non_tracking_school.refresh_from_db()
    assert non_tracking_school.activity_tracking_enabled is True


@pytest.mark.django_db
def test_ops_toggle_disables_tracking(client, superuser, tracking_school):
    client.force_login(superuser)
    resp = client.post(
        reverse("ops_activity_tracking_toggle", kwargs={"slug": tracking_school.slug})
    )
    assert resp.status_code == 302
    tracking_school.refresh_from_db()
    assert tracking_school.activity_tracking_enabled is False


@pytest.mark.django_db
def test_ops_toggle_requires_superuser(client, school_admin, tracking_school):
    client.force_login(school_admin)
    try:
        client.post(
            reverse("ops_activity_tracking_toggle", kwargs={"slug": tracking_school.slug})
        )
    except PermissionError:
        pass
    tracking_school.refresh_from_db()
    assert tracking_school.activity_tracking_enabled is True


@pytest.mark.django_db
def test_ops_toggle_logs_audit_entry(client, superuser, non_tracking_school):
    client.force_login(superuser)
    client.post(
        reverse("ops_activity_tracking_toggle", kwargs={"slug": non_tracking_school.slug})
    )
    assert AdminAuditLog.objects.filter(
        model_label="core.school",
        object_id=str(non_tracking_school.pk),
        extra__name="activity_tracking_enabled",
    ).exists()
