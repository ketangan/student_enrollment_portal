# core/tests/test_waiver.py
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Submission
from core.services.admin_submission_yaml import apply_post_to_submission_data, build_yaml_sections
from core.services.validation import validate_submission
from core.tests.factories import SchoolFactory, SubmissionFactory


# ---------------------------------------------------------------------------
# Minimal form config helpers
# ---------------------------------------------------------------------------

def _waiver_form(required=True, link_url=""):
    field = {
        "key": "liability_waiver",
        "type": "waiver",
        "required": required,
        "text": "I agree to the terms.",
        "checkbox_label": "I have read and agree",
    }
    if link_url:
        field["link_url"] = link_url
    return {"sections": [{"title": "Legal", "fields": [field]}]}


def _make_apply_config(school, *, with_waiver=True, waiver_required=True):
    """Minimal config object for apply_view integration tests."""
    fields = [{"key": "first_name", "label": "First Name", "type": "text", "required": True}]
    if with_waiver:
        fields.append({
            "key": "liability_waiver",
            "type": "waiver",
            "required": waiver_required,
            "text": "I agree to the terms.",
            "link_url": "https://example.com/terms",
        })

    form = {"title": "Apply", "sections": [{"title": "Contact", "fields": fields}]}
    raw = {
        "school": {"slug": school.slug, "display_name": "Test School", "website_url": "", "source_url": ""},
        "form": form,
        "success": {
            "title": "Done",
            "message": "Thanks",
            "notifications": {
                "submission_email": {"to": "a@a.com", "from_email": "a@a.com", "subject": "New"},
                "applicant_confirmation": {"enabled": False},
            },
        },
    }

    class _Cfg:
        display_name = "Test School"
        branding = {}

    _Cfg.form = form
    _Cfg.raw = raw
    return _Cfg()


# ---------------------------------------------------------------------------
# Validation unit tests
# ---------------------------------------------------------------------------

def test_waiver_agreed_stores_true():
    form = _waiver_form()
    from django.http import QueryDict
    post = QueryDict("liability_waiver=true")
    cleaned, errors = validate_submission(form, post)
    assert errors == {}
    assert cleaned["liability_waiver"] is True


def test_waiver_required_unchecked_error():
    form = _waiver_form(required=True)
    from django.http import QueryDict
    post = QueryDict("")  # no checkbox submitted
    cleaned, errors = validate_submission(form, post)
    assert "liability_waiver" in errors
    assert errors["liability_waiver"] == "You must agree to continue."


def test_waiver_optional_unchecked_no_error():
    form = _waiver_form(required=False)
    from django.http import QueryDict
    post = QueryDict("")
    cleaned, errors = validate_submission(form, post)
    assert errors == {}
    assert cleaned["liability_waiver"] is False


def test_waiver_error_message_is_custom():
    """The waiver error must not use the generic required message."""
    form = _waiver_form(required=True)
    from django.http import QueryDict
    post = QueryDict("")
    cleaned, errors = validate_submission(form, post)
    assert errors.get("liability_waiver") != "This field is required."


# ---------------------------------------------------------------------------
# View integration tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_apply_view_waiver_stores_metadata(client, monkeypatch, settings):
    """Agreed waiver stores boolean, timestamp, IP, and text snapshot."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="starter", slug="waiver-metadata")
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school))

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"first_name": "Alice", "liability_waiver": "true"},
        REMOTE_ADDR="10.0.0.1",
    )

    submission = Submission.objects.get(school=school)
    assert submission.data["liability_waiver"] is True
    assert submission.data.get("liability_waiver__at")
    assert submission.data.get("liability_waiver__ip")
    assert submission.data.get("liability_waiver__text") == "I agree to the terms."


@pytest.mark.django_db
def test_apply_view_waiver_rejects_unchecked(client, monkeypatch, settings):
    """Required waiver unchecked → form re-rendered (200), no submission created."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="starter", slug="waiver-reject")
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school))

    response = client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"first_name": "Bob"},
    )

    assert response.status_code == 200
    assert Submission.objects.filter(school=school).count() == 0


@pytest.mark.django_db
def test_apply_view_waiver_stripped_when_feature_disabled(client, monkeypatch, settings):
    """School with waiver_enabled=False: waiver field is stripped — key absent from submission.data."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="starter", slug="waiver-stripped", feature_flags={"waiver_enabled": False})
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school))

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"first_name": "Carol"},
    )

    submission = Submission.objects.get(school=school)
    assert "liability_waiver" not in submission.data


@pytest.mark.django_db
def test_apply_view_waiver_no_metadata_when_optional_unchecked(client, monkeypatch, settings):
    """Optional waiver, unchecked → False stored, no __at metadata."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="starter", slug="waiver-optional")
    monkeypatch.setattr(
        "core.views.load_school_config",
        lambda slug: _make_apply_config(school, waiver_required=False),
    )

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"first_name": "Dave"},
    )

    submission = Submission.objects.get(school=school)
    assert submission.data["liability_waiver"] is False
    assert "liability_waiver__at" not in submission.data


@pytest.mark.django_db
def test_apply_view_waiver_ip_stored(client, monkeypatch, settings):
    """REMOTE_ADDR is stored in __ip when no XFF header."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="starter", slug="waiver-ip")
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school))

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"first_name": "Eve", "liability_waiver": "true"},
        REMOTE_ADDR="192.168.1.42",
    )

    submission = Submission.objects.get(school=school)
    assert submission.data.get("liability_waiver__ip") == "192.168.1.42"


@pytest.mark.django_db
def test_apply_view_waiver_xff_ip_preferred(client, monkeypatch, settings):
    """HTTP_X_FORWARDED_FOR first entry takes precedence over REMOTE_ADDR."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="starter", slug="waiver-xff")
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school))

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"first_name": "Frank", "liability_waiver": "true"},
        REMOTE_ADDR="10.0.0.1",
        HTTP_X_FORWARDED_FOR="203.0.113.5, 10.0.0.1",
    )

    submission = Submission.objects.get(school=school)
    assert submission.data.get("liability_waiver__ip") == "203.0.113.5"


@pytest.mark.django_db
def test_apply_view_waiver_text_snapshot_stored(client, monkeypatch, settings):
    """The exact waiver text from config is snapshotted in __text."""
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = SchoolFactory(plan="starter", slug="waiver-text-snap")
    monkeypatch.setattr("core.views.load_school_config", lambda slug: _make_apply_config(school))

    client.post(
        reverse("apply", kwargs={"school_slug": school.slug}),
        data={"first_name": "Grace", "liability_waiver": "true"},
    )

    submission = Submission.objects.get(school=school)
    assert submission.data.get("liability_waiver__text") == "I agree to the terms."


# ---------------------------------------------------------------------------
# Admin display tests
# ---------------------------------------------------------------------------

class _FakeCfg:
    """Minimal config stub for build_yaml_sections."""
    def __init__(self, form):
        self.form = form


@pytest.mark.django_db
def test_build_yaml_sections_waiver_agreed():
    """Agreed waiver → value dict with agreed/timestamp/ip/text/link_url."""
    form = _waiver_form(link_url="https://example.com/terms")
    cfg = _FakeCfg(form)
    existing = {
        "liability_waiver": True,
        "liability_waiver__at": "2026-04-06T10:00:00+00:00",
        "liability_waiver__ip": "1.2.3.4",
        "liability_waiver__text": "I agree to the terms.",
        "liability_waiver__link_url": "https://example.com/terms",
    }

    sections = build_yaml_sections(cfg, existing)
    field = sections[0]["fields"][0]

    assert field["type"] == "waiver"
    assert field["value"]["agreed"] is True
    assert field["value"]["timestamp"] == "2026-04-06T10:00:00+00:00"
    assert field["value"]["ip"] == "1.2.3.4"
    assert field["value"]["text"] == "I agree to the terms."
    assert field["value"]["link_url"] == "https://example.com/terms"


@pytest.mark.django_db
def test_build_yaml_sections_waiver_not_agreed():
    """Missing or False waiver → value.agreed = False."""
    form = _waiver_form()
    cfg = _FakeCfg(form)

    sections = build_yaml_sections(cfg, {})
    field = sections[0]["fields"][0]

    assert field["value"]["agreed"] is False
    assert field["value"]["timestamp"] == ""
    assert field["value"]["ip"] == ""


@pytest.mark.django_db
def test_apply_post_skips_waiver_fields():
    """Admin POST must not overwrite stored waiver data."""
    school = SchoolFactory(plan="starter")
    form = _waiver_form()
    cfg = _FakeCfg(form)
    existing = {
        "liability_waiver": True,
        "liability_waiver__at": "2026-04-06T10:00:00+00:00",
        "liability_waiver__ip": "1.2.3.4",
        "liability_waiver__text": "I agree to the terms.",
    }

    from django.http import QueryDict
    post = QueryDict("")  # no waiver input posted (read-only in admin)

    result = apply_post_to_submission_data(cfg, post, existing, form=form)

    assert result["liability_waiver"] is True
    assert result["liability_waiver__at"] == "2026-04-06T10:00:00+00:00"
