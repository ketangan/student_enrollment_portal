# core/tests/test_lead_admin.py
from __future__ import annotations

import pytest
from django.urls import reverse

from core.models import Lead
from core.tests.factories import LeadFactory, SchoolAdminMembershipFactory, SchoolFactory, UserFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _staff_client(client, school):
    """Create a staff user with school membership, log them in, return user."""
    membership = SchoolAdminMembershipFactory(school=school)
    client.force_login(membership.user)
    return membership.user


def _superuser_client(client):
    user = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(user)
    return user


# ---------------------------------------------------------------------------
# 4a: ConvertedFilter
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_converted_filter_unconverted(client):
    school = SchoolFactory(plan="starter")
    _staff_client(client, school)
    converted_lead = LeadFactory(school=school)
    unconverted_lead = LeadFactory(school=school)

    # Link a dummy submission to converted_lead
    from core.tests.factories import SubmissionFactory
    sub = SubmissionFactory(school=school)
    converted_lead.converted_submission = sub
    converted_lead.save(update_fields=["converted_submission"])

    url = reverse("admin:core_lead_changelist") + "?converted=no"
    response = client.get(url)

    assert response.status_code == 200
    ids_shown = [obj.pk for obj in response.context["cl"].queryset]
    assert unconverted_lead.pk in ids_shown
    assert converted_lead.pk not in ids_shown


@pytest.mark.django_db
def test_converted_filter_converted(client):
    school = SchoolFactory(plan="starter")
    _staff_client(client, school)
    converted_lead = LeadFactory(school=school)
    LeadFactory(school=school)  # unconverted, should be excluded

    from core.tests.factories import SubmissionFactory
    sub = SubmissionFactory(school=school)
    converted_lead.converted_submission = sub
    converted_lead.save(update_fields=["converted_submission"])

    url = reverse("admin:core_lead_changelist") + "?converted=yes"
    response = client.get(url)

    assert response.status_code == 200
    ids_shown = [obj.pk for obj in response.context["cl"].queryset]
    assert converted_lead.pk in ids_shown
    assert len(ids_shown) == 1


# ---------------------------------------------------------------------------
# 4b: quick_add_view
# ---------------------------------------------------------------------------

QUICK_ADD_URL = "/admin/core/lead/quick_add/"


@pytest.mark.django_db
def test_quick_add_creates_lead(client):
    school = SchoolFactory(plan="starter", slug="qa-school")
    _staff_client(client, school)

    response = client.post(QUICK_ADD_URL, {
        "name": "Jane Caller",
        "email": "jane@example.com",
        "phone": "555-1234",
        "interested_in_label": "Ballet",
        "source": "phone",
        "notes": "Called Monday afternoon",
    })

    assert response.status_code == 302
    lead = Lead.objects.get(school=school, email="jane@example.com")
    assert lead.name == "Jane Caller"
    assert lead.phone == "555-1234"
    assert lead.source == "phone"
    assert lead.notes == "Called Monday afternoon"
    assert lead.interested_in_label == "Ballet"


@pytest.mark.django_db
def test_quick_add_missing_name_no_create(client):
    school = SchoolFactory(plan="starter", slug="qa-missing-name")
    _staff_client(client, school)

    response = client.post(QUICK_ADD_URL, {"email": "x@example.com"})

    assert response.status_code == 302
    assert Lead.objects.filter(school=school).count() == 0


@pytest.mark.django_db
def test_quick_add_missing_email_no_create(client):
    school = SchoolFactory(plan="starter", slug="qa-missing-email")
    _staff_client(client, school)

    response = client.post(QUICK_ADD_URL, {"name": "Bob"})

    assert response.status_code == 302
    assert Lead.objects.filter(school=school).count() == 0


@pytest.mark.django_db
def test_quick_add_duplicate_email_no_create(client):
    school = SchoolFactory(plan="starter", slug="qa-dup-email")
    _staff_client(client, school)
    LeadFactory(school=school, email="dup@example.com")

    response = client.post(QUICK_ADD_URL, {
        "name": "Dupe Person",
        "email": "dup@example.com",
    })

    assert response.status_code == 302
    assert Lead.objects.filter(school=school, email="dup@example.com").count() == 1


@pytest.mark.django_db
def test_quick_add_auto_assigns_school(client):
    """Lead is assigned to the staff user's school, not any other school."""
    school = SchoolFactory(plan="starter", slug="qa-auto-school")
    other_school = SchoolFactory(plan="starter", slug="qa-other-school")
    _staff_client(client, school)

    client.post(QUICK_ADD_URL, {"name": "Auto", "email": "auto@example.com"})

    assert Lead.objects.filter(school=school, email="auto@example.com").exists()
    assert not Lead.objects.filter(school=other_school, email="auto@example.com").exists()


@pytest.mark.django_db
def test_quick_add_superuser_rejected(client):
    """Superusers cannot use quick-add (they have the full admin form)."""
    _superuser_client(client)

    response = client.post(QUICK_ADD_URL, {
        "name": "Super",
        "email": "super@example.com",
    })

    assert response.status_code == 302
    assert Lead.objects.filter(email="super@example.com").count() == 0


@pytest.mark.django_db
def test_quick_add_get_request_redirects(client):
    """GET on quick_add redirects back to changelist without error."""
    school = SchoolFactory(plan="starter", slug="qa-get-req")
    _staff_client(client, school)

    response = client.get(QUICK_ADD_URL)
    assert response.status_code == 302


@pytest.mark.django_db
def test_quick_add_unauthenticated_redirects(client):
    """Unauthenticated request is redirected to login."""
    response = client.post(QUICK_ADD_URL, {"name": "X", "email": "x@x.com"})
    assert response.status_code == 302
    assert "/login/" in response["Location"] or "login" in response["Location"]
