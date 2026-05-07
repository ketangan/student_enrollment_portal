"""
Tests for the school-facing admin product:
  - SchoolAdminRedirectMiddleware
  - /schools/<slug>/admin/           (dashboard)
  - /schools/<slug>/admin/submissions/
  - /schools/<slug>/admin/leads/
  - /schools/<slug>/admin/reports
  - Pure-unit tests for view helpers (_fetch_with_cap, get_submission_status_css)

Naming: each test is prefixed with the area under test.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from core.tests.factories import (
    LeadFactory,
    SchoolAdminMembershipFactory,
    SchoolFactory,
    SubmissionFactory,
    UserFactory,
)
from core.models import LEAD_STATUS_CHOICES, LEAD_STATUS_NEW, Submission


# ── Helpers ───────────────────────────────────────────────────────────────


def _school_admin_user(school):
    """Create a staff user with a membership for *school*."""
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    return user


def _superuser():
    u = UserFactory()
    u.is_superuser = True
    u.is_staff = True
    u.save()
    return u


# ── Middleware ────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_middleware_redirects_school_admin_from_admin_index(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    resp = client.get("/admin/")
    assert resp.status_code == 302
    assert resp["Location"].endswith(
        reverse("school_dashboard", kwargs={"school_slug": school.slug})
    )


@pytest.mark.django_db
def test_middleware_does_not_redirect_superuser(client):
    _superuser()  # ensure DB has a superuser
    su = _superuser()
    client.force_login(su)

    resp = client.get("/admin/")
    # superuser stays in Django admin (200 or redirect to login is fine, not 302 to dashboard)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_middleware_does_not_redirect_deep_link(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    sub = SubmissionFactory(school=school, status="New")
    client.force_login(user)

    url = reverse("admin:core_submission_change", args=[sub.id])
    resp = client.get(url)
    # deep-link should NOT be intercepted by middleware; school admin can reach it
    assert resp.status_code == 200


@pytest.mark.django_db
def test_middleware_safe_when_user_has_no_membership(client):
    """Staff without a membership should not be redirected (and not 500)."""
    user = UserFactory()
    user.is_staff = True
    user.save()
    client.force_login(user)

    resp = client.get("/admin/")
    # No membership → middleware skips → Django admin login check or 200
    assert resp.status_code in (200, 302)
    # Must NOT redirect to a school dashboard
    location = resp.get("Location", "")
    assert "/schools/" not in location


# ── Dashboard ─────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_dashboard_requires_login(client):
    school = SchoolFactory()
    url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 302  # redirect to login


@pytest.mark.django_db
def test_dashboard_school_admin_access(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_dashboard_cross_school_blocked(client):
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    client.force_login(user)

    url = reverse("school_dashboard", kwargs={"school_slug": school_b.slug})
    resp = client.get(url)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_dashboard_superuser_can_access_any_school(client):
    school = SchoolFactory()
    su = _superuser()
    client.force_login(su)

    url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_dashboard_shows_leads_nav_when_enabled(client):
    school = SchoolFactory()
    school.feature_flags = {"leads_enabled": True}
    school.save()

    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode()
    assert "Leads" in content


@pytest.mark.django_db
def test_dashboard_hides_leads_nav_when_disabled(client):
    school = SchoolFactory()
    school.feature_flags = {"leads_enabled": False}
    school.save()

    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode()
    # Nav should not contain a Leads link when disabled
    leads_nav_url = reverse("school_leads", kwargs={"school_slug": school.slug})
    assert leads_nav_url not in content


# ── Submissions ───────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_submissions_requires_login(client):
    school = SchoolFactory()
    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 302


@pytest.mark.django_db
def test_submissions_school_admin_access(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    SubmissionFactory(school=school, status="New")
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_submissions_cross_school_blocked(client):
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = _school_admin_user(school_a)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school_b.slug})
    resp = client.get(url)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_submissions_scoped_to_school(client):
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    sub_a = SubmissionFactory(
        school=school_a,
        data={"first_name": "Alice", "last_name": "Smith"},
        status="New",
    )
    SubmissionFactory(
        school=school_b,
        data={"first_name": "Bob", "last_name": "Jones"},
        status="New",
    )

    user = _school_admin_user(school_a)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school_a.slug})
    resp = client.get(url)
    content = resp.content.decode()

    assert "Alice" in content
    assert "Bob" not in content


@pytest.mark.django_db
def test_submissions_empty_state(client):
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    assert "No submissions found" in resp.content.decode()


@pytest.mark.django_db
def test_submissions_status_filter(client):
    school = SchoolFactory()
    SubmissionFactory(school=school, data={"first_name": "Alice", "last_name": "A"}, status="New")
    SubmissionFactory(school=school, data={"first_name": "Bob", "last_name": "B"}, status="Enrolled")

    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url, {"status": "New"})
    content = resp.content.decode()
    assert "Alice" in content
    assert "Bob" not in content


@pytest.mark.django_db
def test_submissions_action_link_points_to_school_admin_detail(client):
    school = SchoolFactory()
    sub = SubmissionFactory(school=school, status="New")
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    content = resp.content.decode()

    expected_link = reverse("school_submission_detail", kwargs={"school_slug": school.slug, "submission_id": sub.id})
    assert expected_link in content


@pytest.mark.django_db
def test_submissions_search_matches_student_name(client):
    school = SchoolFactory()
    SubmissionFactory(school=school, data={"first_name": "Zara", "last_name": "Cruz"}, status="New")
    SubmissionFactory(school=school, data={"first_name": "Other", "last_name": "Person"}, status="New")

    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url, {"q": "zara"})
    content = resp.content.decode()

    assert "Zara" in content
    assert "Other" not in content


@pytest.mark.django_db
def test_submissions_search_matches_parent_contact(client):
    school = SchoolFactory()
    SubmissionFactory(
        school=school,
        data={"first_name": "Kid", "last_name": "One", "contact_email": "parent@example.com"},
        status="New",
    )
    SubmissionFactory(
        school=school,
        data={"first_name": "Kid", "last_name": "Two", "contact_email": "other@other.com"},
        status="New",
    )

    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url, {"q": "parent@example.com"})
    content = resp.content.decode()

    assert "One" in content
    assert "Two" not in content


@pytest.mark.django_db
def test_submissions_search_does_not_match_json_field_keys(client):
    """Searching for a JSON field key name (not a value) must NOT return results."""
    school = SchoolFactory()
    # The submission has a field named "contact_email" — but searching for "contact_email"
    # (the key itself) must not match, only values are searched.
    SubmissionFactory(
        school=school,
        data={"first_name": "Test", "last_name": "User", "contact_email": "real@example.com"},
        status="New",
    )

    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url, {"q": "contact_email"})
    content = resp.content.decode()

    # The JSON key "contact_email" must not cause the row to appear
    assert "No submissions found" in content


# ── Leads ────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_leads_disabled_shows_feature_disabled_page(client):
    """When leads_enabled=False, leads list returns 403 feature_disabled page (not 404)."""
    school = SchoolFactory()
    school.feature_flags = {"leads_enabled": False}
    school.save()

    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_leads", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 403
    assert b"Leads is disabled" in resp.content


@pytest.mark.django_db
def test_leads_access_when_enabled(client):
    school = SchoolFactory()
    school.feature_flags = {"leads_enabled": True}
    school.save()

    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_leads", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_leads_cross_school_blocked(client):
    school_a = SchoolFactory()
    school_a.feature_flags = {"leads_enabled": True}
    school_a.save()
    school_b = SchoolFactory()
    school_b.feature_flags = {"leads_enabled": True}
    school_b.save()

    user = _school_admin_user(school_a)
    client.force_login(user)

    url = reverse("school_leads", kwargs={"school_slug": school_b.slug})
    resp = client.get(url)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_leads_scoped_to_school(client):
    school_a = SchoolFactory()
    school_a.feature_flags = {"leads_enabled": True}
    school_a.save()
    school_b = SchoolFactory()

    LeadFactory(school=school_a, name="Carol Leads", status=LEAD_STATUS_NEW)
    LeadFactory(school=school_b, name="Dave Other", status=LEAD_STATUS_NEW)

    user = _school_admin_user(school_a)
    client.force_login(user)

    url = reverse("school_leads", kwargs={"school_slug": school_a.slug})
    resp = client.get(url)
    content = resp.content.decode()

    assert "Carol Leads" in content
    assert "Dave Other" not in content


@pytest.mark.django_db
def test_leads_empty_state(client):
    school = SchoolFactory()
    school.feature_flags = {"leads_enabled": True}
    school.save()

    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_leads", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    assert "No leads found" in resp.content.decode()


@pytest.mark.django_db
def test_leads_action_link_points_to_school_admin_detail(client):
    school = SchoolFactory()
    school.feature_flags = {"leads_enabled": True}
    school.save()

    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_leads", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    content = resp.content.decode()

    expected_link = reverse("school_lead_detail", kwargs={"school_slug": school.slug, "lead_id": lead.id})
    assert expected_link in content


# ── Reports ───────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_reports_uses_shared_nav(client):
    """Reports page should now include the shared school-admin nav."""
    school = SchoolFactory(slug="dancemaker-studio", plan="starter")
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode()

    # Nav should include Dashboard link and Submissions link
    assert reverse("school_dashboard", kwargs={"school_slug": school.slug}) in content
    assert reverse("school_submissions", kwargs={"school_slug": school.slug}) in content


@pytest.mark.django_db
def test_reports_school_admin_access(client):
    school = SchoolFactory(slug="dancemaker-studio", plan="starter")
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_reports_cross_school_blocked(client):
    school_a = SchoolFactory(slug="dancemaker-studio", plan="starter")
    school_b = SchoolFactory(plan="starter")
    user = _school_admin_user(school_a)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school_b.slug})
    resp = client.get(url)
    assert resp.status_code == 404


# ── Helper unit tests: slice_list_with_cap ────────────────────────────────


def test_slice_list_with_cap_under_limit():
    """No cap when list is smaller than the limit."""
    from core.views import slice_list_with_cap

    rows, cap_hit = slice_list_with_cap(list(range(3)), 5)
    assert rows == [0, 1, 2]
    assert cap_hit is False


def test_slice_list_with_cap_at_limit():
    """No cap when list length exactly equals the limit."""
    from core.views import slice_list_with_cap

    rows, cap_hit = slice_list_with_cap(list(range(5)), 5)
    assert rows == [0, 1, 2, 3, 4]
    assert cap_hit is False


def test_slice_list_with_cap_over_limit():
    """Cap triggered when list exceeds the limit."""
    from core.views import slice_list_with_cap

    rows, cap_hit = slice_list_with_cap(list(range(6)), 5)
    assert rows == [0, 1, 2, 3, 4]
    assert cap_hit is True


# ── Helper unit tests: fetch_queryset_with_cap ────────────────────────────


@pytest.mark.django_db
def test_fetch_queryset_with_cap_under_limit():
    from core.views import fetch_queryset_with_cap

    school = SchoolFactory()
    for _ in range(3):
        SubmissionFactory(school=school, status="New")
    qs = Submission.objects.filter(school=school)
    rows, cap_hit = fetch_queryset_with_cap(qs, 5)
    assert len(rows) == 3
    assert cap_hit is False


@pytest.mark.django_db
def test_fetch_queryset_with_cap_over_limit():
    from core.views import fetch_queryset_with_cap

    school = SchoolFactory()
    for _ in range(6):
        SubmissionFactory(school=school, status="New")
    qs = Submission.objects.filter(school=school)
    rows, cap_hit = fetch_queryset_with_cap(qs, 5)
    assert len(rows) == 5
    assert cap_hit is True


def test_get_submission_status_css_known_status():
    from core.views import get_submission_status_css

    assert get_submission_status_css("New") == "dash-badge--blue"
    assert get_submission_status_css("Enrolled") == "dash-badge--green"
    assert get_submission_status_css("Declined") == "dash-badge--red"


def test_get_submission_status_css_unknown_falls_back():
    from core.views import get_submission_status_css

    assert get_submission_status_css("SomethingUnknown") == "dash-badge--gray"
    assert get_submission_status_css("") == "dash-badge--gray"
    assert get_submission_status_css(None) == "dash-badge--gray"


# ── Dashboard — inbox ordering ─────────────────────────────────────────────


@pytest.mark.django_db
def test_dashboard_inbox_new_submissions_appear_first(client):
    """New submissions appear in the inbox even when they are the oldest.

    Creates 6 Enrolled submissions (which become the most-recent by timestamp),
    then stamps a New submission as the oldest using an .update().  Without
    New-first ordering the inbox would show only the 6 Enrolled ones; with it
    the New submission must appear regardless of its date.
    """
    from datetime import timedelta

    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    # New submission created first → will have the earliest auto_now_add timestamp.
    # Force it even further into the past so the ordering test is unambiguous.
    new_sub = SubmissionFactory(
        school=school,
        status="New",
        data={"first_name": "NewKid", "last_name": "Student"},
    )
    Submission.objects.filter(pk=new_sub.pk).update(
        created_at=timezone.now() - timedelta(days=30)
    )

    # Six Enrolled submissions with default (recent) timestamps.
    for i in range(6):
        SubmissionFactory(
            school=school,
            status="Enrolled",
            data={"first_name": f"Enrolled{i}", "last_name": "User"},
        )

    url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    # New submission must appear in the inbox despite being the oldest.
    assert "NewKid" in resp.content.decode()


# ── Dashboard — KPI links ──────────────────────────────────────────────────


@pytest.mark.django_db
def test_dashboard_kpi_links_use_status_param_not_exact(client):
    """KPI filter links must use ?status= (school submissions format), not ?status__exact=."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)
    SubmissionFactory(school=school, status="New")

    url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    content = resp.content.decode()

    assert "status__exact" not in content
    assert "status=New" in content


# ── Submissions — display cap ──────────────────────────────────────────────


@pytest.mark.django_db
def test_submissions_display_cap_hit_shown_in_response(client):
    """When >200 matching submissions exist the capped-count message is rendered."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    for i in range(201):
        SubmissionFactory(
            school=school,
            status="New",
            data={"first_name": f"Kid{i}", "last_name": "Test"},
        )

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url, {"status": "New"})
    assert resp.status_code == 200
    assert b"Showing first 200" in resp.content


# ── Middleware — exception narrowing ──────────────────────────────────────


@pytest.mark.django_db
def test_middleware_catches_missing_membership_not_broad_errors(client):
    """A staff user with no SchoolAdminMembership row must not crash middleware.

    RelatedObjectDoesNotExist (subclass of ObjectDoesNotExist) is the only
    exception that should be swallowed.  The existing no-membership test
    verifies the happy path; this test documents the intent explicitly.
    """
    user = UserFactory()
    user.is_staff = True
    user.save()
    client.force_login(user)

    # Accessing /admin/ for a staff-but-unmembered user should not 500.
    resp = client.get("/admin/")
    assert resp.status_code in (200, 302)
    assert "/schools/" not in resp.get("Location", "")


# ── Middleware — ADMIN_URL setting (Round 3) ───────────────────────────────


def test_middleware_reads_admin_url_from_settings():
    """Middleware __init__ builds _admin_index_paths from settings.ADMIN_URL.

    Creating a fresh middleware instance inside override_settings picks up the
    overridden value — simulates a deployment with a hardened admin URL.
    """
    from unittest.mock import MagicMock

    from django.test import override_settings

    from core.middleware import SchoolAdminRedirectMiddleware

    with override_settings(ADMIN_URL="secretadmin/"):
        mw = SchoolAdminRedirectMiddleware(MagicMock())
        assert "/secretadmin/" in mw._admin_index_paths
        assert "/secretadmin" in mw._admin_index_paths
        # Old default must NOT be in the set for this instance.
        assert "/admin/" not in mw._admin_index_paths


# ── Row builder unit tests: is_new flag (Round 3) ─────────────────────────


@pytest.mark.django_db
def test_build_submission_row_is_new_true_for_new_status():
    from core.views import _build_submission_row

    school = SchoolFactory()
    sub = SubmissionFactory(school=school, status="New", data={"first_name": "A", "last_name": "B"})
    sub.school = school  # ensure select_related is satisfied
    row = _build_submission_row(sub, {})
    assert row["is_new"] is True


@pytest.mark.django_db
def test_build_submission_row_is_new_false_for_enrolled():
    from core.views import _build_submission_row

    school = SchoolFactory()
    sub = SubmissionFactory(school=school, status="Enrolled", data={"first_name": "A", "last_name": "B"})
    sub.school = school
    row = _build_submission_row(sub, {})
    assert row["is_new"] is False


@pytest.mark.django_db
def test_build_lead_row_is_new_true_for_new_status():
    from core.views import _build_lead_row

    school = SchoolFactory()
    lead = LeadFactory(school=school, status=LEAD_STATUS_NEW)
    row = _build_lead_row(lead)
    assert row["is_new"] is True


@pytest.mark.django_db
def test_build_lead_row_is_new_false_for_non_new_status():
    from core.views import _build_lead_row

    school = SchoolFactory()
    lead = LeadFactory(school=school, status="enrolled")
    row = _build_lead_row(lead)
    assert row["is_new"] is False


# ── Submissions template: is_new action labels (Round 3) ──────────────────


@pytest.mark.django_db
def test_submissions_new_status_shows_review_action(client):
    school = SchoolFactory()
    SubmissionFactory(school=school, data={"first_name": "Alice", "last_name": "A"}, status="New")
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert b"Review" in resp.content


@pytest.mark.django_db
def test_submissions_non_new_status_shows_open_action(client):
    school = SchoolFactory()
    SubmissionFactory(school=school, data={"first_name": "Bob", "last_name": "B"}, status="Enrolled")
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_submissions", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert b"Open" in resp.content


@pytest.mark.django_db
def test_leads_new_status_shows_contact_action(client):
    school = SchoolFactory()
    school.feature_flags = {"leads_enabled": True}
    school.save()
    LeadFactory(school=school, status=LEAD_STATUS_NEW)
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_leads", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert b"Contact" in resp.content


# ── Dashboard counts via single aggregate (Round 3) ───────────────────────


@pytest.mark.django_db
def test_dashboard_counts_match_submission_statuses(client):
    """Dashboard context counts must reflect actual submission statuses."""
    school = SchoolFactory()
    user = _school_admin_user(school)
    client.force_login(user)

    SubmissionFactory(school=school, data={"first_name": "A", "last_name": "A"}, status="New")
    SubmissionFactory(school=school, data={"first_name": "B", "last_name": "B"}, status="New")
    SubmissionFactory(school=school, data={"first_name": "C", "last_name": "C"}, status="Enrolled")
    SubmissionFactory(school=school, data={"first_name": "D", "last_name": "D"}, status="Declined")

    url = reverse("school_dashboard", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.context["total_submissions"] == 4
    assert resp.context["new_count"] == 2
    assert resp.context["approved_count"] == 1
    assert resp.context["declined_count"] == 1


# ── Reports URL: trailing slash + redirect (Round 3) ──────────────────────


@pytest.mark.django_db
def test_reports_canonical_slash_url_is_accessible(client):
    """The canonical /admin/reports/ URL (with slash) must return 200."""
    school = SchoolFactory(plan="starter")
    user = _school_admin_user(school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    assert "/reports/" in url, "Named URL must include trailing slash"
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_reports_no_slash_url_redirects_to_canonical(client):
    """The old /admin/reports (no trailing slash) must 301 to /admin/reports/."""
    school = SchoolFactory(plan="starter")
    user = _school_admin_user(school)
    client.force_login(user)

    no_slash_url = f"/schools/{school.slug}/admin/reports"
    resp = client.get(no_slash_url)
    assert resp.status_code == 301
    assert resp["Location"].endswith("/reports/")
