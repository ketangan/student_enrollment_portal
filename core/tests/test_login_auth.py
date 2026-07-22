"""
Integration tests for login / logout auth flows.
Covers redirect logic, session lifecycle, edge cases, and the /admin/login/ redirect fix.
"""
import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from core.models import School, SchoolAdminMembership


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        username="su", email="su@test.com", password="pass123"
    )


@pytest.fixture
def school(db):
    return School.objects.create(
        slug="auth-test-school", display_name="Auth Test School",
        plan="trial", trial_started_at=timezone.now(),
    )


@pytest.fixture
def school_b(db):
    return School.objects.create(
        slug="auth-test-school-b", display_name="School B",
        plan="trial", trial_started_at=timezone.now(),
    )


def _make_user(username, password="pass123", **kwargs):
    return User.objects.create_user(username=username, password=password, **kwargs)


def _membership(user, school, role="owner", is_active=True):
    return SchoolAdminMembership.objects.create(
        user=user, school=school, role=role, is_active=is_active
    )


# ── /admin/login/ redirect fix ────────────────────────────────────────────────

@pytest.mark.django_db
def test_admin_login_url_redirects_to_app_login(client):
    """Anyone hitting /admin/login/ (e.g. old bookmark) lands on /login/."""
    r = client.get("/admin/login/")
    assert r.status_code == 302
    assert r["Location"] == "/login/"


@pytest.mark.django_db
def test_admin_login_url_post_also_redirects(client):
    """/admin/login/ POST (e.g. Jazzmin form submit) also gets redirected."""
    r = client.post("/admin/login/", {"username": "x", "password": "y"})
    assert r.status_code == 302
    assert r["Location"] == "/login/"


@pytest.mark.django_db
def test_admin_root_unauthenticated_ultimately_reaches_login(client):
    """/admin/ when unauthenticated: Django redirects → /admin/login/ → our redirect → /login/."""
    r = client.get("/admin/", follow=True)
    final_url = r.redirect_chain[-1][0]
    assert final_url == "/login/"


# ── Login redirect logic ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_superuser_redirected_to_ops_dashboard(client, superuser):
    r = client.post(reverse("login"), {"username": "su", "password": "pass123"})
    assert r.status_code == 302
    assert r["Location"] == reverse("ops_dashboard")


@pytest.mark.django_db
def test_school_admin_redirected_to_their_dashboard(client, school, db):
    user = _make_user("sadmin")
    _membership(user, school)
    r = client.post(reverse("login"), {"username": "sadmin", "password": "pass123"})
    assert r.status_code == 302
    assert r["Location"] == reverse("school_dashboard", kwargs={"school_slug": school.slug})


@pytest.mark.django_db
def test_user_with_multiple_memberships_lands_on_first_active(client, school, school_b, db):
    """User with memberships at two schools lands on whichever .first() returns."""
    user = _make_user("multi")
    _membership(user, school)
    _membership(user, school_b)
    r = client.post(reverse("login"), {"username": "multi", "password": "pass123"})
    assert r.status_code == 302
    location = r["Location"]
    assert "/schools/" in location and "/admin/" in location


@pytest.mark.django_db
def test_user_with_no_membership_is_logged_out_and_returned_to_login(client, db):
    """A plain user with no school membership cannot land anywhere — gets logged out."""
    _make_user("nomem")
    r = client.post(reverse("login"), {"username": "nomem", "password": "pass123"})
    assert r.status_code == 302
    assert r["Location"] == reverse("login")
    # Session must not carry an authenticated user after this
    assert not client.session.get("_auth_user_id")


@pytest.mark.django_db
def test_user_with_only_inactive_membership_is_treated_as_no_membership(client, school, db):
    user = _make_user("inactive_mem")
    _membership(user, school, is_active=False)
    r = client.post(reverse("login"), {"username": "inactive_mem", "password": "pass123"})
    assert r.status_code == 302
    assert r["Location"] == reverse("login")


@pytest.mark.django_db
def test_login_respects_safe_next_param(client, school, db):
    """?next= pointing to a same-host path is respected."""
    user = _make_user("nextu")
    _membership(user, school)
    target = f"/schools/{school.slug}/admin/submissions/"
    r = client.post(reverse("login") + f"?next={target}", {
        "username": "nextu", "password": "pass123",
    })
    assert r.status_code == 302
    assert r["Location"] == target


@pytest.mark.django_db
def test_login_ignores_external_next_param(client, school, db):
    """?next= pointing to an external domain is ignored — falls back to normal redirect."""
    user = _make_user("extu")
    _membership(user, school)
    r = client.post(reverse("login") + "?next=https://evil.com/steal", {
        "username": "extu", "password": "pass123",
    })
    assert r.status_code == 302
    assert "evil.com" not in r["Location"]


@pytest.mark.django_db
def test_already_authenticated_get_to_login_redirects_away(client, school, db):
    """Visiting /login/ when already logged in bounces you to your dashboard."""
    user = _make_user("already")
    _membership(user, school)
    client.force_login(user)
    r = client.get(reverse("login"))
    assert r.status_code == 302
    assert r["Location"] == reverse("school_dashboard", kwargs={"school_slug": school.slug})


# ── Credential rejection ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_wrong_password_returns_200_with_error(client, db):
    _make_user("wrongpw")
    r = client.post(reverse("login"), {"username": "wrongpw", "password": "nope"})
    assert r.status_code == 200
    assert b"Invalid username" in r.content


@pytest.mark.django_db
def test_nonexistent_user_returns_200_with_error(client, db):
    r = client.post(reverse("login"), {"username": "ghost", "password": "x"})
    assert r.status_code == 200
    assert b"Invalid username" in r.content


@pytest.mark.django_db
def test_empty_credentials_returns_200_with_error(client, db):
    r = client.post(reverse("login"), {"username": "", "password": ""})
    assert r.status_code == 200
    assert b"Invalid username" in r.content


@pytest.mark.django_db
def test_inactive_user_cannot_log_in(client, db):
    _make_user("inactive_u", is_active=False)
    r = client.post(reverse("login"), {"username": "inactive_u", "password": "pass123"})
    assert r.status_code == 200
    assert not client.session.get("_auth_user_id")


# ── Logout ────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_logout_post_clears_session_and_redirects(client, school, db):
    user = _make_user("logmeout")
    _membership(user, school)
    client.force_login(user)
    assert client.session.get("_auth_user_id")
    r = client.post(reverse("logout"))
    assert r.status_code == 302
    assert r["Location"] == reverse("login")
    assert not client.session.get("_auth_user_id")


@pytest.mark.django_db
def test_logout_get_rejected_405(client, school, db):
    user = _make_user("getlogout")
    _membership(user, school)
    client.force_login(user)
    r = client.get(reverse("logout"))
    assert r.status_code == 405


@pytest.mark.django_db
def test_logout_when_not_logged_in_still_redirects(client):
    r = client.post(reverse("logout"))
    assert r.status_code == 302
    assert r["Location"] == reverse("login")


# ── Session lifecycle (simulates multiple tabs / requests) ────────────────────

@pytest.mark.django_db
def test_session_persists_across_subsequent_requests(client, school, db):
    """After login, session keeps the user authenticated across requests.
    /login/ returns 302 (redirect away) when authenticated, 200 when not."""
    user = _make_user("persistent")
    _membership(user, school)
    client.post(reverse("login"), {"username": "persistent", "password": "pass123"})
    r1 = client.get(reverse("login"))
    r2 = client.get(reverse("login"))
    assert r1.status_code == 302
    assert r2.status_code == 302


@pytest.mark.django_db
def test_logout_invalidates_session_for_all_subsequent_requests(client, school, db):
    """After logout, the session is gone — /login/ returns 200 (not authenticated)."""
    user = _make_user("invalidate")
    _membership(user, school)
    client.force_login(user)
    client.post(reverse("logout"))
    r = client.get(reverse("login"))
    assert r.status_code == 200
    assert not client.session.get("_auth_user_id")


@pytest.mark.django_db
def test_two_separate_clients_have_independent_sessions(school, db):
    """Two browser sessions (different clients) are fully independent."""
    user_a = _make_user("user_a")
    user_b = _make_user("user_b")
    _membership(user_a, school)
    _membership(user_b, school)

    client_a = Client()
    client_b = Client()

    client_a.force_login(user_a)
    client_b.force_login(user_b)

    # Logging out client_a does not affect client_b — client_b still authenticated
    client_a.post(reverse("logout"))
    assert not client_a.session.get("_auth_user_id")
    assert client_b.session.get("_auth_user_id") == str(user_b.pk)


@pytest.mark.django_db
def test_login_from_two_accounts_sequentially_on_same_client(client, school, db):
    """Logging in as a second user on the same client replaces the first session."""
    user_a = _make_user("seq_a")
    user_b = _make_user("seq_b")
    _membership(user_a, school)
    _membership(user_b, school)

    client.post(reverse("login"), {"username": "seq_a", "password": "pass123"})
    session_a_key = client.session.session_key

    client.post(reverse("logout"))
    client.post(reverse("login"), {"username": "seq_b", "password": "pass123"})
    session_b_key = client.session.session_key

    # New session key issued after re-login (session fixation protection)
    assert session_a_key != session_b_key
    assert client.session.get("_auth_user_id") == str(user_b.pk)
