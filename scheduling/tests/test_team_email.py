import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from core.models import AdminAuditLog, SchoolAdminMembership
from scheduling.models import SchedulingMembership
from scheduling.team_access import membership_form
from .test_location_access import two_locations, manager
from .test_reservations import inventory, path

pytestmark = pytest.mark.django_db


@pytest.fixture(params=["enrollment", "booking"])
def team(request, client, two_locations, booking_member, manager):
    school = booking_member.profile.school
    client.force_login(manager)
    if request.param == "booking":
        url = path(booking_member, "access")
        return {"add": url, "edit": url, "target": booking_member.user,
                "edit_data": {"team_action": "email", "member": booking_member.pk},
                "add_data": {"team_action": "add", "role": "scheduler", "location_access": "all"},
                "school": school, "manager": manager}
    SchoolAdminMembership.objects.create(school=school, user=manager, role="owner")
    manager.is_staff = True
    manager.save(update_fields=["is_staff"])
    booking_member.user.is_staff = True
    booking_member.user.save(update_fields=["is_staff"])
    member = SchoolAdminMembership.objects.create(school=school, user=booking_member.user, role="editor")
    return {"add": reverse("school_team_add", args=[school.slug]),
            "edit": reverse("school_team_update_email", args=[school.slug, member.pk]),
            "target": booking_member.user, "edit_data": {}, "add_data": {"role": "editor"},
            "school": school, "manager": manager}


@pytest.mark.parametrize("email", [None, "", "not-an-email", "a" * 250 + "@example.com"])
def test_creation_requires_valid_email(client, team, email):
    data = {**team["add_data"], "username": "email-new", "password": "testing-password"}
    if email is not None:
        data["email"] = email
    assert client.post(team["add"], data).status_code in (302, 400)
    assert not get_user_model().objects.filter(username="email-new").exists()


def test_shared_email_allowed_and_reset_works(client, team, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    for username in ("email-first", "email-second"):
        assert client.post(team["add"], {**team["add_data"], "username": username,
            "password": "testing-password", "email": " office@example.com "}).status_code == 302
    users = list(get_user_model().objects.filter(username__in=["email-first", "email-second"]))
    assert len(users) == 2 and all(u.email == "office@example.com" for u in users)
    client.logout()
    assert client.post(reverse("password_reset"), {"email": "office@example.com"}).status_code == 302
    assert len(mail.outbox) == 2
    assert all(m.to == ["office@example.com"] for m in mail.outbox)
    user = users[0]
    token = default_token_generator.make_token(user)
    url = reverse("password_reset_confirm", args=[urlsafe_base64_encode(force_bytes(user.pk)), token])
    response = client.get(url)
    assert response.status_code == 302
    response = client.post(response.url, {"new_password1": "Recovery-pass-392!", "new_password2": "Recovery-pass-392!"})
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.check_password("Recovery-pass-392!")
    assert not default_token_generator.check_token(user, token)
    assert client.login(username=user.username, password="Recovery-pass-392!")


def test_edit_email_is_audited_and_preserves_password(client, team, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    target = team["target"]
    target.email = "old@example.com"
    target.save(update_fields=["email"])
    password = target.password
    old_token = default_token_generator.make_token(target)
    before = AdminAuditLog.objects.count()
    assert client.post(team["edit"], {**team["edit_data"], "email": "new@example.com"}).status_code == 302
    target.refresh_from_db()
    assert target.email == "new@example.com" and target.password == password
    assert AdminAuditLog.objects.count() == before + 1
    assert not default_token_generator.check_token(target, old_token)
    client.post(team["edit"], {**team["edit_data"], "email": "new@example.com"})
    assert AdminAuditLog.objects.count() == before + 1
    client.logout()
    client.post(reverse("password_reset"), {"email": "old@example.com"})
    assert not mail.outbox
    client.post(reverse("password_reset"), {"email": "new@example.com"})
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["new@example.com"]


@pytest.mark.parametrize("email", ["", "bad-email"])
def test_invalid_edit_preserves_email(client, team, email):
    old_email = team["target"].email
    count = AdminAuditLog.objects.count()
    client.post(team["edit"], {**team["edit_data"], "email": email})
    team["target"].refresh_from_db()
    assert team["target"].email == old_email
    assert AdminAuditLog.objects.count() == count


def test_non_owner_cannot_edit_email(client, team):
    client.force_login(team["target"])
    old_email = team["target"].email
    assert client.post(team["edit"], {**team["edit_data"], "email": "stolen@example.com"}).status_code == 404
    team["target"].refresh_from_db()
    assert team["target"].email == old_email


def test_inactive_member_cannot_be_edited(client, team):
    SchoolAdminMembership.objects.filter(user=team["target"]).update(is_active=False)
    SchedulingMembership.objects.filter(user=team["target"]).update(is_active=False)
    assert client.post(team["edit"], {**team["edit_data"], "email": "new@example.com"}).status_code == 404


def test_other_school_owner_cannot_edit_email(client, team):
    from core.tests.factories import SchoolFactory, UserFactory
    from scheduling.models import SchedulingProfile
    other = SchoolFactory()
    user = UserFactory(is_staff=True)
    SchoolAdminMembership.objects.create(school=other, user=user, role="owner")
    profile = SchedulingProfile.objects.create(school=other, enabled=True)
    SchedulingMembership.objects.create(profile=profile, user=user, role="owner")
    client.force_login(user)
    assert client.post(team["edit"], {**team["edit_data"], "email": "wrong@example.com"}).status_code == 404


def test_email_edit_requires_csrf(team):
    from django.test import Client
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(team["manager"])
    assert browser.post(team["edit"], {**team["edit_data"], "email": "wrong@example.com"}).status_code == 403


def test_superadmin_can_set_email(client, team):
    from core.tests.factories import UserFactory
    client.force_login(UserFactory(is_staff=True, is_superuser=True))
    assert client.post(team["edit"], {**team["edit_data"], "email": "school@example.com"}).status_code == 302
    team["target"].refresh_from_db()
    assert team["target"].email == "school@example.com"


def test_school_owner_cannot_take_over_superadmin_recovery(client, team):
    target = team["target"]
    old_email = target.email
    target.is_superuser = True
    target.save(update_fields=["is_superuser"])
    assert client.post(team["edit"], {**team["edit_data"], "email": "wrong@example.com"}).status_code == 404
    target.refresh_from_db()
    assert target.email == old_email


def test_legacy_blank_email_does_not_block_name_or_role_changes(client, team):
    target = team["target"]
    target.email = ""
    target.save(update_fields=["email"])
    password = target.password
    if team["edit_data"]:
        member = SchedulingMembership.objects.get(pk=team["edit_data"]["member"])
        url = team["edit"]
        name_data = {"member": member.pk, "team_action": "name"}
        role_url = url
        role_data = {"member": member.pk, "team_action": "role", "role": "viewer",
                     "edit_revision": membership_form(member).fields["edit_revision"].initial}
    else:
        member = SchoolAdminMembership.objects.get(user=target, school=team["school"])
        url = reverse("school_team_update_name", args=[team["school"].slug, member.pk])
        name_data = {}
        role_url = reverse("school_team_role", args=[team["school"].slug, member.pk])
        role_data = {"role": "viewer"}
    assert client.post(url, {**name_data, "first_name": "Changed", "last_name": "Name"}).status_code == 302
    assert client.post(role_url, role_data).status_code == 302
    target.refresh_from_db()
    member.refresh_from_db()
    assert target.email == "" and target.password == password
    assert target.get_full_name() == "Changed Name" and member.role == "viewer"
