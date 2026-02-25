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
    resp = client.get(url, follow=True)
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
    resp = client.get(url, follow=True)
    assert resp.status_code in (403, 404, 302)


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
    resp = client.get(url, follow=True)
    assert resp.status_code == 200
    cl = resp.context.get("cl")
    ids = set(cl.queryset.values_list("id", flat=True))
    assert s1.id in ids and s2.id in ids

    # change page for other school's submission should be accessible
    change_url = reverse("admin:core_submission_change", args=[s2.id])
    resp2 = client.get(change_url)
    assert resp2.status_code in (200, 301)


# ---------------------------------------------------------------------------
# Inactive school enforcement tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_inactive_school_admin_blocked_from_admin(client):
    """School admin with inactive school should be blocked from /admin/."""
    school = SchoolFactory(slug="inactive-school", plan="starter", is_active=False)
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)

    client.force_login(user)
    url = reverse("admin:index")
    resp = client.get(url, follow=False)
    # Should be denied access (403 or redirect)
    assert resp.status_code in (302, 403)


@pytest.mark.django_db
def test_inactive_school_admin_can_access_billing(client):
    """School admin with inactive school should still access /admin/billing/."""
    school = SchoolFactory(slug="inactive-school-billing", plan="starter", is_active=False)
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)

    client.force_login(user)
    url = reverse("admin:billing")
    resp = client.get(url, follow=False)
    # Billing should be accessible even when school is inactive
    assert resp.status_code == 200


@pytest.mark.django_db
def test_superuser_can_access_admin_regardless_of_inactive_school(client):
    """Superusers should always have admin access even with inactive schools."""
    school = SchoolFactory(slug="inactive-school-su", plan="starter", is_active=False)
    su = UserFactory()
    su.is_superuser = True
    su.is_staff = True
    su.save()

    client.force_login(su)

    # Admin index should be accessible
    url_index = reverse("admin:index")
    resp_index = client.get(url_index, follow=False)
    assert resp_index.status_code == 200

    # Billing should also be accessible
    url_billing = reverse("admin:billing")
    resp_billing = client.get(url_billing, follow=False)
    assert resp_billing.status_code == 200


@pytest.mark.django_db
def test_inactive_school_admin_blocked_from_submission_changelist(client):
    """School admin with inactive school should be blocked from submission changelist."""
    school = SchoolFactory(slug="inactive-school-sub", plan="starter", is_active=False)
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)
    SubmissionFactory(school=school)

    client.force_login(user)
    url = reverse("admin:core_submission_changelist")
    resp = client.get(url, follow=False)
    # Should be denied access
    assert resp.status_code in (302, 403)


@pytest.mark.django_db
def test_inactive_school_admin_blocked_from_file_download(client):
    """School admin with inactive school should be blocked from downloading submission files."""
    from core.models import SubmissionFile
    from django.core.files.uploadedfile import SimpleUploadedFile

    school = SchoolFactory(slug="inactive-school-file", plan="starter", is_active=False)
    user = UserFactory()
    SchoolAdminMembershipFactory(user=user, school=school)

    submission = SubmissionFactory(school=school)
    # Create a submission file
    uploaded = SimpleUploadedFile("test.txt", b"test content", content_type="text/plain")
    sf = SubmissionFile.objects.create(
        submission=submission,
        field_key="test_file",
        file=uploaded,
        original_name="test.txt",
        content_type="text/plain",
        size_bytes=12,
    )

    client.force_login(user)
    url = reverse("admin_download_submission_file", args=[sf.id])
    resp = client.get(url, follow=False)
    # Should be denied access
    assert resp.status_code == 404
