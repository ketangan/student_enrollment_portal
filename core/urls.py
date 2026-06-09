from django.urls import path
from django.views.generic import RedirectView
from . import views
from .views_billing import stripe_webhook
from . import views_demo
from core import views_school_programs

urlpatterns = [
    # Demo pages (public, no auth)
    path("demo/<slug:demo_slug>/", views_demo.demo_index, name="demo_index"),
    path("demo/<slug:demo_slug>/<slug:demo_name>/", views_demo.demo_detail, name="demo_detail"),

    path("schools/<slug:school_slug>/apply/", views.apply_view, name="apply"),

    # must be BEFORE apply/<form_key> so "success", "resume", "pay" aren't captured as form_key
    path("schools/<slug:school_slug>/apply/success/", views.apply_success_view, name="apply_success"),
    path("schools/<slug:school_slug>/apply/resume/<str:token>/", views.resume_draft_view, name="apply_resume"),
    path("schools/<slug:school_slug>/apply/pay/<str:draft_token>/", views.apply_payment_view, name="apply_payment"),
    path("schools/<slug:school_slug>/apply/pay/<str:draft_token>/confirm/", views.apply_payment_confirm_view, name="apply_payment_confirm"),

    path("schools/<slug:school_slug>/apply/<slug:form_key>/", views.apply_view, name="apply_form"),

    path("schools/<slug:school_slug>/admin/", views.school_dashboard_view, name="school_dashboard"),
    path("schools/<slug:school_slug>/admin/submissions/", views.school_submissions_view, name="school_submissions"),
    path("schools/<slug:school_slug>/admin/submissions/export/", views.school_submission_export_view, name="school_submission_export"),
    path("schools/<slug:school_slug>/admin/submissions/export/<str:profile_name>/", views.school_submission_profile_export_view, name="school_submission_profile_export"),
    # bulk/ paths must be BEFORE <int:submission_id>/ to avoid URL collision
    path("schools/<slug:school_slug>/admin/submissions/bulk-status/", views.school_submission_bulk_status_update_view, name="school_submission_bulk_status_update"),
    path("schools/<slug:school_slug>/admin/submissions/bulk-mark-contacted/", views.school_submission_bulk_mark_contacted_view, name="school_submission_bulk_mark_contacted"),
    path("schools/<slug:school_slug>/admin/submissions/bulk-follow-up/", views.school_submission_bulk_follow_up_view, name="school_submission_bulk_follow_up"),
    path("schools/<slug:school_slug>/admin/submissions/bulk-download/", views.school_submission_bulk_download_view, name="school_submission_bulk_download"),
    path("schools/<slug:school_slug>/admin/submissions/bulk-print/", views.school_submission_bulk_print_view, name="school_submission_bulk_print"),
    # new/ must be BEFORE <int:submission_id>/ so "new" isn't captured as an int
    path("schools/<slug:school_slug>/admin/submissions/new/", views.school_submission_create_view, name="school_submission_create"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/", views.school_submission_detail_view, name="school_submission_detail"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/edit/", views.school_submission_edit_view, name="school_submission_edit"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/update/", views.school_submission_update_view, name="school_submission_update"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/status/", views.school_submission_status_update_view, name="school_submission_status_update"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/inline-status/", views.school_submission_inline_status_view, name="school_submission_inline_status"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/mark-contacted/", views.school_submission_mark_contacted_view, name="school_submission_mark_contacted"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/follow-up/", views.school_submission_follow_up_set_view, name="school_submission_follow_up_set"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/send-message/", views.school_submission_send_message_view, name="school_submission_send_message"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/resend-confirmation/", views.school_submission_resend_confirmation_view, name="school_submission_resend_confirmation"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/generate-summary/", views.school_submission_generate_summary_view, name="school_submission_generate_summary"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/public-note/", views.school_submission_post_public_note_view, name="school_submission_post_public_note"),
    path("schools/<slug:school_slug>/admin/submissions/<int:submission_id>/resend-status-link/", views.school_submission_resend_status_link_view, name="school_submission_resend_status_link"),
    # bulk/ paths must be BEFORE <int:lead_id>/ to avoid URL collision
    path("schools/<slug:school_slug>/admin/leads/bulk-status/", views.school_lead_bulk_status_update_view, name="school_lead_bulk_status_update"),
    path("schools/<slug:school_slug>/admin/leads/bulk-mark-contacted/", views.school_lead_bulk_mark_contacted_view, name="school_lead_bulk_mark_contacted"),
    path("schools/<slug:school_slug>/admin/leads/bulk-follow-up/", views.school_lead_bulk_follow_up_view, name="school_lead_bulk_follow_up"),
    path("schools/<slug:school_slug>/admin/leads/bulk-clear-follow-up/", views.school_lead_bulk_clear_follow_up_view, name="school_lead_bulk_clear_follow_up"),
    path("schools/<slug:school_slug>/admin/leads/<int:lead_id>/inline-status/", views.school_lead_inline_status_view, name="school_lead_inline_status"),
    # new/ must be BEFORE <int:lead_id>/ so "new" isn't captured as an int
    path("schools/<slug:school_slug>/admin/leads/new/", views.school_lead_create_view, name="school_lead_create"),
    path("schools/<slug:school_slug>/admin/leads/<int:lead_id>/", views.school_lead_detail_view, name="school_lead_detail"),
    path("schools/<slug:school_slug>/admin/leads/<int:lead_id>/start-enrollment/", views.school_lead_start_enrollment_view, name="school_lead_start_enrollment"),
    path("schools/<slug:school_slug>/admin/leads/<int:lead_id>/update/", views.school_lead_update_view, name="school_lead_update"),
    path("schools/<slug:school_slug>/admin/leads/<int:lead_id>/status/", views.school_lead_status_update_view, name="school_lead_status_update"),
    path("schools/<slug:school_slug>/admin/leads/<int:lead_id>/mark-contacted/", views.school_lead_mark_contacted_view, name="school_lead_mark_contacted"),
    path("schools/<slug:school_slug>/admin/leads/<int:lead_id>/send-message/", views.school_lead_send_message_view, name="school_lead_send_message"),
    path("schools/<slug:school_slug>/admin/leads/<int:lead_id>/resend-resume-link/", views.school_lead_resend_resume_link_view, name="school_lead_resend_resume_link"),
    path("schools/<slug:school_slug>/admin/leads/export/", views.school_lead_export_view, name="school_lead_export"),
    path("schools/<slug:school_slug>/admin/leads/", views.school_leads_view, name="school_leads"),
    path("schools/<slug:school_slug>/admin/programs/", views_school_programs.school_programs_list_view, name="school_programs_list"),
    path("schools/<slug:school_slug>/admin/programs/new/", views_school_programs.school_program_create_view, name="school_program_create"),
    path("schools/<slug:school_slug>/admin/programs/<int:program_id>/edit/", views_school_programs.school_program_edit_view, name="school_program_edit"),
    path("schools/<slug:school_slug>/admin/programs/<int:program_id>/deactivate/", views_school_programs.school_program_deactivate_view, name="school_program_deactivate"),
    path("schools/<slug:school_slug>/admin/programs/<int:program_id>/activate/", views_school_programs.school_program_activate_view, name="school_program_activate"),
    path("schools/<slug:school_slug>/admin/programs/<int:program_id>/delete/", views_school_programs.school_program_delete_view, name="school_program_delete"),
    path("schools/<slug:school_slug>/admin/programs/<int:program_id>/sessions/new/", views_school_programs.school_session_create_view, name="school_session_create"),
    path("schools/<slug:school_slug>/admin/programs/<int:program_id>/sessions/<int:session_id>/edit/", views_school_programs.school_session_edit_view, name="school_session_edit"),
    path("schools/<slug:school_slug>/admin/programs/<int:program_id>/sessions/<int:session_id>/activate/", views_school_programs.school_session_activate_view, name="school_session_activate"),
    path("schools/<slug:school_slug>/admin/programs/<int:program_id>/sessions/<int:session_id>/deactivate/", views_school_programs.school_session_deactivate_view, name="school_session_deactivate"),
    path("schools/<slug:school_slug>/admin/programs/<int:program_id>/sessions/<int:session_id>/delete/", views_school_programs.school_session_delete_view, name="school_session_delete"),
    path("schools/<slug:school_slug>/admin/reports/", views.school_reports_view, name="school_reports"),
    path("schools/<slug:school_slug>/admin/settings/", views.school_settings_view, name="school_settings"),
    path("schools/<slug:school_slug>/admin/password/", views.school_password_change_view, name="school_password_change"),
    # Backward-compat: old no-slash URL 301s to the canonical slash version.
    path("schools/<slug:school_slug>/admin/reports", RedirectView.as_view(pattern_name="school_reports", permanent=True)),
    path("schools/<slug:school_slug>/reports", RedirectView.as_view(pattern_name="school_reports", permanent=False)),

    path("schools/<slug:school_slug>/status/<str:token>/", views.family_status_view, name="family_status"),

    path("schools/<slug:school_slug>/interest/", views.lead_capture_view, name="lead_capture"),
    path("schools/<slug:school_slug>/interest/success/", views.lead_capture_success_view, name="lead_capture_success"),

    # School admin: billing
    path("schools/<slug:school_slug>/admin/billing/", views.school_billing_view, name="school_billing"),
    path("schools/<slug:school_slug>/admin/billing/checkout/", views.school_billing_checkout_view, name="school_billing_checkout"),
    path("schools/<slug:school_slug>/admin/billing/portal/", views.school_billing_portal_view, name="school_billing_portal"),

    # Stripe webhook (outside admin — no CSRF, no admin auth)
    path("stripe/webhook/", stripe_webhook, name="stripe_webhook"),
    path("stripe/webhook", stripe_webhook),
]
