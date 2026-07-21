"""
Regression tests: DB program options must appear in lead form instrument dropdowns.

Covers three surfaces:
  1. Public trial/lead form (school_lead_form_view)
  2. Admin New Lead form (school_lead_create_view)
  3. Admin lead detail edit card (school_lead_detail_view)

A new program added in the DB must appear without any YAML changes.
"""
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Lead, School, SchoolProgram
from core.services.config_loader import load_school_config
from core.tests.factories import SchoolAdminMembershipFactory, LeadFactory


SLUG = "south-bay-music"


@pytest.fixture
def sbmc_school(db):
    cfg = load_school_config(SLUG)
    school, _ = School.objects.get_or_create(
        slug=SLUG,
        defaults={"display_name": cfg.display_name, "plan": "starter"},
    )
    school.plan = "starter"
    school.program_field_key = "instrument"
    school.save(update_fields=["plan", "program_field_key"])
    return school


@pytest.fixture
def guitar_program(sbmc_school):
    return SchoolProgram.objects.create(
        school=sbmc_school,
        name="Guitar",
        code="guitar",
        is_active=True,
        display_order=10,
    )


@pytest.fixture
def admin_client(client, sbmc_school):
    membership = SchoolAdminMembershipFactory(school=sbmc_school, role="editor")
    client.force_login(membership.user)
    return client


# ---------------------------------------------------------------------------
# 1. Public lead/trial form
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_public_lead_form_shows_db_program(client, sbmc_school, guitar_program):
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    r = client.get(url)
    assert r.status_code == 200
    assert b"guitar" in r.content.lower()
    assert b"Guitar" in r.content


@pytest.mark.django_db
def test_public_lead_form_excludes_inactive_program(client, sbmc_school, guitar_program):
    guitar_program.is_active = False
    guitar_program.save(update_fields=["is_active"])
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    r = client.get(url)
    assert r.status_code == 200
    assert b"Guitar" not in r.content


# ---------------------------------------------------------------------------
# 2. Admin New Lead form
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_new_lead_form_shows_db_program(admin_client, sbmc_school, guitar_program):
    url = reverse("school_lead_create", kwargs={"school_slug": SLUG})
    r = admin_client.get(url)
    assert r.status_code == 200
    assert b"Guitar" in r.content


@pytest.mark.django_db
def test_admin_new_lead_submit_resolves_db_program_label(admin_client, sbmc_school, guitar_program):
    url = reverse("school_lead_create", kwargs={"school_slug": SLUG})
    r = admin_client.post(url, {
        "student_name": "Test Student",
        "email": "test@example.com",
        "instrument": "guitar",
    })
    assert r.status_code == 302
    lead = Lead.objects.get(school=sbmc_school, email="test@example.com")
    assert lead.interested_in_value == "guitar"
    assert lead.interested_in_label == "Guitar"


# ---------------------------------------------------------------------------
# 3. Admin lead detail edit card
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_lead_detail_edit_card_shows_db_program(admin_client, sbmc_school, guitar_program):
    lead = LeadFactory(school=sbmc_school, email="detail@example.com")
    url = reverse("school_lead_detail", kwargs={"school_slug": SLUG, "lead_id": lead.pk})
    r = admin_client.get(url)
    assert r.status_code == 200
    assert b"Guitar" in r.content


# ---------------------------------------------------------------------------
# 4. Lead update — instrument change saves correct label
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_lead_update_resolves_new_program_label(admin_client, sbmc_school, guitar_program):
    lead = LeadFactory(school=sbmc_school, email="update@example.com", interested_in_value="", interested_in_label="")
    url = reverse("school_lead_update", kwargs={"school_slug": SLUG, "lead_id": lead.pk})
    r = admin_client.post(url, {
        "name": lead.name,
        "email": lead.email,
        "phone": "",
        "field__instrument": "guitar",
        "field__student_age": "",
    })
    assert r.status_code == 302
    lead.refresh_from_db()
    assert lead.interested_in_value == "guitar"
    assert lead.interested_in_label == "Guitar"


# ---------------------------------------------------------------------------
# 5. Inactive program excluded from admin new lead form
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_new_lead_form_excludes_inactive_program(admin_client, sbmc_school, guitar_program):
    guitar_program.is_active = False
    guitar_program.save(update_fields=["is_active"])
    url = reverse("school_lead_create", kwargs={"school_slug": SLUG})
    r = admin_client.get(url)
    assert r.status_code == 200
    assert b"Guitar" not in r.content


# ---------------------------------------------------------------------------
# 6. Zero active programs — forms must not 500
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_public_lead_form_no_programs_no_500(client, sbmc_school):
    url = reverse("school_lead_form", kwargs={"school_slug": SLUG})
    r = client.get(url)
    assert r.status_code == 200


@pytest.mark.django_db
def test_admin_new_lead_form_no_programs_no_500(admin_client, sbmc_school):
    url = reverse("school_lead_create", kwargs={"school_slug": SLUG})
    r = admin_client.get(url)
    assert r.status_code == 200


@pytest.mark.django_db
def test_enrollment_form_no_programs_no_500(client, sbmc_school):
    url = reverse("apply", kwargs={"school_slug": SLUG})
    r = client.get(url)
    assert r.status_code == 200
