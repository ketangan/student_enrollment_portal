# core/tests/test_ai_summary.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from core.services.ai_summary import (
    _build_prompt,
    _build_submission_text,
    generate_ai_summary,
)
from core.tests.factories import SchoolAdminMembershipFactory, SchoolFactory, SubmissionFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GOOD_RESPONSE = '{"summary": "A motivated student.", "criteria_scores": []}'

_CONFIG_RAW = {
    "form": {
        "sections": [
            {
                "fields": [
                    {"key": "first_name", "label": "First Name", "type": "text"},
                    {"key": "dance_style", "label": "Dance Style", "type": "select"},
                ]
            }
        ]
    }
}


def _mock_anthropic_client(response_text=_GOOD_RESPONSE):
    """Return a mock anthropic.Anthropic client whose messages.create returns response_text."""
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=response_text)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_message
    return mock_client


# ---------------------------------------------------------------------------
# _build_submission_text
# ---------------------------------------------------------------------------

def test_build_submission_text_uses_labels():
    data = {"first_name": "Alice", "dance_style": "ballet"}
    text = _build_submission_text(data, _CONFIG_RAW["form"])
    assert "First Name: Alice" in text
    assert "Dance Style: ballet" in text


def test_build_submission_text_skips_waiver_metadata():
    data = {
        "liability_waiver": True,
        "liability_waiver__at": "2026-01-01T00:00:00Z",
        "liability_waiver__ip": "1.2.3.4",
        "liability_waiver__text": "I agree",
        "liability_waiver__link_url": "https://example.com",
        "first_name": "Bob",
    }
    text = _build_submission_text(data, {})
    assert "__at" not in text
    assert "__ip" not in text
    assert "__text" not in text
    assert "__link_url" not in text
    assert "Bob" in text


def test_build_submission_text_bool_values():
    data = {"has_experience": True, "needs_costume": False}
    text = _build_submission_text(data, {})
    assert "Yes" in text
    assert "No" in text


def test_build_submission_text_list_values():
    data = {"days": ["Monday", "Wednesday"]}
    text = _build_submission_text(data, {})
    assert "Monday, Wednesday" in text


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------

def test_build_prompt_includes_criteria():
    prompt = _build_prompt("Name: Alice", "Dance Studio", ["Prior experience", "Medical notes"])
    assert "Prior experience" in prompt
    assert "Medical notes" in prompt
    assert "criteria_scores" in prompt


def test_build_prompt_no_criteria_section_when_empty():
    prompt = _build_prompt("Name: Alice", "Dance Studio", [])
    assert "criteria_scores" not in prompt


# ---------------------------------------------------------------------------
# generate_ai_summary — service unit tests
# ---------------------------------------------------------------------------

def test_generate_returns_none_when_no_api_key(settings):
    settings.ANTHROPIC_API_KEY = ""
    result = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )
    assert result is None


def test_generate_returns_none_when_no_api_key_attr(settings):
    if hasattr(settings, "ANTHROPIC_API_KEY"):
        del settings.ANTHROPIC_API_KEY
    result = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )
    assert result is None


def test_generate_returns_none_on_empty_submission(settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    result = generate_ai_summary(
        submission_data={},
        school_name="Test School",
        form_cfg={},
    )
    assert result is None


@patch("anthropic.Anthropic")
def test_generate_calls_claude_sonnet(mock_anthropic_class, settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_client = _mock_anthropic_client()
    mock_anthropic_class.return_value = mock_client

    generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"


@patch("anthropic.Anthropic")
def test_generate_returns_summary_dict(mock_anthropic_class, settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_client = _mock_anthropic_client()
    mock_anthropic_class.return_value = mock_client

    result = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )

    assert result is not None
    assert result["summary"] == "A motivated student."
    assert result["criteria_scores"] == []


@patch("anthropic.Anthropic")
def test_generate_handles_markdown_fences(mock_anthropic_class, settings):
    """Model wrapping JSON in ```json ... ``` should still parse correctly."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    wrapped = '```json\n{"summary": "Good applicant.", "criteria_scores": []}\n```'
    mock_anthropic_class.return_value = _mock_anthropic_client(wrapped)

    result = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )

    assert result is not None
    assert result["summary"] == "Good applicant."


@patch("anthropic.Anthropic")
def test_generate_handles_non_json_response(mock_anthropic_class, settings):
    """Non-JSON response is wrapped as plain summary instead of crashing."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_anthropic_class.return_value = _mock_anthropic_client("Just some text.")

    result = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )

    assert result is not None
    assert result["summary"] == "Just some text."
    assert result["criteria_scores"] == []


@patch("anthropic.Anthropic")
def test_generate_returns_none_on_api_exception(mock_anthropic_class, settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("Network error")
    mock_anthropic_class.return_value = mock_client

    result = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )
    assert result is None


@patch("anthropic.Anthropic")
def test_generate_truncates_long_submission(mock_anthropic_class, settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_client = _mock_anthropic_client()
    mock_anthropic_class.return_value = mock_client

    long_data = {"notes": "x" * 5000}
    generate_ai_summary(
        submission_data=long_data,
        school_name="Test School",
        form_cfg={},
    )

    content = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert len(content) < 4500  # truncation applied; well under the original 5000+


# ---------------------------------------------------------------------------
# Admin view: generate_summary_view
# ---------------------------------------------------------------------------

def _login_staff(client, school):
    membership = SchoolAdminMembershipFactory(school=school)
    client.force_login(membership.user)
    return membership.user


@pytest.mark.django_db
def test_generate_summary_view_get_redirects(client):
    school = SchoolFactory(plan="pro", slug="ai-get-test")
    _login_staff(client, school)
    sub = SubmissionFactory(school=school)

    url = reverse("admin:core_submission_generate_summary", args=[sub.pk])
    resp = client.get(url)
    assert resp.status_code == 302


@pytest.mark.django_db
def test_generate_summary_view_requires_feature_flag(client):
    """Starter plan cannot generate summaries."""
    school = SchoolFactory(plan="starter", slug="ai-starter-test")
    _login_staff(client, school)
    sub = SubmissionFactory(school=school)

    url = reverse("admin:core_submission_generate_summary", args=[sub.pk])
    resp = client.post(url)
    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.ai_summary is None


@pytest.mark.django_db
@patch("core.admin.submissions.generate_ai_summary")
def test_generate_summary_view_saves_on_success(mock_gen, client):
    mock_gen.return_value = {"summary": "Great student.", "criteria_scores": []}

    school = SchoolFactory(plan="pro", slug="ai-save-test")
    _login_staff(client, school)
    sub = SubmissionFactory(school=school, data={"first_name": "Alice"})

    url = reverse("admin:core_submission_generate_summary", args=[sub.pk])
    resp = client.post(url)
    assert resp.status_code == 302

    sub.refresh_from_db()
    assert sub.ai_summary is not None
    assert sub.ai_summary["summary"] == "Great student."
    assert sub.ai_summary_at is not None


@pytest.mark.django_db
@patch("core.admin.submissions.generate_ai_summary")
def test_generate_summary_view_error_on_none(mock_gen, client):
    """When generate_ai_summary returns None, show error message, don't save."""
    mock_gen.return_value = None

    school = SchoolFactory(plan="pro", slug="ai-none-test")
    _login_staff(client, school)
    sub = SubmissionFactory(school=school, data={"first_name": "Alice"})

    url = reverse("admin:core_submission_generate_summary", args=[sub.pk])
    resp = client.post(url, follow=True)

    sub.refresh_from_db()
    assert sub.ai_summary is None
    messages_list = [str(m) for m in resp.context["messages"]]
    assert any("Could not generate" in m for m in messages_list)


@pytest.mark.django_db
@patch("core.admin.submissions.generate_ai_summary")
def test_generate_summary_view_saves_timestamp(mock_gen, client):
    mock_gen.return_value = {"summary": "Solid.", "criteria_scores": []}

    school = SchoolFactory(plan="pro", slug="ai-ts-test")
    _login_staff(client, school)
    sub = SubmissionFactory(school=school, data={"first_name": "Alice"})

    url = reverse("admin:core_submission_generate_summary", args=[sub.pk])
    client.post(url)

    sub.refresh_from_db()
    assert sub.ai_summary_at is not None


# ---------------------------------------------------------------------------
# ai_summary_display rendering
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ai_summary_display_no_summary_shows_prompt(admin_client):
    """Pro school with no summary: shows 'No summary yet' message."""
    school = SchoolFactory(plan="pro", slug="ai-disp-empty")
    sub = SubmissionFactory(school=school, ai_summary=None)

    url = reverse("admin:core_submission_change", args=[sub.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert b"No summary yet" in resp.content


@pytest.mark.django_db
def test_ai_summary_display_renders_summary(admin_client):
    school = SchoolFactory(plan="pro", slug="ai-disp-has")
    sub = SubmissionFactory(
        school=school,
        ai_summary={"summary": "Excellent dancer.", "criteria_scores": []},
    )

    url = reverse("admin:core_submission_change", args=[sub.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert b"Excellent dancer." in resp.content


@pytest.mark.django_db
def test_ai_summary_display_renders_criteria_scores(admin_client):
    school = SchoolFactory(plan="pro", slug="ai-disp-criteria")
    sub = SubmissionFactory(
        school=school,
        ai_summary={
            "summary": "Decent applicant.",
            "criteria_scores": [
                {"criterion": "Experience", "assessment": "Intermediate", "note": "3 years mentioned"}
            ],
        },
    )

    url = reverse("admin:core_submission_change", args=[sub.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert b"Experience" in resp.content
    assert b"Intermediate" in resp.content
    assert b"3 years mentioned" in resp.content


@pytest.mark.django_db
def test_ai_summary_section_hidden_for_starter(admin_client):
    """Starter plan: AI Summary fieldset not rendered at all."""
    school = SchoolFactory(plan="starter", slug="ai-disp-starter")
    sub = SubmissionFactory(school=school)

    url = reverse("admin:core_submission_change", args=[sub.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert b"Generate AI Summary" not in resp.content


@pytest.mark.django_db
def test_generate_button_present_for_pro(admin_client):
    """Pro plan: 'Generate AI Summary' button rendered in change form."""
    school = SchoolFactory(plan="pro", slug="ai-btn-pro")
    sub = SubmissionFactory(school=school)

    url = reverse("admin:core_submission_change", args=[sub.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert b"Generate AI Summary" in resp.content
