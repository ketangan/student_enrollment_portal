from importlib import import_module
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from core.models import AdminAuditLog, School, SchoolAdminMembership
from core.services.audit_environment import infer_environment
from core.services.portal_environment import current_environment, request_environment
from scheduling.models import SchedulingProfile, SchedulingMembership

pytestmark = pytest.mark.django_db


@pytest.fixture
def portals(settings):
    settings.APP_BASE_URL = "https://app.example.com"
    settings.DEMO_BASE_URL = "https://demo.example.com"
    settings.ALLOWED_HOSTS = ["app.example.com", "demo.example.com", "testserver"]
    demo = School.objects.create(slug="prospect", display_name="Prospect", is_demo=True)
    live = School.objects.create(slug="live-school", display_name="Live School", is_demo=False, plan="custom")
    user = get_user_model().objects.create_user("prospect-admin", password="admin")
    profile = SchedulingProfile.objects.create(school=demo, enabled=True)
    SchedulingMembership.objects.create(profile=profile, user=user, role="owner")
    return SimpleNamespace(demo=demo, live=live, user=user, profile=profile)


def test_host_scope_and_local_combined_mode(portals, settings):
    factory = RequestFactory()
    assert request_environment(factory.get("/", HTTP_HOST="demo.example.com")) == "demo"
    assert request_environment(factory.get("/", HTTP_HOST="app.example.com")) == "production"
    assert request_environment(factory.get("/")) is None
    settings.DEMO_BASE_URL = settings.APP_BASE_URL
    assert request_environment(factory.get("/", HTTP_HOST="app.example.com")) is None


def test_demo_login_and_workspace_do_not_grant_enrollment_or_ops_access(client, portals):
    response = client.post("/login/", {"username": portals.user.username, "password": "admin"}, HTTP_HOST="demo.example.com")
    assert response.status_code == 302 and response.url == reverse("scheduling:index")
    assert client.get(response.url, HTTP_HOST="demo.example.com").url == reverse("scheduling:workspace", args=[portals.demo.slug])
    assert client.get(reverse("scheduling:workspace", args=[portals.demo.slug]), HTTP_HOST="demo.example.com").status_code == 200
    assert client.get(reverse("school_dashboard", args=[portals.demo.slug]), HTTP_HOST="demo.example.com").status_code == 404
    assert client.get("/ops/", HTTP_HOST="demo.example.com").status_code == 302
    assert client.get(reverse("school_dashboard", args=[portals.live.slug]), HTTP_HOST="app.example.com").status_code == 404
    assert AdminAuditLog.objects.get(extra__name="login_ok").environment == "demo"
    assert current_environment.get() == "unknown"


def test_wrong_host_redirects_reads_and_rejects_writes(client, portals):
    path = reverse("scheduling:workspace", args=[portals.demo.slug])
    assert client.get(path + "?location=2", HTTP_HOST="app.example.com").url == "https://demo.example.com" + path + "?location=2"
    assert client.post(path, HTTP_HOST="app.example.com").status_code == 403
    live = reverse("school_dashboard", args=[portals.live.slug])
    assert client.get(live, HTTP_HOST="demo.example.com").url == "https://app.example.com" + live
    assert client.get("/schools/missing/admin/", HTTP_HOST="app.example.com").status_code != 200
    assert client.post("/stripe/application-fees/live-school/webhook/", HTTP_HOST="demo.example.com").status_code != 302


def test_prod_login_does_not_select_demo_workspace(client, portals):
    response = client.post("/login/", {"username": portals.user.username, "password": "admin"}, HTTP_HOST="app.example.com")
    assert response.url == "/login/"
    assert "_auth_user_id" not in client.session


def test_both_memberships_choose_current_environment(client, portals):
    SchoolAdminMembership.objects.create(user=portals.user, school=portals.demo, is_active=True)
    SchoolAdminMembership.objects.create(user=portals.user, school=portals.live, is_active=True)
    client.force_login(portals.user)
    assert client.get("/login/", HTTP_HOST="app.example.com").url == reverse("school_dashboard", args=[portals.live.slug])
    assert client.get("/login/", HTTP_HOST="demo.example.com").url == reverse("school_dashboard", args=[portals.demo.slug])


def test_background_audits_and_historical_backfill_use_business_not_actor_or_payment_mode(portals):
    evidence = [
        {"extra": {"scheduling_profile_id": portals.profile.pk}},
        {"extra": {"school_slug": portals.demo.slug}},
        {"extra": {"school": portals.demo.slug}},
        {"extra": {"school_id": portals.demo.pk, "environment": "test"}},
        {"model_label": "scheduling.schedulingprofile", "object_id": str(portals.profile.pk)},
        {"model_label": "core.school", "object_id": str(portals.demo.pk)},
        {"path": "/bookings/prospect/reservations/"},
    ]
    for values in evidence:
        log = AdminAuditLog.objects.create(action="change", **values)
        assert log.environment == "demo"
        AdminAuditLog.objects.filter(pk=log.pk).update(environment="unknown")
    live = AdminAuditLog.objects.create(action="change", model_label="core.school", object_id=str(portals.live.pk))
    assert live.environment == "production"
    AdminAuditLog.objects.filter(pk=live.pk).update(environment="unknown")
    unknown = AdminAuditLog.objects.create(action="change", model_label="retired.record", object_id="removed", actor=portals.user)
    assert unknown.environment == "unknown"
    import_module("core.migrations.0063_audit_environment").backfill(apps, SimpleNamespace(connection=SimpleNamespace(alias="default")))
    assert AdminAuditLog.objects.filter(environment="demo").count() == len(evidence)
    live.refresh_from_db()
    unknown.refresh_from_db()
    assert live.environment == "production" and unknown.environment == "unknown"
    portals.demo.is_demo = False
    portals.demo.save()
    assert AdminAuditLog.objects.filter(environment="demo").count() == len(evidence)


@pytest.mark.parametrize("extra,label,object_id", [({}, "", ""), ({"school_id": "bad"}, "", ""),
    ({"school_slug": []}, "", ""), ({}, "auth.user", "invalid"), ({}, "core.school", "invalid"),
    ({}, "notamodel", "12"), ({"scheduling_profile_id": -1}, "", "")])
def test_unknown_evidence_is_not_invented(portals, extra, label, object_id):
    log = AdminAuditLog(extra=extra, model_label=label, object_id=object_id)
    assert infer_environment(log) == "unknown"


def test_audit_list_choices_dashboard_and_django_admin_are_environment_scoped(client, portals):
    operator = get_user_model().objects.create_superuser("operator", "", "password")
    client.force_login(operator)
    AdminAuditLog.objects.create(action="add", actor=portals.user, object_repr="DEMO ONLY", extra={"school_id": portals.demo.pk})
    AdminAuditLog.objects.create(action="add", actor=operator, object_repr="LIVE ONLY", extra={"school_id": portals.live.pk})
    AdminAuditLog.objects.create(action="add", object_repr="UNCLASSIFIED")
    for host, present, absent in [("app.example.com", "LIVE ONLY", "DEMO ONLY"), ("demo.example.com", "DEMO ONLY", "LIVE ONLY")]:
        for path in ["/ops/audit/", "/ops/", reverse("admin:core_adminauditlog_changelist")]:
            response = client.get(path, HTTP_HOST=host)
            assert response.status_code == 200
            if path == reverse("admin:core_adminauditlog_changelist"):
                assert list(response.context["cl"].queryset.values_list("object_repr", flat=True)) == [present]
            else:
                assert present in response.content.decode() and absent not in response.content.decode()
            assert "UNCLASSIFIED" not in response.content.decode()
    response = client.get("/ops/audit/", HTTP_HOST="app.example.com")
    assert list(response.context["actor_choices"]) == [operator.username]
    assert client.get("/ops/audit/").context["total_count"] == 3


def test_shared_account_menu_and_booking_password_change(client, portals):
    client.force_login(portals.user)
    path = reverse("scheduling:workspace", args=[portals.demo.slug])
    response = client.get(path, HTTP_HOST="demo.example.com")
    assert "partials/account_menu.html" in [t.name for t in response.templates]
    assert b"Change Password" in response.content and b"data-dash-theme" in response.content
    assert b"Ops Portal" not in response.content and b"Django Admin" not in response.content
    password_path = reverse("scheduling:password_change", args=[portals.demo.slug])
    assert client.get(password_path, HTTP_HOST="demo.example.com").status_code == 200
    assert client.post(password_path, {"old_password": "wrong"}, HTTP_HOST="demo.example.com").context["form"].errors
    response = client.post(password_path, {"old_password": "admin", "new_password1": "New-demo-86245!", "new_password2": "New-demo-86245!"}, HTTP_HOST="demo.example.com")
    assert response.url == path
    portals.user.refresh_from_db()
    assert portals.user.check_password("New-demo-86245!") and "_auth_user_id" in client.session
    operator = get_user_model().objects.create_superuser("operator", "", "password")
    client.force_login(operator)
    for path, host in [(path, "demo.example.com"), (reverse("school_dashboard", args=[portals.live.slug]), "app.example.com")]:
        response = client.get(path, HTTP_HOST=host)
        assert "partials/account_menu.html" in [t.name for t in response.templates]
        assert b"Ops Portal" in response.content and b"Django Admin" in response.content
