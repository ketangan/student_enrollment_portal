# Student Enrollment Portal — Project Source of Truth

This file is the canonical reference for decisions made, work completed, work in progress,
and work planned. Update it after every meaningful commit or decision. It is written for
both human and AI readability — future sessions should read this before doing anything.

---

## What This Product Is

A multi-tenant SaaS student enrollment platform for schools. Schools sign up, get a YAML-driven
application form, and manage the full enrollment pipeline (leads → applications → enrolled) through
a modern web admin. Operators (superadmins) manage all schools from a separate ops portal.

**Stack**: Django 6, PostgreSQL, Stripe billing, Resend/Anymail email, Whitenoise static files,
deployed on Render. 854+ passing pytest tests.

---

## Architecture Decisions (locked — do not change without discussion)

| Decision | Choice | Reason |
|----------|--------|--------|
| Form structure | YAML per school in `configs/schools/<slug>.yaml` | Zero-code school onboarding |
| YAML field keys | **Immutable** once live | Changing key = data loss for saved submissions |
| Feature gating | Plan-based defaults + per-school JSON overrides (`School.feature_flags`) | Flexible without model changes |
| Multi-tenancy | slug → YAML config → DB queryset scoping → membership check | Every query scoped to school |
| Billing | Stripe Checkout + webhooks; dual test/live mode via env suffix | Standard SaaS pattern |
| Email | Anymail + Resend backend | Provider-agnostic |
| Static files | Whitenoise with CompressedManifest | Works on Render without S3 |
| Admin UI (school) | Custom Django views + templates at `/schools/<slug>/admin/` | Better UX than Django admin |
| Admin UI (ops) | Custom views at `/ops/` | See Ops Portal section below |
| Django admin | Kept alive at `/admin/` (or `ADMIN_URL` env var) as raw escape hatch | Do not remove |
| SMS | NOT implemented, NOT planned | Explicit out of scope |
| Scheduling calendar | NOT implemented, NOT planned | Explicit out of scope |

---

## Repository Layout (key paths)

```
config/           Django project settings, urls, wsgi
configs/schools/  Per-school YAML files (one per school slug)
core/
  admin/          Django admin registrations (split by concern)
  migrations/     DB migrations (currently through 0029)
  models.py       All models
  views.py        All school admin + public views (~5000 lines — refactor pending)
  urls.py         School admin + public URL patterns
  services/       billing_stripe.py, notifications.py, validation.py, config_loader.py, etc.
  tests/          All pytest tests
  templatetags/   Jazzmin patch + custom tags
templates/
  school_admin/   Modern school admin UI templates
  ops/            NEW — Ops superadmin UI templates (Phase 1)
  admin/          Django admin overrides (Jazzmin)
static/admin/     dashboard.css, reports.css, theme JS
```

---

## URL Structure

| Prefix | Who Uses It | What It Is |
|--------|-------------|------------|
| `/` | Public / families | Enrollment form, resume, confirmation |
| `/schools/<slug>/admin/` | School admins + superusers | Modern school management UI |
| `/ops/` | Superusers only | Ops portal (NEW — see below) |
| `/admin/` | Superusers only | Django admin — raw escape hatch, do not remove |

---

## Plans & Feature Flags

Plans: `trial`, `starter`, `pro`, `growth`

Key constants: `TRIAL_LENGTH_DAYS = 30` in `core/models.py`

Feature flags are defined in `core/services/feature_flags.py`. Plan defaults + per-school
JSON overrides in `School.feature_flags`. Superusers bypass most feature gates.

---

## Completed Features (phases 2–17)

### School Admin UI (`/schools/<slug>/admin/`)

| Phase | What Was Built |
|-------|---------------|
| 2 | Leads pipeline workflow — status transitions, `admin_lead_yaml.py` |
| 3 | Lead → Submission conversion; `DraftSubmission.lead` FK; migration 0026 |
| 4 | Submission detail page, Lead detail page, Django admin fallback links |
| 5 | Lead edit (notes, follow-up date, Mark Contacted with email side-effect) |
| 6 | Submission internal notes (`Submission.internal_notes`); migration 0027 |
| 7/8 | CSV export for submissions + leads; `_apply_submission_filters` / `_apply_lead_filters` |
| 9 | Mobile hamburger nav, responsive detail grid, clipboard copy buttons |
| 10 | Admin create/edit for leads and submissions; multi-form school support |
| 11 | Follow-up system (next_follow_up_at, last_contacted_at, updated_at); migration 0028; smart filter pills; bulk mark-contacted / bulk follow-up |
| 12 | Email communication: send message to family, resend confirmation, workflow notifications |
| 14 | Conversion intelligence: funnel metrics, trend stats, pipeline gaps, stale counts; hidden smart filters `not_converted` / `not_enrolled` |
| 17 | Billing alignment: feature_disabled.html with upgrade hints; billing nav fixes |

### Platform / Infrastructure

| What | Detail |
|------|--------|
| Trial system | `School.trial_started_at` + `TRIAL_LENGTH_DAYS=30`; `trial_ends_at` property; `trial_days_left`; trial banner in school admin (hidden from superusers) |
| Trial end date override | `School.trial_end_date` DateField (migration 0029); superadmin can extend/clear from Settings page |
| Stripe billing | Checkout, webhooks, subscription lifecycle; `handle_subscription_updated` in `billing_stripe.py` |
| Rate limiting | `django_ratelimit` on public form endpoints; silenced E003/W001 when no Redis (LocMemCache fine for single worker) |
| Password change | Custom school admin page at `/schools/<slug>/admin/password/` (not Django admin) |
| Work queue cap | `DASHBOARD_WORK_QUEUE_LIMIT = 10` constant in `views.py` after `_TERMINAL_SUBMISSION_STATUSES` |
| Mobile table fix | Columns hidden by CSS class (`dash-col-last-activity`, `dash-col-contact`) not nth-child |
| Settings billing link | Always visible ("Manage billing →" for subscribed, "View billing →" for trial/new) |

---

## Current State (as of Phase 17 completion)

- **854 passing tests**
- `core/views.py` is ~5,000 lines / 80+ functions — **refactor is planned but not started**
- Django admin (`/admin/`) is the only superadmin UI; it works but is not polished
- Login/logout currently redirects through Django admin login page — **to be fixed in Ops Phase 1**

---

## Ops Portal — Design Decisions

### Background
The Django admin at `/admin/` handles superadmin operations today. It works but is not polished.
Decision: build a new custom ops portal at `/ops/` with the same visual style as the school admin.
Django admin stays alive as a raw data escape hatch — do not remove it.

### URL: `/ops/`
Not `/admin/` (Django admin stays there). `/ops/` is less guessable than `/superadmin/` but
still memorable for the operator. Can be further hardened via reverse proxy in production.

### Mental Model
- `/ops/` manages the **business** (schools, plans, billing, users, memberships)
- `/schools/<slug>/admin/` manages the **work** within a school (submissions, leads, reports)
- Cross-school aggregate data views (`/ops/submissions/`, `/ops/leads/`) are **Phase 2**

### What the Ops Portal Must Provide (per operator requirements)
1. Everything Django admin provides today for School, User, SchoolAdminMembership models
2. Automatic forms tied to model fields (use Django ModelForms — new fields auto-appear)
3. Audit trail for every ops action (reuse existing `AdminAuditLog` model)
4. Inline editing of related objects (memberships inline on school detail)
5. Create/edit/deactivate schools
6. Manage users and school admin assignments across all schools
7. Business health dashboard (trials expiring, active schools, submission volumes)
8. Clean login/logout flow — not through Django admin login page

### What Is NOT in the Ops Portal (stays in Django admin)
- Raw model debugging / JSON field inspection (rare, Django admin is fine)
- DraftSubmission management
- AdminAuditLog raw list (Django admin has this)

---

## Ops Portal — Implementation Plan

### Phase 1 (current work): Core ops portal — Schools + Users + Auth

Broken into logical git commits:

#### Commit 1 — Auth, URL structure, base template
- `ops_required` decorator: `is_superuser` check, redirect to `/ops/login/` if not
- `LOGIN_URL` stays `/admin/login/` for now; update when custom login is ready
- `config/urls.py`: add `path('ops/', include('core.urls_ops'))` 
- New file: `core/urls_ops.py` with all `/ops/` URL patterns
- `templates/ops/base.html`: same dash visual style, ops-specific nav (Dashboard, Schools, Users)
- Redirect after login: superuser → `/ops/`, school admin → `/schools/<slug>/admin/`
- Redirect after logout: → `/login/` (new standalone page, not Django admin)

#### Commit 2 — Dashboard
- View: `ops_dashboard_view`
- Metrics: total schools, active schools, trials expiring ≤7 days, trials expired, total users
- Expiring trials table (school name, plan, trial end date, days left) with link to school detail
- Recently added schools (last 5)

#### Commit 3 — Schools list + create
- View: `ops_schools_list_view` at `/ops/schools/`
- Paginated list, search by name/slug, filter by plan/is_active
- Per row: name, slug, plan, status, trial end (if trial), submission count, link to ops detail + link to school admin
- View: `ops_school_create_view` at `/ops/schools/new/`
- Uses `SchoolForm(ModelForm)` — auto-includes all School fields
- Audit logged on create

#### Commit 4 — School detail + edit + trial override
- View: `ops_school_detail_view` at `/ops/schools/<slug>/`
- Sections: Info, Plan & Billing (Stripe data), Feature Flags, Trial Override, Memberships, Recent activity
- Edit form: `SchoolEditForm(ModelForm)` — plan, is_active, display_name, website_url, trial_end_date, feature_flags (JSON), Stripe fields
- Feature flags: rendered as individual toggles derived from the model form field (not raw JSON textarea)
- Audit logged on every save; change diff shown in log
- Quick stats: submission count, lead count, last submission date
- "Open school admin →" button to `/schools/<slug>/admin/`

#### Commit 5 — School memberships (inline on school detail)
- List current members (user email, date added)
- Add member: email lookup → create SchoolAdminMembership
- Remove member: POST to remove
- Audit logged
- All inline on the school detail page (no separate page needed)

#### Commit 6 — Users list + detail + create
- View: `ops_users_list_view` at `/ops/users/`
- All users, searchable by email/username, filterable by is_active/is_staff/is_superuser
- Per row: email, username, school memberships, is_active, date joined
- View: `ops_user_detail_view` at `/ops/users/<id>/`
- Edit: `UserEditForm(ModelForm)` — email, first_name, last_name, is_active, is_staff
- Password reset: send Django password reset email
- View memberships, add/remove school access
- Audit logged on changes

#### Commit 7 — Login/logout redirect cleanup
- New standalone login page at `/login/` with school brand-neutral styling
- `LOGIN_URL = '/login/'` in settings
- Custom login view: after login, redirect superuser → `/ops/`, school admin → `/schools/<slug>/admin/`
- `LOGOUT_REDIRECT_URL = '/login/'`
- Remove any Django admin login page references from school admin templates

#### Commit 8 — Tests
- `core/tests/test_ops_phase1.py`
- Test auth guard: non-superuser gets redirected
- Test dashboard metrics accuracy
- Test school create/edit/deactivate
- Test membership add/remove
- Test user create/edit
- Test login redirect for superuser vs school admin

### Phase 2 (future): Cross-school aggregate data views
- `/ops/submissions/` — all submissions from all schools, filterable by school, status, date, program type
- `/ops/leads/` — all leads across all schools
- `/ops/reports/` — aggregate funnel metrics across all schools
- Pagination and query optimization required before building (needs real data volume)

### Phase 3 (complete): Cleanup
- Dead code audit: nothing to remove (all views, templates, URLs confirmed live)
- `core/views.py` split into 5 files: `views_public.py`, `views_school_common.py`,
  `views_school_dashboard.py`, `views_school_submissions.py`, `views_school_leads.py`
- `views.py` kept as backward-compat re-export facade (urls.py unchanged)
- All `is_superuser` template checks audited — all are still appropriate, none removed
- 914 passing tests

---

## Planned Features (Prioritised)

### Phase 18 — Application Fees (Stripe, school-direct)

**Goal**: Schools can optionally charge an application fee, paid by the parent at submission time.
Money goes directly to the school's own Stripe account. Platform never touches the funds.

**Key design decisions**:
- Payment is a **platform-injected step**, not a YAML form field. Configured in YAML as metadata only.
- Stripe keys (publishable + secret) stored on `School` model in DB — never in YAML.
- Fee is charged **at submission time**: applicant fills out form → clicks Submit → payment page → on success, Submission is created. Failed payment = no Submission.
- Synchronous PaymentIntent confirm (no webhook needed for this flow — school's own account, real-time response).
- Refunds are manual (school handles via their own Stripe dashboard).

**YAML config** (per school, metadata only — no keys in YAML):
```yaml
application_fee:
  enabled: true
  amount: 75           # USD, whole dollars only
  description: "Non-refundable application fee"
```

**DB changes** (new fields on `School` model, migration required):
- `app_fee_stripe_public_key` CharField (blank=True)
- `app_fee_stripe_secret_key` CharField (blank=True) — store encrypted or treat as sensitive
- Fee amount + enabled flag live in YAML; keys live in DB to keep secrets out of config files

**Files touched**:
- `core/models.py` — 2 new School fields + migration
- `core/services/config_loader.py` — parse `application_fee` block
- `core/views_public.py` — `apply_view` checks for fee; new `apply_payment_view` (GET+POST)
- `core/urls.py` — new URL `apply_payment` at `/schools/<slug>/apply/payment/`
- `templates/apply_payment.html` — Stripe Elements card form (new template)
- `templates/apply_form.html` — "Continue to payment" instead of "Submit" when fee enabled
- `core/services/billing_stripe.py` — new helper `create_application_fee_intent(school, amount_cents)`
- `core/models.py` — `Submission.payment_intent_id` (CharField blank=True), `Submission.payment_status` (CharField: `""`, `"paid"`, `"waived"`)
- `templates/school_admin/submission_detail.html` — payment status badge
- `templates/school_admin/submissions.html` — payment status column (optional, gated on school having fees)
- `templates/ops/school_detail.html` — show fee config fields (read-only display + link to edit)
- `core/forms_ops.py` — `OpsSchoolEditForm` gets Stripe key fields (write-only password inputs)

**Out of scope**:
- Refund UI (use school's Stripe dashboard)
- Fee waivers via admin (add later if needed)
- Per-form or per-program fee amounts (single fee per school)
- PayPal or any second payment processor

**Tests** (`core/tests/test_phase18.py`, ~12 tests):
- `test_fee_skipped_when_disabled` — form with no fee goes straight to Submission creation
- `test_fee_page_renders_with_school_pub_key` — payment page receives school's publishable key
- `test_successful_payment_creates_submission` — mock PaymentIntent success → Submission created with `payment_status="paid"`
- `test_failed_payment_no_submission` — mock PaymentIntent failure → no Submission, error shown
- `test_payment_page_requires_prior_form_completion` — cannot load payment page without completing form first (session check)
- `test_submission_detail_shows_paid_badge` — school admin sees payment status
- `test_ops_school_edit_stores_stripe_keys` — ops can save publishable + secret key
- `test_fee_amount_must_be_positive` — YAML validation rejects fee ≤ 0

---

### Phase 19 — Waitlist Management

**Goal**: Schools can place applicants on a waitlist with an ordered position. Admins can promote
from the waitlist with a single action that updates status and notifies the family.

**Key design decisions**:
- `"Waitlisted"` status already exists in the status choices — no new status needed.
- Waitlist position is a per-school ordered integer, not global.
- Promotion = status change to next step (configurable in YAML workflow) + optional email notification.
- No enrollment cap enforcement in this phase — cap is informational only.

**DB changes**:
- `Submission.waitlist_position` IntegerField (null=True, blank=True, db_index=True)
- `School.enrollment_cap` IntegerField (null=True, blank=True) — informational display only

**Files touched**:
- `core/models.py` + migration
- `core/views_school_submissions.py` — `school_submission_status_update_view` auto-assigns waitlist position on transition to Waitlisted; new `school_waitlist_view` GET (ordered list of waitlisted submissions)
- `core/urls.py` — `school_waitlist` URL, `school_waitlist_promote` POST URL
- `templates/school_admin/waitlist.html` — drag-reorder list (or simple up/down buttons), Promote button per row
- `templates/school_admin/submissions.html` — waitlist position shown for Waitlisted rows
- `templates/school_admin/submission_detail.html` — waitlist position shown in sidebar
- `core/services/notifications.py` — `send_waitlist_promotion_notification`
- School admin nav — add Waitlist link (gated on school having any waitlisted submissions)

**Out of scope**:
- Drag-and-drop reordering (use up/down buttons for v1)
- Auto-promote when enrolled count drops below cap
- Public waitlist position display (parent portal feature)

**Tests** (`core/tests/test_phase19.py`, ~10 tests):
- `test_waitlist_position_assigned_on_status_change`
- `test_waitlist_positions_are_sequential_per_school`
- `test_promote_from_waitlist_changes_status`
- `test_promote_sends_notification_when_email_enabled`
- `test_waitlist_view_ordered_by_position`
- `test_waitlist_position_null_for_non_waitlisted`

---

### Phase 20 — Parent Portal / Application Status Page

**Goal**: Families can check the status of their application without calling the school.
Public URL, no login, lookup by email address + application ID (public_id).

**Key design decisions**:
- Auth: email + public_id (not DOB — schools don't always collect it). Both must match.
- Shows: current status, school-configured public message per status (optional), submitted date.
- Does NOT show: internal notes, audit log, staff comments.
- School can configure custom status messages in YAML per status value.
- Rate-limited to prevent enumeration attacks.

**YAML config** (optional):
```yaml
status_page:
  enabled: true
  messages:
    New: "We've received your application and will be in touch soon."
    Enrolled: "Congratulations! Your child has been enrolled."
    Waitlisted: "Your child is on our waitlist. We'll contact you if a spot opens."
```

**Files touched**:
- `core/views_public.py` — `application_status_view` (GET: lookup form; POST: show result)
- `core/urls.py` — `application_status` at `/schools/<slug>/status/`
- `templates/application_status.html` — lookup form + result card
- `core/services/config_loader.py` — parse `status_page` block
- Rate limiting: `@ratelimit(key="ip", rate="10/m")` on the view
- `School.status_page_enabled` — or gate purely on YAML flag

**Out of scope**:
- Login / account creation for parents
- Document upload or messaging from parent portal
- Mobile push notifications

**Tests** (`core/tests/test_phase20.py`, ~8 tests):
- `test_status_lookup_correct_credentials`
- `test_status_lookup_wrong_email`
- `test_status_lookup_wrong_public_id`
- `test_status_page_disabled_returns_404`
- `test_custom_yaml_message_shown`
- `test_rate_limit_enforced`

---

### Phase 21 — Multi-Child Household

**Goal**: One parent email can have multiple children applying to the same school.
Currently blocked by unique constraint on (school, normalized_email).

**Key design decisions**:
- **Approach**: Add `child_name` to `Lead` and change unique constraint from
  `(school, normalized_email)` to `(school, normalized_email, child_name)`.
  Simpler than a Household model; avoids a large data migration.
- Lead capture form gets an optional "Child's name" field (not YAML-driven — platform level).
- Admin lead list groups by email visually (CSS styling only, no DB grouping query).
- Dedup logic: when a new lead arrives, match on school + email + child_name. If child_name
  is blank, fall back to old dedup behavior (one lead per email).

**DB changes**:
- `Lead.child_name` CharField(max_length=200, blank=True, default="")
- Remove `unique_together = [("school", "normalized_email")]`
- Add `unique_together = [("school", "normalized_email", "child_name")]`
- Migration: data migration to backfill `child_name=""` for all existing leads (already the default, so no-op)

**Files touched**:
- `core/models.py` + migration
- `core/views_public.py` — lead capture form picks up `child_name`
- `core/views_school_leads.py` — lead create form adds child_name field
- `core/views_school_common.py` — `_build_lead_row` includes child_name in display
- `templates/lead_form.html` — child name field on public capture form
- `templates/school_admin/leads.html` — child name column
- `templates/school_admin/lead_detail.html` — child name in header
- `core/services/admin_lead_yaml.py` — export includes child_name column

**Out of scope**:
- Household model (overkill for v1)
- Linking siblings in the UI
- Per-child application tracking on the parent portal

**Tests** (`core/tests/test_phase21.py`, ~8 tests):
- `test_two_children_same_email_same_school_allowed`
- `test_duplicate_child_name_same_email_rejected`
- `test_blank_child_name_dedup_preserved`
- `test_lead_export_includes_child_name`
- `test_leads_list_shows_child_name`

---

### Phase 22 — Referral Source Analytics

**Goal**: School admins can see which referral sources and UTM campaigns bring the most leads
that actually convert to enrolled students. UTM data already captured on `Lead` model.

**Key design decisions**:
- No new models — all data already exists (`Lead.source`, `Lead.utm_source`,
  `Lead.utm_medium`, `Lead.utm_campaign`, `Lead.converted_submission`).
- Add a new section to the existing `school_reports_view` (not a separate page).
- Two tables: (1) by `source` field, (2) by `utm_campaign` (only when UTM data exists).
- Columns: Source, Total Leads, Converted, Conversion Rate, Enrolled (of converted).

**Files touched**:
- `core/views_school_dashboard.py` — `school_reports_view` adds `source_breakdown` + `utm_breakdown` context dicts
- `templates/reports.html` — two new table sections (gated on leads_enabled)
- No new URLs, no new models, no migrations

**Out of scope**:
- UTM link builder tool
- Attribution across multiple touches (first-touch only)
- Campaign cost / ROI tracking

**Tests** (`core/tests/test_phase22.py`, ~6 tests):
- `test_source_breakdown_correct_counts`
- `test_source_breakdown_conversion_rate`
- `test_utm_breakdown_hidden_when_no_utm_data`
- `test_source_breakdown_hidden_when_leads_disabled`

---

### Phase 23 — Application Deadline Enforcement (low priority)

**Goal**: Schools can set open/close dates for their application form. The form shows a
countdown when approaching the deadline and a custom message when closed.

**Key design decisions**:
- Configured entirely in YAML — no DB fields needed.
- `apply_view` checks date range before rendering form.
- Closed form shows a static page (not a 404).

**YAML config**:
```yaml
form:
  open_date: "2025-09-01"    # optional
  close_date: "2025-12-01"   # optional
  closed_message: "Applications for the 2025–26 school year are now closed."
```

**Files touched**:
- `core/services/config_loader.py` — parse open/close dates
- `core/views_public.py` — `apply_view` date check + redirect to closed page
- `templates/apply_closed.html` — new template (simple message + contact link)
- `core/urls.py` — no new URL needed (reuse apply URL, render different template)

**Out of scope**:
- Per-form deadlines (multi-form schools — add later)
- Auto-reopening
- Deadline enforcement on re-apply / edit

**Tests** (`core/tests/test_phase23.py`, ~4 tests):
- `test_form_accessible_within_date_range`
- `test_form_closed_before_open_date`
- `test_form_closed_after_close_date`
- `test_closed_message_from_yaml`

---

## Known Technical Debt

| Item | Priority | Notes |
|------|----------|-------|
| `core/views.py` split | Done | 5 focused files + re-export facade |
| nth-child CSS removed | Done | Replaced with semantic CSS classes |
| Django admin login as school admin login | Fixed in Ops Phase 1 | |
| `CSRF_COOKIE_SAMESITE = "None"` | Low | Required for iframe embed; document why |
| Lead unique constraint | Phase 21 | Will relax to allow multi-child households |

---

## Superadmin Features Reference

### Via Django Admin (`/admin/`)
- School model: full CRUD, plan, feature_flags JSON, all Stripe fields, list filter by plan/status
- SchoolAdminMembership: full CRUD
- Users: create/edit/delete, is_staff/is_superuser flags, password
- Submissions: cross-school access
- Leads: cross-school access
- DraftSubmissions: full access (school admins have none)
- AdminPreferences: read-only debug view
- Reports hub: school picker

### Via School Admin UI (`/schools/<slug>/admin/`) — superuser extras
- Can access any school (not just their own)
- Can access inactive school dashboards
- "Django admin ↗" links on submission/lead detail pages
- Reports always available (ignores `reports_enabled` flag)
- CSV export always available (ignores `csv_export_enabled` flag)
- Leads always available (ignores `leads_enabled` flag)
- Conversion data always shown
- Trial banner suppressed
- Trial end date override form on Settings page

### Via Ops Portal (`/ops/`) — Phase 1 target
- All of the above, in a modern UI, without needing Django admin

---

## Test Standards (non-negotiable)

- **90% code coverage** on all new code
- Tests must be **meaningful** — test behavior, not implementation. Assert real outcomes
  (DB state, response content, redirect URLs), not mock call counts.
- Every new view gets at minimum: auth guard test, happy-path test, error-path test
- Manual QA checklist must be completed before marking any phase done
- Run full suite after every commit: `pytest -q --tb=short` — must stay green

## Test Suite

Run with: `pytest -q --tb=short`

Current baseline: **854 passing, 0 failing**

Test files by feature:
- `test_lead_workflow.py` — Phase 2 (25 tests)
- `test_lead_start_enrollment.py` — Phase 3 (4 tests)
- `test_detail_pages.py` — Phase 4 (20 tests)
- `test_lead_update.py` / `test_lead_ux.py` — Phase 5 (14 tests)
- `test_submission_update.py` — Phase 6 (5 tests)
- `test_export_csv.py` — Phase 7/8 (4 tests)
- `test_create_edit.py` — Phase 10 (10 tests)
- `test_inbox_workflow.py` — Phase 11 (13 tests) [also named test_phase11.py]
- `test_phase12.py` — Phase 12 (8 tests)
- `test_phase14.py` — Phase 14 (10 tests)
- `test_school_admin.py` — general school admin
- `test_submission_status_workflow.py` — status workflow
- `test_polish.py` — Phase 9 (5 tests)
- `test_ops_phase1.py` — Phase 1 ops portal (TBD, 8 tests planned)
