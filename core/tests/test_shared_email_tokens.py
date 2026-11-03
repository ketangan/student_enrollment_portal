import pytest

from core.services.email_content import BOOKING_TOKENS, ENROLLMENT_TOKENS, render_tokens
from core.services.notifications import _render_template
from core.views_school_email_templates import _TOKENS


def test_shared_registry_preserves_enrollment_tokens_and_booking_extension():
    assert _TOKENS == list(ENROLLMENT_TOKENS)
    assert [key for key, _ in ENROLLMENT_TOKENS] == [
        "full_name", "first_name", "email", "program", "status", "school_name"]
    assert [key for key, _ in BOOKING_TOKENS][:6] == [key for key, _ in ENROLLMENT_TOKENS]


@pytest.mark.parametrize("template,values,expected", [
    (None, None, ""),
    ("Hi {{first_name}} / {{unknown}}", {"first_name": "Jo"}, "Hi Jo / {{unknown}}"),
    ("{{ full_name }}", {"full_name": "Jo"}, "{{ full_name }}"),
    ("{{a}}", {"a": "{{b}}", "b": "Existing behavior"}, "Existing behavior"),
    ("{{n}} / {{html}}", {"n": 3, "html": "<b>Jo</b>"}, "3 / <b>Jo</b>"),
])
def test_enrollment_renderer_keeps_existing_semantics(template, values, expected):
    assert _render_template(template, values) == expected


def test_booking_renderer_keeps_html_escaping_and_no_recursive_substitution():
    assert render_tokens("{{ full_name }} {{booking_time}} {{unknown}}",
        {"full_name": "<b>{{booking_time}}</b>", "booking_time": "10 AM"}, html=True
    ) == "&lt;b&gt;{{booking_time}}&lt;/b&gt; 10 AM {{unknown}}"
