"""
Tests for DB-driven notification recipients.

Covers:
- validate_email_list helper
- get_submission_email_config: DB takes priority over YAML; falls back to YAML when DB empty
- send_submission_notification_email: recipients come from DB when set
- send_lead_admin_notification: DB field takes priority; supports multiple recipients; YAML fallback
- Settings view: save valid recipients, reject invalid, require owner role
- Data migration: SBMC seed present in test DB (sanity only — migration tested via migrate)
"""
import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from core.models import School, SchoolAdminMembership, Submission, Lead
from core.services.notifications import (
    get_submission_email_config,
    send_submission_notification_email,
    send_lead_admin_notification,
    validate_email_list,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def school(db):
    return School.objects.create(
        slug="notif-recip-school",
        display_name="Notif Recipient School",
        plan="starter",
        trial_started_at=timezone.now(),
    )


@pytest.fixture
def owner(db, school):
    u = User.objects.create_user("notif_recip_owner", password="pass", is_staff=True)
    SchoolAdminMembership.objects.create(user=u, school=school, role="owner", is_active=True)
    return u


@pytest.fixture
def editor(db, school):
    u = User.objects.create_user("notif_recip_editor", password="pass", is_staff=True)
    SchoolAdminMembership.objects.create(user=u, school=school, role="editor", is_active=True)
    return u


@pytest.fixture
def submission(db, school):
    return Submission.objects.create(
        school=school,
        data={"child_first_name": "Alice", "child_last_name": "Test"},
        status="New",
    )


@pytest.fixture
def lead(db, school):
    return Lead.objects.create(school=school, name="Bob Test", email="bob@example.com")


def _yaml_config(to="yaml@example.com", cc="", bcc=""):
    return {
        "success": {
            "notifications": {
                "submission_email": {
                    "to": to,
                    "cc": cc,
                    "bcc": bcc,
                    "from_email": "no-reply@example.com",
                    "subject": "New submission",
                }
            }
        }
    }


# ── validate_email_list ───────────────────────────────────────────────────────

def test_validate_email_list_empty():
    valid, invalid = validate_email_list("")
    assert valid == []
    assert invalid == []


def test_validate_email_list_single_valid():
    valid, invalid = validate_email_list("a@b.com")
    assert valid == ["a@b.com"]
    assert invalid == []


def test_validate_email_list_multiple_valid():
    valid, invalid = validate_email_list("a@b.com, c@d.com")
    assert set(valid) == {"a@b.com", "c@d.com"}
    assert invalid == []


def test_validate_email_list_mixed():
    valid, invalid = validate_email_list("good@b.com, notanemail, also@bad")
    assert valid == ["good@b.com"]
    assert set(invalid) == {"notanemail", "also@bad"}


def test_validate_email_list_whitespace_only():
    valid, invalid = validate_email_list("   ")
    assert valid == []
    assert invalid == []


# ── get_submission_email_config ───────────────────────────────────────────────

@pytest.mark.django_db
def test_config_falls_back_to_yaml_when_db_empty(school):
    cfg = get_submission_email_config(_yaml_config(), school=school)
    assert cfg is not None
    assert cfg.to == ["yaml@example.com"]
    assert cfg.cc == []
    assert cfg.bcc == []


@pytest.mark.django_db
def test_config_uses_db_when_set(school):
    school.notification_to_emails = "db@example.com"
    school.save(update_fields=["notification_to_emails"])

    cfg = get_submission_email_config(_yaml_config(), school=school)
    assert cfg is not None
    assert cfg.to == ["db@example.com"]


@pytest.mark.django_db
def test_config_db_overrides_yaml_to(school):
    school.notification_to_emails = "db@example.com"
    school.save(update_fields=["notification_to_emails"])

    cfg = get_submission_email_config(_yaml_config(to="yaml@example.com"), school=school)
    assert "db@example.com" in cfg.to
    assert "yaml@example.com" not in cfg.to


@pytest.mark.django_db
def test_config_db_multiple_recipients(school):
    school.notification_to_emails = "a@x.com, b@x.com"
    school.notification_cc_emails = "c@x.com"
    school.notification_bcc_emails = "d@x.com"
    school.save(update_fields=["notification_to_emails", "notification_cc_emails", "notification_bcc_emails"])

    cfg = get_submission_email_config(_yaml_config(), school=school)
    assert set(cfg.to) == {"a@x.com", "b@x.com"}
    assert cfg.cc == ["c@x.com"]
    assert cfg.bcc == ["d@x.com"]


@pytest.mark.django_db
def test_config_no_recipients_returns_none(school):
    # No DB field, no YAML → None
    cfg = get_submission_email_config(_yaml_config(to=""), school=school)
    assert cfg is None


@pytest.mark.django_db
def test_config_no_school_uses_yaml():
    cfg = get_submission_email_config(_yaml_config(to="yaml@example.com"), school=None)
    assert cfg is not None
    assert cfg.to == ["yaml@example.com"]


# ── send_submission_notification_email ────────────────────────────────────────

@pytest.mark.django_db
def test_submission_notification_sends_to_db_recipients(settings, school, submission):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    school.notification_to_emails = "db-admin@example.com"
    school.save(update_fields=["notification_to_emails"])

    ok = send_submission_notification_email(
        request=None,
        config_raw=_yaml_config(to="yaml@example.com"),
        school_name=school.display_name,
        submission_id=submission.id,
        submission_public_id=submission.public_id,
        student_name="Alice Test",
        submission_data={},
        school=school,
    )

    assert ok is True
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["db-admin@example.com"]


@pytest.mark.django_db
def test_submission_notification_falls_back_to_yaml(settings, school, submission):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    # DB field is empty — should use YAML
    ok = send_submission_notification_email(
        request=None,
        config_raw=_yaml_config(to="yaml@example.com"),
        school_name=school.display_name,
        submission_id=submission.id,
        submission_public_id=submission.public_id,
        student_name="Alice Test",
        submission_data={},
        school=school,
    )

    assert ok is True
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["yaml@example.com"]


@pytest.mark.django_db
def test_submission_notification_multiple_recipients(settings, school, submission):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    school.notification_to_emails = "a@x.com, b@x.com"
    school.save(update_fields=["notification_to_emails"])

    send_submission_notification_email(
        request=None,
        config_raw=_yaml_config(),
        school_name=school.display_name,
        submission_id=submission.id,
        submission_public_id=submission.public_id,
        student_name="Alice Test",
        submission_data={},
        school=school,
    )

    assert set(mail.outbox[0].to) == {"a@x.com", "b@x.com"}


# ── send_lead_admin_notification ──────────────────────────────────────────────

@pytest.mark.django_db
def test_lead_notification_uses_db_field(settings, school, lead):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    school.leads_notify_to_emails = "leads-db@example.com"
    school.save(update_fields=["leads_notify_to_emails"])

    ok = send_lead_admin_notification(
        school=school,
        lead=lead,
        config_raw={"leads": {"notify_to": "yaml-leads@example.com"}},
    )

    assert ok is True
    assert mail.outbox[0].to == ["leads-db@example.com"]


@pytest.mark.django_db
def test_lead_notification_falls_back_to_yaml(settings, school, lead):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    ok = send_lead_admin_notification(
        school=school,
        lead=lead,
        config_raw={"leads": {"notify_to": "yaml-leads@example.com"}},
    )

    assert ok is True
    assert mail.outbox[0].to == ["yaml-leads@example.com"]


@pytest.mark.django_db
def test_lead_notification_multiple_db_recipients(settings, school, lead):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    school.leads_notify_to_emails = "a@x.com, b@x.com"
    school.save(update_fields=["leads_notify_to_emails"])

    ok = send_lead_admin_notification(
        school=school,
        lead=lead,
        config_raw={},
    )

    assert ok is True
    assert set(mail.outbox[0].to) == {"a@x.com", "b@x.com"}


@pytest.mark.django_db
def test_lead_notification_no_recipients_returns_false(settings, school, lead):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    ok = send_lead_admin_notification(
        school=school,
        lead=lead,
        config_raw={},
    )

    assert ok is False
    assert len(mail.outbox) == 0


# ── Settings view: update_notification_emails ─────────────────────────────────

@pytest.mark.django_db
def test_settings_save_valid_recipients(client, school, owner):
    client.force_login(owner)
    url = reverse("school_settings", kwargs={"school_slug": school.slug}) + "?tab=email"
    r = client.post(url, {
        "action": "update_notification_emails",
        "notification_to_emails": "emily@example.com, admin@example.com",
        "notification_cc_emails": "",
        "notification_bcc_emails": "",
        "leads_notify_to_emails": "emily@example.com",
    })
    assert r.status_code == 302
    school.refresh_from_db()
    assert "emily@example.com" in school.notification_to_emails
    assert "admin@example.com" in school.notification_to_emails
    assert school.leads_notify_to_emails == "emily@example.com"


@pytest.mark.django_db
def test_settings_rejects_invalid_email(client, school, owner):
    client.force_login(owner)
    url = reverse("school_settings", kwargs={"school_slug": school.slug})
    r = client.post(url, {
        "action": "update_notification_emails",
        "notification_to_emails": "notanemail",
        "notification_cc_emails": "",
        "notification_bcc_emails": "",
        "leads_notify_to_emails": "",
    }, follow=True)
    # Should redirect back with an error message
    assert r.status_code == 200
    messages_list = list(r.context["messages"])
    assert any("invalid" in str(m).lower() for m in messages_list)
    # DB should not have been updated
    school.refresh_from_db()
    assert school.notification_to_emails == ""


@pytest.mark.django_db
def test_settings_rejects_cc_without_to(client, school, owner):
    client.force_login(owner)
    url = reverse("school_settings", kwargs={"school_slug": school.slug})
    r = client.post(url, {
        "action": "update_notification_emails",
        "notification_to_emails": "",
        "notification_cc_emails": "cc@example.com",
        "notification_bcc_emails": "",
        "leads_notify_to_emails": "",
    }, follow=True)
    assert r.status_code == 200
    messages_list = list(r.context["messages"])
    assert any("to" in str(m).lower() for m in messages_list)
    school.refresh_from_db()
    assert school.notification_cc_emails == ""


@pytest.mark.django_db
def test_settings_requires_owner_role(client, school, editor):
    client.force_login(editor)
    url = reverse("school_settings", kwargs={"school_slug": school.slug})
    r = client.post(url, {
        "action": "update_notification_emails",
        "notification_to_emails": "anyone@example.com",
        "notification_cc_emails": "",
        "notification_bcc_emails": "",
        "leads_notify_to_emails": "",
    })
    # require_school_role raises Http404 for insufficient role
    assert r.status_code == 404
    school.refresh_from_db()
    assert school.notification_to_emails == ""


@pytest.mark.django_db
def test_settings_clears_recipients(client, school, owner):
    school.notification_to_emails = "old@example.com"
    school.save(update_fields=["notification_to_emails"])

    client.force_login(owner)
    url = reverse("school_settings", kwargs={"school_slug": school.slug})
    r = client.post(url, {
        "action": "update_notification_emails",
        "notification_to_emails": "",
        "notification_cc_emails": "",
        "notification_bcc_emails": "",
        "leads_notify_to_emails": "",
    })
    assert r.status_code == 302
    school.refresh_from_db()
    assert school.notification_to_emails == ""


@pytest.mark.django_db
def test_settings_get_renders_current_values(client, school, owner):
    school.notification_to_emails = "emily@example.com"
    school.leads_notify_to_emails = "emily@example.com"
    school.save(update_fields=["notification_to_emails", "leads_notify_to_emails"])

    client.force_login(owner)
    url = reverse("school_settings", kwargs={"school_slug": school.slug}) + "?tab=email"
    r = client.get(url)
    assert r.status_code == 200
    assert "emily@example.com" in r.content.decode()


@pytest.mark.django_db
def test_settings_shows_warning_when_no_to_recipient(client, school, owner):
    # DB field empty — warning banner should be visible
    client.force_login(owner)
    url = reverse("school_settings", kwargs={"school_slug": school.slug}) + "?tab=email"
    r = client.get(url)
    assert r.status_code == 200
    assert "No notification recipient set" in r.content.decode()


@pytest.mark.django_db
def test_settings_no_warning_when_to_recipient_set(client, school, owner):
    school.notification_to_emails = "emily@example.com"
    school.save(update_fields=["notification_to_emails"])

    client.force_login(owner)
    url = reverse("school_settings", kwargs={"school_slug": school.slug}) + "?tab=email"
    r = client.get(url)
    assert r.status_code == 200
    assert "No notification recipient set" not in r.content.decode()
