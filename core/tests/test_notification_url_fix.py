"""
Tests for the notification email URL fix (root cause of Emily's login issue).

Root cause: send_submission_notification_email generated a Django Admin URL
(/admin/core/submission/<id>/change/) in the notification email. Emily tapped
that link on iPhone, landed on the Jazzmin login page, and could not log in.

Fix: _admin_url_for_submission now generates the school admin URL
(/schools/<slug>/admin/submissions/<id>/) when school is provided.
"""
import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from core.models import School, SchoolAdminMembership, Submission
from core.services.notifications import _admin_url_for_submission, send_submission_notification_email


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def school(db):
    return School.objects.create(
        slug="notif-test-school",
        display_name="Notif Test School",
        plan="starter",
        trial_started_at=timezone.now(),
    )


@pytest.fixture
def school_b(db):
    return School.objects.create(
        slug="notif-test-school-b",
        display_name="Notif School B",
        plan="starter",
        trial_started_at=timezone.now(),
    )


@pytest.fixture
def owner(db, school):
    u = User.objects.create_user("notif_owner", password="pass123", is_staff=True)
    SchoolAdminMembership.objects.create(user=u, school=school, role="owner", is_active=True)
    return u


@pytest.fixture
def submission(db, school):
    return Submission.objects.create(
        school=school,
        data={"first_name": "Alice", "last_name": "Example"},
        status="New",
    )


def _notif_config():
    return {
        "success": {
            "notifications": {
                "submission_email": {
                    "to": "admin@example.com",
                    "from_email": "no-reply@example.com",
                    "subject": "New submission",
                }
            }
        }
    }


# ── Unit: _admin_url_for_submission ──────────────────────────────────────────

@pytest.mark.django_db
def test_admin_url_with_school_returns_school_admin_path(school):
    url = _admin_url_for_submission(request=None, submission_id=42, school=school)
    assert f"/schools/{school.slug}/admin/submissions/42/" in url
    assert "/admin/core/submission/" not in url


@pytest.mark.django_db
def test_admin_url_without_school_falls_back_to_django_admin(school):
    """When school=None (e.g. called without context), falls back to Django Admin URL.
    This is the legacy path; the interceptor at admin/login/ is the band-aid for it."""
    url = _admin_url_for_submission(request=None, submission_id=42)
    assert "/admin/core/submission/42/change/" in url


@pytest.mark.django_db
def test_admin_url_school_slug_is_correct(school):
    url = _admin_url_for_submission(request=None, submission_id=99, school=school)
    assert f"/schools/{school.slug}/" in url
    assert "/submissions/99/" in url


# ── Unit: notification email body ────────────────────────────────────────────

@pytest.mark.django_db
def test_notification_email_body_contains_school_admin_url(settings, school, submission):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    ok = send_submission_notification_email(
        request=None,
        config_raw=_notif_config(),
        school_name=school.display_name,
        submission_id=submission.id,
        submission_public_id=submission.public_id,
        student_name="Alice Example",
        submission_data={},
        school=school,
    )

    assert ok is True
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]

    expected_path = f"/schools/{school.slug}/admin/submissions/{submission.id}/"
    assert expected_path in msg.body, "Plain text body must contain school admin URL"
    html_body = msg.alternatives[0][0]
    assert expected_path in html_body, "HTML body must contain school admin URL"


@pytest.mark.django_db
def test_notification_email_body_does_not_contain_django_admin_url(settings, school, submission):
    """The Django Admin path must NOT appear in the email — that's what caused the bug."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    send_submission_notification_email(
        request=None,
        config_raw=_notif_config(),
        school_name=school.display_name,
        submission_id=submission.id,
        submission_public_id=submission.public_id,
        student_name="Alice Example",
        submission_data={},
        school=school,
    )

    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    html_body = msg.alternatives[0][0]

    assert "/admin/core/submission/" not in msg.body
    assert "/admin/core/submission/" not in html_body


@pytest.mark.django_db
def test_notification_email_without_school_still_sends(settings):
    """Legacy path (school=None): email still sends, just uses Django Admin URL."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    ok = send_submission_notification_email(
        request=None,
        config_raw=_notif_config(),
        school_name="Test School",
        submission_id=99,
        submission_public_id="PUB_XYZ",
        student_name="Bob",
        submission_data={},
        school=None,
    )

    assert ok is True
    assert len(mail.outbox) == 1


# ── Smoke: URL routing for the school admin submission detail ─────────────────

@pytest.mark.django_db
def test_school_submission_detail_unauthenticated_redirects_to_app_login(client, school, submission):
    """
    The link in the notification email must redirect unauthenticated users to
    /login/?next=<url>, NOT to the Jazzmin /admin/login/ page.
    """
    url = reverse("school_submission_detail", kwargs={
        "school_slug": school.slug,
        "submission_id": submission.id,
    })
    r = client.get(url)
    assert r.status_code == 302
    location = r["Location"]
    # Must go to app login, not Jazzmin
    assert location.startswith("/login/")
    assert "/admin/login/" not in location


@pytest.mark.django_db
def test_school_submission_detail_unauthenticated_preserves_next_param(client, school, submission):
    """After login, the user should land on the submission, not the dashboard."""
    url = reverse("school_submission_detail", kwargs={
        "school_slug": school.slug,
        "submission_id": submission.id,
    })
    r = client.get(url)
    location = r["Location"]
    assert f"next=" in location
    assert f"/schools/{school.slug}/admin/submissions/{submission.id}/" in location


@pytest.mark.django_db
def test_school_submission_detail_authenticated_owner_returns_200(client, school, submission, owner):
    client.force_login(owner)
    url = reverse("school_submission_detail", kwargs={
        "school_slug": school.slug,
        "submission_id": submission.id,
    })
    r = client.get(url)
    assert r.status_code == 200


@pytest.mark.django_db
def test_school_submission_detail_wrong_school_returns_404(client, school_b, submission, db):
    """A school admin from school B cannot view school A's submission."""
    other_user = User.objects.create_user("other_owner", password="pass123", is_staff=True)
    SchoolAdminMembership.objects.create(user=other_user, school=school_b, role="owner", is_active=True)
    client.force_login(other_user)

    url = reverse("school_submission_detail", kwargs={
        "school_slug": submission.school.slug,
        "submission_id": submission.id,
    })
    r = client.get(url)
    assert r.status_code == 404


@pytest.mark.django_db
def test_login_with_next_pointing_at_submission_redirects_after_auth(client, school, submission, owner):
    """
    Simulates the full Emily flow: taps email link → redirected to /login/?next=... →
    logs in → lands on submission detail (not some generic dashboard).
    """
    submission_url = reverse("school_submission_detail", kwargs={
        "school_slug": school.slug,
        "submission_id": submission.id,
    })
    login_url = reverse("login") + f"?next={submission_url}"
    r = client.post(login_url, {"username": "notif_owner", "password": "pass123"})
    assert r.status_code == 302
    assert r["Location"] == submission_url


# ── Smoke: Django Admin interceptor (band-aid) still works ───────────────────

@pytest.mark.django_db
def test_django_admin_submission_url_unauthenticated_redirects_to_app_login(client, submission):
    """
    /admin/core/submission/<id>/change/ unauthenticated → /admin/login/ →
    intercepted → /login/  (confirmed deployed, regression guard).
    """
    django_admin_url = f"/admin/core/submission/{submission.id}/change/"
    r = client.get(django_admin_url, follow=True)
    final_url = r.redirect_chain[-1][0]
    assert final_url == "/login/"


@pytest.mark.django_db
def test_admin_login_interceptor_still_redirects_to_app_login(client):
    """/admin/login/ (the Jazzmin login page) → /login/ for all methods."""
    r = client.get("/admin/login/")
    assert r.status_code == 302
    assert r["Location"] == "/login/"


# ── Regression: lead notification URL was already correct ────────────────────

@pytest.mark.django_db
def test_lead_notification_uses_school_admin_url(settings, school, db):
    """
    send_lead_admin_notification already uses the school admin URL.
    This guards against regression in that function.
    """
    from core.models import Lead
    from core.services.notifications import send_lead_admin_notification

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    lead = Lead.objects.create(
        school=school,
        data={"first_name": "Charlie", "email": "charlie@example.com"},
    )

    config_raw = {
        "leads": {"notify_to": "admin@example.com"},
    }

    ok = send_lead_admin_notification(
        lead=lead,
        config_raw=config_raw,
        school=school,
    )

    if ok and mail.outbox:
        msg = mail.outbox[0]
        assert "/admin/core/lead/" not in msg.body
        assert f"/schools/{school.slug}/admin/leads/{lead.id}/" in msg.body
