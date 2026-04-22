import csv
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from core.tests.factories import UserFactory, SchoolFactory, SubmissionFactory, SchoolAdminMembershipFactory
from core.services.config_loader import load_school_config


@pytest.mark.django_db
def test_unauthenticated_is_blocked(client):
    url = reverse("school_reports", kwargs={"school_slug": "dancemaker-studio"})
    resp = client.get(url)
    assert resp.status_code in (302, 404)


@pytest.mark.django_db
def test_school_admin_access_and_cross_school_block(client):
    school_a = SchoolFactory(slug="dancemaker-studio", plan="starter")
    school_b = SchoolFactory(slug="kimberlas-classical-ballet", plan="starter")

    user = UserFactory()
    # make membership for school_a and staff
    SchoolAdminMembershipFactory(user=user, school=school_a)

    client.force_login(user)

    url_a = reverse("school_reports", kwargs={"school_slug": school_a.slug})
    resp_a = client.get(url_a)
    assert resp_a.status_code == 200

    url_b = reverse("school_reports", kwargs={"school_slug": school_b.slug})
    resp_b = client.get(url_b)
    assert resp_b.status_code == 404


@pytest.mark.django_db
def test_superuser_can_access_any_school(client):
    school = SchoolFactory(slug="dancemaker-studio", plan="starter")
    admin = UserFactory()
    admin.is_superuser = True
    admin.is_staff = True
    admin.save()

    client.force_login(admin)
    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_range_filter_counts_and_export_csv(client):
    slug = "dancemaker-studio"
    school = SchoolFactory(slug=slug, plan="starter")
    admin_user = UserFactory()
    SchoolAdminMembershipFactory(user=admin_user, school=school)
    client.force_login(admin_user)

    now = timezone.now()

    # create submissions at various ages: 2 days ago, 10 days ago, 40 days ago
    s_recent = SubmissionFactory(school=school, data={"dance_style": "ballet", "skill_level": "beginner"})
    s_recent.created_at = now - timedelta(days=2)
    s_recent.status = "Contacted"
    s_recent.save()

    s_mid = SubmissionFactory(school=school, data={"dance_style": "jazz", "skill_level": "beginner"})
    s_mid.created_at = now - timedelta(days=10)
    s_mid.save()

    s_old = SubmissionFactory(school=school, data={"dance_style": "hip_hop", "skill_level": "advanced"})
    s_old.created_at = now - timedelta(days=40)
    s_old.save()

    # range=7 should include only s_recent
    url7 = reverse("school_reports", kwargs={"school_slug": slug}) + "?range=7"
    resp7 = client.get(url7)
    assert resp7.status_code == 200
    ctx7 = resp7.context
    assert ctx7 is not None
    assert ctx7["total"] == 1

    # range=30 should include recent and mid
    url30 = reverse("school_reports", kwargs={"school_slug": slug}) + "?range=30"
    resp30 = client.get(url30)
    assert resp30.status_code == 200
    assert resp30.context["total"] == 2

    # recent submissions include status (new feature)
    recent_rows = resp30.context.get("recent") or []
    assert any(r.get("status") == "Contacted" for r in recent_rows)
    assert any(r.get("status") == "New" for r in recent_rows)

    # range=90 includes all three
    url90 = reverse("school_reports", kwargs={"school_slug": slug}) + "?range=90"
    resp90 = client.get(url90)
    assert resp90.status_code == 200
    assert resp90.context["total"] == 3

    # program filter: filter to Ballet (Beginner)
    # The program display string for dance_style+skill_level should be like 'Ballet (Beginner)'
    config = load_school_config(slug)
    assert config is not None

    # export CSV with range=30 and program filter (should include only matching rows)
    program_label = "Ballet (Beginner)"
    csv_url = reverse("school_reports", kwargs={"school_slug": slug}) + f"?range=30&export=1&program={program_label}"
    resp_csv = client.get(csv_url)
    assert resp_csv.status_code == 200
    assert "text/csv" in resp_csv["Content-Type"]
    cd = resp_csv.get("Content-Disposition", "")
    assert slug in cd and "last30d" in cd

    body = resp_csv.content.decode("utf-8")
    reader = csv.reader(body.splitlines())
    rows = list(reader)
    # header + one matching row expected
    assert len(rows) >= 1
    assert rows[0][:5] == ["application_id", "created_at", "status", "student_name", "program"]

    # The single returned row should match the contacted status
    assert any(r[2] == "Contacted" for r in rows[1:])


# ---------------------------------------------------------------------------
# Feature-flag gate on reports view
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_reports_blocked_when_reports_disabled_for_trial_school(client):
    """Trial plan schools have reports_enabled=False by default → 403."""
    school = SchoolFactory(slug="trial-school-test", plan="trial", feature_flags={"reports_enabled": False})
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 403
    assert b"Reports" in resp.content
    assert b"disabled" in resp.content


@pytest.mark.django_db
def test_reports_allowed_when_reports_enabled_via_plan(client):
    """Starter plan schools have reports_enabled=True by default → 200."""
    school = SchoolFactory(slug="starter-school-test", plan="starter")
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_reports_allowed_when_trial_school_overrides_flag(client):
    """Trial school with explicit override reports_enabled=True → 200."""
    school = SchoolFactory(slug="override-trial-test", plan="trial", feature_flags={"reports_enabled": True})
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


@pytest.mark.django_db
def test_reports_blocked_when_starter_school_overrides_flag_to_false(client):
    """Starter school with explicit override reports_enabled=False → 403."""
    school = SchoolFactory(slug="override-starter-test", plan="starter", feature_flags={"reports_enabled": False})
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Feature-flag gate on CSV export from reports page
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_csv_export_blocked_when_flag_disabled(client):
    """csv_export_enabled=False should suppress the export for school admins."""
    school = SchoolFactory(slug="no-csv-school", plan="starter", feature_flags={"csv_export_enabled": False})
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    SubmissionFactory(school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug}) + "?export=1"
    resp = client.get(url)
    # Should get the normal reports HTML page, NOT a CSV download
    assert resp.status_code == 200
    assert "text/csv" not in resp.get("Content-Type", "")


@pytest.mark.django_db
def test_csv_export_allowed_when_flag_enabled(client):
    """csv_export_enabled=True (default for starter) should return CSV."""
    school = SchoolFactory(slug="csv-school", plan="starter")
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    SubmissionFactory(school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug}) + "?export=1"
    resp = client.get(url)
    assert resp.status_code == 200
    assert "text/csv" in resp["Content-Type"]


@pytest.mark.django_db
def test_csv_export_button_hidden_when_flag_disabled(client):
    """The 'Export CSV' link should not appear in the HTML when the flag is off."""
    school = SchoolFactory(slug="hidden-btn-school", plan="starter", feature_flags={"csv_export_enabled": False})
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    SubmissionFactory(school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"Export CSV" not in resp.content


@pytest.mark.django_db
def test_csv_export_button_shown_when_flag_enabled(client):
    """The 'Export CSV' link should appear when the flag is on."""
    school = SchoolFactory(slug="show-btn-school", plan="starter")
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    SubmissionFactory(school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"Export CSV" in resp.content


@pytest.mark.django_db
def test_superuser_can_export_csv_even_when_flag_disabled(client):
    """Superusers bypass csv_export_enabled — they always get the CSV."""
    school = SchoolFactory(slug="su-csv-school", plan="starter", feature_flags={"csv_export_enabled": False})
    su = UserFactory()
    su.is_superuser = True
    su.is_staff = True
    su.save()
    SubmissionFactory(school=school)
    client.force_login(su)

    url = reverse("school_reports", kwargs={"school_slug": school.slug}) + "?export=1"
    resp = client.get(url)
    assert resp.status_code == 200
    assert "text/csv" in resp["Content-Type"]


@pytest.mark.django_db
def test_superuser_sees_export_button_even_when_flag_disabled(client):
    """Superusers should see the Export CSV button regardless of the flag."""
    school = SchoolFactory(slug="su-btn-school", plan="starter", feature_flags={"csv_export_enabled": False})
    su = UserFactory()
    su.is_superuser = True
    su.is_staff = True
    su.save()
    SubmissionFactory(school=school)
    client.force_login(su)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"Export CSV" in resp.content
@pytest.mark.django_db
def test_reports_feature_disabled_template_renders_correctly(client):
    """The feature_disabled.html template should include school info and message."""
    school = SchoolFactory(slug="tmpl-test", plan="trial", feature_flags={"reports_enabled": False})
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 403
    content = resp.content.decode("utf-8")
    assert "Reports" in content
    assert "disabled" in content.lower()
    assert "Back to Admin" in content


@pytest.mark.django_db
def test_superuser_bypasses_reports_disabled_flag(client):
    """Superusers should see reports even when the flag is off."""
    school = SchoolFactory(slug="su-flag-test", plan="trial", feature_flags={"reports_enabled": False})
    su = UserFactory()
    su.is_superuser = True
    su.is_staff = True
    su.save()
    client.force_login(su)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Inactive school enforcement tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_school_reports_returns_404_for_inactive_school(client):
    """School admin should get 404 when their school is inactive."""
    school = SchoolFactory(slug="inactive-school", plan="starter", is_active=False)
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)

    client.force_login(user)
    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_superuser_can_access_reports_for_inactive_school(client):
    """Superusers should bypass inactive school checks."""
    school = SchoolFactory(slug="inactive-school-su", plan="starter", is_active=False)
    su = UserFactory()
    su.is_superuser = True
    su.is_staff = True
    su.save()

    client.force_login(su)
    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Schedule preferences (preferred_time) reporting
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_schedule_rows_counts_and_pct(client):
    """schedule_rows should count preferred_time values and compute percent share."""
    school = SchoolFactory(slug="sched-test-school", plan="starter")
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    SubmissionFactory(school=school, data={"preferred_time": "morning"})
    SubmissionFactory(school=school, data={"preferred_time": "morning"})
    SubmissionFactory(school=school, data={"preferred_time": "afternoon"})
    # one with no preferred_time — should not affect denominator
    SubmissionFactory(school=school, data={})

    url = reverse("school_reports", kwargs={"school_slug": school.slug}) + "?range=90"
    resp = client.get(url)
    assert resp.status_code == 200

    rows = resp.context["schedule_rows"]
    by_val = {r["label"]: r for r in rows}

    # 3 submissions answered; morning=2 (66.7%), afternoon=1 (33.3%)
    assert by_val["morning"]["count"] == 2
    assert by_val["afternoon"]["count"] == 1
    assert abs(by_val["morning"]["pct"] - 66.7) < 0.1
    assert abs(by_val["afternoon"]["pct"] - 33.3) < 0.1


@pytest.mark.django_db
def test_schedule_rows_empty_when_no_data(client):
    """schedule_rows should be empty when no submissions have preferred_time."""
    school = SchoolFactory(slug="sched-empty-school", plan="starter")
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    SubmissionFactory(school=school, data={"some_other_field": "x"})

    url = reverse("school_reports", kwargs={"school_slug": school.slug}) + "?range=90"
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.context["schedule_rows"] == []


# ---------------------------------------------------------------------------
# Enrichment interests (multiselect) reporting
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_enrichment_rows_counts_each_selection_independently(client):
    """Each selected value in a multiselect is counted separately."""
    school = SchoolFactory(slug="enrich-test-school", plan="starter")
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    # 3 submissions: art+music, art+dance, art
    SubmissionFactory(school=school, data={"enrichment_interests": ["art", "music"]})
    SubmissionFactory(school=school, data={"enrichment_interests": ["art", "dance"]})
    SubmissionFactory(school=school, data={"enrichment_interests": ["art"]})
    # one with no enrichment_interests
    SubmissionFactory(school=school, data={})

    url = reverse("school_reports", kwargs={"school_slug": school.slug}) + "?range=90"
    resp = client.get(url)
    assert resp.status_code == 200

    rows = resp.context["enrichment_rows"]
    by_val = {r["label"]: r for r in rows}

    # art=3, music=1, dance=1; total selections=5
    assert by_val["art"]["count"] == 3
    assert by_val["music"]["count"] == 1
    assert by_val["dance"]["count"] == 1
    # percentages based on 5 total selections
    assert abs(by_val["art"]["pct"] - 60.0) < 0.1
    assert abs(by_val["music"]["pct"] - 20.0) < 0.1
    assert abs(by_val["dance"]["pct"] - 20.0) < 0.1


@pytest.mark.django_db
def test_enrichment_rows_empty_when_no_data(client):
    """enrichment_rows should be empty when no submissions have enrichment_interests."""
    school = SchoolFactory(slug="enrich-empty-school", plan="starter")
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    SubmissionFactory(school=school, data={"some_field": "x"})

    url = reverse("school_reports", kwargs={"school_slug": school.slug}) + "?range=90"
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.context["enrichment_rows"] == []


@pytest.mark.django_db
def test_enrichment_rows_skips_empty_string_entries(client):
    """Empty string values in the multiselect list are ignored."""
    school = SchoolFactory(slug="enrich-empty-str-school", plan="starter")
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    SubmissionFactory(school=school, data={"enrichment_interests": ["art", "", "music"]})

    url = reverse("school_reports", kwargs={"school_slug": school.slug}) + "?range=90"
    resp = client.get(url)
    assert resp.status_code == 200

    rows = resp.context["enrichment_rows"]
    by_val = {r["label"]: r for r in rows}

    # "" should be ignored; only art and music counted
    assert "" not in by_val
    assert by_val["art"]["count"] == 1
    assert by_val["music"]["count"] == 1


@pytest.mark.django_db
def test_enrichment_rows_ignores_non_list_data(client):
    """If enrichment_interests is not a list (malformed data), it is skipped gracefully."""
    school = SchoolFactory(slug="enrich-nonlist-school", plan="starter")
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    client.force_login(user)

    # A string instead of a list (malformed)
    SubmissionFactory(school=school, data={"enrichment_interests": "art"})
    # A valid submission
    SubmissionFactory(school=school, data={"enrichment_interests": ["music"]})

    url = reverse("school_reports", kwargs={"school_slug": school.slug}) + "?range=90"
    resp = client.get(url)
    assert resp.status_code == 200

    rows = resp.context["enrichment_rows"]
    by_val = {r["label"]: r for r in rows}

    # Only the list-based submission should count
    assert by_val["music"]["count"] == 1
    assert "art" not in by_val
