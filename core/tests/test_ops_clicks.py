from datetime import datetime

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import AdminAuditLog
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
    raw_records = [
        (2, records[0]),
        (3, records[1]),
    ]
    monkeypatch.setattr(outreach_clicks, "_read_click_log_records", lambda tab_name: records)
    monkeypatch.setattr(outreach_clicks, "_read_raw_click_log_records", lambda: raw_records)

    context = outreach_clicks.build_click_log_context(q="teddi", gesture_type="click", limit=100)

    assert context["total_count"] == 1
    assert context["sheet_total_count"] == 2
    assert context["rows"][0].school_name == "Teddi Bear Swim School"
    assert context["rows"][0].timestamp_pacific == "2026-08-26 11:00 AM PDT"
    assert context["rows"][0].raw_sheet_row == 3
    assert context["rows"][0].delete_token.startswith("3:")
    assert context["gesture_choices"] == ["click", "scroll"]


def test_build_click_log_context_tolerates_unmatched_view_rows(monkeypatch, settings):
    settings.TIME_ZONE = "America/Los_Angeles"
    records = [
        {
            "timestamp": "2026-08-26T18:00:00Z",
            "school_name": "No Raw Match",
            "path": "/demo",
        }
    ]
    monkeypatch.setattr(outreach_clicks, "_read_click_log_records", lambda tab_name: records)
    monkeypatch.setattr(outreach_clicks, "_read_raw_click_log_records", lambda: [])

    context = outreach_clicks.build_click_log_context(limit=100)

    assert context["rows"][0].raw_sheet_row is None
    assert context["rows"][0].delete_token == ""


def test_delete_click_log_rows_validates_token_and_deletes_descending(monkeypatch):
    headers = ["timestamp", "lead_id", "path", "gesture_type"]
    rows = [
        headers,
        ["2026-08-25T18:00:00Z", "lead-1", "/demo", "scroll"],
        ["2026-08-26T18:00:00Z", "lead-2", "/mock", "click"],
    ]
    deleted_rows = []

    class FakeWorksheet:
        def get_all_values(self):
            return rows

        def delete_rows(self, row_number):
            deleted_rows.append(row_number)

    monkeypatch.setattr(outreach_clicks, "_worksheet", lambda tab_name: FakeWorksheet())
    first_token = outreach_clicks._delete_value(2, outreach_clicks._row_dict(headers, rows[1]))
    second_token = outreach_clicks._delete_value(3, outreach_clicks._row_dict(headers, rows[2]))

    result = outreach_clicks.delete_click_log_rows([first_token, second_token])

    assert result.deleted == 2
    assert result.skipped == 0
    assert deleted_rows == [3, 2]


def test_delete_click_log_rows_skips_changed_rows(monkeypatch):
    headers = ["timestamp", "lead_id", "path", "gesture_type"]
    rows = [
        headers,
        ["2026-08-25T18:00:00Z", "changed-lead", "/demo", "scroll"],
    ]
    deleted_rows = []

    class FakeWorksheet:
        def get_all_values(self):
            return rows

        def delete_rows(self, row_number):
            deleted_rows.append(row_number)

    monkeypatch.setattr(outreach_clicks, "_worksheet", lambda tab_name: FakeWorksheet())
    stale_record = {"timestamp": "2026-08-25T18:00:00Z", "lead_id": "lead-1", "path": "/demo", "gesture_type": "scroll"}
    stale_token = outreach_clicks._delete_value(2, stale_record)

    result = outreach_clicks.delete_click_log_rows([stale_token])

    assert result.deleted == 0
    assert result.skipped == 1
    assert "changed before deletion" in result.errors[0]
    assert deleted_rows == []


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
def test_ops_audit_clicks_delete_calls_service_and_logs(client, superuser, monkeypatch):
    client.force_login(superuser)
    captured = {}

    def fake_delete(selected_rows):
        captured["selected_rows"] = selected_rows
        return outreach_clicks.OutreachClickDeleteResult(deleted=2, skipped=0, requested=2)

    monkeypatch.setattr(views_ops.outreach_clicks, "delete_click_log_rows", fake_delete)

    resp = client.post(
        reverse("ops_audit_clicks_delete"),
        {
            "delete_rows": ["2:abc", "3:def"],
            "next": reverse("ops_audit_clicks") + "?q=test",
        },
        follow=True,
    )

    assert resp.status_code == 200
    assert captured["selected_rows"] == ["2:abc", "3:def"]
    assert AdminAuditLog.objects.filter(
        model_label="google_sheets.click_log",
        action="delete",
        extra__name="outreach_click_rows_deleted",
    ).exists()


@pytest.mark.django_db
def test_ops_audit_clicks_shows_config_errors(client, superuser, monkeypatch):
    client.force_login(superuser)

    def raise_error(**kwargs):
        raise outreach_clicks.OutreachClickLogConfigError("missing sheet config")

    monkeypatch.setattr(views_ops.outreach_clicks, "build_click_log_context", raise_error)

    resp = client.get(reverse("ops_audit_clicks"))

    assert resp.status_code == 200
    assert b"missing sheet config" in resp.content
