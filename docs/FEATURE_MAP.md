# Pontora Feature Impact Map

When you change an area, check every row in its **Also check** column before shipping.
Keep this file updated when new features are added.

---

## 1. Enrollment Form (apply form)

Public multi-step registration form at `/schools/<slug>/apply/`.

| | |
|---|---|
| **Key files** | `core/views_public.py` · `templates/apply_form.html` · `core/services/validation.py` |
| **Config source** | YAML `form:` section (fields, sections, fee acknowledgments) |
| **Also check** | Email notifications (confirmation sent on submit) · Billing gate (trial expiry blocks form) · File uploads (attachment fields) · Duplicate submission guard · Resume/draft flow · School admin submissions list |
| **Test files** | `core/tests/test_apply_form.py` · `core/tests/test_sbmc_workflow.py` · `core/tests/test_duplicate_submission.py` |

---

## 2. Lead Form

Lightweight public inquiry form at `/schools/<slug>/lead/`. Embeddable via `?embed=1`.

| | |
|---|---|
| **Key files** | `core/views_public.py` (`school_lead_form_view`) · `templates/lead_form.html` · `core/services/lead_intake.py` |
| **Config source** | YAML `leads:` section (fields, redirect_url, redirect_url_map, fee text, success_message) |
| **Also check** | Email notifications (admin notification + lead confirmation sent on submit) · Billing gate (trial expiry blocks form) · School admin leads list · Embed/iframe behaviour on Safari (CSRF exempt, target=_top redirect) · Wix integration (redirect to booking URL after submit) |
| **Test files** | `core/tests/test_lead_form.py` · `core/tests/test_sbmc_workflow.py` · `core/tests/test_no_django_admin_exposure.py` |

---

## 3. Email & Notifications

All outbound emails: lead confirmation, lead admin notification, applicant confirmation, submission notification.

| | |
|---|---|
| **Key files** | `core/services/notifications.py` · `core/services/onboarding.py` |
| **Config source** | YAML `leads.confirmation_*` · YAML `success.notifications.applicant_confirmation` · School SMTP settings (Ph 22) |
| **Also check** | Lead form (sends 2 emails on submit) · Enrollment form (sends 2 emails on submit) · School admin URL in notification links (must link to `/schools/<slug>/admin/…`, not `/admin/`) · SMTP fallback chain (school SMTP → Resend) · Email audit log (stores sent emails) |
| **Test files** | `core/tests/test_notifications.py` · `core/tests/test_notification_url_fix.py` · `core/tests/test_lead_form.py` |

---

## 4. School Admin Portal

School-facing dashboard and sub-pages at `/schools/<slug>/admin/…`.

| | |
|---|---|
| **Key files** | `core/views_school_dashboard.py` · `core/views_school_leads.py` · `core/views_school_submissions.py` · `core/views_school_common.py` · `templates/school_admin/` |
| **Config source** | Plan + feature flags · `SchoolAdminMembership` roles (owner / editor / viewer) |
| **Also check** | Authentication & roles (require_school_role enforced on every view) · Billing gate (trial expiry, plan checks) · Feature flags (leads_enabled, reports_enabled gate pages) · No `/admin/` href should appear for non-superusers |
| **Test files** | `core/tests/test_school_admin.py` · `core/tests/test_no_django_admin_exposure.py` · `core/tests/test_login_auth.py` · `core/tests/test_roles.py` |

---

## 5. Ops Portal

Internal superuser tooling at `/ops/…`.

| | |
|---|---|
| **Key files** | `core/views_ops.py` · `templates/ops/` · `core/forms_ops.py` |
| **Also check** | Audit log (every ops write action must log to AdminAuditLog) · Demo school filter (is_demo schools should be excluded from default views — pending task) · No Django Admin dependency |
| **Test files** | `core/tests/test_ops.py` |

---

## 6. Billing & Stripe

Subscription management, Stripe webhooks, plan upgrades.

| | |
|---|---|
| **Key files** | `core/services/billing_stripe.py` · `core/views_billing.py` · `core/views_school_dashboard.py` (billing state logic) · `templates/school_admin/billing.html` |
| **Config source** | Env vars: `STRIPE_PRICE_*_LIVE / _TEST` · `STRIPE_SECRET_KEY` · `STRIPE_WEBHOOK_SECRET` |
| **Also check** | Feature flags (plan change → feature access changes) · Trial expiry enforcement (public forms, school admin views) · Billing page back-link (must go to school dashboard, not `/admin/`) · Webhook handler (`core/views_billing.py`) for subscription lifecycle events |
| **Test files** | `core/tests/test_billing.py` · `core/tests/test_no_django_admin_exposure.py` |

---

## 7. YAML Configuration

Per-school config files at `configs/schools/<slug>.yaml`. Source of truth for form fields, fees, email text, redirect URLs.

| | |
|---|---|
| **Key files** | `core/services/config_loader.py` · `configs/schools/*.yaml` |
| **Also check** | Enrollment form (fields, sections, fee acknowledgment text) · Lead form (fields, redirect URLs, fee text, success message) · Email notifications (confirmation text, subjects) · Billing (plan gates reference school features) · **YAML field keys are immutable** — changing a key breaks save/resume for in-progress submissions |
| **Test files** | `core/tests/test_config_loader.py` · `core/tests/test_sbmc_workflow.py` |

---

## 8. Authentication & Roles

Login, logout, school admin membership, role enforcement.

| | |
|---|---|
| **Key files** | `core/views_login.py` · `core/views_school_common.py` (`require_school_role`, `_can_view_school_admin_page`) · `config/urls.py` (admin/login/ intercept) · `core/middleware.py` (`SchoolAdminRedirectMiddleware`) |
| **Also check** | All school admin views (role enforcement) · Login redirect chain (must go to school dashboard, not Django admin) · `?next=` param preserved through login for email notification links · Demo access / magic link flow |
| **Test files** | `core/tests/test_login_auth.py` · `core/tests/test_roles.py` · `core/tests/test_notification_url_fix.py` |

---

## 9. Feature Flags & Plans

Plan-based feature gating (leads, reports, CSV export, custom branding, etc.).

| | |
|---|---|
| **Key files** | `core/services/feature_flags.py` · `core/models.py` (`School.feature_flags` JSON override field) |
| **Also check** | School admin pages (feature_disabled.html shown when flag off — back link must go to dashboard) · Billing page (shows upgrade prompt when feature gated) · Ops portal (plan change propagates to flags) |
| **Test files** | `core/tests/test_feature_flags.py` · `core/tests/test_reports.py` · `core/tests/test_no_django_admin_exposure.py` |

---

## 10. File Uploads

Submission file attachments (enrollment form).

| | |
|---|---|
| **Key files** | `core/views_public.py` (upload handling) · `core/models.py` (`SubmissionFile`) |
| **Also check** | Enrollment form (upload fields rendered) · School admin submission detail (file download links) · File download URL (`/admin/uploads/<id>/` — note: still uses `/admin/` path prefix, pending decommission task) |
| **Test files** | `core/tests/test_file_upload.py` |

---

## 11. Demo System

Demo schools, magic links, prospect-facing experience at `demo.mypontora.com`.

| | |
|---|---|
| **Key files** | `core/views_login.py` (demo access / magic link) · `core/management/commands/seed_*_demo.py` · `core/models.py` (`School.is_demo`) |
| **Also check** | Ops portal (demo schools currently appear in all ops views — pending filter task) · School admin (demo session banner shown) · Authentication (magic link single-use enforcement — bug H3) |
| **Test files** | `core/tests/test_demo.py` |

---

## 12. Scheduling (SBMC-specific)

Schedule change requests, parent status page.

| | |
|---|---|
| **Key files** | `core/views_public.py` (parent status page) · `core/views_school_submissions.py` (schedule change admin) |
| **Config source** | YAML `sched_*` fields (SBMC only) |
| **Also check** | School admin submission detail (schedule change UI) · Email notifications (schedule change triggers notification) · Rate limiting (bug H2 — no rate limit on schedule change) |
| **Test files** | `core/tests/test_sbmc_workflow.py` |

---

## Quick cross-reference

| When you touch… | Always also check… |
|---|---|
| `notifications.py` | Lead form · Enrollment form · Email audit log · Admin URL in email links |
| `apply_form.html` | Enrollment form view · Resume/draft flow · File uploads · Mobile rendering |
| `lead_form.html` | Lead form view · Embed/Safari behaviour · Wix redirect |
| `billing_stripe.py` | Webhook handler · Billing page · Feature flags · Trial expiry |
| `config_loader.py` | Every public form · YAML field key immutability |
| Any YAML `*.yaml` | Form fields rendered on apply/lead · Fee text · Email confirmation text |
| `feature_flags.py` | All gated school admin pages · Billing upgrade prompts |
| `views_login.py` | All authenticated views · `?next=` redirect chain · Demo access |
| `views_ops.py` | Audit log entries · Demo school filter |
| `school_admin/base.html` | All school admin pages (nav, favicon, layout) |
