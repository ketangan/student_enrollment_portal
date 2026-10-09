from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as dt_timezone
from io import StringIO
from threading import Barrier
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.forms_trial import TrialActivationForm
from core.models import AdminAuditLog, DemoAccessToken, Lead, School, Submission
from core.services import billing_stripe
from core.services.customer_terms import load_customer_terms, prepare_customer_terms
from core.services.feature_flags import default_flags_for_plan
from core.services.onboarding import convert_demo_to_customer, send_welcome_email
from core.services.trial_activation import start_customer_trial
from core.tests.factories import SchoolAdminMembershipFactory, SchoolFactory, UserFactory

pytestmark = pytest.mark.django_db
NOW = datetime(2026, 10, 8, 19, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def operator():
    return UserFactory(is_staff=True, is_superuser=True)


@pytest.fixture
def pending():
    return SchoolFactory(slug="enrollment-request-demo", trial_requires_activation=True,
                         trial_duration_days=60)


def activate(school, operator, **changes):
    return start_customer_trial(**{
        "school_id": school.pk, "actor": operator,
        "notice_sent_at": timezone.now() - timedelta(minutes=1),
        "notice_reference": "Written go-live email, customer approved", **changes,
    })


def conversion(school, operator, **changes):
    return convert_demo_to_customer(**{
        "school": school, "actor": operator, "plan": "trial", "trial_days": 60,
        "admin_email": "customer@example.invalid", "admin_first_name": "Test",
        "admin_last_name": "Customer", "delete_submissions": False, "delete_leads": False,
        **changes,
    })


@pytest.mark.parametrize("age", [0, 29, 40])
def test_demo_old_trial_dates_never_count_down_or_expire(age):
    school = SchoolFactory(is_demo=True, trial_started_at=timezone.now()-timedelta(days=age),
                           trial_end_date=timezone.localdate()-timedelta(days=10))
    assert school.trial_ends_at is None
    assert not school.is_trial_expired and not school.show_trial_banner
    assert school.subscription_checkout_paused


def test_demo_and_deferred_saves_do_not_start_trial(pending):
    demo = SchoolFactory(is_demo=True)
    for school in (demo, pending):
        school.display_name = "Updated name"
        school.save()
        school.refresh_from_db()
        assert school.trial_started_at is None
        assert school.trial_ends_at is None
        assert not school.show_trial_banner and not school.is_trial_expired
    assert pending.trial_pending_activation


@pytest.mark.parametrize("slug", ["south-bay-music", "ordinary-school"])
def test_non_opted_in_trial_and_paid_behavior_unchanged(slug):
    with patch("django.utils.timezone.now", return_value=NOW):
        school = SchoolFactory(slug=slug)
        assert school.trial_started_at == NOW
        assert school.trial_ends_at == NOW + timedelta(days=30)
        assert not school.subscription_checkout_paused
        school.trial_started_at = NOW-timedelta(days=35)
        school.save()
        assert school.show_trial_banner and school.is_trial_expired
        school.plan = "custom"
        school.save()
        assert not school.is_trial_expired and not school.show_trial_banner


def test_sixty_days_from_notice_not_setup_or_old_override(pending, operator):
    pending.trial_end_date = NOW.date() - timedelta(days=30)
    pending.save()
    with patch("django.utils.timezone.now", return_value=NOW):
        assert activate(pending, operator, notice_sent_at=NOW)
        pending.refresh_from_db()
        assert pending.trial_started_at == NOW
        assert pending.trial_end_date is None
        assert pending.trial_ends_at == NOW + timedelta(days=60)
        assert pending.trial_days_left == 60
        assert not pending.trial_pending_activation
        assert pending.subscription_checkout_paused
        assert not pending.show_trial_banner
        assert not activate(pending, operator, notice_sent_at=NOW-timedelta(hours=1))
    log = AdminAuditLog.objects.get(extra__name="customer_trial_started")
    assert log.actor == operator
    assert log.extra["duration_days"] == 60
    assert log.extra["notice_reference"] == "Written go-live email, customer approved"
    with patch("django.utils.timezone.now", return_value=NOW+timedelta(days=60)):
        assert pending.is_trial_expired and pending.show_trial_banner
        assert pending.trial_days_left == 0
        assert not pending.subscription_checkout_paused


@pytest.mark.parametrize("delta,days,expired,banner", [
    (0, 60, False, False), (49, 11, False, False), (50, 10, False, True),
    (59, 1, False, True), (60, 0, True, True), (61, 0, True, True),
])
def test_customer_trial_boundaries(pending, operator, delta, days, expired, banner):
    with patch("django.utils.timezone.now", return_value=NOW):
        activate(pending, operator, notice_sent_at=NOW)
    pending.refresh_from_db()
    with patch("django.utils.timezone.now", return_value=NOW+timedelta(days=delta)):
        assert pending.trial_days_left == days
        assert pending.is_trial_expired == expired
        assert pending.show_trial_banner == banner
        assert pending.subscription_checkout_paused == (not expired)


@pytest.mark.parametrize("changes", [
    {"notice_sent_at": None}, {"notice_sent_at": datetime(2026, 10, 8)},
    {"notice_sent_at": NOW+timedelta(minutes=1)}, {"notice_reference": "  "},
    {"notice_reference": "a"*501}, {"notice_sent_at": NOW-timedelta(days=60)},
])
def test_activation_rejects_invalid_notice(pending, operator, changes):
    with patch("django.utils.timezone.now", return_value=NOW), pytest.raises(ValidationError):
        activate(pending, operator, **changes)
    pending.refresh_from_db()
    assert pending.trial_started_at is None
    assert not AdminAuditLog.objects.filter(extra__name="customer_trial_started").exists()


@pytest.mark.parametrize("changes", [
    {"is_demo": True}, {"is_active": False}, {"plan": "starter"},
    {"trial_requires_activation": False}, {"stripe_subscription_id": "sub_existing"},
    {"trial_duration_days": 0}, {"trial_duration_days": 366},
])
def test_activation_rejects_ineligible_school(pending, operator, changes):
    School.objects.filter(pk=pending.pk).update(**changes)
    with pytest.raises(ValidationError):
        activate(pending, operator)


@pytest.mark.parametrize("active,superuser", [(True, False), (False, True)])
def test_only_active_operator_can_activate(pending, active, superuser):
    actor = UserFactory(is_active=active, is_superuser=superuser)
    with pytest.raises(PermissionDenied):
        activate(pending, actor)


def test_activation_rolls_back_if_audit_fails(pending, operator):
    with patch("core.services.trial_activation.AdminAuditLog.objects.create", side_effect=RuntimeError), pytest.raises(RuntimeError):
        activate(pending, operator)
    pending.refresh_from_db()
    assert pending.trial_started_at is None


@pytest.mark.django_db(transaction=True)
def test_simultaneous_activations_start_once(pending, operator):
    barrier = Barrier(2)
    sent = timezone.now()-timedelta(minutes=1)
    def attempt():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return activate(pending, operator, notice_sent_at=sent)
        finally:
            close_old_connections()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: attempt(), range(2)))
    assert sorted(results) == [False, True]
    assert AdminAuditLog.objects.filter(extra__name="customer_trial_started").count() == 1


def test_deferred_conversion_clears_demo_dates_without_starting(operator):
    school = SchoolFactory(is_demo=True, trial_requires_activation=True, trial_duration_days=60,
                           trial_started_at=NOW-timedelta(days=50), trial_end_date=NOW.date())
    conversion(school, operator)
    school.refresh_from_db()
    assert not school.is_demo and school.trial_pending_activation
    assert school.trial_started_at is None and school.trial_end_date is None
    school.save()
    assert school.trial_started_at is None


@pytest.mark.parametrize("changes", [{"plan": "starter"}, {"trial_days": 30}])
def test_conversion_cannot_bypass_agreed_deferred_trial(operator, changes):
    school = SchoolFactory(is_demo=True, trial_requires_activation=True, trial_duration_days=60)
    member = SchoolAdminMembershipFactory(school=school)
    with pytest.raises(ValidationError):
        conversion(school, operator, **changes)
    school.refresh_from_db()
    assert school.is_demo
    assert school.admin_memberships.filter(pk=member.pk).exists()


def test_conversion_view_uses_configured_duration(client, operator):
    school = SchoolFactory(is_demo=True, trial_requires_activation=True, trial_duration_days=60)
    client.force_login(operator)
    url = reverse("ops_school_convert", args=[school.slug])
    assert b'value="60"' in client.get(url).content
    bad = client.post(url, {"admin_email": "owner@example.invalid", "plan": "starter"})
    assert b"Prepare this school on Trial" in bad.content
    good = client.post(url, {"admin_email": "owner@example.invalid", "plan": "trial"})
    assert b"60-day trial awaiting written go-live" in good.content


@pytest.mark.parametrize("demo", [True, False])
def test_setup_and_demo_cannot_create_subscription_sessions(pending, demo):
    pending.is_demo = demo
    with patch("core.services.billing_stripe._get_stripe") as provider:
        assert billing_stripe.create_checkout_session(school=pending, price_id="price_test",
            success_url="https://example.invalid", cancel_url="https://example.invalid") is None
        assert billing_stripe.create_portal_session(school=pending, return_url="https://example.invalid") is None
        provider.assert_not_called()


def test_welcome_email_cannot_claim_setup_is_live(pending, operator, mailoutbox):
    SchoolAdminMembershipFactory(school=pending)
    assert not send_welcome_email(pending, operator)
    assert not mailoutbox and not DemoAccessToken.objects.exists()


def test_kidworks_terms_preserve_starter_workflow_and_other_schools(operator):
    school = SchoolFactory(slug="kidworks-childrens-center", is_demo=True,
                           feature_flags={"email_notifications_enabled": False})
    other = SchoolFactory(slug="south-bay-music", plan="custom")
    before = School.objects.filter(pk=other.pk).values().get()
    assert prepare_customer_terms(school_id=school.pk, actor=operator)
    assert not prepare_customer_terms(school_id=school.pk, actor=operator)
    school.refresh_from_db()
    assert school.is_demo and school.plan == "trial" and school.trial_started_at is None
    assert school.trial_requires_activation and school.trial_duration_days == 60
    assert school.feature_flags["email_notifications_enabled"] is False
    school.plan = "starter"
    school.save()
    school.refresh_from_db()
    for key in ["leads_enabled", "csv_export_enabled", "custom_branding_enabled", "custom_statuses_enabled",
                "leads_conversion_enabled", "save_resume_enabled", "status_enabled", "family_portal_enabled"]:
        assert getattr(school.features, key), key
    assert not school.features.ai_summary_enabled
    assert not school.features.multi_form_enabled
    assert not default_flags_for_plan("starter")["custom_statuses_enabled"]
    assert School.objects.filter(pk=other.pk).values().get() == before


def test_paid_starter_webhooks_keep_the_contract_overrides(operator, settings):
    settings.STRIPE_PRICE_STARTER_MONTHLY = "price_kw05_starter"
    school = SchoolFactory(slug="kidworks-childrens-center", is_demo=True)
    prepare_customer_terms(school_id=school.pk, actor=operator)
    school.refresh_from_db()
    conversion(school, operator)
    with patch("django.utils.timezone.now", return_value=NOW):
        activate(school, operator, notice_sent_at=NOW)
    school.refresh_from_db()
    expected = dict(school.feature_flags)
    with patch("django.utils.timezone.now", return_value=NOW+timedelta(days=61)), \
            patch("core.services.billing_stripe._get_stripe") as provider:
        billing_stripe.handle_checkout_completed({
            "metadata": {"school_slug": school.slug},
            "customer": "cus_kw05", "subscription": "sub_kw05",
            "line_items": {"data": [{"price": {"id": "price_kw05_starter"}}]},
        })
        billing_stripe.handle_subscription_updated({
            "id": "sub_kw05", "status": "active",
            "items": {"data": [{"price": {"id": "price_kw05_starter"}}]},
        })
        provider.assert_not_called()
    school.refresh_from_db()
    assert school.plan == "starter" and school.has_active_stripe_subscription
    assert school.feature_flags == expected
    assert all(getattr(school.features, key) for key in expected)
    assert not school.subscription_checkout_paused


def test_terms_command_preview_apply_and_retry(operator):
    school = SchoolFactory(slug="kidworks-childrens-center", is_demo=True)
    before = School.objects.filter(pk=school.pk).values().get()
    out = StringIO()
    call_command("prepare_customer_terms", school.slug, stdout=out)
    assert "Preview only" in out.getvalue()
    assert School.objects.filter(pk=school.pk).values().get() == before
    for _ in range(2):
        call_command("prepare_customer_terms", school.slug, apply=True, actor=operator.username, stdout=out)
    assert "Already prepared" in out.getvalue()
    assert AdminAuditLog.objects.filter(extra__name="customer_terms_prepared").count() == 1


@pytest.mark.parametrize("kind", ["missing-school", "missing-config", "missing-actor", "non-operator", "live"])
def test_terms_command_fails_closed(operator, kind):
    school = SchoolFactory(slug="kidworks-childrens-center" if kind != "missing-config" else "other", is_demo=kind != "live")
    kwargs = {"apply": True, "actor": operator.username, "stdout": StringIO()}
    if kind == "missing-actor":
        kwargs.pop("actor")
    elif kind == "non-operator":
        kwargs["actor"] = UserFactory().username
    with pytest.raises(CommandError):
        call_command("prepare_customer_terms", "absent" if kind == "missing-school" else school.slug, **kwargs)


@pytest.mark.parametrize("terms", [[], {}, {"plan": "trial", "trial_duration_days": 60, "feature_flags": {}},
    {"plan": "starter", "trial_duration_days": True, "feature_flags": {}},
    {"plan": "starter", "trial_duration_days": 366, "feature_flags": {}},
    {"plan": "starter", "trial_duration_days": 60, "feature_flags": []},
    {"plan": "starter", "trial_duration_days": 60, "feature_flags": {"unknown": True}},
    {"plan": "starter", "trial_duration_days": 60, "feature_flags": {"csv_export_enabled": "yes"}},
])
def test_terms_validation(terms):
    import json
    with patch("pathlib.Path.read_text", return_value=json.dumps(terms)), pytest.raises(ValidationError):
        load_customer_terms("example")


@pytest.mark.parametrize("demo", [True, False])
def test_billing_and_settings_are_consistent_without_trial_countdown(client, pending, demo):
    pending.is_demo = demo
    pending.save()
    member = SchoolAdminMembershipFactory(school=pending)
    client.force_login(member.user)
    legacy = client.get(reverse("admin:billing"))
    assert legacy.status_code == 302
    assert legacy.url == reverse("school_billing", args=[pending.slug])
    for name in ["school_dashboard", "school_settings", "school_billing"]:
        response = client.get(reverse(name, args=[pending.slug]))
        assert response.status_code == 200
        assert b"Trial ends in" not in response.content
        assert b"Your trial has expired" not in response.content
        if name == "school_billing":
            assert b"Subscription checkout is unavailable" in response.content
            assert b'name="price_id"' not in response.content
    response = client.get(reverse("ops_school_detail", args=[pending.slug]))
    assert response.status_code == 302  # School admin cannot operate trial settings.


def test_trial_activation_form_and_ops_page(client, pending, operator):
    member = SchoolAdminMembershipFactory(school=pending)
    url = reverse("ops_trial_start", args=[pending.slug])
    assert client.post(url, {}).status_code == 302
    client.force_login(member.user)
    assert client.post(url, {}).status_code == 302
    client.force_login(operator)
    assert client.get(url).status_code == 200
    data = {"notice_sent_at": timezone.now().isoformat(), "notice_reference": "Sent email #123"}
    bad = client.post(url, data)
    assert b"This field is required" in bad.content
    assert client.post(url, {**data, "confirmed": "on"}).status_code == 302
    assert client.post(url, {**data, "confirmed": "on"}).status_code == 302
    assert AdminAuditLog.objects.filter(extra__name="customer_trial_started").count() == 1
    assert b"not awaiting trial activation" in client.get(url).content
    assert client.put(url).status_code == 405
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(operator)
    assert strict.post(url, {**data, "confirmed": "on"}).status_code == 403


def test_future_notice_form_rejected():
    form = TrialActivationForm({"notice_sent_at": (timezone.now()+timedelta(days=1)).isoformat(),
                                "notice_reference": "draft", "confirmed": "on"})
    assert not form.is_valid()
    assert "notice_sent_at" in form.errors


def test_free_contract_trial_remains_charge_free_until_expiry(client, pending, operator):
    with patch("django.utils.timezone.now", return_value=NOW):
        activate(pending, operator, notice_sent_at=NOW)
    member = SchoolAdminMembershipFactory(school=pending)
    client.force_login(member.user)
    with patch("django.utils.timezone.now", return_value=NOW+timedelta(days=59)), \
            patch("core.services.billing_stripe._get_stripe") as provider:
        client.force_login(member.user)
        page = client.get(reverse("school_billing", args=[pending.slug]))
        assert b"No subscription payment is due" in page.content
        assert b"View trial details" in page.content
        assert b"Upgrade now" not in page.content
        assert b'name="price_id"' not in page.content
        assert client.post(reverse("school_billing_checkout", args=[pending.slug]),
                           {"price_id": "price_test"}).status_code == 302
        provider.assert_not_called()
    pending.refresh_from_db()
    assert pending.plan == "trial" and pending.stripe_subscription_id == ""
    with patch("django.utils.timezone.now", return_value=NOW+timedelta(days=60)):
        client.force_login(member.user)
        page = client.get(reverse("school_billing", args=[pending.slug]))
        assert "school_admin/billing.html" in [t.name for t in page.templates]
        assert not pending.subscription_checkout_paused


def test_activation_page_preserves_error_and_pending_state(client, pending, operator):
    client.force_login(operator)
    response = client.post(reverse("ops_trial_start", args=[pending.slug]), {
        "notice_sent_at": (timezone.now()-timedelta(days=61)).isoformat(),
        "notice_reference": "Old email", "confirmed": "on",
    })
    assert b"already expired trial" in response.content
    pending.refresh_from_db()
    assert pending.trial_pending_activation


def test_contract_fields_do_not_change_legacy_edit_form(pending):
    from core.admin.schools import SchoolAdminForm
    form = SchoolAdminForm(instance=pending)
    assert "trial_duration_days" not in form.fields
    assert "trial_requires_activation" not in form.fields


@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("change", [{"plan": "starter"}, {"is_demo": False},
    {"trial_end_date": "2027-01-01"}])
def test_settings_cannot_bypass_deferred_conversion(legacy, change):
    from django.forms.models import model_to_dict
    from core.forms_ops import OpsSchoolEditForm
    from core.admin.schools import SchoolAdminForm
    school = SchoolFactory(is_demo=True, trial_requires_activation=True, trial_duration_days=60,
                           trial_started_at=NOW-timedelta(days=30))
    data = {**model_to_dict(school), **change}
    with patch.object(SchoolAdminForm, "current_user_is_superuser", True):
        form = (SchoolAdminForm if legacy else OpsSchoolEditForm)(data=data, instance=school)
        assert not form.is_valid()
        assert "written go-live" in str(form.non_field_errors())
    school.refresh_from_db()
    assert school.is_demo and school.plan == "trial"


def test_saving_trial_settings_preserves_contracted_starter_overrides(operator):
    import json
    from django.forms.models import model_to_dict
    from core.admin.schools import SchoolAdminForm
    school = SchoolFactory(slug="kidworks-childrens-center", is_demo=True)
    prepare_customer_terms(school_id=school.pk, actor=operator)
    school.refresh_from_db()
    expected = dict(school.feature_flags)
    data = model_to_dict(school)
    data["config_overrides"] = json.dumps(school.config_overrides)
    data["display_name"] = "Updated school name"
    with patch.object(SchoolAdminForm, "current_user_is_superuser", True):
        form = SchoolAdminForm(data=data, instance=school)
        assert form.is_valid(), form.errors
        form.save()
    school.refresh_from_db()
    assert school.feature_flags == expected
    assert school.trial_started_at is None
    school.plan = "starter"
    school.save()
    assert all(getattr(school.features, key) for key in expected)


def test_started_contract_still_allows_operator_trial_extension(pending, operator):
    from core.forms_trial import validate_deferred_trial_edit
    activate(pending, operator)
    pending.refresh_from_db()
    validate_deferred_trial_edit(pending, {"trial_end_date": timezone.localdate()+timedelta(days=70)})


@pytest.mark.parametrize("demo", [True, False])
def test_direct_checkout_posts_cannot_bypass_setup_lock(client, pending, demo):
    pending.is_demo = demo
    pending.save()
    member = SchoolAdminMembershipFactory(school=pending)
    client.force_login(member.user)
    pricing = [{"price_id": "price_test", "plan": "starter", "interval": "month", "amount": "$49"}]
    with patch("core.services.billing_stripe.is_stripe_configured", return_value=True), \
            patch("core.services.billing_stripe.get_pricing_options", return_value=pricing), \
            patch("core.services.billing_stripe._get_stripe") as provider:
        for name in ("school_billing_checkout", "school_billing_portal"):
            response = client.post(reverse(name, args=[pending.slug]), {"price_id": "price_test"})
            assert response.status_code == 302
            assert "billing" in response.url
        for name in ("admin:billing_create_checkout", "admin:billing_portal"):
            response = client.post(reverse(name), {"price_id": "price_test"})
            assert response.status_code == 302
        provider.assert_not_called()


@pytest.mark.parametrize("demo", [True, False])
def test_demo_submissions_and_leads_write_while_real_expired_trial_blocks(client, demo):
    school = SchoolFactory(slug="enrollment-request-demo", is_demo=demo,
                           trial_started_at=timezone.now()-timedelta(days=40),
                           feature_flags={"email_notifications_enabled": False})
    from core.models import SchoolProgram
    program = SchoolProgram.objects.create(school=school, name="Test program", code="test", is_active=True)
    school.program_field_key = "interested_in"
    school.save()
    response = client.post(reverse("apply", args=[school.slug]), {
        "student_first_name": "Sample", "student_last_name": "Child",
        "interested_in": "program:test", "enrollment_type": "enroll_now",
        "contact_email": "sample@example.invalid",
    })
    if demo:
        assert response.status_code == 302, response.content.decode()[:5000]
        assert Submission.objects.filter(school=school).count() == 1
    else:
        assert "trial_expired.html" in [t.name for t in response.templates]
        assert not Submission.objects.filter(school=school).exists()
    response = client.post(reverse("lead_capture", args=[school.slug]), {
        "name": "Sample Family", "email": "family@example.invalid",
    })
    if demo:
        assert response.status_code == 302
        assert Lead.objects.filter(school=school).count() == 1
    else:
        assert "trial_expired.html" in [t.name for t in response.templates]
        assert not Lead.objects.filter(school=school).exists()


def test_inactive_demo_not_reopened_by_trial_fix(client):
    school = SchoolFactory(slug="enrollment-request-demo", is_demo=True, is_active=False)
    for name in ("apply", "lead_capture"):
        assert client.get(reverse(name, args=[school.slug])).status_code == 404


@pytest.mark.parametrize("demo", [True, False])
def test_old_demo_expiry_public_forms_vs_real_expired_trial(client, demo):
    school = SchoolFactory(slug="enrollment-request-demo", is_demo=demo,
                           trial_started_at=timezone.now()-timedelta(days=40))
    for name in ("apply", "lead_capture"):
        url = reverse(name, args=[school.slug])
        response = client.get(url)
        if demo:
            assert response.status_code == 200
            assert "trial_expired.html" not in [t.name for t in response.templates]
        else:
            assert "trial_expired.html" in [t.name for t in response.templates]


def test_demo_token_expiry_is_still_enforced(client):
    school = SchoolFactory(is_demo=True, trial_started_at=timezone.now()-timedelta(days=40))
    SchoolAdminMembershipFactory(school=school)
    token = DemoAccessToken.objects.create(school=school, expires_at=timezone.now()-timedelta(minutes=1))
    response = client.get(reverse("demo_access", args=[token.token]))
    assert "demo_expired.html" in [t.name for t in response.templates]
    assert "_auth_user_id" not in client.session
    token.expires_at = timezone.now()+timedelta(days=14)
    token.save()
    assert client.get(reverse("demo_access", args=[token.token]), follow=True).status_code == 200
    assert "_auth_user_id" in client.session


@pytest.mark.django_db(transaction=True)
def test_trial_migration_preserves_existing_schools_and_rolls_back():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes()
    previous = [("core", "0060_seed_sbmc_notification_emails")]
    try:
        executor.migrate(previous)
        historical = executor.loader.project_state(previous).apps.get_model("core", "School")
        old = historical.objects.create(slug="sbmc-migration-control", plan="custom",
            trial_started_at=NOW, feature_flags={"custom_statuses_enabled": True})
        MigrationExecutor(connection).migrate(latest)
        saved = School.objects.get(pk=old.pk)
        assert saved.trial_duration_days == 30
        assert saved.trial_requires_activation is False
        assert saved.plan == "custom" and saved.trial_started_at == NOW
        assert saved.feature_flags == {"custom_statuses_enabled": True}
        assert not AdminAuditLog.objects.filter(extra__name="customer_trial_started").exists()
        MigrationExecutor(connection).migrate(previous)
        assert historical.objects.get(pk=old.pk).feature_flags == saved.feature_flags
    finally:
        MigrationExecutor(connection).migrate(latest)
