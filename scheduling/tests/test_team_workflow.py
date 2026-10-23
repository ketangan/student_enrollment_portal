import pytest
from django.contrib.auth import get_user_model
from core.models import SchoolAdminMembership
from scheduling.models import SchedulingMembership
from scheduling.team_access import membership_form
from .test_location_access import two_locations, manager
from .test_reservations import inventory, path

pytestmark = pytest.mark.django_db


def test_team_list_queries_do_not_grow_per_member(client, two_locations, booking_member, manager):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    client.force_login(manager)
    url = path(booking_member, "access")
    client.get(url)
    with CaptureQueriesContext(connection) as baseline:
        assert client.get(url).status_code == 200
    accounts = get_user_model().objects.bulk_create([
        get_user_model()(username=f"team-scale-{i}") for i in range(12)])
    SchedulingMembership.objects.bulk_create([
        SchedulingMembership(profile=booking_member.profile, user=account, created_by=manager)
        for account in accounts])
    with CaptureQueriesContext(connection) as expanded:
        assert client.get(url).status_code == 200
    assert len(expanded) <= len(baseline) + 1


def post_member(client, url, member, action, **values):
    member.refresh_from_db()
    data = {"member": member.pk, "team_action": action,
            "edit_revision": membership_form(member).fields["edit_revision"].initial}
    data.update(values)
    return client.post(url, data)


def test_shared_team_workflow(client, two_locations, booking_member, manager):
    client.force_login(manager)
    url = path(booking_member, "access")
    page = client.get(url)
    assert "school_admin/team_panel.html" in [t.name for t in page.templates]
    assert b"Create &amp; Add" in page.content
    assert b"<dialog" not in page.content
    location = two_locations[0][1].location
    response = client.post(url, {"team_action": "add", "username": "new-booking-member",
        "password": "local-test-password", "first_name": "New", "last_name": "Member",
        "email": "school@example.com",
        "role": "scheduler", "location_access": "selected", "locations": [location.pk]})
    assert response.status_code == 302
    account = get_user_model().objects.get(username="new-booking-member")
    assert account.check_password("local-test-password")
    assert account.email == "school@example.com"
    assert not account.is_staff and not account.is_superuser
    assert not SchoolAdminMembership.objects.filter(user=account).exists()
    member = SchedulingMembership.objects.get(user=account)
    assert member.created_by == manager
    assert list(member.locations.all()) == [location]
    from django.test import Client
    signed_in = Client()
    assert signed_in.post("/login/", {"username": account.username, "password": "local-test-password"}).status_code == 302
    assert signed_in.get(path(booking_member, "workspace")).status_code == 200
    assert signed_in.get(url).status_code == 404
    assert post_member(client, url, member, "name", first_name="Updated", last_name="Name").status_code == 302
    account.refresh_from_db()
    assert account.get_full_name() == "Updated Name"
    assert post_member(client, url, member, "role", role="viewer").status_code == 302
    member.refresh_from_db()
    assert member.role == "viewer" and not member.all_locations
    assert post_member(client, url, member, "locations", location_access="all").status_code == 302
    member.refresh_from_db()
    assert member.all_locations and not member.locations.exists()
    assert post_member(client, url, member, "remove").status_code == 302
    member.refresh_from_db()
    account.refresh_from_db()
    assert not member.is_active and account.is_active
    assert signed_in.get(path(booking_member, "workspace")).status_code == 404


def test_self_demotion_with_another_owner_returns_to_dashboard(client, two_locations, booking_member, manager):
    client.force_login(manager)
    booking_member.all_locations = True
    booking_member.role = "owner"
    booking_member.save()
    owner = SchedulingMembership.objects.get(user=manager)
    response = post_member(client, path(booking_member, "access"), owner, "role", role="viewer")
    assert response.status_code == 302 and response.url == path(booking_member, "workspace")
    assert client.get(response.url).status_code == 200
    assert client.get(path(booking_member, "access")).status_code == 404


@pytest.mark.parametrize("changes", [
    {"username": ""}, {"username": "invalid user"}, {"password": "short"},
    {"role": "editor"}, {"location_access": "selected"}, {"locations": [999999], "location_access": "selected"},
])
def test_invalid_creation_is_atomic(client, two_locations, booking_member, manager, changes):
    client.force_login(manager)
    data = {"team_action": "add", "username": "new-team-user", "password": "local-password",
            "role": "viewer", "location_access": "all", "email": "school@example.com"}
    data.update(changes)
    count = get_user_model().objects.count()
    response = client.post(path(booking_member, "access"), data)
    assert response.status_code == 400
    assert get_user_model().objects.count() == count
    assert b"local-password" not in response.content


def test_last_owner_self_removal_and_stale_role(client, two_locations, booking_member, manager):
    client.force_login(manager)
    url = path(booking_member, "access")
    owner = SchedulingMembership.objects.get(user=manager)
    assert post_member(client, url, owner, "role", role="viewer").status_code == 400
    assert post_member(client, url, owner, "locations", location_access="selected",
                       locations=[two_locations[0][1].location_id]).status_code == 400
    assert post_member(client, url, owner, "remove").status_code == 400
    stale = membership_form(booking_member).fields["edit_revision"].initial
    assert post_member(client, url, booking_member, "role", role="viewer").status_code == 302
    assert client.post(url, {"team_action": "role", "member": booking_member.pk,
        "role": "owner", "edit_revision": stale}).status_code == 400


def test_duplicate_username_and_shared_enrollment_panel(client, two_locations, booking_member, manager):
    client.force_login(manager)
    assert client.post(path(booking_member, "access"), {"team_action": "add",
        "username": manager.username.upper(), "password": "local-password",
        "role": "viewer", "location_access": "all", "email": "school@example.com"}).status_code == 400
    from django.template.loader import render_to_string
    school = booking_member.profile.school
    member = SchoolAdminMembership.objects.create(school=school, user=manager, role="owner")
    from django.test import RequestFactory
    request = RequestFactory().get("/")
    request.user = manager
    html = render_to_string("school_admin/team_panel.html",
        {"school": school, "members": [member], "is_owner": True}, request=request)
    assert "Create &amp; Add" in html and 'value="editor"' in html
    assert "location_access" not in html and "team_action" not in html
