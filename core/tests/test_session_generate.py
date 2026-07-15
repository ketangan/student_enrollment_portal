"""
Tests for:
- school_session_generate_view (recurring session generator)
- apply_success redirect_url (post-submit redirect via YAML)
"""
import datetime
import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from core.models import AdminAuditLog, School, SchoolAdminMembership, SchoolProgram, SchoolSession


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def school_admin(db):
    return User.objects.create_user(
        username="gen_admin", email="gen@test.com", password="testpass123", is_staff=True
    )


@pytest.fixture
def school(db):
    return School.objects.create(slug="gen-school", display_name="Gen School", plan="pro")


@pytest.fixture
def membership(db, school_admin, school):
    SchoolAdminMembership.objects.create(user=school_admin, school=school)
    return school


@pytest.fixture
def program(db, school):
    return SchoolProgram.objects.create(school=school, name="Trial Lessons", code="trial", display_order=1)


def _generate_url(school, program):
    return reverse("school_session_generate", kwargs={"school_slug": school.slug, "program_id": program.pk})


def _post(client, school, program, **overrides):
    payload = {
        "day_of_week": "1",  # Tuesday
        "time_label": "3:00 PM",
        "start_date": "2026-08-04",
        "end_date": "2026-08-25",  # 4 Tuesdays: Aug 4, 11, 18, 25
        "capacity": "6",
        "auto_enroll": "1",
    }
    payload.update(overrides)
    return client.post(_generate_url(school, program), payload)


# ── Auth ──────────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_generate_requires_login(client, school, program):
    resp = client.get(_generate_url(school, program))
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_generate_requires_school_membership(client, db, school, program):
    other = User.objects.create_user(username="other", password="x", is_staff=True)
    client.force_login(other)
    resp = client.post(_generate_url(school, program), {})
    assert resp.status_code == 404


# ── GET renders form ──────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_generate_get_renders_form(client, school_admin, membership, program):
    client.force_login(school_admin)
    resp = client.get(_generate_url(membership, program))
    assert resp.status_code == 200
    assert b"Generate recurring" in resp.content or b"Generate Recurring" in resp.content


# ── Correct session count ─────────────────────────────────────────────────────

@pytest.mark.django_db
def test_generate_creates_correct_count(client, school_admin, membership, program):
    client.force_login(school_admin)
    # Tuesdays Aug 4–25 2026 = 4 occurrences
    resp = _post(client, membership, program)
    assert resp.status_code == 302
    assert SchoolSession.objects.filter(program=program).count() == 4


@pytest.mark.django_db
def test_generate_session_names_and_dates(client, school_admin, membership, program):
    client.force_login(school_admin)
    _post(client, membership, program)
    sessions = list(SchoolSession.objects.filter(program=program).order_by("start_date"))
    assert sessions[0].name == "Tuesday 3:00 PM — Aug 4"
    assert sessions[0].start_date == datetime.date(2026, 8, 4)
    assert sessions[0].end_date == datetime.date(2026, 8, 4)
    assert sessions[3].name == "Tuesday 3:00 PM — Aug 25"


@pytest.mark.django_db
def test_generate_session_codes_are_unique(client, school_admin, membership, program):
    client.force_login(school_admin)
    _post(client, membership, program)
    codes = list(SchoolSession.objects.filter(program=program).values_list("code", flat=True))
    assert len(codes) == len(set(codes))
    assert "tue_2026_08_04" in codes


@pytest.mark.django_db
def test_generate_sets_capacity(client, school_admin, membership, program):
    client.force_login(school_admin)
    _post(client, membership, program, capacity="3")
    assert SchoolSession.objects.filter(program=program, capacity=3).count() == 4


@pytest.mark.django_db
def test_generate_no_capacity_leaves_unlimited(client, school_admin, membership, program):
    client.force_login(school_admin)
    _post(client, membership, program, capacity="")
    assert SchoolSession.objects.filter(program=program, capacity__isnull=True).count() == 4


# ── Validation errors ─────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_generate_end_before_start_errors(client, school_admin, membership, program):
    client.force_login(school_admin)
    resp = _post(client, membership, program, start_date="2026-08-25", end_date="2026-08-04")
    assert resp.status_code == 200
    assert SchoolSession.objects.filter(program=program).count() == 0


@pytest.mark.django_db
def test_generate_no_occurrences_in_range_errors(client, school_admin, membership, program):
    client.force_login(school_admin)
    # Wednesday only, range is a single Tuesday — no match
    resp = _post(client, membership, program, day_of_week="2", start_date="2026-08-04", end_date="2026-08-04")
    assert resp.status_code == 200
    assert SchoolSession.objects.filter(program=program).count() == 0


@pytest.mark.django_db
def test_generate_missing_day_errors(client, school_admin, membership, program):
    client.force_login(school_admin)
    resp = _post(client, membership, program, day_of_week="")
    assert resp.status_code == 200
    assert SchoolSession.objects.filter(program=program).count() == 0


@pytest.mark.django_db
def test_generate_missing_time_label_errors(client, school_admin, membership, program):
    client.force_login(school_admin)
    resp = _post(client, membership, program, time_label="")
    assert resp.status_code == 200
    assert SchoolSession.objects.filter(program=program).count() == 0


# ── Duplicate skipping ────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_generate_skips_existing_codes(client, school_admin, membership, program):
    # Pre-create one session with the code that would be generated for Aug 4
    SchoolSession.objects.create(
        program=program, name="Existing", code="tue_2026_08_04", display_order=0
    )
    client.force_login(school_admin)
    _post(client, membership, program)
    # 4 Tuesdays, 1 already existed → 3 new + 1 existing = 4 total
    assert SchoolSession.objects.filter(program=program).count() == 4


# ── Audit log ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_generate_logs_audit_entry(client, school_admin, membership, program):
    client.force_login(school_admin)
    _post(client, membership, program)
    assert AdminAuditLog.objects.filter(
        extra__name="sessions_generated",
        extra__day="Tuesday",
    ).exists()


# ── Post-submit redirect ──────────────────────────────────────────────────────

@pytest.mark.django_db
def test_apply_success_redirects_when_redirect_url_set(client, db):
    from unittest.mock import patch, MagicMock

    school = School.objects.create(slug="redir-school", display_name="Redir School", plan="starter", is_active=True)

    raw_cfg = {"success": {"redirect_url": "https://makemusic.app/trial"}}
    mock_config = MagicMock()
    mock_config.display_name = "Redir School"
    mock_config.raw = raw_cfg
    mock_config.branding = None

    with patch("core.views_public.load_school_config", return_value=mock_config):
        # Set the session key as the submit flow would
        session = client.session
        session["_enrollify_last_form_key"] = "default"
        session.save()

        resp = client.get(reverse("apply_success", kwargs={"school_slug": "redir-school"}))

    assert resp.status_code == 302
    assert resp["Location"] == "https://makemusic.app/trial"


@pytest.mark.django_db
def test_apply_success_no_redirect_when_url_not_set(client, db):
    from unittest.mock import patch, MagicMock

    school = School.objects.create(slug="noredir-school", display_name="No Redir", plan="starter", is_active=True)

    raw_cfg = {"success": {"title": "Done!"}}
    mock_config = MagicMock()
    mock_config.display_name = "No Redir"
    mock_config.raw = raw_cfg
    mock_config.branding = None

    with patch("core.views_public.load_school_config", return_value=mock_config):
        resp = client.get(reverse("apply_success", kwargs={"school_slug": "noredir-school"}))

    assert resp.status_code == 200


@pytest.mark.django_db
def test_apply_success_per_form_redirect_overrides_global(client, db):
    from unittest.mock import patch, MagicMock

    school = School.objects.create(slug="multiredir-school", display_name="Multi Redir", plan="pro", is_active=True)

    raw_cfg = {
        "success": {"redirect_url": "https://global.example.com/"},
        "forms": {
            "trial": {
                "success": {"redirect_url": "https://makemusic.app/trial"},
                "form": {},
            }
        },
    }
    mock_config = MagicMock()
    mock_config.display_name = "Multi Redir"
    mock_config.raw = raw_cfg
    mock_config.branding = None

    with patch("core.views_public.load_school_config", return_value=mock_config):
        session = client.session
        session["_enrollify_last_form_key"] = "trial"
        session.save()

        resp = client.get(reverse("apply_success", kwargs={"school_slug": "multiredir-school"}))

    assert resp.status_code == 302
    assert resp["Location"] == "https://makemusic.app/trial"
