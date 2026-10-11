import pytest
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory
from django.urls import reverse

from scheduling.models import SchedulingMembership, SchedulingProfile

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("model", [SchedulingProfile, SchedulingMembership])
@pytest.mark.parametrize("is_superuser,is_active,allowed", [(False, True, False), (True, True, True), (True, False, False)])
def test_model_permissions_are_operator_only(booking_user, model, is_superuser, is_active, allowed):
    booking_user.is_superuser = is_superuser
    booking_user.is_active = is_active
    request = RequestFactory().get("/admin/")
    request.user = booking_user
    model_admin = admin.site._registry[model]
    for method in ["has_module_permission", "has_view_permission", "has_add_permission", "has_change_permission", "has_delete_permission"]:
        assert getattr(model_admin, method)(request) is allowed


@pytest.mark.parametrize("model_name", ["schedulingprofile", "schedulingmembership"])
def test_staff_with_django_permissions_cannot_provision(client, booking_member, model_name):
    user = booking_member.user
    user.is_staff = True
    user.user_permissions.set(Permission.objects.filter(content_type__app_label="scheduling"))
    user.save()
    client.force_login(user)
    assert client.get(reverse(f"admin:scheduling_{model_name}_changelist")).status_code == 403
    assert client.post(reverse(f"admin:scheduling_{model_name}_add"), {}).status_code == 403


def test_operator_can_enable_and_grant_access_with_audit(client, booking_profile, booking_user):
    operator = get_user_model().objects.create_superuser("operator", "op@example.test", "secret")
    booking_profile.enabled = False
    booking_profile.save()
    client.force_login(operator)
    url = reverse("admin:scheduling_schedulingprofile_change", args=[booking_profile.pk])
    assert client.get(url).status_code == 200
    response = client.post(url, {"school": booking_profile.school_id, "enabled": "on", "_save": "Save"})
    assert response.status_code == 302
    booking_profile.refresh_from_db()
    assert booking_profile.enabled is True
    response = client.post(reverse("admin:scheduling_schedulingmembership_add"), {
        "profile": booking_profile.pk, "user": booking_user.pk,
        "role": "owner", "is_active": "on", "_save": "Save",
    })
    assert response.status_code == 302
    membership = SchedulingMembership.objects.get(profile=booking_profile, user=booking_user)
    assert membership.role == "owner" and membership.is_active
    assert LogEntry.objects.filter(user=operator, content_type__app_label="scheduling").count() == 2
    assert not booking_user.is_staff
