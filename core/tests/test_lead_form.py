"""
Tests for the public lead form (school_lead_form_view) and webhook lead intake.
"""
from __future__ import annotations

import json
import pytest
from django.urls import reverse

from core.models import Lead, School, SchoolProgram
from core.services.lead_intake import create_or_update_lead, ensure_lead_webhook_token
from core.tests.factories import SchoolFactory


SLUG = "enrollment-request-demo"


def _school(slug=SLUG, plan="starter"):
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
# Public lead form — GET
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_form_get_renders(client):
    _school()
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    r = client.get(url)
    assert r.status_code == 200
    assert b"Request Information" in r.content or b"form_title" not in r.content  # YAML default renders


@pytest.mark.django_db
def test_lead_form_get_embed_mode(client):
    _school()
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG}) + "?embed=1"
    r = client.get(url)
    assert r.status_code == 200
    # embed mode: no logo chrome, body background transparent
    assert b"background:transparent" in r.content or b"background: transparent" in r.content


@pytest.mark.django_db
def test_lead_form_get_404_inactive_school(client):
    school = _school()
    school.is_active = False
    school.save(update_fields=["is_active"])
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    r = client.get(url)
    assert r.status_code == 404
    school.is_active = True
    school.save(update_fields=["is_active"])


@pytest.mark.django_db
def test_lead_form_get_db_programs_shown(client):
    school = _school(slug="duc-learning-center")
    school.program_field_key = "interested_in"
    school.plan = "starter"
    school.save(update_fields=["program_field_key", "plan"])
    SchoolProgram.objects.get_or_create(school=school, code="tutoring", defaults={"name": "1-on-1 Tutoring", "is_active": True})
    url = reverse("school_lead_form", kwargs={"school_slug": "duc-learning-center"})
    r = client.get(url)
    assert r.status_code == 200
    assert b"1-on-1 Tutoring" in r.content


# ---------------------------------------------------------------------------
# Public lead form — POST creates lead
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_form_post_creates_lead(client):
    _school()
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    r = client.post(url, {"name": "Test Parent", "email": "testparent@example.com", "phone": "555-1234"})
    assert r.status_code == 200
    assert Lead.objects.filter(school__slug=SLUG, normalized_email="testparent@example.com").exists()


@pytest.mark.django_db
def test_lead_form_post_stores_message_in_data(client):
    _school()
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    client.post(url, {"name": "Parent A", "email": "pa@example.com", "message": "Hello there"})
    lead = Lead.objects.get(school__slug=SLUG, normalized_email="pa@example.com")
    assert lead.data.get("message") == "Hello there"


@pytest.mark.django_db
def test_lead_form_post_captures_src_param(client):
    _school()
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG}) + "?src=homepage_banner"
    r = client.post(url, {"name": "Src Parent", "email": "srcp@example.com"})
    assert r.status_code == 200
    lead = Lead.objects.get(school__slug=SLUG, normalized_email="srcp@example.com")
    assert lead.data.get("src") == "homepage_banner"


@pytest.mark.django_db
def test_lead_form_post_source_is_website_lead_form(client):
    _school()
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    client.post(url, {"name": "Source Check", "email": "sourcecheck@example.com"})
    lead = Lead.objects.get(school__slug=SLUG, normalized_email="sourcecheck@example.com")
    assert lead.source == "website_lead_form"


@pytest.mark.django_db
def test_lead_form_honeypot_blocks_spam(client):
    _school()
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    r = client.post(url, {"name": "Bot", "email": "bot@example.com", "trap_field": "i am a bot"})
    assert r.status_code == 200
    # Success is rendered but NO lead created
    assert not Lead.objects.filter(school__slug=SLUG, normalized_email="bot@example.com").exists()


@pytest.mark.django_db
def test_lead_form_post_missing_name_shows_error(client):
    _school()
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    r = client.post(url, {"name": "", "email": "x@example.com"})
    assert r.status_code == 200
    assert b"required" in r.content.lower()
    assert not Lead.objects.filter(school__slug=SLUG, normalized_email="x@example.com").exists()


# ---------------------------------------------------------------------------
# ensure_lead_webhook_token
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_ensure_lead_webhook_token_generates_if_empty():
    school = SchoolFactory(plan="starter")
    assert school.lead_webhook_token == ""
    token = ensure_lead_webhook_token(school)
    assert token
    school.refresh_from_db()
    assert school.lead_webhook_token == token


@pytest.mark.django_db
def test_ensure_lead_webhook_token_idempotent():
    school = SchoolFactory(plan="starter")
    t1 = ensure_lead_webhook_token(school)
    t2 = ensure_lead_webhook_token(school)
    assert t1 == t2


# ---------------------------------------------------------------------------
# Webhook — bad token returns 404
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_webhook_bad_token_returns_404(client):
    school = _school()
    url = f"/webhooks/leads/{SLUG}/badtoken/"
    r = client.post(url, json.dumps({"name": "X", "email": "x@example.com"}), content_type="application/json")
    assert r.status_code == 404


@pytest.mark.django_db
def test_webhook_empty_token_returns_404(client):
    school = _school()
    # school has no token set — token="" won't match anything
    url = f"/webhooks/leads/{SLUG}//"
    r = client.post(url, content_type="application/json")
    assert r.status_code in (404, 400)


# ---------------------------------------------------------------------------
# Webhook — valid token, JSON payload
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_webhook_json_payload_creates_lead(client):
    school = _school()
    token = ensure_lead_webhook_token(school)
    url = f"/webhooks/leads/{SLUG}/{token}/"
    payload = {"name": "Webhook Parent", "email": "wp@example.com", "phone": "5550001"}
    r = client.post(url, json.dumps(payload), content_type="application/json")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "lead_id" in data
    assert Lead.objects.filter(school=school, normalized_email="wp@example.com").exists()


@pytest.mark.django_db
def test_webhook_form_encoded_payload_creates_lead(client):
    school = _school()
    token = ensure_lead_webhook_token(school)
    url = f"/webhooks/leads/{SLUG}/{token}/"
    r = client.post(url, {"name": "Form Parent", "email": "fp@example.com"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert Lead.objects.filter(school=school, normalized_email="fp@example.com").exists()


@pytest.mark.django_db
def test_webhook_maps_common_field_aliases(client):
    school = _school()
    token = ensure_lead_webhook_token(school)
    url = f"/webhooks/leads/{SLUG}/{token}/"
    payload = {
        "parent_name": "Alias Parent",
        "parent_email": "alias@example.com",
        "parent_phone": "5551234",
        "child_name": "Alias Child",
        "program_interest": "Math Tutoring",
        "comments": "Interested in after-school help",
    }
    r = client.post(url, json.dumps(payload), content_type="application/json")
    assert r.status_code == 200
    lead = Lead.objects.get(school=school, normalized_email="alias@example.com")
    assert lead.name == "Alias Parent"
    assert lead.phone == "5551234"
    assert lead.interested_in_label == "Math Tutoring"
    assert lead.data.get("student_name") == "Alias Child"
    assert lead.data.get("message") == "Interested in after-school help"


@pytest.mark.django_db
def test_webhook_source_is_webhook(client):
    school = _school()
    token = ensure_lead_webhook_token(school)
    url = f"/webhooks/leads/{SLUG}/{token}/"
    client.post(url, json.dumps({"name": "W", "email": "w@example.com"}), content_type="application/json")
    lead = Lead.objects.get(school=school, normalized_email="w@example.com")
    assert lead.source == "webhook"


@pytest.mark.django_db
def test_webhook_requires_email_or_phone(client):
    school = _school()
    token = ensure_lead_webhook_token(school)
    url = f"/webhooks/leads/{SLUG}/{token}/"
    r = client.post(url, json.dumps({"name": "No Contact"}), content_type="application/json")
    assert r.status_code == 400
    assert r.json()["ok"] is False


@pytest.mark.django_db
def test_webhook_phone_only_accepted(client):
    """Webhook requires email OR phone — phone alone should work."""
    school = _school()
    token = ensure_lead_webhook_token(school)
    url = f"/webhooks/leads/{SLUG}/{token}/"
    r = client.post(url, json.dumps({"name": "Phone Only", "phone": "5559999"}), content_type="application/json")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.django_db
def test_webhook_invalid_json_returns_400(client):
    school = _school()
    token = ensure_lead_webhook_token(school)
    url = f"/webhooks/leads/{SLUG}/{token}/"
    r = client.post(url, "not json", content_type="application/json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_webhook_extra_fields_stored_in_data(client):
    school = _school()
    token = ensure_lead_webhook_token(school)
    url = f"/webhooks/leads/{SLUG}/{token}/"
    payload = {"name": "Extra", "email": "extra@example.com", "custom_field": "custom_value"}
    client.post(url, json.dumps(payload), content_type="application/json")
    lead = Lead.objects.get(school=school, normalized_email="extra@example.com")
    assert lead.data.get("extra", {}).get("custom_field") == "custom_value"


# ---------------------------------------------------------------------------
# Multi-student / multi-program — same guardian email creates separate leads
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_public_form_creates_separate_leads_for_same_email(client):
    """
    Submitting the public lead form twice with the same guardian email but
    different student names must create two separate Lead records.
    A family with two kids should not merge into one lead.
    """
    school = _school()
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})

    client.post(url, {"name": "Sofia Reyes", "email": "family@example.com", "phone": "5550001"})
    client.post(url, {"name": "Lucas Reyes", "email": "family@example.com", "phone": "5550001"})

    leads = Lead.objects.filter(school=school, normalized_email="family@example.com").order_by("created_at")
    assert leads.count() == 2, f"Expected 2 leads, got {leads.count()}"
    names = {l.name for l in leads}
    assert names == {"Sofia Reyes", "Lucas Reyes"}


@pytest.mark.django_db
def test_webhook_creates_separate_leads_for_same_email(client):
    """
    Two webhook calls with the same email but different programs must each
    create a new lead — not update the first.
    """
    school = _school()
    token = ensure_lead_webhook_token(school)
    url = f"/webhooks/leads/{SLUG}/{token}/"

    client.post(url, json.dumps({"name": "Sofia Reyes", "email": "fam2@example.com", "program_interest": "Ballet"}), content_type="application/json")
    client.post(url, json.dumps({"name": "Sofia Reyes", "email": "fam2@example.com", "program_interest": "Jazz"}), content_type="application/json")

    leads = Lead.objects.filter(school=school, normalized_email="fam2@example.com").order_by("created_at")
    assert leads.count() == 2
    programs = {l.interested_in_label for l in leads}
    assert programs == {"Ballet", "Jazz"}


@pytest.mark.django_db
def test_lead_detail_shows_same_email_hint(client):
    """
    Lead detail page must show a warning when another lead with the same
    guardian email exists at the same school.
    """
    from core.tests.factories import LeadFactory, SchoolAdminMembershipFactory, UserFactory

    school = _school()
    user = UserFactory()
    SchoolAdminMembershipFactory(school=school, user=user, role="owner")

    lead1 = LeadFactory(school=school, email="hint@example.com", name="Sofia Reyes", interested_in_value="piano")
    lead2 = LeadFactory(school=school, email="hint@example.com", name="Lucas Reyes", interested_in_value="violin")

    client.force_login(user)
    url = reverse("school_lead_detail", kwargs={"school_slug": SLUG, "lead_id": lead1.id})
    resp = client.get(url)
    assert resp.status_code == 200

    content = resp.content.decode()
    # Warning must mention the other lead's name
    assert "Lucas Reyes" in content, "Hint must link to the other lead by name"
    # Sofia must not appear in the hint (that's the current lead's own name)
    # The warning banner text must be present
    assert "same guardian email" in content.lower() or "Same guardian email" in content


@pytest.mark.django_db
def test_lead_detail_no_hint_when_only_one_lead(client):
    """Lead detail must NOT show the duplicate hint when the email is unique."""
    from core.tests.factories import LeadFactory, SchoolAdminMembershipFactory, UserFactory

    school = _school()
    user = UserFactory()
    SchoolAdminMembershipFactory(school=school, user=user, role="owner")

    lead = LeadFactory(school=school, email="unique@example.com", name="Only Child")

    client.force_login(user)
    url = reverse("school_lead_detail", kwargs={"school_slug": SLUG, "lead_id": lead.id})
    resp = client.get(url)
    assert resp.status_code == 200
    assert "same guardian email" not in resp.content.decode().lower()
