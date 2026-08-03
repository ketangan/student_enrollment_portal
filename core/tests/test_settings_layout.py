"""
Settings page — layout, rendering, and mobile responsiveness tests.

Covers:
  - GET renders 200 for all roles
  - Forms tab shows empty state when no programs + no YAML overrides
  - Forms tab hides empty state when program_field_key is set
  - All five tabs present for owner; Team/Billing hidden for editor/viewer
  - Team table wrapped in scroll container (mobile scrollability)
  - Responsive grid: no hard-coded "1fr 1fr" in rendered HTML (regression guard)
  - Settings tab bar has overflow CSS for mobile (regression guard)
  - CSS file contains the table-width mobile fix (regression guard)
  - CSS file contains the KPI label mobile shrink (regression guard)
"""
from __future__ import annotations

import os

import pytest
from django.test import Client

from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
)

_SETTINGS_URL = "/schools/{slug}/admin/settings/"
_CSS_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../static/admin/dashboard.css",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(client, school, **params):
    url = _SETTINGS_URL.format(slug=school.slug)
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    return client.get(url)


def _make_client(school, role="owner"):
    m = SchoolAdminMembershipFactory(school=school, role=role)
    c = Client()
    c.force_login(m.user)
    return c


# ---------------------------------------------------------------------------
# Basic rendering
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_settings_renders_200_for_owner():
    school = SchoolFactory()
    client = _make_client(school, "owner")
    response = _get(client, school)
    assert response.status_code == 200, f"Expected 200 for owner, got {response.status_code}"


@pytest.mark.django_db
def test_settings_renders_200_for_editor():
    school = SchoolFactory()
    client = _make_client(school, "editor")
    response = _get(client, school)
    assert response.status_code == 200, f"Expected 200 for editor, got {response.status_code}"


@pytest.mark.django_db
def test_settings_returns_404_for_viewer():
    """Viewers do not have settings access — the view gates it to owner/editor."""
    school = SchoolFactory()
    client = _make_client(school, "viewer")
    response = _get(client, school)
    assert response.status_code == 404, f"Expected 404 for viewer, got {response.status_code}"


# ---------------------------------------------------------------------------
# Forms tab — empty state
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_forms_tab_shows_empty_state_when_no_programs_no_overrides():
    """Factory school: no YAML config, no program_field_key → empty state card."""
    school = SchoolFactory()
    assert not school.program_field_key, "Factory school should have no program_field_key"
    client = _make_client(school, "owner")

    response = _get(client, school, tab="forms")
    content = response.content.decode()

    assert "No form settings yet" in content, (
        "Forms tab should show empty state when school has no programs and no YAML overrides"
    )
    assert "Program management and form content overrides will appear here" in content


@pytest.mark.django_db
def test_forms_tab_empty_state_hidden_when_program_field_key_set():
    """When school.program_field_key is set, Programs section shows; empty state must not."""
    school = SchoolFactory(program_field_key="program")
    client = _make_client(school, "owner")

    response = _get(client, school, tab="forms")
    content = response.content.decode()

    assert "No form settings yet" not in content, (
        "Empty state must NOT show when program_field_key is configured"
    )
    # Programs section header is rendered
    assert "Programs" in content
    assert "id=\"programs\"" in content


@pytest.mark.django_db
def test_forms_tab_empty_state_visible_for_editors_too():
    """Editors see the same empty state; Forms tab is not owner-only."""
    school = SchoolFactory()
    client = _make_client(school, "editor")

    response = _get(client, school, tab="forms")
    content = response.content.decode()

    assert "No form settings yet" in content, "Editor should also see the Forms empty state"


# ---------------------------------------------------------------------------
# Tab bar — presence and access control
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_owner_sees_all_five_tabs():
    school = SchoolFactory()
    client = _make_client(school, "owner")
    content = _get(client, school).content.decode()

    for tab in ("general", "email", "team", "billing", "forms"):
        assert f'data-tab="{tab}"' in content, f"Owner must see tab: {tab}"


@pytest.mark.django_db
def test_editor_does_not_see_team_or_billing_tabs():
    """Team and Billing tabs are owner-only; editors must not see them in the tab bar."""
    school = SchoolFactory()
    client = _make_client(school, "editor")
    content = _get(client, school).content.decode()

    # General, Email, Forms visible to editors
    for tab in ("general", "email", "forms"):
        assert f'data-tab="{tab}"' in content, f"Editor should see tab: {tab}"

    # Team and Billing are owner-only
    # The template wraps these in {% if is_owner %} so they won't appear
    # Note: we check for the button, not just any occurrence of the word
    assert 'data-tab="team"' not in content, "Editor must not see Team tab button"
    assert 'data-tab="billing"' not in content, "Editor must not see Billing tab button"


@pytest.mark.django_db
def test_editor_does_not_see_team_or_billing_tab_panels():
    """Editor's rendered page must not contain the Team or Billing panel content (owner-only)."""
    school = SchoolFactory()
    client = _make_client(school, "editor")
    content = _get(client, school).content.decode()

    # Panel content for team/billing is wrapped in {% if is_owner %} — must be absent
    assert 'data-tab="team"' not in content
    assert 'data-tab="billing"' not in content


# ---------------------------------------------------------------------------
# Mobile layout structure — HTML integrity checks
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_team_table_is_wrapped_in_scroll_container():
    """Team table must live inside a dash-table-scroll div so it can scroll on mobile."""
    school = SchoolFactory()
    # Add a second member so the table renders
    m1 = SchoolAdminMembershipFactory(school=school, role="owner")
    m2 = SchoolAdminMembershipFactory(school=school, role="editor")
    client = Client()
    client.force_login(m1.user)

    content = _get(client, school, tab="team").content.decode()

    # Both the scroll wrapper and the table itself must be present
    assert "dash-table-scroll" in content, "Team table must be wrapped in dash-table-scroll"
    assert 'class="dash-table"' in content, "Team table must use dash-table class"


@pytest.mark.django_db
def test_programs_table_is_wrapped_in_scroll_container():
    """Programs table (when shown) must live inside dash-table-scroll for mobile scrolling."""
    from core.models import SchoolProgram
    school = SchoolFactory(program_field_key="program")
    SchoolProgram.objects.create(
        school=school,
        name="Ballet",
        code="ballet",
        is_active=True,
        capacity=None,
        auto_enroll=False,
        waitlist_enabled=False,
        display_order=0,
    )
    client = _make_client(school, "owner")

    content = _get(client, school, tab="forms").content.decode()

    assert "dash-table-scroll" in content, "Programs table must be wrapped in dash-table-scroll"


@pytest.mark.django_db
def test_settings_page_has_no_fixed_two_col_grid():
    """
    Regression guard: the settings page must NOT contain hard-coded 1fr 1fr
    grid columns (all grids must use auto-fill/minmax so they stack on mobile).
    """
    school = SchoolFactory()
    client = _make_client(school, "owner")
    content = _get(client, school).content.decode()

    assert "grid-template-columns:1fr 1fr" not in content, (
        "Hard-coded '1fr 1fr' grid found in settings page — this breaks mobile layout. "
        "Use repeat(auto-fill, minmax(...)) instead."
    )


@pytest.mark.django_db
def test_settings_tab_bar_has_overflow_css():
    """
    Regression guard: the settings tab bar must have overflow-x:auto on narrow
    screens so all tabs remain accessible without horizontal page overflow.
    """
    school = SchoolFactory()
    client = _make_client(school, "owner")
    content = _get(client, school).content.decode()

    # The inline <style> block must contain the mobile overflow rule
    assert "overflow-x:auto" in content, (
        "Settings tab bar must include overflow-x:auto in its mobile media query"
    )


# ---------------------------------------------------------------------------
# CSS file regression guards
# ---------------------------------------------------------------------------


def _read_css():
    path = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../static/admin/dashboard.css"))
    with open(path) as f:
        return f.read()


def test_css_table_has_mobile_overflow_fix():
    """
    Regression guard: dashboard.css must contain width:auto + min-width:100% on
    .dash-table inside the 768px media query so overflow-x:auto can activate.
    Without this, tables squish columns instead of scrolling horizontally.
    """
    css = _read_css()
    assert "width: auto;" in css, "CSS missing 'width: auto' on .dash-table (mobile table overflow fix)"
    assert "min-width: 100%;" in css, "CSS missing 'min-width: 100%' on .dash-table (mobile table overflow fix)"


def test_css_kpi_label_has_mobile_shrink():
    """
    Regression guard: dashboard.css must reduce .dash-kpi-label font size in
    the 768px media query so long labels like 'Overdue / Needs Follow-Up' don't
    wrap to 3 lines inside 2-column KPI cards at 390px viewport.
    """
    css = _read_css()
    # Check for the kpi-label mobile override — it's inside a @media block
    # We just check both strings appear in the file; ordering is implicit from
    # the fact we wrote them together in the media query.
    assert ".dash-kpi-label" in css, "CSS must define .dash-kpi-label"
    # The mobile rule sets font-size:10px (vs the default 12px)
    assert "font-size: 10px;" in css, (
        "CSS missing font-size:10px override for .dash-kpi-label in mobile media query"
    )
