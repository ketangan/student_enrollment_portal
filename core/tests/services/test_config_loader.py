import yaml
from pathlib import Path
import pytest

from core.services import config_loader


FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def test_load_valid_config(settings):
    settings.BASE_DIR = str(FIXTURES_DIR)

    cfg = config_loader.load_school_config("valid-school")
    assert cfg is not None
    assert cfg.school_slug == "valid-school"
    assert cfg.display_name == "Valid School"
    # branding provided
    assert cfg.branding["logo_url"] == "/logo.png"
    assert isinstance(cfg.form, dict)


def test_missing_file_returns_none(settings):
    settings.BASE_DIR = str(FIXTURES_DIR)
    assert config_loader.load_school_config("does-not-exist") is None


def test_invalid_yaml_raises(settings):
    settings.BASE_DIR = str(FIXTURES_DIR)
    with pytest.raises(yaml.YAMLError):
        # invalid_yaml.yaml contains malformed YAML
        config_loader.load_school_config("invalid_yaml")


def test_missing_school_key_raises(settings):
    settings.BASE_DIR = str(FIXTURES_DIR)
    cfg = config_loader.load_school_config("missing_school_key")
    assert cfg is not None
    with pytest.raises(KeyError):
        _ = cfg.school_slug


def test_display_name_fallback_and_branding_defaults(settings):
    settings.BASE_DIR = str(FIXTURES_DIR)
    cfg = config_loader.load_school_config("no-display")
    assert cfg is not None
    # fallback prettify from slug
    assert cfg.display_name == "No Display"
    # branding may be missing; branding keys should exist with defaults
    b = cfg.branding
    assert isinstance(b, dict)
    assert "theme" in b and "primary_color" in b["theme"]
