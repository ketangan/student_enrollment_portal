"""
Settings page edge cases.

Tests boundary conditions and permission checks for all POST actions on
/schools/<slug>/admin/settings/.  These are integration tests that hit the
actual view rather than mocking the internals.

Coverage:
  - update_follow_up_days: out-of-range (0, 31), non-numeric, editor blocked
  - update_smtp: invalid port, editor blocked, owner succeeds, port cleared
  - update_stripe: blank public key rejected, editor blocked, audit log captures prefix
  - update_display_name: blank rejected, too long rejected, no-change message
"""
from __future__ import annotations

import pytest
from django.test import Client

from core.models import AdminAuditLog
from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    UserFactory,
)

_SETTINGS_URL = "/schools/{slug}/admin/settings/"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _owner_client(school):
    """Return (client, membership) for a fresh owner of the given school."""
    membership = SchoolAdminMembershipFactory(school=school, role="owner")
    c = Client()
    c.force_login(membership.user)
    return c, membership


def _editor_client(school):
    """Return (client, membership) for a fresh editor of the given school."""
    membership = SchoolAdminMembershipFactory(school=school, role="editor")
    c = Client()
    c.force_login(membership.user)
    return c, membership


def _post(client, school, **kwargs):
    """POST to the school settings view."""
    return client.post(_SETTINGS_URL.format(slug=school.slug), kwargs, follow=False)


# ---------------------------------------------------------------------------
# update_follow_up_days
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_follow_up_days_zero_is_rejected():
    """0 days is below the minimum (1); view returns error and doesn't save."""
    school = SchoolFactory()
    client, _ = _owner_client(school)
    original = school.default_follow_up_days

    response = _post(client, school, action="update_follow_up_days", follow_up_days="0")
    assert response.status_code in (302, 200)

    school.refresh_from_db()
    assert school.default_follow_up_days == original, "0 should be rejected"


@pytest.mark.django_db
def test_follow_up_days_31_is_rejected():
    """31 days exceeds the maximum (30); view rejects with error."""
    school = SchoolFactory()
    client, _ = _owner_client(school)
    original = school.default_follow_up_days

    response = _post(client, school, action="update_follow_up_days", follow_up_days="31")
    assert response.status_code in (302, 200)

    school.refresh_from_db()
    assert school.default_follow_up_days == original, "31 should be rejected"


@pytest.mark.django_db
def test_follow_up_days_non_numeric_is_rejected():
    """Non-numeric input raises ValueError internally; view returns error."""
    school = SchoolFactory()
    client, _ = _owner_client(school)
    original = school.default_follow_up_days

    response = _post(client, school, action="update_follow_up_days", follow_up_days="abc")
    assert response.status_code in (302, 200)

    school.refresh_from_db()
    assert school.default_follow_up_days == original, "Non-numeric should be rejected"


@pytest.mark.django_db
def test_follow_up_days_editor_blocked():
    """Editor role cannot update follow_up_days (owner required); gets 404."""
    school = SchoolFactory()
    client, _ = _editor_client(school)
    original = school.default_follow_up_days

    response = _post(client, school, action="update_follow_up_days", follow_up_days="7")
    assert response.status_code == 404, (
        f"Editor should get 404 for owner-only action, got {response.status_code}"
    )

    school.refresh_from_db()
    assert school.default_follow_up_days == original


@pytest.mark.django_db
def test_follow_up_days_valid_owner_succeeds():
    """Valid day count (5) with owner role is saved."""
    school = SchoolFactory()
    client, _ = _owner_client(school)

    response = _post(client, school, action="update_follow_up_days", follow_up_days="5")
    assert response.status_code in (301, 302), f"Expected redirect, got {response.status_code}"

    school.refresh_from_db()
    assert school.default_follow_up_days == 5


@pytest.mark.django_db
def test_follow_up_days_min_boundary_accepted():
    """1 day (minimum) is accepted."""
    school = SchoolFactory()
    client, _ = _owner_client(school)
    _post(client, school, action="update_follow_up_days", follow_up_days="1")
    school.refresh_from_db()
    assert school.default_follow_up_days == 1


@pytest.mark.django_db
def test_follow_up_days_max_boundary_accepted():
    """30 days (maximum) is accepted."""
    school = SchoolFactory()
    client, _ = _owner_client(school)
    _post(client, school, action="update_follow_up_days", follow_up_days="30")
    school.refresh_from_db()
    assert school.default_follow_up_days == 30


# ---------------------------------------------------------------------------
# update_smtp
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_smtp_invalid_port_is_rejected():
    """Port 99999 is out of range (1–65535); view returns error without saving."""
    school = SchoolFactory()
    client, _ = _owner_client(school)

    response = _post(
        client, school,
        action="update_smtp",
        smtp_host="mail.example.com",
        smtp_port="99999",
        smtp_username="user",
        smtp_from_email="from@example.com",
        smtp_use_tls="1",
    )
    assert response.status_code in (302, 200)

    school.refresh_from_db()
    assert school.smtp_host == "", "Invalid port should prevent any field from saving"


@pytest.mark.django_db
def test_smtp_port_zero_is_rejected():
    """Port 0 is out of range."""
    school = SchoolFactory()
    client, _ = _owner_client(school)
    _post(client, school, action="update_smtp", smtp_host="mx.test.com", smtp_port="0")
    school.refresh_from_db()
    assert school.smtp_host == ""


@pytest.mark.django_db
def test_smtp_editor_blocked():
    """Editor cannot save SMTP settings; gets 404."""
    school = SchoolFactory()
    client, _ = _editor_client(school)

    response = _post(
        client, school,
        action="update_smtp",
        smtp_host="mail.example.com",
        smtp_port="587",
        smtp_username="u",
        smtp_from_email="f@x.com",
        smtp_use_tls="1",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_smtp_owner_saves_all_fields():
    """Owner can save all SMTP fields; values persist in DB."""
    school = SchoolFactory()
    client, _ = _owner_client(school)

    response = _post(
        client, school,
        action="update_smtp",
        smtp_host="smtp.postmark.com",
        smtp_port="587",
        smtp_username="api_user",
        smtp_password="secret123",
        smtp_from_email="admin@myschool.com",
        smtp_use_tls="1",
    )
    assert response.status_code in (301, 302)

    school.refresh_from_db()
    assert school.smtp_host == "smtp.postmark.com"
    assert school.smtp_port == 587
    assert school.smtp_username == "api_user"
    assert school.smtp_from_email == "admin@myschool.com"
    assert school.smtp_use_tls is True


@pytest.mark.django_db
def test_smtp_empty_port_is_accepted_and_nulled():
    """Omitting port (empty string) saves smtp_port=None — port is optional."""
    school = SchoolFactory()
    client, _ = _owner_client(school)
    _post(
        client, school,
        action="update_smtp",
        smtp_host="smtp.postmark.com",
        smtp_port="",
        smtp_username="",
        smtp_from_email="",
    )
    school.refresh_from_db()
    assert school.smtp_port is None


@pytest.mark.django_db
def test_smtp_clear_clears_all_fields():
    """clear_smtp action resets every SMTP field to empty / defaults."""
    school = SchoolFactory()
    school.smtp_host = "old.host.com"
    school.smtp_port = 465
    school.smtp_username = "olduser"
    school.smtp_password = "oldpass"
    school.smtp_from_email = "old@school.com"
    school.smtp_use_tls = False
    school.save()

    client, _ = _owner_client(school)
    response = _post(client, school, action="clear_smtp")
    assert response.status_code in (301, 302)

    school.refresh_from_db()
    assert school.smtp_host == ""
    assert school.smtp_port is None
    assert school.smtp_username == ""
    assert school.smtp_from_email == ""
    assert school.smtp_use_tls is True  # reset to default=True


# ---------------------------------------------------------------------------
# update_stripe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_stripe_blank_public_key_rejected():
    """Submitting an empty publishable key returns error; nothing saved."""
    school = SchoolFactory()
    client, _ = _owner_client(school)

    response = _post(
        client, school,
        action="update_stripe",
        app_fee_stripe_public_key="",
        app_fee_stripe_secret_key="sk_test_secret",
    )
    assert response.status_code in (302, 200)

    school.refresh_from_db()
    assert school.app_fee_stripe_public_key == "", "Blank public key should be rejected"


@pytest.mark.django_db
def test_stripe_editor_blocked():
    """Editor cannot update Stripe keys; gets 404."""
    school = SchoolFactory()
    client, _ = _editor_client(school)

    response = _post(
        client, school,
        action="update_stripe",
        app_fee_stripe_public_key="pk_test_abc",
        app_fee_stripe_secret_key="sk_test_abc",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_stripe_owner_saves_keys():
    """Valid Stripe keys are saved for owner."""
    school = SchoolFactory()
    client, _ = _owner_client(school)

    response = _post(
        client, school,
        action="update_stripe",
        app_fee_stripe_public_key="pk_test_123abc",
        app_fee_stripe_secret_key="sk_test_456def",
    )
    assert response.status_code in (301, 302)

    school.refresh_from_db()
    assert school.app_fee_stripe_public_key == "pk_test_123abc"
    assert school.app_fee_stripe_secret_key == "sk_test_456def"


@pytest.mark.django_db
def test_stripe_audit_log_captures_public_key_prefix():
    """Audit log stores only the first 8 chars of the public key — not the full key."""
    school = SchoolFactory()
    client, _ = _owner_client(school)

    _post(
        client, school,
        action="update_stripe",
        app_fee_stripe_public_key="pk_test_VERYSECRET",
        app_fee_stripe_secret_key="",
    )

    log = AdminAuditLog.objects.filter(
        object_id=str(school.pk), action="action"
    ).order_by("-id").first()
    assert log is not None
    extra = log.extra or {}
    assert extra.get("name") == "update_stripe_keys"
    prefix = extra.get("public_key_prefix", "")
    assert prefix == "pk_test_", f"Expected 'pk_test_' prefix, got '{prefix}'"
    assert "VERYSECRET" not in prefix, "Full secret portion must not appear in audit log"


@pytest.mark.django_db
def test_stripe_clear_removes_keys():
    """clear_stripe action removes both Stripe keys."""
    school = SchoolFactory()
    school.app_fee_stripe_public_key = "pk_test_old"
    school.app_fee_stripe_secret_key = "sk_test_old"
    school.save()

    client, _ = _owner_client(school)
    response = _post(client, school, action="clear_stripe")
    assert response.status_code in (301, 302)

    school.refresh_from_db()
    assert school.app_fee_stripe_public_key == ""
    assert school.app_fee_stripe_secret_key == ""


# ---------------------------------------------------------------------------
# update_display_name
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_display_name_blank_rejected():
    """Blank display name returns error; original name unchanged."""
    school = SchoolFactory()
    original_name = school.display_name
    client, _ = _owner_client(school)

    _post(client, school, action="update_display_name", display_name="")
    school.refresh_from_db()
    assert school.display_name == original_name


@pytest.mark.django_db
def test_display_name_too_long_rejected():
    """Display name > 120 chars returns error; original unchanged."""
    school = SchoolFactory()
    original_name = school.display_name
    client, _ = _owner_client(school)

    long_name = "A" * 121
    _post(client, school, action="update_display_name", display_name=long_name)
    school.refresh_from_db()
    assert school.display_name == original_name


@pytest.mark.django_db
def test_display_name_same_value_no_change_message():
    """Submitting the same name shows info message; no DB write."""
    school = SchoolFactory()
    client, _ = _owner_client(school)
    # Follow redirect to check messages
    response = client.post(
        _SETTINGS_URL.format(slug=school.slug),
        {"action": "update_display_name", "display_name": school.display_name},
        follow=True,
    )
    messages = [str(m) for m in response.context["messages"]]
    assert any("No change" in m for m in messages), (
        f"Expected 'No change' message for same-value submit, got: {messages}"
    )


@pytest.mark.django_db
def test_display_name_max_boundary_accepted():
    """Exactly 120-character name is accepted."""
    school = SchoolFactory()
    client, _ = _owner_client(school)
    new_name = "B" * 120

    _post(client, school, action="update_display_name", display_name=new_name)
    school.refresh_from_db()
    assert school.display_name == new_name


@pytest.mark.django_db
def test_display_name_editor_blocked():
    """Editor cannot update display name (owner required)."""
    school = SchoolFactory()
    original_name = school.display_name
    client, _ = _editor_client(school)

    response = _post(client, school, action="update_display_name", display_name="New Name")
    assert response.status_code == 404

    school.refresh_from_db()
    assert school.display_name == original_name


# ---------------------------------------------------------------------------
# Viewer: no access to settings page at all
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_viewer_cannot_access_settings_page():
    """
    Viewer role gets 404 on the settings page (baseline is 'editor').
    `require_school_role(request, school, "editor")` raises Http404 for viewers.
    """
    school = SchoolFactory()
    membership = SchoolAdminMembershipFactory(school=school, role="viewer")
    c = Client()
    c.force_login(membership.user)

    response = c.get(_SETTINGS_URL.format(slug=school.slug))
    assert response.status_code == 404
