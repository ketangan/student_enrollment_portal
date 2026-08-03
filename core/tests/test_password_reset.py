# core/tests/test_password_reset.py
"""
Tests for the Django password reset flow (H1).

Covers:
  - All four reset pages render with correct templates
  - Valid email POST sends email and redirects to done page
  - Unknown email POST also redirects to done page (no user enumeration)
  - Valid confirm link renders the set-password form
  - Invalid / already-used confirm link renders the invalid-link message
  - Successful password set redirects to complete, user can log in with new password
  - "Forgot password?" link present on login page
  - "Don't know your current password?" link present on password change page
"""
from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.tests.factories import SchoolAdminMembershipFactory, SchoolFactory


# ── Helpers ──────────────────────────────────────────────────────────────────

def _uid_and_token(user: User) -> tuple[str, str]:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return uid, token


# ── Page rendering ────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_password_reset_form_renders(client):
    resp = client.get(reverse("password_reset"))
    assert resp.status_code == 200
    assert b"Send reset link" in resp.content


@pytest.mark.django_db
def test_password_reset_done_renders(client):
    resp = client.get(reverse("password_reset_done"))
    assert resp.status_code == 200
    assert b"Check" in resp.content or b"email" in resp.content.lower()


@pytest.mark.django_db
def test_password_reset_complete_renders(client):
    resp = client.get(reverse("password_reset_complete"))
    assert resp.status_code == 200
    assert b"Sign in" in resp.content


# ── Email sending ─────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_reset_valid_email_sends_email_and_redirects(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    user = User.objects.create_user(username="resetuser", email="reset@example.com", password="old123")
    resp = client.post(reverse("password_reset"), {"email": "reset@example.com"})
    assert resp.status_code == 302
    assert resp.url == "/password-reset/sent/"
    assert len(mail.outbox) == 1
    assert "reset@example.com" in mail.outbox[0].to
    assert "password" in mail.outbox[0].subject.lower() or "reset" in mail.outbox[0].subject.lower()


@pytest.mark.django_db
def test_reset_unknown_email_does_not_reveal_existence(client, settings):
    """Submitting an email that isn't registered must still redirect to done — no enumeration."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    resp = client.post(reverse("password_reset"), {"email": "nobody@example.com"})
    assert resp.status_code == 302
    assert resp.url == "/password-reset/sent/"
    assert len(mail.outbox) == 0


@pytest.mark.django_db
def test_reset_email_contains_reset_link(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    user = User.objects.create_user(username="linkuser", email="link@example.com", password="pass123")
    client.post(reverse("password_reset"), {"email": "link@example.com"})
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    assert "password-reset/confirm/" in body


# ── Confirm page ──────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_confirm_valid_token_shows_form(client):
    user = User.objects.create_user(username="confuser", email="conf@example.com", password="old123")
    uid, token = _uid_and_token(user)
    url = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})
    resp = client.get(url, follow=True)
    assert resp.status_code == 200
    assert b"Set new password" in resp.content


@pytest.mark.django_db
def test_confirm_invalid_token_shows_invalid_message(client):
    user = User.objects.create_user(username="badtoken", email="bad@example.com", password="old123")
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    url = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": "invalid-token-xyz"})
    resp = client.get(url, follow=True)
    assert resp.status_code == 200
    assert b"invalid" in resp.content.lower() or b"already been used" in resp.content.lower()


@pytest.mark.django_db
def test_confirm_bad_uid_shows_invalid_message(client):
    url = reverse("password_reset_confirm", kwargs={"uidb64": "AAAA", "token": "invalid-token"})
    resp = client.get(url, follow=True)
    assert resp.status_code == 200
    assert b"invalid" in resp.content.lower() or b"already been used" in resp.content.lower()


# ── Successful password reset end-to-end ──────────────────────────────────────

@pytest.mark.django_db
def test_full_reset_flow_sets_new_password(client):
    """End-to-end: request reset → follow confirm link → set password → can log in."""
    user = User.objects.create_user(username="e2euser", email="e2e@example.com", password="oldpassword")
    uid, token = _uid_and_token(user)
    confirm_url = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})

    # Django's PasswordResetConfirmView does a GET-then-POST pattern with an internal redirect.
    # GET the confirm URL — Django redirects to a session-bound URL.
    resp = client.get(confirm_url)
    assert resp.status_code == 302
    session_url = resp.url  # e.g. /password-reset/confirm/<uid>/set-password/

    # POST the new password to the session-bound URL
    resp = client.post(session_url, {
        "new_password1": "NewSecurePass99!",
        "new_password2": "NewSecurePass99!",
    })
    assert resp.status_code == 302
    assert resp.url == "/password-reset/complete/"

    # Verify the user can now log in with the new password
    user.refresh_from_db()
    assert user.check_password("NewSecurePass99!")
    assert not user.check_password("oldpassword")


@pytest.mark.django_db
def test_reset_token_cannot_be_reused(client):
    """A token used once cannot be used again (single-use enforcement)."""
    user = User.objects.create_user(username="onceuser", email="once@example.com", password="old123")
    uid, token = _uid_and_token(user)
    confirm_url = reverse("password_reset_confirm", kwargs={"uidb64": uid, "token": token})

    # Use the token
    resp = client.get(confirm_url)
    session_url = resp.url
    client.post(session_url, {"new_password1": "NewPass1!", "new_password2": "NewPass1!"})

    # Try to use the same token again (new client session)
    from django.test import Client
    fresh_client = Client()
    resp2 = fresh_client.get(confirm_url, follow=True)
    assert b"invalid" in resp2.content.lower() or b"already been used" in resp2.content.lower()


# ── UI links ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_login_page_has_forgot_password_link(client):
    resp = client.get(reverse("login"))
    assert resp.status_code == 200
    assert b"password-reset" in resp.content
    assert b"Forgot" in resp.content


@pytest.mark.django_db
def test_password_change_page_has_reset_link(client):
    school = SchoolFactory()
    membership = SchoolAdminMembershipFactory(school=school)
    client.force_login(membership.user)
    resp = client.get(reverse("school_password_change", kwargs={"school_slug": school.slug}))
    assert resp.status_code == 200
    assert b"password-reset" in resp.content
    assert b"Don" in resp.content  # "Don't know your current password?"
