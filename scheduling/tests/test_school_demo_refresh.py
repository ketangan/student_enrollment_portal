from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError

from core.models import SchoolAdminMembership
from scheduling.management.commands import refresh_school_journey_demo as refresh
from scheduling.models import Camp, LessonTeacher, SchedulingMembership, Session
from scheduling.tests.test_integrated_demo import demo_environment, integrated

pytestmark = pytest.mark.django_db


@pytest.fixture
def local_refresh(monkeypatch, integrated):
    monkeypatch.setattr(refresh, "settings", SimpleNamespace(DEBUG=True, DATABASES={"default": {
        "NAME": refresh.PREVIEW_DATABASE, "HOST": "localhost"}},
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"))
    return integrated


def test_refresh_is_repeatable_and_preserves_bookings_and_passwords(local_refresh):
    profile, _ = local_refresh
    original = list(Session.objects.order_by("pk").values())
    call_command("refresh_school_journey_demo")
    teacher = LessonTeacher.objects.get(profile=profile)
    assert teacher.login_user.check_password("admin")
    teacher.login_user.set_password("keep-existing-password")
    teacher.login_user.save()
    counts = (Session.objects.count(), Camp.objects.count(), get_user_model().objects.count())
    call_command("refresh_school_journey_demo")
    assert counts == (Session.objects.count(), Camp.objects.count(), get_user_model().objects.count())
    assert list(Session.objects.filter(pk__in=[item["id"] for item in original]).order_by("pk").values()) == original
    teacher.refresh_from_db()
    assert teacher.login_user.check_password("keep-existing-password")
    assert not SchedulingMembership.objects.filter(user=teacher.login_user).exists()
    assert not SchoolAdminMembership.objects.filter(user=teacher.login_user).exists()


@pytest.mark.parametrize("field,value", [("DEBUG", False), ("NAME", "production"),
    ("HOST", "remote.example.com"), ("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")])
def test_refresh_refuses_unsafe_environment(local_refresh, field, value):
    if field in {"NAME", "HOST"}:
        refresh.settings.DATABASES["default"][field] = value
    else:
        setattr(refresh.settings, field, value)
    with pytest.raises(CommandError, match="local preview"):
        call_command("refresh_school_journey_demo")


@pytest.mark.parametrize("field,value", [("is_demo", False), ("smtp_host", "smtp.example.com"),
    ("stripe_customer_id", "cus_live")])
def test_refresh_refuses_connected_school(local_refresh, field, value):
    profile, _ = local_refresh
    setattr(profile.school, field, value)
    profile.school.save()
    with pytest.raises(CommandError, match="externally connected"):
        call_command("refresh_school_journey_demo")


@pytest.mark.parametrize("kind", ["staff", "school", "scheduling", "teacher", "different-login"])
def test_refresh_never_rebinds_shared_accounts_and_rolls_back(local_refresh, kind):
    profile, _ = local_refresh
    teacher = LessonTeacher.objects.get(profile=profile)
    user = get_user_model().objects.create_user(f"school_teacher_{teacher.pk}", password="keep")
    if kind == "staff":
        user.is_staff = True
        user.save()
    elif kind == "school":
        SchoolAdminMembership.objects.create(user=user, school=profile.school)
    elif kind == "scheduling":
        SchedulingMembership.objects.create(user=user, profile=profile)
    elif kind == "teacher":
        LessonTeacher.objects.create(profile=profile, name="Other", login_user=user)
    else:
        teacher.login_user = get_user_model().objects.create_user("existing-teacher")
        teacher.save()
    before = list(Session.objects.order_by("pk").values())
    with pytest.raises(CommandError, match="shared teacher|different login"):
        call_command("refresh_school_journey_demo")
    assert list(Session.objects.order_by("pk").values()) == before
    user.refresh_from_db()
    assert user.check_password("keep")
