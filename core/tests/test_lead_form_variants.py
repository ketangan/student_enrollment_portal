"""
Tests for multi-variant lead forms (/lead/<form_key>/).

Covers the 8 cases specified in the design review:
1. Legacy /lead/ route remains unchanged
2. Valid named variant renders correctly
3. Unknown variant returns 404
4. Records retain form_key on creation
5. Notifications use variant configuration (form title, category)
6. Confirmation email subject uses per-variant confirmation_subject
7. Scheduling submissions excluded from default pipeline view
8. iframe embedding preserves the named route
"""
import json
from unittest.mock import patch, MagicMock

import pytest
from django.test import Client
from django.urls import reverse

from core.models import Lead, School
from core.services.config_loader import get_lead_form_config


# ── Fixtures ──────────────────────────────────────────────────────────────────

SCHOOL_SLUG = "variant-test-school"

MINIMAL_YAML_RAW = {
    "school": {"slug": SCHOOL_SLUG, "display_name": "Variant Test School"},
    "leads": {
        "form_title": "Request Info",
        "notify_to": "admin@test.com",
        "confirmation_enabled": True,
        "fields": [],
    },
    "lead_forms": {
        "scheduling": {
            "form_title": "2026–27 Scheduling",
            "notify_to": "admin@test.com",
            "confirmation_enabled": True,
            "confirmation_subject": "We got your scheduling preferences",
            "pipeline_visible": False,
            "category": "scheduling",
            "fields": [
                {"key": "student_name", "label": "Student Name", "type": "text", "required": True},
                {"key": "day_preference", "label": "Weekday or Weekend?", "type": "select",
                 "required": True, "options": [
                     {"label": "Weekday", "value": "weekday"},
                     {"label": "Weekend", "value": "weekend"},
                 ]},
            ],
        }
    },
}


@pytest.fixture
def school(db):
    return School.objects.create(
        slug=SCHOOL_SLUG,
        display_name="Variant Test School",
        plan="trial",
        is_active=True,
    )


@pytest.fixture
def client():
    return Client()


def _lead_url(slug, form_key=None):
    if form_key:
        return reverse("school_lead_form_variant", kwargs={"school_slug": slug, "form_key": form_key})
    return reverse("school_lead_form", kwargs={"school_slug": slug})


# ── 1. Legacy /lead/ route unchanged ──────────────────────────────────────────

def test_legacy_lead_route_unchanged(client, school):
    with patch("core.views_public.load_school_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            raw=MINIMAL_YAML_RAW,
            branding=MagicMock(
                custom_css=None, custom_js=None, logo_url=None,
                theme={"primary_color": "#000", "accent_color": "#000",
                       "background": "#fff", "card": "#fff", "text": "#000",
                       "muted": "#999", "border": "#ccc", "radius": "6px",
                       "font_family": "sans-serif", "heading_font": "serif"},
            ),
            display_name="Variant Test School",
        )
        resp = client.get(_lead_url(SCHOOL_SLUG))
    assert resp.status_code == 200
    assert b"Request Info" in resp.content


# ── 2. Valid named variant renders ────────────────────────────────────────────

def test_valid_named_variant_renders(client, school):
    with patch("core.views_public.load_school_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            raw=MINIMAL_YAML_RAW,
            branding=MagicMock(
                custom_css=None, custom_js=None, logo_url=None,
                theme={"primary_color": "#000", "accent_color": "#000",
                       "background": "#fff", "card": "#fff", "text": "#000",
                       "muted": "#999", "border": "#ccc", "radius": "6px",
                       "font_family": "sans-serif", "heading_font": "serif"},
            ),
            display_name="Variant Test School",
        )
        resp = client.get(_lead_url(SCHOOL_SLUG, "scheduling"))
    assert resp.status_code == 200
    assert b"2026" in resp.content  # form_title "2026\xe2\x80\x9327 Scheduling"


# ── 3. Unknown variant returns 404 ────────────────────────────────────────────

def test_unknown_variant_returns_404(client, school):
    with patch("core.views_public.load_school_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            raw=MINIMAL_YAML_RAW,
            branding=MagicMock(
                custom_css=None, custom_js=None, logo_url=None,
                theme={"primary_color": "#000", "accent_color": "#000",
                       "background": "#fff", "card": "#fff", "text": "#000",
                       "muted": "#999", "border": "#ccc", "radius": "6px",
                       "font_family": "sans-serif", "heading_font": "serif"},
            ),
            display_name="Variant Test School",
        )
        resp = client.get(_lead_url(SCHOOL_SLUG, "nonexistent"))
    assert resp.status_code == 404


# ── 4. Records retain form_key on creation ────────────────────────────────────

def test_lead_record_retains_form_key(client, school):
    with patch("core.views_public.load_school_config") as mock_cfg, \
         patch("core.views_public.send_lead_admin_notification"), \
         patch("core.views_public.send_lead_confirmation"):
        mock_cfg.return_value = MagicMock(
            raw=MINIMAL_YAML_RAW,
            branding=MagicMock(
                custom_css=None, custom_js=None, logo_url=None,
                theme={"primary_color": "#000", "accent_color": "#000",
                       "background": "#fff", "card": "#fff", "text": "#000",
                       "muted": "#999", "border": "#ccc", "radius": "6px",
                       "font_family": "sans-serif", "heading_font": "serif"},
            ),
            display_name="Variant Test School",
        )
        resp = client.post(_lead_url(SCHOOL_SLUG, "scheduling"), {
            "name": "Jane Parent",
            "email": "jane@test.com",
            "phone": "5551234567",
            "student_name": "Jane Jr",
            "day_preference": "weekday",
        })

    lead = Lead.objects.get(school=school, email="jane@test.com")
    assert lead.form_key == "scheduling"
    assert lead.data.get("form_key") == "scheduling"
    assert lead.data.get("category") == "scheduling"
    assert lead.data.get("pipeline_visible") is False


# ── 5. Notifications include variant form title and category ──────────────────

def test_admin_notification_uses_variant_config(school):
    from core.services.notifications import send_lead_admin_notification

    lead = Lead.objects.create(
        school=school,
        name="Test Parent",
        email="p@test.com",
        normalized_email="p@test.com",
        form_key="scheduling",
        data={"form_key": "scheduling", "category": "scheduling"},
    )
    cfg = get_lead_form_config(MINIMAL_YAML_RAW, "scheduling")

    with patch("core.services.notifications.get_school_email_connection") as mock_conn:
        mock_msg = MagicMock()
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        with patch("core.services.notifications.EmailMultiAlternatives") as mock_email:
            mock_email.return_value = mock_msg
            send_lead_admin_notification(
                school=school, lead=lead,
                config_raw=MINIMAL_YAML_RAW, lead_cfg=cfg,
            )
            subject = mock_email.call_args[0][0]

    assert "2026" in subject or "Scheduling" in subject  # form_title used in subject


# ── 6. Confirmation uses per-variant subject ──────────────────────────────────

def test_confirmation_uses_variant_subject(school):
    from core.services.notifications import send_lead_confirmation

    lead = Lead.objects.create(
        school=school,
        name="Test Parent",
        email="p2@test.com",
        normalized_email="p2@test.com",
        form_key="scheduling",
    )
    cfg = get_lead_form_config(MINIMAL_YAML_RAW, "scheduling")

    with patch("core.services.notifications.EmailMultiAlternatives") as mock_email:
        mock_msg = MagicMock()
        mock_email.return_value = mock_msg
        with patch("core.services.notifications.get_school_email_connection"):
            send_lead_confirmation(
                lead=lead, school_name="Variant Test School",
                config_raw=MINIMAL_YAML_RAW, lead_cfg=cfg,
            )
        subject = mock_email.call_args[0][0]

    assert subject == "We got your scheduling preferences"


# ── 7. Scheduling submissions excluded from default pipeline ──────────────────

def test_scheduling_excluded_from_default_pipeline(client, school, django_user_model):
    # Create one pipeline lead and one scheduling lead
    Lead.objects.create(
        school=school, name="Prospect", email="prospect@test.com",
        normalized_email="prospect@test.com",
        form_key="", data={},
    )
    Lead.objects.create(
        school=school, name="Scheduler", email="scheduler@test.com",
        normalized_email="scheduler@test.com",
        form_key="scheduling", data={"pipeline_visible": False},
    )

    admin = django_user_model.objects.create_superuser("admin_v", "a@test.com", "pass")
    client.force_login(admin)

    resp = client.get(reverse("school_leads", kwargs={"school_slug": SCHOOL_SLUG}))
    assert resp.status_code == 200
    # Pipeline view should include the prospect, not the scheduler
    lead_names = [l["name"] for l in resp.context["leads"]]
    assert "Prospect" in lead_names
    assert "Scheduler" not in lead_names

    # ?category=scheduling shows only scheduling submissions
    resp2 = client.get(
        reverse("school_leads", kwargs={"school_slug": SCHOOL_SLUG}) + "?category=scheduling"
    )
    lead_names2 = [l["name"] for l in resp2.context["leads"]]
    assert "Scheduler" in lead_names2
    assert "Prospect" not in lead_names2


# ── 8. Embed mode preserves named route ──────────────────────────────────────

def test_embed_mode_preserves_named_route(client, school):
    with patch("core.views_public.load_school_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(
            raw=MINIMAL_YAML_RAW,
            branding=MagicMock(
                custom_css=None, custom_js=None, logo_url=None,
                theme={"primary_color": "#000", "accent_color": "#000",
                       "background": "#fff", "card": "#fff", "text": "#000",
                       "muted": "#999", "border": "#ccc", "radius": "6px",
                       "font_family": "sans-serif", "heading_font": "serif"},
            ),
            display_name="Variant Test School",
        )
        resp = client.get(_lead_url(SCHOOL_SLUG, "scheduling") + "?embed=1")
    assert resp.status_code == 200
    # embed=1 strips the header; form content still from the scheduling variant
    assert b"2026" in resp.content


# ── config_loader: None returned for missing variant ─────────────────────────

def test_config_loader_returns_none_for_missing_variant():
    result = get_lead_form_config(MINIMAL_YAML_RAW, "nonexistent")
    assert result is None


def test_config_loader_pipeline_visible_parsed():
    cfg = get_lead_form_config(MINIMAL_YAML_RAW, "scheduling")
    assert cfg["pipeline_visible"] is False
    assert cfg["category"] == "scheduling"
    assert cfg["confirmation_subject"] == "We got your scheduling preferences"


def test_config_loader_legacy_defaults():
    cfg = get_lead_form_config(MINIMAL_YAML_RAW)
    assert cfg["pipeline_visible"] is True
    assert cfg["category"] == "lead"
