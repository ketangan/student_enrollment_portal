(Sales / Demo / Product Overview)

# Student Enrollment Portal
### Multi-School Online Application Platform

Student Enrollment Portal is a **multi-tenant application platform** that lets schools, studios, and programs collect applications and leads online — without custom development per school.

It replaces PDFs, email, and paper forms with a modern, branded, and secure enrollment experience while running every school on a single backend and database.

---

## Who This Is For

- Dance studios
- Music academies
- Cultural programs
- After-school programs
- Small schools and academies
- Any organization collecting student registrations or applications

---

## What Problems It Solves

- ❌ PDF forms and email chaos
- ❌ Manual data entry
- ❌ No reporting or visibility
- ❌ Custom dev per school

**→ One platform. Many schools. Zero custom code.**

---

## Feature List

### Public Enrollment Forms
- Forms defined entirely in YAML — no code changes required per school
- Multi-step forms with section grouping
- Multi-form support per school (different forms per program, with picker UI)
- Save & resume — applicants get a magic link to return to a partial application
- Field types: text, email, tel, number, date, select, multiselect, textarea, file upload, waiver/consent
- File uploads with configurable accepted types and max size *(flag: `file_uploads_enabled` — Starter+)*
- YAML-configurable submission statuses per school (e.g. New → In Review → Enrolled)
- Application confirmation email sent to family on submit *(flag: `email_notifications_enabled` — Starter+)*
- Scheduling/booking link shown on confirmation page (e.g. Calendly) — YAML-configurable *(Starter+)*
- Rate limiting on public form endpoints

### Form Embedding & Delivery
- **Dedicated hosted page** — standalone application URL at `/schools/<slug>/apply/`
- **Fully embedded** — iframe embedded directly in school's admissions page
- **Modal/popup** — form opens in an overlay on the school's existing page
- **New-tab link-out** — button on school site opens form in a new tab
- Mobile-responsive across all embed options
- School-specific URLs via slug routing

### Application Fee Collection
- YAML-driven per school — enabled via `application_fee.enabled` in school config
- Per-form configurable — different fees for different programs on the same school
- Fee waiver support (e.g. scholarship applications) — waiver reason recorded
- Payment status tracked on every submission: paid, pending, waived, failed
- Fee collection step inserted between form submit and confirmation
- Stripe PaymentIntents on the server side (no card data touches the app)
- School-owned Stripe accounts — each school provides its own keys (`app_fee_stripe_public_key` / `app_fee_stripe_secret_key`); funds go directly to the school, not pooled

### School Branding
- Per-school primary color, accent color, logo, custom CSS, custom JS
- SVG/image logo rendered in form header and admin
- Sensible defaults when branding is not configured
- Branding applied to public forms, confirmation pages, and lead capture pages

### School Admin Portal
- School-scoped access — admins see only their school's data, enforced at view and queryset level
- Superusers manage all schools
- Audit log on every admin write action (add, change, delete, action)
- Inactive-school enforcement — expired or deactivated schools cannot accept new submissions or leads

### Dashboard
- New submissions count with direct link
- Follow-ups due today
- Needs attention count (overdue leads + stale submissions combined)
- Lead → Application conversion rate *(when leads enabled)*
- Active leads not yet converted and applications pending decision — with direct filter links
- Recent activity feed

### Submissions Management
- Search by student name, email, guardian name
- Status filter and smart filters: stale (5+ days no activity), needs follow-up, recent activity, not yet enrolled
- Priority sorting — overdue follow-ups → upcoming → new → rest
- Bulk status update, bulk mark contacted, bulk follow-up scheduling
- Bulk print (multi-submission print view)
- CSV export — flat export of all visible submissions *(flag: `csv_export_enabled` — Starter+)*
- YAML-configurable named export profiles (e.g. Brightwheel) with custom field mappings — no code required
- Internal notes indicator (dot badge) on list view when notes exist

### Submission Detail
- Full application view with field labels resolved from YAML config
- Inline edit — update any form field from the admin without re-submission
- Attachment viewing and download
- AI-generated application summary — one-click, shown inline *(flag: `ai_summary_enabled` — Growth tier)*
- Internal notes — append-only with timestamp, separate from form data
- Audit/activity log — every status change, note, email, and admin action recorded
- Print/PDF-ready layout
- Status management with YAML-defined workflow transitions
- Send message to parent — admin-composed one-off email *(flag: `email_notifications_enabled`)*
- Resend confirmation email to family *(flag: `email_notifications_enabled`)*
- Linked lead (if submission originated from a lead)
- **Family status page** — admin can post public notes visible to the family; shareable token-based URL included in confirmation email *(flag: `family_portal_enabled` — Starter+)*

### Lead Management Pipeline
- Public lead capture form (separate from application form)
- Lead source tracking: website, referral, social media, event, manual entry
- UTM parameter pass-through (source, medium, campaign) captured on lead forms
- YAML-configurable pipeline statuses and workflow transitions per school *(flag: `leads_enabled` — Starter+)*
- Follow-up scheduling with due-date tracking and overdue badge
- Mark contacted — sets `last_contacted_at`, auto-advances status
- Stale lead detection (5+ days no activity)
- Lead → application conversion — admin starts enrollment directly from lead detail page; matched to submission by email
- Manual lead creation and editing from admin
- Bulk mark contacted, bulk follow-up scheduling
- Smart filters: not yet converted, stale
- CSV export with Converted (Yes/No) and Converted At columns
- Lost reason tracking with breakdown in reports

### Communication
- Submission confirmation email to family on submit — includes family status page link when enabled
- Admin-composed one-off email to family from submission or lead detail page
- Status-triggered workflow emails (contacted, follow-up reminder) with optional send checkbox
- Resend confirmation email to family
- YAML-configurable email templates per school with `{{name}}` and `{{school}}` variable substitution
- Per-school from-address configurable in YAML
- Feature flag: `email_notifications_enabled` (Starter+); superusers bypass

### Reports & Analytics
- **KPI tiles** — App→Enrolled rate with basis (N of M), Lead→Application rate, Avg days to enroll
- **Period comparison table** — applications, leads, enrolled counts for current vs previous period with delta
- **Time-series chart** — applications over time (inline SVG, no JS library), 7/30/90-day range selector
- **Enrollment funnel** — all-time leads → applications → enrolled with conversion rates
- **Program mix** — horizontal bar breakdown of applications by program
- **Lead source effectiveness** — application and conversion counts by lead source
- **Pipeline gap analysis** — unconverted leads and pending applications with direct action links
- **Stale pipeline** — leads and submissions with no activity in 5+ days
- CSV export of filtered report data
- Feature flags: `reports_enabled` (Starter+), `csv_export_enabled`; superusers bypass both

### Feature Flag System
- Plan-based gating — Trial → Starter → Pro → Growth unlocks features cumulatively
- Per-school JSON overrides — any flag can be forced on or off for a specific school regardless of plan
- Named flags: `leads_enabled`, `reports_enabled`, `csv_export_enabled`, `email_notifications_enabled`, `ai_summary_enabled`, `file_uploads_enabled`, `family_portal_enabled`
- Application fees are YAML-gated (not plan-gated) — enabled per school in config

### Stripe Billing & Trial
- Stripe-powered subscription billing with plan tiers (Trial → Starter → Pro → Growth)
- 30-day free trial — full-featured, time-limited
- Trial countdown banner shown to school admins in the final 10 days (threshold configurable in one place: `TRIAL_BANNER_THRESHOLD_DAYS` in `models.py`)
- Trial expiry enforced — new submissions and leads blocked after expiry
- Self-serve upgrade via Stripe Checkout
- Self-serve billing management via Stripe Customer Portal
- Webhooks keep plan and subscription status in sync

### Superadmin Ops Portal
- Create and manage schools (slug, plan, branding, trial dates)
- Manage school admin memberships
- Cross-school activity dashboard and reporting
- Trial end date override per school
- Application fee Stripe key management per school
- Demo data generation and management

### Sales Demo System
- Shareable demo environments at `/demo/<slug>/`
- Four form integration options per demo: dedicated page, modal, embedded bottom section, new-tab link-out
- School-branded demo pages (logo, colors, copy) without affecting live schools
- AI-assisted demo generation — scrape school website, auto-generate YAML form and branding
- Seed command for realistic demo data generation (`python manage.py seed_enrollment_demo`)

### Tenant Isolation & Security
- All queries scoped to school at view and queryset level — no cross-school data leakage
- Inactive and expired-trial schools blocked from all public intake endpoints
- Rate limiting on all public form and lead capture endpoints (429 enforcement)
- Audit logging on every admin write action
- `X-Frame-Options: SAMEORIGIN` — iframes allowed only on same domain

---

## Plan Feature Tiers

Every school runs on a **plan** that unlocks features cumulatively:

| Feature                         | Trial | Starter | Pro | Growth |
|:--------------------------------|:-----:|:-------:|:---:|:------:|
| Application forms (YAML)        | ✅    | ✅     | ✅  | ✅     |
| Submission review & status      | ✅    | ✅     | ✅  | ✅     |
| CSV export                      | ✅    | ✅     | ✅  | ✅     |
| Audit log                       | ✅    | ✅     | ✅  | ✅     |
| Reports & charts                | ❌    | ✅     | ✅  | ✅     |
| Email notifications             | ❌    | ✅     | ✅  | ✅     |
| File uploads                    | ❌    | ✅     | ✅  | ✅     |
| Lead capture & pipeline         | ❌    | ✅     | ✅  | ✅     |
| Waiver / consent field          | ❌    | ✅     | ✅  | ✅     |
| Scheduling link config          | ❌    | ✅     | ✅  | ✅     |
| Family status page              | ❌    | ✅     | ✅  | ✅     |
| Custom branding (CSS/JS)        | ❌    | ❌     | ✅  | ✅     |
| Multi-form / multi-step         | ❌    | ❌     | ✅  | ✅     |
| Custom statuses                 | ❌    | ❌     | ✅  | ✅     |
| Lead → Submission conversion    | ❌    | ❌     | ✅  | ✅     |
| Save & Resume drafts            | ❌    | ❌     | ✅  | ✅     |
| YAML export profiles            | ❌    | ❌     | ✅  | ✅     |
| AI Application Summary          | ❌    | ❌     | ❌  | ✅     |

Per-school feature flag overrides let you enable or disable any feature individually.

---

## How It Works (High Level)

1. Each school gets a unique slug (e.g. `dancemaker-studio`)
2. Visiting `/schools/<slug>/apply` shows that school's branded application form
3. Submissions are validated, stored, and optionally trigger a confirmation email
4. School admins log in to review submissions, manage leads, and view reports
5. Schools start on a 14-day trial; after the trial they subscribe via Stripe to continue

---

## Security & Isolation

- Schools **cannot access each other's data** — enforced at every layer
- Admin permissions checked at view, queryset, and model level
- JSON storage allows flexible per-school schemas without migrations
- Inactive and expired-trial schools are blocked from accepting new intake

---

## Deployment

- Built with Django + PostgreSQL
- Cloud-ready (Render / Fly / Heroku-style platforms)
- Email via Resend
- Payments via Stripe (test mode and live mode)
- Unit + integration tests (578+ passing); Playwright E2E tests

---

## Status

**Production-ready**

Live feature set:
- Application intake (dynamic YAML forms, file uploads, waivers, save & resume)
- Lead capture, pipeline management, lead-to-submission conversion
- Admin review, status tracking, audit log, reports, CSV export
- Stripe billing with 14-day trial, plan tiers, and lifecycle enforcement
- AI-powered application summaries (Growth tier)
- Per-school branding, embed snippet, scheduling link

---

## Contact / Demo

If you're interested in a demo or pilot:
> Reach out to the project owner for access and walkthroughs.
