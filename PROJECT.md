# Student Enrollment Portal — Project Source of Truth

This file is the canonical reference for decisions made, work completed, work in progress,
and work planned. Update it after every meaningful commit or decision. It is written for
both human and AI readability — future sessions should read this before doing anything.

October 10 release completed: revision `31220c5` is live on the existing Render service
and shared database after CI passed. All 28 migrations ran automatically. Hosted Plushumi
is a demo-only tenant; SBMC scheduling remains disabled and its active subscription and
live enrollment pages passed verification. Real Stripe/Wix/Notion connections remain off.
The comparison hub is https://demo.mypontora.com/demo/plushumi/integrations/.
Read [the production release checklist](docs/scheduling-shared-production-release.md)
for migration rehearsal, regression evidence, rollback and final deployment status.

October 10 post-release audit: SBMC is active at USD 24.99/month in Stripe, with no
pending cancellation or payment deadline. Shared billing code WAS included in the
release, but the existing Stripe endpoint subscribes only to checkout/subscription
events, not the invoice events required for new alerts/recovery. Provider setup and
end-to-end email verification remain open; do not claim those are fully operational.
See [payment activation status](docs/payment-alerts-and-recovery.md) and milestone **B1**
in the [implementation tracker](docs/scheduling-implementation-tracker.md#manual-testing)
for the open setup, delivery and recovery checklist.
The documentation-only CI failure was a real-clock rate-window test assumption;
the deployed merge's CI passed. Its correction is test-only, with no production restart.

Kid Works go-live (October 8, 2026): the agreement is signed; configuration and launch
acceptance are pending. Use the [Kid Works go-live checklist](docs/kidworks-go-live.md)
before making changes. Dinuka's meeting requirements supersede the outdated website
and original demo: playdates follow enrollment, one new child per slot, personal
follow-up preserved, and Dinuka handles Brightwheel uploads. The checklist separates
internal engineering work from customer decisions and records launch/test gates.

KW-05 (October 8): package/trial controls are implemented and locally verified;
see [impact analysis, tests and rollout notes](docs/kidworks-kw05-verification.md).
Kid Works has a reviewed per-school Starter override configuration and an explicit
60-day trial-start workflow tied to the sent written go-live notice. Demos and setup
do not consume that trial; checkout is blocked through its free period. Normal live
schools retain existing defaults. The code was deployed October 10, but is not activated for Dinuka;
KW-01/02/04 and the clean production launch/acceptance gates remain pending.

Historical October 8 release packaging (superseded by the October 10 release above):
KW-05 and shared payment alerts/recovery were isolated from
the pending scheduling release. The enrollment-only release passes 2,079 tests; the full
local workspace passes 179 focused integration tests after optional-module separation.
Desktop/mobile billing checks passed. See [payment release notes](docs/payment-alerts-and-recovery.md)
for migration, webhook and email deployment prerequisites. Scheduling-specific hooks and
the rest of the scheduling work remain local; do not interpret this push as their release.

Browser view (local only): [Project workspace](http://127.0.0.1:8003/).
Start with `venv/bin/python scripts/project_status.py` (development requirements required).
The read-only viewer reads this file and the scheduling tracker on each refresh. It is not
part of the school application or production deployment.

Working rule (October 3, 2026): when defects are found during a task, fix them before
handoff, with regression tests, impact review and relevant browser checks. Do not merely
report known defects and declare the task complete. If a correction needs a product decision,
new external access or could affect unrelated live behavior, state that limitation explicitly
instead of guessing or silently widening scope. Keep unfinished features distinct from defects.

October 6 design-editor visual correction: replace oversized outlined color cards with compact
aligned color/photo rows and shared column headers. Main fields align side by side on laptops;
phone controls retain usable sizing and accessible labels. 177 affected regression tests plus
desktop/390px, dark-theme and invalid-input browser checks passed. Presentation-only, with no
saved-data, backend, migration, shared enrollment or payment changes. No push. See the
[visual correction notes](docs/scheduling-plushumi-demo.md#compact-design-editor).

October 6 customer booking refinement: compact calendar/time slots, closely grouped colors,
larger selected-plushie preview, and participant thumbnails through review/checkout/confirmation.
Pasadena Cozy Creations adds three October 11 sessions and four confirmed bookings (seven people)
without resetting existing records. 140 affected Python tests and 38 JavaScript tests passed;
98.84% measured Python coverage, with the guarded additive seed at 100%. Desktop/mobile customer
checkout and admin visibility verified. No migration, shared Enrollment/payment edit or push.

October 6 catalog settings refinement: compact option rows place muted edit icons beside values.
Color Add/Edit now requires a visual picker/editable hex instead of optional hex/image fields;
existing image data and design-specific photos are preserved. 183 affected Python tests and 38
JavaScript tests passed; inventory forms/views have 97.08% combined coverage and the new picker
100% line/branch/function coverage. Desktop/390px checks passed. No migration, shared Enrollment
or payment edit, saved demo-data change or push. See the demo guide and tracker manual checks.

October 6 catalog usability: workshop links open their assigned catalog; designs use color swatches
and independent per-color photo URLs; shared booking choices live under Catalog settings. Recipes
use remove-row icons; materials show distinct On hand/Reserved values and configurable low-stock
warnings. Scheduling-only date/time inputs now use the supplied calendar style, backed by vendored
Flatpickr. Additive migration 0023 is isolated-preview-only; no data reset or shared enrollment/payment
runtime edits. 3,454 full regression tests, 174 focused Python, 35 JavaScript and 64 tracker tests passed,
each changed inventory module above 95% combined coverage, with a 90% JavaScript gate. Desktop/390px browser checks passed. See the
[current verification guide](docs/scheduling-plushumi-demo.md#catalog-and-scheduling-usability)
and tracker for final full-suite results. No push or deployment.

October 6 inventory correction: Catalog designs/colors are now separate from raw-material stock.
Each variant has a per-participant recipe; bookings reserve combined materials per studio and
freeze that recipe. Preparation changes readiness only; completion consumes once; cancellation
releases unused holds. Scheduling migration 0022 preserves other tenants' old kits and balances.
Only the isolated Plushumi preview was reset (backup first) to 9 designs, 81 color recipes,
11 materials at 5 studios and 15 reservations. Other-school data fingerprints are unchanged.
No enrollment/shared-payment runtime edits, production activation or push in this change.
3,434 full regression tests, 141 final focused checks, 16 JavaScript and 64 tracker checks passed;
changed inventory modules have 96.27-100% combined coverage. Desktop/mobile booking, setup,
stock receipt, prep/completion and audit were exercised. Ready for user testing; recipe amounts
are illustrative and connected Wix/Notion remains deferred. See the [current demo guide](docs/scheduling-plushumi-demo.md)
and P3's current manual checks in the tracker; old finished-item stock walkthroughs are superseded.

October 4 priority: finish a usable, narrow workshop pilot before broadening the music product.
P1 (see browser tracker) adds operator-granted Bookings capabilities, a daily workspace and
portable test safeguards. M4 follows with authorized Wix/Notion proof; no live integrations
are authorized yet. Existing M7 local demonstrations remain available; remaining M7c2/M8
work is deferred. No SBMC activation or enrollment behavior change is part of P1.
P1 is now ready for user testing: 2,861 tests passed; scoped statement/branch coverage is
99.91%, with each measured module above 90%. Desktop/mobile request-to-staff review and
history-preserving feature controls were verified. See [P1 verification](docs/scheduling-p1-verification.md)
and the tracker for manual steps. The connected customer pilot is not complete or approved for rollout.

October 4 integration requirement: providers must be independently configurable per business.
M4a implements a source/destination registry, settings and encrypted credentials, plus offline
destination projections. Wix + Notion is an initial combination, never hardcoded into booking logic.
New providers require a bounded adapter and contract tests; arbitrary URLs/scripts are not user settings.
Saving configuration cannot switch active inventory/payment authority or start external delivery.
M4a is ready for user testing: 2,901 tests passed in 143.28 seconds; new integration Python code
has 100% statement/branch coverage, with 99.92% across the measured scheduling/payment/test-safety
modules. Desktop/mobile save, source switching, invalid mapping rejection and restart persistence
were verified. Only the isolated Workshop Demo is enabled. Authentication/permission verification,
webhooks and real delivery still need implementation and authorized provider testing. See
[M4a verification](docs/scheduling-m4a-verification.md) and its expandable tracker steps.

October 4 revised priority: defer connected M4 pending Plushumi's workflow and authorized
provider access. M9a is accepted for progression: native tour slots, capacity-protected bookings,
attendance/cancellation and an opt-in lead connection. Final local regression: 2,960 tests
passed; four new runtime modules have 100% statement/branch coverage; every measured module
passes 90% (99.92% combined). Desktop/mobile, room/host conflict, capacity, lead updates and
restart persistence were checked. See [M9a verification](docs/scheduling-m9a-verification.md)
and the tracker for the preschool preview and manual steps. One additive scheduling migration
was applied only to the isolated preview. No existing school is activated or changed by default.
M9b is now ready for user testing: optional completed-tour/application-release rules, exact
lead/draft/submission identity, staff or automatic playdate release, real capacity-protected
playdates, and parent/staff progress panels. Existing resume/application/payment entry points
enforce the same release policy. The shared enrollment hooks default to legacy behavior without
an enabled admissions policy; only the fictional preschool preview is enabled. SBMC and Kid Works
are not reconfigured. Shared name/search helpers add a last-resort single `student_name` fallback;
existing field precedence is preserved. No second payment module was added.

M9b verification: **3,007 tests passed**, two existing warnings, 183.81 seconds. Five new
admissions modules have **99.03% combined statement/branch coverage**; every measured module
passes the 90% gate (99.86% combined across scheduling/shared payments/test safeguards).
Desktop/mobile application/approval/playdate flow, invalid capacity/settings, cancellation,
rebooking and restart persistence were checked; existing SBMC/Kid Works public forms remain
accessible without the admissions gate. One additive M9b migration was applied only to the
isolated preview. Read [M9b impact and verification](docs/scheduling-m9b-verification.md) and
expand M9b in the tracker for local credentials and repeatable manual steps.

M9c is the current demo-readiness slice: single-use email return links, revocable visit sessions,
local notification/retry previews, atomic family/staff rescheduling, audited attendance correction,
finite weekly/biweekly times, editable closures, and optional host/music-teacher conflict mapping.
Setup and a complete fictional preschool journey are available in the isolated preview.
M9c is ready for user testing: **3,055 tests passed**, two existing warnings, 202.43 seconds.
Six new runtime modules have **98.92% combined statement/branch coverage**; every measured
module exceeds 90%. Desktop/mobile, restart persistence and existing-school public forms were
checked. See [M9c evidence](docs/scheduling-m9c-verification.md) and its expandable manual steps.
The generic configuration is opt-in; SBMC and Kid Works remain unmodified and disabled for M9.
The additive migration 0018 was applied only to the isolated Bookings preview, not the main
local database or production. Local preview payment routes fail closed for policy-bound
applications; no second payment implementation and no real-money certification are implied.

October 5: M9d prepares `enrollment-request-demo` (Riverside Learning Academy) as the
integrated local demonstration on port 8002. An ordinary `demo_admin` / `admin` account
owns enrollment and scheduling. All implemented capabilities have fictional practice data;
school navigation and Settings expose Bookings only to enabled, authorized members.
The original enrollment database on port 8001 remains untouched. No SBMC/Kid Works activation,
new migration, live calls or push. Read the [school demo walkthrough](docs/scheduling-integrated-demo.md).
M9d is ready for user testing: **3,076 tests passed**, two existing warnings, 198.94 seconds;
every measured module meets the 90% statement/branch coverage gate. New navigation/tag modules
are 100%, the guarded fixture command 94.57%. Desktop/mobile settings, tour-to-application-to-
playdate, simulated deposit, lesson reschedule preview and restart persistence were verified.
Acceptance remains pending; see the M9d tracker entry.

October 5: P2 is ready for user testing: a separate, branded Plushumi local working demo matching
the six shared concept screens. It reuses saved requests, native capacity, the shared test
payment ledger and offline Notion projection; Wix checkout/Notion delivery are explicitly
simulated, not claimed as working integrations. The original shared storyboard and the
Enrollment Request Demo being tested remain unchanged. See
[Plushumi walkthrough](docs/scheduling-plushumi-demo.md) and P2 in the browser tracker.
Final P2 verification: 3,119 tests passed, two existing warnings, 207.94 seconds. New Python
code has 99.58% combined statement/branch coverage; every measured module meets 90%.
Desktop/mobile customer and staff flows, failure/cancellation, mapping and restart persistence
were verified. Local `plushumi-admin` / `admin` owns scheduling only. No migration or push.

October 5: P3 adds an optional shared inventory catalog with per-studio stock across workshops,
parties, camps and preparation. Reservation holds, consumption and adjustments share existing
tenant locks and payment reconciliation; no second payment module is added. Staff/settings UI
uses the enrollment theme through scheduling-scoped CSS; enrollment templates/assets remain
unchanged by this slice. Migration 0019 is applied only to the isolated port-8002 preview.
Only the Plushumi fixture is assigned shared inventory; no SBMC/Kid Works configuration change.
P3 is ready for user testing: **3,138 application/regression tests passed**, two existing
warnings, 237.18 seconds; additional tracker checks passed. The five new inventory modules
have **98.64% combined statement/branch coverage** and every measured module exceeds 90%.
Browser checks covered workshop consumption, party materials, sold-out choices, protected
adjustments, desktop/mobile layouts, existing-school forms and restart persistence.
See [P3 evidence](docs/scheduling-p3-verification.md) and expand P3 in the browser tracker
for manual tests and local credentials. No external API calls, push or deployment.

P3 usability follow-up: reservation availability controls are separated from filters;
catalogs have one Manage action and expandable studio assignments; option editing has
aligned labeled icons; saving stock retains the studio. Latest full suite: **3,145 passed**,
two existing warnings, 243.95 seconds; inventory coverage **98.64%**, all measured modules
above 90%. Desktop/mobile and keyboard checks passed. This follow-up makes no enrollment
template/configuration changes or migrations. Navigation grouping and richer reservation
filters were follow-ups at that point; the October 6 cleanup below implements them.

M9 is not production-complete. Real email delivery, paid-flow certification, customer policy
mapping, operational onboarding and explicit rollout approval remain open. Visit access
revocation does not revoke existing enrollment draft/status bearer links. No live messages,
external provider calls, production migration or push occurred. Connected M4 is still deferred
pending Plushumi input. Recurring tuition remains the separate M8 milestone.

October 5 workshop demo audit: corrected paused-intake access, invisible stock errors, group
stock validation, exact-stock checkout retries, missing native bookings/selections in prep,
catalog image identity and narrow prep layout. Full regression: **3,149 passed**, two warnings;
experience Python coverage **99.07%**, every measured module above 90%. Browser rehearsal
covered edit/failed-checkout/retry, saved staff records and desktop/mobile layouts. Native
print output is unverified. No new migration or enrollment runtime changes in this audit.
The demo is not a finished self-service workshop product: native pricing/policy settings,
branded party/camp journeys, customer communication/changes and connected M4
remain tracked gaps. See [the workshop audit and call sequence](docs/scheduling-plushumi-demo.md).

October 6 workshop polish: reversible Prepared/Reopen status is independent of stock usage
and payments; navigation is grouped with a compact mobile menu; reservation search includes
contact/workshop, studio, status and studio-local date ranges; catalog stock items are searchable
and paginated. **3,161 tests passed**, two existing warnings, 256.35 seconds. New preparation
and filter modules have **100% Python statement/branch coverage**; every measured module
exceeds 90% (99.52% overall). Desktop/mobile, keyboard, concurrent/stale edits and restart
persistence passed. Additive scheduling migration 0020 was applied only to isolated preview/test
databases. No enrollment template/configuration changes, SBMC/Kid Works activation, provider
calls, push or deployment. P3 manual steps and the browser demo guide are updated. Prepared
is now implemented; pricing/policy administration, branded party/camp journeys, customer
communications and connected M4 remain separate unfinished product work.

October 6 navigation update: Bookings now uses Student Enrollment's header, colors,
typography, theme preference and underlined section tabs. Main menus are Dashboard,
Reservations (Reservations, Workshop Prep), Setup (Workshop, Parties, Camps, Inventory),
and Settings (Location & rooms, Team Access, Integrations). Requests is removed from the
menu, not from stored records or existing URLs. Optional tours, lessons and messages remain
under Reservations for enabled workspaces; owner-only links retain their access checks.
This update is scoped to scheduling templates/styles/navigation; no shared enrollment assets,
school configuration, payment behavior or schema changed. Port 8002 was refreshed.
Verification: **3,217 regression tests passed** (two existing warnings, 249.17 seconds),
plus five JavaScript menu tests. Changed Python navigation/context code and JavaScript
menu code are **100% covered**; the combined Python coverage is 99.53%, with every
measured module above 90%. Desktop/mobile, all section destinations, keyboard focus and
theme persistence checked. No push or deployment.

October 6 component/location follow-up: staff tables, row hover, badges, numeric columns,
forms, buttons, pagination and summary counts now follow the enrollment visual system.
Dashboard has an optional, tenant-validated Location filter beside the date/account controls;
attention counts and upcoming workshop/camp sessions, parties, lessons and visits are scoped
before limits are applied. Attention drilldowns, review tabs, pagination and the workshop list
retain the location. Invalid/foreign selections fail closed; the setup checklist is explicitly
workspace-wide. Fixed local-day agenda formatting and a mobile table overflow caused by
an offscreen accessibility label. Public branding and shared enrollment assets are unchanged.
Verification: **3,251 regression tests passed** (two existing warnings, 249.57 seconds),
followed by focused final template checks and **six JavaScript tests**. All seven changed
Python modules and the admin JavaScript have **100% statement/branch coverage**; measured
Python overall is **99.53%**, every module above 90%. Desktop, phone and 901px boundary,
filtered drilldowns, form errors, keyboard table scrolling and Midnight theme were checked.
No schema, payments, provider calls, school activation, push or deployment. Local port 8002
refreshed; repeatable manual checks are in P3 steps 21-23 of the browser tracker.

October 6 visual-parity correction supersedes the earlier broad visual-completion claims:
the initial pass missed cross-page header alignment, persistent location selection,
Enrollment's actual grid structure and Recent Activity. Those are now corrected using
Enrollment components/conventions, with location saved per workspace/session across staff
sections. Catalogs and team/integration settings remain workspace-wide. Reservation KPIs
and Recent Activity use real scoped records; consumed materials are excluded from To prepare.
Verification: **3,272 regression tests passed** (two existing warnings, 251.80 seconds),
seven JavaScript tests at 100% coverage; measured Python coverage **99.54%**, every module
above 90%. Actual Enrollment/Bookings desktop comparison and native 362px mobile checks
covered menus, grids, location persistence, search/sort/date errors, prep lists and themes.
No shared Enrollment asset/runtime edits or new migration in this correction; no external
provider calls, push or deployment. See [corrective evidence](docs/scheduling-p3-verification.md#october-6-visual-parity-correction)
and P3 manual steps 19-24. Local port 8002 is refreshed.

October 6 reservation workspace update: Workshop Prep is record-specific, with Pending/Complete
indicators in the main list. Reservation information, Money and Audit log are separate;
Room/participants/materials are in Prep. Cancellation requires an explicit with/without-refund
choice and reason, reusing shared payments and preserving retry/rollback safety. The manual
offline-settlement UI/handler is removed; the shared historical ledger/internal local simulator
remain. Exact local-demo receipts support simulated refunds; external offline receipts do not.
Verification: **3,304 full regression tests**, then **262 focused final checks**, plus seven
JavaScript tests. New cancellation/audit/workspace modules are **100% statement/branch covered**;
all measured modules pass 90% (99.54% overall). Desktop/mobile and simulated prep/cancellation
journey tested. No new migration, shared Enrollment/SBMC edit, real provider call or push.
See [reservation verification](docs/scheduling-p3-verification.md#october-6-reservation-workspace)
and P3 steps 25-27 in the browser tracker. Port 8002 refreshed.

October 6 reservation completion update: both tabs now share Participants & selections, with
separate saved-choice columns. Money is renamed Payment summary. Prep has a collapsed reservation
summary and studio-scoped materials without the redundant studio column. Workshop completed
replaces manual stock-use controls: confirmation atomically consumes unused allocations, saves
Completed and audits the action. Duplicate/concurrent retries do not consume twice; payments
remain separate and late callbacks cannot reopen a completed reservation.
Verification: **3,327 full regression tests** (272.45 seconds), then **412 final focused checks**
(42.48 seconds) after refund safety/template polish, plus seven JavaScript tests. Completion,
participant read models, preparation and booking checkout are **100% statement/branch covered**;
every measured module exceeds 90% (99.53% overall). Desktop/mobile and a new fictional customer-to-
admin completion journey passed, including restart persistence. Scheduling-only migration 0021
was applied to test/isolated preview databases only; no shared Enrollment/SBMC edits or external
calls. No push/deployment. See [completion evidence](docs/scheduling-p3-verification.md#october-6-completion-and-participants)
and P3 steps 28-31. Port 8002 is ready for testing.

October 6 reservation layout follow-up: Prep's Participants & selections now lives inside the
initially collapsed Reservation details section. Explicit grid columns and wrapping prevent
header/content overlap. The local Plushumi demo has its existing logo, a sample-data strip and
a hardcoded seven-day trial notice, with no trial functionality. Header navigation matches
Enrollment's 60px height plus border, retaining a separate mobile location row.
Verification: **3,340 regression tests passed**, seven JavaScript checks and 64 tracker checks;
99.53% Python coverage, all measured modules above 90%. Five live-browser viewport sizes,
keyboard scrolling, collapsed/expanded details and header alignment passed. No database
migration, shared Enrollment/SBMC edits, live integration calls or push. See
[layout evidence](docs/scheduling-p3-verification.md#october-6-grid-and-demo-header-polish)
and P3 steps 29, 32-33. Port 8002 refreshed.

Subsequent comparison against the live local Enrollment grid corrected content width, row/button
density, muted secondary text, divider contrast and scrolling-card structure. The fixed trial
notice is seven days and Location's caption is visually hidden, retaining its accessible label.
**3,340 full tests passed** (281.83 seconds), plus 134 focused, seven JavaScript and 64 tracker
checks. Five responsive sizes, sorting, keyboard scrolling, studio selection, Inventory and dark
theme were verified. No Enrollment/SBMC file changes or new migrations. See
[comparison evidence](docs/scheduling-p3-verification.md#october-6-enrollment-grid-comparison)
and P3 steps 32-34.

Original next-priority decision: after connected M4, prioritize a configurable preschool
tour -> application -> playdate workflow as the first M9 enrollment-integration slice, before
broader music/recurring-billing expansion. Reuse the reservation engine with appropriate generic
visit terminology and an opt-in enrollment connector. Current Kid Works demo date selections
are not capacity-controlled reservations and do not implement this connection. Keep SBMC and
standalone bookings unchanged; re-estimate the concrete preschool scope before coding. This
priority does not waive M4's provider proof/recovery checks or any deployment approval.

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
| Scheduling calendar | Optional Bookings setup, workshop/party intake and local offline-only reservations implemented; online payments/calendar still gated | Standalone or enrollment-integrated; default off for existing schools. See scheduling tracker below. |

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

## Completed Features

### School Admin UI (`/schools/<slug>/admin/`)

| Phase | What Was Built | Test file |
|-------|---------------|-----------|
| 2 | Leads pipeline workflow — status transitions, `admin_lead_yaml.py` | `test_lead_workflow.py` |
| 3 | Lead → Submission conversion; `DraftSubmission.lead` FK; migration 0026 | `test_lead_start_enrollment.py` |
| 4 | Submission detail + Lead detail pages; Django admin fallback links | `test_detail_pages.py` |
| 5 | Lead edit: notes, follow-up date, Mark Contacted | `test_lead_update.py`, `test_lead_ux.py` |
| 6 | Submission internal notes (`Submission.internal_notes`); migration 0027 | `test_submission_update.py` |
| 7/8 | CSV export for submissions + leads; shared filter helpers | `test_export_csv.py` |
| 9 | Mobile hamburger nav, responsive detail grid, clipboard copy buttons | `test_polish.py` |
| 10 | Admin create/edit for leads and submissions; multi-form school support | `test_create_edit.py` |
| 11 | Follow-up system (`next_follow_up_at`, `last_contacted_at`); migration 0028; smart filter pills; bulk actions | `test_phase11.py`, `test_inbox_workflow.py` |
| 12 | Email: send message to family, resend confirmation, workflow notifications | `test_phase12.py` |
| 13 | Hardening: cross-school 404s, config=None resilience, mark-contacted idempotency, draft select_for_update | `test_phase13.py` |
| 14 | Conversion intelligence: funnel metrics, trend stats, pipeline gaps, stale counts; `not_converted`/`not_enrolled` filters | `test_phase14.py` |
| 15 | Family status page (token-based public URL); admin post-public-note; confirmation email status link | `test_phase15.py`, `test_family_status_page.py` |
| 17 | Billing alignment: `feature_disabled.html` with upgrade hints; billing nav fixes | `test_phase17.py` |
| 18 | Application fees (Stripe, school-direct); `apply_payment_view`; `Submission.payment_status` | `test_phase18.py` |
| 19 | Enrollment analytics reports: leads funnel, program mix, time-series, source rows | `test_phase19_reports.py` |
| — | Enrollment capacity limits: per-program YAML soft caps; waitlist banner; admin badge; capacity override with audit log | `test_capacity.py` |
| — | AI-generated submission summary | `test_ai_summary.py` |
| — | Generic CSV export profiles (YAML-configured field maps, e.g. Brightwheel) | `test_export_profiles.py` |

### Ops Portal (`/ops/`)

| Phase | What Was Built | Test file |
|-------|---------------|-----------|
| Ops 1 | Auth guard, dashboard, schools CRUD, memberships inline, users CRUD, login/logout redirects | `test_ops_phase1.py` |
| Ops 2 | Cross-school submissions list, leads list, aggregate reports | `test_ops_phase2.py` |

### Platform / Infrastructure

| What | Detail |
|------|--------|
| Trial system | `School.trial_started_at` + `TRIAL_LENGTH_DAYS=30`; trial banner in school admin (hidden from superusers) |
| Trial end date override | `School.trial_end_date` DateField (migration 0029); superadmin can extend/clear |
| Stripe billing | Checkout, webhooks, subscription lifecycle; `billing_stripe.py` |
| Rate limiting | `django_ratelimit` on public form endpoints |
| Password change | Custom school admin page at `/schools/<slug>/admin/password/` |
| Views refactor | `views.py` split into `views_public.py`, `views_school_common.py`, `views_school_dashboard.py`, `views_school_submissions.py`, `views_school_leads.py`; `views.py` kept as re-export facade |
| Custom login | Standalone `/login/` page; superuser → `/ops/`, school admin → school dashboard |

---

## Current State

- SBMC trial/lead forms now use the approved website's Manrope typography, navy text/focus styles and orange pill button, with the About page’s #EDF1FA light-blue card background and white input fields. Scoped through the lead-form body class in SBMC's custom stylesheet; enrollment application styling and other schools are unchanged. Manrope fonts and license are bundled locally. Verified 37 lead-form tests plus 520/390/320px rendering and browser email validation. Pending production deployment.
- **974 passing tests**
- All views refactored — `core/views.py` is now a thin re-export facade
- Ops Portal (Phases 1 + 2) complete — Django admin kept as raw escape hatch only
- Custom login/logout flow in place (no longer through Django admin)
- AI summary prompt hardened: summaries must stay evidence-based, avoid admission/enrollment recommendations, avoid protected-trait/sensitive inferences, and avoid medical/developmental/behavioral diagnosis.

---

## Ops Portal — Architecture Notes

- `/ops/` manages the **business** (schools, plans, billing, users, memberships)
- `/schools/<slug>/admin/` manages the **work** within a school (submissions, leads, reports)
- Django admin at `/admin/` stays alive as raw data escape hatch — do not remove
- `ops_required` decorator guards all `/ops/` views: `is_superuser` check, redirect to `/login/`

---

## Planned Features (Prioritised)

### Optional Bookings Module

October 3 requirements audit: the one-repository, optional standalone Bookings direction is
aligned, but client rollout is not cleared. The two reproduced defects are now corrected locally:
confirmed balance checkout survives intake pause, and stale setup edits are rejected across
workshops, rooms, locations, sessions, choices, camps and lessons. Mixed online/offline
settlement, selective capability packaging, scoped staff access and portable CI safety gates
are also incomplete. Jasmine's Wix/Notion outcome remains deferred M4, not delivered by the
native music milestones. See the [requirements and rollout review](docs/scheduling-alignment-review.md)
and tracker before adding features. Corrective work stays inside Bookings; no enrollment behavior,
payment provider code, schema, production settings or deployment was changed by these fixes.

Design and visual concepts exist; M1a access/workspace foundation is accepted locally
(not deployed). M1b setup, M2 requests/preparation and M3 party inquiries/review are accepted.
M5a is accepted: native capacity holds and a shared order/ledger with offline-only test receipts.
M5b is accepted locally: account-scoped checkout attempts and verified deposit/balance settlement.
M5c adds durable recovery, safe checkout closure, owner-approved partial refunds and payment
follow-up controls. The local build is ready for user testing; the user explicitly deferred real
sandbox verification. Refunds reuse the same shared ledger and Stripe facade, not a second module.
Each business will own its receiving Stripe account, separate from Pontora subscriptions.
M4 is deferred at the user's request; Jasmine remains the commercial priority.
M6 is ready for user testing locally: owner-configured fixed-session camps, atomic all-date registration,
shared capacity with workshops, per-attendee choices, confirmed preparation rosters and one
shared payment order. The user requested progression from M5c on October 2. No enrollment or
core payment behavior is changed for this milestone. See [M6 verification](docs/scheduling-m6-verification.md).
M6 verification: 2,402 full-suite tests passed in 74.35 seconds; 99.93% combined scheduling/payments
line/branch coverage, with new camp forms/services/views at 100%. Desktop/mobile registration,
simulated deposit, all-date cancellation, daily preparation and migration preservation verified.
M6 is accepted by the October 2 request to proceed. M7 is split into M7a setup/finite lessons,
M7b1 staff changes/attendance, M7b2 make-up policies, and M7c guardian access/self-booking. M7a is accepted
for progression by the October 3 request. It is available in
the isolated Music Studio Demo: generic services, qualified teachers, location-specific hours,
lesson closures, family/student records, atomic weekly schedules and staff cancellation.
No payment, notification or enrollment transitions occur. A new default-off lessons capability
does not enable SBMC. Shared-room checks protect workshops, camps and parties in both directions.
Emily's policy questions and a ready-to-send note are in M7a's tracker notes.
M7a verification: 2,461 full-suite tests passed; 100% Python line/branch coverage for the new
lesson models/forms/services/views. Desktop creation, conflict rejection, closure handling,
daylight-saving stability, sibling schedules and single-date cancellation passed. Mobile
form/schedule layout and restart persistence also passed after an initial browser-control
timeout was resolved. See [M7a verification](docs/scheduling-m7a-verification.md).
M7b1 is accepted for progression by the request to start M7b2: preview/confirm single or future scheduled-date changes,
eligible substitutes and room changes, preserved original/replacement links, and staff attendance
with audited corrections. No billing or enrollment effects. Historical M7b1 verification:
2,521 full-suite tests in 96.03 seconds; lesson Python line/branch coverage 100%; scheduling/shared
payments coverage 99.95%. Desktop/mobile rescheduling, attendance corrections and viewer access
passed. Self-review fixed stale cancellation scope; browser review removed a hidden-field label.
No shared enrollment/payment behavior changed for M7b1. See
[M7b1 verification](docs/scheduling-m7b1-verification.md).
M7b2 is accepted for progression: default-off make-up policies, explicit cancellation previews,
one-student/service/duration credits, expiry, atomic conflict-checked redemption, restoration,
forfeiture and owner revocation with history. Rescheduling preserves the original credit/expiry;
lesson entitlements are not money refunds. Only Music Studio Demo is configured with fictional
rules. Isolated migration 0009 adds credit/policy/cancellation/event tables without changing
existing school plans, lesson history or payment data. Final full verification: 2,623 tests
passed in 107.78 seconds, credit code 100% line/branch coverage; evidence and manual testing steps are in
[M7b2 verification](docs/scheduling-m7b2-verification.md) and the browser tracker. No SBMC
enablement, shared enrollment/payment edits, real API calls, commit or push in this slice.
M7c is split into M7c1 private family schedules and M7c2 optional self-booking/trials.
M7c1 is accepted for progression: owner-attested, explicit student scope; expiring single-use
invitations; read-only family schedules/credits; non-consuming staff preview; immediate
revocation. Reuses Django sessions, with no automatic email matching or enrollment-token
reuse. Additive migration 0010 only in the isolated preview. Verified notification delivery,
convenient recurring re-entry and family booking remain future work, not silently completed.
Final verification: 2,677 tests passed in 111.46 seconds; 100% new access Python line/branch
coverage; 99.96% combined scheduling/shared-payments runtime coverage (excluding migrations).
Desktop/mobile access, filtering, revocation and restart persistence passed. Browser CSRF
compatibility, past-attendance labeling and email-length validation were corrected before handoff.
See [M7c1 verification](docs/scheduling-m7c1-verification.md).
M7c2's local booking slice now implements published public trial offers, separately authorized
family single-lesson booking, bounded availability using the existing teacher/student/room
conflict planner, atomic retries, saved confirmation history and core.payments offline test
quotes/receipts/refunds. No enrollment account or subscription is required. Make-up eligibility
is explicit per offer, default-off and snapshotted; existing staff lesson policies are unchanged.
Additive migrations 0011/0012 are applied only to the isolated preview. Existing family grants
stay read-only. Public/family booking requires DEBUG and `BOOKINGS_LESSON_BOOKING_PREVIEW`; production
activation is deliberately unavailable. No shared payment/enrollment runtime was edited.
See [M7c2 verification](docs/scheduling-m7c2-verification.md). M7c2 remains in progress overall:
Local slice verified: 2,747 tests in 124.86 seconds, four new booking modules at 100% Python
statement/branch coverage, scheduling/shared-payments runtime 99.94% combined. Desktop/mobile
booking, staff cancellation/refund and restart persistence passed.
The next M7c2 slice now implements a default-off Bookings message outbox with local simulated
processing, bounded retries, ambiguous-outcome review, suppression and saved attempt history.
It reuses the existing mail factory without modifying enrollment notifications or using school
SMTP credentials. Migration 0013 affects only the isolated preview; old notices are not replayed.
Verified October 4: 2,793 tests in 133.74 seconds; five new message modules at 100% Python
statement/branch coverage (310 statements, 60 branches). Manual desktop/mobile tests covered
bounded retry, uncertain-outcome review, waiting-message links, suppression, duplicate prevention
and restart persistence. This completes the local queue slice, not real delivery.
See [message verification](docs/scheduling-m7c2-messages-verification.md). Real provider delivery,
verified guest re-entry and online lesson payment settlement are
unfinished engineering, not just missing credentials. Self-cancel/reschedule/credit redemption
remain staff-managed; recurring tuition is M8. Confirm Emily's
policies before rollout; term caps, absence exceptions and transferable entitlements are not assumed.
External collection remains gated behind real sandbox proof and paid-pilot operational checks;
recurring billing remains M8. The recovery command exists but no production scheduler is installed.
The October 1 direction is incremental delivery: a standalone workshop workflow first, early parties/camps,
optional Wix/Notion connections, then validated music-school requirements and enrollment links.
Existing schools, including SBMC, remain unchanged and disabled for this module by default.
Shared authentication and payment changes require explicit impact analysis and regression tests;
standalone access must not require an enrollment subscription or enrollment-admin permissions.

- [Design and boundaries](docs/scheduling-module-design.md)
- [Implementation tracker and acceptance gates](docs/scheduling-implementation-tracker.md)

Keep Bookings in this repository as a separate module with a standalone workspace and an
optional enrollment bridge. Share identity, scoped access, payments and notifications through
explicit interfaces; do not duplicate those systems or require enrollment records for bookings.
Extract shared code incrementally, not through a preparatory rewrite of the enrollment app.

The tracker is the source for Bookings milestones, current work, and user acceptance. No feature
is production-ready merely because its mock screen exists. Tuesday, October 6 is a narrow
prototype target, not a commitment to a production scheduler or working provider integrations.
M5a verification: 281 scheduling tests; 2,237 full regression tests passed; 99.88% combined
scheduling/shared-ledger Python line/branch coverage. Current M5b tests, shared-code impact and
desktop/mobile evidence are in [the M5b impact report](docs/scheduling-m5b-verification.md):
2,292 full-suite tests passed; scheduling/shared-payments combined coverage 99.90%; new checkout
services and views 100%. This does not substitute for future real sandbox verification.
M5c impact, recovery limits and paid-release gates are tracked in
[the M5c verification report](docs/scheduling-m5c-verification.md).
M5c final: 2,334 full-suite tests passed in 61.92 seconds; 99.92% combined scheduling/payments
coverage; recovery/refund services, recovery command and staff recovery views 100%. Desktop/mobile
and pending-refund restart persistence verified. This does not clear the paid-pilot gate.
The browser tracker has expandable manual steps and local-only credentials for each implemented
milestone, plus visible unanswered questions. Dedicated PostgreSQL test/preview databases and
network guards prevent paid calls. Shared payment records are additive under core/payments;
existing Stripe/application-fee entry-point signatures and School subscription fields remain.
M5b extends the existing Stripe facade, passes explicit keys for subscription calls instead of
mutating stripe.api_key, and uses a separate test-only merchant context for booking checkout.
No second Stripe integration was added. Legacy application-fee binding/fail-open risks remain
documented and are not silently changed for SBMC; hardening/migration needs a separate review.
No main-local/production migration, existing-school enablement, commit or push.

### Program Management — DB-Driven Programs (next to implement)

**Why we are building this:**
- Schools cannot self-manage programs today — every change (new program, rename, capacity edit)
  requires an operator to edit YAML and redeploy. This is a hard blocker for high-churn schools.
- Some schools add 10+ new programs every week. YAML cannot support that workflow.
- Small schools (karate, music) want students automatically enrolled or waitlisted at submission
  time — with no admin present. That requires reliable DB-level slot tracking per program.
- Program renames break string matching against JSON data — a FK solves this permanently.
- Slot count adjustments must be self-service for school admins, not require operator involvement.

**Key design decisions:**

1. **`SchoolProgram` model** (new) — authoritative source of truth for programs per school:
   - `school` ForeignKey(School)
   - `name` CharField (display label)
   - `code` CharField — matches the value stored in `Submission.data[program_field_key]`.
     **Editable while no submissions reference it; locked once any submission uses this code.**
     (Admins make typos during setup — don't make setup painful by locking immediately.)
   - `is_active` BooleanField (default True) — deactivate, never hard-delete if submissions exist
   - `display_order` PositiveIntegerField (default 0) — controls form dropdown order
   - `capacity` IntegerField (null=True, blank=True) — None = unlimited
   - `auto_enroll` BooleanField (default False) — master switch for auto-enrollment mode (see point 5)
   - `waitlist_enabled` BooleanField (default False) — only meaningful when `auto_enroll=True`
   - `form_keys` JSONField (default=[]) — empty = available on all forms; `["enrollment", "summer"]` = specific forms.
     **Expose only in Django admin for v1. Most schools will not need this.**
   - `created_at`, `updated_at` (auto timestamps)

2. **`School.program_field_key`** CharField (blank=True, default="") — names which YAML field key
   is the "program selector" for this school (e.g. `"dance_style"`, `"interested_in"`).
   When set, the form renderer replaces YAML options for that field with active `SchoolProgram` records.

3. **Form rendering rule — no heuristics, no silent fallback:**
   ```python
   if field.key == school.program_field_key:
       programs = SchoolProgram.objects.filter(school=school, is_active=True).order_by("display_order", "name")
       if programs.exists():
           field["options"] = [{"value": p.code, "label": p.name} for p in programs]
       else:
           # No active programs — explicit product behavior (not a silent degradation):
           # 1. School admin /programs/ page shows a warning banner.
           # 2. Public form renders the field with a single disabled option:
           #    "No programs currently available — contact the school"
           # 3. If the field is required, form submission is blocked (validation error).
           # YAML options for this field are NOT used as fallback. Ops must fix the data.
           field["options"] = []
           field["no_programs_warning"] = True
   else:
       # YAML options used as-is
   ```
   No silent YAML fallback once `program_field_key` is set. Ops must be notified and add active programs.

4. **`Submission.program`** ForeignKey(SchoolProgram, null=True, blank=True, on_delete=SET_NULL):
   - Set at submission time by matching `Submission.data[program_field_key]` → `SchoolProgram.code`
   - JSON field (`Submission.data`) remains unchanged as historical display snapshot
   - FK used for all capacity logic, reporting, and rename-safe queries
   - No FK = school hasn't migrated to DB programs yet (YAML path still works)

5. **Auto-enrollment at submission time** (concurrency-safe):

   Waitlisting is an extension of auto-enroll mode, not a standalone setting. Explicit behavior matrix:

   | `auto_enroll` | slots available | `waitlist_enabled` | result |
   |---|---|---|---|
   | True | Yes | any | `STATUS_ENROLLED` |
   | True | No | True | `STATUS_WAITLISTED` |
   | True | No | False | `STATUS_NEW` |
   | False | any | any | `STATUS_NEW` |

   `STATUS_WAITLISTED` must be a named constant (see below), not the string `"Waitlisted"`.

   ```python
   with transaction.atomic():
       program = SchoolProgram.objects.select_for_update().get(school=school, code=program_code)
       enrolled_count = Submission.objects.filter(
           school=school, program=program, status=STATUS_ENROLLED
       ).count()
       slots_available = program.capacity is None or enrolled_count < program.capacity
       if program.auto_enroll and slots_available:
           submission.status = STATUS_ENROLLED
       elif program.auto_enroll and not slots_available and program.waitlist_enabled:
           submission.status = STATUS_WAITLISTED
       else:
           submission.status = STATUS_NEW
   ```
   `select_for_update()` prevents two concurrent submissions both seeing "1 slot left" and both getting enrolled.

   **Status constant rule**: `STATUS_WAITLISTED` must come from a named constant in `core/models.py`
   (alongside existing `STATUS_ENROLLED`, `STATUS_NEW`, etc.), not from a hardcoded string anywhere
   in the codebase. If the school uses custom statuses, `STATUS_WAITLISTED` should be derivable from
   YAML config (e.g. `waitlist.status` key) — see Waitlist Management spec.

6. **Admin UI** (school admin at `/schools/<slug>/admin/programs/`):
   - Program list: name, code, capacity (or "Unlimited"), enrolled count, waitlist count, active badge, ↑↓ reorder
   - Add program form: name, code (auto-slugified, editable), capacity, auto_enroll, waitlist_enabled
   - Edit program: code field locked (read-only + tooltip) once any submission references it; all other fields editable;
     capacity decrease shows warning if current enrolled > new cap
   - Deactivate: if submissions exist, deactivate only (no DELETE); if no submissions, allow hard delete
   - Warning banner at top of list page when `program_field_key` is set but no active programs exist
   - `form_keys` not exposed in school admin UI for v1 (Django admin only)
   - URL names: `school_programs_list`, `school_program_create`, `school_program_edit`, `school_program_deactivate`

7. **Migration command** (for onboarding existing schools):
   ```
   python manage.py seed_school_programs_from_yaml --school <slug> [--backfill-submissions]
   ```
   Without `--backfill-submissions`: creates `SchoolProgram` records + sets `school.program_field_key`. Idempotent.

   With `--backfill-submissions`: also walks existing `Submission` rows and sets `submission.program` FK:
   - Match priority: exact `code` match → normalized name match
   - Only set FK when exactly one program matches (skip ambiguous)
   - Log a summary: matched N, skipped M (ambiguous), skipped K (no match)
   - Never overwrite an already-set FK

   Idempotent — safe to re-run. Always logs a summary of created / skipped / backfilled rows.

**Audit requirements — every program state change must be logged (no "I don't know when this changed" gaps):**

| Event | `extra["name"]` | Key fields logged |
|-------|----------------|-------------------|
| Program created | `program_created` | code, name, capacity, auto_enroll, waitlist_enabled |
| Program edited | `program_edited` | changed fields with old/new values |
| Program deactivated | `program_deactivated` | code, name, reason (has_submissions bool) |
| Capacity changed | `program_capacity_changed` | old_capacity, new_capacity, current_enrolled |
| auto_enroll toggled | `program_auto_enroll_changed` | old, new |
| Submission auto-enrolled | `auto_enrolled` | program_code, enrolled_count, capacity |
| Submission auto-waitlisted | `auto_waitlisted` | program_code, enrolled_count, capacity |

**DB changes**:
- New model `SchoolProgram` + migration
- `School.program_field_key` CharField + migration
- `Submission.program` ForeignKey + migration (nullable — existing rows have NULL FK until backfill command is run)

**Files touched**:
- `core/models.py` — `SchoolProgram`, `School.program_field_key`, `Submission.program` FK
- `core/migrations/` — 3 migrations (or 1 combined)
- `core/services/programs.py` (new) — `get_program_options`, `resolve_submission_program`,
  `apply_auto_enrollment`, `get_programs_summary`
- `core/views_school_submissions.py` — `apply_auto_enrollment` called on new submission save;
  form rendering uses `get_program_options` when `program_field_key` is set
- `core/views_school_programs.py` (new) — list, create, edit, deactivate views
- `core/urls.py` — 4 new program admin URLs
- `templates/school_admin/programs.html` (new) — program list + reorder + enrolled/waitlist counts
- `templates/school_admin/program_form.html` (new) — create/edit form
- `templates/school_admin/base.html` — "Programs" nav link (gated on `program_field_key` set)
- `core/admin/` — Django admin registration for `SchoolProgram`
- `core/management/commands/seed_school_programs_from_yaml.py` (new)
- `configs/settings.py` — no changes needed

**Out of scope (v1)**:
- `starts_on` / `ends_on` program date range (add later)
- Per-program description or rich text
- Public-facing "available programs" page
- Drag-and-drop reordering (up/down buttons for v1)
- Per-program fee amounts (that's Phase 18 scope)
- Automatic capacity decrease notifications to waitlisted families
- `form_keys` exposed in school admin UI (Django admin only for v1)

**Tests** (`core/tests/test_programs.py`, ~22 tests):
- `test_program_options_from_db_when_field_key_set`
- `test_program_options_from_yaml_when_no_field_key`
- `test_no_active_programs_renders_disabled_option_and_blocks_required_field`
- `test_inactive_program_hidden_from_form_but_existing_submission_still_displays`
- `test_auto_enroll_on_submission_when_slots_available`
- `test_auto_waitlist_on_submission_when_at_capacity_and_waitlist_enabled`
- `test_auto_enroll_status_new_when_auto_enroll_off` — auto_enroll=False must NOT waitlist
- `test_auto_waitlist_does_not_trigger_when_auto_enroll_false` — explicit: full program + auto_enroll=False → New
- `test_concurrent_submissions_only_one_auto_enrolled` (select_for_update race condition)
- `test_submission_program_fk_set_on_save`
- `test_admin_create_program`
- `test_admin_edit_program_logs_audit`
- `test_program_code_cannot_change_when_submissions_exist`
- `test_program_code_can_change_when_no_submissions`
- `test_admin_deactivate_program_with_submissions`
- `test_admin_deactivate_program_without_submissions_hard_deletes`
- `test_seed_command_creates_programs_from_yaml`
- `test_seed_command_idempotent`
- `test_backfill_matches_code_then_normalized_name`
- `test_backfill_skips_ambiguous_matches`
- `test_program_list_shows_enrolled_count`
- `test_capacity_change_audit_logged_with_old_new_values`

---

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

**Goal**: Schools can place applicants on a per-program waitlist with an ordered position.
Admins can reorder the queue and promote families with a single action.

**Key design decisions**:
- Position is scoped to `(school, program)` — Ballet #1 and Jazz #1 are independent queues.
  Rationale: capacity limits are already per-program, waitlist must match.
- YAML-gated (like capacity): presence of `waitlist.status` activates the feature. No feature flag.
- Position auto-assigned on status transition to the configured waitlist status.
  Reuses `get_program_field_key` / `get_program_value` from `core/services/capacity.py`.
- Promote action: changes status to `promote_to`, clears position, resequences remaining positions
  in that program to close the gap, optionally sends email notification.
- Reorder: up/down swaps positions between adjacent submissions in the same `(school, program)`.
  Two-row UPDATE in a single transaction.
- Status changed away from Waitlisted via dropdown (bypassing Promote) also clears position and logs it.
- Program changed via edit form while submission is Waitlisted: position cleared, new position
  auto-assigned in destination program if it is also the waitlist status.

**YAML config**:
```yaml
waitlist:
  status: "Waitlisted"      # which status value triggers auto-assignment (required)
  promote_to: "In Review"   # status set on promotion (required)
  # notification_template: "waitlist_promoted"  # optional, falls back to default copy
```

**DB changes**:
- `Submission.waitlist_position` IntegerField (null=True, blank=True, db_index=True)
  NULL = not on waitlist. Value = position within `(school, program)` queue.

**Audit log — every position change must be logged (no "I don't know how this happened" gaps)**:

| Event | `extra["name"]` | Key fields logged |
|-------|----------------|-------------------|
| Status → Waitlisted (auto-assign) | `waitlist_position_assigned` | program, position |
| Reorder ↑/↓ | `waitlist_reorder` | program, old_position, new_position (logged on BOTH swapped submissions) |
| Promote | `waitlist_promote` | program, old_position, new_status, email_sent, positions_resequenced |
| Status changed away from Waitlisted (not via Promote) | `waitlist_position_cleared` | program, old_position |
| Bulk status → Waitlisted | `waitlist_position_assigned` | program, position (one entry per submission) |
| Program changed via edit while Waitlisted | `waitlist_position_cleared` + `waitlist_position_assigned` | old_program, new_program, positions |

**Where admins see the waitlist**:
- **Submissions list**: Waitlisted rows show `#N` position badge inline with the status.
- **Dedicated page** `/schools/<slug>/admin/waitlist/`: primary management UI. Program tabs across
  the top (one per program with waitlisted submissions). Each tab: ordered table with position badge,
  student name, submitted date, parent contact, ↑ ↓ reorder buttons, Promote button per row.
- **Submission detail sidebar**: position badge + Promote button when submission is Waitlisted.
- **Nav**: "Waitlist" link appears in school admin sidebar only when ≥1 waitlisted submission exists.

**Files touched**:
- `core/models.py` + migration
- `core/services/waitlist.py` (new) — `get_waitlist_config`, `assign_waitlist_position`,
  `clear_waitlist_position`, `resequence_program_waitlist`, `promote_from_waitlist`
- `core/views_school_submissions.py` — status update views call `assign_waitlist_position` /
  `clear_waitlist_position`; new `school_waitlist_view` GET; new `school_waitlist_promote_view` POST;
  new `school_waitlist_reorder_view` POST; edit view handles program-change-while-waitlisted
- `core/urls.py` — `school_waitlist`, `school_waitlist_promote`, `school_waitlist_reorder`
- `templates/school_admin/waitlist.html` — program tabs, ordered table, ↑/↓, Promote
- `templates/school_admin/submissions.html` — `#N` badge on Waitlisted rows
- `templates/school_admin/submission_detail.html` — position badge + Promote in sidebar
- `templates/school_admin/base.html` — conditional Waitlist nav link
- `core/services/notifications.py` — `send_waitlist_promotion_notification`

**Out of scope**:
- Drag-and-drop reordering (up/down buttons for v1)
- Auto-promote when enrolled count drops below cap
- Public waitlist position display for families
- Per-form (multi-form school) waitlist — program-level is sufficient

**Tests** (`core/tests/test_waitlist.py`, ~14 tests):
- `test_position_assigned_on_status_change` — per program, sequential
- `test_position_not_assigned_when_waitlist_not_configured`
- `test_position_cleared_on_status_change_away` — dropdown bypass path
- `test_promote_changes_status_clears_position`
- `test_promote_resequences_remaining`
- `test_promote_sends_notification_when_email_enabled`
- `test_promote_audit_log_entry`
- `test_reorder_up_swaps_positions`
- `test_reorder_audit_logged_on_both_submissions`
- `test_bulk_status_assigns_positions`
- `test_program_change_while_waitlisted_clears_and_reassigns`
- `test_waitlist_view_grouped_by_program`
- `test_waitlist_nav_link_hidden_when_none`
- `test_position_scoped_per_program` — Ballet #1 and Jazz #1 are independent

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

## Plushumi Sales Demo (September 30, 2026)

- `/demo/plushumi/` serves a six-screen booking storyboard using the prospect's branding.
- Static demonstration only: no school record, payments, Wix/Notion calls, or migrations.
- Existing enrollment workflows and demo routes are unchanged. See `docs/plushumi-booking-demo.md`.
- Route, static-asset, and existing-demo regression coverage: `core/tests/test_plushumi_demo.py`.

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
- **Every push requires recorded verification of the exact revision being pushed:** meaningful
  unit tests, database-backed integration tests, the full regression suite, and agent-performed
  browser/manual smoke tests. Include the changed workflow plus SBMC/disabled-school checks.
  A user's acceptance or a coverage percentage does not replace these checks.
- Record test commands/results, coverage, manual steps/outcomes, shared-code impact and known
  gaps in the milestone verification record. Never reuse stale results after relevant edits.
  If a required check fails or cannot run, stop and report it before pushing; do not silently
  waive it or describe an untested provider integration as working.
- Ordinary tests must not make paid API calls, send real messages or create real bookings or
  payments. Use realistic provider fixtures; separately validate integrations in an authorized
  sandbox before claiming them ready. Production safety gates apply independently of git pushes.

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
