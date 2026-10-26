from types import SimpleNamespace
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest
from django.urls import reverse

from scheduling.capabilities import CAPABILITIES
from scheduling.navigation import workspace_navigation


class NavigationParser(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.groups = {}
        self.current = None
        self.link = None
        self.stylesheets = []
        self.feed(content.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.stylesheets.append(urlsplit(attrs["href"]).path)
        if tag == "nav":
            self.current = attrs.get("aria-label")
            self.groups[self.current] = []
        elif tag == "a" and self.current:
            self.link = {**attrs, "text": ""}
            self.groups[self.current].append(self.link)

    def handle_data(self, data):
        if self.link is not None:
            self.link["text"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self.link = None
        elif tag == "nav":
            self.current = None


def context(active="overview", enabled=(), owner=True, experience=False, lessons=False, messages=False):
    return {"business": SimpleNamespace(slug="navigation-test"),
            "profile": SimpleNamespace(lessons_enabled=lessons),
            "features": {key: key in enabled for key in CAPABILITIES},
            "active_nav": active, "can_view_access": owner, "can_edit_setup": owner,
            "experience_demo_available": experience, "message_preview_available": messages}


def test_plushumi_navigation_has_exact_groups_and_no_request_menu():
    result = workspace_navigation(context("settings", ("workshops", "parties", "camps", "reservations", "integrations"), experience=True))
    assert [(item["label"], [tab["label"] for tab in item["tabs"]]) for item in result["sections"]] == [
        ("Dashboard", []), ("Reservations", ["Reservations", "Studio Prep", "Party inquiries"]),
        ("Setup", ["Workshop", "Parties", "Camps", "Catalog", "Materials Inventory"]),
        ("Settings", ["Location & rooms", "Guest communication", "Team Access", "Integrations"])]
    assert result["section"]["label"] == "Setup"
    assert [tab["label"] for tab in result["section"]["tabs"] if tab["active"]] == ["Materials Inventory"]
    assert result["sections"][3]["url"] == "/bookings/navigation-test/locations/"
    assert all("requests" not in item["url"] for section in result["sections"] for item in section["tabs"])


@pytest.mark.parametrize("active,section", [
    ("overview", "Dashboard"), ("reservations", "Reservations"), ("preparation", "Reservations"),
    ("requests", "Reservations"), ("workshops", "Setup"), ("parties", "Setup"), ("camps", "Setup"),
    ("settings", "Setup"), ("locations", "Settings"), ("access", "Settings"), ("integrations", "Settings"),
    ("guest_communication", "Settings"), ("studio_preparation", "Reservations"), ("party_inquiries", "Reservations"),
    ("visits", "Reservations"), ("lessons", "Reservations"), ("messages", "Reservations"), ("unknown", "Dashboard"),
])
def test_every_page_selects_its_parent_section(active, section):
    result = workspace_navigation(context(active, CAPABILITIES, experience=True, lessons=True, messages=True))
    assert result["section"]["label"] == section


@pytest.mark.parametrize("owner", [True, False])
@pytest.mark.parametrize("enabled", [(), ("workshops",), ("lessons",), ("visits",), ("integrations",)])
@pytest.mark.parametrize("lessons,messages", [(False, False), (True, False), (True, True)])
def test_optional_tools_and_permission_visibility(owner, enabled, lessons, messages):
    result = workspace_navigation(context("requests", enabled, owner, lessons=lessons, messages=messages))
    tabs = {tab["label"] for section in result["sections"] for tab in section["tabs"]}
    assert ("Team Access" in tabs) == owner
    assert ("Integrations" in tabs) == (owner and "integrations" in enabled)
    assert ("Lessons" in tabs) == (lessons and "lessons" in enabled)
    assert ("Tours" in tabs) == ("visits" in enabled)
    assert ("Messages" in tabs) == (messages and bool({"lessons", "visits"}.intersection(enabled)))
    assert "Workshop Prep" not in tabs and "Studio Prep" not in tabs
    assert "Reservations" not in tabs
    if "workshops" in enabled:
        assert result["section"]["label"] == "Setup"


@pytest.mark.django_db
@pytest.mark.parametrize("route,section,tab", [
    ("workspace", "Dashboard", None), ("reservations", "Reservations", "Reservations"),
    ("studio_preparation", "Reservations", "Studio Prep"),
    ("workshops", "Setup", "Workshop"), ("parties", "Reservations", "Party inquiries"),
    ("party_setup", "Setup", "Parties"), ("camps", "Setup", "Camps"),
    ("settings", "Setup", "Materials Inventory"), ("catalogs", "Setup", "Catalog"), ("locations", "Settings", "Location & rooms"),
    ("access", "Settings", "Team Access"), ("integrations", "Settings", "Integrations"),
])
def test_rendered_navigation_and_existing_links(client, booking_member, route, section, tab):
    client.force_login(booking_member.user)
    response = client.get(reverse("scheduling:" + route, kwargs={"school_slug": booking_member.profile.school.slug}))
    assert response.status_code == 200
    parsed = NavigationParser(response.content)
    groups = parsed.groups
    nav = groups["Bookings"]
    assert [link["text"] for link in nav] == ["Dashboard", "Reservations", "Setup", "Settings"]
    assert [link["text"] for link in nav if link.get("aria-current") == "page"] == [section]
    assert b'class="dash-school-name">Workshop Test</div>' in response.content
    if route == "workspace":
        assert b'class="dash-greeting-title"' in response.content
        assert b'Recent Activity' in response.content
    else:
        assert f"<h1>{section}</h1>".encode() in response.content
    assert b'id="dashboard-location-filter"' in response.content
    assert "/static/admin/dashboard.css" in parsed.stylesheets
    assert b'aria-controls="booking-nav" aria-expanded="false"' in response.content
    if tab:
        subnav = groups[section + " sections"]
        assert [link["text"] for link in subnav if link.get("aria-current") == "page"] == [tab]
        for link in subnav:
            assert client.get(link["href"]).status_code == 200
    else:
        assert not any(label and label.endswith(" sections") for label in groups)


@pytest.mark.django_db
def test_navigation_build_has_no_database_queries(booking_profile, django_assert_num_queries):
    data = context("locations", CAPABILITIES, lessons=True)
    with django_assert_num_queries(0):
        workspace_navigation(data)
