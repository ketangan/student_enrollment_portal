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

### Phase 3 (future): Cleanup
- Remove dead code after ops portal fully replaces Django admin for superadmin workflows
- Identify unused views, templates, CSS — list explicitly before deleting
- Run full test suite after each deletion to catch regressions
- Django admin stays (it's the escape hatch) but superadmin-specific Django admin customizations may be simplified
- `core/views.py` refactor: split into `views_public.py`, `views_school_submissions.py`, `views_school_leads.py`, `views_school_admin.py`
- Audit all `is_superuser` template checks to see if any become redundant

---

## Known Technical Debt

| Item | Priority | Notes |
|------|----------|-------|
| `core/views.py` ~5000 lines | High | Split into 4-5 files; no functional change, just organization |
| nth-child CSS removed | Done | Replaced with semantic CSS classes |
| Django admin login as school admin login | Fixed in Ops Phase 1 Commit 7 | |
| `CSRF_COOKIE_SAMESITE = "None"` | Low | Required for iframe embed; document why |

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
