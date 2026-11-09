# Enrollment Demo Product Audit

Date: November 8, 2026. Scope: local Enrollment Request Demo, displayed as
Riverside Learning Academy. This records the original audit and the implementation
follow-up below, not a release claim. Existing SBMC and Plushie behavior must remain
unchanged.

## November 8 Implementation Follow-up

### Navigation Cleanup

Owner feedback supersedes the original navigation recommendation below: Reports
and Leads are top-level destinations, not children of Enrollment. Both template
families use the same opt-in header geometry and fixed label weights. The Programs
anchor menu and admin public-page shortcuts are removed. Program sections are
Enrollment programs, Trial lessons, One-time classes, and Camps & seasonal programs.
Schedule uses existing form/button components; Settings > Scheduling uses the
existing settings card/table treatment for Rooms and Staff access.

The local demo already has only Main Campus. The new validated `single_location`
policy hides filters when there is exactly one accessible location, without
widening permissions or silently accepting stale location IDs. Other schools default
to their existing selectors; a second location restores the selector.

The `school-theme-20261108` background run was intentionally interrupted before
this change (migration phase, exit 2). It was not a completed full-suite pass.
Current verification is recorded in `school-navigation-cleanup-20261108`.

Browser checks covered all seven top-level destinations at 1440x1000, 768x1024,
1024x768 and 390x844 in the in-app Chromium browser. Header link coordinates were
identical across the seven desktop pages. Phone Reports table overflow was found
and corrected with scoped scroll regions, then all changed pages were rechecked.
Teacher filtering, date selection, reset, mobile navigation and scheduling-settings
destinations were exercised. Tablet form/filter controls measured at least 44px high.
Physical Android, touch scrolling and virtual-keyboard behavior still need a real
device check. Full-suite status is tracked separately from these focused checks.

### Theme Correction

The initial simplified dashboard and list styling did not follow the application's
existing visual language. Functional tests and overflow checks did not establish
theme consistency. The correction restores the original dashboard view and its
metrics, work queue and activity components, with a scoped upcoming-schedule card.
Scheduling and program lists reuse the existing admin table, status, heading and
button styles. Teacher pages use the same admin navigation and table treatment.
The simplified journeys and feature policy remain intact.

The initial background regression was deliberately interrupted during its migration
phase before making these corrections (exit 2, unchanged source fingerprint), not
reported as a passing run. Corrected verification is tracked separately in the
`school-theme-20261108` evidence directory. Physical-device testing remains pending.

Theme-correction verification: 530 focused tests passed, followed by a final
287-test rerun after tablet action sizing. Shared dashboard component assertions
cover simplified, non-simplified and legacy layouts; theme/navigation tests cover
program tables and read-only actions. Sixty responsive page checks (15 screens at
1440x900, 768x1024, 1024x768 and 390x844) had no document overflow or error pages.
Manual checks exercised dashboard queue tabs, tablet menu navigation, Clean and
Midnight themes (restored to Clean), teacher-only sign-in/availability/form access,
and phone participant addition. Final tablet Details buttons and phone work-queue
actions measured at least 44 CSS pixels high. The full regression is pending in
the separate theme-correction evidence directory; nothing has been pushed.

Implemented locally behind validated school options; no hosted settings changed:

- Seven primary destinations and a shared school shell, with applications and agenda
  together on the dashboard. Reports and Leads are top-level destinations.
- Direct enrollment for new applications, simplified form/status configuration,
  deliberate resume behavior, and preserved historical staged admissions.
- Teacher account binding without owner privileges, own-teacher schedule and weekly
  availability, future-effective edits, booking-conflict protection and time off.
- Trial discovery starts on an available day and shows location time zones. Staff
  trial detail brings timing, teacher, changes, payment history and application links
  together. Application linking requires explicit identity verification.
- Classes/programs reuse existing request, capacity and reservation engines, with
  a common public entry and no mandatory materials catalog. Current fictional
  examples were added without deleting historical bookings.
- Removed normal-navigation promotions for parties, inventory/preparation, tours,
  playdates, make-up setup, recurring creation and local test-ledger controls.
  Related controls are opt-in; historical authorized records remain accessible.

These are not exclusively YAML changes. Two additive migrations and shared,
default-off code implement teacher access, dated availability and application links.
The absence of school-specific defaults changing is not a substitute for regression.

### Verification

The preceding time-dependent Studio Prep fixture was corrected with deterministic
local starts and explicit midnight-boundary coverage; all 30 targeted tests passed.
An intermediate combined regression passed 707 tests, including SBMC, authentication,
admissions, scheduling, appointments and the new journeys. Final review fixes and
responsive refinements require a fresh run; do not attribute that intermediate pass
to later edits.

The final focused run passed 887 tests in 120.50 seconds, including new journey,
teacher access, school isolation, SBMC workflow, authentication, shared request,
camp/payment, lesson, admissions and reservation workspace tests. The first final
attempt caught hidden preparation markup on simplified class reservations; it was
removed before this passing rerun. `git diff --check` also passed.

Browser verification used the Codex in-app browser at 1440x900, 390x844,
768x1024 and 1024x768 CSS pixels. Fifteen core screens at each size loaded without
document-level horizontal overflow (60 checks). Visual inspection covered the
dashboard, schedule, program list, teacher availability and public classes; tablet
navigation was opened and used. Teacher editing and phone participant controls were
also exercised during the walkthrough. This does not claim every legacy screen or
every control was manually tested.

Durable logs, JUnit results, responsive checks and screenshots are in the local
`school-journeys-20261108` evidence directory. The detached full-suite runner records
revision, working-tree fingerprint, phase logs and exit status there. Full-suite
verification remains pending until its final status is checked; no release or push
has been performed.

Browser walkthroughs submitted synthetic direct applications, a public trial,
an explicit application link, a class request converted to a paid reservation hold,
and a seasonal hold spanning all dates. Teacher-only login, rejected conflicting
future hours and a successful future edit were exercised. No real email/payment
was sent. A stale staged draft discovered during the first walkthrough prompted
an explicit fresh-entry fix; old signed resume links still preserve their case.

### Remaining Work, Not Implied Complete

- Live trial payment/email delivery and removal of the DEBUG-only preview gate are
  separate production-enablement decisions.
- Class requests use the existing staff-review/quote workflow. They are not instant
  paid confirmations; paid program places remain held until payment is recorded.
- Historical advanced screens remain available to authorized users. The new options
  are not a universal revocation layer for every legacy URL. A broader lifecycle
  switcher must preserve outstanding credits, family access and historical cases.
- Consolidating the two underlying team/permission editors, richer cross-journey
  email contexts, readable audit summaries, report semantics and optional-feature
  re-enablement UX remain follow-up audit items. No claim that every table row below
  has been fully resolved is intended.
- Physical Android Chrome, native virtual keyboard and device printing remain
  unverified; responsive browser emulation is not actual-device acceptance.

Use [the current setup guide](enrollment-lessons-setup.md#current-three-journey-demo)
for repeatable configuration. The original findings below remain traceable history.

## Decision and Verdict

The owner approved three core journeys:

1. Submit and manage an enrollment application.
2. Choose a teacher and book an available trial time; teachers manage their own availability.
3. Book a one-off class or a seasonal, multi-session program.

Tours, playdates, make-up credits and recurring lesson administration are optional
and should not be exposed in this demo. Keep their reusable implementation and
historical records.

**The current demo is not cohesive enough to present as these three journeys.**
The shared header is improved, but the application still exposes internal record
types and setup machinery instead of a small number of owner tasks. Several gaps
are functional, not cosmetic. Hiding links alone will not fix them.

## Priority Findings

| Priority | Finding and evidence | Required outcome |
| --- | --- | --- |
| P1 | The advertised public application link requires a completed tour. Its School tours link leads to no available tour times. | Enrollment works independently when staged admissions is not selected. Preserve old admissions cases and access history. |
| P1 | Existing availability edit fields are all disabled once a teacher/location has a lesson series. `scheduling/lessons.py` confirms this guard. | Staff can change future availability without moving existing bookings or allowing conflicts. Provide an effective date and explicit conflict handling rather than removing the guard. |
| P1 | There is no teacher-scoped manage-my-availability role. Scheduling roles are owner, scheduler and viewer; setup editing requires owner access. | Bind a teacher to an authorized account and scope their bookings/availability. Do not grant school-wide owner privileges to make the demo work. |
| P1 | Trial booking creates separate household/student records and has no enrollment lead/application relationship. | Provide authorized linking and an obvious trial-to-enrollment next step. Never merge people merely because their email matches. |
| P1 | School navigation explicitly rejects workshops/camps. Direct class/camp pages exist but cannot be included through this layout's feature list. | Extend the existing school navigation to support the agreed class/program journey, without exposing party or inventory tools. |
| P1 | Customer lesson booking requires `DEBUG` and `BOOKINGS_LESSON_BOOKING_PREVIEW`; notices are local previews and staff see a test payment ledger. | Keep the local demo honest. Production enablement and real delivery are separate work, not a configuration claim. Do not hide warnings while pretending payment/email occurred. |
| P1 | Latest full regression failed one Studio Prep test; coverage and JavaScript gates were not reached in that run. | Diagnose and fix the time-dependent test or product behavior, then rerun before implementation/release claims. Details below. |
| P2 | Public class schedule has no bookable future sessions and still promotes Plan a party. Camp dates are all in October, despite Registration open in admin. | Seed current class/program examples, remove party promotion when disabled, and make registration status reflect actual bookability. |
| P2 | Enrollment, trials and classes use different public headers, terminology and entry points. | School-branded entry with three clear choices; consistent dates, buttons, errors and confirmation behavior. |
| P2 | Scheduling has seven peer tabs, duplicated setup links and a recurring-series primary action. | One operational schedule, with teacher availability and activity setup in clear secondary locations. |

P1 means a blocker to demonstrating the agreed workflow or releasing the current
changes, not a claim of a production outage.

## Proposed Product Shape

Recommended owner navigation: **Dashboard / Enrollment / Schedule / Programs / Settings**.
Reports can remain accessible as a secondary destination. This is a proposal, not
a request to rewrite the underlying models.

| Area | What the owner should do here |
| --- | --- |
| Dashboard | See applications needing attention, today's appointments/classes and upcoming program registrations. Avoid a first screen dominated by empty historical metrics. |
| Enrollment | Manage applications and inquiries, with readable statuses and links to related trials. Keep family/contact context in the record. |
| Schedule | See upcoming appointments and group sessions in a list/calendar; filter by teacher, activity and location. Add a booking, open its details, reschedule or cancel. |
| Programs | Configure enrollment offerings, trials, one-off classes and seasonal programs. Distinguish their types clearly rather than making users understand the database model. |
| Settings | School details, team/access, email, forms, locations and policies. Keep infrequent rules out of the operational schedule. |

Teacher view: **My schedule / My availability**, with only necessary information
about their own bookings. School owners retain all-teacher access. Availability
should be a weekly editor with time off, not a long flat list of weekday records.

Public entry: **Enroll / Book a trial / Classes & programs**. Do not require an
application or manually provisioned family account merely to book a public trial.
Do not require tours unless that school has explicitly chosen staged admissions.

Do not force different booking rules into one model: a teacher trial has an
individual time slot; a group class has shared capacity; a seasonal program may
reserve capacity across multiple dates. Reuse each existing engine behind a
consistent interface.

## Screen-by-Screen Findings

The numbered evidence files `screen-0.txt` through `screen-53.txt` correspond to
the first 54 admin pages/form variants reviewed. Closely related variants are
grouped below; this is not a claim that every action was submitted.

| Screen / evidence IDs | What is wrong or inconsistent | Decision |
| --- | --- | --- |
| Dashboard (0) | Enrollment-only metrics; old playdate work item; no teacher/trial/class agenda. | Keep, redesign around current work and enabled journeys. |
| Submissions (1) | Technical naming, playdate statuses, Brightwheel export in generic demo. | Use Applications in UI; configure relevant statuses/export actions. |
| Leads (2) | Tour-oriented stages and no visible connection to trial activity. | Keep as Inquiries within Enrollment; connect related activity. |
| Reports (3) | Empty recent metrics alongside old funnel data; report heading outside main content landmark. | Keep secondary; scope metrics to enabled features and improve accessibility/data. |
| General settings (4) | Features included is a read-only entitlement summary, not feature controls. Public links omit trials/classes. | Do not mislabel it as a module switcher; expose all enabled public entry links together. |
| Scheduling / Lessons (5) | Seven tabs; primary Schedule lessons starts recurring administration, not trial booking. | Default to upcoming schedule; hide recurring-series workflow in this demo. |
| Bookings (6) | Separate from Lessons; list emphasizes booking-created date, not appointment date/time and teacher. | Integrate operational booking information; retain booking/payment history within detail. |
| Students & families (7) | Two unrelated lists; separate family/student setup before ordinary staff booking. | Keep records, expose contextually and in searchable people management only when needed. |
| Teachers & availability (8, 22) | Services first, then teachers, then every weekday row, then closures. Too much scrolling and duplicate setup destination. | Teacher list leading to a compact weekly availability editor; move service setup to Programs. |
| Make-up credits (9) | Empty page plus policies for a feature outside the agreed demo. | Disable as an optional subfeature, including dependent controls. |
| Tours (10) | Mixes setup, public links, diagnostics, empty schedules and old bookings. | Hide this module for the demo; retain a simpler operational visit list when enabled elsewhere. |
| Scheduling settings (11) | Grouping helped, but still a directory of implementation/rule screens; duplicates operational navigation. | Show concise relevant settings only, with advanced policies out of the main flow. |
| Schedule lessons (12) | Student/service/teacher/location/room and first/last dates plus recurrence; excessive for one trial. | Separate simple staff trial booking from optional recurring administration. |
| Lesson details (13-16) | Series and occurrence concepts; attendance shown as Unmarked; financial/confirmation data elsewhere. | One booking detail with appointment, contact, status and related history. Attendance controls need a dedicated interaction check before claiming they work. |
| Application detail (17) | All statuses displayed like a linear path, including Archived/Enrolled/Closed/Waitlisted; raw internal field names in audit. | Show current status and legitimate next actions; readable audit summaries; preserve detailed evidence internally. |
| New submission (18) | Observed navigation lands on Leads under current tour gating, not the form promised by the label. | Correct action/label and admission gating together. |
| New lead (19) | Basic form is reasonable; no need to expand it. | Keep simple and school-configurable. |
| Lead detail (20) | Tour-first progression; Enrolled and Lost appear sequential; no trial context. | Branching outcomes, linked trial and application, one clear next action. |
| Lesson settings (21) | A full page for one enable checkbox. | Consolidate into existing school setup/configuration. |
| Make-up policy (23) | Several rules for an optional workflow. | Hide when credits disabled; preserve existing policy/history. |
| Booking offers (24) | Public booking link buried here; trial, extra lessons, family booking and credits mixed together; local preview notice. | Present as trial/activity setup; hide irrelevant options; retain accurate delivery/payment limitations. |
| Visit readiness/setup (25) | Operational diagnostics and simulation warnings on regular admin paths. | Operator-only diagnostics, not a normal school menu item. |
| Admissions workflow (26) | Tour/application/playdate release and status-mapping machinery is unrelated to core demo. | Turn off requirements for new demo applications safely; existing cases cannot simply be discarded. |
| Locations and rooms (27) | Room and tax details prominent for a simple single-campus school. | Settings; auto-select unambiguous location/room, allow expansion for multi-location users. |
| Scheduling team (28) | Separate from school Team; booking-only roles; no own-teacher scope. | One coherent team/access experience over existing permissions, not automatic privilege widening. |
| Email template creation (29) | Enrollment tokens do not supply trial teacher/time context; no coherent cross-journey template entry. | Reuse template editor/token conventions with scoped event contexts. Keep templates school-specific. |
| Billing (30) | Pontora subscription billing is distinct from family fees. | Keep in Settings; do not mix it with school booking payments. |
| Enrollment program create/edit (31-32) | Capacity/auto-enroll/waitlist and sessions/cohorts are not the same objects as bookable classes/camps. | Keep distinct rules/models, but make their outward purpose explicit under Programs. |
| Trial booking detail (33) | Test payment ledger, manual payment/refund kind, local-test checkbox and saved-not-sent messages. | Remove test harness controls from ordinary school workflows; do not replace them with false success claims. |
| Family access (34) | Manual guardian/student access provisioning is an unnecessary public-trial prerequisite concept. | Hide advanced provisioning from core demo; preserve authorization for existing portals. |
| Family forms (35-36) | Separate small forms produce navigation overhead. | Reuse inline within relevant staff booking/contact workflows. |
| Student forms (37-38) | Household becomes read-only after lessons; reason not evident in form. | Preserve identity/history integrity; provide a deliberate correction path rather than silent disabled fields. |
| Service forms (39-40) | Useful duration/subject configuration but detached from trial setup. | Reuse under activity setup; no second service model. |
| Teacher forms (41-42) | Cross-visit identity field exposed; buffer/travel locks after lessons; no teacher login assignment. | Add scoped account assignment; keep advanced travel settings optional; preserve conflict identity. |
| Availability forms (43-44) | Existing teacher availability is entirely read-only while Save remains present. | Highest-priority functional repair: effective future changes and conflict protection. |
| Closure form (45) | Separate technical vocabulary and broad scope. | Time off / school closure within availability; retain protection against silently invalidating bookings. |
| Visit program forms (46, 48) | Unnecessary tour/playdate setup for this demo. | Hide with optional visits. |
| Local message inbox (47) | Simulation/recovery machinery exposed to school users. | Keep operator diagnostics outside normal school navigation and permissions. |
| Visit time/repeat forms (49-50) | Separate setup flows for an excluded module. | Hide with visits; keep reusable implementation. |
| Visit detail (51) | Historical attended visit and access management. | Preserve history, remove from core demo path. |
| Reschedule (52) | Single appointment and series/future scope mixed together. | Default to the selected trial only; expose recurrence only when enabled and applicable. |
| Cancel (53) | Series scope/requester/credit concepts bundled. | Simple single-booking cancel; preserve policy, audit and financial safeguards. |
| Workshop list/detail | No school-nav destination, zero upcoming sessions, price not set while Full payment shown; dates in October. | Add enabled class navigation and valid current offerings. Show truthful readiness and price state. |
| Camp list/detail | Registration open but no future dates; publication warning and no registrations. | Bookability-aware status and current seasonal example; retain all-date capacity/deposit rules. |
| Public class schedule | No future session actions; Plan a party link; project/color copy. | Enable an ordinary class/program route with no party or material-choice dependency. |
| Public application entry | Tour gate leads to no available tours. | Fix first; direct enrollment is one of the three promised journeys. |
| Public inquiry form | Simple form, but disconnected from other public entry points. | Keep; apply common school branding and return navigation. |
| Resumed application | Duplicate contact fields, desktop modifier-key multi-select, ID upload, separate Request trial/evaluation choice with no booking step. | Trim configurable fields, use touch-friendly choices, link trial intent to actual scheduling. Do not make ID upload a generic demo default. |
| Public trial catalogue | Reasonably readable on phone; inconsistent offer titles and public branding. | Keep basic structure; standardize titles and school branding. |
| Public teacher/time selection | Opens on a day with no slots even when the next day has availability; requires guessing dates and Show times; no visible time-zone context. | Start at next available date, expose available dates and show studio-local time zone. |

Settings tabs were also read: General, Email, Team, Billing, Forms and Scheduling.
The key cross-tab defects are duplicated access/setup concepts, unrepresented
booking links, and feature/status/form choices not aligned with the demo scope.

## Features to Remove From This Demo, Not the Codebase

| Optional feature | What must disappear or change together |
| --- | --- |
| Tours and playdates | Navigation, setup links, public promotions, tour/playdate-only statuses, release requirements and new-entry gating. Preserve historical cases and authorized links. |
| Staged admissions | New enrollment must not require a hidden tour. Current policy cannot be disabled through its form once cases exist; safe transition needs investigation/implementation. |
| Make-up credits | Credit page, policies, offer eligibility, and credit-specific booking/cancellation controls. |
| Recurring lesson administration | Series creation and bulk/future-series controls. Keep weekly teacher availability and internal occurrence/conflict logic needed for trials. |
| Advanced family portal provisioning | Manual access grants and family-only extra-lesson offers. Do not remove secure access to existing records. |
| Parties | Party inquiries, packages and Plan a party public promotion. |
| Inventory and Studio Prep | Materials, preparation, plushie/design/color selection. Ordinary class rosters remain useful and should stay. |
| School-specific external integrations | Brightwheel export and unrelated vendor setup. Re-enable through supported school configuration when required. |
| Operator QA tools | Local message simulator, test payment ledger and diagnostic readiness controls from normal staff navigation. Preserve environment safety controls and truthful error reporting. |
| Irrelevant form fields | ID upload, duplicate contacts, tour-specific fields and keyboard-only multi-selects. Configure fields; do not delete reusable support. |

**Do not remove** teacher availability, time off, conflict checks, contact/student
context, booking history, cancellation, audit evidence, or applicable payment
safeguards. Those support the core journeys even when their current screens are poor.

## Configuration Reality

| Area | Existing mechanism | Gap / constraint |
| --- | --- | --- |
| Broad scheduling modules | `SchedulingProfile` capabilities: lessons, visits, workshops, camps, parties, reservations, integrations. | Dependencies exist; camps depend on workshops/reservations. Enabling an engine must not force every related UI feature on. |
| School navigation | `scheduling.navigation` in school YAML; validated in `scheduling/school_navigation.py`. | Currently school layout supports only lessons/visits; presentation filtering is not authorization or booking enablement. |
| Optional lesson features | Recurrence, credits, family offers implemented within lessons. | No complete independent demo feature switches; hiding one tab is insufficient. |
| Historical capability removal | `scheduling/capabilities.py` protects existing records. | Must distinguish stopping new activity, hiding a menu, permissions, and deleting/removing a capability. Do not bypass history guards. |
| Staged admissions | Database policy, controlled by `scheduling/admission_forms.py`. | Disabling with existing cases is blocked. Define future-entry behavior while preserving existing cases, rather than deleting demo history. |
| Enrollment labels/fields/exports | Existing school YAML conventions. | Mostly configuration work, with validation for persisted statuses and links. |
| Teacher self-service | Existing scheduling access and teacher records. | Requires account binding, object-scoped permissions and tests; not just a menu toggle. |
| Trial-to-enrollment connection | Separate lesson booking/student/household and enrollment records. | Requires authorized explicit linking. Existing refusal to auto-merge by email is correct. |
| Trial production readiness | Local-preview gate and notice model. | Needs deliberate production delivery/payment decisions; local success does not establish live readiness. |

Extend these existing mechanisms with the smallest validated set of options.
Do not add a generic feature-flag framework, duplicate booking engines, or branch
on this school's slug. Operator-managed module policy belongs in established
configuration; owner-editable availability, offerings and settings belong in the
database. Each setting must have one authoritative source.

## Implementation Order and Acceptance

1. Resolve the failed regression before new application implementation. Preserve
   current working-tree changes and record a fresh source fingerprint.
2. Establish the approved demo feature profile. Fix direct enrollment and its
   statuses/fields; preserve historical admissions data. Test disabled features
   through direct URLs, public links, permissions and dependent rules, not just tabs.
3. Simplify Schedule and Programs using existing engines. Include classes and
   seasonal programs in school navigation without inventory/party assumptions.
4. Implement teacher-scoped availability and future-effective edits. Verify owner,
   teacher, scheduler, viewer, location-limited and cross-school access; unavailable
   time, closures, buffers and overlapping bookings must remain protected.
5. Connect trial and enrollment records with explicit identity verification and
   readable next actions. Keep the public trial form short; display appointment
   date/time/teacher consistently in staff lists and confirmations.
6. Refresh synthetic data for all three journeys, school branding and public links.
   Remove normal-staff QA controls while keeping honest sandbox/delivery boundaries.
7. Run complete journey regressions, then desktop/phone/tablet interaction checks.
   SBMC and Plushie regression coverage is mandatory before any shared-code release.

Minimum demonstration scenarios:

- Family applies directly; staff sees the application and can follow up without a tour.
- Teacher changes future availability; a family sees valid slots, books one, and
  the school sees it under that teacher. Existing bookings are not moved or lost.
- Staff connects an authorized trial participant to enrollment without duplicate
  applications or exposing another family's data.
- Family books a one-off group class; capacity and roster update correctly.
- Family books a seasonal program spanning multiple dates; capacity and payment
  policy are consistent across dates, without design/material selection.
- Disabled modules remain absent from menus, setup, templates and public calls to
  action; existing records remain available only to authorized users.

Industry-specific labels and sample offerings should be configuration/data, not
different implementations: music trial + family music class + summer program;
karate introduction + one-off seminar + holiday camp; preschool enrollment +
optional introductory appointment + parent/child class + seasonal program. A
preschool tour can be enabled separately, not forced into every school's trial flow.

## Verification and Limits

This pass inspected 54 discovered admin pages/form variants, six settings tabs,
class/camp administration, and public enrollment, inquiry, trial and schedule
entry flows. Source inspection covered navigation, capability dependencies,
permissions, availability guards, admissions policy and trial booking models.
No applications/bookings/payments/emails were submitted and no school configuration
was changed during this audit.

Current-pass responsive spot checks: in-app Chromium desktop; 768x1024 teacher
setup with navigation opened; 1024x768 schedule; 390x844 public trial catalogue.
Landscape schedule showed no document overflow and measured main actions at least
44px high. Portrait scheduling tabs extend offscreen horizontally and teacher
setup remains long and inefficient. These checks are not all-page touch testing.
Physical Android Chrome, actual virtual keyboard and device printing are unverified.

Evidence directory (local, not committed):
`/Users/njulka/.codex/visualizations/2026/08/07/019fd9a2-8b5e-7f93-84ef-ac4e38c58723/school-product-audit-20261108/`.
Contains numbered DOM snapshots, page inventories and representative screenshots.
Do not publish private family/resume links from audit evidence.

Latest preceding regression: `school-navigation-20261107/status.json`, completed
November 8 at 07:16 UTC, exit 1. Migration drift check and 23 migration tests passed;
application result was **1 failed, 5,455 passed, 3 skipped, 23 deselected**.
Coverage and JavaScript gates were not subsequently reached. The failing test is
`test_overlapping_half_hour_starts_stay_in_separate_ordered_groups` in
`scheduling/tests/test_studio_preparation.py`. Its now-relative fixture can cross a
day boundary while assertions assume one day; this is a suspected cause, not a
completed repair. Use deterministic local times and explicit midnight-boundary
coverage after confirming the behavior.

Run revision: `51cf29bd0a50da8f8796ddee23a16939ffb67a7a` plus working-tree
fingerprint `750c004c6c917951e732da73363947db4cbae99604fa91a66ffcd055721e89cf`;
runner reports source unchanged. This audit changes documentation only and does
not turn that failed run into a pass. No push or release is authorized by this audit.
