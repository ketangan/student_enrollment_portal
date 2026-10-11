from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from core.models import School, SchoolAdminMembership
from core.payments.models import PaymentAccount
from scheduling.management.commands import seed_integrated_booking_demo as seed
from scheduling.models import (
    AdmissionPolicy, Camp, LessonSeries, Location, Offering, SchedulingMembership,
    SchedulingProfile, Session, VisitBooking, WorkshopRequest,
)
from scheduling import admissions, visits
from scheduling.navigation import enrollment_url, school_entry

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo_environment(monkeypatch, settings):
    monkeypatch.setattr(seed, "settings", SimpleNamespace(DEBUG=True, DATABASES={"default": {
        "NAME": seed.PREVIEW_DATABASE, "HOST": "127.0.0.1"}}))
    settings.BOOKINGS_MESSAGE_PREVIEW = True
    settings.BOOKINGS_LESSON_BOOKING_PREVIEW = True
    settings.DEBUG = True


@pytest.fixture
def integrated(demo_environment):
    call_command("seed_integrated_booking_demo", password="admin")
    return SchedulingProfile.objects.get(school__slug=seed.SLUG), get_user_model().objects.get(username=seed.USERNAME)


def path(name, **kwargs):
    return reverse("scheduling:" + name, kwargs={"school_slug": seed.SLUG, **kwargs})


def test_setup_is_scoped_repeatable_and_password_works(integrated):
    profile, user = integrated
    other = School.objects.create(slug="south-bay-music", feature_flags={"keep": True})
    before = {model: model.objects.count() for model in [School, Location, Offering, Session, VisitBooking, WorkshopRequest, LessonSeries, Camp]}
    call_command("seed_integrated_booking_demo", password="admin")
    assert {model: model.objects.count() for model in before} == before
    other.refresh_from_db()
    assert other.feature_flags == {"keep": True}
    assert not SchedulingProfile.objects.filter(school=other).exists()
    assert user.check_password("admin") and not user.is_superuser
    assert profile.capabilities == list(seed.CAPABILITIES)
    assert AdmissionPolicy.objects.get(profile=profile).enabled
    assert LessonSeries.objects.get(profile=profile).occurrences.count() == 4
    assert Camp.objects.get(offering__location__profile=profile).sessions.count() == 3


def test_payment_merchant_is_separate_from_other_demos(demo_environment):
    other = School.objects.create(slug="workshop-demo")
    PaymentAccount.objects.create(school=other, reference="acct_local_preview")
    call_command("seed_integrated_booking_demo", password="admin")
    assert PaymentAccount.objects.get(school__slug=seed.SLUG).reference == "acct_local_preview_enrollment"
    assert PaymentAccount.objects.get(school=other).reference == "acct_local_preview"


@pytest.mark.parametrize("change", [{"DEBUG": False}, {"NAME": "customer-db"}, {"HOST": "production.example.com"}])
def test_command_refuses_nonlocal_database(demo_environment, change):
    if "DEBUG" in change:
        seed.settings.DEBUG = change["DEBUG"]
    else:
        seed.settings.DATABASES["default"].update(change)
    with pytest.raises(CommandError, match="isolated"):
        call_command("seed_integrated_booking_demo", password="admin")
    assert not School.objects.exists()


@pytest.mark.parametrize("field,value", [("is_demo", False), ("stripe_customer_id", "cus_real"), ("smtp_host", "smtp.example.com"), ("app_fee_stripe_secret_key", "configured")])
def test_command_preserves_external_or_nondemo_school(demo_environment, field, value):
    school = School.objects.create(slug=seed.SLUG, is_demo=True)
    setattr(school, field, value)
    school.save()
    with pytest.raises(CommandError, match="Refusing"):
        call_command("seed_integrated_booking_demo", password="admin")
    assert not get_user_model().objects.exists()


@pytest.mark.parametrize("kind", ["operator", "school", "bookings"])
def test_shared_user_password_is_never_reset(demo_environment, kind):
    user = get_user_model().objects.create_user(seed.USERNAME, password="keep", is_superuser=kind == "operator")
    school = School.objects.create(slug="other")
    if kind == "school":
        SchoolAdminMembership.objects.create(user=user, school=school)
    if kind == "bookings":
        SchedulingMembership.objects.create(user=user, profile=SchedulingProfile.objects.create(school=school))
    with pytest.raises(CommandError, match="shared account"):
        call_command("seed_integrated_booking_demo", password="admin")
    user.refresh_from_db()
    assert user.check_password("keep")
    assert not School.objects.filter(slug=seed.SLUG).exists()


def test_real_payment_account_is_not_replaced(demo_environment):
    school = School.objects.create(slug=seed.SLUG, is_demo=True)
    PaymentAccount.objects.create(school=school, reference="acct_other")
    with pytest.raises(CommandError, match="simulated"):
        call_command("seed_integrated_booking_demo", password="admin")
    assert not SchedulingProfile.objects.exists()


def test_school_settings_and_all_booking_entry_points(integrated, client):
    profile, user = integrated
    assert client.login(username=user.username, password="admin")
    dashboard = client.get(reverse("school_dashboard", kwargs={"school_slug": seed.SLUG}))
    assert b">Scheduling</a>" in dashboard.content
    settings_path = reverse("school_settings", kwargs={"school_slug": seed.SLUG}) + "?tab=scheduling"
    response = client.get(settings_path)
    assert response.status_code == 200 and response.context["active_tab"] == "scheduling"
    entry = response.context["scheduling_entry"]
    assert b'data-tab="scheduling"' in response.content
    for link in entry["links"]:
        result = client.get(link["url"])
        assert result.status_code == 200, (link, result.status_code)
    for name in ["workspace", "visits", "workshops", "requests", "parties", "camps", "lessons", "reservations",
                 "family_access", "lesson_credits", "lesson_bookings", "messages"]:
        result = client.get(path(name))
        assert result.status_code == 200, name
        assert b"Enrollment admin" in result.content
    assert client.get(path("features")).status_code == 404  # Owner is not an operator.
    for name in ["visit_catalogue", "public_schedule", "lesson_trials"]:
        assert Client().get(path(name)).status_code == 200


def test_navigation_respects_memberships_and_tenant_switch(integrated, client):
    profile, user = integrated
    assert school_entry(AnonymousUser(), profile.school) is None
    assert enrollment_url(AnonymousUser(), profile) == ""
    assert enrollment_url(user, None) == ""
    outsider = get_user_model().objects.create_user("outsider")
    assert school_entry(outsider, profile.school) is None
    assert enrollment_url(outsider, profile) == ""
    membership = SchedulingMembership.objects.get(user=user)
    membership.role = "viewer"
    membership.save()
    assert len(school_entry(user, profile.school)["links"]) == 1
    membership.is_active = False
    membership.save()
    assert school_entry(user, profile.school) is None
    client.force_login(user)
    page = client.get(reverse("school_settings", kwargs={"school_slug": seed.SLUG}) + "?tab=scheduling")
    assert page.context["active_tab"] == "general"
    assert b'data-tab="scheduling"' not in page.content
    profile.enabled = False
    profile.save()
    user.is_superuser = True
    user.save()
    assert school_entry(user, profile.school) is None
    assert enrollment_url(user, profile)


def test_independent_bookings_user_has_no_enrollment_link(integrated, client):
    profile, user = integrated
    SchoolAdminMembership.objects.filter(user=user).delete()
    client.force_login(user)
    assert b"Enrollment admin" not in client.get(path("workspace")).content
    assert "Tour-to-enrollment rules" not in str(school_entry(user, profile.school))


def test_tours_only_workspace_and_closed_preview_controls(integrated, client, settings):
    profile, user = integrated
    profile.capabilities = ["visits"]
    profile.save()
    settings.BOOKINGS_MESSAGE_PREVIEW = False
    client.force_login(user)
    response = client.get(path("workspace"))
    assert b"not been activated" not in response.content
    assert b"Next 7 days" in response.content
    entry = str(school_entry(user, profile.school))
    assert "Visit settings and closures" not in entry
    assert "Workshops and participant choices" not in entry
    assert "Integrations" not in entry
    profile.capabilities = ["lessons"]
    profile.lessons_enabled = False
    profile.save()
    entry = str(school_entry(user, profile.school))
    assert "Lesson scheduling settings" in entry and "Teachers," not in entry
    profile.lessons_enabled = True
    profile.save()
    settings.BOOKINGS_LESSON_BOOKING_PREVIEW = False
    assert "Trial and family booking offers" not in str(school_entry(user, profile.school))


@pytest.mark.parametrize("slug", ["south-bay-music", "kidworks-childrens-center"])
def test_existing_school_ui_stays_opted_out(integrated, client, slug):
    _, user = integrated
    school = School.objects.create(slug=slug, plan="custom", is_demo=True)
    SchoolAdminMembership.objects.create(school=school, user=user, role="owner")
    client.force_login(user)
    response = client.get(reverse("school_settings", kwargs={"school_slug": slug}))
    assert response.status_code == 200
    assert not response.context["scheduling_entry"]
    assert b'data-tab="scheduling"' not in response.content
    assert f'/bookings/{slug}/'.encode() not in response.content


def test_integrated_tour_application_playdate_journey(integrated, client):
    profile, user = integrated
    booking = VisitBooking.objects.get(contact_email="taylor-walkthrough@example.invalid")
    visits.change_state(user, seed.SLUG, booking.public_id, "attended", "booked")
    case = admissions.case_for_lead(AdmissionPolicy.objects.get(profile=profile), booking.lead, create=True)
    draft = admissions.prepare_draft(case)
    parent = Client()
    assert parent.get(reverse("apply_resume", kwargs={"school_slug": seed.SLUG, "token": draft.token}), follow=True).status_code == 200
    response = parent.post(reverse("apply", kwargs={"school_slug": seed.SLUG}), {
        "student_first_name": "Casey", "student_last_name": "Walkthrough", "interested_in": "creative",
        "enrollment_type": "enroll_now", "contact_email": booking.contact_email, "guardian_name": booking.contact_name,
        "_draft_token": draft.token})
    assert response.status_code == 302, response.content.decode()[-3000:]
    case.refresh_from_db()
    assert case.submission and case.lead.converted_submission_id == case.submission_id
    client.force_login(user)
    assert client.post(path("admission_case", lead_pk=case.lead_id), {"action": "release_playdate", "revision": ""}).status_code == 302
    slot = visits.slots(profile).filter(program__kind="playdate").first()
    url = path("playdate_book", token=case.submission.status_token, pk=slot.pk)
    token = parent.get(url).context["booking_token"]
    response = parent.post(url, {"booking_token": token, "adults": "1", "children": "1", "authority": "on"})
    assert response.status_code == 302, response.content.decode()[-3000:]
    case.submission.refresh_from_db()
    assert case.submission.status == "Playdate Scheduled"
    assert parent.get(response.url).status_code == 200
    status = parent.get(reverse("family_status", kwargs={"school_slug": seed.SLUG,
                                                        "token": case.submission.status_token}))
    assert status.status_code == 200
    rows = status.context["student_info"]
    assert {"label": "Program", "value": "Creative Learning"} in rows
    assert {"label": "Enrollment Type", "value": "Enroll now"} in rows
    assert not any(row["label"] == "Instrument" for row in rows)
