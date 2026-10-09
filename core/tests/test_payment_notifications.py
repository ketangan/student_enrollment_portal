import copy
import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import BillingInvoice, DraftSubmission, PaymentNotification, School
from core.services.billing_invoices import handle_invoice_event
from core.services.billing_stripe import handle_subscription_deleted, handle_subscription_updated
from core.services.parent_payment_events import notify_application_payment
from core.services.payment_notifications import deliver_payment_notice, queue_payment_notice
from core.tests.factories import SchoolFactory, SchoolAdminMembershipFactory, UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def school():
    return SchoolFactory(plan="starter", stripe_customer_id="cus_school",
        stripe_subscription_id="sub_school", stripe_subscription_status="active")


@pytest.fixture
def owner(school, client):
    membership = SchoolAdminMembershipFactory(school=school)
    client.force_login(membership.user)
    return membership.user


@pytest.fixture
def provider(settings):
    settings.STRIPE_MODE = "live"
    settings.PAYMENT_ALERT_TEST_EVENTS = False
    settings.PAYMENT_ALERT_RECIPIENTS = ["ketan@mypontora.com"]
    with patch("core.services.billing_invoices.gateway._get_stripe") as mocked:
        yield mocked.return_value


def invoice_event(kind="invoice.payment_failed", *, invoice="in_first", when=None, attempt=1):
    return {"id": f"evt_{invoice}_{kind}_{attempt}", "type": kind, "livemode": True,
        "created": int((when or timezone.now()).timestamp()), "data": {"object": {
            "id": invoice, "customer": "cus_school", "subscription": "sub_school", "livemode": True,
            "status": "paid" if kind == "invoice.paid" else "open", "currency": "usd",
            "amount_due": 5000, "amount_paid": 5000 if kind == "invoice.paid" else 0,
            "attempt_count": attempt, "billing_reason": "subscription_cycle",
        }}}


def apply(provider, event, **current):
    provider.Invoice.retrieve.return_value = {**event["data"]["object"], **current}
    handle_invoice_event(event)


def notice(school, **kwargs):
    return queue_payment_notice(key="test-key", school=school, kind="Payment received", reference="pi_one",
        amount=5000, currency="usd", livemode=True, **kwargs)


def test_every_recurring_invoice_and_retry_is_notified_once(school, provider):
    for invoice in ("in_first", "in_next_month"):
        event = invoice_event("invoice.paid", invoice=invoice)
        apply(provider, event)
        event["id"] += "_duplicate_object"
        apply(provider, event)
    assert PaymentNotification.objects.count() == 2
    assert all("received" in item.subject for item in PaymentNotification.objects.all())
    school.refresh_from_db()
    assert school.payment_grace_ends_at is None


def test_failure_starts_exactly_seven_days_and_retries_do_not_extend(school, provider):
    start = timezone.now() - timedelta(days=2)
    first = invoice_event(when=start)
    apply(provider, first)
    apply(provider, first)
    apply(provider, invoice_event(attempt=2))
    school.refresh_from_db()
    expected = timezone.datetime.fromtimestamp(first["created"], tz=timezone.get_default_timezone()) + timedelta(days=7)
    assert school.payment_grace_ends_at == expected
    assert not school.is_payment_access_expired
    assert PaymentNotification.objects.count() == 2


def test_older_failure_arriving_last_does_not_extend_grace(school, provider):
    apply(provider, invoice_event(attempt=2))
    earlier = invoice_event(when=timezone.now() - timedelta(days=3))
    apply(provider, earlier)
    school.refresh_from_db()
    assert school.payment_grace_ends_at < timezone.now() + timedelta(days=4, seconds=1)


def test_paid_then_delayed_failure_does_not_suspend(school, provider):
    apply(provider, invoice_event("invoice.paid"))
    apply(provider, invoice_event(), status="paid")
    school.refresh_from_db()
    assert school.payment_grace_ends_at is None


def test_payments_clear_only_the_resolved_invoice(school, provider):
    apply(provider, invoice_event(invoice="in_old", when=timezone.now() - timedelta(days=8)))
    apply(provider, invoice_event(invoice="in_new", when=timezone.now() - timedelta(days=2)))
    apply(provider, invoice_event("invoice.paid", invoice="in_new"))
    school.refresh_from_db()
    assert school.is_payment_access_expired
    apply(provider, invoice_event("invoice.paid", invoice="in_old"))
    school.refresh_from_db()
    assert not school.is_payment_overdue


@pytest.mark.parametrize("kind,status,overdue", [
    ("invoice.payment_action_required", "open", True),
    ("invoice.finalization_failed", "draft", False),
    ("invoice.voided", "void", False),
    ("invoice.marked_uncollectible", "uncollectible", True),
])
def test_invoice_resolution_and_action_required(school, provider, kind, status, overdue):
    if kind != "invoice.finalization_failed":
        apply(provider, invoice_event())
    apply(provider, invoice_event(kind), status=status)
    school.refresh_from_db()
    assert school.is_payment_overdue == overdue


def test_card_change_and_active_subscription_do_not_clear_unpaid_invoice(school, provider):
    apply(provider, invoice_event(when=timezone.now() - timedelta(days=8)))
    handle_subscription_updated({"id": school.stripe_subscription_id, "status": "active",
                                 "default_payment_method": "pm_new"})
    school.refresh_from_db()
    assert school.is_payment_access_expired


def test_paid_invoice_does_not_reactivate_manually_disabled_school(school, provider):
    school.is_active = False
    school.save(update_fields=["is_active"])
    apply(provider, invoice_event("invoice.paid"))
    school.refresh_from_db()
    assert not school.is_active


def test_old_subscription_debt_does_not_suspend_replacement(school, provider):
    school.stripe_subscription_id = "sub_new"
    school.save(update_fields=["stripe_subscription_id"])
    apply(provider, invoice_event())
    school.refresh_from_db()
    assert school.payment_grace_ends_at is None


def test_demo_is_not_suspended(school, provider):
    school.is_demo = True
    school.save(update_fields=["is_demo"])
    apply(provider, invoice_event())
    school.refresh_from_db()
    assert not school.is_payment_overdue


def test_test_payment_does_not_affect_live_access_or_email(school, provider, settings):
    settings.STRIPE_MODE = "test"
    event = invoice_event()
    event["livemode"] = event["data"]["object"]["livemode"] = False
    apply(provider, event)
    school.refresh_from_db()
    assert school.payment_grace_ends_at is None
    assert not PaymentNotification.objects.exists()


@pytest.mark.parametrize("field,value", [("customer", "cus_else"), ("subscription", "sub_else"),
    ("id", "in_else"), ("livemode", False), ("status", "unknown")])
def test_retrieved_invoice_must_match_event(school, provider, field, value):
    with pytest.raises(ValidationError):
        apply(provider, invoice_event(), **{field: value})
    assert not BillingInvoice.objects.exists()
    assert not PaymentNotification.objects.exists()


@pytest.mark.parametrize("field,value", [("id", ""), ("livemode", False), ("livemode", None), ("account", "acct_other")])
def test_invalid_event_identity(school, provider, field, value):
    event = invoice_event()
    event[field] = value
    with pytest.raises(ValidationError):
        apply(provider, event)


def test_invoice_api_outage_rolls_back_all_changes(school, provider):
    provider.Invoice.retrieve.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError):
        handle_invoice_event(invoice_event())
    assert not BillingInvoice.objects.exists()
    assert not PaymentNotification.objects.exists()


def test_new_invoice_subscription_shape(school, provider):
    event = invoice_event()
    obj = event["data"]["object"]
    obj.pop("subscription")
    obj["parent"] = {"subscription_details": {"subscription": {"id": "sub_school"}}}
    obj["customer"] = {"id": "cus_school"}
    apply(provider, event)
    assert BillingInvoice.objects.get().subscription_id == "sub_school"


def test_invoice_before_checkout_resolves_subscription_metadata(school, provider):
    school.stripe_customer_id = school.stripe_subscription_id = ""
    school.save(update_fields=["stripe_customer_id", "stripe_subscription_id"])
    provider.Subscription.retrieve.return_value = {"customer": "cus_school", "metadata": {"school_slug": school.slug}}
    apply(provider, invoice_event())
    school.refresh_from_db()
    assert school.is_payment_overdue
    assert school.stripe_subscription_id == "sub_school"


@pytest.mark.parametrize("parent", [None, {}, {"subscription_details": None}])
def test_one_time_invoice_notifies_without_locking(school, provider, parent):
    event = invoice_event("invoice.paid")
    event["data"]["object"]["subscription"] = None
    event["data"]["object"]["parent"] = parent
    apply(provider, event)
    assert "Invoice payment received" in PaymentNotification.objects.get().subject
    assert not BillingInvoice.objects.exists()
    school.refresh_from_db()
    assert not school.is_payment_overdue


def test_blank_subscription_events_do_not_modify_school(school):
    original = SchoolFactory()
    handle_subscription_updated({"status": "active"})
    handle_subscription_deleted({})
    original.refresh_from_db()
    assert original.is_active and original.stripe_subscription_status == ""


def test_notifications_send_after_commit_deduplicate_and_support_aliases(school, settings, django_capture_on_commit_callbacks):
    settings.PAYMENT_ALERT_RECIPIENTS = ["ketan@mypontora.com", "billing@mypontora.com", "ketan@mypontora.com"]
    with django_capture_on_commit_callbacks(execute=True):
        item = notice(school)
        notice(school)
        assert not mail.outbox
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["ketan@mypontora.com", "billing@mypontora.com"]
    item.refresh_from_db()
    assert item.sent_at


def test_failed_mail_is_durable_and_retried(school):
    item = notice(school)
    with patch("core.services.payment_notifications.EmailMessage.send", side_effect=TimeoutError):
        assert not deliver_payment_notice(item.pk)
    item.refresh_from_db()
    assert item.sent_at is None and item.last_error == "TimeoutError"
    assert not deliver_payment_notice(item.pk)
    PaymentNotification.objects.filter(pk=item.pk).update(next_attempt_at=timezone.now())
    call_command("send_payment_notifications")
    item.refresh_from_db()
    assert item.sent_at and item.attempts == 2


@pytest.mark.parametrize("backend", ["console", "dummy"])
def test_fake_delivery_is_not_recorded_as_sent(school, settings, backend):
    settings.EMAIL_BACKEND = f"django.core.mail.backends.{backend}.EmailBackend"
    item = notice(school)
    with pytest.raises(CommandError):
        call_command("send_payment_notifications")
    item.refresh_from_db()
    assert item.sent_at is None


def test_invalid_recipients_do_not_silently_drop_alert(school, settings):
    settings.PAYMENT_ALERT_RECIPIENTS = []
    item = notice(school)
    assert not deliver_payment_notice(item.pk)
    item.refresh_from_db()
    assert item.last_error == "ImproperlyConfigured"
    PaymentNotification.objects.filter(pk=item.pk).update(next_attempt_at=timezone.now())
    settings.PAYMENT_ALERT_RECIPIENTS = ["invalid"]
    assert not deliver_payment_notice(item.pk)
    item.refresh_from_db()
    assert item.last_error == "ValidationError"
    PaymentNotification.objects.filter(pk=item.pk).update(next_attempt_at=timezone.now())
    settings.PAYMENT_ALERT_RECIPIENTS = ["ketan@mypontora.com"]
    assert deliver_payment_notice(item.pk)


def expire(school):
    school.payment_grace_ends_at = timezone.now() - timedelta(seconds=1)
    school.save(update_fields=["payment_grace_ends_at"])


def test_grace_banner_and_expired_access_integration(school, owner, client):
    school.payment_grace_ends_at = timezone.now() + timedelta(days=7)
    school.save(update_fields=["payment_grace_ends_at"])
    dashboard = reverse("school_dashboard", args=[school.slug])
    response = client.get(dashboard)
    assert response.status_code == 200
    assert b"Subscription payment failed" in response.content
    expire(school)
    response = client.get(dashboard)
    assert response.status_code == 302 and response.url == reverse("school_billing", args=[school.slug])
    response = client.get(response.url)
    assert response.status_code == 200
    assert b"Update card or pay invoice" in response.content
    assert response.content.count(b"Subscription payment overdue.") == 1
    assert b"Your subscription payment is overdue." not in response.content


def test_billing_portal_error_is_rendered_once(school, owner, client):
    expire(school)
    with patch("core.services.billing_stripe.create_portal_session", return_value=None):
        response = client.post(reverse("school_billing_portal", args=[school.slug]), follow=True)
    assert response.status_code == 200
    assert response.content.count(b"Could not open billing portal. Please try again.") == 1


@pytest.mark.parametrize("slug,plan", [("northside-music", "starter"), ("lakeside-preschool", "custom")])
@pytest.mark.parametrize("days_left", [5, -1])
def test_overdue_uses_existing_billing_page_for_any_school(school, owner, client, slug, plan, days_left):
    school.slug = slug
    school.display_name = slug.replace("-", " ").title()
    school.plan = plan
    school.payment_grace_ends_at = timezone.now() + timedelta(days=days_left)
    school.save()
    with patch("core.services.billing_stripe.get_pricing_options") as pricing:
        response = client.get(reverse("school_billing", args=[school.slug]), {"status": "success"})
    assert response.status_code == 200
    assert "school_admin/billing.html" in [template.name for template in response.templates]
    assert response.context["billing_state"] == "payment_overdue"
    assert response.context["can_open_portal"]
    assert response.context["plan_display"] in ("Starter", "Custom")
    assert school.display_name.encode() in response.content
    assert b"Billing &amp; Plan" in response.content and b"Current Plan" in response.content
    assert b"Update card or pay invoice" in response.content
    assert b"name=\"price_id\"" not in response.content
    assert b"billing-plan-grid\">" not in response.content
    assert b"Payment successful!" not in response.content
    assert (b"Enrollment and scheduling access is paused" in response.content) == (days_left < 0)
    assert (b"Payment due by" in response.content) == (days_left > 0)
    pricing.assert_not_called()


def test_overdue_without_customer_shows_support_instead_of_unusable_action(school, owner, client):
    expire(school)
    school.stripe_customer_id = ""
    school.save(update_fields=["stripe_customer_id"])
    response = client.get(reverse("school_billing", args=[school.slug]))
    assert response.status_code == 200
    assert not response.context["can_open_portal"]
    assert b"Contact Pontora to resolve this payment" in response.content
    assert b"Update card or pay invoice" not in response.content


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize("name", ["apply", "family_status", "school_lead_form"])
def test_public_links_block_after_grace(school, client, method, name):
    expire(school)
    args = [school.slug, "arbitrary-token"] if name == "family_status" else [school.slug]
    response = getattr(client, method)(reverse(name, args=args))
    assert response.status_code == 403
    assert b"temporarily unavailable" in response.content
    assert b"payment" not in response.content


def test_nonowner_can_read_recovery_but_cannot_change_card(school, owner, client):
    school.admin_memberships.update(role="viewer")
    expire(school)
    response = client.get(reverse("school_billing", args=[school.slug]))
    assert response.status_code == 200
    assert b"account owner" in response.content
    assert "school_admin/billing.html" in [template.name for template in response.templates]
    assert not response.context["can_open_portal"]
    assert b"Update card or pay invoice" not in response.content
    assert client.post(reverse("school_billing_portal", args=[school.slug])).status_code == 404


@pytest.mark.parametrize("role", ["owner", "editor", "viewer", None])
def test_enrollment_billing_does_not_require_scheduling(school, role):
    from core.services.billing_access import billing_role
    user = UserFactory(is_staff=True)
    if role:
        SchoolAdminMembershipFactory(school=school, user=user, role=role)
    with patch("core.services.billing_access.apps.is_installed", return_value=False):
        assert billing_role(user, school) == role


def test_billing_role_rejects_inactive_users_and_allows_operators(school):
    from django.contrib.auth.models import AnonymousUser
    from core.services.billing_access import billing_role
    assert billing_role(None, school) is None
    assert billing_role(AnonymousUser(), school) is None
    assert billing_role(UserFactory(is_active=False), school) is None
    assert billing_role(UserFactory(is_superuser=True), school) == "owner"


@pytest.mark.parametrize("booking_role", ["owner", "scheduler", "viewer", None])
def test_optional_billing_membership_lookup_is_scoped_to_school(school, booking_role):
    from core.services.billing_access import billing_role
    user = UserFactory(is_staff=False)
    with patch("core.services.billing_access.apps.is_installed", return_value=True), \
            patch("core.services.billing_access.apps.get_model") as get_model:
        model = get_model.return_value
        model.objects.filter.return_value.values_list.return_value.first.return_value = booking_role
        assert billing_role(user, school) == booking_role
        get_model.assert_called_once_with("scheduling", "SchedulingMembership")
        model.objects.filter.assert_called_once_with(
            user=user, profile__school=school, profile__enabled=True, is_active=True,
        )


def test_owner_can_change_card_during_lockout(school, owner, client):
    expire(school)
    with patch("core.services.billing_stripe.create_portal_session", return_value="https://billing.stripe.com/example") as portal:
        response = client.post(reverse("school_billing_portal", args=[school.slug]))
    assert response.status_code == 302 and response.url.startswith("https://billing.stripe.com/")
    portal.assert_called_once()
    school.refresh_from_db()
    assert school.is_payment_access_expired


def test_no_duplicate_subscription_checkout(school, owner, client):
    with patch("core.services.billing_stripe.create_checkout_session") as checkout:
        response = client.post(reverse("school_billing_checkout", args=[school.slug]), {"price_id": "price_new"})
    assert response.status_code == 302
    checkout.assert_not_called()


def test_recovery_does_not_cross_tenants(school, owner, client):
    other = SchoolFactory()
    expire(other)
    assert client.get(reverse("school_billing", args=[other.slug])).status_code == 404
    assert client.get(reverse("school_dashboard", args=[school.slug])).status_code == 200


def test_payment_restores_access_end_to_end(school, owner, client, provider):
    apply(provider, invoice_event(when=timezone.now() - timedelta(days=8)))
    assert client.get(reverse("school_dashboard", args=[school.slug])).status_code == 302
    apply(provider, invoice_event("invoice.paid"))
    assert client.get(reverse("school_dashboard", args=[school.slug])).status_code == 200


def intent_event(school, draft, kind="payment_intent.succeeded"):
    return {"id": "evt_parent", "type": kind, "livemode": True, "data": {"object": {
        "id": "pi_parent", "livemode": True, "amount": 5000, "amount_received": 5000,
        "currency": "usd", "status": "succeeded" if kind.endswith("succeeded") else "requires_payment_method",
        "metadata": {"school_slug": school.slug, "draft_token": draft.token},
    }}}


@pytest.mark.parametrize("kind", ["payment_intent.succeeded", "payment_intent.payment_failed"])
def test_parent_payments_notify_without_school_lockout(school, kind):
    draft = DraftSubmission.objects.create(school=school)
    event = intent_event(school, draft, kind)
    notify_application_payment(school, event)
    notify_application_payment(school, event)
    assert PaymentNotification.objects.count() == 1
    school.refresh_from_db()
    assert not school.is_payment_overdue and school.is_active


def signature(payload, secret):
    timestamp = str(int(time.time()))
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_parent_signed_webhook_works_without_browser_return(school, client, settings):
    draft = DraftSubmission.objects.create(school=school)
    payload = json.dumps(intent_event(school, draft)).encode()
    settings.APP_FEE_WEBHOOK_SECRETS = {school.slug: "whsec_school"}
    url = reverse("application_payment_webhook", args=[school.slug])
    response = client.post(url, payload, content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signature(payload, "whsec_school"))
    assert response.status_code == 200
    assert PaymentNotification.objects.count() == 1
    response = client.post(url, payload, content_type="application/json",
        HTTP_STRIPE_SIGNATURE=signature(payload, "whsec_wrong_school"))
    assert response.status_code == 400


def test_platform_invoice_webhook_dispatch(school, provider, client):
    import stripe
    payload = invoice_event()
    provider.Invoice.retrieve.return_value = payload["data"]["object"]
    with patch("core.views_billing.construct_webhook_event", return_value=stripe.Event.construct_from(payload, None)):
        response = client.post(reverse("stripe_webhook"), b"{}", content_type="application/json")
    assert response.status_code == 200
    assert BillingInvoice.objects.count() == 1


def test_unrelated_parent_events_are_ignored(school):
    notify_application_payment(school, {"type": "other"})
    event = intent_event(school, DraftSubmission.objects.create(school=school))
    event["data"]["object"]["metadata"] = {}
    notify_application_payment(school, event)
    assert not PaymentNotification.objects.exists()


@pytest.mark.parametrize("mutation", ["missing_draft", "wrong_mode", "wrong_account", "not_paid"])
def test_parent_event_binding_validation(school, mutation):
    draft = DraftSubmission.objects.create(school=school)
    event = intent_event(school, draft)
    obj = event["data"]["object"]
    if mutation == "missing_draft":
        obj["metadata"]["draft_token"] = "missing"
    elif mutation == "wrong_mode":
        obj["livemode"] = False
    elif mutation == "wrong_account":
        event["account"] = "acct_other"
    else:
        obj["status"] = "requires_action"
    with pytest.raises(ValidationError):
        notify_application_payment(school, event)


def test_missing_webhook_config_and_oversized_payload_rejected(school, client):
    url = reverse("application_payment_webhook", args=[school.slug])
    assert client.post(url, b"{}", content_type="application/json").status_code == 400
    assert client.post(url, b"x" * 262145, content_type="application/json").status_code == 400


def test_empty_delivery_response_remains_pending(school):
    item = notice(school)
    with patch("core.services.payment_notifications.EmailMessage.send", return_value=0):
        assert not deliver_payment_notice(item.pk)
    item.refresh_from_db()
    assert item.last_error == "RuntimeError" and not item.sent_at


def test_customer_mismatch_rolls_back(school, provider):
    school.stripe_customer_id = "cus_wrong"
    school.save(update_fields=["stripe_customer_id"])
    with pytest.raises(ValidationError):
        apply(provider, invoice_event())


def test_ambiguous_school_binding_is_rejected(school, provider):
    SchoolFactory(stripe_subscription_id="sub_school")
    with pytest.raises(ValidationError):
        apply(provider, invoice_event())


def test_bad_email_config_does_not_lose_payment_state(school, provider, settings):
    settings.PAYMENT_ALERT_RECIPIENTS = []
    apply(provider, invoice_event())
    assert BillingInvoice.objects.get().status == "open"
    assert PaymentNotification.objects.count() == 1


def test_subscription_metadata_must_match_customer(school, provider):
    school.stripe_customer_id = school.stripe_subscription_id = ""
    school.save(update_fields=["stripe_customer_id", "stripe_subscription_id"])
    provider.Subscription.retrieve.return_value = {"customer": "cus_wrong", "metadata": {"school_slug": school.slug}}
    with pytest.raises(ValidationError):
        apply(provider, invoice_event())


def test_invoice_must_have_customer(school, provider):
    event = invoice_event()
    event["data"]["object"].pop("customer")
    with pytest.raises(ValidationError):
        apply(provider, event)


def test_invoice_binding_cannot_move_to_other_school(school, provider):
    BillingInvoice.objects.create(school=SchoolFactory(), provider_id="in_first", subscription_id="sub_school",
                                  livemode=True, status="open")
    with pytest.raises(ValidationError):
        apply(provider, invoice_event())


def test_unmatched_invoice_still_alerts_operator(provider):
    event = invoice_event("invoice.paid")
    event["data"]["object"]["subscription"] = None
    apply(provider, event)
    assert PaymentNotification.objects.get().school_id is None


def test_exact_deadline_and_superuser_bypass(school, owner, client):
    now = timezone.now()
    school.payment_grace_ends_at = now
    school.save(update_fields=["payment_grace_ends_at"])
    with patch("core.models.timezone.now", return_value=now):
        assert school.is_payment_access_expired
    owner.is_superuser = True
    owner.save(update_fields=["is_superuser"])
    assert client.get(reverse("school_dashboard", args=[school.slug])).status_code == 200


def test_legacy_admin_and_download_cannot_bypass_lock(school, owner, client):
    from core.tests.factories import SubmissionFactory, SubmissionFileFactory
    expire(school)
    submission = SubmissionFactory(school=school)
    upload = SubmissionFileFactory(submission=submission)
    assert client.get(reverse("admin_download_submission_file", args=[upload.pk])).status_code == 404
    response = client.get(reverse("admin:core_submission_changelist"))
    assert response.status_code != 200


def test_legacy_billing_nonowner_cannot_change_card(school, owner, client):
    school.admin_memberships.update(role="viewer")
    assert client.post(reverse("admin:billing_portal")).status_code == 404


def test_healthy_paid_custom_plan_can_update_card(school, owner, client):
    school.plan = "custom"
    school.save(update_fields=["plan"])
    page = client.get(reverse("school_billing", args=[school.slug]))
    assert b"Manage Billing" in page.content
    with patch("core.services.billing_stripe.create_portal_session", return_value="https://billing.stripe.com/example"):
        assert client.post(reverse("school_billing_portal", args=[school.slug])).url.startswith("https://billing.stripe.com")


def test_old_subscription_update_cannot_change_replacement(school):
    handle_subscription_updated({"id": "sub_old", "status": "canceled", "metadata": {"school_slug": school.slug}})
    school.refresh_from_db()
    assert school.stripe_subscription_status == "active" and school.is_active


def test_checkout_rebind_recomputes_grace(school, provider):
    from core.services.billing_stripe import handle_checkout_completed
    apply(provider, invoice_event(when=timezone.now() - timedelta(days=8)))
    # Outstanding debt must be resolved before allowing a replacement checkout.
    apply(provider, invoice_event("invoice.paid"))
    school.stripe_subscription_status = "canceled"
    school.save(update_fields=["stripe_subscription_status"])
    handle_checkout_completed({"metadata": {"school_slug": school.slug}, "customer": "cus_school",
        "subscription": "sub_replacement", "line_items": {"data": [{"price": {"id": "unknown"}}]}})
    school.refresh_from_db()
    assert school.stripe_subscription_id == "sub_replacement" and not school.is_payment_overdue


def test_delayed_checkout_cannot_replace_current_subscription(school, provider):
    from core.services.billing_stripe import handle_checkout_completed
    apply(provider, invoice_event(when=timezone.now() - timedelta(days=8)))
    handle_checkout_completed({"metadata": {"school_slug": school.slug}, "customer": "cus_school",
        "subscription": "sub_old", "line_items": {"data": [{"price": {"id": "unknown"}}]}})
    school.refresh_from_db()
    assert school.stripe_subscription_id == "sub_school" and school.is_payment_access_expired


@pytest.mark.django_db(transaction=True)
def test_concurrent_webhook_delivery_has_one_invoice_and_email(provider):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import close_old_connections
    school = SchoolFactory(plan="starter", stripe_subscription_id="sub_school", stripe_customer_id="cus_school")
    event = invoice_event()
    provider.Invoice.retrieve.return_value = event["data"]["object"]
    barrier = Barrier(2)
    def deliver():
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            handle_invoice_event(copy.deepcopy(event))
        finally:
            close_old_connections()
    with patch("core.services.payment_notifications.EmailMessage.send", return_value=1) as send:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: deliver(), range(2)))
    assert BillingInvoice.objects.count() == PaymentNotification.objects.count() == 1
    assert send.call_count == 1
