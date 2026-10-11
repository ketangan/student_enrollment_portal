from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import School, SchoolAdminMembership
from scheduling.models import SchedulingMembership, SchedulingProfile

pytestmark = pytest.mark.django_db


def workspace(profile):
    return reverse("scheduling:workspace", kwargs={"school_slug": profile.school.slug})


def access(profile):
    return reverse("scheduling:access", kwargs={"school_slug": profile.school.slug})


@pytest.mark.parametrize("page", ["index", "workspace", "access"])
def test_all_pages_require_login(client, booking_profile, page):
    path = reverse("scheduling:index") if page == "index" else globals()[page](booking_profile)
    response = client.get(path)
    assert response.status_code == 302
    assert response.url == reverse("login") + "?" + urlencode({"next": path}, safe="/")


def test_booking_only_login_has_a_real_destination(client, booking_member):
    response = client.post(reverse("login"), {
        "username": booking_member.user.username, "password": "test-bookings",
    }, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [(reverse("scheduling:index"), 302), (workspace(booking_member.profile), 302)]
    assert client.session["_auth_user_id"] == str(booking_member.user.pk)
    assert b"Workshop Test" in response.content
    assert b"Settings" in response.content
    settings_page = client.get(reverse("scheduling:locations", kwargs={"school_slug": booking_member.profile.school.slug}))
    assert b"Team Access" in settings_page.content
    assert not SchoolAdminMembership.objects.filter(user=booking_member.user).exists()


def test_existing_login_honors_bookings_next(client, booking_member):
    target = access(booking_member.profile)
    response = client.post(reverse("login"), {
        "username": booking_member.user.username, "password": "test-bookings", "next": target,
    }, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain == [(target, 302)]
    assert b"booking-owner" in response.content


@pytest.mark.parametrize("next_url", ["https://evil.example/", "//evil.example/", "javascript:alert(1)"])
def test_external_redirect_not_allowed(client, booking_member, next_url):
    response = client.post(reverse("login"), {
        "username": booking_member.user.username, "password": "test-bookings", "next": next_url,
    })
    assert response.url == reverse("scheduling:index")


@pytest.mark.parametrize("role", ["owner", "scheduler", "viewer"])
def test_roles_get_workspace_but_only_owner_gets_team(client, booking_member, role):
    booking_member.role = role
    booking_member.save()
    client.force_login(booking_member.user)
    response = client.get(workspace(booking_member.profile))
    assert response.status_code == 200
    response = client.get(reverse("scheduling:locations", kwargs={"school_slug": booking_member.profile.school.slug}))
    assert (b"Team Access" in response.content) is (role == "owner")
    assert b"no-store" in response["Cache-Control"].encode()
    assert client.get(access(booking_member.profile)).status_code == (200 if role == "owner" else 404)


@pytest.mark.parametrize("revocation", ["membership", "profile", "user", "delete_membership"])
def test_access_revocation_applies_to_existing_session(client, booking_member, revocation):
    client.force_login(booking_member.user)
    assert client.get(workspace(booking_member.profile)).status_code == 200
    if revocation == "membership":
        booking_member.is_active = False
        booking_member.save()
    elif revocation == "profile":
        booking_member.profile.enabled = False
        booking_member.profile.save()
    elif revocation == "user":
        booking_member.user.is_active = False
        booking_member.user.save()
    else:
        booking_member.delete()
    response = client.get(workspace(booking_member.profile))
    assert response.status_code == (302 if revocation == "user" else 404)


def test_multiple_businesses_show_chooser_without_other_tenants(client, booking_member):
    school = School.objects.create(slug="second-workshop", display_name="Second Workshop")
    second = SchedulingProfile.objects.create(school=school, enabled=True)
    SchedulingMembership.objects.create(profile=second, user=booking_member.user)
    hidden = SchedulingProfile.objects.create(
        school=School.objects.create(slug="private-business", display_name="Private Business"), enabled=True
    )
    client.force_login(booking_member.user)
    response = client.get(reverse("scheduling:index"))
    assert response.status_code == 200
    assert b"Second Workshop" in response.content and b"Workshop Test" in response.content
    assert b"Private Business" not in response.content
    for path in [workspace(hidden), access(hidden), "/bookings/no-such-business/"]:
        assert client.get(path).status_code == 404


def test_empty_workspace_list_does_not_leak_businesses(client, booking_user, booking_profile):
    client.force_login(booking_user)
    response = client.get(reverse("scheduling:index"))
    assert response.status_code == 200
    assert b"No Bookings access" in response.content
    assert b"Workshop Test" not in response.content


def test_trial_and_enrollment_billing_do_not_gate_bookings(client, booking_member):
    school = booking_member.profile.school
    school.trial_started_at = timezone.now() - timedelta(days=90)
    school.is_active = False
    school.save()
    client.force_login(booking_member.user)
    response = client.get(workspace(booking_member.profile))
    assert response.status_code == 200
    for forbidden in [b"Your trial", b"Upgrade", b"Submissions", b"Leads", b"Manage Billing"]:
        assert forbidden not in response.content
    school.refresh_from_db()
    assert school.is_trial_expired
    assert not school.is_active
    assert school.plan == "trial"


def test_booking_member_cannot_read_enrollment_even_if_staff(client, booking_member):
    booking_member.user.is_staff = True
    booking_member.user.save()
    client.force_login(booking_member.user)
    for name in ["school_dashboard", "school_submissions", "school_leads"]:
        response = client.get(reverse(name, kwargs={"school_slug": booking_member.profile.school.slug}))
        assert response.status_code == 404


@pytest.mark.parametrize("slug", ["south-bay-music", "kidworks-childrens-center"])
def test_dual_member_retains_enrollment_default(client, booking_member, slug):
    school = School.objects.create(slug=slug, display_name=slug, plan="custom")
    SchoolAdminMembership.objects.create(user=booking_member.user, school=school)
    booking_member.user.is_staff = True
    booking_member.user.save()
    response = client.post(reverse("login"), {
        "username": booking_member.user.username, "password": "test-bookings",
    })
    assert response.url == reverse("school_dashboard", kwargs={"school_slug": slug})
    assert client.get(response.url).status_code == 200
    assert client.get(workspace(booking_member.profile)).status_code == 200
    assert client.get(f"/bookings/{slug}/").status_code == 404
    assert not SchedulingProfile.objects.filter(school=school).exists()


def test_disabled_profile_does_not_grant_default_login_destination(client, booking_member):
    booking_member.profile.enabled = False
    booking_member.profile.save()
    response = client.post(reverse("login"), {
        "username": booking_member.user.username, "password": "test-bookings",
    })
    assert response.url == reverse("login")
    assert not client.session.get("_auth_user_id")


def test_superuser_keeps_ops_and_must_respect_module_disable(client, booking_profile):
    user = get_user_model().objects.create_superuser("operator", "operator@example.test", "secret")
    response = client.post(reverse("login"), {"username": "operator", "password": "secret"})
    assert response.url == reverse("ops_dashboard")
    assert client.get(access(booking_profile)).status_code == 200
    assert b"No team members assigned" in client.get(access(booking_profile)).content
    booking_profile.enabled = False
    booking_profile.save()
    assert client.get(workspace(booking_profile)).status_code == 404


def test_member_list_is_tenant_scoped_and_shows_inactive_access(client, booking_member):
    user = get_user_model().objects.create_user("disabled-staff", is_active=False)
    SchedulingMembership.objects.create(profile=booking_member.profile, user=user)
    other = SchedulingProfile.objects.create(school=School.objects.create(slug="private-team"), enabled=True)
    private_user = get_user_model().objects.create_user("private-team-owner")
    SchedulingMembership.objects.create(profile=other, user=private_user, role="owner")
    client.force_login(booking_member.user)
    response = client.get(access(booking_member.profile))
    assert b"booking-owner" in response.content
    assert b"disabled-staff" in response.content
    assert b"Inactive" in response.content
    assert b"private-team-owner" not in response.content


def test_role_downgrade_takes_effect_without_signing_out(client, booking_member):
    client.force_login(booking_member.user)
    assert client.get(access(booking_member.profile)).status_code == 200
    booking_member.role = "viewer"
    booking_member.save()
    assert client.get(access(booking_member.profile)).status_code == 404
    assert b"Team access" not in client.get(workspace(booking_member.profile)).content


def test_enrollment_login_does_not_query_bookings_tables(client, booking_user, booking_profile):
    SchoolAdminMembership.objects.create(user=booking_user, school=booking_profile.school)
    with CaptureQueriesContext(connection) as queries:
        response = client.post(reverse("login"), {
            "username": booking_user.username, "password": "test-bookings",
        })
    assert response.url == reverse("school_dashboard", kwargs={"school_slug": booking_profile.school.slug})
    assert not any("scheduling_" in query["sql"] for query in queries)


@pytest.mark.parametrize("page", ["index", "workspace", "access"])
def test_read_only_pages_reject_posts(client, booking_member, page):
    client.force_login(booking_member.user)
    path = reverse("scheduling:index") if page == "index" else globals()[page](booking_member.profile)
    assert client.post(path, {"enabled": True, "role": "owner"}).status_code == 405


def test_logout_requires_csrf_and_clears_access(booking_member):
    client = Client(enforce_csrf_checks=True)
    client.force_login(booking_member.user)
    client.get(workspace(booking_member.profile))
    assert client.post(reverse("logout")).status_code == 403
    token = client.cookies["csrftoken"].value
    assert client.post(reverse("logout"), {"csrfmiddlewaretoken": token}).status_code == 302
    assert client.get(workspace(booking_member.profile)).status_code == 302


def test_business_name_is_escaped(client, booking_member):
    booking_member.profile.school.display_name = '<script>alert("x")</script>'
    booking_member.profile.school.save()
    client.force_login(booking_member.user)
    response = client.get(workspace(booking_member.profile))
    assert b"<script>" not in response.content
    assert b"&lt;script&gt;" in response.content
