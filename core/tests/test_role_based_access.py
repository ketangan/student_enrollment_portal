"""
Tests for role-based school admin access (Phase 23).

Covers:
- viewer can read, blocked on mutations
- editor can mutate, blocked on owner endpoints
- owner can do everything
- last owner cannot be removed or demoted
- duplicate membership prevention
- one user, multiple schools
- inactive membership denies access
- superuser bypasses role checks
- existing users retain access after migration (all are owners)
"""

import pytest
from django.urls import reverse

from core.models import SchoolAdminMembership
from core.tests.factories import (
    SchoolAdminMembershipFactory,
    SchoolFactory,
    UserFactory,
    SubmissionFactory,
    LeadFactory,
)
from core.services.school_permissions import get_school_membership, require_school_role


# ── Helpers ───────────────────────────────────────────────────────────────────

def _membership(school, role="owner", user=None, is_active=True):
    u = user or UserFactory(is_staff=True)
    m = SchoolAdminMembership.objects.create(
        school=school, user=u, role=role, is_active=is_active
    )
    return u, m


def _settings_url(school):
    return reverse("school_settings", kwargs={"school_slug": school.slug})


def _submissions_url(school):
    return reverse("school_submissions", kwargs={"school_slug": school.slug})


def _leads_url(school):
    return reverse("school_leads", kwargs={"school_slug": school.slug})


def _status_update_url(school, submission_id):
    return reverse("school_submission_status_update", kwargs={"school_slug": school.slug, "submission_id": submission_id})


def _lead_status_url(school, lead_id):
    return reverse("school_lead_status_update", kwargs={"school_slug": school.slug, "lead_id": lead_id})


# ── get_school_membership ─────────────────────────────────────────────────────

@pytest.mark.django_db
def test_get_school_membership_returns_active():
    school = SchoolFactory()
    user, m = _membership(school, role="editor")
    result = get_school_membership(user, school)
    assert result == m


@pytest.mark.django_db
def test_get_school_membership_ignores_inactive():
    school = SchoolFactory()
    user, _ = _membership(school, role="editor", is_active=False)
    assert get_school_membership(user, school) is None


@pytest.mark.django_db
def test_get_school_membership_wrong_school():
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user, _ = _membership(school_a)
    assert get_school_membership(user, school_b) is None


@pytest.mark.django_db
def test_has_role_rank():
    school = SchoolFactory()
    m = SchoolAdminMembership(school=school, role="editor")
    assert m.has_role("viewer") is True
    assert m.has_role("editor") is True
    assert m.has_role("owner") is False


# ── Viewer: read-only access ──────────────────────────────────────────────────

@pytest.mark.django_db
def test_viewer_can_access_submissions_list(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="viewer")
    client.force_login(user)
    resp = client.get(_submissions_url(school))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_viewer_can_access_leads_list(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="viewer")
    client.force_login(user)
    resp = client.get(_leads_url(school))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_viewer_blocked_on_settings(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="viewer")
    client.force_login(user)
    resp = client.get(_settings_url(school))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_viewer_blocked_on_status_mutation(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="viewer")
    submission = SubmissionFactory(school=school, status="pending")
    client.force_login(user)
    resp = client.post(
        _status_update_url(school, submission.id),
        {"status": "accepted", "next": ""},
    )
    assert resp.status_code == 404


@pytest.mark.django_db
def test_viewer_blocked_on_lead_status_mutation(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="viewer")
    lead = LeadFactory(school=school)
    client.force_login(user)
    resp = client.post(
        _lead_status_url(school, lead.id),
        {"status": "contacted", "next": ""},
    )
    assert resp.status_code == 404


# ── Editor: mutations allowed, owner endpoints blocked ────────────────────────

@pytest.mark.django_db
def test_editor_can_update_submission_status(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="editor")
    submission = SubmissionFactory(school=school, status="pending")
    client.force_login(user)
    resp = client.post(
        _status_update_url(school, submission.id),
        {"status": "accepted", "next": _submissions_url(school)},
    )
    assert resp.status_code == 302


@pytest.mark.django_db
def test_editor_blocked_on_settings(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="editor")
    client.force_login(user)
    resp = client.get(_settings_url(school))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_editor_blocked_on_billing(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="editor")
    client.force_login(user)
    resp = client.get(reverse("school_billing", kwargs={"school_slug": school.slug}))
    assert resp.status_code == 404


# ── Owner: full access ────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_owner_can_access_settings(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="owner")
    client.force_login(user)
    resp = client.get(_settings_url(school))
    assert resp.status_code == 200


# ── Superuser bypasses all role checks ───────────────────────────────────────

@pytest.mark.django_db
def test_superuser_can_access_settings_without_membership(client):
    school = SchoolFactory()
    superuser = UserFactory(is_staff=True, is_superuser=True)
    client.force_login(superuser)
    resp = client.get(_settings_url(school))
    assert resp.status_code == 200


# ── Inactive membership denies access ────────────────────────────────────────

@pytest.mark.django_db
def test_inactive_membership_denies_access(client):
    school = SchoolFactory()
    user, _ = _membership(school, role="owner", is_active=False)
    client.force_login(user)
    resp = client.get(_submissions_url(school))
    assert resp.status_code == 404


# ── Multi-school: one user, multiple schools ──────────────────────────────────

@pytest.mark.django_db
def test_user_can_belong_to_multiple_schools(client):
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user = UserFactory(is_staff=True)
    SchoolAdminMembership.objects.create(school=school_a, user=user, role="owner")
    SchoolAdminMembership.objects.create(school=school_b, user=user, role="editor")
    client.force_login(user)

    resp_a = client.get(_settings_url(school_a))
    assert resp_a.status_code == 200  # owner on school_a

    resp_b = client.get(_settings_url(school_b))
    assert resp_b.status_code == 404  # only editor on school_b


@pytest.mark.django_db
def test_user_cannot_access_other_schools(client):
    school_a = SchoolFactory()
    school_b = SchoolFactory()
    user, _ = _membership(school_a, role="owner")
    client.force_login(user)
    resp = client.get(_submissions_url(school_b))
    assert resp.status_code == 404


# ── Duplicate membership prevention ──────────────────────────────────────────

@pytest.mark.django_db
def test_duplicate_membership_blocked_by_constraint():
    from django.db import IntegrityError
    school = SchoolFactory()
    user, _ = _membership(school, role="owner")
    with pytest.raises(IntegrityError):
        SchoolAdminMembership.objects.create(school=school, user=user, role="editor")


# ── Team management: last owner protection ────────────────────────────────────

@pytest.mark.django_db
def test_cannot_remove_last_owner(client):
    school = SchoolFactory()
    owner_user, owner_m = _membership(school, role="owner")
    client.force_login(owner_user)

    resp = client.post(
        reverse("school_team_remove", kwargs={"school_slug": school.slug, "membership_id": owner_m.id}),
    )
    assert resp.status_code == 302
    msgs = list(resp.wsgi_request._messages)
    assert any("last owner" in str(m).lower() for m in msgs)
    owner_m.refresh_from_db()
    assert owner_m.is_active is True


@pytest.mark.django_db
def test_cannot_demote_last_owner(client):
    school = SchoolFactory()
    owner_user, owner_m = _membership(school, role="owner")
    client.force_login(owner_user)

    resp = client.post(
        reverse("school_team_role", kwargs={"school_slug": school.slug, "membership_id": owner_m.id}),
        {"role": "editor"},
    )
    assert resp.status_code == 302
    msgs = list(resp.wsgi_request._messages)
    assert any("last owner" in str(m).lower() for m in msgs)
    owner_m.refresh_from_db()
    assert owner_m.role == "owner"


@pytest.mark.django_db
def test_can_remove_owner_when_another_owner_exists(client):
    school = SchoolFactory()
    owner1, m1 = _membership(school, role="owner")
    owner2, m2 = _membership(school, role="owner", user=UserFactory(is_staff=True))
    client.force_login(owner1)

    resp = client.post(
        reverse("school_team_remove", kwargs={"school_slug": school.slug, "membership_id": m2.id}),
    )
    assert resp.status_code == 302
    m2.refresh_from_db()
    assert m2.is_active is False


# ── Team add ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_team_add_existing_user(client):
    school = SchoolFactory()
    owner, _ = _membership(school, role="owner")
    new_user = UserFactory(is_staff=True)
    client.force_login(owner)

    resp = client.post(
        reverse("school_team_add", kwargs={"school_slug": school.slug}),
        {"email": new_user.email, "role": "editor"},
    )
    assert resp.status_code == 302
    assert SchoolAdminMembership.objects.filter(
        school=school, user=new_user, role="editor", is_active=True
    ).exists()


@pytest.mark.django_db
def test_team_add_unknown_email_gives_explicit_error(client):
    school = SchoolFactory()
    owner, _ = _membership(school, role="owner")
    client.force_login(owner)

    resp = client.post(
        reverse("school_team_add", kwargs={"school_slug": school.slug}),
        {"email": "nobody@example.com", "role": "editor"},
    )
    assert resp.status_code == 302
    msgs = list(resp.wsgi_request._messages)
    assert any("no enrollify account" in str(m).lower() for m in msgs)


@pytest.mark.django_db
def test_team_add_blocked_for_non_owner(client):
    school = SchoolFactory()
    editor, _ = _membership(school, role="editor")
    new_user = UserFactory(is_staff=True)
    client.force_login(editor)

    resp = client.post(
        reverse("school_team_add", kwargs={"school_slug": school.slug}),
        {"email": new_user.email, "role": "viewer"},
    )
    assert resp.status_code == 404


# ── Existing memberships: migration correctness ───────────────────────────────

@pytest.mark.django_db
def test_factory_default_role_is_owner():
    m = SchoolAdminMembershipFactory()
    assert m.role == "owner"
    assert m.is_active is True
