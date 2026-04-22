from django.urls import path
from django.views.generic import RedirectView
from . import views
from .views_billing import stripe_webhook
from . import views_demo

urlpatterns = [
    # Demo pages (public, no auth)
    path("demo/<slug:demo_slug>/", views_demo.demo_index, name="demo_index"),
    path("demo/<slug:demo_slug>/<slug:demo_name>/", views_demo.demo_detail, name="demo_detail"),

    path("schools/<slug:school_slug>/apply/", views.apply_view, name="apply"),

    # must be BEFORE apply/<form_key> so "success" and "resume" don't get captured
    path("schools/<slug:school_slug>/apply/success/", views.apply_success_view, name="apply_success"),
    path("schools/<slug:school_slug>/apply/resume/<str:token>/", views.resume_draft_view, name="apply_resume"),

    path("schools/<slug:school_slug>/apply/<slug:form_key>/", views.apply_view, name="apply_form"),

    path("schools/<slug:school_slug>/admin/reports", views.school_reports_view, name="school_reports"),
    path("schools/<slug:school_slug>/reports", RedirectView.as_view(pattern_name="school_reports", permanent=False)),

    path("schools/<slug:school_slug>/interest/", views.lead_capture_view, name="lead_capture"),
    path("schools/<slug:school_slug>/interest/success/", views.lead_capture_success_view, name="lead_capture_success"),

    # Stripe webhook (outside admin — no CSRF, no admin auth)
    path("stripe/webhook/", stripe_webhook, name="stripe_webhook"),
    path("stripe/webhook", stripe_webhook),
]
