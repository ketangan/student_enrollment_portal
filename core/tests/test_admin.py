import json
import csv
from django.contrib import admin
from django.test import RequestFactory
from django.http import Http404
from django.contrib.auth import get_user_model
import pytest

from core.admin import (
    admin_reports_hub_view,
    UserSuperuserAddForm,
    SchoolAdmin,
    SubmissionAdmin,
    PrettyJSONWidget,
    SchoolScopedUserAdmin,
    SchoolAdminMembershipAdmin,
)
from core import admin as core_admin
from core.tests.factories import (
    UserFactory,
    SchoolFactory,
    SchoolAdminMembershipFactory,
    SubmissionFactory,
)
from core.models import Submission


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
