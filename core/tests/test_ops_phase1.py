"""
Tests for the /ops/ superadmin portal — Phase 1.
Covers auth guard, dashboard, schools CRUD, memberships, users CRUD, login/logout redirects.
"""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School, SchoolAdminMembership


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        username="ops_super", email="super@test.com", password="testpass123"
    )


@pytest.fixture
def school_admin_user(db):
    return User.objects.create_user(
        username="schooladmin", email="admin@school.com", password="testpass123",
        is_staff=True,
    )


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        username="regular", email="regular@test.com", password="testpass123"
    )


@pytest.fixture
def school(db):
    return School.objects.create(
        slug="test-school", display_name="Test School", plan="trial",
        trial_started_at=timezone.now(),
    )


@pytest.fixture
def school_with_membership(db, school, school_admin_user):
    SchoolAdminMembership.objects.create(user=school_admin_user, school=school)
    return school


# ── Auth guard ────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_dashboard_requires_superuser_anonymous(client):
    resp = client.get(reverse("ops_dashboard"))
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_ops_dashboard_blocks_regular_user(client, regular_user):
    client.force_login(regular_user)
    with pytest.raises(PermissionError):
        client.get(reverse("ops_dashboard"))


@pytest.mark.django_db
def test_ops_dashboard_accessible_by_superuser(client, superuser):
    client.force_login(superuser)
    resp = client.get(reverse("ops_dashboard"))
    assert resp.status_code == 200


# ── Dashboard ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_dashboard_shows_school_counts(client, superuser, school):
    client.force_login(superuser)
    resp = client.get(reverse("ops_dashboard"))
    assert resp.status_code == 200
    assert resp.context["total_schools"] == 1
    assert resp.context["active_schools"] == 1


@pytest.mark.django_db
def test_ops_dashboard_expiring_trial_appears(client, superuser):
    client.force_login(superuser)
    s = School.objects.create(
        slug="expiring", display_name="Expiring", plan="trial",
        trial_started_at=timezone.now() - timezone.timedelta(days=27),
    )
    resp = client.get(reverse("ops_dashboard"))
    slugs = [sc.slug for sc in resp.context["expiring_soon"]]
    assert "expiring" in slugs


@pytest.mark.django_db
def test_ops_dashboard_expired_trial_appears(client, superuser):
    client.force_login(superuser)
    School.objects.create(
        slug="expired-trial", display_name="Expired", plan="trial",
        trial_started_at=timezone.now() - timezone.timedelta(days=45),
    )
    resp = client.get(reverse("ops_dashboard"))
    slugs = [sc.slug for sc in resp.context["expired_trials"]]
    assert "expired-trial" in slugs


# ── Schools list ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_schools_list(client, superuser, school):
    client.force_login(superuser)
    resp = client.get(reverse("ops_schools_list"))
    assert resp.status_code == 200
    assert any(s.slug == "test-school" for s in resp.context["schools"])


@pytest.mark.django_db
def test_ops_schools_list_search(client, superuser, school):
    client.force_login(superuser)
    resp = client.get(reverse("ops_schools_list") + "?q=test-school")
    assert resp.status_code == 200
    assert any(s.slug == "test-school" for s in resp.context["schools"])


@pytest.mark.django_db
def test_ops_schools_list_plan_filter(client, superuser, school):
    client.force_login(superuser)
    School.objects.create(slug="starter-school", plan="starter")
    resp = client.get(reverse("ops_schools_list") + "?plan=trial")
    slugs = [s.slug for s in resp.context["schools"]]
    assert "test-school" in slugs
    assert "starter-school" not in slugs


# ── School create ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_school_create(client, superuser):
    client.force_login(superuser)
    resp = client.post(reverse("ops_school_create"), {
        "slug": "new-school",
        "display_name": "New School",
        "plan": "trial",
        "is_active": True,
        "feature_flags": "{}",
    })
    assert School.objects.filter(slug="new-school").exists()
    assert resp.status_code == 302
    assert AdminAuditLog.objects.filter(model_label="core.school", action="add").exists()


@pytest.mark.django_db
def test_ops_school_create_duplicate_slug(client, superuser, school):
    client.force_login(superuser)
    resp = client.post(reverse("ops_school_create"), {
        "slug": "test-school",
        "display_name": "Dup",
        "plan": "trial",
        "is_active": True,
        "feature_flags": "{}",
    })
    assert resp.status_code == 200
    assert b"already exists" in resp.content


# ── School edit ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_school_detail_get(client, superuser, school):
    client.force_login(superuser)
    resp = client.get(reverse("ops_school_detail", kwargs={"slug": school.slug}))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_ops_school_edit_saves_and_logs(client, superuser, school):
    client.force_login(superuser)
    resp = client.post(reverse("ops_school_detail", kwargs={"slug": school.slug}), {
        "display_name": "Updated Name",
        "plan": "starter",
        "is_active": True,
        "feature_flags": "{}",
    })
    school.refresh_from_db()
    assert school.display_name == "Updated Name"
    assert school.plan == "starter"
    assert AdminAuditLog.objects.filter(model_label="core.school", action="change").exists()


# ── Memberships ───────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_add_member(client, superuser, school, regular_user):
    client.force_login(superuser)
    resp = client.post(
        reverse("ops_school_member_add", kwargs={"slug": school.slug}),
        {"email": regular_user.email},
    )
    assert resp.status_code == 302
    assert SchoolAdminMembership.objects.filter(user=regular_user, school=school).exists()
    assert AdminAuditLog.objects.filter(model_label="core.schooladminmembership", action="add").exists()


@pytest.mark.django_db
def test_ops_add_member_nonexistent_email(client, superuser, school):
    client.force_login(superuser)
    resp = client.post(
        reverse("ops_school_member_add", kwargs={"slug": school.slug}),
        {"email": "ghost@nowhere.com"},
    )
    assert resp.status_code == 302
    assert not SchoolAdminMembership.objects.filter(school=school).exists()


@pytest.mark.django_db
def test_ops_remove_member(client, superuser, school_with_membership, school_admin_user):
    client.force_login(superuser)
    resp = client.post(
        reverse("ops_school_member_remove",
                kwargs={"slug": school_with_membership.slug, "user_id": school_admin_user.pk}),
    )
    assert resp.status_code == 302
    assert not SchoolAdminMembership.objects.filter(user=school_admin_user).exists()


# ── Users list + create ───────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_users_list(client, superuser):
    client.force_login(superuser)
    resp = client.get(reverse("ops_users_list"))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_ops_user_create(client, superuser):
    client.force_login(superuser)
    resp = client.post(reverse("ops_user_create"), {
        "username": "brandnew",
        "email": "brandnew@test.com",
        "first_name": "",
        "last_name": "",
        "password": "securepass99",
        "password_confirm": "securepass99",
        "is_staff": False,
        "is_superuser": False,
    })
    assert User.objects.filter(username="brandnew").exists()
    assert AdminAuditLog.objects.filter(model_label="auth.user", action="add").exists()


@pytest.mark.django_db
def test_ops_user_create_with_school(client, superuser, school):
    client.force_login(superuser)
    client.post(reverse("ops_user_create"), {
        "username": "schoolstaff",
        "email": "staff@school.com",
        "first_name": "",
        "last_name": "",
        "password": "securepass99",
        "password_confirm": "securepass99",
        "is_staff": True,
        "is_superuser": False,
        "school": school.pk,
    })
    user = User.objects.get(username="schoolstaff")
    assert SchoolAdminMembership.objects.filter(user=user, school=school).exists()


@pytest.mark.django_db
def test_ops_user_create_password_mismatch(client, superuser):
    client.force_login(superuser)
    resp = client.post(reverse("ops_user_create"), {
        "username": "mismatch",
        "email": "mismatch@test.com",
        "password": "pass1234",
        "password_confirm": "different",
        "is_staff": False,
        "is_superuser": False,
    })
    assert resp.status_code == 200
    assert not User.objects.filter(username="mismatch").exists()


@pytest.mark.django_db
def test_ops_user_detail_edit(client, superuser, regular_user):
    client.force_login(superuser)
    resp = client.post(reverse("ops_user_detail", kwargs={"user_id": regular_user.pk}), {
        "username": regular_user.username,
        "email": "updated@test.com",
        "first_name": "Updated",
        "last_name": "",
        "is_active": True,
        "is_staff": False,
        "is_superuser": False,
    })
    regular_user.refresh_from_db()
    assert regular_user.email == "updated@test.com"
    assert AdminAuditLog.objects.filter(model_label="auth.user", action="change").exists()


@pytest.mark.django_db
def test_ops_user_deactivate(client, superuser, regular_user):
    client.force_login(superuser)
    client.post(reverse("ops_user_toggle_active", kwargs={"user_id": regular_user.pk}))
    regular_user.refresh_from_db()
    assert not regular_user.is_active


@pytest.mark.django_db
def test_ops_user_cannot_deactivate_self(client, superuser):
    client.force_login(superuser)
    resp = client.post(reverse("ops_user_toggle_active", kwargs={"user_id": superuser.pk}))
    superuser.refresh_from_db()
    assert superuser.is_active  # unchanged


# ── Login / logout redirects ──────────────────────────────────────────────────

@pytest.mark.django_db
def test_login_redirects_superuser_to_ops(client, superuser):
    resp = client.post(reverse("login"), {
        "username": "ops_super", "password": "testpass123"
    })
    assert resp.status_code == 302
    assert resp["Location"] == reverse("ops_dashboard")


@pytest.mark.django_db
def test_login_redirects_school_admin_to_dashboard(client, school_admin_user, school_with_membership):
    resp = client.post(reverse("login"), {
        "username": "schooladmin", "password": "testpass123"
    })
    assert resp.status_code == 302
    assert f"/schools/{school_with_membership.slug}/admin/" in resp["Location"]


@pytest.mark.django_db
def test_login_invalid_credentials(client, db):
    resp = client.post(reverse("login"), {"username": "nobody", "password": "wrong"})
    assert resp.status_code == 200
    assert b"Invalid username" in resp.content


@pytest.mark.django_db
def test_logout_redirects_to_login(client, superuser):
    client.force_login(superuser)
    resp = client.post(reverse("logout"))
    assert resp.status_code == 302
    assert resp["Location"] == reverse("login")
