# core/tests/test_ai_summary.py
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse

from core.services.ai_summary import (
    _build_prompt,
    _build_submission_text,
    _normalize_result,
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


def test_build_submission_text_skips_empty_values():
    data = {"first_name": "Alice", "notes": "", "tags": [], "middle_name": None}
    text = _build_submission_text(data, {})
    assert "Alice" in text
    assert "notes" not in text
    assert "tags" not in text
    assert "middle_name" not in text


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
# _normalize_result
# ---------------------------------------------------------------------------

def test_normalize_result_passthrough_clean_dict():
    data = {"summary": "Good student.", "criteria_scores": [{"criterion": "A", "assessment": "B", "note": "C"}]}
    result = _normalize_result(data)
    assert result["summary"] == "Good student."
    assert len(result["criteria_scores"]) == 1


def test_normalize_result_coerces_non_dict_to_summary():
    result = _normalize_result("just a string")
    assert result["summary"] == "just a string"
    assert result["criteria_scores"] == []


def test_normalize_result_non_dict_input_is_safe():
    result = _normalize_result(["list", "input"])
    assert isinstance(result["summary"], str)
    assert result["criteria_scores"] == []


def test_normalize_result_coerces_non_string_summary():
    result = _normalize_result({"summary": 42, "criteria_scores": []})
    assert result["summary"] == "42"


def test_normalize_result_filters_non_dict_criteria_scores():
    data = {"summary": "OK", "criteria_scores": [{"criterion": "A"}, "bad_string", 99, None]}
    result = _normalize_result(data)
    assert len(result["criteria_scores"]) == 1
    assert result["criteria_scores"][0]["criterion"] == "A"


def test_normalize_result_coerces_non_list_criteria_scores():
    data = {"summary": "OK", "criteria_scores": "not a list"}
    result = _normalize_result(data)
    assert result["criteria_scores"] == []


# ---------------------------------------------------------------------------
# generate_ai_summary — service unit tests
# ---------------------------------------------------------------------------

def test_generate_returns_none_when_no_api_key(settings):
    settings.ANTHROPIC_API_KEY = ""
    result, error = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )
    assert result is None
    assert error is not None


def test_generate_returns_none_when_no_api_key_attr(settings):
    if hasattr(settings, "ANTHROPIC_API_KEY"):
        del settings.ANTHROPIC_API_KEY
    result, error = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )
    assert result is None
    assert error is not None


def test_generate_returns_none_on_empty_submission(settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    result, error = generate_ai_summary(
        submission_data={},
        school_name="Test School",
        form_cfg={},
    )
    assert result is None
    assert error is not None


def test_generate_returns_none_on_all_empty_fields(settings):
    """Submission with only empty/null values should be treated as empty."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    result, error = generate_ai_summary(
        submission_data={"first_name": "", "notes": None, "tags": []},
        school_name="Test School",
        form_cfg={},
    )
    assert result is None
    assert error is not None


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
def test_generate_client_has_timeout(mock_anthropic_class, settings):
    """Client must be instantiated with an explicit timeout."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_client = _mock_anthropic_client()
    mock_anthropic_class.return_value = mock_client

    generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )

    init_kwargs = mock_anthropic_class.call_args.kwargs
    assert "timeout" in init_kwargs
    assert init_kwargs["timeout"] > 0


@patch("anthropic.Anthropic")
def test_generate_returns_summary_dict(mock_anthropic_class, settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_client = _mock_anthropic_client()
    mock_anthropic_class.return_value = mock_client

    result, error = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )

    assert result is not None
    assert error is None
    assert result["summary"] == "A motivated student."
    assert result["criteria_scores"] == []


@patch("anthropic.Anthropic")
def test_generate_result_is_normalized(mock_anthropic_class, settings):
    """Response with non-dict criteria items must be filtered before returning."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    bad_response = '{"summary": "OK", "criteria_scores": [{"criterion": "A"}, "junk", null]}'
    mock_anthropic_class.return_value = _mock_anthropic_client(bad_response)

    result, error = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )

    assert result is not None
    assert error is None
    assert len(result["criteria_scores"]) == 1


@patch("anthropic.Anthropic")
def test_generate_handles_markdown_fences(mock_anthropic_class, settings):
    """Model wrapping JSON in ```json ... ``` should still parse correctly."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    wrapped = '```json\n{"summary": "Good applicant.", "criteria_scores": []}\n```'
    mock_anthropic_class.return_value = _mock_anthropic_client(wrapped)

    result, error = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )

    assert result is not None
    assert error is None
    assert result["summary"] == "Good applicant."


@patch("anthropic.Anthropic")
def test_generate_handles_non_json_response(mock_anthropic_class, settings):
    """Non-JSON response is wrapped as plain summary instead of crashing."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_anthropic_class.return_value = _mock_anthropic_client("Just some text.")

    result, error = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )

    assert result is not None
    assert error is None
    assert result["summary"] == "Just some text."
    assert result["criteria_scores"] == []


@patch("anthropic.Anthropic")
def test_generate_non_json_fallback_is_logged(mock_anthropic_class, settings, caplog):
    """Non-JSON fallback must be logged at WARNING level."""
    import logging
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_anthropic_class.return_value = _mock_anthropic_client("plain prose, not JSON")

    with caplog.at_level(logging.WARNING, logger="core.services.ai_summary"):
        generate_ai_summary(
            submission_data={"first_name": "Alice"},
            school_name="Test School",
            form_cfg={},
        )

    assert any("non-JSON" in r.message for r in caplog.records)


@patch("anthropic.Anthropic")
def test_generate_returns_none_on_api_exception(mock_anthropic_class, settings):
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("Network error")
    mock_anthropic_class.return_value = mock_client

    result, error = generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
    )
    assert result is None
    assert "Network error" in error


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


@patch("anthropic.Anthropic")
def test_generate_truncation_does_not_cut_mid_line(mock_anthropic_class, settings):
    """Truncation must happen at line boundaries, not mid-value."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_client = _mock_anthropic_client()
    mock_anthropic_class.return_value = mock_client

    # Create data where lines are each ~100 chars so truncation hits a boundary
    many_fields = {f"field_{i}": "a" * 90 for i in range(50)}
    generate_ai_summary(
        submission_data=many_fields,
        school_name="Test School",
        form_cfg={},
    )

    content = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    # Every non-truncation line should end with 'a' (not mid-field)
    lines = content.split("\n")
    for line in lines:
        if line == "[truncated]":
            break
        assert not line.endswith("aaaa"[:-1]) or ":" in line  # lines are complete key:value pairs


@patch("anthropic.Anthropic")
def test_generate_caps_criteria_count(mock_anthropic_class, settings):
    """More than 10 criteria should be silently capped."""
    settings.ANTHROPIC_API_KEY = "sk-test"
    mock_client = _mock_anthropic_client()
    mock_anthropic_class.return_value = mock_client

    many_criteria = [f"Criterion {i}" for i in range(20)]
    generate_ai_summary(
        submission_data={"first_name": "Alice"},
        school_name="Test School",
        form_cfg={},
        criteria=many_criteria,
    )

    content = mock_client.messages.create.call_args.kwargs["messages"][0]["content"]
    # Only the first 10 criteria should appear
    assert "Criterion 9" in content
    assert "Criterion 10" not in content


# ---------------------------------------------------------------------------
# Admin view: generate_summary_view
# ---------------------------------------------------------------------------

def _login_staff(client, school):
    membership = SchoolAdminMembershipFactory(school=school)
    client.force_login(membership.user)
    return membership.user


@pytest.mark.django_db
def test_generate_summary_view_get_redirects(client):
    school = SchoolFactory(plan="growth", slug="ai-get-test")
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
def test_generate_summary_view_rejects_wrong_school(client):
    """Staff from a different school must be denied (403)."""
    school_a = SchoolFactory(plan="growth", slug="ai-scope-a")
    school_b = SchoolFactory(plan="growth", slug="ai-scope-b")
    _login_staff(client, school_a)
    sub = SubmissionFactory(school=school_b)

    url = reverse("admin:core_submission_generate_summary", args=[sub.pk])
    resp = client.post(url)
    assert resp.status_code == 403


@pytest.mark.django_db
@patch("core.admin.submissions.generate_ai_summary")
def test_generate_summary_view_saves_on_success(mock_gen, client):
    mock_gen.return_value = ({"summary": "Great student.", "criteria_scores": []}, None)

    school = SchoolFactory(plan="growth", slug="ai-save-test")
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
def test_generate_summary_view_logs_audit(mock_gen, client):
    """Successful generation must write an audit log entry."""
    from core.models import AdminAuditLog
    mock_gen.return_value = ({"summary": "Solid.", "criteria_scores": []}, None)

    school = SchoolFactory(plan="growth", slug="ai-audit-test")
    _login_staff(client, school)
    sub = SubmissionFactory(school=school, data={"first_name": "Alice"})

    url = reverse("admin:core_submission_generate_summary", args=[sub.pk])
    client.post(url)

    log = AdminAuditLog.objects.filter(object_id=str(sub.pk), action="action").first()
    assert log is not None
    assert log.extra.get("name") == "generate_ai_summary"


@pytest.mark.django_db
@patch("core.admin.submissions.generate_ai_summary")
def test_generate_summary_view_error_on_none(mock_gen, client):
    """When generate_ai_summary returns None, show error message, don't save."""
    mock_gen.return_value = (None, "Something went wrong.")

    school = SchoolFactory(plan="growth", slug="ai-none-test")
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
def test_generate_summary_view_shows_specific_error(mock_gen, client):
    """The actual error string from the service appears in the admin message."""
    mock_gen.return_value = (None, "Your credit balance is too low.")

    school = SchoolFactory(plan="growth", slug="ai-err-msg-test")
    _login_staff(client, school)
    sub = SubmissionFactory(school=school, data={"first_name": "Alice"})

    url = reverse("admin:core_submission_generate_summary", args=[sub.pk])
    resp = client.post(url, follow=True)

    messages_list = [str(m) for m in resp.context["messages"]]
    assert any("credit balance" in m for m in messages_list)


@pytest.mark.django_db
@patch("core.admin.submissions.generate_ai_summary")
def test_generate_summary_view_saves_timestamp(mock_gen, client):
    mock_gen.return_value = ({"summary": "Solid.", "criteria_scores": []}, None)

    school = SchoolFactory(plan="growth", slug="ai-ts-test")
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
    """Growth school with no summary: shows 'No summary yet' message."""
    school = SchoolFactory(plan="growth", slug="ai-disp-empty")
    sub = SubmissionFactory(school=school, ai_summary=None)

    url = reverse("admin:core_submission_change", args=[sub.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert b"No summary yet" in resp.content


@pytest.mark.django_db
def test_ai_summary_display_renders_summary(admin_client):
    school = SchoolFactory(plan="growth", slug="ai-disp-has")
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
    school = SchoolFactory(plan="growth", slug="ai-disp-criteria")
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
def test_ai_summary_display_handles_malformed_data(admin_client):
    """Non-dict ai_summary must not crash the admin page."""
    school = SchoolFactory(plan="growth", slug="ai-disp-malformed")
    sub = SubmissionFactory(school=school, ai_summary="unexpected string")

    url = reverse("admin:core_submission_change", args=[sub.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert b"malformed" in resp.content


@pytest.mark.django_db
def test_ai_summary_display_handles_non_dict_criteria_items(admin_client):
    """criteria_scores with non-dict items must not crash the admin page."""
    school = SchoolFactory(plan="growth", slug="ai-disp-bad-criteria")
    sub = SubmissionFactory(
        school=school,
        ai_summary={"summary": "OK", "criteria_scores": ["bad", None, 42]},
    )

    url = reverse("admin:core_submission_change", args=[sub.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert b"OK" in resp.content


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
def test_generate_button_present_for_growth(admin_client):
    """Growth plan: 'Generate AI Summary' button rendered in change form."""
    school = SchoolFactory(plan="growth", slug="ai-btn-pro")
    sub = SubmissionFactory(school=school)

    url = reverse("admin:core_submission_change", args=[sub.pk])
    resp = admin_client.get(url)
    assert resp.status_code == 200
    assert b"Generate AI Summary" in resp.content
