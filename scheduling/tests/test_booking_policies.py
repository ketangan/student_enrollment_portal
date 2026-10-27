from types import SimpleNamespace

import pytest
from django.template import Context, Template

from scheduling import booking_policies


def test_school_scoped_published_excerpts():
    school = SimpleNamespace(slug="plushumi-demo")
    workshop = booking_policies.notice(school, "workshop")
    party = booking_policies.notice(school, "party")
    assert workshop["revision"] == "website-2026-10-26"
    assert "reserved seats" in workshop["text"]
    assert "deposits" in party["text"]
    assert "reserved seats" not in party["text"]
    assert booking_policies.notice(SimpleNamespace(slug="south-bay-music"), "workshop") == {}
    assert booking_policies.notice(school, "camp") == {}


@pytest.mark.parametrize("url", [None, "javascript:alert(1)", "http://example.com", "/policy"])
def test_unsafe_source_is_not_rendered(monkeypatch, url):
    monkeypatch.setattr(booking_policies, "load_school_config", lambda slug:
        SimpleNamespace(raw={"scheduling": {"booking_policies": {"source_url": url}}}))
    assert booking_policies.notice(SimpleNamespace(slug="other"), "party") == {}


def test_shared_notice_escapes_content_and_does_not_invent_consent(monkeypatch):
    monkeypatch.setattr(booking_policies, "load_school_config", lambda slug:
        SimpleNamespace(raw={"scheduling": {"booking_policies": {
            "source_url": "https://example.com/policy", "party_text": "<script>unsafe</script>"}}}))
    html = Template("{% load booking_policies %}{% booking_policy school 'party' %}").render(
        Context({"school": SimpleNamespace(slug="other")}))
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert 'rel="noopener noreferrer"' in html
    assert "checkbox" not in html and "waiver" not in html
