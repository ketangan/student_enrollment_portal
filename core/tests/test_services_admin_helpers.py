import os
import yaml
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.urls import reverse
from django.test import RequestFactory

import pytest

from core.services import validation, config_loader, form_utils
from core.templatetags.form_extras import get_item
from core.tests.factories import SchoolFactory, SubmissionFactory, UserFactory, SchoolAdminMembershipFactory
from core import admin as core_admin


def test__is_empty_and_validation_branches():
    # _is_empty checks
    assert validation._is_empty(None)
    assert validation._is_empty("")
    assert validation._is_empty([])
    assert not validation._is_empty(0)

    # Build a form with various field types
    form = {
        "sections": [
            {
                "fields": [
                    {"key": "req_text", "type": "text", "required": True},
                    {"key": "email", "type": "email"},
                    {"key": "bday", "type": "date"},
                    {"key": "amt", "type": "number"},
                    {"key": "opt", "type": "checkbox"},
                    {"key": "multi", "type": "multiselect"},
                ]
            }
        ]
    }

    # Prepare POST-like data using QueryDict for getlist support
    q = QueryDict("email=bademail&bday=2020-13-01&amt=notanumber&opt=on&multi=1&multi=2")

    cleaned, errors = validation.validate_submission(form, q)

    # required missing produces error
    assert "req_text" in errors
    # email invalid
    assert errors.get("email") == "Enter a valid email address."
    # date invalid
    assert errors.get("bday") == "Enter a valid date (YYYY-MM-DD)."
    # number invalid
    assert errors.get("amt") == "Enter a valid number."
    # checkbox parsed
    assert cleaned.get("opt") is True
    # multiselect becomes list from getlist; when valid, cleaned should include list
    assert isinstance(cleaned.get("multi"), list) or cleaned.get("multi") == ["1", "2"]


def test_form_utils_and_resolve_label():
    form = {
        "sections": [
            {
                "fields": [
                    {
                        "key": "dance_style",
                        "type": "select",
                        "options": [{"value": "b", "label": "Ballet"}, {"value": "j", "label": "Jazz"}],
                    },
                    {"key": "level", "type": "multiselect", "options": [{"value": 1, "label": "One"}]},
                ]
            }
        ]
    }

    label_map = form_utils.build_option_label_map(form)
    assert label_map["dance_style"]["b"] == "Ballet"

    assert form_utils.resolve_label("dance_style", "b", label_map) == "Ballet"
    assert form_utils.resolve_label("level", [1], label_map) == "One"
    assert form_utils.resolve_label("unknown", None, label_map) is None


def test_prettify_and_load_school_config(tmp_path, settings):
    # create configs/schools/<slug>.yaml
    base = tmp_path / "proj"
    configs_dir = base / "configs" / "schools"
    configs_dir.mkdir(parents=True)

    slug = "my-school"
    data = {
        "school": {"slug": slug},
        "form": {"sections": []},
        "branding": {"logo_url": "logo.png", "theme": {"primary_color": "#000000"}},
    }

    file_path = configs_dir / f"{slug}.yaml"
    file_path.write_text(yaml.safe_dump(data))

    settings.BASE_DIR = str(base)

    cfg = config_loader.load_school_config(slug)
    assert cfg is not None
    assert cfg.school_slug == slug
    assert cfg.display_name == "My School"
    assert cfg.branding["logo_url"] == "logo.png"


def test_get_item_templatetag():
    assert get_item({"a": 1}, "a") == 1
    # non-mapping should return empty string
    class Bad:
        def get(self, k, d=None):
            raise Exception("boom")

    assert get_item(Bad(), "x") == ""


def test_admin_helpers_and_getters(db):
    User = get_user_model()
    u = User.objects.create_user(username="u1")
    assert not core_admin._is_superuser(u)

    su = User.objects.create_superuser(username="su", email="su@example.com", password="pw")
    assert core_admin._is_superuser(su)

    school = SchoolFactory.create()
    memb = SchoolAdminMembershipFactory.create(user=su, school=school)
    # membership school id
    assert core_admin._membership_school_id(su) == memb.school_id
    assert core_admin._has_school_membership(su)


def test_management_ensure_superuser(monkeypatch, db):
    User = get_user_model()
    # ensure env missing -> no create
    monkeypatch.delenv("DJANGO_SUPERUSER_USERNAME", raising=False)
    monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD", raising=False)
    call_command("ensure_superuser")

    # now set env and run
    monkeypatch.setenv("DJANGO_SUPERUSER_USERNAME", "ciadmin")
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "pass")
    monkeypatch.setenv("DJANGO_SUPERUSER_EMAIL", "ci@example.com")

    call_command("ensure_superuser")
    assert User.objects.filter(username="ciadmin").exists()
