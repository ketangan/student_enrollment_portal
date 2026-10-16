"""Optional scheduling/payment integration with shared platform billing."""
from datetime import timedelta
import json
from unittest.mock import patch
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from core.models import PaymentNotification
from core.services.customer_terms import prepare_customer_terms
from core.payments.notifications import notify_booking_failure
from core.tests.factories import SchoolFactory, SchoolAdminMembershipFactory, UserFactory
from core.tests.test_payment_notifications import school, owner, expire, signature
from core.tests.test_customer_trial_activation import operator

pytestmark = pytest.mark.django_db


def test_unrelated_booking_event_ignored():
    assert not notify_booking_failure({"type": "other"})


def test_customer_terms_do_not_enable_scheduling(operator):
    from scheduling.models import SchedulingProfile
    school = SchoolFactory(slug="kidworks-childrens-center", is_demo=True)
    prepare_customer_terms(school_id=school.pk, actor=operator)
    assert not SchedulingProfile.objects.exists()


@pytest.fixture
def booking_attempt(school):
    from core.payments.models import PaymentAccount, PaymentAttempt, PaymentOrder
    account = PaymentAccount.objects.create(school=school, reference="acct_booking", enabled=True)
    order = PaymentOrder.objects.create(school=school, purpose="booking", source_key="test-reservation",
        total_cents=10000, deposit_cents=2500, account=account, merchant_reference=account.reference)
    return PaymentAttempt.objects.create(order=order, account=account, amount_cents=2500,
        success_url="http://localhost/return/", cancel_url="http://localhost/return/",
        expires_at=timezone.now() + timedelta(minutes=35))


def booking_failure(attempt):
    return {"id": "evt_booking_failure", "type": "payment_intent.payment_failed", "livemode": False,
        "account": "acct_booking", "data": {"object": {
            "id": "pi_booking", "livemode": False, "amount": 2500, "currency": "usd",
            "metadata": {"payment_attempt": str(attempt.public_id), "payment_order": str(attempt.order.public_id),
                         "purpose": "booking"},
        }}}


def test_booking_failures_are_alerts_not_cancellations(booking_attempt, settings, client):
    settings.PAYMENT_ALERT_TEST_EVENTS = True
    settings.PAYMENTS_STRIPE_TEST_SECRET_KEY = "sk_test_isolated"
    settings.PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET = "whsec_booking"
    event = booking_failure(booking_attempt)
    payload = json.dumps(event).encode()
    for _ in range(2):
        response = client.post(reverse("scheduling:stripe_test_webhook"), payload,
            content_type="application/json", HTTP_STRIPE_SIGNATURE=signature(payload, "whsec_booking"))
        assert response.status_code == 200
    assert PaymentNotification.objects.count() == 1
    assert PaymentNotification.objects.get().subject.startswith("[TEST]")
    booking_attempt.refresh_from_db()
    assert booking_attempt.state == "pending" and booking_attempt.entry_id is None
    assert not booking_attempt.order.school.is_payment_overdue


def test_booking_verified_settlement_notifies_once(booking_attempt, settings):
    from core.payments.checkout import apply_snapshot
    settings.PAYMENT_ALERT_TEST_EVENTS = True
    item = booking_attempt
    session = {"id": "cs_test_local", "mode": "payment", "livemode": False, "currency": "usd",
        "payment_method_types": ["card"],
        "amount_total": 2500, "status": "complete", "payment_status": "paid", "payment_intent": "pi_booking",
        "metadata": {"payment_attempt": str(item.public_id), "payment_order": str(item.order.public_id), "purpose": "booking"}}
    apply_snapshot(item, session)
    apply_snapshot(item, session)
    assert PaymentNotification.objects.count() == 1
    assert "$25.00 USD" in PaymentNotification.objects.get().body


@pytest.mark.parametrize("field,value", [("account", "acct_other"), ("livemode", True), ("id", "")])
def test_booking_failure_rejects_wrong_binding(booking_attempt, field, value):
    event = booking_failure(booking_attempt)
    event[field] = value
    with pytest.raises(ValidationError):
        notify_booking_failure(event)
    assert not PaymentNotification.objects.exists()


def test_booking_failure_without_attempt_ignored_and_unknown_attempt_rejected(booking_attempt):
    import uuid
    event = booking_failure(booking_attempt)
    event["data"]["object"]["metadata"] = {}
    assert notify_booking_failure(event)
    event["data"]["object"]["metadata"] = {"payment_attempt": str(uuid.uuid4())}
    with pytest.raises(ValidationError):
        notify_booking_failure(event)


@pytest.mark.parametrize("role", ["owner", "scheduler", "viewer"])
def test_standalone_booking_users_have_recovery_without_enrollment_access(school, client, role):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    user = UserFactory(is_staff=False)
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    SchedulingMembership.objects.create(profile=profile, user=user, role=role)
    expire(school)
    client.force_login(user)
    response = client.get(reverse("scheduling:workspace", args=[school.slug]))
    assert response.status_code == 302 and response.url == reverse("school_billing", args=[school.slug])
    recovery = client.get(response.url)
    assert recovery.status_code == 200
    assert "school_admin/billing.html" in [template.name for template in recovery.templates]
    assert (b"Update card or pay invoice" in recovery.content) == (role == "owner")
    assert b">Submissions</a>" not in recovery.content
    assert client.get(reverse("school_dashboard", args=[school.slug])).status_code == 404
    with patch("core.services.billing_stripe.create_portal_session", return_value="https://billing.stripe.com/example") as portal:
        result = client.post(reverse("school_billing_portal", args=[school.slug]))
    assert result.status_code == (302 if role == "owner" else 404)
    assert portal.called == (role == "owner")


def test_booking_banner_before_deadline_and_public_lock_after(school, owner, client):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    SchedulingMembership.objects.create(profile=profile, user=owner, role="owner")
    school.payment_grace_ends_at = timezone.now() + timedelta(days=7)
    school.save(update_fields=["payment_grace_ends_at"])
    response = client.get(reverse("scheduling:workspace", args=[school.slug]))
    assert response.status_code == 200 and b"Subscription payment failed" in response.content
    expire(school)
    response = client.post(reverse("scheduling:workspace", args=[school.slug]))
    assert response.status_code == 302
    client.logout()
    response = client.get(reverse("scheduling:public_schedule", args=[school.slug]))
    assert response.status_code == 403


def test_disabled_booking_membership_does_not_grant_billing(school, client):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    user = UserFactory()
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    SchedulingMembership.objects.create(profile=profile, user=user, role="owner", is_active=False)
    expire(school)
    client.force_login(user)
    assert client.get(reverse("school_billing", args=[school.slug])).status_code == 404


@pytest.mark.parametrize("role", ["owner", "scheduler", "viewer"])
def test_booking_settings_link_to_shared_billing_for_owner_only(client, role):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    school = SchoolFactory(plan="trial", is_active=True, is_demo=False,
                           trial_started_at=timezone.now(), trial_end_date=None)
    user = UserFactory(is_staff=False)
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    SchedulingMembership.objects.create(profile=profile, user=user, role=role)
    client.force_login(user)
    response = client.get(reverse("scheduling:locations", args=[school.slug]))
    assert response.status_code == 200
    tabs = response.context["booking_navigation"]["section"]["tabs"]
    billing_url = reverse("scheduling:school_billing", args=[school.slug])
    assert any(tab["url"] == billing_url for tab in tabs) == (role == "owner")
    billing = client.get(billing_url)
    assert billing.status_code == (200 if role == "owner" else 404)
    if role == "owner":
        assert "school_admin/billing.html" in [template.name for template in billing.templates]
        assert b"days remaining" in billing.content
        assert b">Submissions</a>" not in billing.content
        assert b"Agreed future upgrade" not in billing.content
        assert b"Back to settings" in billing.content
        assert reverse("scheduling:locations", args=[school.slug]).encode() in billing.content


@pytest.mark.parametrize("superadmin", [False, True])
@pytest.mark.parametrize("demo", [False, True])
def test_booking_billing_uses_booking_shell_for_owner_and_superadmin(client, superadmin, demo):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    school = SchoolFactory(is_demo=demo, is_active=True, plan="trial", trial_started_at=timezone.now())
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    user = UserFactory(is_superuser=superadmin, is_staff=superadmin)
    if not superadmin:
        SchedulingMembership.objects.create(profile=profile, user=user, role="owner")
    client.force_login(user)
    response = client.get(reverse("scheduling:school_billing", args=[school.slug]))
    assert response.status_code == 200
    assert "scheduling/base.html" in [t.name for t in response.templates]
    assert b'aria-label="Bookings"' in response.content
    assert b">Submissions</a>" not in response.content
    assert b">Leads</a>" not in response.content
    assert response.context["booking_navigation"]["section"]["key"] == "settings"
    assert any(t["key"] == "billing" and t["active"] for t in response.context["booking_navigation"]["section"]["tabs"])
    assert reverse("scheduling:locations", args=[school.slug]).encode() in response.content


@pytest.mark.parametrize("route", ["school_billing", "school_billing_checkout", "school_billing_portal"])
def test_booking_billing_routes_deny_other_tenants_and_enrollment_only_users(client, route):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    school, other = SchoolFactory(is_demo=True), SchoolFactory(is_demo=True)
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    SchedulingProfile.objects.create(school=other, enabled=True)
    user = UserFactory(is_staff=False)
    SchedulingMembership.objects.create(profile=profile, user=user, role="owner")
    client.force_login(user)
    method = client.get if route == "school_billing" else client.post
    assert method(reverse("scheduling:" + route, args=[other.slug])).status_code == 404
    assert client.get(reverse("school_dashboard", args=[school.slug])).status_code == 404
    enrollment = SchoolAdminMembershipFactory(school=school, role="owner")
    client.force_login(enrollment.user)
    assert method(reverse("scheduling:" + route, args=[school.slug])).status_code == 404


def test_booking_overdue_portal_preserves_booking_return_and_owner_guard(school, client, settings):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    settings.APP_BASE_URL = "http://testserver"
    user = UserFactory(is_staff=False)
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    member = SchedulingMembership.objects.create(profile=profile, user=user, role="owner")
    expire(school)
    client.force_login(user)
    billing = reverse("scheduling:school_billing", args=[school.slug])
    assert client.get(billing).status_code == 200
    with patch("core.services.billing_stripe.create_portal_session", return_value="https://billing.stripe.com/example") as portal:
        response = client.post(reverse("scheduling:school_billing_portal", args=[school.slug]))
        assert response.status_code == 302
        assert portal.call_args.kwargs["return_url"] == "http://testserver" + billing
        member.role = "viewer"
        member.save(update_fields=["role"])
        assert client.post(reverse("scheduling:school_billing_portal", args=[school.slug])).status_code == 404
        assert portal.call_count == 1


def test_booking_demo_checkout_stays_on_demo_host(client, settings):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    settings.DEMO_BASE_URL = "https://demo.example.test"
    settings.APP_BASE_URL = "https://app.example.test"
    settings.ALLOWED_HOSTS = ["demo.example.test"]
    school = SchoolFactory(is_demo=True)
    profile = SchedulingProfile.objects.create(school=school, enabled=True)
    user = UserFactory(is_staff=False)
    SchedulingMembership.objects.create(profile=profile, user=user, role="owner")
    client.force_login(user)
    response = client.post(reverse("scheduling:school_billing_checkout", args=[school.slug]), HTTP_HOST="demo.example.test", secure=True)
    assert response.url == "https://demo.example.test" + reverse("scheduling:school_billing", args=[school.slug])


def test_starter_package_preserves_export_and_opted_in_visits(client, operator):
    from scheduling.models import SchedulingProfile, SchedulingMembership
    from scheduling.navigation import school_entry
    school = SchoolFactory(slug="kidworks-childrens-center", is_demo=True)
    prepare_customer_terms(school_id=school.pk, actor=operator)
    school.refresh_from_db()
    school.is_demo = False
    school.plan = "starter"
    school.save()
    member = SchoolAdminMembershipFactory(school=school)
    profile = SchedulingProfile.objects.create(school=school, enabled=True, capabilities=["visits"])
    SchedulingMembership.objects.create(profile=profile, user=member.user, role="owner")
    assert any(link["label"] == "Tours and playdates" for link in school_entry(member.user, school)["links"])
    client.force_login(member.user)
    for name in ("school_dashboard", "school_leads", "school_submission_export"):
        response = client.get(reverse(name, args=[school.slug]))
        assert response.status_code == 200
    assert client.get(reverse("scheduling:visits", args=[school.slug])).status_code == 200
    profile.enabled = False
    profile.save()
    assert school_entry(member.user, school) is None
