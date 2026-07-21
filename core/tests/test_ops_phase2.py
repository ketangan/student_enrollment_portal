"""
Tests for the /ops/ superadmin portal — Phase 2.
Covers cross-school submissions list, leads list, and reports.
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from core.models import Lead, School, SchoolAdminMembership, Submission


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def superuser(db):
    from django.contrib.auth.models import User
    return User.objects.create_superuser(
        username="ops2_super", email="super2@test.com", password="testpass123"
    )


@pytest.fixture
def regular_user(db):
    from django.contrib.auth.models import User
    return User.objects.create_user(
        username="regular2", email="regular2@test.com", password="testpass123"
    )


@pytest.fixture
def school_a(db):
    return School.objects.create(
        slug="school-a", display_name="School A", plan="starter",
    )


@pytest.fixture
def school_b(db):
    return School.objects.create(
        slug="school-b", display_name="School B", plan="trial",
        trial_started_at=timezone.now(),
    )


@pytest.fixture
def submissions(db, school_a, school_b):
    s1 = Submission.objects.create(school=school_a, status="New", data={})
    s2 = Submission.objects.create(school=school_a, status="Enrolled", data={})
    s3 = Submission.objects.create(school=school_b, status="New", data={})
    return [s1, s2, s3]


@pytest.fixture
def leads(db, school_a, school_b):
    l1 = Lead.objects.create(
        school=school_a, name="Alice Smith", email="alice@test.com",
        normalized_email="alice@test.com", status="new", source="website",
    )
    l2 = Lead.objects.create(
        school=school_a, name="Bob Jones", email="bob@test.com",
        normalized_email="bob@test.com", status="enrolled", source="referral",
    )
    l3 = Lead.objects.create(
        school=school_b, name="Carol Lee", email="carol@test.com",
        normalized_email="carol@test.com", status="new", source="website",
    )
    return [l1, l2, l3]


# ── Submissions view — auth ────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_submissions_requires_superuser_anonymous(client):
    resp = client.get(reverse("ops_submissions"))
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_ops_submissions_blocks_regular_user(client, regular_user):
    client.force_login(regular_user)
    resp = client.get(reverse("ops_submissions"))
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_ops_submissions_accessible_by_superuser(client, superuser):
    client.force_login(superuser)
    resp = client.get(reverse("ops_submissions"))
    assert resp.status_code == 200


# ── Submissions view — listing and filtering ──────────────────────────────────

@pytest.mark.django_db
def test_ops_submissions_shows_all_schools(client, superuser, submissions):
    client.force_login(superuser)
    resp = client.get(reverse("ops_submissions"))
    assert resp.status_code == 200
    assert resp.context["total_count"] == 3


@pytest.mark.django_db
def test_ops_submissions_filter_by_school(client, superuser, submissions, school_a):
    client.force_login(superuser)
    resp = client.get(reverse("ops_submissions") + f"?school={school_a.slug}")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 2
    assert resp.context["school_filter"] == school_a.slug


@pytest.mark.django_db
def test_ops_submissions_filter_by_status(client, superuser, submissions):
    client.force_login(superuser)
    resp = client.get(reverse("ops_submissions") + "?status=Enrolled")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 1


@pytest.mark.django_db
def test_ops_submissions_filter_by_date_from(client, superuser, submissions):
    client.force_login(superuser)
    future = (timezone.now() + timezone.timedelta(days=1)).date().isoformat()
    resp = client.get(reverse("ops_submissions") + f"?date_from={future}")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 0


@pytest.mark.django_db
def test_ops_submissions_filter_by_form_key(client, superuser, school_a):
    client.force_login(superuser)
    Submission.objects.create(school=school_a, status="New", data={}, form_key="secondary")
    Submission.objects.create(school=school_a, status="New", data={}, form_key="default")
    resp = client.get(reverse("ops_submissions") + "?form_key=secondary")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 1


@pytest.mark.django_db
def test_ops_submissions_invalid_date_filter_ignored(client, superuser, submissions):
    client.force_login(superuser)
    resp = client.get(reverse("ops_submissions") + "?date_from=not-a-date")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 3


# ── Leads view — auth ─────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_leads_requires_superuser_anonymous(client):
    resp = client.get(reverse("ops_leads"))
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_ops_leads_blocks_regular_user(client, regular_user):
    client.force_login(regular_user)
    resp = client.get(reverse("ops_leads"))
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_ops_leads_accessible_by_superuser(client, superuser):
    client.force_login(superuser)
    resp = client.get(reverse("ops_leads"))
    assert resp.status_code == 200


# ── Leads view — listing and filtering ───────────────────────────────────────

@pytest.mark.django_db
def test_ops_leads_shows_all_schools(client, superuser, leads):
    client.force_login(superuser)
    resp = client.get(reverse("ops_leads"))
    assert resp.status_code == 200
    assert resp.context["total_count"] == 3


@pytest.mark.django_db
def test_ops_leads_filter_by_school(client, superuser, leads, school_b):
    client.force_login(superuser)
    resp = client.get(reverse("ops_leads") + f"?school={school_b.slug}")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 1


@pytest.mark.django_db
def test_ops_leads_filter_by_status(client, superuser, leads):
    client.force_login(superuser)
    resp = client.get(reverse("ops_leads") + "?status=enrolled")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 1


@pytest.mark.django_db
def test_ops_leads_filter_by_source(client, superuser, leads):
    client.force_login(superuser)
    resp = client.get(reverse("ops_leads") + "?source=referral")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 1


@pytest.mark.django_db
def test_ops_leads_search_by_name(client, superuser, leads):
    client.force_login(superuser)
    resp = client.get(reverse("ops_leads") + "?q=alice")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 1


@pytest.mark.django_db
def test_ops_leads_search_by_email(client, superuser, leads):
    client.force_login(superuser)
    resp = client.get(reverse("ops_leads") + "?q=carol@test.com")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 1


@pytest.mark.django_db
def test_ops_leads_filter_by_date_from(client, superuser, leads):
    client.force_login(superuser)
    future = (timezone.now() + timezone.timedelta(days=1)).date().isoformat()
    resp = client.get(reverse("ops_leads") + f"?date_from={future}")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 0


# ── Reports view — auth ───────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_reports_requires_superuser_anonymous(client):
    resp = client.get(reverse("ops_reports"))
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_ops_reports_blocks_regular_user(client, regular_user):
    client.force_login(regular_user)
    resp = client.get(reverse("ops_reports"))
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_ops_reports_accessible_by_superuser(client, superuser):
    client.force_login(superuser)
    resp = client.get(reverse("ops_reports"))
    assert resp.status_code == 200


# ── Reports view — aggregate data ─────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_reports_totals_correct(client, superuser, submissions, leads, school_a, school_b):
    client.force_login(superuser)
    resp = client.get(reverse("ops_reports"))
    assert resp.status_code == 200
    totals = resp.context["totals"]
    assert totals["submissions"] == 3
    assert totals["leads"] == 3
    assert totals["enrolled"] == 1
    assert totals["schools"] == 2


@pytest.mark.django_db
def test_ops_reports_per_school_rows(client, superuser, submissions, school_a, school_b):
    client.force_login(superuser)
    resp = client.get(reverse("ops_reports"))
    rows = resp.context["school_rows"]
    slugs = [r.slug for r in rows]
    assert "school-a" in slugs
    assert "school-b" in slugs


@pytest.mark.django_db
def test_ops_reports_conversion_rates(client, superuser, school_a):
    client.force_login(superuser)
    # 1 of 2 leads converts to a submission
    sub = Submission.objects.create(school=school_a, status="Enrolled", data={})
    Lead.objects.create(
        school=school_a, name="X", email="x@t.com", normalized_email="x@t.com",
        status="enrolled", source="website", converted_submission=sub,
    )
    Lead.objects.create(
        school=school_a, name="Y", email="y@t.com", normalized_email="y@t.com",
        status="new", source="website",
    )
    Submission.objects.create(school=school_a, status="Enrolled", data={})
    Submission.objects.create(school=school_a, status="New", data={})
    resp = client.get(reverse("ops_reports"))
    totals = resp.context["totals"]
    # 1 converted lead / 2 total leads → 50%
    assert totals["lead_to_sub_rate"] == 50
    # 2 enrolled / 3 subs → 67%
    assert totals["sub_to_enrolled_rate"] == 67


@pytest.mark.django_db
def test_ops_reports_lead_rate_never_exceeds_100(client, superuser, school_a):
    """Lead→App rate must be based on converted leads, not raw submission count.

    A school with 4 leads and 120 submissions should NOT show 3000%.
    Even if every submission came from somewhere, only leads with a linked
    converted_submission count as converted.
    """
    client.force_login(superuser)
    for i in range(120):
        Submission.objects.create(school=school_a, status="New", data={})
    for i in range(4):
        Lead.objects.create(
            school=school_a, name=f"Lead {i}", email=f"l{i}@t.com",
            normalized_email=f"l{i}@t.com", status="new", source="website",
        )
    resp = client.get(reverse("ops_reports"))
    totals = resp.context["totals"]
    # 0 of 4 leads have converted_submission set → rate is 0%
    assert totals["lead_to_sub_rate"] == 0
    # Rate must never exceed 100
    assert totals["lead_to_sub_rate"] <= 100


@pytest.mark.django_db
def test_ops_reports_zero_leads_no_crash(client, superuser, school_a):
    """No ZeroDivisionError when a school has zero leads or zero submissions."""
    client.force_login(superuser)
    resp = client.get(reverse("ops_reports"))
    assert resp.status_code == 200
    totals = resp.context["totals"]
    assert totals["lead_to_sub_rate"] is None


# ── Pagination ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_submissions_pagination(client, superuser, school_a):
    client.force_login(superuser)
    for i in range(55):
        Submission.objects.create(school=school_a, status="New", data={})
    resp = client.get(reverse("ops_submissions"))
    assert resp.status_code == 200
    assert resp.context["page_obj"].paginator.num_pages == 2
    assert len(list(resp.context["page_obj"])) == 50


@pytest.mark.django_db
def test_ops_leads_pagination(client, superuser, school_a):
    client.force_login(superuser)
    for i in range(55):
        Lead.objects.create(
            school=school_a, name=f"Lead {i}", email=f"lead{i}@t.com",
            normalized_email=f"lead{i}@t.com", status="new", source="website",
        )
    resp = client.get(reverse("ops_leads"))
    assert resp.status_code == 200
    assert resp.context["page_obj"].paginator.num_pages == 2
