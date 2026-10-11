"""Operator setup must be tenant-scoped and must never alter school subscriptions."""
from types import SimpleNamespace

import pytest
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from core.models import School
from core.payments.admin import PaymentAccountForm
from core.payments.models import PaymentAccount

pytestmark = pytest.mark.django_db


@pytest.fixture
def configured(settings):
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_booking"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_booking"
    settings.PAYMENTS_STRIPE_TEST_ACCOUNT_ID = "acct_shared"
    settings.PAYMENTS_STRIPE_TEST_METHOD_CONFIG = "pmc_cards"


@pytest.fixture
def operator(client):
    user = get_user_model().objects.create_superuser("payment-operator", "", "test-password")
    client.force_login(user)
    return user


def test_operator_can_enable_multiple_demos_without_changing_live_school(client, operator, configured):
    live = School.objects.create(slug="live-school", is_demo=False, plan="custom",
        stripe_subscription_id="sub_protected", stripe_subscription_status="active")
    before = School.objects.filter(pk=live.pk).values().get()
    url = reverse("admin:payments_paymentaccount_add")
    for index in range(2):
        school = School.objects.create(slug=f"prospect-{index}", is_demo=True)
        response = client.post(url, {"school": school.pk, "scope": "platform_demo", "enabled": "on"})
        assert response.status_code == 302
        account = PaymentAccount.objects.get(school=school)
        assert account.enabled and account.reference == "acct_shared" and account.scope == "platform_demo"
    assert PaymentAccount.objects.count() == 2
    assert LogEntry.objects.filter(user=operator, content_type__model="paymentaccount").count() == 2
    assert School.objects.filter(pk=live.pk).values().get() == before
    assert not PaymentAccount.objects.filter(school=live).exists()
    assert client.get(reverse("admin:payments_paymentaccount_changelist")).status_code == 200


def test_saved_binding_is_readonly_and_cannot_be_deleted(client, operator, configured):
    school = School.objects.create(slug="demo-owner", is_demo=True)
    other = School.objects.create(slug="demo-other", is_demo=True)
    account = PaymentAccount.objects.create(school=school, reference="acct_original", enabled=True)
    url = reverse("admin:payments_paymentaccount_change", args=[account.pk])
    form = client.get(url).context["adminform"].form
    assert "school" not in form.fields and "reference" not in form.fields
    assert "enabled" in form.fields
    assert client.post(url, {"school": other.pk, "reference": "acct_stolen"}).status_code == 302
    account.refresh_from_db()
    assert account.school_id == school.pk and account.reference == "acct_original" and not account.enabled
    assert client.post(reverse("admin:payments_paymentaccount_delete", args=[account.pk]), {"post": "yes"}).status_code == 403


@pytest.mark.parametrize("active,superuser", [(False, True), (True, False), (False, False)])
def test_operator_permissions_are_explicit(active, superuser):
    request = RequestFactory().get("/")
    request.user = SimpleNamespace(is_active=active, is_superuser=superuser)
    control = admin.site._registry[PaymentAccount]
    for method in (control.has_module_permission, control.has_view_permission,
                   control.has_add_permission, control.has_change_permission, control.has_delete_permission):
        assert not method(request)


def test_staff_cannot_bypass_operator_control(client, configured):
    user = get_user_model().objects.create_user("regular-staff", is_staff=True)
    client.force_login(user)
    school = School.objects.create(slug="demo-staff", is_demo=True)
    account = PaymentAccount.objects.create(school=school, reference="acct_staff")
    for name, args in [("add", []), ("changelist", []), ("change", [account.pk])]:
        url = reverse(f"admin:payments_paymentaccount_{name}", args=args)
        assert client.get(url).status_code == 403
        assert client.post(url, {"school": school.pk, "reference": "acct_staff", "enabled": "on"}).status_code == 403
    account.refresh_from_db()
    assert not account.enabled


@pytest.mark.parametrize("field,value", [
    ("PAYMENTS_STRIPE_TEST_SECRET_KEY", ""),
    ("PAYMENTS_STRIPE_TEST_SECRET_KEY", "sk_live_forbidden"),
    ("PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET", ""),
    ("PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET", "invalid"),
])
def test_enabling_requires_booking_test_credentials(configured, settings, field, value):
    setattr(settings, field, value)
    school = School.objects.create(slug="demo-credentials", is_demo=True)
    form = PaymentAccountForm(data={"school": school.pk, "scope": "connected", "reference": "acct_demo", "enabled": True})
    assert not form.is_valid() and "enabled" in form.errors
    disabled = PaymentAccountForm(data={"school": school.pk, "scope": "connected", "reference": "acct_demo"})
    assert disabled.is_valid(), disabled.errors


def test_form_rejects_live_school_invalid_account_and_duplicate_bindings(configured):
    live = School.objects.create(slug="live-school")
    demo = School.objects.create(slug="demo-form", is_demo=True)
    form = PaymentAccountForm(data={"school": live.pk, "scope": "connected", "reference": "acct_live", "enabled": True})
    assert not form.is_valid() and "school" in form.errors
    for reference in ["sk_test_secret", "", "acct_", "acct_bad/value"]:
        form = PaymentAccountForm(data={"school": demo.pk, "scope": "connected", "reference": reference})
        assert not form.is_valid() and "reference" in form.errors
    account = PaymentAccount.objects.create(school=live, reference="acct_legacy")
    form = PaymentAccountForm(data={"scope": "connected", "reference": "acct_legacy", "enabled": True}, instance=account)
    assert not form.is_valid() and "__all__" in form.errors
    PaymentAccount.objects.create(school=demo, reference="acct_demo")
    form = PaymentAccountForm(data={"school": demo.pk, "scope": "connected", "reference": "acct_duplicate"})
    assert not form.is_valid()


@pytest.mark.parametrize("configured_id,submitted_id", [("", ""), ("bad", ""), ("acct_shared", "acct_wrong")])
def test_shared_account_cannot_override_render_binding(configured, settings, configured_id, submitted_id):
    settings.PAYMENTS_STRIPE_TEST_ACCOUNT_ID = configured_id
    school = School.objects.create(slug="shared-account-demo", is_demo=True)
    form = PaymentAccountForm(data={"school": school.pk, "scope": "platform_demo", "reference": submitted_id})
    assert not form.is_valid() and "__all__" in form.errors


def test_shared_account_requires_method_configuration(configured, settings):
    settings.PAYMENTS_STRIPE_TEST_METHOD_CONFIG = ""
    school = School.objects.create(slug="shared-method-config", is_demo=True)
    form = PaymentAccountForm(data={"school": school.pk, "scope": "platform_demo", "enabled": True})
    assert not form.is_valid() and "enabled" in form.errors


@pytest.mark.parametrize("configured_id", ["acct_shared", "", "acct_replaced"])
def test_shared_account_can_be_disabled_without_rebinding(client, operator, configured, settings, configured_id):
    settings.PAYMENTS_STRIPE_TEST_ACCOUNT_ID = configured_id
    school = School.objects.create(slug="shared-disable", is_demo=True)
    account = PaymentAccount.objects.create(school=school, reference="acct_shared", scope="platform_demo", enabled=True)
    url = reverse("admin:payments_paymentaccount_change", args=[account.pk])
    assert client.post(url, {"scope": "connected", "reference": "acct_other"}).status_code == 302
    account.refresh_from_db()
    assert not account.enabled and account.scope == "platform_demo" and account.reference == "acct_shared"
