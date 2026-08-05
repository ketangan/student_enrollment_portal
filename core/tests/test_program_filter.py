"""
SBMC age field behavior tests.

Covers:
  - SBMC YAML field order (birthday before age)
  - Age field is not required
  - hide_if_age_gte: 18 configured in YAML
  - Enrollment form renders data-hide-if-age-gte="18" on the age input
"""
from __future__ import annotations

import pathlib

import pytest
import yaml
from django.test import Client
from django.urls import reverse

from core.models import School, SchoolProgram

SBMC_SLUG = "south-bay-music"
_YAML_PATH = pathlib.Path("configs/schools/south-bay-music.yaml")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sbmc_school():
    school, _ = School.objects.get_or_create(
        slug=SBMC_SLUG,
        defaults={
            "display_name": "South Bay Music Conservatory",
            "plan": "trial",
            "program_field_key": "instrument",
        },
    )
    if not school.program_field_key:
        school.program_field_key = "instrument"
        school.save(update_fields=["program_field_key"])
    return school


def _sbmc_programs(school):
    for code, name in [("piano", "Piano"), ("violin", "Violin")]:
        SchoolProgram.objects.get_or_create(
            school=school, code=code, defaults={"name": name, "is_active": True, "display_order": 0}
        )


def _sbmc_form_fields():
    """Walk all fields from the SBMC YAML form (top-level 'form:' key)."""
    raw = yaml.safe_load(_YAML_PATH.read_text())
    form = raw.get("form", {})
    for section in form.get("sections", []):
        yield from section.get("fields", [])


# ---------------------------------------------------------------------------
# Age field — YAML guards
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sbmc_yaml_has_birthday_before_age():
    """
    Regression guard: student_birthday must appear before student_age in the
    south-bay-music.yaml form, so DOB is collected before age is auto-populated.
    """
    keys = [
        f["key"] for f in _sbmc_form_fields()
        if f.get("key") in ("student_birthday", "student_age")
    ]
    assert keys == ["student_birthday", "student_age"], (
        f"student_birthday must appear before student_age in YAML, got order: {keys}"
    )


@pytest.mark.django_db
def test_sbmc_yaml_age_field_is_not_required():
    """
    Regression guard: the student_age field in south-bay-music.yaml must be
    required: false — it's auto-populated from DOB and never user-entered.
    """
    age_field = next(
        (f for f in _sbmc_form_fields() if f.get("key") == "student_age"), None
    )
    assert age_field is not None, "student_age field not found in south-bay-music.yaml"
    assert age_field.get("required") is False, (
        f"student_age must have required: false, got: {age_field.get('required')!r}"
    )


@pytest.mark.django_db
def test_sbmc_yaml_age_field_has_hide_if_age_gte_18():
    """
    Regression guard: student_age must have hide_if_age_gte: 18 so the age row
    is shown for children (age < 18) and hidden for adults.
    """
    age_field = next(
        (f for f in _sbmc_form_fields() if f.get("key") == "student_age"), None
    )
    assert age_field is not None, "student_age field not found in south-bay-music.yaml"
    assert age_field.get("hide_if_age_gte") == 18, (
        f"student_age must have hide_if_age_gte: 18, got: {age_field.get('hide_if_age_gte')!r}"
    )


# ---------------------------------------------------------------------------
# Age field — rendered HTML
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_sbmc_enrollment_form_renders_age_with_hide_threshold():
    """
    SBMC enrollment form must render data-hide-if-age-gte="18" on the student_age
    input. JS uses this to show age for children (age < 18) and hide for adults.
    """
    school = _sbmc_school()
    _sbmc_programs(school)

    client = Client()
    resp = client.get(reverse("apply", kwargs={"school_slug": SBMC_SLUG}))

    assert resp.status_code == 200
    content = resp.content.decode()
    assert 'id="student_age"' in content, "student_age input not found in SBMC form"
    assert 'data-hide-if-age-gte="18"' in content, (
        "student_age input must carry data-hide-if-age-gte=\"18\" — check that "
        "south-bay-music.yaml has hide_if_age_gte: 18 on the student_age field."
    )
