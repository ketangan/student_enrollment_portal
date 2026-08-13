"""
Tests for the Broadcast Message feature (Phase 25).
Covers: feature flag gate, role gate, compose/preview/send flow,
audience deduplication, and sent history.
"""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from unittest.mock import patch

from core.models import (
    AdminAuditLog,
    BroadcastMessage,
    BroadcastRecipient,
    Lead,
    School,
    SchoolAdminMembership,
    Submission,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        username="bc_super", email="bc_super@test.com", password="testpass"
    )


@pytest.fixture
def editor_user(db):
    return User.objects.create_user(
        username="bc_editor", email="bc_editor@test.com", password="testpass",
        is_staff=True,
    )


@pytest.fixture
def viewer_user(db):
    return User.objects.create_user(
        username="bc_viewer", email="bc_viewer@test.com", password="testpass",
        is_staff=True,
    )


@pytest.fixture
def school_with_flag(db):
    """A school that has broadcast_enabled via feature_flags JSON override."""
    return School.objects.create(
        slug="bc-school",
        display_name="Broadcast School",
        plan="starter",
        feature_flags={"broadcast_enabled": True},
    )


@pytest.fixture
def school_no_flag(db):
    """A school without broadcast enabled."""
    return School.objects.create(
        slug="bc-noflag",
        display_name="No Flag School",
        plan="growth",
    )


@pytest.fixture
def editor_membership(db, school_with_flag, editor_user):
    return SchoolAdminMembership.objects.create(
        school=school_with_flag, user=editor_user, role="editor", is_active=True,
    )


@pytest.fixture
def viewer_membership(db, school_with_flag, viewer_user):
    return SchoolAdminMembership.objects.create(
        school=school_with_flag, user=viewer_user, role="viewer", is_active=True,
    )


@pytest.fixture
def lead_a(db, school_with_flag):
    return Lead.objects.create(
        school=school_with_flag, name="Alice Parent", email="alice@example.com",
        normalized_email="alice@example.com", status="new",
    )


@pytest.fixture
def lead_b(db, school_with_flag):
    return Lead.objects.create(
        school=school_with_flag, name="Bob Parent", email="bob@example.com",
        normalized_email="bob@example.com", status="contacted",
    )


@pytest.fixture
def submission_a(db, school_with_flag):
    return Submission.objects.create(
        school=school_with_flag, status="New",
        data={"contact_email": "alice@example.com", "parent_name": "Alice Parent"},
    )


@pytest.fixture
def submission_b(db, school_with_flag):
    return Submission.objects.create(
        school=school_with_flag, status="New",
        data={"contact_email": "carol@example.com", "parent_name": "Carol Parent"},
    )


# ── Feature flag tests ────────────────────────────────────────────────────────

class TestFeatureFlag:
    def test_flag_default_off_for_all_plans(self, db):
        for plan in ("trial", "starter", "pro", "growth", "custom"):
            school = School.objects.create(slug=f"ff-{plan}", plan=plan)
            assert not school.features.broadcast_enabled, f"Expected False for plan={plan}"

    def test_flag_on_via_json_override(self, school_with_flag):
        assert school_with_flag.features.broadcast_enabled is True

    def test_flag_off_when_override_false(self, db):
        school = School.objects.create(
            slug="ff-explicit-off", plan="growth",
            feature_flags={"broadcast_enabled": False},
        )
        assert not school.features.broadcast_enabled


# ── Access gate tests ─────────────────────────────────────────────────────────

class TestAccessGate:
    def test_compose_requires_login(self, client, school_with_flag):
        url = reverse("school_broadcast", kwargs={"school_slug": school_with_flag.slug})
        resp = client.get(url)
        assert resp.status_code in (302, 403)

    def test_compose_404_if_flag_off(self, client, superuser, school_no_flag):
        client.force_login(superuser)
        url = reverse("school_broadcast", kwargs={"school_slug": school_no_flag.slug})
        resp = client.get(url)
        assert resp.status_code == 404

    def test_compose_404_for_viewer(self, client, viewer_user, viewer_membership, school_with_flag):
        client.force_login(viewer_user)
        url = reverse("school_broadcast", kwargs={"school_slug": school_with_flag.slug})
        resp = client.get(url)
        assert resp.status_code == 404

    def test_compose_200_for_editor(self, client, editor_user, editor_membership, school_with_flag):
        client.force_login(editor_user)
        url = reverse("school_broadcast", kwargs={"school_slug": school_with_flag.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def test_compose_200_for_superuser(self, client, superuser, school_with_flag):
        client.force_login(superuser)
        url = reverse("school_broadcast", kwargs={"school_slug": school_with_flag.slug})
        resp = client.get(url)
        assert resp.status_code == 200


# ── Compose → Preview flow ────────────────────────────────────────────────────

class TestComposePreviewFlow:
    def _compose_url(self, school):
        return reverse("school_broadcast", kwargs={"school_slug": school.slug})

    def _preview_url(self, school):
        return reverse("school_broadcast_preview", kwargs={"school_slug": school.slug})

    def test_compose_post_missing_subject_shows_error(self, client, superuser, school_with_flag, lead_a):
        client.force_login(superuser)
        resp = client.post(self._compose_url(school_with_flag), {
            "subject": "", "body": "Hello!", "include_leads": "on",
        })
        assert resp.status_code == 200
        assert b"Subject is required" in resp.content

    def test_compose_post_no_audience_shows_error(self, client, superuser, school_with_flag):
        client.force_login(superuser)
        resp = client.post(self._compose_url(school_with_flag), {
            "subject": "Test", "body": "Hello!",
        })
        assert resp.status_code == 200
        assert b"at least one audience" in resp.content

    def test_compose_post_valid_redirects_to_preview(self, client, superuser, school_with_flag, lead_a):
        client.force_login(superuser)
        resp = client.post(self._compose_url(school_with_flag), {
            "subject": "Test Subject", "body": "Hello leads!",
            "include_leads": "on",
        })
        assert resp.status_code == 302
        assert resp["Location"].endswith(self._preview_url(school_with_flag))

    def test_preview_get_without_session_redirects_to_compose(self, client, superuser, school_with_flag):
        client.force_login(superuser)
        resp = client.get(self._preview_url(school_with_flag))
        assert resp.status_code == 302
        assert resp["Location"].endswith(self._compose_url(school_with_flag))

    def test_preview_shows_recipient_count(self, client, superuser, school_with_flag, lead_a, lead_b):
        client.force_login(superuser)
        # Post compose to set session
        client.post(self._compose_url(school_with_flag), {
            "subject": "Test", "body": "Hello!", "include_leads": "on",
        })
        resp = client.get(self._preview_url(school_with_flag))
        assert resp.status_code == 200
        assert b"2" in resp.content  # 2 leads


# ── Audience deduplication ────────────────────────────────────────────────────

class TestAudienceBuilding:
    from core.views_broadcast import _build_audience

    def test_deduplication_across_leads_and_submissions(
        self, db, school_with_flag, lead_a, lead_b, submission_a, submission_b
    ):
        from core.views_broadcast import _build_audience
        # lead_a and submission_a both have alice@example.com — should count once
        recipients, skipped = _build_audience(
            school_with_flag,
            include_leads=True,
            include_submissions=True,
            leads_filter={},
            submissions_filter={},
        )
        emails = [r["email"].lower() for r in recipients]
        assert emails.count("alice@example.com") == 1
        assert "bob@example.com" in emails
        assert "carol@example.com" in emails
        assert len(recipients) == 3

    def test_leads_only(self, db, school_with_flag, lead_a, lead_b):
        from core.views_broadcast import _build_audience
        recipients, _ = _build_audience(
            school_with_flag, include_leads=True, include_submissions=False,
            leads_filter={}, submissions_filter={},
        )
        assert len(recipients) == 2

    def test_leads_filtered_by_status(self, db, school_with_flag, lead_a, lead_b):
        from core.views_broadcast import _build_audience
        # lead_a is "new", lead_b is "contacted"
        recipients, _ = _build_audience(
            school_with_flag, include_leads=True, include_submissions=False,
            leads_filter={"statuses": ["new"]}, submissions_filter={},
        )
        assert len(recipients) == 1
        assert recipients[0]["email"] == "alice@example.com"

    def test_submissions_only(self, db, school_with_flag, submission_a, submission_b):
        from core.views_broadcast import _build_audience
        recipients, _ = _build_audience(
            school_with_flag, include_leads=False, include_submissions=True,
            leads_filter={}, submissions_filter={},
        )
        assert len(recipients) == 2


# ── Send flow ─────────────────────────────────────────────────────────────────

class TestSendFlow:
    def _compose_url(self, school):
        return reverse("school_broadcast", kwargs={"school_slug": school.slug})

    def _preview_url(self, school):
        return reverse("school_broadcast_preview", kwargs={"school_slug": school.slug})

    def test_send_creates_broadcast_and_recipients(
        self, client, superuser, school_with_flag, lead_a, lead_b
    ):
        client.force_login(superuser)
        # Compose
        client.post(self._compose_url(school_with_flag), {
            "subject": "Hello everyone", "body": "Big news!", "include_leads": "on",
        })
        # Send
        with patch("core.views_broadcast.send_admin_message", return_value=True) as mock_send:
            resp = client.post(self._preview_url(school_with_flag))

        assert resp.status_code == 302
        assert resp["Location"] == self._compose_url(school_with_flag) + "?tab=sent"

        bm = BroadcastMessage.objects.get(school=school_with_flag)
        assert bm.subject == "Hello everyone"
        assert bm.sent_count == 2
        assert bm.recipient_count == 2
        assert BroadcastRecipient.objects.filter(broadcast=bm).count() == 2
        assert mock_send.call_count == 2

    def test_send_handles_partial_failure(
        self, client, superuser, school_with_flag, lead_a, lead_b
    ):
        client.force_login(superuser)
        client.post(self._compose_url(school_with_flag), {
            "subject": "Test", "body": "Test body", "include_leads": "on",
        })
        # First send succeeds, second fails
        with patch("core.views_broadcast.send_admin_message", side_effect=[True, False]):
            client.post(self._preview_url(school_with_flag))

        bm = BroadcastMessage.objects.get(school=school_with_flag)
        assert bm.sent_count == 1
        assert bm.failed_count == 1

    def test_send_clears_session_draft(
        self, client, superuser, school_with_flag, lead_a
    ):
        client.force_login(superuser)
        client.post(self._compose_url(school_with_flag), {
            "subject": "Test", "body": "Body", "include_leads": "on",
        })
        assert "broadcast_draft" in client.session
        with patch("core.views_broadcast.send_admin_message", return_value=True):
            client.post(self._preview_url(school_with_flag))
        assert "broadcast_draft" not in client.session

    def test_send_no_recipients_stays_on_preview(
        self, client, superuser, school_with_flag
    ):
        client.force_login(superuser)
        # Include leads but there are no leads → 0 recipients
        client.post(self._compose_url(school_with_flag), {
            "subject": "Test", "body": "Body", "include_leads": "on",
        })
        with patch("core.views_broadcast.send_admin_message", return_value=True):
            resp = client.post(self._preview_url(school_with_flag))
        # Should redirect back to preview, not compose
        assert "broadcast" in resp["Location"]
        assert BroadcastMessage.objects.filter(school=school_with_flag).count() == 0

    def test_send_creates_audit_log(
        self, client, superuser, school_with_flag, lead_a, lead_b
    ):
        client.force_login(superuser)
        client.post(self._compose_url(school_with_flag), {
            "subject": "Audit test", "body": "Body", "include_leads": "on",
        })
        with patch("core.views_broadcast.send_admin_message", return_value=True):
            client.post(self._preview_url(school_with_flag))

        bm = BroadcastMessage.objects.get(school=school_with_flag)
        log = AdminAuditLog.objects.filter(
            action="action",
            object_id=str(bm.pk),
        ).first()
        assert log is not None, "AdminAuditLog entry should be created on broadcast send"
        assert log.extra["name"] == "broadcast_sent"
        assert log.extra["sent_count"] == 2
        assert log.extra["failed_count"] == 0
        assert log.extra["recipient_count"] == 2
        assert log.actor == superuser

    def test_send_no_recipients_creates_no_audit_log(
        self, client, superuser, school_with_flag
    ):
        client.force_login(superuser)
        client.post(self._compose_url(school_with_flag), {
            "subject": "No recips", "body": "Body", "include_leads": "on",
        })
        before = AdminAuditLog.objects.count()
        with patch("core.views_broadcast.send_admin_message", return_value=True):
            client.post(self._preview_url(school_with_flag))
        assert AdminAuditLog.objects.count() == before


# ── Sent history ──────────────────────────────────────────────────────────────

class TestSentHistory:
    def test_sent_broadcasts_appear_on_compose_page(
        self, client, superuser, school_with_flag
    ):
        client.force_login(superuser)
        bm = BroadcastMessage.objects.create(
            school=school_with_flag,
            subject="Previous broadcast",
            body="Previous body",
            created_by=superuser,
            sent_count=5,
            recipient_count=5,
        )
        url = reverse("school_broadcast", kwargs={"school_slug": school_with_flag.slug})
        resp = client.get(url)
        assert resp.status_code == 200
        assert b"Previous broadcast" in resp.content

    def test_recipients_stored_and_visible_in_history(
        self, client, superuser, school_with_flag
    ):
        client.force_login(superuser)
        bm = BroadcastMessage.objects.create(
            school=school_with_flag, subject="Test", body="Body",
            created_by=superuser, sent_count=1, recipient_count=1,
        )
        BroadcastRecipient.objects.create(
            broadcast=bm, email="test@example.com", name="Test Person",
            source="lead", status="sent",
        )
        url = reverse("school_broadcast", kwargs={"school_slug": school_with_flag.slug})
        resp = client.get(url)
        assert b"test@example.com" in resp.content


# ── Nav link integration tests ────────────────────────────────────────────────

class TestBroadcastNavLink:
    """
    The Broadcast nav link must appear on every school admin page when the flag is on,
    and must be absent when the flag is off.

    This class guards against the bug where the dashboard (and any other view that
    builds its own context dict) forgets to pass `broadcast_enabled`.
    """

    BROADCAST_HREF_TPL = '/schools/{slug}/admin/broadcast/'

    def _href(self, school):
        return self.BROADCAST_HREF_TPL.format(slug=school.slug)

    @pytest.mark.parametrize("url_name,kwargs_extra", [
        ("school_dashboard", {}),
        ("school_submissions", {}),
        ("school_reports", {}),
        ("school_broadcast", {}),
    ])
    def test_broadcast_nav_shows_on_all_main_pages(
        self, client, superuser, school_with_flag, url_name, kwargs_extra
    ):
        client.force_login(superuser)
        url = reverse(url_name, kwargs={"school_slug": school_with_flag.slug, **kwargs_extra})
        resp = client.get(url)
        assert resp.status_code == 200, f"{url} returned {resp.status_code}"
        assert self._href(school_with_flag).encode() in resp.content, (
            f"Broadcast nav link missing on {url_name} ({url})"
        )

    def test_broadcast_nav_absent_when_flag_off(self, client, superuser, school_no_flag):
        client.force_login(superuser)
        url = reverse("school_dashboard", kwargs={"school_slug": school_no_flag.slug})
        resp = client.get(url)
        assert resp.status_code == 200
        assert self._href(school_no_flag).encode() not in resp.content, (
            "Broadcast nav link should not appear when flag is off"
        )
