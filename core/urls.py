from django.urls import path
from .views import apply_view, apply_success_view
from django.views.generic import RedirectView

from . import views


urlpatterns = [
    path("schools/<slug:school_slug>/apply", apply_view, name="apply"),
    path("schools/<slug:school_slug>/apply/<slug:form_key>/", views.apply_view, name="apply_form"),
    path("schools/<slug:school_slug>/apply/success", apply_success_view, name="apply_success"),

    # Reports (school-admin-only)
    path(
        "schools/<slug:school_slug>/admin/reports",
        views.school_reports_view,
        name="school_reports",
    ),

    # Nice-to-have: if you accidentally go here, it forwards you to the right URL
    path(
        "schools/<slug:school_slug>/reports",
        RedirectView.as_view(pattern_name="school_reports", permanent=False),
    ),
]
