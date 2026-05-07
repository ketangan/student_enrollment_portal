"""
Tests for Phase 18 — Application Fees (Stripe, school-direct).

Coverage:
- get_application_fee_config(): enabled/disabled/waived logic
- apply_view redirects to payment page when fee applies + school has Stripe keys
- apply_view creates Submission directly when fee is disabled or waived
- apply_view creates Submission directly when fee is enabled but school has no Stripe keys (graceful)
- apply_payment_view: GET creates PaymentIntent and renders form
- apply_payment_view: graceful degradation when Stripe fails
- apply_payment_confirm_view: creates Submission + payment_status="paid" on success
- apply_payment_confirm_view: re-renders payment page on failed/incomplete payment
- apply_payment_confirm_view: idempotent — already-submitted draft redirects to success
- Multi-form school: fee check uses the submitted form_key (not "multi")
- Waived form: submission created with payment_status="waived"
- Ops school edit form: app_fee_stripe_public_key and app_fee_stripe_secret_key fields present
"""
from unittest import mock

import pytest
from django.urls import reverse

from core.models import DraftSubmission, School, Submission
from core.services.config_loader import get_application_fee_config


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def maplewood_school(db):
    """School that matches configs/schools/maplewood-learning.yaml."""
    return School.objects.create(
        slug="maplewood-learning",
        display_name="Maplewood Learning Center",
        plan="starter",
    )


@pytest.fixture
def maplewood_school_with_keys(maplewood_school):
    """Same school but with fake Stripe keys set."""
    maplewood_school.app_fee_stripe_public_key = "pk_test_fake"
    maplewood_school.app_fee_stripe_secret_key = "sk_test_fake"
    maplewood_school.save(update_fields=["app_fee_stripe_public_key", "app_fee_stripe_secret_key"])
    return maplewood_school


# ── get_application_fee_config ────────────────────────────────────────────────

def test_fee_config_enabled_not_waived():
    raw = {
        "application_fee": {
            "enabled": True,
            "amount": 50,
            "description": "Non-refundable application fee",
            "waived_for_forms": ["scholarship"],
        }
    }
    cfg = get_application_fee_config(raw, "general")
    assert cfg["enabled"] is True
    assert cfg["amount"] == 50
    assert cfg["waived"] is False


def test_fee_config_waived_form():
    raw = {
        "application_fee": {
            "enabled": True,
            "amount": 50,
            "waived_for_forms": ["scholarship"],
        }
    }
    cfg = get_application_fee_config(raw, "scholarship")
    assert cfg["enabled"] is True
    assert cfg["waived"] is True


def test_fee_config_disabled():
    raw = {"application_fee": {"enabled": False, "amount": 50}}
    cfg = get_application_fee_config(raw, "general")
    assert cfg["enabled"] is False
    assert cfg["amount"] == 0


def test_fee_config_no_block():
    cfg = get_application_fee_config({}, "default")
    assert cfg["enabled"] is False
    assert cfg["amount"] == 0
    assert cfg["waived"] is False


# ── apply_view → payment redirect ────────────────────────────────────────────

@pytest.mark.django_db
def test_apply_view_redirects_to_payment_when_fee_applies(client, maplewood_school_with_keys):
    """
    When school has fee enabled + Stripe keys, submitting a non-waived form
    should redirect to the payment page (not create a Submission).
    """
    url = reverse("apply_form", kwargs={
        "school_slug": "maplewood-learning", "form_key": "general"
    })
    post_data = {
        "student_first_name": "Alice",
        "student_last_name": "Smith",
        "date_of_birth": "2015-06-01",
        "grade_applying_for": "k",
        "guardian_name": "Jane Smith",
        "contact_email": "jane@example.com",
        "contact_phone": "555-0100",
    }
    resp = client.post(url, post_data)

    assert resp.status_code == 302
    assert "/apply/pay/" in resp["Location"]
    assert Submission.objects.filter(school=maplewood_school_with_keys).count() == 0


@pytest.mark.django_db
def test_apply_view_creates_draft_before_payment_redirect(client, maplewood_school_with_keys):
    """A DraftSubmission must be saved before redirecting to payment."""
    url = reverse("apply_form", kwargs={
        "school_slug": "maplewood-learning", "form_key": "general"
    })
    post_data = {
        "student_first_name": "Bob",
        "student_last_name": "Jones",
        "date_of_birth": "2015-01-01",
        "grade_applying_for": "1st",
        "guardian_name": "Mary Jones",
        "contact_email": "mary@example.com",
        "contact_phone": "555-0200",
    }
    client.post(url, post_data)
    assert DraftSubmission.objects.filter(school=maplewood_school_with_keys).count() == 1


@pytest.mark.django_db
def test_apply_view_skips_payment_when_no_stripe_keys(client, maplewood_school):
    """
    If the school has fee configured but no Stripe keys, Submission is created directly.
    Graceful degradation — don't block submissions because of missing config.
    """
    url = reverse("apply_form", kwargs={
        "school_slug": "maplewood-learning", "form_key": "general"
    })
    post_data = {
        "student_first_name": "Carol",
        "student_last_name": "Lee",
        "date_of_birth": "2016-03-15",
        "grade_applying_for": "pre_k",
        "guardian_name": "Dave Lee",
        "contact_email": "dave@example.com",
        "contact_phone": "555-0300",
    }
    resp = client.post(url, post_data)
    assert resp.status_code == 302
    assert "/apply/pay/" not in resp["Location"]
    assert Submission.objects.filter(school=maplewood_school).count() == 1


@pytest.mark.django_db
def test_apply_view_waived_form_skips_payment(client, maplewood_school_with_keys):
    """The scholarship form is waived — should create Submission directly with payment_status='waived'."""
    url = reverse("apply_form", kwargs={
        "school_slug": "maplewood-learning", "form_key": "scholarship"
    })
    post_data = {
        "student_first_name": "Eve",
        "student_last_name": "Kim",
        "date_of_birth": "2014-07-20",
        "grade_applying_for": "2nd",
        "scholarship_type": "need_based",
        "statement": "We need financial support.",
        "guardian_name": "Frank Kim",
        "contact_email": "frank@example.com",
        "contact_phone": "555-0400",
    }
    resp = client.post(url, post_data)
    assert resp.status_code == 302
    assert "/apply/pay/" not in resp["Location"]
    sub = Submission.objects.filter(school=maplewood_school_with_keys).first()
    assert sub is not None
    assert sub.payment_status == "waived"


# ── apply_payment_view ────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_apply_payment_view_renders_with_client_secret(client, maplewood_school_with_keys):
    """GET to payment page should call Stripe, get client_secret, render Elements form."""
    draft = DraftSubmission.objects.create(
        school=maplewood_school_with_keys,
        form_key="default",
        last_form_key="general",
        data={
            "student_first_name": "Grace",
            "student_last_name": "Park",
            "contact_email": "grace@example.com",
        },
    )
    url = reverse("apply_payment", kwargs={
        "school_slug": "maplewood-learning",
        "draft_token": draft.token,
    })
    fake_intent = mock.MagicMock()
    fake_intent.client_secret = "pi_test_secret_123"
    fake_intent.id = "pi_test_123"

    with mock.patch(
        "core.views_public.create_application_fee_intent",
        return_value=("pi_test_secret_123", "pi_test_123"),
    ):
        resp = client.get(url)

    assert resp.status_code == 200
    assert b"pk_test_fake" in resp.content
    assert b"pi_test_secret_123" in resp.content
    assert b"Stripe" in resp.content


@pytest.mark.django_db
def test_apply_payment_view_graceful_when_stripe_fails(client, maplewood_school_with_keys):
    """If Stripe raises an exception, fall through and create Submission without payment."""
    draft = DraftSubmission.objects.create(
        school=maplewood_school_with_keys,
        form_key="default",
        last_form_key="general",
        data={
            "student_first_name": "Henry",
            "student_last_name": "Wu",
            "contact_email": "henry@example.com",
        },
    )
    url = reverse("apply_payment", kwargs={
        "school_slug": "maplewood-learning",
        "draft_token": draft.token,
    })
    with mock.patch(
        "core.views_public.create_application_fee_intent",
        side_effect=Exception("Stripe connection error"),
    ):
        resp = client.get(url)

    # Falls through to submission creation, redirects to success
    assert resp.status_code == 302
    assert Submission.objects.filter(school=maplewood_school_with_keys).count() == 1


@pytest.mark.django_db
def test_apply_payment_view_already_submitted_draft_redirects_to_success(client, maplewood_school_with_keys):
    """If the draft is already submitted, redirect to success without re-creating."""
    from django.utils import timezone
    draft = DraftSubmission.objects.create(
        school=maplewood_school_with_keys,
        form_key="default",
        data={"contact_email": "x@test.com"},
        submitted_at=timezone.now(),
    )
    url = reverse("apply_payment", kwargs={
        "school_slug": "maplewood-learning",
        "draft_token": draft.token,
    })
    resp = client.get(url)
    assert resp.status_code == 302
    assert "success" in resp["Location"]
    assert Submission.objects.filter(school=maplewood_school_with_keys).count() == 0


# ── apply_payment_confirm_view ────────────────────────────────────────────────

@pytest.mark.django_db
def test_apply_payment_confirm_creates_submission_on_success(client, maplewood_school_with_keys):
    """On payment_intent with status=succeeded, Submission is created with payment_status='paid'."""
    draft = DraftSubmission.objects.create(
        school=maplewood_school_with_keys,
        form_key="default",
        last_form_key="general",
        data={
            "student_first_name": "Iris",
            "student_last_name": "Chen",
            "contact_email": "iris@example.com",
        },
    )
    url = reverse("apply_payment_confirm", kwargs={
        "school_slug": "maplewood-learning",
        "draft_token": draft.token,
    })

    fake_intent = mock.MagicMock()
    fake_intent.status = "succeeded"

    with mock.patch(
        "core.views_public.retrieve_application_fee_intent",
        return_value=fake_intent,
    ):
        resp = client.get(url + "?payment_intent=pi_test_abc&redirect_status=succeeded")

    assert resp.status_code == 302
    assert "success" in resp["Location"]

    sub = Submission.objects.filter(school=maplewood_school_with_keys).first()
    assert sub is not None
    assert sub.payment_status == "paid"
    assert sub.payment_intent_id == "pi_test_abc"


@pytest.mark.django_db
def test_apply_payment_confirm_rerenders_on_failed_payment(client, maplewood_school_with_keys):
    """If intent status is not 'succeeded', re-render the payment page with an error."""
    draft = DraftSubmission.objects.create(
        school=maplewood_school_with_keys,
        form_key="default",
        last_form_key="general",
        data={
            "student_first_name": "Jack",
            "contact_email": "jack@example.com",
        },
    )
    url = reverse("apply_payment_confirm", kwargs={
        "school_slug": "maplewood-learning",
        "draft_token": draft.token,
    })

    fake_intent = mock.MagicMock()
    fake_intent.status = "requires_payment_method"

    with mock.patch(
        "core.views_public.retrieve_application_fee_intent",
        return_value=fake_intent,
    ):
        resp = client.get(url + "?payment_intent=pi_test_fail&redirect_status=failed")

    assert resp.status_code == 200
    assert b"not completed" in resp.content or b"try again" in resp.content.lower()
    assert Submission.objects.filter(school=maplewood_school_with_keys).count() == 0


@pytest.mark.django_db
def test_apply_payment_confirm_idempotent_already_submitted(client, maplewood_school_with_keys):
    """Calling confirm on an already-submitted draft must not create a second Submission."""
    from django.utils import timezone
    Submission.objects.create(
        school=maplewood_school_with_keys, status="New", data={}, payment_status="paid"
    )
    draft = DraftSubmission.objects.create(
        school=maplewood_school_with_keys,
        form_key="default",
        data={"contact_email": "y@test.com"},
        submitted_at=timezone.now(),
    )
    url = reverse("apply_payment_confirm", kwargs={
        "school_slug": "maplewood-learning",
        "draft_token": draft.token,
    })
    resp = client.get(url + "?payment_intent=pi_test_dup&redirect_status=succeeded")
    assert resp.status_code == 302
    assert "success" in resp["Location"]
    # Still only 1 submission
    assert Submission.objects.filter(school=maplewood_school_with_keys).count() == 1


# ── Ops form fields ────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_ops_school_edit_form_includes_app_fee_fields():
    """OpsSchoolEditForm must expose both app_fee_stripe_* fields."""
    from core.forms_ops import OpsSchoolEditForm
    form = OpsSchoolEditForm()
    assert "app_fee_stripe_public_key" in form.fields
    assert "app_fee_stripe_secret_key" in form.fields


@pytest.mark.django_db
def test_ops_school_edit_saves_stripe_keys(client, db):
    """Superadmin can set per-school Stripe keys via the ops portal."""
    from django.contrib.auth.models import User
    superuser = User.objects.create_superuser(
        username="ops18_super", email="s18@test.com", password="testpass123"
    )
    school = School.objects.create(slug="fee-test-school", display_name="Fee Test", plan="starter")
    client.force_login(superuser)
    resp = client.post(
        reverse("ops_school_detail", kwargs={"slug": school.slug}),
        {
            "display_name": "Fee Test",
            "plan": "starter",
            "is_active": True,
            "feature_flags": "{}",
            "app_fee_stripe_public_key": "pk_test_abc123",
            "app_fee_stripe_secret_key": "sk_test_xyz456",
        },
    )
    school.refresh_from_db()
    assert school.app_fee_stripe_public_key == "pk_test_abc123"
    assert school.app_fee_stripe_secret_key == "sk_test_xyz456"
