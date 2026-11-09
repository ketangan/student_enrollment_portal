from io import StringIO
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.test import RequestFactory

from core.models import School
from core.services.portal_environment import (
    deployment_environment, request_environment, require_deployment_school,
    scope_deployment_schools,
)
from core.services.onboarding import convert_demo_to_customer
from core.services.url_builder import request_reverse
from scheduling.models import SchedulingProfile

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("environment", ["demo", "production"])
def test_dedicated_deployment_scopes_aliases_and_jobs(settings, environment):
    settings.PORTAL_DEPLOYMENT_ENV = environment
    settings.APP_BASE_URL = "https://app.example.com"
    settings.DEMO_BASE_URL = "https://demo.example.com"
    demo = School.objects.create(slug="prospect", is_demo=True)
    production = School.objects.create(slug="customer", is_demo=False)
    expected = demo if environment == "demo" else production
    other = production if environment == "demo" else demo
    for host in ["legacy.example.com", "service.onrender.com", "demo.example.com", "app.example.com"]:
        request = RequestFactory().get("/", HTTP_HOST=host)
        assert request_environment(request) == environment
        assert request_reverse(request, "school_dashboard", kwargs={"school_slug": expected.slug}).startswith(
            settings.DEMO_BASE_URL if environment == "demo" else settings.APP_BASE_URL)
    assert list(scope_deployment_schools(School.objects.all())) == [expected]
    profile = SchedulingProfile.objects.create(school=expected)
    SchedulingProfile.objects.create(school=other)
    assert list(scope_deployment_schools(SchedulingProfile.objects.all(), "school__")) == [profile]
    require_deployment_school(expected)
    with pytest.raises(ValidationError):
        require_deployment_school(other)


def test_invalid_environment_fails_closed_and_shared_remains_compatible(settings):
    settings.PORTAL_DEPLOYMENT_ENV = "typo"
    with pytest.raises(ValidationError):
        deployment_environment()
    settings.PORTAL_DEPLOYMENT_ENV = "shared"
    assert deployment_environment() is None
    queryset = School.objects.all()
    assert scope_deployment_schools(queryset) is queryset
    require_deployment_school(SimpleNamespace(is_demo=False))


def test_demo_cannot_convert_in_place_or_recover_live_payments(settings):
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    school = School.objects.create(slug="prospect", is_demo=True)
    with pytest.raises(ValidationError, match="production"):
        convert_demo_to_customer(school=school, plan="trial", trial_days=30,
            admin_email="owner@example.invalid", admin_first_name="", admin_last_name="",
            delete_submissions=True, delete_leads=True, actor=None)
    school.refresh_from_db()
    assert school.is_demo
    with pytest.raises(CommandError, match="Live"):
        call_command("reconcile_booking_payments", environment="live")


def test_demo_startup_rejects_wrong_database_and_live_credentials(settings):
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    settings.STRIPE_MODE = "test"
    settings.PAYMENTS_SQUARE_LIVE_ENABLED = False
    settings.PAYMENTS_SQUARE_TERMINAL_LIVE_ENABLED = False
    settings.PAYMENTS_SQUARE_LIVE_APPLICATION_SECRET = ""
    school = School.objects.create(slug="prospect", is_demo=True)
    call_command("check_portal_deployment", stdout=StringIO())
    school.is_demo = False
    school.save(update_fields=["is_demo"])
    with pytest.raises(CommandError, match="Non-demo"):
        call_command("check_portal_deployment")
    school.is_demo = True
    school.app_fee_stripe_secret_key = "sk_live_not-a-real-key"
    school.save(update_fields=["is_demo", "app_fee_stripe_secret_key"])
    with pytest.raises(CommandError, match="Live application"):
        call_command("check_portal_deployment")
    settings.STRIPE_MODE = "live"
    with pytest.raises(CommandError, match="sandbox"):
        call_command("check_portal_deployment")


@pytest.mark.parametrize("environment", ["demo", "production"])
def test_outbox_job_only_processes_its_own_schools(settings, monkeypatch, environment):
    from scheduling.management.commands import process_party_notifications as command
    settings.PORTAL_DEPLOYMENT_ENV = environment
    settings.DEBUG = False
    settings.BOOKINGS_PARTY_EMAIL_ENABLED = True
    profiles = []
    for demo in (True, False):
        school = School.objects.create(slug=f"business-{demo}", is_demo=demo)
        profiles.append(SchedulingProfile.objects.create(school=school, enabled=True, party_notifications_enabled=True))
    called = []
    monkeypatch.setattr(command, "live_enabled", lambda profile: True)
    monkeypatch.setattr(command, "demo_delivery_enabled", lambda profile: profile.school.is_demo)
    monkeypatch.setattr(command, "process", lambda pk, limit: called.append(pk) or 0)
    call_command("process_party_notifications", all_schools=True, stdout=StringIO())
    assert called == [profiles[0 if environment == "demo" else 1].pk]


def test_ops_cannot_reclassify_school_across_databases(settings):
    from core.forms_ops import OpsSchoolEditForm
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    school = School.objects.create(slug="prospect", is_demo=True)
    form = OpsSchoolEditForm(instance=school)
    assert form.fields["is_demo"].disabled
    assert form.fields["is_demo"].initial is True


def test_payment_provider_rejects_other_deployment_before_network(settings):
    from core.payments.accounts import validate_scope
    from core.payments.square import validate_account
    settings.PORTAL_DEPLOYMENT_ENV = "production"
    account = SimpleNamespace(school=SimpleNamespace(is_demo=True), provider="square")
    for validate in [validate_scope, validate_account]:
        with pytest.raises(ValidationError, match="different portal"):
            validate(account)


@pytest.mark.parametrize("key", ["sk_live_example", "rk_live_example", "unknown", "  "])
def test_demo_application_fee_rejects_non_test_credentials_before_network(settings, monkeypatch, key):
    import stripe
    from core.services import billing_stripe
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    school = School.objects.create(slug="fee-demo", is_demo=True, app_fee_stripe_secret_key=key)
    def unexpected_call(*args, **kwargs):
        pytest.fail("Unsafe credentials reached Stripe")
    monkeypatch.setattr(stripe.PaymentIntent, "create", unexpected_call)
    monkeypatch.setattr(stripe.PaymentIntent, "retrieve", unexpected_call)
    for call in [lambda: billing_stripe.create_application_fee_intent(school=school, amount_cents=500),
                 lambda: billing_stripe.retrieve_application_fee_intent(school=school, payment_intent_id="pi_test")]:
        with pytest.raises((ValidationError, ValueError)):
            call()


@pytest.mark.parametrize("environment,key", [("demo", "sk_test_example"), ("demo", "rk_test_example"),
    ("production", "sk_live_example"), ("shared", "sk_live_example")])
def test_application_fee_keeps_supported_credentials(settings, monkeypatch, environment, key):
    import stripe
    from core.services import billing_stripe
    settings.PORTAL_DEPLOYMENT_ENV = environment
    school = School.objects.create(slug="fee-school", is_demo=environment == "demo", app_fee_stripe_secret_key=key)
    calls = []
    def create(**kwargs):
        calls.append(kwargs["api_key"])
        return SimpleNamespace(client_secret="client", id="pi_test")
    monkeypatch.setattr(stripe.PaymentIntent, "create", create)
    assert billing_stripe.create_application_fee_intent(school=school, amount_cents=500) == ("client", "pi_test")
    assert calls == [key]


@pytest.mark.parametrize("secret,public", [("rk_live_example", "pk_test_example"),
    ("unknown", "pk_test_example"), ("sk_test_example", "pk_live_example")])
def test_demo_ops_and_startup_reject_non_test_application_keys(settings, secret, public):
    from core.forms_ops import OpsSchoolEditForm
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    school = School.objects.create(slug="fee-demo", is_demo=True)
    form = OpsSchoolEditForm(instance=school, data={"display_name": "Demo", "plan": "starter",
        "is_active": True, "feature_flags": "{}", "app_fee_stripe_secret_key": secret,
        "app_fee_stripe_public_key": public})
    assert not form.is_valid()
    assert "app_fee_stripe_secret_key" in form.errors or "app_fee_stripe_public_key" in form.errors
    school.app_fee_stripe_secret_key = secret
    school.app_fee_stripe_public_key = public
    school.save()
    with pytest.raises(CommandError, match="application-fee"):
        call_command("check_portal_deployment")


def test_demo_rejects_connected_live_account(settings):
    from core.payments.accounts import validate_scope
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    account = SimpleNamespace(school=SimpleNamespace(is_demo=True), provider="stripe", scope="connected", environment="live")
    with pytest.raises(ValidationError, match="sandbox"):
        validate_scope(account)


def test_demo_owner_cannot_save_live_application_credentials(client, settings):
    from django.urls import reverse
    from core.tests.factories import SchoolAdminMembershipFactory, UserFactory
    settings.PORTAL_DEPLOYMENT_ENV = "demo"
    school = School.objects.create(slug="south-bay-music", is_demo=True, plan="trial")
    owner = UserFactory()
    SchoolAdminMembershipFactory(school=school, user=owner, role="owner")
    client.force_login(owner)
    response = client.post(reverse("school_settings", kwargs={"school_slug": school.slug}), {
        "action": "update_stripe", "app_fee_stripe_public_key": "pk_test_example",
        "app_fee_stripe_secret_key": "rk_live_example"})
    assert response.status_code == 302
    school.refresh_from_db()
    assert school.app_fee_stripe_secret_key == ""
    assert school.app_fee_stripe_public_key == ""


@pytest.mark.parametrize("environment", ["demo", "production"])
def test_hosted_public_form_never_recreates_an_absent_or_wrong_environment_school(settings, environment):
    from django.http import Http404
    from core.views_public import _get_or_create_school_from_config
    settings.PORTAL_DEPLOYMENT_ENV = environment
    config = SimpleNamespace(display_name="Configured school", raw={})
    branding = {"theme": {}}
    with pytest.raises(Http404):
        _get_or_create_school_from_config("configured-school", config, branding)
    assert not School.objects.filter(slug="configured-school").exists()
    school = School.objects.create(slug="configured-school", is_demo=environment != "demo")
    with pytest.raises(Http404):
        _get_or_create_school_from_config(school.slug, config, branding)
    school.is_demo = environment == "demo"
    school.save()
    assert _get_or_create_school_from_config(school.slug, config, branding).pk == school.pk


def test_shared_local_form_can_still_initialize_from_config(settings):
    from core.views_public import _get_or_create_school_from_config
    settings.PORTAL_DEPLOYMENT_ENV = "shared"
    school = _get_or_create_school_from_config("configured-school",
        SimpleNamespace(display_name="Configured school", raw={}), {"theme": {}})
    assert school.pk


@pytest.mark.parametrize("environment", ["demo", "production"])
def test_operator_creation_uses_the_fixed_deployment(client, settings, environment):
    from django.urls import reverse
    from core.tests.factories import UserFactory
    settings.PORTAL_DEPLOYMENT_ENV = environment
    client.force_login(UserFactory(is_superuser=True, is_staff=True))
    response = client.post(reverse("ops_school_create"), {"slug": "new-school", "display_name": "New School",
        "plan": "starter", "is_active": True, "feature_flags": "{}"})
    assert response.status_code == 302
    assert School.objects.get(slug="new-school").is_demo == (environment == "demo")
