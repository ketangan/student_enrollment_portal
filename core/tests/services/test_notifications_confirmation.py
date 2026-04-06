"""
Unit tests for applicant confirmation email functionality.

Covers:
- get_applicant_confirmation_config: parsing YAML block
- _find_applicant_email: locating email field in form config
- send_applicant_confirmation_email: end-to-end send behaviour
"""
from __future__ import annotations

import pytest
from django.core import mail

from core.services.notifications import (
    ApplicantConfirmationConfig,
    _find_applicant_email,
    get_applicant_confirmation_config,
    send_applicant_confirmation_email,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confirmation_block(
    *,
    enabled=True,
    from_email="noreply@school.com",
    subject="",
    message="",
):
    return {
        "success": {
            "notifications": {
                "applicant_confirmation": {
                    "enabled": enabled,
                    "from_email": from_email,
                    "subject": subject,
                    "message": message,
                }
            }
        }
    }


def _single_form_config(email_field_key="contact_email", required=True):
    return {
        "form": {
            "sections": [
                {
                    "title": "Contact",
                    "fields": [
                        {
                            "key": email_field_key,
                            "label": "Email",
                            "type": "email",
                            "required": required,
                        }
                    ],
                }
            ]
        }
    }


def _multi_form_config(email_field_key="contact_email", required=True):
    return {
        "forms": {
            "step1": {
                "title": "Step 1",
                "form": {
                    "sections": [
                        {
                            "title": "Personal",
                            "fields": [
                                {"key": "name", "label": "Name", "type": "text", "required": True}
                            ],
                        }
                    ]
                },
            },
            "step2": {
                "title": "Step 2",
                "form": {
                    "sections": [
                        {
                            "title": "Contact",
                            "fields": [
                                {
                                    "key": email_field_key,
                                    "label": "Email",
                                    "type": "email",
                                    "required": required,
                                }
                            ],
                        }
                    ]
                },
            },
        }
    }


# ---------------------------------------------------------------------------
# get_applicant_confirmation_config
# ---------------------------------------------------------------------------


def test_config_returns_dataclass_when_enabled():
    cfg = get_applicant_confirmation_config(_confirmation_block())
    assert isinstance(cfg, ApplicantConfirmationConfig)
    assert cfg.from_email == "noreply@school.com"


def test_config_returns_none_when_enabled_false():
    raw = _confirmation_block(enabled=False)
    assert get_applicant_confirmation_config(raw) is None


def test_config_returns_none_when_block_missing():
    assert get_applicant_confirmation_config({}) is None


def test_config_returns_none_when_config_raw_is_none():
    assert get_applicant_confirmation_config(None) is None  # type: ignore[arg-type]


def test_config_returns_none_when_notifications_key_absent():
    raw = {"success": {}}
    assert get_applicant_confirmation_config(raw) is None


def test_config_falls_back_to_default_from_email(settings):
    settings.DEFAULT_FROM_EMAIL = "default@fallback.com"
    raw = _confirmation_block()
    raw["success"]["notifications"]["applicant_confirmation"]["from_email"] = ""
    cfg = get_applicant_confirmation_config(raw)
    assert cfg is not None
    assert cfg.from_email == "default@fallback.com"


def test_config_returns_none_when_from_email_empty_and_no_default(settings):
    settings.DEFAULT_FROM_EMAIL = ""
    raw = _confirmation_block()
    raw["success"]["notifications"]["applicant_confirmation"]["from_email"] = ""
    assert get_applicant_confirmation_config(raw) is None


def test_config_stores_custom_subject_and_message():
    raw = _confirmation_block(subject="Got it, {{student_name}}!", message="We'll call you.")
    cfg = get_applicant_confirmation_config(raw)
    assert cfg is not None
    assert cfg.subject == "Got it, {{student_name}}!"
    assert cfg.message == "We'll call you."


def test_config_stores_empty_subject_and_message_when_omitted():
    raw = _confirmation_block()
    cfg = get_applicant_confirmation_config(raw)
    assert cfg is not None
    assert cfg.subject == ""
    assert cfg.message == ""


# ---------------------------------------------------------------------------
# _find_applicant_email
# ---------------------------------------------------------------------------


def test_find_email_returns_required_email_field():
    config_raw = _single_form_config(email_field_key="contact_email", required=True)
    data = {"contact_email": "jane@example.com"}
    assert _find_applicant_email(data, config_raw) == "jane@example.com"


def test_find_email_falls_back_to_optional_when_no_required():
    config_raw = _single_form_config(email_field_key="guardian_email", required=False)
    data = {"guardian_email": "parent@example.com"}
    assert _find_applicant_email(data, config_raw) == "parent@example.com"


def test_find_email_prefers_required_over_optional():
    config_raw = {
        "form": {
            "sections": [
                {
                    "title": "Contact",
                    "fields": [
                        {"key": "guardian_email", "type": "email", "required": False},
                        {"key": "contact_email", "type": "email", "required": True},
                    ],
                }
            ]
        }
    }
    data = {"guardian_email": "parent@example.com", "contact_email": "student@example.com"}
    assert _find_applicant_email(data, config_raw) == "student@example.com"


def test_find_email_returns_none_when_no_email_field():
    config_raw = {"form": {"sections": [{"title": "Info", "fields": [{"key": "name", "type": "text", "required": True}]}]}}
    assert _find_applicant_email({"name": "Alice"}, config_raw) is None


def test_find_email_returns_none_when_value_empty():
    config_raw = _single_form_config()
    assert _find_applicant_email({"contact_email": ""}, config_raw) is None
    assert _find_applicant_email({"contact_email": "   "}, config_raw) is None


def test_find_email_returns_none_when_submission_data_empty():
    config_raw = _single_form_config()
    assert _find_applicant_email({}, config_raw) is None


def test_find_email_works_in_multi_form_config():
    config_raw = _multi_form_config(email_field_key="contact_email", required=True)
    data = {"name": "Bob", "contact_email": "bob@example.com"}
    assert _find_applicant_email(data, config_raw) == "bob@example.com"


def test_find_email_strips_whitespace():
    config_raw = _single_form_config()
    data = {"contact_email": "  trimmed@example.com  "}
    assert _find_applicant_email(data, config_raw) == "trimmed@example.com"


def test_find_email_returns_none_for_empty_config_raw():
    assert _find_applicant_email({"contact_email": "x@y.com"}, {}) is None


# ---------------------------------------------------------------------------
# send_applicant_confirmation_email
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_send_confirmation_email_sends_to_applicant(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    config_raw = {
        **_single_form_config(),
        **_confirmation_block(from_email="noreply@school.com"),
    }

    ok = send_applicant_confirmation_email(
        config_raw=config_raw,
        school_name="Awesome Dance Studio",
        submission_public_id="ABC123XYZ",
        student_name="Alice Smith",
        submission_data={"contact_email": "alice@example.com"},
    )

    assert ok is True
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["alice@example.com"]
    assert "ABC123XYZ" in msg.body
    assert "Alice Smith" in msg.body


def test_send_confirmation_returns_false_when_config_disabled():
    raw = {
        **_single_form_config(),
        **_confirmation_block(enabled=False),
    }
    assert send_applicant_confirmation_email(
        config_raw=raw,
        school_name="School",
        submission_public_id="PUB1",
        student_name="Bob",
        submission_data={"contact_email": "bob@example.com"},
    ) is False


def test_send_confirmation_returns_false_when_config_block_missing():
    raw = _single_form_config()  # no applicant_confirmation block
    assert send_applicant_confirmation_email(
        config_raw=raw,
        school_name="School",
        submission_public_id="PUB1",
        student_name="Bob",
        submission_data={"contact_email": "bob@example.com"},
    ) is False


def test_send_confirmation_returns_false_when_no_email_in_data():
    raw = {
        **_single_form_config(),
        **_confirmation_block(),
    }
    # submission data has no email value
    assert send_applicant_confirmation_email(
        config_raw=raw,
        school_name="School",
        submission_public_id="PUB1",
        student_name="Bob",
        submission_data={"contact_email": ""},
    ) is False


@pytest.mark.django_db
def test_send_confirmation_renders_template_vars_in_subject(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    config_raw = {
        **_single_form_config(),
        **_confirmation_block(
            subject="Hi {{student_name}}, your app to {{school_name}} is in!"
        ),
    }

    send_applicant_confirmation_email(
        config_raw=config_raw,
        school_name="Art Academy",
        submission_public_id="PUB999",
        student_name="Carol",
        submission_data={"contact_email": "carol@example.com"},
    )

    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == "Hi Carol, your app to Art Academy is in!"


@pytest.mark.django_db
def test_send_confirmation_uses_default_subject_when_none_configured(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    config_raw = {
        **_single_form_config(),
        **_confirmation_block(subject=""),
    }

    send_applicant_confirmation_email(
        config_raw=config_raw,
        school_name="Ballet School",
        submission_public_id="PUB1",
        student_name="Alice",
        submission_data={"contact_email": "alice@example.com"},
    )

    assert len(mail.outbox) == 1
    assert "Ballet School" in mail.outbox[0].subject


@pytest.mark.django_db
def test_send_confirmation_includes_response_time_when_present(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    config_raw = {
        **_single_form_config(),
        **_confirmation_block(),
        "success": {
            **_confirmation_block()["success"],
            "response_time": "2–3 business days",
        },
    }
    # merge applicant_confirmation back in since we overwrote success
    config_raw["success"]["notifications"] = {
        "applicant_confirmation": {
            "enabled": True,
            "from_email": "noreply@school.com",
        }
    }

    send_applicant_confirmation_email(
        config_raw=config_raw,
        school_name="Studio",
        submission_public_id="PUB1",
        student_name="Dave",
        submission_data={"contact_email": "dave@example.com"},
    )

    assert len(mail.outbox) == 1
    assert "2–3 business days" in mail.outbox[0].body


@pytest.mark.django_db
def test_send_confirmation_email_has_html_alternative(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()

    config_raw = {
        **_single_form_config(),
        **_confirmation_block(),
    }

    send_applicant_confirmation_email(
        config_raw=config_raw,
        school_name="School",
        submission_public_id="PUB1",
        student_name="Eve",
        submission_data={"contact_email": "eve@example.com"},
    )

    msg = mail.outbox[0]
    html_bodies = [body for body, mime in getattr(msg, "alternatives", []) if mime == "text/html"]
    assert html_bodies, "Expected an HTML alternative"
    assert "PUB1" in html_bodies[0]
