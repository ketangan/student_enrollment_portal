import json
import csv
from django.contrib import admin
from django.test import RequestFactory
from django.http import Http404
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import site as admin_site
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import PermissionDenied
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.messages import get_messages
from django.contrib.sessions.middleware import SessionMiddleware

import pytest

from core.admin import (
    admin_reports_hub_view,
    UserSuperuserAddForm,
    SchoolAdmin,
    SubmissionAdmin,
    PrettyJSONWidget,
    SchoolScopedUserAdmin,
    SchoolAdminMembershipAdmin,
    SubmissionAdmin,
)
from core import admin as core_admin
from core.tests.factories import (
    UserFactory,
    SchoolFactory,
    SchoolAdminMembershipFactory,
    SubmissionFactory,
)
from core.models import Submission, SubmissionFile


class _DummyCfg:
    def __init__(self, *, form: dict, raw: dict | None = None):
        self.form = form
        self.raw = raw or {}


def test_admin_reports_hub_superuser_sees_schools(db):
    rf = RequestFactory()
    user = UserFactory.create(is_superuser=True, is_staff=True)
    # create some schools
    SchoolFactory.create_batch(3)

    request = rf.get("/admin/reports/")
    request.user = user

    resp = admin_reports_hub_view(request)
    assert resp.status_code == 200
    # TemplateResponse exposes context_data with 'schools'
    assert "schools" in getattr(resp, "context_data", {})


def test_admin_reports_hub_nonstaff_404(db):
    rf = RequestFactory()
    user = UserFactory.create(is_staff=False)
    request = rf.get("/admin/reports/")
    request.user = user

    with pytest.raises(Http404):
        admin_reports_hub_view(request)


def test_admin_reports_hub_school_admin_redirects(db):
    rf = RequestFactory()
    user = UserFactory.create(is_staff=True)
    school = SchoolFactory.create()
    SchoolAdminMembershipFactory.create(user=user, school=school)

    request = rf.get("/admin/reports/")
    request.user = user

    resp = admin_reports_hub_view(request)
    # Should be a redirect (HttpResponseRedirect subclass)
    assert resp.status_code in (301, 302)


def test_user_superuser_add_form_sets_staff(db):
    data = {
        "username": "x_unique",
        "first_name": "a",
        "last_name": "b",
        "email": "x@x.com",
        "school": None,
        "password1": "UncommonPassword!2026",
        "password2": "UncommonPassword!2026",
    }
    form = UserSuperuserAddForm(data)
    assert form.is_valid(), form.errors
    user = form.save(commit=True)
    assert user.is_staff


def test_school_admin_reports_link_and_permissions(db):
    rf = RequestFactory()
    su = UserFactory.create(is_superuser=True, is_staff=True)
    nonstaff = UserFactory.create(is_staff=False)
    school = SchoolFactory.create()

    # reports_link returns html including url
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)
    html = sa.reports_link(school)
    assert "Reports" in str(html)

    # permissions: anonymous/no user -> False
    req = RequestFactory().get("/")
    req.user = nonstaff
    assert sa.has_module_permission(req) is False


def test_school_scoped_user_admin_get_queryset_and_save_model(db):
    User = get_user_model()
    admin_instance = core_admin.SchoolScopedUserAdmin(User, admin.site)

    superuser = UserFactory.create(is_superuser=True, is_staff=True)
    school = SchoolFactory.create()

    # save_model should create membership when called by superuser
    class DummyForm:
        cleaned_data = {"school": school}

    new_user = User.objects.create_user(username="newu")
    req = RequestFactory().post("/")
    req.user = superuser

    admin_instance.save_model(req, new_user, DummyForm(), change=False)

    # membership created
    assert hasattr(new_user, "school_membership") or school.admin_memberships.filter(user=new_user).exists()


def test_submission_admin_display_and_get_list_display(db):
    su = UserFactory.create(is_superuser=True, is_staff=True)
    non_su = UserFactory.create(is_staff=True)
    school = SchoolFactory.create()
    SubmissionFactory.create_batch(3, school=school)

    sub_admin = SubmissionAdmin(SubmissionFactory._meta.model, admin.site)

    # get_list_display depends on user
    req = RequestFactory().get("/")
    req.user = su
    assert "school_display" in sub_admin.get_list_display(req)

    req.user = non_su
    assert "school_display" not in sub_admin.get_list_display(req)

    # school_display on a submission
    sub = SubmissionFactory.create(school=school)
    assert sub_admin.school_display(sub) == (school.display_name or school.slug)


def test_pretty_json_widget_formatting():
    w = PrettyJSONWidget()
    assert w.format_value({"a": 1}).strip().startswith("{")
    # JSON string input
    js = json.dumps({"b": 2})
    assert "\n" in w.format_value(js)

    # invalid input should fallback to parent (non-serializable but str-able)
    out = w.format_value(object())
    assert out is not None


def test_useradmin_get_form_and_membership_queryset(db):
    User = get_user_model()
    admin_instance = SchoolScopedUserAdmin(User, admin.site)
    rf = RequestFactory()
    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = rf.get("/")
    req.user = su

    # get_form on change should use UserSuperuserForm
    u = UserFactory.create()
    form_class = admin_instance.get_form(req, obj=u)
    assert "email" in form_class.base_fields

    # membership admin queryset: only for superuser returns qs
    mam = SchoolAdminMembershipAdmin(SchoolAdminMembershipFactory._meta.model, admin.site)
    qs = mam.get_queryset(req)
    assert qs is not None

@pytest.mark.django_db
def test_submission_admin_attachments_renders_link():
    submission = SubmissionFactory()

    SubmissionFile.objects.create(
        submission=submission,
        field_key="id_document",
        file=SimpleUploadedFile("odometer.jpg", b"abc" * 1000, content_type="image/jpeg"),
        original_name="odometer.jpg",
        content_type="image/jpeg",
        size_bytes=3000,
    )

    ma = SubmissionAdmin(submission.__class__, admin_site)
    html = ma.attachments(submission)

    assert "href=" in str(html)
    assert ">View<" in str(html)


def test_submission_admin_save_model_updates_data_single_form(monkeypatch, db):
    school = SchoolFactory.create()
    submission = SubmissionFactory.create(
        school=school,
        data={"first_name": "Old", "keep_me": "yes"},
    )
    submission.form_key = "default"
    submission.save()

    cfg = _DummyCfg(
        form={
            "sections": [
                {
                    "title": "Main",
                    "fields": [
                        {"key": "first_name", "label": "First Name", "type": "text"},
                        {"key": "age", "type": "number"},
                    ],
                }
            ]
        }
    )
    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: cfg)

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = RequestFactory().post(
        "/admin/core/submission/1/change/",
        data={"dyn__first_name": "New", "dyn__age": "11"},
    )
    req.user = su

    ma = SubmissionAdmin(Submission, admin_site)
    ma.save_model(req, submission, form=None, change=True)

    submission.refresh_from_db()
    assert submission.data["first_name"] == "New"
    assert submission.data["age"] == 11.0
    # untouched keys remain
    assert submission.data["keep_me"] == "yes"


def test_submission_admin_save_model_updates_data_multi_form_multi(monkeypatch, db):
    school = SchoolFactory.create()
    submission = SubmissionFactory.create(
        school=school,
        data={"keep_me": "yes", "a1": "old-a", "b1": "old-b"},
    )
    submission.form_key = "multi"
    submission.save()

    form_a = {"sections": [{"title": "A", "fields": [{"key": "a1", "label": "A One", "type": "text"}]}]}
    form_b = {"sections": [{"title": "B", "fields": [{"key": "b1", "label": "B One", "type": "text"}]}]}
    raw_forms = {"step_a": {"form": form_a}, "step_b": {"form": form_b}}
    cfg = _DummyCfg(form=form_a, raw={"forms": raw_forms})
    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: cfg)

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = RequestFactory().post(
        "/admin/core/submission/1/change/",
        data={"dyn__a1": "new-a", "dyn__b1": "new-b"},
    )
    req.user = su

    ma = SubmissionAdmin(Submission, admin_site)
    ma.save_model(req, submission, form=None, change=True)

    submission.refresh_from_db()
    assert submission.data["a1"] == "new-a"
    assert submission.data["b1"] == "new-b"
    assert submission.data["keep_me"] == "yes"


def test_submission_admin_log_change_formats_updated_labels(monkeypatch, db):
    school = SchoolFactory.create()
    submission = SubmissionFactory.create(school=school, data={"first_name": "Old"})
    submission.form_key = "default"
    submission.save()

    cfg = _DummyCfg(
        form={
            "sections": [
                {"title": "Main", "fields": [{"key": "first_name", "label": "First Name", "type": "text"}]}
            ]
        }
    )
    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: cfg)

    captured = {}

    def _capture(self, request, obj, message):
        captured["message"] = message
        return None

    monkeypatch.setattr(admin.ModelAdmin, "log_change", _capture)

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = RequestFactory().post("/admin/")
    req.user = su

    # simulate change snapshot + new data
    req._old_submission_data = {"first_name": "Old"}
    submission.data = {"first_name": "New"}

    ma = SubmissionAdmin(Submission, admin_site)
    ma.log_change(req, submission, message="Changed")
    assert captured["message"] == "Updated: First Name"


def test_submission_admin_permissions_and_queryset_scoping(db):
    school1 = SchoolFactory.create()
    school2 = SchoolFactory.create()
    sub1 = SubmissionFactory.create(school=school1)
    SubmissionFactory.create(school=school2)

    ma = SubmissionAdmin(Submission, admin_site)
    rf = RequestFactory()

    # Non-staff with membership still cannot access module (requires staff)
    u = UserFactory.create(is_staff=False)
    SchoolAdminMembershipFactory.create(user=u, school=school1)
    u.is_staff = False
    u.save(update_fields=["is_staff"])
    req = rf.get("/")
    req.user = u
    assert ma.has_module_permission(req) is False

    # Staff with membership can view list and only their school's queryset
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school1)
    req.user = staff
    assert ma.has_module_permission(req) is True
    assert ma.has_view_permission(req, obj=None) is True

    qs = ma.get_queryset(req)
    assert list(qs.values_list("id", flat=True)) == [sub1.id]

    # Object view permission: only same school
    assert ma.has_view_permission(req, obj=sub1) is True
    other = Submission.objects.exclude(pk=sub1.pk).first()
    assert ma.has_view_permission(req, obj=other) is False


def test_submission_admin_change_view_denies_wrong_school(db):
    school1 = SchoolFactory.create()
    school2 = SchoolFactory.create()
    sub = SubmissionFactory.create(school=school2)

    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school1)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/")
    req.user = staff

    with pytest.raises(PermissionDenied):
        ma.change_view(req, str(sub.id))


def test_submission_admin_attachments_empty_and_program_name_no_config(monkeypatch, db):
    school = SchoolFactory.create()
    sub = SubmissionFactory.create(school=school)

    ma = SubmissionAdmin(Submission, admin_site)

    # attachments with no files
    assert ma.attachments(sub) == "—"

    # program_name falls back to submission.program_display_name when no config
    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: None)
    assert ma.program_name(sub) == sub.program_display_name()


def test_submission_admin_changeform_view_required_fields_redirects_with_message(monkeypatch, db):
    school = SchoolFactory.create()
    sub = SubmissionFactory.create(school=school, data={"first_name": "Existing"})
    sub.form_key = "default"
    sub.save()

    cfg = _DummyCfg(
        form={
            "sections": [
                {
                    "title": "Main",
                    "fields": [
                        {"key": "first_name", "label": "First Name", "type": "text", "required": True},
                    ],
                }
            ]
        }
    )
    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: cfg)

    ma = SubmissionAdmin(Submission, admin_site)

    user = UserFactory.create(is_superuser=True, is_staff=True)
    req = RequestFactory().post(f"/admin/core/submission/{sub.id}/change/", data={})
    req.user = user

    # Enable messages framework for RequestFactory requests
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    setattr(req, "_messages", FallbackStorage(req))

    resp = ma.changeform_view(req, object_id=str(sub.id))
    # Missing required dyn__first_name should redirect back to the same path
    assert resp.status_code in (301, 302)
    assert resp["Location"].endswith(req.path)

    msgs = [m.message for m in get_messages(req)]
    assert "First Name is required." in msgs
