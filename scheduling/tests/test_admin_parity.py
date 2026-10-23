from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from core.models import AdminAuditLog, School
from scheduling.dashboard_activity import EVENTS, recent_activity
from scheduling.models import Location, SchedulingProfile, SchedulingMembership
from scheduling.reservation_queries import staff_reservations
from scheduling.templatetags.booking_navigation import booking_initials
from scheduling.workspace import overview
from scheduling.tests.test_dashboard_locations import second_studio, url
from scheduling.tests.test_reservations import inventory, workshop_request, reserve, settle
from scheduling.tests.test_camps import camp
from scheduling.tests.test_inventory import stock, reserve as stock_reserve, confirm as stock_confirm
from scheduling.tests.test_experience_demo import demo, local, browser, config, service

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("route", ["workspace", "reservations", "workshops", "parties", "camps", "settings", "locations", "access", "integrations"])
def test_location_is_shared_between_admin_pages(client, inventory, second_studio, booking_member, route):
    profile = inventory[0]
    selected = second_studio[1].location
    client.force_login(booking_member.user)
    client.get(url(profile), {"location": selected.pk})
    response = client.get(url(profile, route))
    assert response.status_code == 200
    assert response.context["selected_location"] == selected
    assert response.content.count(b'id="booking-location"') == 1
    assert b'class="dash-header has-location-filter"' in response.content
    if route == "workshops":
        assert list(response.context["offerings"]) == [second_studio[1]]
    if route == "locations":
        assert list(response.context["locations"]) == [selected]
    if route == "access":
        assert booking_member in response.context["memberships"]
        assert b'aria-label="Team"' in response.content
    if route == "integrations":
        assert b"Workspace-wide settings" in response.content
    client.get(url(profile, route), {"location": ""})
    assert client.get(url(profile)).context["selected_location"] is None


def test_stored_filter_invalid_selection_reset_and_business_isolation(client, inventory, second_studio, booking_member):
    profile = inventory[0]
    place = second_studio[1].location
    client.force_login(booking_member.user)
    client.get(url(profile), {"location": place.pk})
    assert client.get(url(profile), {"location": "bad"}).status_code == 400
    assert client.get(url(profile)).context["selected_location"] == place
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="second-business"), enabled=True)
    SchedulingMembership.objects.create(profile=other, user=booking_member.user, role="owner")
    assert client.get(url(other)).context["selected_location"] is None
    state = client.session
    state[f"booking_location:{profile.pk}"] = "99999999"
    state.save()
    response = client.get(url(profile))
    assert response.status_code == 400 and b"Choose a valid location" in response.content
    assert not response.context.get("agenda")
    assert client.get(url(profile), {"location": ""}).status_code == 200


def test_reservation_search_sort_clear_and_detail_location_switch(client, inventory, second_studio, booking_member):
    first = reserve(booking_member, workshop_request(inventory))
    second = reserve(booking_member, workshop_request(second_studio))
    client.force_login(booking_member.user)
    page = client.get(url(inventory[0], "reservations"), {"location": second_studio[1].location_id, "q": "Second", "sort": "scheduled"})
    assert list(page.context["items"]) == [second]
    assert b'aria-sort="ascending"' in page.content
    assert b'class="dash-student-cell"' in page.content
    assert b'class="dash-action-btn dash-action-btn--secondary"' in page.content
    assert ("q", "Second") in page.context["location_query_fields"]
    query = page.context["sort_urls"]["scheduled"]
    descending = client.get(url(inventory[0], "reservations") + query)
    assert descending.context["sort"] == "-scheduled"
    cleared = client.get(url(inventory[0], "reservations"))
    assert list(cleared.context["items"]) == [second]
    detail = client.get(reverse("scheduling:reservation_detail", kwargs={"school_slug": inventory[0].school.slug, "public_id": second.public_id}))
    assert detail.context["location_action"] == url(inventory[0], "reservations")
    all_page = client.get(url(inventory[0], "reservations"), {"location": "", "sort": "-name"})
    assert set(all_page.context["items"]) == {first, second}
    assert client.get(url(inventory[0], "reservations"), {"sort": "not-a-column"}).status_code == 200


def test_camps_and_stock_follow_header_location(client, camp, inventory, second_studio, booking_member):
    client.force_login(booking_member.user)
    assert client.get(url(inventory[0], "camps")).context["camps"].paginator.count == 1
    client.get(url(inventory[0]), {"location": second_studio[1].location_id})
    page = client.get(url(inventory[0], "camps"))
    assert not page.context["camps"]
    assert list(page.context["offerings"]) == [second_studio[1]]
    assert client.get(url(inventory[0], "settings")).context["selected_location"] == second_studio[1].location


def test_prep_filters_before_selection_and_rejects_foreign_session(demo, browser):
    browser.force_login(get_user_model().objects.get(username="plushie_admin"))
    places = list(demo.locations.order_by("pk"))
    selected = places[-1]
    response = browser.get(url(demo), {"location": selected.pk})
    prep_url = reverse("scheduling:experience_preparation", kwargs={"school_slug": demo.school.slug})
    page = browser.get(prep_url)
    assert page.status_code == 200
    assert page.context["selected"].offering.location_id == selected.pk
    assert {item.offering.location_id for item in page.context["sessions"]} == {selected.pk}
    foreign = service.sessions(demo).exclude(offering__location=selected).first()
    assert browser.get(prep_url, {"session": foreign.pk}).status_code == 404
    assert browser.get(prep_url, {"location": "invalid"}).status_code == 400
    empty = Location.objects.create(profile=demo, name="Empty")
    assert browser.get(prep_url, {"location": empty.pk}).context["selected"] is None


def test_dashboard_activity_is_bounded_sorted_scoped_and_escapes_names(client, inventory, second_studio, booking_member, django_assert_max_num_queries):
    first = reserve(booking_member, workshop_request(inventory))
    second = reserve(booking_member, workshop_request(second_studio))
    first.snapshot["name"] = "<script>not executable</script>"
    first.save()
    now = timezone.now()
    for index, action in enumerate(EVENTS):
        log = AdminAuditLog.objects.create(model_label="scheduling.reservation", object_id=str(first.pk), changes={"action": action})
        AdminAuditLog.objects.filter(pk=log.pk).update(created_at=now+timedelta(seconds=index))
    for value in (None, now.isoformat()):
        AdminAuditLog.objects.create(model_label="scheduling.reservation", object_id=str(first.pk), changes={"prepared_at": {"from": None, "to": value}})
    # Even spoofed audit metadata cannot pull another studio into the feed.
    AdminAuditLog.objects.create(model_label="scheduling.reservation", object_id=str(second.pk), changes={"action": "canceled"}, extra={"scheduling_profile_id": inventory[0].pk})
    AdminAuditLog.objects.create(model_label="core.submission", object_id=str(first.pk), changes={"action": "canceled"})
    data = overview(inventory[0], location=inventory[1].location)
    assert len(data["recent_activity"]) == 8
    assert {row["name"] for row in data["recent_activity"]} == {first.snapshot["name"]}
    assert [row["created_at"] for row in data["recent_activity"]] == sorted([row["created_at"] for row in data["recent_activity"]], reverse=True)
    with django_assert_max_num_queries(3):
        recent_activity(inventory[0], staff_reservations(inventory[0]))
    client.force_login(booking_member.user)
    page = client.get(url(inventory[0]), {"location": inventory[1].location_id})
    assert b"&lt;script&gt;not executable&lt;/script&gt;" in page.content
    assert b"<script>not executable</script>" not in page.content
    empty = Location.objects.create(profile=inventory[0], name="Empty")
    assert not overview(inventory[0], location=empty)["recent_activity"]


def test_prepare_card_and_destination_match_and_all_tab_clears_queue(client, inventory, booking_member):
    item = reserve(booking_member, workshop_request(inventory))
    settle(booking_member, item)
    client.force_login(booking_member.user)
    card = next(c for c in overview(inventory[0])["dashboard_cards"] if c["label"] == "Preparation Pending")
    assert card["count"] == 1
    page = client.get(card["url"])
    assert list(page.context["items"]) == [item]
    assert "preparation" not in page.context["tab_filters"]
    item.prepared_at = timezone.now()
    item.save()
    assert not client.get(card["url"]).context["items"]


def test_consumed_materials_are_not_counted_as_unprepared(stock):
    from scheduling.inventory import consume
    item = stock_reserve(stock)
    stock_confirm(stock, item)
    card = next(c for c in overview(stock.profile)["dashboard_cards"] if c["label"] == "Preparation Pending")
    assert card["count"] == 1
    consume(stock.user, stock.school.slug, item.public_id)
    assert next(c for c in overview(stock.profile)["dashboard_cards"] if c["label"] == "Preparation Pending")["count"] == 0
    assert not stock.client.get(card["url"]).context["items"]


def test_search_count_is_separate_from_studio_totals(client, inventory, booking_member):
    reserve(booking_member, workshop_request(inventory))
    client.force_login(booking_member.user)
    response = client.get(url(inventory[0], "reservations"), {"q": "no match"})
    assert response.context["total_count"] == 1
    assert response.context["items"].paginator.count == 0
    assert b"Clear filters" in response.content


@pytest.mark.parametrize("name,initials", [(None, ""), ("", ""), ("  Alex   Chen  ", "AC"), ("Plushumi", "P")])
def test_booking_initials(name, initials):
    assert booking_initials(name) == initials
