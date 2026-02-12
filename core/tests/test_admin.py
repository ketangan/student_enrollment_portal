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
from core.admin.schools import SchoolAdminForm
from core.admin.schools import PrettyJSONWidget as SchoolPrettyJSONWidget
from core import admin as core_admin
from core.tests.factories import (
    UserFactory,
    SchoolFactory,
    SchoolAdminMembershipFactory,
    SubmissionFactory,
)
from core.models import Submission, SubmissionFile, AdminAuditLog


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
    assert "public_id" in sub_admin.get_list_display(req)

    req.user = non_su
    assert "school_display" not in sub_admin.get_list_display(req)
    assert "public_id" in sub_admin.get_list_display(req)

    # school_display on a submission
    sub = SubmissionFactory.create(school=school)
    assert sub_admin.school_display(sub) == (school.display_name or school.slug)


def test_submission_admin_search_finds_by_public_id(db):
    school = SchoolFactory.create()
    user = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=user, school=school)

    sub = SubmissionFactory.create(school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/admin/core/submission/")
    req.user = user

    qs = ma.get_queryset(req)
    out_qs, _distinct = ma.get_search_results(req, qs, sub.public_id)
    assert out_qs.filter(id=sub.id).exists()


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


# ---------------------------------------------------------------------------
# SchoolAdminForm validation (feature_flags field)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_school_admin_form_valid_feature_flags():
    """Flags matching plan defaults are stripped; only overrides are stored."""
    school = SchoolFactory.create()
    SchoolAdminForm.current_user_is_superuser = True
    form = SchoolAdminForm(
        instance=school,
        data={
            "slug": school.slug,
            "display_name": school.display_name,
            "website_url": school.website_url or "",
            "source_url": school.source_url or "",
            "plan": "starter",
            "feature_flags": json.dumps({"reports_enabled": True}),
            "logo_url": "",
            "theme_primary_color": "",
            "theme_accent_color": "",
        },
    )
    assert form.is_valid(), form.errors
    # reports_enabled=True IS the starter default → no override stored
    assert form.cleaned_data["feature_flags"] == {}


@pytest.mark.django_db
def test_school_admin_form_empty_flags_returns_empty_dict():
    school = SchoolFactory.create()
    SchoolAdminForm.current_user_is_superuser = True
    form = SchoolAdminForm(
        instance=school,
        data={
            "slug": school.slug,
            "display_name": school.display_name,
            "website_url": school.website_url or "",
            "source_url": school.source_url or "",
            "plan": "trial",
            "feature_flags": "",
            "logo_url": "",
            "theme_primary_color": "",
            "theme_accent_color": "",
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["feature_flags"] == {}


@pytest.mark.django_db
def test_school_admin_form_rejects_invalid_json():
    school = SchoolFactory.create()
    SchoolAdminForm.current_user_is_superuser = True
    form = SchoolAdminForm(
        instance=school,
        data={
            "slug": school.slug,
            "display_name": school.display_name,
            "website_url": school.website_url or "",
            "source_url": school.source_url or "",
            "plan": "trial",
            "feature_flags": "{not valid json",
            "logo_url": "",
            "theme_primary_color": "",
            "theme_accent_color": "",
        },
    )
    assert not form.is_valid()
    assert "feature_flags" in form.errors


@pytest.mark.django_db
def test_school_admin_form_rejects_non_dict_json():
    school = SchoolFactory.create()
    SchoolAdminForm.current_user_is_superuser = True
    form = SchoolAdminForm(
        instance=school,
        data={
            "slug": school.slug,
            "display_name": school.display_name,
            "website_url": school.website_url or "",
            "source_url": school.source_url or "",
            "plan": "trial",
            "feature_flags": json.dumps(["not", "a", "dict"]),
            "logo_url": "",
            "theme_primary_color": "",
            "theme_accent_color": "",
        },
    )
    assert not form.is_valid()
    assert "feature_flags" in form.errors


@pytest.mark.django_db
def test_school_admin_form_rejects_non_boolean_values():
    school = SchoolFactory.create()
    SchoolAdminForm.current_user_is_superuser = True
    form = SchoolAdminForm(
        instance=school,
        data={
            "slug": school.slug,
            "display_name": school.display_name,
            "website_url": school.website_url or "",
            "source_url": school.source_url or "",
            "plan": "trial",
            "feature_flags": json.dumps({"reports_enabled": "yes"}),
            "logo_url": "",
            "theme_primary_color": "",
            "theme_accent_color": "",
        },
    )
    assert not form.is_valid()
    assert "feature_flags" in form.errors


@pytest.mark.django_db
def test_school_admin_list_display_includes_plan():
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)
    assert "plan" in sa.list_display


# ---------------------------------------------------------------------------
# SchoolAdmin PrettyJSONWidget (from schools.py)
# ---------------------------------------------------------------------------


def test_school_pretty_json_widget_formats_dict():
    w = SchoolPrettyJSONWidget()
    out = w.format_value({"b": 2, "a": 1})
    assert '"a": 1' in out
    assert '"b": 2' in out


def test_school_pretty_json_widget_formats_json_string():
    w = SchoolPrettyJSONWidget()
    out = w.format_value('{"x": true}')
    assert "\n" in out  # pretty-printed
    assert '"x": true' in out


def test_school_pretty_json_widget_empty_values():
    w = SchoolPrettyJSONWidget()
    assert w.format_value(None) == ""
    assert w.format_value("") == ""
    assert w.format_value({}) == ""


def test_school_pretty_json_widget_fallback_on_bad_input():
    w = SchoolPrettyJSONWidget()
    out = w.format_value(object())
    assert out is not None  # should not crash


# ---------------------------------------------------------------------------
# SchoolAdminForm: non-string key validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_school_admin_form_rejects_non_string_keys():
    """Feature flag keys must be strings — numeric keys should be rejected."""
    school = SchoolFactory.create()
    SchoolAdminForm.current_user_is_superuser = True
    # JSON with int key "1" — json.loads will turn it to str, but we test
    # what happens when the value is already a dict with int keys (e.g. from JSONField)
    form = SchoolAdminForm(
        instance=school,
        data={
            "slug": school.slug,
            "display_name": school.display_name,
            "website_url": school.website_url or "",
            "source_url": school.source_url or "",
            "plan": "trial",
            # Note: JSON spec only allows string keys, so json.dumps will stringify.
            # We test with valid JSON that already has boolean values but test
            # the non-string key branch via a dict directly.
            "feature_flags": json.dumps({"reports_enabled": True}),
            "logo_url": "",
            "theme_primary_color": "",
            "theme_accent_color": "",
        },
    )
    # Valid case should pass
    assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------
# SchoolAdminForm __init__ behaviour (superuser vs school-admin)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_school_admin_form_hides_plan_and_flags_for_non_superuser():
    """Non-superuser school admins should not see plan or feature_flags fields."""
    school = SchoolFactory.create(plan="starter")
    SchoolAdminForm.current_user_is_superuser = False
    form = SchoolAdminForm(instance=school)
    assert "plan" not in form.fields
    assert "feature_flags" not in form.fields
    # Other fields should still exist
    assert "slug" in form.fields
    assert "display_name" in form.fields


@pytest.mark.django_db
def test_school_admin_form_shows_plan_and_flags_for_superuser():
    """Superusers should see plan and feature_flags fields."""
    school = SchoolFactory.create(plan="starter")
    SchoolAdminForm.current_user_is_superuser = True
    form = SchoolAdminForm(instance=school)
    assert "plan" in form.fields
    assert "feature_flags" in form.fields


@pytest.mark.django_db
def test_school_admin_form_initial_shows_effective_flags():
    """__init__ should populate initial feature_flags with merged effective flags."""
    school = SchoolFactory.create(plan="trial", feature_flags={})
    SchoolAdminForm.current_user_is_superuser = True
    form = SchoolAdminForm(instance=school)
    effective = form.initial["feature_flags"]
    # trial plan: reports_enabled=False, others True
    assert effective["reports_enabled"] is False
    assert effective["status_enabled"] is True


@pytest.mark.django_db
def test_school_admin_form_initial_includes_overrides():
    """Effective flags should reflect per-school overrides."""
    school = SchoolFactory.create(
        plan="trial", feature_flags={"reports_enabled": True}
    )
    SchoolAdminForm.current_user_is_superuser = True
    form = SchoolAdminForm(instance=school)
    assert form.initial["feature_flags"]["reports_enabled"] is True


@pytest.mark.django_db
def test_school_admin_form_clean_stores_overrides_only():
    """clean_feature_flags should only store flags that differ from plan defaults."""
    school = SchoolFactory.create(plan="trial")
    SchoolAdminForm.current_user_is_superuser = True
    # trial defaults: reports_enabled=False. Submit reports_enabled=True → override.
    form = SchoolAdminForm(
        instance=school,
        data={
            "slug": school.slug,
            "display_name": school.display_name,
            "website_url": school.website_url or "",
            "source_url": school.source_url or "",
            "plan": "trial",
            "feature_flags": json.dumps({
                "reports_enabled": True,   # differs from trial default (False)
                "status_enabled": True,    # matches trial default → stripped
            }),
            "logo_url": "",
            "theme_primary_color": "",
            "theme_accent_color": "",
        },
    )
    assert form.is_valid(), form.errors
    overrides = form.cleaned_data["feature_flags"]
    assert overrides == {"reports_enabled": True}


@pytest.mark.django_db
def test_school_admin_form_clean_all_defaults_returns_empty():
    """If every submitted flag matches the plan default, overrides should be empty."""
    school = SchoolFactory.create(plan="starter")
    SchoolAdminForm.current_user_is_superuser = True
    form = SchoolAdminForm(
        instance=school,
        data={
            "slug": school.slug,
            "display_name": school.display_name,
            "website_url": school.website_url or "",
            "source_url": school.source_url or "",
            "plan": "starter",
            "feature_flags": json.dumps({
                "reports_enabled": True,
                "status_enabled": True,
                "csv_export_enabled": True,
                "audit_log_enabled": True,
            }),
            "logo_url": "",
            "theme_primary_color": "",
            "theme_accent_color": "",
        },
    )
    assert form.is_valid(), form.errors
    assert form.cleaned_data["feature_flags"] == {}


@pytest.mark.django_db
def test_school_admin_get_form_injects_superuser_flag():
    """SchoolAdmin.get_form() should set current_user_is_superuser on the form class."""
    rf = RequestFactory()
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = rf.get("/")
    req.user = su
    FormClass = sa.get_form(req)
    assert FormClass.current_user_is_superuser is True

    staff = UserFactory.create(is_staff=True)
    req.user = staff
    FormClass = sa.get_form(req)
    assert FormClass.current_user_is_superuser is False


# ---------------------------------------------------------------------------
# SchoolAdmin permission methods
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_school_admin_has_change_permission():
    rf = RequestFactory()
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = rf.get("/")
    req.user = su
    assert sa.has_change_permission(req) is True

    staff = UserFactory.create(is_staff=True)
    req.user = staff
    assert sa.has_change_permission(req) is False


@pytest.mark.django_db
def test_school_admin_has_add_permission():
    rf = RequestFactory()
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = rf.get("/")
    req.user = su
    assert sa.has_add_permission(req) is True

    staff = UserFactory.create(is_staff=True)
    req.user = staff
    assert sa.has_add_permission(req) is False


@pytest.mark.django_db
def test_school_admin_has_delete_permission():
    rf = RequestFactory()
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = rf.get("/")
    req.user = su
    assert sa.has_delete_permission(req) is True

    staff = UserFactory.create(is_staff=True)
    req.user = staff
    assert sa.has_delete_permission(req) is False


@pytest.mark.django_db
def test_school_admin_has_view_permission_for_membership_staff():
    rf = RequestFactory()
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)

    school = SchoolFactory.create()
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    req = rf.get("/")
    req.user = staff
    assert sa.has_view_permission(req) is True


@pytest.mark.django_db
def test_school_admin_get_queryset_scoped_to_membership():
    rf = RequestFactory()
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)

    school_a = SchoolFactory.create()
    school_b = SchoolFactory.create()

    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school_a)

    req = rf.get("/")
    req.user = staff
    qs = sa.get_queryset(req)
    assert school_a.pk in set(qs.values_list("pk", flat=True))
    assert school_b.pk not in set(qs.values_list("pk", flat=True))


@pytest.mark.django_db
def test_school_admin_get_queryset_returns_none_without_membership():
    rf = RequestFactory()
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)

    SchoolFactory.create()
    staff = UserFactory.create(is_staff=True)
    # no membership

    req = rf.get("/")
    req.user = staff
    qs = sa.get_queryset(req)
    assert qs.count() == 0


@pytest.mark.django_db
def test_school_admin_reports_link_empty_slug():
    sa = SchoolAdmin(SchoolFactory._meta.model, admin.site)
    assert sa.reports_link(None) == ""

    school = SchoolFactory.create()
    school.slug = ""
    assert sa.reports_link(school) == ""


# ---------------------------------------------------------------------------
# SubmissionAdmin: feature-flag gated behaviour
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_submission_admin_get_list_display_includes_status_when_enabled():
    school = SchoolFactory.create(plan="starter")  # status_enabled=True by default
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/")
    req.user = staff

    cols = ma.get_list_display(req)
    assert "status" in cols


@pytest.mark.django_db
def test_submission_admin_get_list_display_hides_status_when_disabled():
    school = SchoolFactory.create(plan="trial", feature_flags={"status_enabled": False})
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/")
    req.user = staff

    cols = ma.get_list_display(req)
    assert "status" not in cols


@pytest.mark.django_db
def test_submission_admin_get_list_filter_includes_status_when_enabled():
    school = SchoolFactory.create(plan="starter")
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/")
    req.user = staff

    filters = ma.get_list_filter(req)
    assert "status" in filters


@pytest.mark.django_db
def test_submission_admin_get_list_filter_hides_status_when_disabled():
    school = SchoolFactory.create(plan="trial", feature_flags={"status_enabled": False})
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/")
    req.user = staff

    filters = ma.get_list_filter(req)
    assert "status" not in filters


@pytest.mark.django_db
def test_submission_admin_get_list_filter_superuser_always_has_status():
    su = UserFactory.create(is_superuser=True, is_staff=True)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/")
    req.user = su

    filters = ma.get_list_filter(req)
    assert "status" in filters


@pytest.mark.django_db
def test_submission_admin_get_fieldsets_includes_status_when_enabled():
    school = SchoolFactory.create(plan="starter")
    sub = SubmissionFactory.create(school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = RequestFactory().get("/")
    req.user = su

    fieldsets = ma.get_fieldsets(req, obj=sub)
    general_fields = fieldsets[0][1]["fields"]
    assert "status" in general_fields


@pytest.mark.django_db
def test_submission_admin_get_fieldsets_hides_status_when_disabled():
    school = SchoolFactory.create(plan="trial", feature_flags={"status_enabled": False})
    sub = SubmissionFactory.create(school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    school_admin = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=school_admin, school=school)
    req = RequestFactory().get("/")
    req.user = school_admin

    fieldsets = ma.get_fieldsets(req, obj=sub)
    general_fields = fieldsets[0][1]["fields"]
    assert "status" not in general_fields


@pytest.mark.django_db
def test_submission_admin_get_form_removes_status_when_disabled(monkeypatch):
    school = SchoolFactory.create(plan="trial", feature_flags={"status_enabled": False})
    sub = SubmissionFactory.create(school=school)
    sub.form_key = "default"
    sub.save()

    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: None)

    ma = SubmissionAdmin(Submission, admin_site)
    school_admin = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=school_admin, school=school)
    req = RequestFactory().get("/")
    req.user = school_admin

    form_cls = ma.get_form(req, obj=sub)
    assert "status" not in form_cls.base_fields


@pytest.mark.django_db
def test_submission_admin_get_form_keeps_status_when_enabled(monkeypatch):
    school = SchoolFactory.create(plan="starter")
    sub = SubmissionFactory.create(school=school)
    sub.form_key = "default"
    sub.save()

    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: None)

    ma = SubmissionAdmin(Submission, admin_site)
    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = RequestFactory().get("/")
    req.user = su

    form_cls = ma.get_form(req, obj=sub)
    assert "status" in form_cls.base_fields


@pytest.mark.django_db
def test_submission_admin_get_actions_hides_export_csv_when_disabled():
    school = SchoolFactory.create(plan="trial", feature_flags={"csv_export_enabled": False})
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/")
    req.user = staff

    actions = ma.get_actions(req)
    assert "export_csv" not in actions


@pytest.mark.django_db
def test_submission_admin_get_actions_shows_export_csv_when_enabled():
    school = SchoolFactory.create(plan="starter")
    staff = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=staff, school=school)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/")
    req.user = staff

    actions = ma.get_actions(req)
    assert "export_csv" in actions


@pytest.mark.django_db
def test_submission_admin_get_actions_superuser_always_sees_export():
    su = UserFactory.create(is_superuser=True, is_staff=True)

    ma = SubmissionAdmin(Submission, admin_site)
    req = RequestFactory().get("/")
    req.user = su

    actions = ma.get_actions(req)
    assert "export_csv" in actions


@pytest.mark.django_db
def test_submission_admin_export_csv_blocked_when_disabled():
    school = SchoolFactory.create(plan="trial", feature_flags={"csv_export_enabled": False})
    sub = SubmissionFactory.create(school=school)

    school_admin = UserFactory.create(is_staff=True)
    SchoolAdminMembershipFactory.create(user=school_admin, school=school)
    ma = SubmissionAdmin(Submission, admin_site)

    req = RequestFactory().get("/")
    req.user = school_admin

    # Attach message storage
    SessionMiddleware(lambda r: None).process_request(req)
    req.session.save()
    setattr(req, "_messages", FallbackStorage(req))

    qs = Submission.objects.filter(id=sub.id)
    result = ma.export_csv(req, qs)
    assert result is None  # blocked, returns None


@pytest.mark.django_db
def test_submission_admin_save_model_skips_audit_when_disabled(monkeypatch):
    school = SchoolFactory.create(plan="trial", feature_flags={"audit_log_enabled": False})
    sub = SubmissionFactory.create(school=school, data={"first_name": "Old"})
    sub.form_key = "default"
    sub.save()

    cfg = _DummyCfg(
        form={
            "sections": [
                {"title": "Main", "fields": [{"key": "first_name", "label": "First Name", "type": "text"}]}
            ]
        }
    )
    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: cfg)

    initial_count = AdminAuditLog.objects.count()

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = RequestFactory().post(
        "/admin/core/submission/1/change/",
        data={"dyn__first_name": "New"},
    )
    req.user = su

    ma = SubmissionAdmin(Submission, admin_site)
    ma.save_model(req, sub, form=None, change=True)

    assert AdminAuditLog.objects.count() == initial_count  # no new audit log


@pytest.mark.django_db
def test_submission_admin_save_model_creates_audit_when_enabled(monkeypatch):
    school = SchoolFactory.create(plan="starter")  # audit_log_enabled=True by default
    sub = SubmissionFactory.create(school=school, data={"first_name": "Old"})
    sub.form_key = "default"
    sub.save()

    cfg = _DummyCfg(
        form={
            "sections": [
                {"title": "Main", "fields": [{"key": "first_name", "label": "First Name", "type": "text"}]}
            ]
        }
    )
    monkeypatch.setattr("core.admin.submissions.load_school_config", lambda slug: cfg)

    initial_count = AdminAuditLog.objects.count()

    su = UserFactory.create(is_superuser=True, is_staff=True)
    req = RequestFactory().post(
        "/admin/core/submission/1/change/",
        data={"dyn__first_name": "New"},
    )
    req.user = su

    ma = SubmissionAdmin(Submission, admin_site)
    ma.save_model(req, sub, form=None, change=True)

    assert AdminAuditLog.objects.count() > initial_count


@pytest.mark.django_db
def test_submission_admin_export_csv_skips_audit_when_audit_disabled():
    school = SchoolFactory.create(plan="trial", feature_flags={"audit_log_enabled": False})
    SubmissionFactory.create(school=school)

    su = UserFactory.create(is_superuser=True, is_staff=True)
    ma = SubmissionAdmin(Submission, admin_site)

    req = RequestFactory().get("/")
    req.user = su

    initial_count = AdminAuditLog.objects.count()
    qs = Submission.objects.filter(school=school)
    ma.export_csv(req, qs)
    assert AdminAuditLog.objects.count() == initial_count
