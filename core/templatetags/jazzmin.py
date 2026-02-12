from __future__ import annotations

import copy
import logging

from django import template
from django.conf import settings
from django.contrib.admin.views.main import ChangeList
from django.contrib.admin.templatetags.admin_list import PAGE_VAR
from django.templatetags.static import static
from django.utils.html import format_html
from django.utils.safestring import SafeText, mark_safe

from core.services.admin_themes import DEFAULT_THEME_KEY, get_theme_ui_tweaks

# Import the upstream Jazzmin tag library and re-export everything,
# then override the tags that need patching.
from jazzmin.templatetags import jazzmin as upstream_jazzmin
from jazzmin.settings import (
    DEFAULT_UI_TWEAKS as _JAZZMIN_DEFAULT_UI_TWEAKS,
    DARK_THEMES as _JAZZMIN_DARK_THEMES,
    THEMES as _JAZZMIN_THEMES,
)

logger = logging.getLogger(__name__)

register = template.Library()

# Re-export upstream tags/filters so Jazzmin templates keep working.
register.tags.update(upstream_jazzmin.register.tags)
register.filters.update(upstream_jazzmin.register.filters)


# ── Override: dynamic per-user theme ─────────────────────────────────────

def _resolve_user_theme_key(context) -> str | None:
    """Return the logged-in user's chosen theme key, or None."""
    request = context.get("request")
    if request and getattr(request, "user", None) and request.user.is_authenticated:
        try:
            return request.user.admin_preference.theme
        except Exception:
            pass
    return None


@register.simple_tag(takes_context=True)
def get_jazzmin_ui_tweaks(context):
    """Return fully-processed Jazzmin UI tweaks for the current user's theme.

    Thread-safe: we replicate Jazzmin's ``get_ui_tweaks()`` processing locally
    rather than temporarily mutating ``settings.JAZZMIN_UI_TWEAKS``.

    Falls back to the static ``JAZZMIN_UI_TWEAKS`` setting when:
    - no request in context (e.g. management commands)
    - user is anonymous (login page)
    - user has no AdminPreference row yet
    - the AdminPreference table doesn't exist (pre-migration)
    """
    theme_key = _resolve_user_theme_key(context)
    if theme_key is None:
        # No user preference — fall back to static settings
        raw_tweaks = getattr(settings, "JAZZMIN_UI_TWEAKS", {})
    else:
        raw_tweaks = get_theme_ui_tweaks(theme_key)

    return _process_ui_tweaks(raw_tweaks)


def _process_ui_tweaks(raw_overrides: dict) -> dict:
    """Replicate Jazzmin's get_ui_tweaks() logic without reading settings.

    This is a thread-safe version: it takes an explicit tweaks dict rather
    than reading from ``settings.JAZZMIN_UI_TWEAKS``.
    """
    raw_tweaks = copy.deepcopy(_JAZZMIN_DEFAULT_UI_TWEAKS)
    raw_tweaks.update(raw_overrides)
    tweaks = {x: y for x, y in raw_tweaks.items() if y not in (None, "", False)}

    if tweaks.get("layout_boxed"):
        tweaks.pop("navbar_fixed", None)
        tweaks.pop("footer_fixed", None)

    bool_map = {
        "navbar_small_text": "text-sm",
        "footer_small_text": "text-sm",
        "body_small_text": "text-sm",
        "brand_small_text": "text-sm",
        "sidebar_nav_small_text": "text-sm",
        "no_navbar_border": "border-bottom-0",
        "sidebar_disable_expand": "sidebar-no-expand",
        "sidebar_nav_child_indent": "nav-child-indent",
        "sidebar_nav_compact_style": "nav-compact",
        "sidebar_nav_legacy_style": "nav-legacy",
        "sidebar_nav_flat_style": "nav-flat",
        "layout_boxed": "layout-boxed",
        "sidebar_fixed": "layout-fixed",
        "navbar_fixed": "layout-navbar-fixed",
        "footer_fixed": "layout-footer-fixed",
        "actions_sticky_top": "sticky-top",
    }

    for key, value in bool_map.items():
        if key in tweaks:
            tweaks[key] = value

    def classes(*args: str) -> str:
        return " ".join([tweaks.get(arg, "") for arg in args]).strip()

    theme = tweaks.get("theme", "default")
    if theme not in _JAZZMIN_THEMES:
        logger.warning("%s not found in themes, using default", theme)
        theme = "default"

    dark_mode_theme = tweaks.get("dark_mode_theme", None)
    if dark_mode_theme and dark_mode_theme not in _JAZZMIN_DARK_THEMES:
        logger.warning("%s is not a dark theme, using darkly", dark_mode_theme)
        dark_mode_theme = "darkly"

    theme_body_classes = " theme-{}".format(theme)
    if theme in _JAZZMIN_DARK_THEMES:
        theme_body_classes += " dark-mode"

    ret = {
        "raw": raw_tweaks,
        "theme": {"name": theme, "src": static(_JAZZMIN_THEMES[theme])},
        "sidebar_classes": classes("sidebar", "sidebar_disable_expand"),
        "navbar_classes": classes("navbar", "no_navbar_border", "navbar_small_text"),
        "body_classes": classes(
            "accent", "body_small_text", "navbar_fixed", "footer_fixed", "sidebar_fixed", "layout_boxed"
        )
        + theme_body_classes,
        "actions_classes": classes("actions_sticky_top"),
        "sidebar_list_classes": classes(
            "sidebar_nav_small_text",
            "sidebar_nav_flat_style",
            "sidebar_nav_legacy_style",
            "sidebar_nav_child_indent",
            "sidebar_nav_compact_style",
        ),
        "brand_classes": classes("brand_small_text", "brand_colour"),
        "footer_classes": classes("footer_small_text"),
        "button_classes": tweaks.get("button_classes", {}),
    }

    if dark_mode_theme:
        ret["dark_mode_theme"] = {
            "name": dark_mode_theme,
            "src": static(_JAZZMIN_THEMES[dark_mode_theme]),
        }

    return ret


@register.simple_tag
def jazzmin_paginator_number(change_list: ChangeList, i: int) -> SafeText:
    """Generate an individual page index link in a paginated list.

    Jazzmin's upstream implementation returns `format_html(html_str)` with no
    args/kwargs, which raises `TypeError: args or kwargs must be provided` on
    Django 6+.

    We keep Jazzmin's HTML output but wrap it safely.
    """

    html_str = ""
    start = i == 1
    end = i == change_list.paginator.num_pages
    spacer = i in (".", "…")
    current_page = i == change_list.page_num

    if start:
        link = change_list.get_query_string({PAGE_VAR: change_list.page_num - 1}) if change_list.page_num > 1 else "#"
        html_str += """
        <li class=\"page-item previous {disabled}\">
            <a class=\"page-link\" href=\"{link}\" data-dt-idx=\"0\" tabindex=\"0\">«</a>
        </li>
        """.format(link=link, disabled="disabled" if link == "#" else "")

    if current_page:
        html_str += """
        <li class=\"page-item active\">
            <a class=\"page-link\" href=\"javascript:void(0);\" data-dt-idx=\"3\" tabindex=\"0\">{num}</a>
        </li>
        """.format(num=i)
    elif spacer:
        html_str += """
        <li class=\"page-item\">
            <a class=\"page-link\" href=\"javascript:void(0);\" data-dt-idx=\"3\" tabindex=\"0\">… </a>
        </li>
        """
    else:
        query_string = change_list.get_query_string({PAGE_VAR: i})
        end_class = "end" if end else ""
        html_str += """
            <li class=\"page-item\">
            <a href=\"{query_string}\" class=\"page-link {end}\" data-dt-idx=\"3\" tabindex=\"0\">{num}</a>
            </li>
        """.format(num=i, query_string=query_string, end=end_class)

    if end:
        link = change_list.get_query_string({PAGE_VAR: change_list.page_num + 1}) if change_list.page_num < i else "#"
        html_str += """
        <li class=\"page-item next {disabled}\">
            <a class=\"page-link\" href=\"{link}\" data-dt-idx=\"7\" tabindex=\"0\">»</a>
        </li>
        """.format(link=link, disabled="disabled" if link == "#" else "")

    # Use format_html with an argument to satisfy Django's requirement.
    return format_html("{}", mark_safe(html_str))
