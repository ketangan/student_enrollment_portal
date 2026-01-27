# core/admin/reports.py
from __future__ import annotations

from django.contrib import admin
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from core.admin.common import _is_superuser, _membership_school_id
from core.models import School


def admin_reports_hub_view(request):
    user = request.user
    if not user or not user.is_authenticated or not user.is_staff:
        raise Http404("Page not found")

    if _is_superuser(user):
        schools = School.objects.all().order_by("display_name", "slug")
        context = admin.site.each_context(request)
        context.update({"schools": schools})
        return TemplateResponse(request, "admin/reports_hub.html", context)

    school_id = _membership_school_id(user)
    if not school_id:
        raise Http404("Page not found")

    school = School.objects.filter(id=school_id).first()
    if not school:
        raise Http404("Page not found")

    return redirect(reverse("school_reports", kwargs={"school_slug": school.slug}))


_original_admin_get_urls = admin.site.get_urls


def _admin_get_urls():
    urls = _original_admin_get_urls()
    custom = [
        path("reports/", admin.site.admin_view(admin_reports_hub_view), name="reports_hub"),
    ]
    return custom + urls


admin.site.get_urls = _admin_get_urls
