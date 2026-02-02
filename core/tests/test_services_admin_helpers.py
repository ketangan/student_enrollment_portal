import os
import yaml
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.http import QueryDict
from django.urls import reverse
from django.test import RequestFactory

import pytest

from core.services import validation, config_loader, form_utils
from core.services.admin_submission_yaml import (
    apply_post_to_submission_data,
    build_yaml_sections,
    validate_required_fields,
)
from core.templatetags.form_extras import get_item
from core.tests.factories import SchoolFactory, SubmissionFactory, UserFactory, SchoolAdminMembershipFactory
from core import admin as core_admin
from core.admin.common import DYN_PREFIX, _resolve_submission_form_cfg_and_labels


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


def test_admin_common_bytes_dyn_and_label_map(monkeypatch):
    # _bytes_to_mb branches
    from core.admin.common import _bytes_to_mb

    assert _bytes_to_mb(0) == ""
    assert _bytes_to_mb(-1) == ""
    assert _bytes_to_mb("nope") == ""
    assert _bytes_to_mb(500) == "0 KB"  # rounds
    assert _bytes_to_mb(2048).endswith("KB")
    assert _bytes_to_mb(5 * 1024 * 1024).endswith("MB")
    assert _bytes_to_mb(5 * 1024 * 1024 * 1024).endswith("GB")

    # dyn key helpers
    from core.admin.common import _dyn_key, _orig_key, _build_field_label_map

    assert _dyn_key("first_name") == f"{DYN_PREFIX}first_name"
    assert _orig_key(f"{DYN_PREFIX}first_name") == "first_name"
    assert _orig_key("plain") == "plain"

    # label map prefers explicit labels
    cfg = _DummyCfg(
        form={
            "sections": [
                {"fields": [{"key": "k1", "label": "K One"}, {"key": "k2"}]},
            ]
        }
    )
    monkeypatch.setattr("core.admin.common.load_school_config", lambda slug: cfg)
    lm = _build_field_label_map("any")
    assert lm == {"k1": "K One"}


def test_admin_common_module_import_side_effects_are_safe(monkeypatch):
    # Cover the import-time admin.site tweaks and NotRegistered exception branch.
    import importlib

    import django.contrib.admin as django_admin
    from django.contrib.auth.models import Group

    def _raise_not_registered(model):
        assert model is Group
        raise django_admin.sites.NotRegistered

    monkeypatch.setattr(django_admin.site, "unregister", _raise_not_registered)

    import core.admin.common as common

    importlib.reload(common)
    assert django_admin.site.site_url is None


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


class _DummyCfg:
    def __init__(self, *, form: dict, raw: dict | None = None):
        self.form = form
        self.raw = raw or {}


def test__resolve_submission_form_cfg_and_labels_single_form_prefers_yaml_label():
    cfg = _DummyCfg(
        form={
            "sections": [
                {
                    "title": "Main",
                    "fields": [
                        {"key": "first_name", "label": "First Name", "type": "text"},
                        {"key": "age", "type": "number"},
                    ],
                }
            ]
        },
        raw={},
    )

    form_cfg, label_map = _resolve_submission_form_cfg_and_labels(cfg, "default")
    assert form_cfg == cfg.form
    assert label_map["first_name"] == "First Name"
    assert label_map["age"] == "Age"  # fallback


def test__resolve_submission_form_cfg_and_labels_multi_form_specific_key():
    enrollment_form = {
        "sections": [
            {"title": "Enrollment", "fields": [{"key": "student_name", "label": "Student Name"}]}
        ]
    }
    waiver_form = {
        "sections": [
            {"title": "Waivers", "fields": [{"key": "agree", "label": "I Agree", "type": "checkbox"}]}
        ]
    }
    raw_forms = {
        "enrollment": {"form": enrollment_form},
        "waiver": {"form": waiver_form},
    }
    cfg = _DummyCfg(form=enrollment_form, raw={"forms": raw_forms})

    form_cfg, label_map = _resolve_submission_form_cfg_and_labels(cfg, "waiver")
    assert form_cfg == waiver_form
    assert "agree" in label_map
    assert "student_name" not in label_map


def test__resolve_submission_form_cfg_and_labels_multi_form_multi_combines_in_yaml_order():
    form_a = {
        "sections": [
            {"title": "A", "fields": [{"key": "a1", "label": "A One"}]},
        ]
    }
    form_b = {
        "sections": [
            {"title": "B", "fields": [{"key": "b1", "label": "B One"}]},
        ]
    }

    raw_forms = {
        "step_a": {"form": form_a},
        "step_b": {"form": form_b},
    }
    cfg = _DummyCfg(form=form_a, raw={"forms": raw_forms})

    combined_form, label_map = _resolve_submission_form_cfg_and_labels(cfg, "multi")
    assert [s["title"] for s in combined_form.get("sections", [])] == ["A", "B"]
    assert set(label_map.keys()) == {"a1", "b1"}


def test_admin_submission_yaml_helpers_build_sections_validate_and_apply():
    form = {
        "sections": [
            {
                "title": "Main",
                "fields": [
                    {"key": "name", "label": "Full Name", "type": "text", "required": True},
                    {"key": "age", "type": "number"},
                    {"key": "agree", "label": "I Agree", "type": "checkbox", "required": True},
                    {"key": "levels", "type": "multiselect"},
                    {"key": "upload", "type": "file"},
                ],
            }
        ]
    }
    cfg = _DummyCfg(form=form)

    existing = {"name": "Old", "age": 9, "agree": False, "levels": ["x"]}

    # build_yaml_sections without post_data uses existing values and skips file
    sections = build_yaml_sections(cfg, existing, post_data=None, form=form)
    assert sections and sections[0]["title"] == "Main"
    keys = [f["key"] for f in sections[0]["fields"]]
    assert "upload" not in keys

    # missing required fields should produce human errors
    empty_post = QueryDict("")
    errors = validate_required_fields(cfg, empty_post, form=form)
    assert "Full Name is required." in errors
    assert "I Agree is required." in errors

    # post_data overrides existing; multiselect uses getlist; checkbox uses presence
    q = QueryDict(mutable=True)
    q.update({f"{DYN_PREFIX}name": "New", f"{DYN_PREFIX}age": "12"})
    q.setlist(f"{DYN_PREFIX}levels", ["a", "b"])
    q.update({f"{DYN_PREFIX}agree": "on"})

    new_data = apply_post_to_submission_data(cfg, q, existing_data=existing, form=form)
    assert new_data["name"] == "New"
    assert new_data["age"] == 12.0
    assert new_data["agree"] is True
    assert new_data["levels"] == ["a", "b"]

    # number invalid => raw string
    q2 = QueryDict(mutable=True)
    q2.update({f"{DYN_PREFIX}age": "not-a-number"})
    new_data2 = apply_post_to_submission_data(cfg, q2, existing_data=existing, form=form)
    assert new_data2["age"] == "not-a-number"
