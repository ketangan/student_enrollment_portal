import pytest
from django.urls import reverse

from core.tests.factories import SchoolFactory, SubmissionFactory, UserFactory, SchoolAdminMembershipFactory


@pytest.mark.django_db
def test_school_admin_sees_only_their_submissions(client):
    school_a = SchoolFactory(slug="dancemaker-studio")
    school_b = SchoolFactory(slug="kimberlas-classical-ballet")

    # create submissions
    s1 = SubmissionFactory(school=school_a)
    s2 = SubmissionFactory(school=school_a)
    s_other = SubmissionFactory(school=school_b)

    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school_a)
    client.force_login(user)

    url = reverse("admin:core_submission_changelist")
    resp = client.get(url)
    assert resp.status_code == 200

    # admin changelist provides ChangeList as 'cl' in context
    cl = resp.context.get("cl")
    assert cl is not None
    qs = getattr(cl, "queryset", None)
    assert qs is not None

    ids = set(qs.values_list("id", flat=True))
    assert s1.id in ids and s2.id in ids
    assert s_other.id not in ids


@pytest.mark.django_db
def test_school_admin_cannot_view_other_submission_change_page(client):
    school_a = SchoolFactory(slug="dancemaker-studio")
    school_b = SchoolFactory(slug="kimberlas-classical-ballet")

    s_other = SubmissionFactory(school=school_b)

    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school_a)
    client.force_login(user)

    url = reverse("admin:core_submission_change", args=[s_other.id])
    resp = client.get(url)
    assert resp.status_code in (404, 302)


@pytest.mark.django_db
def test_superuser_sees_all_submissions_and_change_page(client):
    school_a = SchoolFactory(slug="dancemaker-studio")
    school_b = SchoolFactory(slug="kimberlas-classical-ballet")

    s1 = SubmissionFactory(school=school_a)
    s2 = SubmissionFactory(school=school_b)

    admin = UserFactory()
    admin.is_superuser = True
    admin.is_staff = True
    admin.save()
    client.force_login(admin)

    url = reverse("admin:core_submission_changelist")
    resp = client.get(url)
    assert resp.status_code == 200
    cl = resp.context.get("cl")
    ids = set(cl.queryset.values_list("id", flat=True))
    assert s1.id in ids and s2.id in ids

    # change page for other school's submission should be accessible
    change_url = reverse("admin:core_submission_change", args=[s2.id])
    resp2 = client.get(change_url)
    assert resp2.status_code == 200
