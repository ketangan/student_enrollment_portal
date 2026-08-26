from datetime import datetime

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.services import outreach_clicks
from core import views_ops


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        username="ops_super", email="super@test.com", password="testpass123"
    )


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        username="regular", email="regular@test.com", password="testpass123"
    )


def test_format_pacific_timestamp_converts_utc_iso(settings):
    settings.TIME_ZONE = "America/Los_Angeles"

    assert outreach_clicks._format_pacific_timestamp("2026-01-15T18:30:00Z") == (
        "2026-01-15 10:30 AM PST"
    )


def test_format_pacific_timestamp_handles_naive_sheet_time_as_local(settings):
    settings.TIME_ZONE = "America/Los_Angeles"

    assert outreach_clicks._format_pacific_timestamp("8/26/2026 14:05:00") == (
        "2026-08-26 02:05 PM PDT"
    )


def test_build_click_log_context_filters_and_sorts(monkeypatch, settings):
    settings.TIME_ZONE = "America/Los_Angeles"
    records = [
        {
            "timestamp": "2026-08-25T18:00:00Z",
            "school_name": "Older School",
            "website": "https://older.example",
            "path": "/demo",
            "gesture_type": "scroll",
        },
        {
            "timestamp": "2026-08-26T18:00:00Z",
            "school_name": "Teddi Bear Swim School",
            "website": "https://teddi.example",
            "path": "/mocks/teddi/sports-action/",
            "gesture_type": "click",
            "utm_campaign": "website_mock",
        },
    ]
    monkeypatch.setattr(outreach_clicks, "_read_click_log_records", lambda tab_name: records)

    context = outreach_clicks.build_click_log_context(q="teddi", gesture_type="click", limit=100)

    assert context["total_count"] == 1
    assert context["sheet_total_count"] == 2
    assert context["rows"][0].school_name == "Teddi Bear Swim School"
    assert context["rows"][0].timestamp_pacific == "2026-08-26 11:00 AM PDT"
    assert context["gesture_choices"] == ["click", "scroll"]


@pytest.mark.django_db
def test_ops_audit_clicks_requires_superuser_anonymous(client):
    resp = client.get(reverse("ops_audit_clicks"))

    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_ops_audit_clicks_blocks_regular_user(client, regular_user):
    client.force_login(regular_user)

    resp = client.get(reverse("ops_audit_clicks"))

    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_ops_audit_clicks_renders_sheet_rows(client, superuser, monkeypatch):
    client.force_login(superuser)
    row = outreach_clicks.OutreachClickRow(
        timestamp_raw="2026-08-26T18:00:00Z",
        timestamp_pacific="2026-08-26 11:00 AM PDT",
        school_name="Teddi Bear Swim School",
        website="https://teddi.example",
        path="/mocks/teddi/sports-action/",
        gesture_type="click",
        lead_id="90277-abc123",
        utm_source="mock_followup",
        utm_medium="email",
        utm_campaign="website_mock",
        tracking_kind="lead",
        referer="",
        sort_timestamp=datetime(2026, 8, 26, 18, 0),
    )
    monkeypatch.setattr(
        views_ops.outreach_clicks,
        "build_click_log_context",
        lambda **kwargs: {
            "rows": [row],
            "total_count": 1,
            "sheet_total_count": 1,
            "q": kwargs.get("q", ""),
            "gesture_filter": kwargs.get("gesture_type", ""),
            "gesture_choices": ["click"],
            "limit": 200,
            "limit_options": (100, 200),
            "source_tab": "Click_Log_View",
        },
    )

    resp = client.get(reverse("ops_audit_clicks"))

    assert resp.status_code == 200
    assert resp.context["active_nav"] == "audit"
    assert b"Teddi Bear Swim School" in resp.content
    assert b"/mocks/teddi/sports-action/" in resp.content
    assert b"2026-08-26 11:00 AM PDT" in resp.content


@pytest.mark.django_db
def test_ops_audit_clicks_shows_config_errors(client, superuser, monkeypatch):
    client.force_login(superuser)

    def raise_error(**kwargs):
        raise outreach_clicks.OutreachClickLogConfigError("missing sheet config")

    monkeypatch.setattr(views_ops.outreach_clicks, "build_click_log_context", raise_error)

    resp = client.get(reverse("ops_audit_clicks"))

    assert resp.status_code == 200
    assert b"missing sheet config" in resp.content
