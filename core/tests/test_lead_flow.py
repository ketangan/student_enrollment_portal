"""
Tests for Feature 3: Lead Capture Form + Lead Model.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Lead, LEAD_STATUS_CONTACTED, LEAD_STATUS_LOST, LEAD_STATUS_NEW
from core.services.config_loader import SchoolConfig, get_program_options
from core.tests.factories import LeadFactory, SchoolFactory


SLUG = "enrollment-request-demo"


def _lead_school(slug=SLUG, plan="starter"):
    """Return a School with leads enabled."""
    from core.models import School
    from core.services.config_loader import load_school_config
    cfg = load_school_config(slug)
    school, _ = School.objects.get_or_create(
        slug=slug,
        defaults={"display_name": cfg.display_name, "plan": plan},
    )
    if school.plan != plan:
        school.plan = plan
        school.save(update_fields=["plan"])
    return school


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_creates_public_id_on_save():
    lead = LeadFactory()
    assert lead.public_id
    assert len(lead.public_id) <= 16


@pytest.mark.django_db
def test_lead_normalizes_email_on_save():
    lead = LeadFactory(email="  Alice@Example.COM  ")
    assert lead.normalized_email == "alice@example.com"


@pytest.mark.django_db
def test_lead_normalizes_phone_on_save():
    lead = LeadFactory(phone="(555) 123-4567")
    assert lead.normalized_phone == "5551234567"


@pytest.mark.django_db
def test_lead_status_defaults_to_new():
    lead = LeadFactory()
    assert lead.status == LEAD_STATUS_NEW


# ---------------------------------------------------------------------------
# config_loader — get_program_options
# ---------------------------------------------------------------------------

def test_get_program_options_returns_options_from_interested_in():
    config = SchoolConfig(raw={
        "school": {"slug": "test"},
        "form": {
            "sections": [{
                "fields": [{
                    "key": "interested_in",
                    "type": "select",
                    "options": [
                        {"label": "Ballet", "value": "ballet"},
                        {"label": "Jazz", "value": "jazz"},
                    ],
                }],
            }],
        },
    })
    opts = get_program_options(config)
    assert opts == [{"label": "Ballet", "value": "ballet"}, {"label": "Jazz", "value": "jazz"}]


def test_get_program_options_returns_empty_when_no_program_field():
    config = SchoolConfig(raw={
        "school": {"slug": "test"},
        "form": {
            "sections": [{
                "fields": [{"key": "first_name", "type": "text"}],
            }],
        },
    })
    assert get_program_options(config) == []


def test_get_program_options_respects_explicit_yaml_override():
    config = SchoolConfig(raw={
        "school": {"slug": "test"},
        "leads": {"program_field_key": "class_type"},
        "form": {
            "sections": [{
                "fields": [
                    # This key is in the heuristic set — should be skipped
                    {
                        "key": "interested_in",
                        "type": "select",
                        "options": [{"label": "Ballet", "value": "ballet"}],
                    },
                    # This is the explicit override key
                    {
                        "key": "class_type",
                        "type": "select",
                        "options": [{"label": "Morning", "value": "morning"}],
                    },
                ],
            }],
        },
    })
    opts = get_program_options(config)
    assert opts == [{"label": "Morning", "value": "morning"}]


# ---------------------------------------------------------------------------
# Views — GET
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_capture_view_get_renders_form(client):
    _lead_school()
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"trap_field" in resp.content  # honeypot present


@pytest.mark.django_db
def test_lead_capture_view_get_passes_program_options_to_context(client):
    _lead_school()
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    resp = client.get(url)
    assert resp.status_code == 200
    # The demo YAML has an "interested_in" select field
    assert "program_options" in resp.context
    assert isinstance(resp.context["program_options"], list)


@pytest.mark.django_db
def test_lead_capture_view_get_passes_utm_params_to_context(client):
    _lead_school()
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    resp = client.get(url, {"utm_source": "instagram", "utm_campaign": "spring"})
    assert resp.status_code == 200
    assert resp.context["utm_source"] == "instagram"
    assert resp.context["utm_campaign"] == "spring"


@pytest.mark.django_db
def test_lead_capture_view_404_inactive_school(client):
    from core.models import School
    from core.services.config_loader import load_school_config
    cfg = load_school_config(SLUG)
    School.objects.filter(slug=SLUG).delete()
    School.objects.create(slug=SLUG, display_name=cfg.display_name, plan="starter", is_active=False)
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_lead_capture_view_404_feature_disabled(client):
    _lead_school(plan="trial")
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    assert client.get(url).status_code == 404


@pytest.mark.django_db
def test_lead_capture_view_404_no_yaml_config(client):
    url = reverse("lead_capture", kwargs={"school_slug": "nonexistent-school-xyz"})
    assert client.get(url).status_code == 404


# ---------------------------------------------------------------------------
# Views — POST
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_capture_view_post_creates_lead_and_redirects(client):
    _lead_school()
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    resp = client.post(url, {"name": "Alice", "email": "alice@example.com"})
    assert resp.status_code in (302, 303)
    assert Lead.objects.filter(normalized_email="alice@example.com").exists()


@pytest.mark.django_db
def test_lead_capture_view_post_deduplicates_by_email(client):
    school = _lead_school()
    LeadFactory(school=school, email="alice@example.com", name="Alice Old")
    assert Lead.objects.filter(school=school).count() == 1

    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    client.post(url, {"name": "Alice New", "email": "ALICE@example.com"})

    assert Lead.objects.filter(school=school).count() == 1
    lead = Lead.objects.get(school=school, normalized_email="alice@example.com")
    assert lead.name == "Alice New"


@pytest.mark.django_db
def test_lead_capture_view_post_resets_lost_lead_to_new(client):
    school = _lead_school()
    LeadFactory(school=school, email="alice@example.com", status=LEAD_STATUS_LOST)

    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    client.post(url, {"name": "Alice", "email": "alice@example.com"})

    lead = Lead.objects.get(school=school, normalized_email="alice@example.com")
    assert lead.status == LEAD_STATUS_NEW


@pytest.mark.django_db
def test_lead_capture_view_post_keeps_contacted_status_on_resubmit(client):
    school = _lead_school()
    LeadFactory(school=school, email="alice@example.com", status=LEAD_STATUS_CONTACTED)

    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    client.post(url, {"name": "Alice", "email": "alice@example.com"})

    lead = Lead.objects.get(school=school, normalized_email="alice@example.com")
    assert lead.status == LEAD_STATUS_CONTACTED


@pytest.mark.django_db
def test_lead_capture_view_post_missing_name_shows_error(client):
    _lead_school()
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    resp = client.post(url, {"name": "", "email": "alice@example.com"})
    assert resp.status_code == 200
    assert b"required" in resp.content.lower()
    assert not Lead.objects.filter(normalized_email="alice@example.com").exists()


@pytest.mark.django_db
def test_lead_capture_view_post_missing_email_shows_error(client):
    _lead_school()
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    resp = client.post(url, {"name": "Alice", "email": ""})
    assert resp.status_code == 200
    assert b"required" in resp.content.lower()
    assert not Lead.objects.filter(name="Alice").exists()


@pytest.mark.django_db
def test_lead_capture_view_post_honeypot_filled_silently_ignored(client):
    _lead_school()
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    resp = client.post(url, {"name": "Bot", "email": "bot@spam.com", "trap_field": "I am a bot"})
    assert resp.status_code in (301, 302, 303)
    assert not Lead.objects.filter(normalized_email="bot@spam.com").exists()


@pytest.mark.django_db
def test_lead_capture_view_post_stores_utm_params(client):
    _lead_school()
    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    client.post(url, {
        "name": "Alice",
        "email": "alice@example.com",
        "utm_source": "instagram",
        "utm_medium": "story",
        "utm_campaign": "spring2026",
    })
    lead = Lead.objects.get(normalized_email="alice@example.com")
    assert lead.utm_source == "instagram"
    assert lead.utm_medium == "story"
    assert lead.utm_campaign == "spring2026"


@pytest.mark.django_db
def test_lead_capture_view_post_dedup_preserves_existing_notes(client):
    school = _lead_school()
    LeadFactory(school=school, email="alice@example.com", notes="Interested in ballet")

    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    client.post(url, {"name": "Alice", "email": "alice@example.com"})

    lead = Lead.objects.get(school=school, normalized_email="alice@example.com")
    assert lead.notes == "Interested in ballet"


@pytest.mark.django_db
def test_lead_capture_view_post_dedup_updates_phone_when_blank_to_present(client):
    school = _lead_school()
    LeadFactory(school=school, email="alice@example.com", phone="")

    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    client.post(url, {"name": "Alice", "email": "alice@example.com", "phone": "5551234567"})

    lead = Lead.objects.get(school=school, normalized_email="alice@example.com")
    assert lead.phone == "5551234567"


@pytest.mark.django_db
def test_lead_capture_view_post_dedup_does_not_overwrite_phone_with_blank(client):
    school = _lead_school()
    LeadFactory(school=school, email="alice@example.com", phone="5559999999")

    url = reverse("lead_capture", kwargs={"school_slug": SLUG})
    client.post(url, {"name": "Alice", "email": "alice@example.com", "phone": ""})

    lead = Lead.objects.get(school=school, normalized_email="alice@example.com")
    assert lead.phone == "5559999999"


# ---------------------------------------------------------------------------
# Success view
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_capture_success_view_renders(client):
    _lead_school()
    url = reverse("lead_capture_success", kwargs={"school_slug": SLUG})
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"Thank" in resp.content or b"interest" in resp.content.lower()
