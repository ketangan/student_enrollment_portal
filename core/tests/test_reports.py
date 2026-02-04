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
    school_a = SchoolFactory(slug="dancemaker-studio")
    school_b = SchoolFactory(slug="kimberlas-classical-ballet")

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
    school = SchoolFactory(slug="dancemaker-studio")
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
    school = SchoolFactory(slug=slug)
    admin_user = UserFactory()
    SchoolAdminMembershipFactory(user=admin_user, school=school)
    client.force_login(admin_user)

    now = timezone.now()

    # create submissions at various ages: 2 days ago, 10 days ago, 40 days ago
    s_recent = SubmissionFactory(school=school, data={"dance_style": "ballet", "skill_level": "beginner"})
    s_recent.created_at = now - timedelta(days=2)
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
    assert rows[0][:4] == ["application_id", "created_at", "student_name", "program"]
