"""
Tests for Phase: Customer Onboarding (Convert Demo, Checklist, Welcome Email).
"""
import pytest
from datetime import timedelta
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import (
    AdminAuditLog,
    DemoAccessToken,
    DemoArchive,
    Lead,
    OnboardingChecklistItem,
    School,
    SchoolAdminMembership,
    Submission,
)
from core.services.onboarding import (
    archive_demo_data,
    convert_demo_to_customer,
    get_or_create_checklist,
    mark_checklist_item,
    unmark_checklist_item,
    qr_base64,
)


@pytest.fixture
def ops_user(db):
    return User.objects.create_superuser(username="ops", email="ops@test.com", password="pass")


@pytest.fixture
def demo_school(db):
    school = School.objects.create(
        slug="test-demo",
        display_name="Test Demo School",
        plan="trial",
        is_active=True,
        is_demo=True,
    )
    return school


@pytest.fixture
def demo_admin(db, demo_school):
    user = User.objects.create_user(username="demo_admin", email="demo@school.com", is_staff=True)
    SchoolAdminMembership.objects.create(user=user, school=demo_school)
    return user


@pytest.fixture
def demo_token(db, demo_school, ops_user):
    return DemoAccessToken.objects.create(
        school=demo_school,
        expires_at=timezone.now() + timedelta(days=14),
        created_by=ops_user,
        purpose=DemoAccessToken.PURPOSE_DEMO,
    )


# ── School.is_demo field ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_school_is_demo_default_false():
    school = School.objects.create(slug="non-demo", plan="starter", is_active=True)
    assert not school.is_demo


@pytest.mark.django_db
def test_demo_school_is_demo_true(demo_school):
    assert demo_school.is_demo


# ── DemoAccessToken.purpose ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_token_purpose_defaults_to_demo(demo_school, ops_user):
    token = DemoAccessToken.objects.create(
        school=demo_school,
        expires_at=timezone.now() + timedelta(days=7),
        created_by=ops_user,
    )
    assert token.purpose == DemoAccessToken.PURPOSE_DEMO


@pytest.mark.django_db
def test_onboarding_token_purpose(demo_school, ops_user):
    token = DemoAccessToken.objects.create(
        school=demo_school,
        expires_at=timezone.now() + timedelta(days=7),
        created_by=ops_user,
        purpose=DemoAccessToken.PURPOSE_ONBOARDING,
    )
    assert token.purpose == DemoAccessToken.PURPOSE_ONBOARDING


# ── archive_demo_data ─────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_archive_creates_demo_archive(demo_school, demo_admin, ops_user):
    Submission.objects.create(school=demo_school, form_key="default", data={"name": "Test"}, status="New")
    Lead.objects.create(school=demo_school, name="Lead A", email="a@b.com", source="website")

    archive = archive_demo_data(demo_school, ops_user)

    assert archive.school == demo_school
    assert len(archive.submissions_json) == 1
    assert len(archive.leads_json) == 1


@pytest.mark.django_db
def test_archive_idempotent(demo_school, ops_user):
    archive1 = archive_demo_data(demo_school, ops_user)
    archive2 = archive_demo_data(demo_school, ops_user)
    assert archive1.pk == archive2.pk  # update_or_create — same record


# ── convert_demo_to_customer ──────────────────────────────────────────────────

@pytest.mark.django_db
def test_convert_sets_is_demo_false(demo_school, demo_admin, ops_user):
    result = convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="real@customer.com",
        admin_first_name="Real",
        admin_last_name="Admin",
        delete_submissions=False,
        delete_leads=False,
        actor=ops_user,
    )
    demo_school.refresh_from_db()
    assert not demo_school.is_demo
    assert demo_school.plan == "starter"
    assert demo_school.is_active


@pytest.mark.django_db
def test_convert_creates_real_admin(demo_school, demo_admin, ops_user):
    result = convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="newadmin@customer.com",
        admin_first_name="New",
        admin_last_name="Admin",
        delete_submissions=False,
        delete_leads=False,
        actor=ops_user,
    )
    assert result["user_created"]
    assert result["user"].email == "newadmin@customer.com"
    assert SchoolAdminMembership.objects.filter(school=demo_school, user=result["user"]).exists()


@pytest.mark.django_db
def test_convert_reuses_existing_user(demo_school, demo_admin, ops_user):
    existing = User.objects.create_user(username="existing", email="existing@customer.com", is_active=True)
    result = convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="existing@customer.com",
        admin_first_name="",
        admin_last_name="",
        delete_submissions=False,
        delete_leads=False,
        actor=ops_user,
    )
    assert not result["user_created"]
    assert result["user"].pk == existing.pk


@pytest.mark.django_db
def test_convert_removes_demo_admin_membership(demo_school, demo_admin, ops_user):
    assert SchoolAdminMembership.objects.filter(school=demo_school).exists()
    convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="real@customer.com",
        admin_first_name="",
        admin_last_name="",
        delete_submissions=False,
        delete_leads=False,
        actor=ops_user,
    )
    # Old demo admin no longer has membership
    assert not SchoolAdminMembership.objects.filter(user=demo_admin, school=demo_school).exists()
    # But old user account still exists
    assert User.objects.filter(pk=demo_admin.pk).exists()


@pytest.mark.django_db
def test_convert_expires_demo_tokens(demo_school, demo_admin, ops_user, demo_token):
    convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="real@customer.com",
        admin_first_name="",
        admin_last_name="",
        delete_submissions=False,
        delete_leads=False,
        actor=ops_user,
    )
    demo_token.refresh_from_db()
    assert demo_token.is_expired


@pytest.mark.django_db
def test_convert_creates_onboarding_token(demo_school, demo_admin, ops_user):
    result = convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="real@customer.com",
        admin_first_name="",
        admin_last_name="",
        delete_submissions=False,
        delete_leads=False,
        actor=ops_user,
    )
    token = result["magic_token"]
    assert token.purpose == DemoAccessToken.PURPOSE_ONBOARDING
    assert not token.is_expired


@pytest.mark.django_db
def test_convert_deletes_submissions_when_requested(demo_school, demo_admin, ops_user):
    Submission.objects.create(school=demo_school, form_key="default", data={}, status="New")
    Submission.objects.create(school=demo_school, form_key="default", data={}, status="New")
    result = convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="real@customer.com",
        admin_first_name="",
        admin_last_name="",
        delete_submissions=True,
        delete_leads=False,
        actor=ops_user,
    )
    assert result["deleted_submissions"] == 2
    assert Submission.objects.filter(school=demo_school).count() == 0


@pytest.mark.django_db
def test_convert_preserves_submissions_when_not_requested(demo_school, demo_admin, ops_user):
    Submission.objects.create(school=demo_school, form_key="default", data={}, status="New")
    result = convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="real@customer.com",
        admin_first_name="",
        admin_last_name="",
        delete_submissions=False,
        delete_leads=False,
        actor=ops_user,
    )
    assert result["deleted_submissions"] == 0
    assert Submission.objects.filter(school=demo_school).count() == 1


@pytest.mark.django_db
def test_convert_trial_plan_sets_end_date(demo_school, demo_admin, ops_user):
    result = convert_demo_to_customer(
        school=demo_school,
        plan="trial",
        trial_days=30,
        admin_email="real@customer.com",
        admin_first_name="",
        admin_last_name="",
        delete_submissions=False,
        delete_leads=False,
        actor=ops_user,
    )
    demo_school.refresh_from_db()
    assert demo_school.trial_end_date is not None


@pytest.mark.django_db
def test_convert_archives_before_deletion(demo_school, demo_admin, ops_user):
    Submission.objects.create(school=demo_school, form_key="default", data={"x": 1}, status="New")
    convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="real@customer.com",
        admin_first_name="",
        admin_last_name="",
        delete_submissions=True,
        delete_leads=True,
        actor=ops_user,
    )
    archive = DemoArchive.objects.get(school=demo_school)
    assert len(archive.submissions_json) == 1  # archived before deletion


@pytest.mark.django_db
def test_convert_audit_log(demo_school, demo_admin, ops_user):
    convert_demo_to_customer(
        school=demo_school,
        plan="starter",
        trial_days=None,
        admin_email="real@customer.com",
        admin_first_name="",
        admin_last_name="",
        delete_submissions=False,
        delete_leads=False,
        actor=ops_user,
    )
    log = AdminAuditLog.objects.filter(
        model_label="core.school",
        extra__name="demo_converted_to_customer",
    ).first()
    assert log is not None
    assert log.extra["school_slug"] == demo_school.slug


# ── get_or_create_checklist ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_checklist_creates_all_items(demo_school):
    items = get_or_create_checklist(demo_school)
    assert len(items) == len(OnboardingChecklistItem.ITEMS)


@pytest.mark.django_db
def test_checklist_idempotent(demo_school):
    items1 = get_or_create_checklist(demo_school)
    items2 = get_or_create_checklist(demo_school)
    assert len(items1) == len(items2)
    assert OnboardingChecklistItem.objects.filter(school=demo_school).count() == len(OnboardingChecklistItem.ITEMS)


@pytest.mark.django_db
def test_checklist_order_matches_definition(demo_school):
    items = get_or_create_checklist(demo_school)
    keys = [i.item for i in items]
    expected = [k for k, _ in OnboardingChecklistItem.ITEMS]
    assert keys == expected


# ── mark / unmark ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_mark_checklist_item(demo_school, ops_user):
    get_or_create_checklist(demo_school)
    mark_checklist_item(demo_school, "school_created", ops_user)
    item = OnboardingChecklistItem.objects.get(school=demo_school, item="school_created")
    assert item.completed_at is not None
    assert item.completed_by == ops_user


@pytest.mark.django_db
def test_mark_checklist_idempotent(demo_school, ops_user):
    get_or_create_checklist(demo_school)
    mark_checklist_item(demo_school, "school_created", ops_user)
    first_ts = OnboardingChecklistItem.objects.get(school=demo_school, item="school_created").completed_at
    mark_checklist_item(demo_school, "school_created", ops_user)
    second_ts = OnboardingChecklistItem.objects.get(school=demo_school, item="school_created").completed_at
    assert first_ts == second_ts  # not updated on second call


@pytest.mark.django_db
def test_unmark_checklist_item(demo_school, ops_user):
    get_or_create_checklist(demo_school)
    mark_checklist_item(demo_school, "school_created", ops_user)
    unmark_checklist_item(demo_school, "school_created", ops_user)
    item = OnboardingChecklistItem.objects.get(school=demo_school, item="school_created")
    assert item.completed_at is None
    assert item.completed_by is None


# ── Ops views ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_convert_view_get(client, ops_user, demo_school, demo_admin):
    client.force_login(ops_user)
    resp = client.get(f"/ops/schools/{demo_school.slug}/convert/")
    assert resp.status_code == 200
    assert b"Convert to Customer" in resp.content


@pytest.mark.django_db
def test_convert_view_requires_ops(client, demo_school):
    regular = User.objects.create_user(username="reg", password="pass")
    client.force_login(regular)
    try:
        resp = client.get(f"/ops/schools/{demo_school.slug}/convert/")
        # Should either 403 or redirect to login
        assert resp.status_code in (302, 403)
    except PermissionError:
        pass  # view raises PermissionError for non-superusers — that's fine


@pytest.mark.django_db
def test_convert_view_post_success(client, ops_user, demo_school, demo_admin):
    client.force_login(ops_user)
    resp = client.post(f"/ops/schools/{demo_school.slug}/convert/", {
        "admin_email": "real@customer.com",
        "admin_first_name": "Real",
        "admin_last_name": "Customer",
        "plan": "starter",
        "delete_submissions": "1",
        "delete_leads": "1",
    })
    assert resp.status_code == 200
    assert b"converted successfully" in resp.content
    demo_school.refresh_from_db()
    assert not demo_school.is_demo


@pytest.mark.django_db
def test_convert_view_validation_error(client, ops_user, demo_school, demo_admin):
    client.force_login(ops_user)
    resp = client.post(f"/ops/schools/{demo_school.slug}/convert/", {
        "admin_email": "not-an-email",
        "plan": "starter",
    })
    assert resp.status_code == 200
    assert b"Valid admin email is required" in resp.content


@pytest.mark.django_db
def test_checklist_toggle_view_marks_complete(client, ops_user, demo_school):
    client.force_login(ops_user)
    get_or_create_checklist(demo_school)
    resp = client.post(f"/ops/schools/{demo_school.slug}/checklist/school_created/toggle/")
    assert resp.status_code == 302
    item = OnboardingChecklistItem.objects.get(school=demo_school, item="school_created")
    assert item.completed_at is not None


@pytest.mark.django_db
def test_checklist_toggle_view_unmarks(client, ops_user, demo_school):
    client.force_login(ops_user)
    get_or_create_checklist(demo_school)
    mark_checklist_item(demo_school, "school_created", ops_user)
    resp = client.post(f"/ops/schools/{demo_school.slug}/checklist/school_created/toggle/")
    assert resp.status_code == 302
    item = OnboardingChecklistItem.objects.get(school=demo_school, item="school_created")
    assert item.completed_at is None


@pytest.mark.django_db
def test_school_detail_shows_convert_button_for_demo(client, ops_user, demo_school):
    client.force_login(ops_user)
    resp = client.get(f"/ops/schools/{demo_school.slug}/")
    assert resp.status_code == 200
    assert b"Convert to Customer" in resp.content


@pytest.mark.django_db
def test_school_detail_no_convert_button_for_non_demo(client, ops_user):
    school = School.objects.create(slug="real-school", plan="starter", is_active=True, is_demo=False)
    client.force_login(ops_user)
    resp = client.get(f"/ops/schools/{school.slug}/")
    assert resp.status_code == 200
    assert b"Convert to Customer" not in resp.content


@pytest.mark.django_db
def test_school_detail_shows_checklist_for_non_demo(client, ops_user):
    school = School.objects.create(slug="real-school2", plan="starter", is_active=True, is_demo=False)
    client.force_login(ops_user)
    resp = client.get(f"/ops/schools/{school.slug}/")
    assert resp.status_code == 200
    assert b"Onboarding Checklist" in resp.content


# ── demo_access_view with converted school ────────────────────────────────────

@pytest.mark.django_db
def test_demo_link_redirects_to_apply_after_conversion(client, demo_school, demo_admin, demo_token):
    # Convert the school
    demo_school.is_demo = False
    demo_school.save(update_fields=["is_demo"])

    # Old demo token for converted school → redirect to enrollment form
    resp = client.get(f"/demo-access/{demo_token.token}/")
    assert resp.status_code == 302
    assert "/apply/" in resp["Location"] or "enroll" in resp["Location"].lower()


@pytest.mark.django_db
def test_onboarding_token_logs_in_regardless_of_is_demo(client, demo_school, demo_admin, ops_user):
    # Convert the school
    demo_school.is_demo = False
    demo_school.save(update_fields=["is_demo"])

    onboarding_token = DemoAccessToken.objects.create(
        school=demo_school,
        expires_at=timezone.now() + timedelta(days=7),
        created_by=ops_user,
        purpose=DemoAccessToken.PURPOSE_ONBOARDING,
    )
    resp = client.get(f"/demo-access/{onboarding_token.token}/")
    assert resp.status_code == 302
    # Should redirect to dashboard (successful login), not to apply page
    assert "apply" not in resp["Location"]


# ── qr_base64 ─────────────────────────────────────────────────────────────────

def test_qr_base64_returns_nonempty_string():
    result = qr_base64("https://app.enrollifyapp.com/apply/test-school/")
    assert len(result) > 100  # base64 PNG will be much longer
    # Verify it's valid base64
    import base64
    decoded = base64.b64decode(result)
    assert decoded[:4] == b"\x89PNG"  # PNG magic bytes
