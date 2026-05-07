"""
Tests for Phase 18 — Application Fees (Stripe, school-direct).

Coverage:
- get_application_fee_config(): enabled/disabled/waived logic
- apply_view wizard: all 3 steps redirect to payment on final submit
- apply_view creates Submission directly when no Stripe keys (graceful degradation)
- apply_view creates Submission with payment_status="waived" when fee is waived
- apply_payment_view: GET creates PaymentIntent and renders Stripe Elements form
- apply_payment_view: graceful degradation when Stripe fails
- apply_payment_confirm_view: creates Submission + payment_status="paid" on success
- apply_payment_confirm_view: re-renders payment page on failed/incomplete payment
- apply_payment_confirm_view: idempotent — already-submitted draft redirects to success
- Ops school edit form: app_fee_stripe_* fields present and saveable
"""
from unittest import mock

import pytest
from django.urls import reverse

from core.models import DraftSubmission, School, Submission
from core.services.config_loader import get_application_fee_config


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def maplewood_school(db):
    """School matching configs/schools/maplewood-learning.yaml (3-step wizard)."""
    return School.objects.create(
        slug="maplewood-learning",
        display_name="Maplewood Learning Center",
        plan="starter",
        feature_flags={"multi_form_enabled": True},
    )


@pytest.fixture
def maplewood_school_with_keys(maplewood_school):
    """Same school but with per-school Stripe test keys."""
    maplewood_school.app_fee_stripe_public_key = "pk_test_fake"
    maplewood_school.app_fee_stripe_secret_key = "sk_test_fake"
    maplewood_school.save(update_fields=["app_fee_stripe_public_key", "app_fee_stripe_secret_key"])
    return maplewood_school


# ── Helpers ───────────────────────────────────────────────────────────────────

def _post_wizard(client, school_slug):
    """
    POST through all 3 wizard steps (student → program → contact).
    Returns the final response (from the contact/submit step).
    Session is maintained by the test client between steps.
    """
    client.post(
        reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": "student"}),
        {"student_first_name": "Alice", "student_last_name": "Smith",
         "date_of_birth": "2015-06-01", "grade_applying_for": "k"},
    )
    client.post(
        reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": "program"}),
        {"program_type": "general"},
    )
    return client.post(
        reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": "contact"}),
        {"guardian_name": "Jane Smith", "contact_email": "jane@example.com",
         "contact_phone": "555-0100"},
    )


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


# ── apply_view wizard → payment redirect ──────────────────────────────────────

@pytest.mark.django_db
def test_apply_view_wizard_redirects_to_payment_on_final_step(client, maplewood_school_with_keys):
    """
    Posting through all 3 wizard steps: final step should redirect to the payment
    page (not create a Submission), because fee is enabled and Stripe keys are set.
    """
    resp = _post_wizard(client, "maplewood-learning")

    assert resp.status_code == 302
    assert "/apply/pay/" in resp["Location"]
    assert Submission.objects.filter(school=maplewood_school_with_keys).count() == 0


@pytest.mark.django_db
def test_apply_view_wizard_creates_draft_before_payment_redirect(client, maplewood_school_with_keys):
    """A DraftSubmission is saved at each step; one exists by the time payment redirect fires."""
    _post_wizard(client, "maplewood-learning")
    # Draft is created (and reused across steps), so exactly 1 exists
    assert DraftSubmission.objects.filter(school=maplewood_school_with_keys).count() == 1


@pytest.mark.django_db
def test_apply_view_wizard_skips_payment_when_no_stripe_keys(client, maplewood_school):
    """
    School has fee enabled in YAML but no Stripe keys configured in DB.
    Graceful degradation: Submission is created directly without blocking the applicant.
    """
    resp = _post_wizard(client, "maplewood-learning")

    assert resp.status_code == 302
    assert "/apply/pay/" not in resp["Location"]
    assert Submission.objects.filter(school=maplewood_school).count() == 1


@pytest.mark.django_db
def test_apply_view_wizard_waived_form_skips_payment(client, maplewood_school_with_keys):
    """
    When get_application_fee_config returns waived=True, Submission is created
    directly and payment_status is set to 'waived'.
    """
    waived_cfg = {"enabled": True, "amount": 50, "description": "Fee", "waived": True}
    with mock.patch("core.views_public.get_application_fee_config", return_value=waived_cfg):
        resp = _post_wizard(client, "maplewood-learning")

    assert resp.status_code == 302
    assert "/apply/pay/" not in resp["Location"]
    sub = Submission.objects.filter(school=maplewood_school_with_keys).first()
    assert sub is not None
    assert sub.payment_status == "waived"


@pytest.mark.django_db
def test_apply_view_wizard_intermediate_steps_do_not_trigger_fee(client, maplewood_school_with_keys):
    """Steps 1 and 2 must redirect to the next step, not to payment."""
    resp1 = client.post(
        reverse("apply_form", kwargs={"school_slug": "maplewood-learning", "form_key": "student"}),
        {"student_first_name": "Bob", "student_last_name": "Jones",
         "date_of_birth": "2015-01-01", "grade_applying_for": "1st"},
    )
    assert resp1.status_code == 302
    assert "/apply/pay/" not in resp1["Location"]

    resp2 = client.post(
        reverse("apply_form", kwargs={"school_slug": "maplewood-learning", "form_key": "program"}),
        {"program_type": "afterschool", "attendance_days": ["mon", "wed", "fri"]},
    )
    assert resp2.status_code == 302
    assert "/apply/pay/" not in resp2["Location"]


# ── apply_payment_view ────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_apply_payment_view_renders_with_client_secret(client, maplewood_school_with_keys):
    """GET to payment page should call Stripe, get client_secret, render Elements form."""
    draft = DraftSubmission.objects.create(
        school=maplewood_school_with_keys,
        form_key="multi",
        last_form_key="contact",
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
        form_key="multi",
        last_form_key="contact",
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

    assert resp.status_code == 302
    assert Submission.objects.filter(school=maplewood_school_with_keys).count() == 1


@pytest.mark.django_db
def test_apply_payment_view_already_submitted_draft_redirects_to_success(client, maplewood_school_with_keys):
    """If the draft is already submitted, redirect to success without re-creating."""
    from django.utils import timezone
    draft = DraftSubmission.objects.create(
        school=maplewood_school_with_keys,
        form_key="multi",
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
        form_key="multi",
        last_form_key="contact",
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
        form_key="multi",
        last_form_key="contact",
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
        form_key="multi",
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
    client.post(
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
