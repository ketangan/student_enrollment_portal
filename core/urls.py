from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    path("schools/<slug:school_slug>/apply/", views.apply_view, name="apply"),

    # must be BEFORE apply/<form_key> so "success" doesn't get captured
    path("schools/<slug:school_slug>/apply/success/", views.apply_success_view, name="apply_success"),

    path("schools/<slug:school_slug>/apply/<slug:form_key>/", views.apply_view, name="apply_form"),

    path("schools/<slug:school_slug>/admin/reports", views.school_reports_view, name="school_reports"),
    path("schools/<slug:school_slug>/reports", RedirectView.as_view(pattern_name="school_reports", permanent=False)),
]
