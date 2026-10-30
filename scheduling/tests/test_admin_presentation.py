from html.parser import HTMLParser

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from scheduling.tests.test_experience_demo import demo, local, browser, config
from scheduling.tests.test_inventory import stock, reserve, confirm, url

pytestmark = pytest.mark.django_db


class PrepSections(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.details = []
        self.participants = []
        self.materials = []
        self.feed(content.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "details":
            self.details.append(attrs)
        if attrs.get("id") == "participants-title":
            self.participants.append(list(self.details))
        if "materials-table" in attrs.get("class", "").split():
            self.materials.append(list(self.details))

    def handle_endtag(self, tag):
        if tag == "details":
            self.details.pop()


def test_prep_and_details_share_workspace_but_materials_stay_visible(stock):
    item = reserve(stock, 2)
    confirm(stock, item)
    prep = stock.client.get(url(stock, "reservation_preparation", public_id=item.public_id))
    parsed = PrepSections(prep.content)
    assert parsed.participants == [[]]
    assert b'data-reservation-panel="details" hidden' in prep.content
    assert parsed.materials == [[]]
    assert b"Design" in prep.content and b"Color" in prep.content
    detail = stock.client.get(url(stock, "reservation_detail", public_id=item.public_id))
    assert PrepSections(detail.content).participants == [[]]


@pytest.mark.parametrize("route", ["workspace", "reservations", "workshops", "settings", "locations", "access"])
def test_plushumi_demo_brand_and_static_banners_on_all_admin_sections(demo, browser, route):
    browser.force_login(get_user_model().objects.get(username="plushie_admin"))
    flags_before = dict(demo.school.feature_flags)
    intake_before = demo.native_reservations_enabled
    response = browser.get(reverse(f"scheduling:{route}", kwargs={"school_slug": demo.school.slug}))
    assert response.status_code == 200
    assert response.context["branded_admin_demo"] is True
    assert b'alt="The Plushie Workshop"' in response.content
    assert b'assets/logo.png' in response.content
    assert b'class="dash-school-name"' not in response.content
    assert b"Built specifically for The Plushie Workshop &middot; Sample data loaded" in response.content
    assert b'Trial ends in <strong>7 days</strong>.' in response.content
    assert b'<label class="sr-only" for="booking-location">Location</label>' in response.content
    assert b"Upgrade now" not in response.content
    demo.school.refresh_from_db()
    demo.refresh_from_db()
    assert demo.school.feature_flags == flags_before
    assert demo.native_reservations_enabled == intake_before


@pytest.mark.parametrize("boundary", ["other_business", "not_demo", "not_local", "flag_off"])
def test_demo_banner_and_logo_do_not_leak_to_other_workspaces(demo, browser, monkeypatch, boundary):
    if boundary == "other_business":
        demo.school.slug = "another-workshop"
    elif boundary == "not_demo":
        demo.school.is_demo = False
    elif boundary == "not_local":
        monkeypatch.setattr(config, "local_database", lambda: False)
    else:
        demo.school.feature_flags["booking_experience_demo"] = False
    demo.school.display_name = "Business without demo branding"
    demo.school.save()
    browser.force_login(get_user_model().objects.get(username="plushie_admin"))
    response = browser.get(reverse("scheduling:workspace", kwargs={"school_slug": demo.school.slug}))
    assert response.status_code == 200
    assert not response.context["branded_admin_demo"]
    assert b'class="dash-school-name">Business without demo branding</div>' in response.content
    assert b"booking-demo-banner" not in response.content
    assert b"booking-demo-trial" not in response.content
    assert b"assets/logo.png" not in response.content


def test_grid_keeps_full_long_values_and_column_structure(stock):
    item = reserve(stock)
    item.snapshot["name"] = "A-long-customer-name-without-spaces-" * 4 + "<script>"
    item.snapshot["workshop"] = "A long workshop title with many choices and extended hours"
    item.save()
    response = stock.client.get(url(stock, "reservations"))
    content = response.content.decode()
    assert item.snapshot["name"].replace("<script>", "&lt;script&gt;") in content
    assert item.snapshot["workshop"] in content
    assert content.count('<col class="reservation-') == 8
    assert content.count('scope="col"') == 8
    assert 'class="reservation-cell-primary"' in content
    assert 'class="reservation-cell-secondary"' in content
    assert 'tabindex="0"' in content
    assert 'aria-label="View ' in content
    assert '<div class="dash-card reservation-table">' in content
    assert '<div class="dash-table-scroll" role="region" aria-label="Reservations" tabindex="0">' in content
    assert 'class="reservation-sort-link"' in content
    assert 'class="reservation-sort-icon" aria-hidden="true"' in content
