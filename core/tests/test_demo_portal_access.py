from io import StringIO
from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.contrib.admin.models import LogEntry, CHANGE
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, DemoAccessToken, Lead, School, SchoolAdminMembership, Submission, OpsIncident
from core.services.demo_access import generate_demo_link, resolve_recipient
from scheduling.models import SchedulingMembership, SchedulingProfile

pytestmark = pytest.mark.django_db


@pytest.fixture
def portals(settings):
    settings.APP_BASE_URL = "https://app.example.com"
    settings.DEMO_BASE_URL = "https://demo.example.com"
    settings.ALLOWED_HOSTS = ["app.example.com", "demo.example.com", "testserver"]
    demo = School.objects.create(slug="prospect", display_name="Demo Company", is_demo=True)
    live = School.objects.create(slug="live-school", display_name="Live Company", plan="custom")
    owner = get_user_model().objects.create_user("demo-owner", password="admin")
    operator = get_user_model().objects.create_superuser("operator", "", "operator-password")
    profile = SchedulingProfile.objects.create(school=demo, enabled=True)
    membership = SchedulingMembership.objects.create(profile=profile, user=owner, role="owner")
    return SimpleNamespace(demo=demo, live=live, owner=owner, operator=operator, profile=profile, membership=membership)


def test_operator_creates_scheduling_magic_link_without_enrollment_access(client, portals):
    client.force_login(portals.operator)
    response = client.post(reverse("ops_demo_token_generate", args=[portals.demo.slug]), HTTP_HOST="demo.example.com")
    assert response.status_code == 302
    token = DemoAccessToken.objects.get(school=portals.demo)
    assert token.recipient == portals.owner and token.destination == "scheduling"
    detail = client.get(response.url, HTTP_HOST="demo.example.com")
    link = "https://demo.example.com" + reverse("demo_access", args=[token.token])
    assert detail.context["demo_link"] == link and link.encode() in detail.content
    assert detail.context["demo_destinations"] == [("scheduling", "Scheduling")]
    client.logout()
    response = client.get(link)
    assert response.url == reverse("scheduling:workspace", args=[portals.demo.slug])
    assert client.session["_auth_user_id"] == str(portals.owner.pk)
    assert response.cookies["sessionid"]["domain"] == ""
    assert "no-store" in response.headers["Cache-Control"]
    assert client.get(response.url, HTTP_HOST="demo.example.com").status_code == 200
    assert client.get(reverse("school_dashboard", args=[portals.demo.slug]), HTTP_HOST="demo.example.com").status_code == 404
    assert client.get("/ops/", HTTP_HOST="demo.example.com").status_code == 302
    token.refresh_from_db()
    assert token.last_used_at and client.session["demo_token_id"] == token.pk


def test_wrong_host_magic_link_never_authenticates_on_production(client, portals):
    token = generate_demo_link(portals.demo, portals.operator)
    path = reverse("demo_access", args=[token.token])
    response = client.get(path, HTTP_HOST="app.example.com")
    assert response.url == "https://demo.example.com" + path
    assert "_auth_user_id" not in client.session
    assert client.post(path, HTTP_HOST="demo.example.com").status_code == 405
    preview = "/demo/plushumi/integrations/"
    assert client.get(preview, HTTP_HOST="app.example.com").url == "https://demo.example.com" + preview
    assert client.post(preview, HTTP_HOST="app.example.com").status_code == 403


@pytest.mark.parametrize("change", ["expired", "inactive_user", "inactive_membership", "disabled_profile", "inactive_school", "superuser", "cross_school", "recipient_removed"])
def test_magic_link_fails_closed_when_access_is_removed(client, portals, change):
    token = generate_demo_link(portals.demo, portals.operator)
    if change == "expired":
        token.expires_at = timezone.now()
        token.save()
    elif change in {"inactive_user", "superuser"}:
        setattr(portals.owner, "is_active" if change == "inactive_user" else "is_superuser", change == "superuser")
        portals.owner.save()
    elif change == "inactive_membership":
        portals.membership.is_active = False
        portals.membership.save()
    elif change == "disabled_profile":
        portals.profile.enabled = False
        portals.profile.save()
    elif change == "inactive_school":
        portals.demo.is_active = False
        portals.demo.save()
    elif change == "cross_school":
        SchoolAdminMembership.objects.create(school=portals.live, user=portals.owner)
    else:
        portals.membership.delete()
        another = get_user_model().objects.create_user("replacement")
        SchedulingMembership.objects.create(profile=portals.profile, user=another, role="owner")
    response = client.get(reverse("demo_access", args=[token.token]), HTTP_HOST="demo.example.com")
    assert response.status_code == 200 and "_auth_user_id" not in client.session
    token.refresh_from_db()
    assert token.last_used_at is None


def test_replacement_revokes_old_demo_link_and_keeps_onboarding_token(portals):
    original = generate_demo_link(portals.demo, portals.operator)
    onboarding = DemoAccessToken.objects.create(school=portals.demo, purpose="onboarding", expires_at=timezone.now()+timezone.timedelta(days=1))
    replacement = generate_demo_link(portals.demo, portals.operator)
    original.refresh_from_db()
    onboarding.refresh_from_db()
    assert original.is_expired and not replacement.is_expired and not onboarding.is_expired


def test_generator_rejects_live_invalid_and_privileged_accounts(client, portals):
    client.force_login(portals.operator)
    for school, destination, host in [(portals.live, None, "app.example.com"), (portals.demo, "bad", "demo.example.com")]:
        response = client.post(reverse("ops_demo_token_generate", args=[school.slug]), {"destination": destination or ""}, HTTP_HOST=host)
        assert response.status_code == 302
        assert not DemoAccessToken.objects.exists()
    portals.owner.is_superuser = True
    portals.owner.save()
    with pytest.raises(ValueError):
        generate_demo_link(portals.demo, portals.operator)


def test_legacy_enrollment_links_and_explicit_destination_still_work(client, portals):
    SchoolAdminMembership.objects.create(school=portals.demo, user=portals.owner)
    legacy = DemoAccessToken.objects.create(school=portals.demo, expires_at=timezone.now()+timezone.timedelta(days=1))
    assert resolve_recipient(legacy) == portals.owner
    token = generate_demo_link(portals.demo, portals.operator)
    assert token.destination == "enrollment" and token.recipient == portals.owner
    assert client.get(reverse("demo_access", args=[token.token]), HTTP_HOST="demo.example.com").url == reverse("school_dashboard", args=[portals.demo.slug])
    assert generate_demo_link(portals.demo, portals.operator, "scheduling").destination == "scheduling"


def test_ops_lists_counters_filters_reports_and_details_are_scoped(client, portals):
    client.force_login(portals.operator)
    for school in (portals.demo, portals.live):
        Submission.objects.create(school=school, form_key=school.slug, status=school.slug, data={})
        Lead.objects.create(school=school, name=school.display_name, email=school.slug+"@example.com")
        OpsIncident.objects.create(title=school.display_name, affected_school=school, created_by=portals.operator, occurred_at=timezone.now())
    for host, school, other in [("demo.example.com", portals.demo, portals.live), ("app.example.com", portals.live, portals.demo)]:
        dashboard = client.get("/ops/", HTTP_HOST=host)
        assert dashboard.context["total_schools"] == 1
        for path in ("/ops/schools/", "/ops/submissions/", "/ops/leads/", "/ops/reports/", "/ops/incidents/", "/ops/diagnostics/"):
            response = client.get(path, HTTP_HOST=host)
            assert response.status_code == 200
            assert school.display_name.encode() in response.content
            assert other.display_name.encode() not in response.content
        submissions = client.get("/ops/submissions/", HTTP_HOST=host)
        assert list(submissions.context["form_key_choices"]) == [school.slug]
        assert list(submissions.context["status_choices"]) == [school.slug]
        assert client.get("/ops/submissions/", {"school":other.slug}, HTTP_HOST=host).context["total_count"] == 0
        wrong = reverse("ops_school_detail", args=[other.slug])
        assert client.get(wrong, HTTP_HOST=host).status_code == 302
        assert client.post(wrong, {}, HTTP_HOST=host).status_code == 403
    assert portals.owner.username.encode() not in client.get("/ops/users/", HTTP_HOST="app.example.com").content
    assert client.get(reverse("ops_user_detail", args=[portals.owner.pk]), HTTP_HOST="app.example.com").status_code == 404
    assert client.post(reverse("ops_user_toggle_active", args=[portals.owner.pk]), HTTP_HOST="app.example.com").status_code == 404


def test_django_admin_records_related_choices_and_recent_actions_are_scoped(client, portals):
    client.force_login(portals.operator)
    ct = ContentType.objects.get_for_model(School)
    for school in (portals.demo, portals.live):
        LogEntry.objects.create(user=portals.operator, content_type=ct, object_id=str(school.pk), object_repr=school.display_name, action_flag=CHANGE)
    for host, school, other in [("demo.example.com", portals.demo, portals.live), ("app.example.com", portals.live, portals.demo)]:
        for path in (reverse("admin:index"), reverse("admin:core_school_changelist")):
            response = client.get(path, HTTP_HOST=host)
            assert response.status_code == 200
            assert other.display_name.encode() not in response.content
        request = RequestFactory().get("/admin/", HTTP_HOST=host)
        request.user = portals.operator
        membership_admin = admin.site._registry[SchedulingMembership]
        assert list(membership_admin.get_form(request).base_fields["profile"].queryset)  # Instance scoping below, not shared class mutation.
        form = membership_admin.get_form(request)()
        assert set(form.fields["profile"].queryset.values_list("school_id", flat=True)) == ({portals.demo.pk} if school.is_demo else set())
        assert list(admin.site.get_log_entries(request).values_list("object_repr", flat=True)) == [school.display_name]


def test_account_maintenance_dry_run_then_exact_deletion_preserves_business_data(portals):
    audit = AdminAuditLog.objects.create(actor=portals.owner, action="change", object_repr="old test action")
    related = AdminAuditLog.objects.create(action="change", model_label="auth.user", object_id=str(portals.owner.pk))
    keep = AdminAuditLog.objects.create(actor=portals.operator, action="change", extra={"school_id":portals.live.pk})
    school_history = AdminAuditLog.objects.create(action="change", extra={"school_id":portals.demo.pk})
    ct = ContentType.objects.get_for_model(School)
    history = LogEntry.objects.create(user=portals.owner, content_type=ct, object_id=str(portals.demo.pk), action_flag=CHANGE)
    args = [portals.demo.slug, "--username", portals.owner.username, "--rename", "new_demo_admin", "--delete-old-audit"]
    output = StringIO()
    call_command("maintain_demo_account", *args, stdout=output)
    assert "Dry run" in output.getvalue() and AdminAuditLog.objects.filter(pk=audit.pk).exists()
    call_command("maintain_demo_account", *args, "--apply", stdout=output)
    portals.owner.refresh_from_db()
    assert portals.owner.username == "new_demo_admin" and portals.owner.check_password("admin")
    assert not AdminAuditLog.objects.filter(pk__in=[audit.pk, related.pk]).exists()
    assert not LogEntry.objects.filter(pk=history.pk).exists()
    assert AdminAuditLog.objects.filter(pk__in=[keep.pk, school_history.pk]).count() == 2
    assert SchedulingMembership.objects.get(pk=portals.membership.pk).user_id == portals.owner.pk
    assert AdminAuditLog.objects.get(extra__name="demo_account_updated").environment == "demo"


@pytest.mark.parametrize("case", ["live", "missing", "privileged", "shared", "inactive", "collision", "invalid"])
def test_account_maintenance_refuses_unsafe_targets(portals, case):
    school = portals.live if case == "live" else portals.demo
    if case == "privileged":
        portals.owner.is_superuser = True
        portals.owner.save()
    if case == "shared":
        SchoolAdminMembership.objects.create(school=portals.live, user=portals.owner)
    if case == "inactive":
        portals.membership.is_active = False
        portals.membership.save()
    with pytest.raises(CommandError):
        call_command("maintain_demo_account", school.slug, "--username", "missing" if case == "missing" else portals.owner.username,
            "--rename", "invalid name" if case == "invalid" else portals.operator.username if case == "collision" else "new_demo_admin", "--apply")


def test_account_rename_without_cleanup_keeps_audit(portals):
    audit = AdminAuditLog.objects.create(actor=portals.owner, action="change")
    call_command("maintain_demo_account", portals.demo.slug, "--username", portals.owner.username,
        "--rename", "renamed_only", "--apply", stdout=StringIO())
    assert AdminAuditLog.objects.filter(pk=audit.pk).exists()


def test_customer_demo_destination_is_configurable_and_rejects_external_paths(portals, settings, monkeypatch):
    from scheduling.experience_demo import config
    fallback = reverse("scheduling:experience_start", args=[portals.demo.slug])
    for path in ("", "//example.net", "https://example.net", "/\\example.net", None):
        monkeypatch.setattr(config, "configuration", lambda slug, value=path: {"customer_demo_path": value})
        assert config.customer_demo_url(portals.profile) == fallback
    monkeypatch.setattr(config, "configuration", lambda slug: {"customer_demo_path": "/demo/comparison/"})
    assert config.customer_demo_url(portals.profile) == "https://demo.example.com/demo/comparison/"


def test_demo_host_school_creation_cannot_create_a_production_school(client, portals):
    client.force_login(portals.operator)
    response = client.post(reverse("ops_school_create"), {"slug":"new-prospect", "display_name":"New Prospect",
        "plan":"trial", "is_active":"on", "feature_flags":"{}"}, HTTP_HOST="demo.example.com")
    assert response.status_code == 302
    assert School.objects.get(slug="new-prospect").is_demo


def test_legacy_school_staff_user_admin_queryset_still_uses_their_membership(portals):
    SchoolAdminMembership.objects.create(school=portals.demo, user=portals.owner)
    request = RequestFactory().get("/admin/", HTTP_HOST="demo.example.com")
    request.user = portals.owner
    assert list(admin.site._registry[get_user_model()].get_queryset(request)) == [portals.owner]
