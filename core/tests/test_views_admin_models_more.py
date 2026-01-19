import csv
from datetime import timedelta
from django.utils import timezone
from django.test import RequestFactory
from django.urls import reverse
import pytest

from core.views import merge_branding, _can_view_school_admin_page, school_reports_view
from core.tests.factories import SchoolFactory, SubmissionFactory, UserFactory, SchoolAdminMembershipFactory
from core import admin as core_admin
from core.admin import SubmissionAdmin, UserSuperuserForm
from core.models import Submission


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

    school = SchoolFactory.create()
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
