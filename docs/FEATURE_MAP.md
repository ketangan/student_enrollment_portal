# Pontora Feature Impact Map

When you change an area, check every row in its **Also check** column before shipping.
Keep this file updated when new features are added.

---

## 1. Enrollment Form (apply form)

Multi-step public registration form at `/schools/<slug>/apply/`.

| | |
|---|---|
| **Key files** | `core/views_public.py` (`school_apply_view`, `school_apply_step_view`) · `templates/apply_form.html` · `templates/apply_payment.html` · `templates/apply_submitted_already.html` · `templates/apply_expired.html` · `core/services/validation.py` · `core/services/form_utils.py` |
| **Config source** | YAML `form:` section (sections, fields, fee acknowledgments, `auto_label`, `waiver` types) · YAML `application_fee:` |
| **Also check** | Email notifications (applicant confirmation + admin notification sent on submit) · Billing gate (trial expiry blocks form → `apply_expired.html`) · File uploads (attachment fields → `SubmissionFile`) · Duplicate submission guard (`apply_submitted_already.html`) · Save/Resume draft flow (session token, mid-form abandon) · Capacity (form can redirect to waitlist) · School admin submissions list (new submission appears) · **YAML field keys are immutable** |
| **Test files** | `test_apply_flow.py` · `test_save_resume.py` · `test_waiver.py` · `test_sbmc_workflow.py` · `test_detail_pages.py` · `test_trial_expiry.py` |

---

## 2. Lead Form

Lightweight public inquiry form at `/schools/<slug>/lead/`. Embeddable via `?embed=1`.

| | |
|---|---|
| **Key files** | `core/views_public.py` (`school_lead_form_view`, `lead_capture_view`) · `templates/lead_form.html` · `templates/lead_success.html` · `core/services/lead_intake.py` |
| **Config source** | YAML `leads:` section (fields, `redirect_url`, `redirect_url_map`, `redirect_url_field`, `success_message`, `form_title`, `form_description`) |
| **Also check** | Email notifications (admin notification + lead confirmation sent async in background thread) · Billing gate (trial expiry blocks form) · School admin leads list (new lead appears) · Embed/iframe behaviour on Safari (`@csrf_exempt` is required — CSRF meaningless on unauthenticated public forms; `target=_top` on redirect JS) · Wix integration (redirect to booking URL after submit uses `redirect_url_map` keyed by field value) · Rate limiting (both views use `@ratelimit`) |
| **Test files** | `test_lead_form.py` · `test_lead_form_variants.py` · `test_lead_flow.py` · `test_lead_program_injection.py` · `test_lead_ux.py` · `test_sbmc_workflow.py` |

---

## 3. Email & Notifications

All outbound emails: lead confirmation, lead admin notification, applicant confirmation, submission admin notification, magic links, welcome emails.

| | |
|---|---|
| **Key files** | `core/services/notifications.py` · `core/services/onboarding.py` (welcome email) |
| **Config source** | YAML `leads.confirmation_*` · YAML `success.notifications.applicant_confirmation` · YAML `success.notifications.submission_email` · School SMTP settings (custom SMTP per school, falls back to Resend) |
| **Also check** | Lead form (2 emails sent async on submit) · Enrollment form (2 emails sent on submit) · School admin URL in notification links (must link to `/schools/<slug>/admin/…`, not `/admin/`) · SMTP routing: `get_school_email_connection(school=None)` → school SMTP or Resend fallback · Email audit log (sent emails stored on `EmailAuditLog`) · Gmail/spam classification (HTML-heavy templates → Promotions tab; keep plain-text ratio up) |
| **Test files** | `test_lead_form.py` (notification assertions) · `test_notification_url_fix.py` · `test_onboarding.py` |

---

## 4. Custom Email Templates

Per-school custom email templates managed in school admin at `/schools/<slug>/admin/email-templates/`.

| | |
|---|---|
| **Key files** | `core/views_school_email_templates.py` · `templates/school_admin/email_template_form.html` · `templates/school_admin/email_template_token_confirm_delete.html` |
| **Config source** | `EmailTemplate` model (name, subject, body, active flag) · `EmailTemplateToken` model (custom tokens per school) |
| **Also check** | Notifications (templates override system defaults if active) · Token deletion guard (prompts confirmation if token is used in an active template) · Role enforcement (editor/owner only; viewers blocked) |
| **Test files** | `test_admin.py` · `test_views_admin_models_more.py` |

---

## 5. School Admin Portal

School-facing dashboard and sub-pages at `/schools/<slug>/admin/…`. Entry point for all school admin features.

| | |
|---|---|
| **Key files** | `core/views_school_dashboard.py` (`school_dashboard_view`, `school_settings_view`, `school_billing_view`, `school_team_*_view`, `school_password_change_view`, `school_smtp_test_view`, `admin_theme_api`) · `core/views_school_common.py` (`require_school_role`, `_can_view_school_admin_page`) · `templates/school_admin/` (all school admin templates, base: `base.html`) |
| **Config source** | Plan + feature flags · `SchoolAdminMembership` roles (owner / editor / viewer) |
| **Also check** | Authentication & roles (every view uses `require_school_role`) · Billing gate (trial expiry, plan checks) · Feature flags (leads_enabled, reports_enabled gate sub-pages) · No `/admin/` href visible to non-superusers · Settings page (SMTP config, display name, logo) · Team management (add/remove/role-change members; last-owner invariant enforced) · `school_admin/base.html` — nav and favicon used on every school admin page |
| **Test files** | `test_school_admin.py` · `test_no_django_admin_exposure.py` · `test_login_auth.py` · `test_role_based_access.py` · `test_settings_edge_cases.py` |

---

## 6. Submissions Management

School admin inbox for viewing and acting on enrollment form submissions.

| | |
|---|---|
| **Key files** | `core/views_school_submissions.py` (all submission views) · `templates/school_admin/submissions.html` · `templates/school_admin/submission_detail.html` · `templates/school_admin/submission_bulk_print.html` |
| **Config source** | YAML `admin.submission_statuses` · YAML `admin.submission_workflow.filters` · YAML `admin.default_submission_status` |
| **Also check** | Notifications (manual message send, resend confirmation, resend status link) · AI summary (on-demand from detail page → `school_submission_generate_summary_view`) · CSV export + profile export · Bulk actions (bulk status update, bulk mark contacted, bulk follow-up, bulk download, bulk print) · Schedule change acknowledgment (SBMC-specific) · Role enforcement (viewer cannot mutate status) · File attachments on detail page |
| **Test files** | `test_inbox_workflow.py` · `test_submission_status_workflow.py` · `test_submission_update.py` · `test_detail_pages.py` · `test_export_csv.py` · `test_export_profiles.py` · `test_ai_summary.py` · `test_sbmc_workflow.py` · `test_admin_scoping.py` |

---

## 7. Leads Management

School admin pipeline for prospects who submitted a lead form.

| | |
|---|---|
| **Key files** | `core/views_school_leads.py` (all lead views) · `core/services/lead_conversion.py` · `templates/school_admin/leads.html` · `templates/school_admin/lead_detail.html` |
| **Config source** | YAML `admin.lead_workflow.filters` · YAML `admin.lead_workflow.transitions` |
| **Also check** | Lead form (creates the lead) · Email notifications (manual message send, resend resume link) · Lead → enrollment conversion (`school_lead_start_enrollment_view` → pre-fills apply form) · Lead export (CSV) · Bulk actions (bulk status update, mark contacted, follow-up, clear follow-up) · Role enforcement (viewer read-only) · Feature flag `leads_enabled` gates the entire leads section |
| **Test files** | `test_lead_admin.py` · `test_lead_workflow.py` · `test_lead_update.py` · `test_lead_start_enrollment.py` · `test_lead_conversion.py` · `test_lead_reports.py` · `test_admin_scoping.py` |

---

## 8. Programs & Sessions

School admin management of programs (instruments, courses) and their sessions (time slots).

| | |
|---|---|
| **Key files** | `core/views_school_programs.py` (all program and session views) · `core/services/programs.py` · `templates/school_admin/programs.html` · `templates/school_admin/program_form.html` · `templates/school_admin/session_form.html` · `templates/school_admin/session_generate_form.html` |
| **Config source** | `Program` and `ProgramSession` models · `School.program_field_key` (YAML) for field binding |
| **Also check** | Enrollment form (program select field pulls from active programs/sessions) · Capacity management (programs drive capacity limits) · Lead form (program/instrument select field) · `program_field_key` in YAML ties YAML field key to program model · Session bulk-generate form (`school_session_generate_view`) |
| **Test files** | `test_programs.py` · `test_sessions.py` · `test_session_generate.py` · `test_create_edit.py` |

---

## 9. Reports & Exports

Aggregate reports and data exports for school admins.

| | |
|---|---|
| **Key files** | `core/views_school_submissions.py` (`school_submission_export_view`, `school_submission_profile_export_view`) · `core/views_school_leads.py` (`school_lead_export_view`) · `templates/school_admin/reports.html` |
| **Config source** | Feature flag `reports_enabled` gates access |
| **Also check** | CSV export includes all submission field data (YAML field order) · Profile export uses named export profiles defined in config · Lead CSV export separate from submission CSV · Billing plan check (reports may be plan-gated) · Role enforcement (viewer can read/export) |
| **Test files** | `test_export_csv.py` · `test_export_profiles.py` · `test_lead_reports.py` · `test_reports.py` · `test_phase19_reports.py` |

---

## 10. Capacity Management

Per-program enrollment caps; submissions beyond capacity go to waitlist.

| | |
|---|---|
| **Key files** | `core/services/capacity.py` |
| **Config source** | YAML `capacity.programs` (dict of program_value → int limit) · YAML `capacity.excluded_statuses` · YAML `capacity.waitlist_message` |
| **Also check** | Enrollment form (capacity check on submit; redirect to waitlist message if full) · School admin dashboard (capacity stats displayed) · Programs (capacity limits are per-program slug; must match program values in YAML) |
| **Test files** | `test_capacity.py` |

---

## 11. Ops Portal

Internal superuser tooling at `/ops/…`. Replaces Django Admin for all operational tasks.

| | |
|---|---|
| **Key files** | `core/views_ops.py` · `core/forms_ops.py` · `templates/ops/` (all ops templates, base: `ops/base.html`) |
| **Functions** | School create/detail/convert (demo→customer) · User create/detail/deactivate/reset-password · Cross-school submissions and leads views · Reports · Demo token generate/extend · Onboarding checklist · Welcome email send · Activity tracking toggle · Audit log viewer |
| **Also check** | Every write action must log to `AdminAuditLog` (`action` max_length=16; use `extra={"name":"..."}` for longer names) · Demo school filter — `is_demo=True` schools currently appear in all ops views (pending task to add toggle) · No Django Admin dependency — all ops features must live here |
| **Test files** | `test_ops_phase1.py` · `test_ops_phase2.py` · `test_services_admin_helpers.py` |

---

## 12. Billing & Stripe

Subscription management, checkout, Stripe webhooks, plan lifecycle.

| | |
|---|---|
| **Key files** | `core/services/billing_stripe.py` · `core/views_billing.py` (Stripe webhook handler) · `core/views_school_dashboard.py` (`school_billing_view`, `school_billing_checkout_view`, `school_billing_portal_view`) · `templates/school_admin/billing.html` |
| **Config source** | Env vars: `STRIPE_PRICE_STARTER_MONTHLY_*`, `STRIPE_PRICE_PRO_MONTHLY_*`, `STRIPE_PRICE_GROWTH_MONTHLY_*`, `STRIPE_PRICE_CUSTOM_MONTHLY_*` (`_LIVE` / `_TEST` suffix) · `STRIPE_SECRET_KEY` · `STRIPE_WEBHOOK_SECRET` |
| **Also check** | Feature flags (plan change in webhook → feature access changes immediately) · Trial expiry enforcement (public forms show expired page; school admin pages show locked page) · Billing page back-link (must go to school dashboard, not `/admin/`) · Webhook handler (`views_billing.py`) handles subscription lifecycle: `customer.subscription.created/updated/deleted`, `checkout.session.completed` · Custom plan: `STRIPE_PRICE_CUSTOM_MONTHLY` env var; manually assigned in ops; no self-serve checkout |
| **Test files** | `test_billing.py` · `test_trial_expiry.py` · `test_no_django_admin_exposure.py` |

---

## 13. YAML Configuration

Per-school config files at `configs/schools/<slug>.yaml`. Source of truth for form fields, fees, email text, redirect URLs, admin workflows.

| | |
|---|---|
| **Key files** | `core/services/config_loader.py` · `configs/schools/*.yaml` |
| **Key constraints** | **YAML field `key:` values are immutable** — changing a key breaks save/resume for any in-progress submission that stored data under the old key · Only leaf values (amounts, labels, URLs, messages) can safely change mid-season |
| **Also check** | Enrollment form (sections, fields, fee acknowledgment `amount_display` + `label` must match) · Lead form (fields, `redirect_url`, `redirect_url_map`) · Email notifications (confirmation text, subjects) · Capacity (programs dict keys must match field option values) · Admin workflow (submission statuses, lead transitions) · `test_yaml_config_integrity.py` runs validation on all YAML files in CI |
| **Test files** | `test_config_loader_edge_cases.py` · `test_yaml_config_integrity.py` · `test_sbmc_workflow.py` |

---

## 14. Authentication & Roles

Login, logout, school admin membership, role enforcement across all school admin views.

| | |
|---|---|
| **Key files** | `core/views_login.py` · `core/views_school_common.py` (`require_school_role`, `_can_view_school_admin_page`) · `core/services/school_access.py` · `core/services/school_permissions.py` · `config/urls.py` (`/admin/login/` intercept → `/login/`) · `core/middleware.py` (`SchoolAdminRedirectMiddleware`) |
| **Roles** | `owner` — full access + team management · `editor` — all except team management · `viewer` — read-only (no status changes, no mutations) |
| **Also check** | All school admin views (role enforcement via `require_school_role`) · Login redirect chain (`?next=` preserved through login; must land on school dashboard, not Django admin) · Last-owner invariant (cannot remove or demote the sole owner) · Demo access / magic link flow (single-use token, bug H3) · Password change (`school_password_change_view`) |
| **Test files** | `test_login_auth.py` · `test_role_based_access.py` · `test_notification_url_fix.py` · `test_no_django_admin_exposure.py` · `test_admin_scoping.py` |

---

## 15. Feature Flags & Plans

Plan-based feature gating. Plan hierarchy: trial → starter → pro → growth → custom.

| | |
|---|---|
| **Key files** | `core/services/feature_flags.py` · `core/models.py` (`School.feature_flags` JSON override field for per-school overrides) |
| **Gated features** | `leads_enabled` · `reports_enabled` · `csv_export_enabled` · `custom_branding_enabled` · `ai_summary_enabled` · (others in `feature_flags.py`) |
| **Also check** | School admin pages show `feature_disabled.html` when flag off (back link must go to dashboard) · Billing page shows upgrade prompt when feature is plan-gated · Ops portal plan change propagates to flags immediately (via Stripe webhook or manual update) · Per-school `feature_flags` JSON can override plan defaults |
| **Test files** | `test_phase11.py`–`test_phase18.py` (many flags tested across phases) · `test_reports.py` · `test_no_django_admin_exposure.py` |

---

## 16. Demo System

Demo schools and magic-link access at `demo.mypontora.com`. Separate subdomain, same DB.

| | |
|---|---|
| **Key files** | `core/views_login.py` (`demo_access_view`) · `core/views_demo.py` (`demo_index`, `demo_detail`) · `core/management/commands/seed_*_demo.py` · `core/models.py` (`School.is_demo = BooleanField`) |
| **Also check** | Ops portal (demo schools currently appear in all ops views — pending task: filter by `is_demo=False` by default with toggle) · School admin (demo session banner displayed) · Authentication (magic link single-use enforcement — bug H3 open) · URL routing: `demo.mypontora.com` → demo views; `app.mypontora.com` → customer views (same code, host-based routing) |
| **Test files** | `test_login_auth.py` (magic link tests) · `test_onboarding.py` |

---

## 17. Scheduling & Family Status Page

Parent-facing status page and schedule change request flow (SBMC-specific but schema is general).

| | |
|---|---|
| **Key files** | `core/views_public.py` (`family_status_page_view`, `school_status_login_view`, `schedule_change_request_view`) · `core/views_school_submissions.py` (`school_submission_acknowledge_schedule_change_view`) · `templates/family_status.html` · `templates/school_status_login.html` |
| **Config source** | YAML `sched_*` fields on submissions (stored at apply time) · YAML `success.hide_resubmit` |
| **Also check** | Email notifications (schedule change triggers notification to school) · Rate limiting (bug H2: no rate limit on schedule change endpoint — open bug) · School admin submission detail (schedule change UI shows change request, acknowledge button) · Scheduling preference fields on enrollment form must be keyed `sched_*` |
| **Test files** | `test_family_status_page.py` · `test_scheduling_status_page.py` · `test_sbmc_workflow.py` |

---

## 18. Onboarding (Demo → Customer Conversion)

Flow for converting a demo school into a paying customer in ops.

| | |
|---|---|
| **Key files** | `core/services/onboarding.py` · `core/views_ops.py` (`ops_school_convert_view`, `ops_checklist_toggle_view`, `ops_school_welcome_email_view`) |
| **Also check** | Ops portal (conversion is triggered from ops school detail page) · Billing (conversion links school to Stripe; trial clock starts) · Email (welcome email sent via `notifications.py`) |
| **Test files** | `test_onboarding.py` |

---

## 19. Activity Tracking

Page-view and action audit logging for school admin usage analytics.

| | |
|---|---|
| **Key files** | `core/middleware.py` (activity tracking middleware) · `core/models.py` (`ActivityLog`) · `core/views_ops.py` (`ops_activity_tracking_toggle_view`) |
| **Also check** | Ops portal (toggle activity tracking per school; view logs) · Privacy (activity tracking is opt-in per school; check toggle before assuming it's on) |
| **Test files** | `test_activity_tracking.py` |

---

## 20. AI Summary

On-demand GPT-powered summary of a submission's form data, shown in submission detail.

| | |
|---|---|
| **Key files** | `core/services/ai_summary.py` · `core/views_school_submissions.py` (`school_submission_generate_summary_view`) |
| **Config source** | Feature flag `ai_summary_enabled` · Env var for OpenAI/model API key |
| **Also check** | Submission detail page (summary rendered inline; button triggers generation) · Feature flag gate (button hidden if flag off) · Cost per call (each generation hits external API) |
| **Test files** | `test_ai_summary.py` |

---

## 21. Webhooks & Integrations

Inbound webhook for lead intake from external systems; outbound integrations.

| | |
|---|---|
| **Key files** | `core/views_webhooks.py` (`webhook_lead_intake_view`) · `core/services/integrations.py` · `config/urls.py` (`/webhooks/leads/<slug>/<token>/`) |
| **Config source** | Token in URL (school-specific, rotatable) · `School.webhook_token` |
| **Also check** | Lead form (webhook creates the same `Lead` model as the public form) · Notifications (webhook lead intake triggers same admin notification as form submission) · Rate limiting (token-auth only; no CSRF; no session) |
| **Test files** | `test_lead_flow.py` (webhook path) |

---

## 22. File Uploads

File attachments on the enrollment form, stored per submission.

| | |
|---|---|
| **Key files** | `core/views_public.py` (file upload handling during apply) · `core/views.py` (`admin_download_submission_file`) · `core/models.py` (`SubmissionFile`) |
| **Config source** | YAML `form:` field of `type: file` |
| **Also check** | Enrollment form (file input rendered) · Submission detail (file download links) · Download URL at `/admin/uploads/<id>/` — still uses `/admin/` prefix (pending decommission task — do not break this URL until a replacement is wired up) |
| **Test files** | `test_phase14.py` (file upload tests) |

---

## Quick cross-reference

| When you touch… | Always also check… |
|---|---|
| `notifications.py` | Lead form · Enrollment form · Webhook intake · Email audit log · Admin URL in email links |
| `apply_form.html` | Enrollment form view · Save/resume flow · File uploads · Mobile rendering · Waiver field |
| `lead_form.html` | Lead form view · Embed/Safari `@csrf_exempt` · `target=_top` redirect JS · Wix redirect |
| `billing_stripe.py` | Stripe webhook handler (`views_billing.py`) · Billing page · Feature flags · Trial expiry |
| `config_loader.py` | Every public form · YAML field key immutability |
| Any `*.yaml` config | Enrollment form fields · Lead form fields/redirect URLs · Fee text (`label` + `amount_display` both) · Email confirmation text |
| `feature_flags.py` | All gated school admin pages · Billing upgrade prompts · Per-school JSON overrides |
| `views_login.py` | All authenticated views · `?next=` redirect chain · Demo access magic link |
| `views_ops.py` | Audit log entries · Demo school filter · No Django Admin dependency |
| `school_admin/base.html` | All school admin pages (nav, favicon, layout) |
| `views_school_common.py` | All school admin views (role decorator) · Last-owner invariant |
| `views_school_programs.py` | Enrollment form program dropdown · Capacity config · Session generate |
| `capacity.py` | Enrollment form submit flow · Dashboard capacity stats |
| `ai_summary.py` | Submission detail page · Feature flag `ai_summary_enabled` |
| `family_status.html` | Schedule change view · Status login · SBMC YAML `sched_*` fields |
| `onboarding.py` | Ops convert view · Billing start · Welcome email |
