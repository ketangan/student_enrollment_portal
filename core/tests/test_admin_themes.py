import json

import pytest
from django.test import RequestFactory

from core.models import AdminPreference
from core.services.admin_themes import (
    ADMIN_THEMES,
    DEFAULT_THEME_KEY,
    THEME_CHOICES,
    get_theme_ui_tweaks,
    get_themes_for_api,
)
from core.tests.factories import SchoolFactory, UserFactory


# ---------------------------------------------------------------------------
# Theme registry unit tests
# ---------------------------------------------------------------------------


class TestThemeRegistry:
    def test_default_theme_exists_in_registry(self):
        assert DEFAULT_THEME_KEY in ADMIN_THEMES

    def test_every_theme_has_required_keys(self):
        for key, cfg in ADMIN_THEMES.items():
            assert "label" in cfg, f"{key} missing label"
            assert "icon" in cfg, f"{key} missing icon"
            assert "description" in cfg, f"{key} missing description"
            assert "ui_tweaks" in cfg, f"{key} missing ui_tweaks"

    def test_every_theme_has_bootswatch_theme_key(self):
        for key, cfg in ADMIN_THEMES.items():
            assert "theme" in cfg["ui_tweaks"], f"{key} missing Bootswatch theme"

    def test_theme_choices_matches_registry(self):
        assert len(THEME_CHOICES) == len(ADMIN_THEMES)
        for key, label in THEME_CHOICES:
            assert key in ADMIN_THEMES
            assert ADMIN_THEMES[key]["label"] == label

    def test_get_theme_ui_tweaks_returns_complete_dict(self):
        for key in ADMIN_THEMES:
            tweaks = get_theme_ui_tweaks(key)
            # Must include all Jazzmin baseline keys
            assert "theme" in tweaks
            assert "navbar" in tweaks
            assert "sidebar" in tweaks
            assert "button_classes" in tweaks
            assert isinstance(tweaks["button_classes"], dict)

    def test_get_theme_ui_tweaks_unknown_key_falls_back_to_default(self):
        tweaks = get_theme_ui_tweaks("nonexistent_theme")
        default_tweaks = get_theme_ui_tweaks(DEFAULT_THEME_KEY)
        assert tweaks == default_tweaks

    def test_get_theme_ui_tweaks_empty_string_falls_back_to_default(self):
        tweaks = get_theme_ui_tweaks("")
        default_tweaks = get_theme_ui_tweaks(DEFAULT_THEME_KEY)
        assert tweaks == default_tweaks

    def test_get_theme_ui_tweaks_none_falls_back_to_default(self):
        tweaks = get_theme_ui_tweaks(None)
        default_tweaks = get_theme_ui_tweaks(DEFAULT_THEME_KEY)
        assert tweaks == default_tweaks

    def test_get_theme_ui_tweaks_does_not_mutate_defaults(self):
        """Calling for different themes must not cross-contaminate."""
        midnight = get_theme_ui_tweaks("midnight")
        clean = get_theme_ui_tweaks("clean")
        assert midnight["theme"] != clean["theme"]
        # Call midnight again — should still be the same
        midnight2 = get_theme_ui_tweaks("midnight")
        assert midnight == midnight2

    def test_get_themes_for_api_returns_list_of_dicts(self):
        result = get_themes_for_api()
        assert isinstance(result, list)
        assert len(result) == len(ADMIN_THEMES)
        for item in result:
            assert {"key", "label", "icon", "description"} == set(item.keys())


# ---------------------------------------------------------------------------
# AdminPreference model tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAdminPreferenceModel:
    def test_create_preference(self):
        user = UserFactory()
        pref = AdminPreference.objects.create(user=user, theme="clean")
        assert pref.theme == "clean"
        assert str(pref) == f"{user.username} → clean"

    def test_default_theme(self):
        user = UserFactory()
        pref = AdminPreference.objects.create(user=user)
        assert pref.theme == DEFAULT_THEME_KEY

    def test_one_to_one_reverse_access(self):
        user = UserFactory()
        AdminPreference.objects.create(user=user, theme="minty")
        user.refresh_from_db()
        assert user.admin_preference.theme == "minty"

    def test_cascade_delete(self):
        user = UserFactory()
        AdminPreference.objects.create(user=user, theme="midnight")
        user.delete()
        assert AdminPreference.objects.count() == 0


# ---------------------------------------------------------------------------
# Theme API view tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAdminThemeAPI:
    """Integration tests for the /admin/api/theme/ endpoint."""

    def _url(self):
        return "/admin/api/theme/"

    def test_get_anonymous_returns_403(self, client):
        resp = client.get(self._url())
        assert resp.status_code == 302  # admin_view redirects anon users to login

    def test_get_non_staff_returns_403(self, client):
        user = UserFactory()
        client.force_login(user)
        resp = client.get(self._url())
        assert resp.status_code == 302  # admin_view redirects non-staff

    def test_get_staff_returns_themes(self, client):
        user = UserFactory(is_staff=True)
        client.force_login(user)
        resp = client.get(self._url())
        assert resp.status_code == 200
        data = resp.json()
        assert "themes" in data
        assert "current" in data
        assert data["current"] == DEFAULT_THEME_KEY
        assert len(data["themes"]) == len(ADMIN_THEMES)

    def test_get_returns_saved_preference(self, client):
        user = UserFactory(is_staff=True)
        AdminPreference.objects.create(user=user, theme="clean")
        client.force_login(user)
        resp = client.get(self._url())
        data = resp.json()
        assert data["current"] == "clean"

    def test_post_saves_preference(self, client):
        user = UserFactory(is_staff=True)
        client.force_login(user)
        resp = client.post(
            self._url(),
            data=json.dumps({"theme": "minty"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["theme"] == "minty"
        assert AdminPreference.objects.get(user=user).theme == "minty"

    def test_post_updates_existing_preference(self, client):
        user = UserFactory(is_staff=True)
        AdminPreference.objects.create(user=user, theme="midnight")
        client.force_login(user)
        resp = client.post(
            self._url(),
            data=json.dumps({"theme": "clean"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert AdminPreference.objects.get(user=user).theme == "clean"

    def test_post_unknown_theme_returns_400(self, client):
        user = UserFactory(is_staff=True)
        client.force_login(user)
        resp = client.post(
            self._url(),
            data=json.dumps({"theme": "nope"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_post_invalid_json_returns_400(self, client):
        user = UserFactory(is_staff=True)
        client.force_login(user)
        resp = client.post(
            self._url(),
            data="not json",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_superuser_can_use_api(self, client):
        user = UserFactory(is_staff=True, is_superuser=True)
        client.force_login(user)
        resp = client.get(self._url())
        assert resp.status_code == 200
        resp = client.post(
            self._url(),
            data=json.dumps({"theme": "minty"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
