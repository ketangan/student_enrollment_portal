import csv
import pytest
from datetime import timedelta
from django.utils import timezone
from django.test import RequestFactory
from django.urls import reverse
from django.http import Http404
from django.core.files.uploadedfile import SimpleUploadedFile

from core.views import merge_branding, _can_view_school_admin_page, school_reports_view
from core.tests.factories import SchoolFactory, SubmissionFactory, UserFactory, SchoolAdminMembershipFactory
from core import admin as core_admin
from core.admin import SubmissionAdmin, UserSuperuserForm
from core.models import Submission
from core.models import SubmissionFile


class DummyConfig:
    def __init__(self):
        self.display_name = "Dummy School"
        self.form = {}
        self.raw = {}
        self.branding = {}


class DummyMultiConfig:
    def __init__(self):
        self.display_name = "Dummy Multi School"
        self.branding = {}
        self.raw = {
            "school": {"slug": "dummy-multi"},
            "forms": {
                "step1": {"form": {"sections": [{"fields": [{"key": "first_name", "type": "text"}]}]}},
                "step2": {"form": {"sections": [{"fields": [{"key": "last_name", "type": "text"}]}]}},
            },
        }
        # `config.form` is the safe default (first form)
        self.form = self.raw["forms"]["step1"]["form"]


def test_apply_view_get_creates_school_and_renders(client, monkeypatch, db):
    monkeypatch.setattr("core.views.load_school_config", lambda slug: DummyConfig())

    school_slug = "test-school-xyz"
    url = reverse("apply", kwargs={"school_slug": school_slug})

    resp = client.get(url)
    assert resp.status_code == 200
    assert b"apply_form" in resp.content or resp.context and "school" in resp.context


def test_apply_view_multi_default_redirects_to_first_form(client, monkeypatch, db):
    monkeypatch.setattr("core.views.load_school_config", lambda slug: DummyMultiConfig())

    school_slug = "dummy-multi"
    # Pre-create school on "pro" plan so multi_form_enabled is active.
    SchoolFactory.create(slug=school_slug, plan="pro")

    resp = client.get(reverse("apply", kwargs={"school_slug": school_slug}))
    assert resp.status_code in (301, 302)
    assert resp["Location"].endswith(reverse("apply_form", kwargs={"school_slug": school_slug, "form_key": "step1"}))


def test_apply_success_view_404_when_no_config(client, monkeypatch):
    monkeypatch.setattr("core.views.load_school_config", lambda slug: None)
    url = reverse("apply_success", kwargs={"school_slug": "nope"})
    resp = client.get(url)
    assert resp.status_code == 404


def test_apply_success_view_normalizes_next_steps_and_contact(client, monkeypatch):
    cfg = DummyConfig()
    cfg.display_name = "Configured School"
    cfg.branding = {"theme": {"primary_color": "#123456"}}
    cfg.raw = {
        "school": {"slug": "configured"},
        "success": {
            "title": "Done",
            "message": "Thanks!",
            "next_steps": "Call us",
            "contact": {"email": "help@example.com"},
            "hours": "9-5",
            "response_time": "1 day",
        },
    }
    monkeypatch.setattr("core.views.load_school_config", lambda slug: cfg)

    resp = client.get(reverse("apply_success", kwargs={"school_slug": "configured"}))
    assert resp.status_code == 200
    assert resp.context["success_title"] == "Done"
    assert resp.context["success_message"] == "Thanks!"
    assert resp.context["next_steps"] == ["Call us"]
    assert resp.context["contact_email"] == "help@example.com"
    assert resp.context["hours"] == "9-5"
    assert resp.context["response_time"] == "1 day"


def test_school_reports_export_csv(client, monkeypatch, db):
    # Prepare config and data
    monkeypatch.setattr("core.views.load_school_config", lambda slug: DummyConfig())

    school = SchoolFactory.create(plan="starter")
    SubmissionFactory.create_batch(2, school=school)

    user = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=user, school=school)

    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url, {"export": "1"})

    assert resp.status_code == 200
    assert resp["Content-Type"] == "text/csv"
    # header includes a filename ending in .csv (may include quotes)
    assert ".csv" in resp.get("Content-Disposition", "")


def test_school_reports_range_defaults_and_none_label_in_csv(client, monkeypatch, db):
    monkeypatch.setattr("core.views.load_school_config", lambda slug: DummyConfig())

    school = SchoolFactory.create(plan="starter")
    # No program keys => program_display_name() == "" => should become "No program selected" in export
    SubmissionFactory.create(school=school, data={"first_name": "Alice"})

    user = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=user, school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})

    # invalid range falls back to 30
    resp = client.get(url, {"range": "999"})
    assert resp.status_code == 200
    assert resp.context["range_days"] == 30

    # export should include the none label (not blank)
    resp2 = client.get(url, {"export": "1"})
    assert resp2.status_code == 200
    rows = list(csv.reader(resp2.content.decode("utf-8").splitlines()))
    assert rows[0][:5] == ["application_id", "created_at", "status", "student_name", "program"]
    assert any(r[2] == "New" for r in rows[1:])
    assert any(r[4] == "No program selected" for r in rows[1:])


def test_admin_download_submission_file_permissions_and_filename(client, db):
    school1 = SchoolFactory.create()
    school2 = SchoolFactory.create()
    submission = SubmissionFactory.create(school=school1)

    sf = SubmissionFile.objects.create(
        submission=submission,
        field_key="id_document",
        file=SimpleUploadedFile("stored__file.txt", b"hello", content_type="text/plain"),
        original_name="original.txt",
        content_type="text/plain",
        size_bytes=5,
    )

    # Wrong-school staff should get 404
    staff_wrong = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff_wrong, school=school2)
    client.force_login(staff_wrong)
    resp = client.get(reverse("admin_download_submission_file", kwargs={"file_id": sf.id}))
    assert resp.status_code == 404

    # Right-school staff can download
    staff_right = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff_right, school=school1)
    client.force_login(staff_right)
    resp2 = client.get(reverse("admin_download_submission_file", kwargs={"file_id": sf.id}))
    assert resp2.status_code == 200
    assert "original.txt" in resp2.get("Content-Disposition", "")

    # Missing file path should 404
    SubmissionFile.objects.filter(pk=sf.pk).update(file="")
    resp3 = client.get(reverse("admin_download_submission_file", kwargs={"file_id": sf.id}))
    assert resp3.status_code == 404


def test_merge_branding_variants():
    # empty input returns defaults
    out = merge_branding(None)
    assert "theme" in out and "primary_color" in out["theme"]

    # partial theme overrides
    inp = {"theme": {"primary_color": "#123456"}}
    out2 = merge_branding(inp)
    assert out2["theme"]["primary_color"] == "#123456"


def test__can_view_school_admin_page_variants(db):
    school = SchoolFactory.create()
    user = UserFactory.create(is_staff=False)
    req = RequestFactory().get("/")
    req.user = user
    assert not _can_view_school_admin_page(req, school)

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req.user = su
    assert _can_view_school_admin_page(req, school)

    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)
    req.user = staff
    assert _can_view_school_admin_page(req, school)


def test_school_reports_metrics_and_filters(client, monkeypatch, db):
    # fix now for deterministic since calculations
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)

    school = SchoolFactory.create(plan="starter")
    # create submissions with different class_name values
    SubmissionFactory.create(school=school, data={"class_name": "A"})
    SubmissionFactory.create(school=school, data={"class_name": "B"})
    SubmissionFactory.create(school=school, data={})

    user = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=user, school=school)
    client.force_login(user)

    url = reverse("school_reports", kwargs={"school_slug": school.slug})
    resp = client.get(url)
    assert resp.status_code == 200
    # context should have totals and program_rows
    assert resp.context["total"] == 3
    assert isinstance(resp.context["program_rows"], list)


def test_submission_admin_export_csv_and_search(db):
    su = UserFactory.create(is_superuser=True, is_staff=True)
    school = SchoolFactory.create()
    s1 = SubmissionFactory.create(school=school, data={"first_name": "Alice", "class_name": "Class1"})
    s2 = SubmissionFactory.create(school=school, data={"first_name": "Bob", "class_name": "Class2"})

    sub_admin = SubmissionAdmin(Submission, core_admin.admin.site)

    req = RequestFactory().get("/")
    req.user = su

    # export_csv: call as action
    qs = Submission.objects.filter(id__in=[s1.id, s2.id])
    resp = sub_admin.export_csv(req, qs)
    assert resp["Content-Type"] == "text/csv"
    text = resp.content.decode("utf-8")
    # basic CSV structure: has header and at least two rows
    rows = list(csv.reader(text.splitlines()))
    assert len(rows) >= 3

    # get_search_results: search by student name
    base_qs = Submission.objects.all()
    results, distinct = sub_admin.get_search_results(req, base_qs, "alice")
    assert s1.id in list(results.values_list("id", flat=True))


def test_user_superuser_form_initial_school(db):
    # create existing user with membership to exercise form init branch
    user = UserFactory.create()
    school = SchoolFactory.create()
    SchoolAdminMembershipFactory.create(user=user, school=school)
    form = UserSuperuserForm(instance=user)
    # initial school should be set (no exception)
    assert "school" in form.fields


def test_models_student_and_program_display(db):
    # applicant_name branch
    school = SchoolFactory.create(slug="torrance-sister-city-association")
    sub = SubmissionFactory.create(school=school, data={"applicant_name": "Friend"})
    assert sub.student_display_name() == "Friend"
    # TSCA program special-case
    assert sub.program_display_name() == "Student Exchange"


def test_program_display_name_variants(monkeypatch, db):
    # class_name
    school = SchoolFactory.create(slug="some-school")
    s = SubmissionFactory.create(school=school, data={"class_name": "Ballet 101"})
    assert s.program_display_name() == "Ballet 101"

    # dance_style + skill_level + label map via load_school_config
    cfg = type("C", (), {"form": {"sections": [{"fields": [{"key": "dance_style", "type": "select", "options": [{"value": "b", "label": "Ballet"}],}, {"key": "skill_level", "type": "select", "options": [{"value": "beg", "label": "Beginner"}]}] }]}})
    monkeypatch.setattr("core.views.load_school_config", lambda slug: cfg)

    school2 = SchoolFactory.create(slug="label-school")
    s2 = SubmissionFactory.create(school=school2, data={"dance_style": "b", "skill_level": "beg"})
    # program_display_name should use resolve_label -> "Ballet (Beginner)"
    assert "(" in s2.program_display_name(label_map={"dance_style": {"b": "Ballet"}, "skill_level": {"beg": "Beginner"}})
