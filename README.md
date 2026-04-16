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

## Key Features

### Multi-School, Single Platform
- One backend, many schools
- Each school has its own URL slug and scoped admin access
- No data leakage between schools

### Dynamic Forms (No Code)
- Forms defined in YAML files — fields, validation, multi-step flows, branding
- New schools onboarded without touching code
- Supports text, select, multiselect, file upload, date, waiver/consent field types

### Lead Capture & Pipeline
- Public lead capture form (separate from application)
- Admin inbox with pipeline status tracking, bulk actions, follow-up scheduling
- Lead → Submission conversion (matched by email field)
- Pipeline analytics in reporting dashboard

### Save & Resume
- Applicants can save a partial form and return via a magic link
- Drafts expire; completed submissions are permanent

### School-Scoped Admin
- School admins see **only their school's data**
- Superusers manage all schools
- Audit log on every write action

### Built-In Reporting
- Date-range filters, program breakdowns, visual charts
- Lead pipeline analytics
- CSV exports — standard flat export or YAML-configured export profiles for third-party systems

### Billing & Subscription
- Stripe-powered subscription with 14-day free trial
- Trial auto-expires and blocks new intake after 14 days
- Plans: Trial → Starter → Pro → Growth
- Self-serve upgrade via Stripe Checkout
- Self-serve billing management via Stripe Portal
- Webhooks keep plan/status in sync

### Branded Experience
- Per-school colors, logos, custom CSS/JS
- Defaults provided if branding isn't configured
- Form embedding via iframe snippet (shown in admin)

### AI Application Summary *(Growth)*
- One-click AI-generated summary of submission data
- Shown in admin submission detail view

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
