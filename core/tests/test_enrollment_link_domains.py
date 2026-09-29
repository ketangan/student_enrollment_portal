"""Enrollment links must not move demo families onto the production domain."""
from urllib.parse import urlsplit

import pytest
from django.core import mail
from django.core.exceptions import DisallowedHost
from django.test import RequestFactory
from django.urls import reverse

from core.models import DraftSubmission
from core.services.notifications import send_resume_link_email
from core.services.url_builder import app_reverse, demo_reverse, request_reverse
from core.tests.factories import LeadFactory, SchoolAdminMembershipFactory, SchoolFactory, SubmissionFactory


@pytest.fixture(autouse=True)
def domains(settings):
    settings.APP_BASE_URL = "https://app.mypontora.com"
    settings.DEMO_BASE_URL = "https://demo.mypontora.com"
    settings.ALLOWED_HOSTS = ["app.mypontora.com", "demo.mypontora.com", "localhost", "127.0.0.1", "[::1]", "testserver", ".example.com"]
    settings.DEFAULT_FROM_EMAIL = "test@example.com"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.SECURE_SSL_REDIRECT = False


@pytest.mark.parametrize("host,debug,expected", [
    ("demo.mypontora.com", False, "https://demo.mypontora.com"),
    ("app.mypontora.com", False, "https://app.mypontora.com"),
    ("127.0.0.1:8001", True, "http://127.0.0.1:8001"),
    ("localhost:8001", True, "http://localhost:8001"),
    ("[::1]:8001", True, "http://[::1]:8001"),
    ("127.0.0.1:8001", False, "https://app.mypontora.com"),
    ("demo.mypontora.com.example.com", True, "https://app.mypontora.com"),
    ("testserver", False, "https://app.mypontora.com"),
])
def test_only_trusted_environment_selected(settings, host, debug, expected):
    settings.DEBUG = debug
    request = RequestFactory().get("/", HTTP_HOST=host)
    assert request_reverse(request, "login") == expected + reverse("login")


def test_no_request_and_explicit_builders_retain_existing_behavior():
    assert request_reverse(None, "login") == app_reverse("login")
    assert app_reverse("login").startswith("https://app.mypontora.com/")
    assert demo_reverse("login").startswith("https://demo.mypontora.com/")


def test_disallowed_host_rejected():
    request = RequestFactory().get("/", HTTP_HOST="attacker.invalid")
    with pytest.raises(DisallowedHost):
        request_reverse(request, "login")


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["kidworks-childrens-center", "south-bay-music"])
@pytest.mark.parametrize("host,secure,origin", [
    ("demo.mypontora.com", True, "https://demo.mypontora.com"),
    ("app.mypontora.com", True, "https://app.mypontora.com"),
    ("127.0.0.1:8001", False, "http://127.0.0.1:8001"),
])
def test_lead_links_redirect_and_email_stay_in_environment(client, settings, slug, host, secure, origin):
    settings.DEBUG = True
    school = SchoolFactory(slug=slug, plan="pro", is_demo=(host == "demo.mypontora.com"))
    lead = LeadFactory(school=school, email="parent@example.com", status="new")
    client.force_login(SchoolAdminMembershipFactory(school=school, role="owner").user)
    kwargs = {"school_slug": slug, "lead_id": lead.pk}
    request_options = {"HTTP_HOST": host, "secure": secure}
    response = client.get(reverse("school_lead_detail", kwargs=kwargs), **request_options)
    assert response.status_code == 200
    draft = DraftSubmission.objects.get(lead=lead, submitted_at__isnull=True)
    expected = origin + reverse("apply_resume", kwargs={"school_slug": slug, "token": draft.token})
    assert response.context["form_url"] == expected
    assert response.context["resume_url"] == expected
    assert expected.encode() in response.content

    response = client.post(reverse("school_lead_start_enrollment", kwargs=kwargs), **request_options)
    assert response.status_code == 302
    assert response.url == expected
    assert DraftSubmission.objects.filter(lead=lead).count() == 1

    response = client.post(reverse("school_lead_resend_resume_link", kwargs=kwargs), **request_options)
    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert expected in mail.outbox[0].body

    response = client.get(reverse("school_leads", kwargs={"school_slug": slug}), **request_options)
    assert response.status_code == 200
    assert urlsplit(response.context["lead_capture_url"]).netloc == host


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["demo.mypontora.com", "app.mypontora.com"])
def test_parent_save_resume_email_stays_on_same_domain(client, host):
    school = SchoolFactory(slug="kidworks-childrens-center", plan="pro")
    response = client.post(reverse("apply", kwargs={"school_slug": school.slug}), {
        "_action": "save_draft", "child_first_name": "Avery", "guardian_email": "parent@example.com",
    }, HTTP_HOST=host, secure=True)
    assert response.status_code == 302
    draft = DraftSubmission.objects.get(school=school)
    expected = "https://" + host + reverse("apply_resume", args=[school.slug, draft.token])
    assert len(mail.outbox) == 1
    assert expected in mail.outbox[0].body


@pytest.mark.django_db
def test_resume_email_without_request_keeps_app_default():
    school = SchoolFactory(slug="kidworks-childrens-center", plan="pro")
    draft = DraftSubmission.objects.create(school=school, email="parent@example.com")
    assert send_resume_link_email(draft=draft, school=school)
    assert app_reverse("apply_resume", args=[school.slug, draft.token]) in mail.outbox[0].body


@pytest.mark.django_db
@pytest.mark.parametrize("slug,email_key", [("kidworks-childrens-center", "guardian_email"), ("south-bay-music", "guardian_email")])
@pytest.mark.parametrize("host", ["demo.mypontora.com", "app.mypontora.com", "127.0.0.1:8001"])
def test_status_links_and_admin_emails_stay_in_environment(client, settings, slug, email_key, host):
    settings.DEBUG = True
    school = SchoolFactory(slug=slug, plan="pro")
    submission = SubmissionFactory(school=school, data={email_key: "parent@example.com"})
    client.force_login(SchoolAdminMembershipFactory(school=school).user)
    opts = {"HTTP_HOST": host, "secure": not host.startswith("127.")}
    origin = ("https://" if opts["secure"] else "http://") + host
    kwargs = {"school_slug": slug, "submission_id": submission.pk}
    expected = origin + reverse("family_status", kwargs={"school_slug": slug, "token": submission.status_token})
    response = client.get(reverse("school_submission_detail", kwargs=kwargs), **opts)
    assert response.status_code == 200
    assert response.context["family_status_url"] == expected
    for action in ["school_submission_resend_status_link", "school_submission_resend_confirmation"]:
        response = client.post(reverse(action, kwargs=kwargs), **opts)
        assert response.status_code == 302
    assert len(mail.outbox) == 2
    assert all(expected in message.body for message in mail.outbox)
    response = client.get(reverse("school_submissions", kwargs={"school_slug": slug}), **opts)
    assert response.context["apply_url"] == origin + reverse("apply", kwargs={"school_slug": slug})


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["demo.mypontora.com", "app.mypontora.com"])
def test_new_application_confirmation_keeps_domain(client, monkeypatch, host):
    from core.models import Submission
    monkeypatch.setattr("core.views_public.send_submission_notification_email", lambda **kwargs: None)
    school = SchoolFactory(slug="kidworks-childrens-center", plan="pro")
    response = client.post(reverse("apply", kwargs={"school_slug": school.slug}), {
        "child_first_name": "Avery", "child_last_name": "Test", "date_of_birth": "2023-01-02",
        "program_interest": "garden_room_full_day", "guardian_name": "Test Parent",
        "guardian_email": "parent@example.com", "guardian_phone": "3105550123",
        "enrollment_policies": "on", "photo_release": "deny", "emergency_authorization": "on",
    }, HTTP_HOST=host, secure=True)
    assert response.status_code == 302
    submission = Submission.objects.get(school=school)
    expected = "https://" + host + reverse("family_status", kwargs={"school_slug": school.slug, "token": submission.status_token})
    assert len(mail.outbox) == 1
    assert expected in mail.outbox[0].body
    assert "does not confirm enrollment" in mail.outbox[0].body


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["demo.mypontora.com", "app.mypontora.com"])
def test_existing_fee_return_and_draft_completion_keep_domain(client, monkeypatch, settings, host):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from core.models import Submission
    settings.DEV_SKIP_PAYMENT = False
    school = SchoolFactory(slug="maplewood-learning", plan="pro",
        app_fee_stripe_public_key="pk_test_fake", app_fee_stripe_secret_key="sk_test_fake")
    draft = DraftSubmission.objects.create(school=school, form_key="multi", last_form_key="contact",
        data={"student_first_name": "Avery", "student_last_name": "Test", "contact_email": "parent@example.com"})
    opts = {"HTTP_HOST": host, "secure": True}
    kwargs = {"school_slug": school.slug, "draft_token": draft.token}
    confirm_path = reverse("apply_payment_confirm", kwargs=kwargs)
    expected = "https://" + host + confirm_path
    monkeypatch.setattr("core.views_public.create_application_fee_intent", Mock(return_value=("pi_fake_secret", "pi_fake")))
    response = client.get(reverse("apply_payment", kwargs=kwargs), **opts)
    assert response.status_code == 200
    assert response.context["confirm_url"] == expected
    intent = SimpleNamespace(status="requires_payment_method")
    monkeypatch.setattr("core.views_public.retrieve_application_fee_intent", Mock(return_value=intent))
    response = client.get(confirm_path, {"payment_intent": "pi_fake"}, **opts)
    assert response.status_code == 200
    assert response.context["confirm_url"] == expected
    intent.status = "succeeded"
    monkeypatch.setattr("core.views_public.send_submission_notification_email", Mock())
    confirmation = Mock(return_value=True)
    monkeypatch.setattr("core.views_public.send_applicant_confirmation_email", confirmation)
    response = client.get(confirm_path, {"payment_intent": "pi_fake"}, **opts)
    assert response.status_code == 302
    submission = Submission.objects.get(school=school)
    assert submission.payment_status == "paid"
    assert confirmation.call_args.kwargs["status_url"] == "https://" + host + reverse("family_status", kwargs={"school_slug": school.slug, "token": submission.status_token})
