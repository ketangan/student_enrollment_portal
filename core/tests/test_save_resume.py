# core/tests/test_save_resume.py
"""
Tests for Feature 9: Save & Resume (magic link draft).

Coverage:
  - Model: token uniqueness, expiry window
  - Single-form view: save draft, resume, throttle, submit lifecycle
  - Multi-form view: step 1 → draft, step 2 → merge, final → Submission
  - resume_draft_view: expired, submitted, wrong school, token wins over session
  - Admin: required-field validation demoted to warning
"""
from __future__ import annotations

import pytest
from datetime import timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone
from django.core import mail

from core.models import DraftSubmission, School, Submission
from core.tests.factories import SchoolFactory


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

SINGLE_SLUG = "enrollment-request-demo"
MULTI_SLUG = "multi-form-demo"

# Minimal POST data for single-form (enrollment-request-demo).
# contact_email is type=email so _find_applicant_email will detect it.
_SINGLE_FORM_DATA = {
    "student_first_name": "Alice",
    "student_last_name": "Smith",
    "contact_email": "alice@example.com",
    "interested_in": "beginner",
    "enrollment_type": "enroll_now",
}

# Minimal POST data for multi-form step 1 (multi-form-demo enrollment form).
# "email" field there is type=text (not email) — _find_applicant_email won't
# detect it, so no resume link email is sent in multi-form tests.
_MULTI_STEP1_DATA = {
    "first_name": "Bob",
    "last_name": "Jones",
    "email": "bob@example.com",
    "program": "ballet",
}

_MULTI_STEP2_DATA = {
    "street": "123 Main St",
    "city": "Springfield",
    "state": "CA",
    "zip": "90001",
    "emergency_name": "Jane Jones",
    "emergency_phone": "555-9876",
}


def _make_school(slug, plan="pro"):
    school, _ = School.objects.get_or_create(
        slug=slug,
        defaults={"display_name": slug, "plan": plan},
    )
    school.plan = plan
    school.save(update_fields=["plan"])
    return school


def _make_draft(school, *, form_key="default", email="", data=None,
                last_form_key="", submitted=False, expired=False):
    draft = DraftSubmission(
        school=school,
        form_key=form_key,
        data=data or {},
        email=email,
        last_form_key=last_form_key,
    )
    if expired:
        draft.token_expires_at = timezone.now() - timedelta(days=1)
    else:
        draft.token_expires_at = timezone.now() + timedelta(days=7)
    if submitted:
        draft.submitted_at = timezone.now()
    draft.save()
    return draft


# ---------------------------------------------------------------------------
# 1–3. Model tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_draft_token_uniqueness():
    school = SchoolFactory()
    d1 = _make_draft(school)
    d2 = _make_draft(school)
    assert d1.token != d2.token


@pytest.mark.django_db
def test_draft_not_expired_within_window():
    school = SchoolFactory()
    draft = _make_draft(school)
    assert not draft.is_expired()


@pytest.mark.django_db
def test_draft_expired_after_window():
    school = SchoolFactory()
    draft = _make_draft(school, expired=True)
    assert draft.is_expired()


# ---------------------------------------------------------------------------
# 4–6. Single-form: save draft
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_save_draft_creates_draft_and_updates_session(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = _make_school(SINGLE_SLUG)
    url = reverse("apply", kwargs={"school_slug": SINGLE_SLUG})
    post_data = {**_SINGLE_FORM_DATA, "_action": "save_draft"}
    resp = client.post(url, data=post_data)
    assert resp.status_code == 302
    assert DraftSubmission.objects.filter(school=school).count() == 1
    draft = DraftSubmission.objects.get(school=school)
    assert client.session.get(f"apply_draft_id:{SINGLE_SLUG}") == draft.pk


@pytest.mark.django_db
def test_save_draft_emails_when_email_present(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEFAULT_FROM_EMAIL = "noreply@test.com"
    settings.BASE_URL = "http://testserver"
    mail.outbox.clear()
    _make_school(SINGLE_SLUG)
    url = reverse("apply", kwargs={"school_slug": SINGLE_SLUG})
    post_data = {**_SINGLE_FORM_DATA, "_action": "save_draft"}
    client.post(url, data=post_data)
    assert len(mail.outbox) == 1
    assert "alice@example.com" in mail.outbox[0].to


@pytest.mark.django_db
def test_save_draft_no_email_no_send(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    mail.outbox.clear()
    _make_school(SINGLE_SLUG)
    url = reverse("apply", kwargs={"school_slug": SINGLE_SLUG})
    # POST with only the action flag — no recognisable email field present
    post_data = {"student_first_name": "No", "student_last_name": "Email", "_action": "save_draft"}
    client.post(url, data=post_data)
    assert len(mail.outbox) == 0


# ---------------------------------------------------------------------------
# 7–11. Single-form: resume view
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resume_view_prepopulates_form_from_draft_data(client):
    school = _make_school(SINGLE_SLUG)
    draft = _make_draft(school, email="alice@example.com",
                        data={"student_first_name": "Alice", "student_last_name": "Smith"})
    url = reverse("apply_resume", kwargs={"school_slug": SINGLE_SLUG, "token": draft.token})
    resp = client.get(url, follow=True)
    assert resp.status_code == 200
    # After redirect to apply, the form should pre-populate from draft
    content = resp.content.decode()
    assert "Alice" in content


@pytest.mark.django_db
def test_resume_view_expired_shows_expired_page(client):
    school = _make_school(SINGLE_SLUG)
    draft = _make_draft(school, expired=True)
    url = reverse("apply_resume", kwargs={"school_slug": SINGLE_SLUG, "token": draft.token})
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"expired" in resp.content.lower()


@pytest.mark.django_db
def test_resume_view_already_submitted_shows_submitted_page(client):
    school = _make_school(SINGLE_SLUG)
    draft = _make_draft(school, submitted=True)
    url = reverse("apply_resume", kwargs={"school_slug": SINGLE_SLUG, "token": draft.token})
    resp = client.get(url)
    assert resp.status_code == 200
    assert b"already submitted" in resp.content.lower()


@pytest.mark.django_db
def test_resume_view_wrong_school_returns_404(client):
    other_school = SchoolFactory()
    draft = _make_draft(other_school)
    url = reverse("apply_resume", kwargs={"school_slug": SINGLE_SLUG, "token": draft.token})
    # This draft belongs to other_school, not SINGLE_SLUG school → 404
    resp = client.get(url)
    assert resp.status_code == 404


@pytest.mark.django_db
def test_resume_view_token_wins_over_stale_session(client):
    school = _make_school(SINGLE_SLUG)
    stale_draft = _make_draft(school, data={"first_name": "Stale"})
    fresh_draft = _make_draft(school, data={"first_name": "Fresh"}, email="fresh@example.com")

    # Plant stale draft in session
    session = client.session
    session[f"apply_draft_id:{SINGLE_SLUG}"] = stale_draft.pk
    session.save()

    # Resume with fresh token
    url = reverse("apply_resume", kwargs={"school_slug": SINGLE_SLUG, "token": fresh_draft.token})
    client.get(url, follow=True)

    # Session must now point to fresh draft
    assert client.session.get(f"apply_draft_id:{SINGLE_SLUG}") == fresh_draft.pk


# ---------------------------------------------------------------------------
# 12–13. Single-form: submit lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_final_submit_marks_draft_submitted_does_not_delete(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = _make_school(SINGLE_SLUG)
    draft = _make_draft(school, email="alice@example.com",
                        data={"first_name": "Alice"})
    # Put draft in session
    session = client.session
    session[f"apply_draft_id:{SINGLE_SLUG}"] = draft.pk
    session.save()

    url = reverse("apply", kwargs={"school_slug": SINGLE_SLUG})
    # Submit valid full form
    from core.services.config_loader import load_school_config
    cfg = load_school_config(SINGLE_SLUG)
    from core.tests.test_apply_flow import _build_valid_post_data
    post_data = _build_valid_post_data(cfg.form)
    client.post(url, data=post_data)

    draft.refresh_from_db()
    assert draft.submitted_at is not None  # marked submitted
    assert DraftSubmission.objects.filter(pk=draft.pk).exists()  # NOT deleted
    assert Submission.objects.filter(school=school).exists()


@pytest.mark.django_db
def test_stale_session_draft_id_does_not_break_get(client):
    school = _make_school(SINGLE_SLUG)
    # Plant a nonexistent draft id in session
    session = client.session
    session[f"apply_draft_id:{SINGLE_SLUG}"] = 999999
    session.save()

    url = reverse("apply", kwargs={"school_slug": SINGLE_SLUG})
    resp = client.get(url)
    assert resp.status_code == 200  # no crash, stale session silently cleared


# ---------------------------------------------------------------------------
# 14–15. Save & resume email throttle
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_resume_email_sent_on_first_save(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEFAULT_FROM_EMAIL = "noreply@test.com"
    settings.BASE_URL = "http://testserver"
    mail.outbox.clear()
    _make_school(SINGLE_SLUG)
    url = reverse("apply", kwargs={"school_slug": SINGLE_SLUG})
    client.post(url, data={**_SINGLE_FORM_DATA, "_action": "save_draft"})
    assert len(mail.outbox) == 1


@pytest.mark.django_db
def test_resume_email_throttled_on_rapid_resave(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.DEFAULT_FROM_EMAIL = "noreply@test.com"
    settings.BASE_URL = "http://testserver"
    mail.outbox.clear()
    _make_school(SINGLE_SLUG)
    url = reverse("apply", kwargs={"school_slug": SINGLE_SLUG})
    post_data = {**_SINGLE_FORM_DATA, "_action": "save_draft"}
    # First save: email sent
    client.post(url, data=post_data)
    assert len(mail.outbox) == 1
    # Second save immediately after: throttled
    client.post(url, data=post_data)
    assert len(mail.outbox) == 1  # still 1, not 2


# ---------------------------------------------------------------------------
# 16–19. Multi-form tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_multi_step1_creates_draft(client):
    school = _make_school(MULTI_SLUG)
    url = reverse("apply_form", kwargs={"school_slug": MULTI_SLUG, "form_key": "enrollment"})
    resp = client.post(url, data=_MULTI_STEP1_DATA)
    assert resp.status_code == 302
    assert DraftSubmission.objects.filter(school=school, form_key="multi").count() == 1
    draft = DraftSubmission.objects.get(school=school)
    assert draft.last_form_key == "enrollment"
    assert client.session.get(f"apply_draft_id:{MULTI_SLUG}") == draft.pk


@pytest.mark.django_db
def test_multi_step2_merges_into_draft_and_updates_last_form_key(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = _make_school(MULTI_SLUG)
    # Step 1
    url1 = reverse("apply_form", kwargs={"school_slug": MULTI_SLUG, "form_key": "enrollment"})
    client.post(url1, data=_MULTI_STEP1_DATA)
    draft = DraftSubmission.objects.get(school=school)

    # Step 2
    url2 = reverse("apply_form", kwargs={"school_slug": MULTI_SLUG, "form_key": "address"})
    client.post(url2, data=_MULTI_STEP2_DATA)

    draft.refresh_from_db()
    assert draft.last_form_key == "address"
    assert draft.data.get("first_name") == "Bob"  # step 1 data preserved
    assert draft.data.get("street") == "123 Main St"  # step 2 data merged


@pytest.mark.django_db
def test_multi_final_step_converts_draft_to_submission(client, settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    school = _make_school(MULTI_SLUG)

    from core.services.config_loader import load_school_config
    from core.tests.test_apply_flow import _build_valid_post_data
    cfg = load_school_config(MULTI_SLUG)
    forms = cfg.raw.get("forms", {})
    form_keys = list(forms.keys())

    # Walk through all steps
    for i, fk in enumerate(form_keys):
        form_cfg = forms[fk].get("form", {})
        step_data = _build_valid_post_data(form_cfg)
        url = reverse("apply_form", kwargs={"school_slug": MULTI_SLUG, "form_key": fk})
        resp = client.post(url, data=step_data)
        if i < len(form_keys) - 1:
            assert resp.status_code == 302

    # After final step: Submission created, draft marked submitted
    assert Submission.objects.filter(school=school).count() == 1
    draft = DraftSubmission.objects.get(school=school)
    assert draft.submitted_at is not None


@pytest.mark.django_db
def test_multi_resume_rehydrates_session_and_redirects_to_next_step(client):
    school = _make_school(MULTI_SLUG)
    # Draft completed step 1 (enrollment), should resume at step 2 (address)
    draft = _make_draft(school, form_key="multi", last_form_key="enrollment")
    url = reverse("apply_resume", kwargs={"school_slug": MULTI_SLUG, "token": draft.token})
    resp = client.get(url)
    assert resp.status_code == 302
    assert "address" in resp["Location"]
    assert client.session.get(f"apply_draft_id:{MULTI_SLUG}") == draft.pk


# ---------------------------------------------------------------------------
# 20–21. Admin validation fix
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_edit_warns_not_blocks_on_missing_required_field(monkeypatch):
    """
    changeform_view must queue a warning and return 200 (not redirect) when required
    fields are absent from the POST — admin is trusted, save is not blocked.
    """
    from core.admin.submissions import SubmissionAdmin
    from core.tests.factories import SchoolFactory, SubmissionFactory, UserFactory
    from django.contrib.admin.sites import site as admin_site
    from django.test import RequestFactory
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.contrib.messages import get_messages
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.http import HttpResponse

    school = SchoolFactory(slug=SINGLE_SLUG)
    sub = SubmissionFactory(school=school, data={"student_first_name": "Alice"})
    sub.form_key = "default"
    sub.save()

    # Minimal config with one required field that is missing from the POST
    class _Cfg:
        form = {
            "sections": [{
                "title": "Main",
                "fields": [
                    {"key": "student_first_name", "label": "First Name", "type": "text", "required": True},
                ],
            }]
        }
        raw = {}

    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: _Cfg())

    ma = SubmissionAdmin(model=Submission, admin_site=admin_site)
    user = UserFactory.create(is_superuser=True, is_staff=True)
    # POST with empty body — required field is absent
    req = RequestFactory().post(f"/admin/core/submission/{sub.pk}/change/", data={})
    req.user = user
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    setattr(req, "_messages", FallbackStorage(req))

    # Patch ModelAdmin.changeform_view to avoid full Django admin form rendering
    monkeypatch.setattr(
        "django.contrib.admin.ModelAdmin.changeform_view",
        lambda self, req, *a, **kw: HttpResponse("ok", status=200),
    )

    resp = ma.changeform_view(req, object_id=str(sub.pk))

    # Must NOT redirect — admin falls through to save even with missing fields
    assert resp.status_code == 200

    # A warning must be queued for the missing required field
    msgs = [m.message for m in get_messages(req)]
    assert any("First Name" in m for m in msgs), f"Expected warning about First Name in: {msgs}"


@pytest.mark.django_db
def test_admin_edit_saves_despite_warnings(monkeypatch):
    """Admin save must succeed (200, not redirect) even when required fields are missing."""
    from core.admin.submissions import SubmissionAdmin
    from core.tests.factories import SchoolFactory, SubmissionFactory, UserFactory
    from django.contrib.admin.sites import site as admin_site
    from django.test import RequestFactory
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.http import HttpResponse

    school = SchoolFactory(slug=SINGLE_SLUG)
    sub = SubmissionFactory(school=school, data={"student_first_name": "Alice"})
    sub.form_key = "default"
    sub.save()

    class _Cfg:
        form = {
            "sections": [{
                "title": "Main",
                "fields": [
                    {"key": "student_first_name", "label": "First Name", "type": "text", "required": True},
                    {"key": "contact_email", "label": "Email", "type": "email", "required": True},
                ],
            }]
        }
        raw = {}

    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: _Cfg())

    ma = SubmissionAdmin(model=Submission, admin_site=admin_site)
    user = UserFactory.create(is_superuser=True, is_staff=True)
    req = RequestFactory().post(f"/admin/core/submission/{sub.pk}/change/", data={})
    req.user = user
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    setattr(req, "_messages", FallbackStorage(req))

    # Patch ModelAdmin.changeform_view — simulates the save succeeding
    save_called = []
    def fake_super_changeform(self, req, *a, **kw):
        save_called.append(True)
        return HttpResponse("saved", status=200)

    monkeypatch.setattr(
        "django.contrib.admin.ModelAdmin.changeform_view",
        fake_super_changeform,
    )

    resp = ma.changeform_view(req, object_id=str(sub.pk))

    # super().changeform_view must have been reached — admin is not blocked
    assert save_called, "Expected super().changeform_view to be called (save not blocked)"
    assert resp.status_code == 200
