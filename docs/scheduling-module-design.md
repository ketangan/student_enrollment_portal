# Pontora Bookings: Product and Architecture Proposal

Status: architecture proposal; M1a access and M1b setup/publishing implemented locally after explicit authorization.
See the implementation tracker for delivered scope. This document does not authorize deployment
or imply that the remaining scheduler/payment/integration capabilities are implemented.
Research and repository review: September 30, 2026. Payment extension and visual walkthrough review: October 1, 2026.

## 1. Recommendation and Boundaries

Build an optional **Pontora Bookings** product in the existing repository, with its own Django app and data model. Offer it independently or alongside enrollment. Use one implementation, not a Plushumi fork and an SBMC fork.

Do **not** make "all Wix Bookings features plus all MakeMusic Studio features" the first release requirement. That includes several products' worth of scheduling, payments, memberships, communications, reporting, and operational support. A broad checklist is not a commercially sensible delivery scope.

The immediate opportunity is narrower and more defensible: structured, visual booking information that reaches the customer's operational system without retyping. Prove that with Plushumi before funding a complete scheduler. SBMC is a separate validation partner, not permission to replace its working system.

### Independent configuration choices

| Dimension | Choice | Meaning |
| --- | --- | --- |
| Scheduling authority | Connected provider, initially Wix | Pontora improves selection/intake and automates downstream information; Wix remains authoritative for bookings, capacity, and payments. |
| Scheduling authority | Pontora native | Pontora controls availability, reservations, policies, and booking lifecycle. |
| Commercial package | Bookings only | Independent booking/admin experience; no enrollment application or enrollment subscription required. |
| Commercial package | Enrollment + Bookings | The same engine, with explicit links to leads, applications, students, and the parent portal. |
| Staff workspace | Pontora, external operations, or both | A bookings-only portal can manage daily work; an external-operations customer can stay in Notion/Wix with minimal Pontora connection/recovery settings. |
| Optional connectors | None, Notion, supported calendar/export destinations | Enable each independently; Notion is not a prerequisite for Wix or native scheduling. |

These are separate dimensions. Selling a standalone product does not require immediately replacing Wix, or deploying a second codebase.

**Non-negotiable:** a shared capacity/resource pool must have one scheduling authority. Different services cannot use different engines while silently sharing the same room or teacher. Separate pools or complete cutover are required.

### Alternatives considered

- **Configure Wix plus a small integration only:** lowest-risk way to solve Plushumi's immediate problem. Choose this if its native forms can deliver the required visual/per-participant experience. Still reuse our mapping and delivery components.
- **Custom front end over Wix:** recommended feasibility target when native forms cannot meet that experience. No second customer form after booking.
- **Native Pontora scheduler:** valuable reusable product, but a larger investment. Proceed against signed pilot scope and verified repeat demand.
- **Another embedded scheduling vendor:** valid fallback if Wix's APIs fail the pilot. Compare resource rules, APIs, branding, data portability, and commercial terms before selection; an additional vendor is not automatically simpler.
- **Separate microservices or a customer-specific app:** reject initially. Neither solves the hard booking rules; both increase operations and integration overhead.

## 2. Evidence and Unverified Assumptions

### Confirmed

- Plushumi's team asked for visual design/color selection and elimination of manual Wix-to-Notion copying, while retaining Wix if possible. They did not ask to migrate billing or operate a new calendar.
- Their public site lists multiple locations and both public workshops and private events. Its published policies distinguish full workshop payment from event deposits, require participant-specific seats, and address cancellation windows and guardian waivers. These are configuration inputs to confirm, not rules to hardcode. [P1][P2]
- The user confirmed SBMC uses `studio.makemusic.app` and supplied its public piano-trial booking screen. Observed screen details are recorded below; its administrative configuration, billing arrangements, and operational exceptions have not been inspected.
- Wix documents custom booking flows for appointments, classes, and courses. A simple appointments tutorial does not prove the group-workshop flow works unchanged. Current Wix Forms integration must be used, not deprecated `additionalFields` examples. [W1][W2][W3]
- This repository has tenant accounts, enrollment workflows, program capacity, billing, and scheduling preferences. It does not contain a general-purpose booking engine.

### Not established

- Whether Plushumi's locations share one Wix site/account, one Notion workspace, and one legal merchant.
- Their actual Wix service types, booking forms, API permissions, plan restrictions, coupons/gift cards, and checkout customizations.
- Whether a design/color is merely a preparation preference or a stock-constrained item that must be reserved.
- Whether each participant can choose a different design/color within one group purchase through the approved Wix flow.
- Whether SBMC would switch systems, and which MakeMusic capabilities it relies on. A screenshot can establish UI context, not these business rules.
- Whether MakeMusic offers an appropriate supported API/export for SBMC. Do not promise live synchronization based on a calendar feed.
- Camps were requested as an early priority by the user, but a current Plushumi camp offering was not verified on the public pages inspected October 1. Confirm its session, registration, and pricing rules before claiming customer-specific coverage.

No production access, private customer data, or paid provider operations were used for this design review.

### Visual walkthrough added October 1

The accompanying 19-screen concept is a design review, not a deployed scheduler. It uses local fictional booking data and makes no provider calls. Except for the observed $30 SBMC trial, amounts and policies are illustrative and are not approved customer pricing.

| Screens | What can be reviewed |
| --- | --- |
| 1 | Clickable customer journeys and parallel operations flowchart. |
| 2-4 | Per-customer enablement, standalone versus integrated access, reusable offering/pricing configuration, teacher/room availability and policies. |
| 5-8 | Workshop dates, party size, visual per-participant selections, one-time checkout, confirmation and a session change. |
| 9-11 | One-time piano trial, separate recurring tuition consent, family portal and an explicit end-of-tuition/lesson impact preview. |
| 12-14 | Staff agenda, attendance, partial refund lifecycle, recurring series conflict preview, make-up credit and optional enrollment bridge. |
| 15-17 | Customer payment ledger, invoice action required, preparation totals, Notion retry and field ownership, location-limited roles. |
| 18-19 | Shared payment architecture, code-review findings, waitlist, late payment reconciliation and launch gates. |

The walkthrough deliberately does not depict full native party-deposit collection, course/term administration, stock reservation, accounting/payroll, dispute tooling or live two-way calendar synchronization. Those remain the phase dependencies described below; the mock screens do not prove them implemented or the Wix flow feasible. A calendar date picker is not evidence of concurrency protection, and a simulated paid screen is not evidence of payment correctness.

## 3. Competitor Findings and Product Coverage

Competitor documentation informs the model; it is not a requirement to reproduce every competitor feature or limitation.

| Reference | Relevant established behavior | Design implication |
| --- | --- | --- |
| Wix Bookings | Appointments, classes, courses; custom forms; booking/payment lifecycle; policies and waitlists. [W1][W2][W4] | Separate service types, intake, reservations, payment, and policy. Validate real class booking APIs first. |
| MakeMusic Studio | Recurring appointments, staff and room scheduling, family profiles, attendance, per-appointment billing, flat subscriptions, packages. [M1][M2][M3] | Recurring lessons and household billing cannot be modeled as single workshop purchases. |
| My Music Staff | Private/group lessons, student portal booking, attendance, make-up credits, reminders, location organization. [MS1] | Credits and self-service rescheduling are first-class music-school requirements. |
| Sawyer | Scheduled activities/semesters and multiple registration/pricing choices. [S1] | A term registration spanning several sessions is different from booking one class. |
| Acuity | Resources supplement availability; group-class resource behavior has limitations. [A1] | Do not assume attaching a room to an event prevents overlaps. Enforce our own stated invariants in native mode. |

MakeMusic's help documentation explicitly says it lacks a built-in make-up credit tracker. My Music Staff supplies a useful reference for that separate capability. Do not claim they behave identically. [M4][MS1]

Wix's resource documentation and class-creation guidance are not fully consistent about class resource conflict prevention. Treat this as a sandbox acceptance test, not a dependable guarantee copied into a contract. [W5][W6]

### Target capability map

Phases: **C** connected Plushumi pilot; **N** native booking foundation; **E** early parties/camps extension; **L** lesson-school release; **X** later expansion. C delegates scheduling/payment functions to Wix rather than claiming native implementation. The implementation tracker breaks these release capabilities into small user-tested milestones.

| Capability | Plushumi application | Music-school application | Phase |
| --- | --- | --- | --- |
| Branded responsive booking | Workshop selection | Trial/lesson selection | C/N |
| Visual, conditional choices per attendee | Design, color, preview | Instrument, level, duration | C/N |
| Multiple participants, one purchaser | Friends/family group | Guardian with children | C/N; saved household portal L |
| Locations and local time zones | Multiple cities | Studios/online lessons | C/N |
| One-off and repeated public sessions | Workshops | Group classes | Provider in C; N |
| Availability, buffers, closures, lead times | Staffing/preparation | Teacher hours/travel | Provider in C; N |
| Seat, staff, room conflict protection | Stations and workshop space | Teacher/piano/room | Provider in C; N for exclusive resources |
| Self-service cancel/reschedule with cutoffs | Changes before preparation | Lesson changes | Provider in C; N single occurrence; L series |
| Full payment, refunds, offline reconciliation | Public workshop checkout | Trial payments | Provider in C; N |
| Deposits, remaining balance, private buyout | Parties/private studio | Special events | Provider in C; E native, before any native paid party launch |
| Notion delivery and preparation totals | Eliminate retyping | Optional roster export | C; reusable N |
| Waitlist and expiring seat offers | Fill cancellations | Group/make-up slots | Provider if supported C; L native |
| Attendance and roster | Check-in | Present/absent/no-show | N basic; L richer policies |
| Standing weekly lessons, exceptions | Usually unnecessary | Core requirement | L |
| Per-lesson, package, flat tuition billing | Optional packages | Potential migration blocker | L, according to verified SBMC usage |
| Make-up and package credits | Optional repeat workshops | Missed lessons | L |
| Courses/camps | Multi-session activities; exact Plushumi offering unverified | Term programs | E fixed cohort; flexible drop-ins/partial enrollment later if needed |
| Calendar export/import | Optional | Teacher calendar conflicts | N export; L verified busy-time integration |
| SMS, coupons, gift cards, memberships | Preserve existing Wix benefits | Optional school policies | Provider in C; X native unless a launch dependency |
| Staff payroll, accounting, music library | Not requested | Competitor extras | Not in initial product |

Do not migrate a customer until the subset they actually use passes acceptance tests, even if a feature is placed in a later phase here.

## 4. Customer Workflows

### Plushumi: connected mode first

1. Customer chooses a location and workshop time using current Wix availability.
2. Customer selects attendee count and chooses a design/color for each attendee, with an accurate supplied preview asset. Invalid combinations are unavailable, not accepted and corrected later.
3. Purchaser/contact details, required participant information, policy acceptance, and applicable waiver acknowledgment are collected in the same journey. Do not send an additional form afterward.
4. Revalidate availability with Wix, create the correctly typed booking, and use Wix checkout. Confirmation depends on provider-confirmed booking/payment state, not merely the browser reaching a success page.
5. Store a normalized local projection and the visual selections keyed to the provider booking/attendee identities. If Wix cannot store their full shape, Pontora can own that extension data, but prove the correlation survives checkout, retries, and edits.
6. Send structured information to the existing Notion destination. Staff see date/time/location, participants and selections, payment summary, and preparation totals without retyping.
7. Cancellations, rescheduling, and selection corrections update the same records. Bookings made through an old Wix entry point must be detected and labeled if their custom selections are missing.

Wix remains the authority for booking status, capacity, refunds, and its notifications. Pontora does not independently sell the same seats or send duplicate confirmations.

**Mandatory prototype:** a real test class with two attendees choosing different options, completed checkout, cancellation, and a correct Notion update. API documentation establishes plausibility, not completion of this prototype. Keep launch blocked if the flow duplicates participant forms or cannot reliably preserve selections.

Include price-affecting options, attendee quantity, and any in-scope discounts in that prototype: the displayed quote and Wix's actual checkout total must agree. An attractive preview attached to an incorrect charge is a failed integration.

Inventory all entry points for the pilot service: public website, direct links, mobile, and staff-created bookings. Redirect or configure them to collect the agreed selections in their original booking journey. Monitoring for missing selections is a safety net, not an acceptable steady-state replacement for this work.

The first pilot can exclude private parties, gift-card flows, or selected locations only by explicit agreement. Existing Wix entry points for excluded services remain intact.

### Plushumi: native mode, only if wanted

Same customer journey, but Pontora owns sessions, capacity, checkout, and operational rules. Multi-location architecture is present from the start; rollout can still begin with one location/service.

An exclusive party blocks its room and staff against public sessions. Preparation data is attendee-specific. Consumable stock is a separate optional feature, not inferred from a color dropdown. Deposits and balances must be supported before moving parties out of Wix.

### SBMC: integrated enrollment and scheduling

#### Observed customer screen

User-supplied screenshot reviewed September 30, 2026:

| Visible element | Requirement it supports |
| --- | --- |
| SBMC logo and piano-trial header artwork | Tenant branding and optional service-specific artwork. |
| Piano Trial Lesson, 30 minutes, $30.00 | Service-specific fixed duration, displayed price, and description. These values belong in configuration. |
| One visible instructor card with photo and short biography | Staff profiles associated with an offering. The screenshot does not prove how multiple instructors are selected. |
| Monthly calendar and appointment-time dropdown | Date selection followed by time selection for an appointment offering. Available-slot rules require separate verification. |
| Continue button disabled in the shown state | An incomplete-selection state; actual validation and the next screen were not exercised. |
| Link to other offerings | Direct service links plus a navigable service catalog. |
| Pacific-time note | Explicit booking timezone in the existing UI, not evidence that every Pontora screen needs the same label. |

This is evidence of a public trial appointment journey, not recurring tuition, package balances, staff calendars, payment timing, or room-conflict handling. It fits the native appointment model without requiring customer-specific scheduling code. Preserve fixed-duration services: only show a duration selector when the service actually offers a choice.

#### Proposed integrated journey

1. Existing trial/enrollment workflows continue unchanged until SBMC explicitly opts into a pilot.
2. An opted-in trial service allows a family to select an offering and time, with teacher/duration choices only when configured. Teacher, room, location, closures, and buffers all participate in availability.
3. An administrator can assign a recurring lesson series to the existing student, review conflicts, and notify the correct guardian. A lesson appointment does not automatically change the student's enrollment status.
4. Families see their children's upcoming lessons and policy-permitted changes in one portal. Teachers see only their assigned schedule and necessary roster information.
5. Teacher cancellation, family cancellation, no-show, substitute teacher, and school closure are different events with explicit charge/refund/credit outcomes.
6. Packages, monthly tuition, and pay-per-lesson are distinct billing agreements. A five-week month does not automatically change flat tuition; a reschedule does not automatically create a new invoice.

An existing enrollment record may link to many appointments. Standalone customers need no enrollment record at all. Moving SBMC is a cutover project, not a feature-flag flip with imported screenshots.

For an explicitly enabled trial-booking bridge, collect contact/student details once and link the reservation to the lead. Only confirmed reservation state may advance that lead to Trial Scheduled; entering preferences or opening checkout must not do so. Completion requires the agreed attendance/admin event, not merely the appointment time passing. This is proposed opt-in behavior, not a change to SBMC's current redirects or statuses.

## 5. Repository Fit and Change Impact

The following references were inspected in this checkout. They establish reuse boundaries, not a production audit of current customer data.

| Existing surface | Reuse or preserve | What is missing / risk |
| --- | --- | --- |
| `core/models.py:116` School | Existing tenant identity, branding, account relationships | Internal name can remain School even for workshops. Do not create a parallel tenant system. |
| `core/models.py:282` SchoolAdminMembership | Existing administrator identity/access | Owner/editor/viewer does not provide isolated instructor/location access. |
| `core/models.py:317` SchoolProgram | Optional link from a bookable service to an enrollment program | Program capacity is enrollment capacity, not time-slot inventory. |
| `core/models.py:353` SchoolSession | Keep current term/session semantics | Date range, not a timed occurrence with staff/resources. Do not repurpose it. |
| `core/models.py:431` Submission | Optional source link and student/application context | Not a booking/order/customer record. Do not add every booking field to its JSON. |
| `core/services/programs.py:281` | Existing transactional enrollment-capacity pattern | Keep its locking/status behavior unchanged; use separate scheduling transactions. |
| `core/services/feature_flags.py:70` | Existing per-school operator-only flag pattern | Trial plan enables all ordinary plan features. Scheduling must not be added to that list. |
| `core/services/billing_stripe.py:158` and `:437` | Established platform billing and separate school application-fee behavior | Neither is a booking ledger. Application fee helpers lack the reservation lifecycle this needs. |
| `core/services/integrations.py:17` | Explicit field-mapping conventions | CSV export mapping only, not a durable Notion connector or sync queue. |
| `core/services/playdates.py:75` | Preserve opt-in demo workflow | Saving a date/preference is not a resource-aware reservation. |
| `core/services/payment_preview.py:5` | Preserve disabled demo fee display | Not working checkout. |
| `configs/schools/south-bay-music.yaml:279` and `:488` | Preserve preferences and instrument-specific external booking redirects | Do not reinterpret requests as confirmed appointments or switch redirect targets automatically. |
| `core/services/url_builder.py:54` | Existing request-aware URL convention | All new booking/email/webhook returns must preserve the correct environment. |
| `requirements.txt`, `render.yaml` | Existing Django/PostgreSQL deployment | No Celery dependency or worker appears in these checked-in files. Live Render setup was not inspected. |
| `PROJECT.md` architecture decisions | Optional Bookings direction recorded October 1 | Planned, not implemented; existing schools remain disabled by default. |

### Proposed layout

```text
scheduling/
  models/                 tenant-scoped booking domain
  services/               availability, reservations, changes, policies
  pricing/                service quotes and lesson entitlements, not Stripe transport
  adapters/               Wix, Notion, calendar; no second Stripe implementation
  enrollment_bridge.py    explicit links and opted-in actions only
  access.py               entitlement + role + location checks
  tasks.py                durable delivery/reconciliation jobs
  urls.py                 own URL namespace
  views/                  booking/admin/provider callbacks
  tests/                  domain, provider contracts, concurrency, UI
templates/scheduling/
static/scheduling/
core/payments/            proposed shared extension of existing billing service
  service.py              one-time orders, recurring agreements, refunds
  stripe_gateway.py       single account-scoped Stripe implementation
  events.py               verified, persisted, deduplicated event routing
  handlers/               platform, application, booking domain consequences
core/services/billing_stripe.py   compatibility facade for existing callers
```

This is a modular monolith: one repository and database, with narrow interfaces. Initially a single app can hold these responsibilities. Split further only when ownership or actual complexity warrants it.

Repository decision reaffirmed October 1: independent commercial packaging does not require a
second repo or a second booking implementation. Standalone and enrollment-integrated navigation
use the same scheduling services. Reuse identity, scoped tenant access, payment and notification
services through explicit interfaces; do not copy code between products. Extract shared code only
when a delivered feature needs it, with compatibility tests, rather than rewriting enrollment first.
Consider repository extraction later for proven independent release/team/infrastructure needs,
after interfaces and data ownership stabilize. A shared release still has regression risk; module
boundaries and feature flags are not a substitute for testing or migration review.

No global signal on every Submission save. The enrollment bridge calls scheduling explicitly for opted-in workflows. The booking core must also run without importing an enrollment view or requiring a Submission.

### Service contracts

Keep a small typed command interface behind public/admin views:

| Command/query | Contract |
| --- | --- |
| `get_availability` | Tenant, offering, bounded date window, party size, optional staff choice; returns versioned candidates, not guaranteed reservations. |
| `quote_booking` | Validated attendee/options payload; returns authoritative price/policy snapshot and expiry. |
| `reserve_booking` | Quote/candidate, actor, idempotency key; creates a native hold or tracked provider operation. |
| `confirm_payment` | Verified provider event/account; never callable as an unverified browser assertion. |
| `reschedule_booking` / `cancel_booking` | Tenant, booking ID, expected version, actor, policy outcome, reason; preserve history. |
| `record_attendance` | Occurrence/attendee, permitted staff actor, expected version; financial consequences applied once by policy. |
| `publish_booking_change` | Versioned durable event consumed by notifications, Notion, and the opt-in enrollment bridge. |

Use explicit conflict, payment-pending, provider-unavailable, and policy-blocked results so views can explain the next action. The native/provider adapter selection is server-owned, not a customer-submitted switch. New routes live in a scheduling namespace; old enrollment routes retain their behavior.

### Unavoidable shared edits, later

App/URL registration, a default-off navigation hook, entitlement access, and explicitly opted-in portal links touch shared code. "No shared code touched" would be an inaccurate promise. Instead, each such edit needs a written impact analysis, targeted legacy tests, and default-off behavior.

Real scheduling will require additive database migrations. The previous no-migration constraint applied to the Kid Works demo; do not try to implement production reservations inside YAML to avoid migrations. No migrations are part of this design task.

## 6. Domain Model and Core Invariants

The names below describe responsibilities, not a commitment to create every table in the first release. C implements only connected-mode projections, configuration, selections, access, and delivery. Native entities arrive with N/L/X.

| Entity/group | Purpose and essential distinctions |
| --- | --- |
| ModuleAccess / SchedulingProfile | Tenant opt-in, capability grants, operational mode, trial/active/read-only state, configuration version. Absence means disabled. |
| Location | Address or online location, IANA time zone, opening hours, merchant/provider mapping. |
| StaffAccess / StaffProfile | User/tenant relationship, permitted locations, bookable skills/services, availability. Staff-only access does not grant enrollment-admin privileges. |
| Service / Offering | Stable service identity, type, duration, policy, intake schema, publication state; offering binds location, authority, and price. Optional SchoolProgram link. |
| Resource / Requirement | Staff, room, equipment; required qualification and resource selection. Exclusive resources first; pooled resources later. |
| AvailabilityRule / Exception | Working intervals, effective dates, closures, leave, buffers, manual blocks. Separate from confirmed appointments. |
| Series / Occurrence | Local-time recurrence definition plus persisted dated instances and exceptions. Occurrence owns class capacity and shared staff/resource reservations. |
| Customer / Participant / GuardianLink | Purchaser is not necessarily attendee; one payer may book several people. Optional links to existing enrollment records. |
| Booking / BookingAttendee / Reservation | Commercial/customer grouping, participant data, and seat allocation per occurrence. Keep stable IDs when rescheduling. |
| Hold / ResourceClaim | Temporary seat/resource claim with expiration and state; staff-approved pending requests also have explicit reservation deadlines. |
| OptionSchema / Selection | Versioned visual choice catalog, stable IDs, allowed combinations, per-booking/per-attendee scope, answer snapshot. |
| PolicyVersion / Quote | Accepted terms, effective cancellation rules, monetary breakdown, currency, price version. No historical repricing when catalog changes. |
| Shared payments: PaymentOrder / PaymentAttempt / Refund | Owned by the shared payments layer, with processor/account/mode references and allocations to booking or application lines. Never reuse the platform subscription ID for workshop payments. |
| Shared payments: BillingAgreement / Invoice | Recurring money collection, lifecycle, invoice reconciliation and dunning; not a recurring appointment calendar. |
| Scheduling: EntitlementLedger | Prepaid lesson entitlements, make-up credits, consumption, adjustments and reversals. Money credits and lesson credits remain distinct. |
| WaitlistEntry / Offer | Later: required party size, priority, expiration, and offered reservation. A waitlist is not a booking. |
| Attendance / BookingAudit | Attendance per attendee/occurrence; immutable actor/time/reason history for operational changes. |
| ProviderConnection / ExternalMapping | Tenant/account/site/mode scoped identities and secret references; map local objects to remote objects. |
| WebhookReceipt / OutboxDelivery | Deduplicated incoming events and durable outgoing work, versions, retries, and failure visibility. |

### Four scheduling patterns

1. **Appointment:** customer chooses an available start; duration and required staff/resources determine what fits. Supports a single participant or a private group.
2. **Class/workshop:** published occurrence; multiple unrelated bookings consume its seats. Staff/room are reserved once for the occurrence, not once per attendee.
3. **Course/cohort:** customer purchases a set of occurrences. Check and reserve all required occurrences atomically; define whether drop-ins or late joins are permitted.
4. **Standing lesson series:** one student/group owns repeating appointments with individual exceptions. Billing recurrence is independent of schedule recurrence.

Private parties are exclusive-capacity offerings, not necessarily a fifth engine. Tours are appointments; group tours are classes. Presets select these primitives without school-name conditionals.

### Invariants

- Every object, query, command, export, task, and provider mapping is tenant-scoped. Referenced related records must belong to the same tenant; enforce with constraints where practicable and service checks everywhere.
- Unique external identity includes provider, account/site, environment, and object ID. Test/live objects cannot collide or be mixed.
- Confirmed seats plus active reserved seats never exceed capacity. Reducing capacity below commitments is blocked, not silently overbooked.
- Exclusive resource intervals cannot overlap. Use half-open intervals and include setup/cleanup/travel buffers where applicable.
- A participant appearing in multiple bookings is not merged just because names or email addresses match.
- Amounts use integer minor units and a currency, never floating point. An order initially uses one merchant and one currency.
- Changes to capacity, availability, prices, teachers, or location do not silently alter previously accepted commitments.
- All booking mutations are idempotent and version-checked. The same idempotency key with different request data is rejected.
- Historical records retain accepted labels, prices, option IDs, and policy versions even when the catalog is retired.

Support optimistic version checks on admin edits too. Two administrators changing one booking must see a conflict instead of silently overwriting each other's work.

## 7. Correctness Rules, Including Difficult Cases

### Availability and double booking

Availability intersects service rules, local opening hours, qualified staff hours, required resources, exceptions, active reservations, and booking lead/cutoff rules. "Any teacher" selects a qualified available teacher deterministically; it is not unlimited capacity.

The calendar is a view, not the authority. Cached availability may be stale. Recheck inside the reservation transaction.

For native class seats, lock the occurrence and relevant inventory rows, expire eligible abandoned holds, validate requested party size, reserve, and commit an outbox event together. For appointment resource claims, lock candidate resource rows in deterministic order before checking overlapping intervals; locking existing reservations alone is insufficient when none exist yet.

Use PostgreSQL `tstzrange` exclusion constraints for active exclusive resource claims as a second protection. Expiration changes claim state explicitly; do not put a moving `now()` condition in a constraint. Confirm availability/capacity under locks even if an exclusion constraint exists. [T1]

Shared equipment with capacity greater than one is a different problem from one-room exclusion. Initially allocate concrete units; implement pooled inventory only when justified, with locked quantity calculations across overlapping intervals.

Group reservations are all-or-nothing by default. Show that only one of two requested seats is available instead of quietly splitting a family. Course booking locks all relevant occurrences in stable order and rolls back completely on any conflict.

For connected mode, provider validation and confirmation are authoritative. A local mirror cannot create an atomic transaction with Wix. Persist operation state and use the provider's documented lifecycle; ambiguous outcomes require lookup/reconciliation, not blind second booking creation.

### Recurrence, changes, and time zones

- Use `python-dateutil` recurrence support and IANA time zones; do not hand-roll date arithmetic. Explicitly test library behavior for nonexistent/ambiguous local times. [T2]
- Store the intended local start/time zone and persisted UTC occurrence times. A 4 PM weekly lesson stays at 4 PM across daylight saving changes.
- Support "this occurrence," "this and future," and "entire future series." Never rewrite attended/past financial history. Preview affected dates/conflicts before committing.
- Bound materialization to a published horizon, such as 180 days, and extend ahead of the booking window. A standing reservation has priority over new public availability during horizon extension. Failed extension alerts staff and closes affected future availability rather than advertising conflicting slots.
- Require a finite end date for the first lesson release if indefinite standing rights cannot yet be guaranteed.
- Holidays can skip, substitute, or move an occurrence according to explicit policy. Do not add a charge or make-up automatically merely because a closure exists.
- Missing local times are blocked/skipped with an administrator-visible explanation; repeated local times require a chosen offset. No silent shift of a booked lesson.
- Single-location screens can keep labels simple. Multi-location/online confirmation must make the location's time clear. Hiding a timezone label must not remove timezone-aware storage/validation.
- A teacher moving between locations needs a configurable travel buffer. Do not call a routing API for each availability check.

### Separate state machines

| State axis | Examples | Important rule |
| --- | --- | --- |
| Reservation | held, awaiting_approval, confirmed, canceled, expired | Only designated active states occupy capacity; approval holds expire. |
| Payment | unpaid, pending, paid, partially_refunded, refunded, disputed | Paid does not imply attended; canceled does not imply refunded. |
| Attendance | unmarked, present, absent, no_show | Marking no-show applies the accepted policy, not an invented default refund. |
| Integration | queued, syncing, synced, action_required | A Notion failure does not turn a paid confirmed booking into a failed booking. |

Completed attendance and enrollment remain separate. A workshop customer must not become an enrolled student automatically. Any opted-in enrollment transition has its own documented trigger and idempotency.

### Cancellation, rescheduling, and waitlists

- Snapshot policy at purchase: customer/teacher cancellation, notice cutoff, refund method, fees, credit eligibility, expiration, and approval requirements.
- Reserve the new slot before releasing the old one. A failed reschedule leaves the original confirmed reservation intact. Cross-provider changes are explicit compensating operations, not pretend database transactions.
- For extra payment, keep the original booking until the replacement and charge succeed; reconcile late/ambiguous outcomes. For a refund, show pending settlement separately.
- A teacher change or class cancellation must notify the correct affected families and staff, once. Bulk operations preview scope before committing.
- First waitlist release can be staff promotion only. Automated offers later hold the required seats for one eligible party, expire predictably, and prevent public sale of offered seats.
- Define party-size fairness explicitly: do not skip a party of three forever without showing why. No automatic partial group bookings.
- Preserve audit records of authorized policy exceptions. Do not provide a general "force double-book" bypass to core resource constraints.

### Credits and tuition

A package of ten lessons, a make-up lesson credit, a monetary refund, and flat monthly tuition are four different objects/rights.

Use an append-only ledger with grant/reserve/redeem/release/expire/reversal events and unique source IDs. Reserve credit when booking so simultaneous requests cannot spend it twice. Cancellation rules determine whether it is returned. Define service/teacher/participant eligibility and transferability; do not default to sharing credits across a household.

Flat tuition charges on its own schedule, including agreed holiday treatment. Per-lesson billing needs an explicit charge trigger and attendance/confirmation policy. Packages need purchase/refill and expiration rules. Keep all three policies out of the occurrence recurrence generator.

Pause, resume, plan changes, failed renewals, arrears, proration, and dispute behavior require explicit acceptance tests before switching an existing school's billing. No automated charge retry may create a second payment for the same obligation.

## 8. Integration and Payment Boundaries

### Configurable provider foundation (M4a)

The October 4 implementation separates a booking source from each destination. Customers can
use Pontora alone, Pontora with an operations destination, or an external booking source with
an operations destination. Wix and Notion are registered providers, not a fixed pair.

Owner/operator settings store typed identifiers, field mappings and encrypted credentials in
tenant-scoped connection records. Multiple independently named test/production drafts are
allowed; these labels do not create vendor sandboxes. Provider-specific forms and adapters
own their validation and request projection. New systems need adapter code and contract tests,
not changes to the booking domain or arbitrary executable configuration from a settings screen.

M4a supplies only draft settings and offline destination previews. It cannot change inventory
authority, activate checkout, sync customer data or assert that credentials work. OAuth,
schema discovery, durable delivery, provider identity reconciliation and explicit activation
remain M4 work. Read the [M4a implementation and verification](scheduling-m4a-verification.md)
for the extension contract, current limitations, official authentication research and key handling.

### Wix adapter

Expose narrow operations such as `list_offerings`, `list_availability`, `create_booking`, `get_booking`, `request_change`, and `get_payment_state`. Capability flags declare what the provider supports. Unsupported operations are not silently emulated.

Use stable provider IDs and current Forms submission APIs. Keep catalog and configuration locally cached; revalidate selected availability at booking. Provider webhooks and periodic bounded reconciliation update the mirror. Re-fetch current state for ambiguous/out-of-order events; do not downgrade a canceled booking from an old "created" event.

Each provider operation records its correlation/idempotency mechanism. Where Wix does not supply the needed guarantee, design a verified reconciliation strategy before allowing retries. Booking timeout shows "confirming" until resolved, not a second checkout button that creates duplicates.

A booking made through Wix outside Pontora must still appear in preparation data. A newly introduced required selection needs a deliberate transition plan for existing/future bookings, not surprise customer follow-up.

### Notion adapter

Notion is an operational destination, not the booking transaction database.

- Map to the customer's existing database/data source and property IDs. Current Notion APIs distinguish data sources from database containers; discover the actual schema instead of copying old examples. [N1]
- Support either one reservation row with an attendee summary or related booking/attendee rows. Choose the existing team workflow, not a new schema they must adopt for our convenience.
- Reserve a stable external booking/attendee key and save returned page IDs. Update known pages rather than search the entire workspace on every event.
- Pontora/provider-owned fields include date, participant selections, booking/payment summary. Team-owned fields such as preparation progress and notes are never overwritten by sync.
- On ambiguous page creation, look up the correlation key before retrying. Serialize delivery per entity. Notion does not give us a universal exactly-once transaction; detect and repair rare duplicates instead of claiming impossibility.
- Schema changes, deleted pages, revoked access, and invalid enum options appear as actionable errors. Do not recreate a deliberately deleted record repeatedly without a defined policy.
- Queue and rate-limit requests per connection/workspace, respect `Retry-After`, and cap retries. Current documented limits vary by workspace plan; do not assume one universal limit. [N2]
- Initial sync is one-way for authoritative booking fields. Two-way editing is a separately scoped feature, not an invisible default.

If a selection changes after staff marked preparation complete, preserve their notes but explicitly flag the changed preparation requirements for review. Keep the prior/new values in history; do not silently leave an already-packed booking looking ready.

Booking confirmation must not wait for Notion. Show last successful sync, pending count, failed count, and a safe retry action. Provide a local/exportable prep sheet during outages; routine copying must not be required.

### Payments: one implementation, three separately routed money flows

1. A business pays Pontora for software access.
2. A customer pays that business for a workshop/lesson.
3. An applicant pays an existing school application fee through the current enrollment flow.

These are separate business purposes, **not three payment modules**. Extend and gradually extract the current `core/services/billing_stripe.py` behind its existing entry points. Scheduling never implements its own Stripe checkout, subscription, or webhook client. Do not merge the purposes into existing platform subscription fields or change application-fee behavior as an incidental shortcut.

#### Critical code review: reuse the integration, not its unsafe assumptions

Inspected on October 1; findings are source-review evidence, not claims of a tested exploit or production incident:

| Current code | Finding | Required boundary before reuse |
| --- | --- | --- |
| `core/services/billing_stripe.py::_get_stripe` | Mutates a process-global Stripe API key. | Explicit account/environment context per operation; no mutable global merchant selection. |
| `create_checkout_session` and subscription handlers | Built for the school's Pontora subscription. Checkout uses `school.id`; events can change `School.plan` and `School.is_active`. `handle_subscription_updated` can fall back to `metadata.school_slug`. | A parent's tuition event must never reach platform entitlement handlers. Route through a verified local order/agreement, purpose, account and mode; school metadata alone is insufficient. |
| `core/views_public.py::apply_payment_view` | Creates a new intent on each payment-page request; the returned intent ID is not saved as a reusable attempt here. On Stripe creation failure it completes the application without payment. | Persistent attempts and idempotency; preserve explicit unpaid/pending state. Never reuse this fail-open behavior to confirm a paid reservation. Review any change to the existing application policy separately. |
| `apply_payment_confirm_view` and `_complete_submission_from_draft` | Checks provider status, but does not bind the intent to this draft, amount, currency and immutable order before recording `paid`. The completion helper persists the supplied ID/status; it adds no such verification. | Validate identity, account/mode, amount/currency and one-use allocation server-side, including attempts to reuse another successful intent. |
| `core/views_billing.py::stripe_webhook` | Existing dispatcher handles platform checkout/subscription events, not the full customer invoice/payment/refund lifecycle or durable event receipts. | Add a shared verified inbox with explicit purpose dispatch and invoice/payment/refund handlers; do not widen the existing platform handler indiscriminately. |

`core/tests/test_phase18.py` covers application payment success, failure, bypass and already-submitted drafts; `core/tests/test_billing.py` covers platform billing. Their existence is not evidence that the additional cross-order, merchant and recurring-invoice cases are covered. No production payment code was changed or tested against real processors during this design task.

#### Shared payment contract

- `PaymentContext`: tenant, merchant/payment account, environment, currency and business purpose. Resolved server-side from the offering/order, never from an untrusted host name or browser account ID alone.
- `PaymentOrder`: immutable line/amount/tax snapshot, payer, purpose, owning record and outstanding amount. An application fee and a booking order use the same financial machinery, with different domain callbacks.
- `PaymentAttempt`: stable operation key, provider session/intent, status and retry history. A network timeout causes lookup/reconciliation, not a fresh charge.
- `BillingAgreement`: payer and merchant, versioned recurring price, cadence, start/anchor, effective cancellation and terms. Each renewal has its own invoice and payment attempts. Do not create a second agreement when only a renewal payment fails.
- `PaymentCustomer`: scoped by provider account and mode. A school paying Pontora and a parent paying that school are different customers, even when emails happen to match.
- `ProviderEventReceipt`: unique provider/account/mode/event ID plus processing state. A durable transaction/outbox records the financial change and domain delivery. A database uniqueness rule and idempotent handlers prevent repeated grants/refunds.
- `Refund` and adjustments remain in the shared money layer. Scheduling decides eligibility and attendance/seat consequences; it must not assume a requested refund has settled.

One Stripe gateway can use one-time Checkout (`mode=payment`) and subscription Checkout (`mode=subscription`). Existing PaymentIntent-based application checkout can remain another entry point to that same gateway; a second user interface does not require a second processor integration. Mixed one-time/subscription purchases need explicit allocations and initial-invoice semantics; defer them until required rather than silently treating a registration fee as recurring. [T8]

Recurring **lessons** and recurring **payments** are independent. A weekly appointment might be paid per visit, from a package, or by a flat monthly tuition agreement. Canceling one lesson does not cancel tuition; ending tuition does not silently erase future appointments. Apply separately approved policies with a preview of the effective dates, reservations and credits affected.

Recurring access needs verified initial payment or an explicitly approved trial, renewal success/failure, authentication-required, cancellation and refunds/disputes. Consume invoice lifecycle events in addition to subscription changes, reconciling provider state when delivery is out of order. Grace periods and future reservation treatment are business configuration, not unconditional school deactivation. [T9]

For recurring catalog prices, retain provider references in the database under the merchant/mode. Do not add an environment variable per school or tuition amount. Existing Pontora subscription environment variables stay compatible. Initial connected Wix payments are mirrored as externally owned records; Pontora does not also collect the money or manufacture a Stripe agreement.

#### Safe extension sequence

1. Characterize existing platform and application fee behavior with regression tests, including real SDK object shapes. Add the missing negative tests and record the current risks.
2. Extract the transport and object normalization into the shared layer, preserving the existing facade/signatures. Explicit account clients replace global-key mutation behind tested existing entry points. No customer migration in this step.
3. Add shared order/attempt/agreement/event records and explicit purpose dispatch behind default-off capabilities. Native bookings use this layer directly. Keep legacy events on a tightly scoped legacy route until their objects are mapped; unknown events are quarantined, not guessed from `school_slug`.
4. Harden application-fee order binding and failure handling in a separately reviewed change, with a decided unpaid-application policy. This is a prerequisite for claiming a generally reusable payment module, not permission to silently change current schools.
5. Move legacy platform/application callers to the shared financial orchestration incrementally. Old function names may remain facades; duplicate provider logic must not remain as a permanent alternative engine.
6. Prove one-time checkout/refunds first, and recurring invoices/dunning/cancellation before any music-school migration. Extracting shared code is a real SBMC regression risk even when scheduling is disabled, so baseline tests are mandatory at each step.

No large framework, generic payment-plugin loader or second deployment is needed. The target is **one shared payments package, one Stripe implementation, domain-specific consequences**. The previous version did not make that ownership clear enough and could have led to a duplicate payment stack.

Connected Wix bookings retain Wix payment/refund authority. For native bookings, prefer a supported Stripe Connect onboarding model over collecting merchant secret keys in new settings forms. Determine merchant ownership, country/currency support, refund/dispute responsibility, and Connect configuration before enabling live charges. Use merchant-scoped payment clients and IDs, not a mutable global Stripe key.

Native checkout must have a saved server-calculated quote and reservation before payment begins. Customer-supplied totals, prices, quantity, and account IDs are never trusted. Verify amount, currency, merchant, environment, and booking correlation on payment events. The success redirect is not proof of payment. [T3]

For Stripe-hosted Checkout, timed session expiration has a documented 30-minute minimum. Do not promise a 10-minute hold while leaving Checkout payable for 30 minutes. Initially align the hold with Checkout's allowed expiry and disable delayed-settlement methods for scarce seats unless we implement a matching reservation policy. [T4]

When expiry races payment, transition to reconciliation and check/expire the provider session before reselling the seat. Provider unavailability can temporarily reduce availability; it cannot justify overselling. If a late payment arrives after capacity was validly released, reacquire capacity atomically or initiate a traceable refund and notify operations. Never confirm beyond capacity.

Use signed, deduplicated webhooks and durable event receipts. Duplicate events and retries cannot repeat charges, refunds, or credits. Tax categories/collection are merchant decisions requiring appropriate advice; this design does not invent tax settings.

### Calendar providers

Start with stable-ID calendar invitations/feeds. Do not describe a one-way ICS feed as live two-way scheduling.

Later Google busy-time integration uses incremental synchronization, persisted tokens, expiring notification subscriptions, and bounded reconciliation. Token invalidation rebuilds only the provider mirror, never deletes Pontora's bookings. [T5]

External calendar edits cannot atomically coordinate with our database. Define a freshness threshold and fail closed for new bookings when a required busy-time source is stale. An event created elsewhere immediately after our check can still conflict; surface it for resolution. Do not promise zero cross-system conflicts.

## 9. Configuration, Access, and User Experience

### Opt-in and standalone behavior

Add explicit scheduling access outside ordinary enrollment plan defaults. Initial operator enablement can follow the existing operator-only flag pattern, backed by a SchedulingProfile. Paid standalone/bundled launch needs a module entitlement record with provisioning, trial, active, suspended/read-only, and ended states.

The gate checks tenant/module access plus actor permissions for every UI, endpoint, background command, and export. Do not rely on hiding a navigation item. Existing Trial, Growth, Custom, or founder pricing must not automatically grant scheduling.

Enrollment licensing remains as it is. A booking-only customer uses a dedicated shell and booking entitlement, not a fabricated enrollment plan to suppress an expired enrollment-trial banner. Future module billing uses separate subscription/item mapping without changing legacy plan reconciliation.

Disabling new bookings does not discard reservations, stop refund reconciliation, or prevent staff from honoring existing commitments. Define separate switches for sales, operational access, outbound connectors, and provider callback processing.

### Per-customer customization

Administrators manage services, calendars, options, and policies through forms. YAML can supply validated presets/defaults for onboarding, but not act as the live reservation database or an arbitrary workflow programming language.

Illustrative configuration, not current supported syntax:

```yaml
scheduling:
  schema_version: 1
  enabled: false
  preset: workshop
  default_authority: wix
  terminology:
    attendee: Participant
    service: Workshop
  selection_schema:
    scope: attendee
    fields:
      - key: design
        type: image_choice
        catalog: workshop_designs
        required: true
      - key: color
        type: color_choice
        catalog: workshop_colors
        required: true
    preview:
      asset_catalog: approved_design_color_images
  integrations:
    notion:
      connection_ref: workshop_operations
      mapping_ref: reservation_prep_v1
```

Music preset: appointment + standing-series capabilities, instrument-qualified staff, duration choices, optional package/make-up rules. Preschool preset: tours/playdates with approval policy. They use the same engine; no `school.slug == ...` business logic.

Custom field types are a small versioned set: text, number, date, dropdown, multi-choice, image choice, color choice, consent. Add a limited declarative visibility/allowed-combination rule set rather than arbitrary expressions. Money, identity, capacity, and booking status remain typed server-owned fields.

Selection previews use supplied photos or approved pre-rendered variants. Do not generate a guessed product image with AI during checkout. Store catalog IDs and renderable snapshots so retired options remain understandable on old rosters.

Location-specific pricing, permitted staff, languages/labels, booking windows, confirmation policy, cancellation rules, and connection mappings belong in database configuration. No new environment variable per school, price, or color.

### Admin experience

- **Calendar / Agenda:** day/week views, location/staff/service filters, create/move/cancel, conflict explanations; a list view works well on phones.
- **Reservations:** search, payment/attendance state, customer history, and actions appropriate to the scheduling authority.
- **Roster / Preparation:** attendee options, per-session preparation totals, check-in, export, and sync health. Preparation fields generic and configured.
- **Services & Availability:** offerings, images/options, staff qualifications, resources, hours, closures, policies.
- **Connections & Settings:** Wix/Notion/payment/calendar connection status, field mappings, role access, audit history.

Bookings-only businesses see these areas, not empty Leads/Submissions tabs. Enrollment customers get an additional gated scheduling entry and linked booking information where useful. Advanced fields remain hidden until their capability is enabled.

Standalone customers receive a business account and booking-specific staff access, not an enrollment form or fabricated enrollment subscription. Current `core/views_login.py` routes school memberships to the enrollment dashboard; this must be handled explicitly in a scoped booking login/default route with regression tests. Reusing Django authentication does not mean granting SchoolAdminMembership to every booking operator.

Customers working entirely in Wix/Notion need not adopt a second daily dashboard. They still need an authorized owner for connections, permissions, failed-sync recovery and exports, either through minimal Pontora settings or explicitly agreed operator-assisted management. Notion remains a downstream operations view, not an independent source of seat availability. Unsupported providers require an adapter and contract tests; a settings dropdown cannot make arbitrary integrations work.

### Setup and publication

Use a short setup checklist: business/locations, rooms and resources, services and options, eligible staff, availability/closures/buffers, prices and policies, then preview and publish. A music preset adds instruments and lesson durations; a workshop preset adds session capacity and per-attendee visual choices. Students see published offerings and available times, not draft configuration or a teacher's private calendar.

Existing reservations must survive configuration edits. Preview affected bookings before room, staff, duration or recurrence changes; never silently rebuild a school's calendar. Importing SBMC's existing commitments and reconciling its billing remain separate migration gates, not part of the initial setup wizard.

Reuse the current design conventions. FullCalendar is a candidate for calendar rendering, not booking logic; standard components are MIT, while resource-oriented Premium views require a suitable license. Decide that cost explicitly. [T6]

### Permissions and customer access

- Owner: configuration, merchant connections, staff access, policy/financial exceptions.
- Scheduler: authorized locations, booking management, limited payment actions.
- Instructor: own availability/assigned roster and attendance; no automatic access to all enrollment applications or billing.
- Preparation staff: relevant location/session selections only.
- Customer/guardian: only their authorized bookings/participants, within policy.

Add module-specific grants referencing existing Django users and School. Do not give instructors SchoolAdminMembership merely to reuse a login if that exposes enrollment data. Audit every sensitive action.

Guests may book without creating an account. Management links are opaque, scoped, expiring/revocable tokens; do not authorize by submitted email alone. Household access requires verified guardian relationships. Existing parent enrollment status tokens must not automatically authorize other children's bookings or financial actions.

A party organizer is not automatically every minor's legal guardian. Preserve the business's approved waiver process and record who accepted which version for whom; distinguish acknowledgment from an actual required signature. Do not claim that a group purchaser's checkbox satisfies all participants' consent requirements.

## 10. Reliability, Cost, and Security

### Background work

Use a transactional outbox in PostgreSQL plus a proven worker system. Proposed default: Celery with a managed Redis broker and a worker process, added only with the first connector release. It adds hosting/operations cost; the checked-in deployment currently does not provide it. Revisit the broker choice if deployment constraints make another supported backend preferable.

Create event and domain change in one database transaction. A dispatcher publishes persisted work; losing a broker message is recoverable because unfinished outbox rows remain. Use worker leases, bounded retries/backoff, per-provider throttles, and dead-letter/action-required status. A scheduler runs reconciliation, reminders, and expiry checks. Do not keep essential work in web-process threads or the user's laptop.

Exactly-once delivery across providers is not promised. Use at-least-once delivery with idempotent effects, event versions, and reconciliation. Notifications check the latest reservation version before sending so a canceled lesson does not receive an old reminder. [T7]

### Avoid repeating outreach's API-cost mistakes

- No Anthropic calls in booking, pricing, availability, or Notion mapping. These operations are deterministic.
- No Google Places calls needed for scheduling. Locations are entered/configured once.
- Reading admin pages uses local projections, not a full provider scan.
- Cache static catalogs and short-lived availability with bounded date windows; commit always revalidates.
- Coalesce repeated Notion updates for the same booking and skip unchanged payloads using hashes/versions.
- Rate-limit by tenant and provider connection; cap reconciliation work and paginate/checkpoint imports.
- Capture API calls per completed booking, duplicate-delivery count, failed operations, and sync lag. Show usable diagnostics in Ops, not only Sentry stack traces.
- Do not suppress necessary validation or ignore legitimate bookings merely to meet an API budget. Degrade explicitly with a clear temporary-unavailability message.

### Security and environment isolation

- Secrets in a secret manager/encrypted storage with rotation and least-privilege scopes; references in settings, never browser bundles or YAML.
- Verify webhook signatures and resolve tenant from a registered provider/account mapping, not arbitrary payload school IDs.
- Explicit environment-scoped connections and credential/account validation. Demo/local uses sandbox data and outbound-message suppression. No live keys fall back into test mode or vice versa.
- CSRF protection on browser mutations, endpoint rate limits and anti-abuse controls on holds, secure uploads, and allowlisted integration destinations to avoid SSRF.
- Minimize child/contact data copied to Notion and provider logs. Do not export all enrollment answers or waivers by default.
- Establish retention, deletion/anonymization, backups, recovery, and audit access. Financial records and operational history may need different retention rules; obtain qualified guidance rather than assuming compliance.
- Aggregate cross-location reporting only within the same authorized business. Independently owned franchises need separate tenant boundaries unless a formal shared-access model is approved.

### Proposed pilot service targets, not current guarantees

- Local admin reads: p95 under 1 second; availability responses: p95 under 2 seconds excluding provider outages, measured under agreed load.
- Notion sync: normally within 60 seconds; visible warning after 5 minutes and an operations alert after 15 minutes of persistent failure.
- Booking/payment state: never label pending as confirmed to hide latency.
- Alert on worker backlog, stale calendar sources, failed recurring-horizon extension, ambiguous payments, and invalid credentials.
- Report bookings completed, selection completeness, preparation corrections, manual interventions, and API usage. Baseline the current manual process before claiming savings.

## 11. Delivery Plan and Release Gates

This is a multi-release product effort, not a small form enhancement. Do not quote a production date or fixed implementation fee before the provider prototype and customer-specific migration inventory.

The October 1 discussion prioritizes a narrow working demonstration by Tuesday, October 6, followed by feature-by-feature acceptance. See [implementation tracker](scheduling-implementation-tracker.md) for the live status. A local persisted workshop request and preparation roster can be built without provider access; it must not be described as a working Wix integration or confirmed paid reservation. Prototype implementation is separate from the paid release gates below.

### Phase 0: validate scope and provider feasibility

- Confirm Plushumi's service types, accounts, group selections, preparation schema, existing gift-card/payment paths, and desired pilot boundary.
- In an approved sandbox, prove the two-attendee class booking, correct payment state, Notion write, cancellation/reschedule update, and duplicate-event handling.
- Inventory SBMC's actual scheduling/billing rules and supported exports/API. Confirm whether it wants a replacement or only better enrollment handoff.
- Produce an agreed capability checklist, impact analysis, and commercial pilot scope.

**Gate:** demonstrated end-to-end provider flow and explicit customer acceptance of what is included/excluded. If Wix cannot preserve the required experience, present the native option; do not quietly add a second customer form.

### Phase C: paid connected pilot

Visual intake, per-attendee selections, Wix authority, local projection, reusable Notion mapping, preparation view, safe retries, audit, access controls, worker monitoring. Start with one agreed service/location; expand after measuring completeness and staff effort.

**Gate:** every pilot paid booking and subsequent change reaches operations correctly, including bookings entering through Wix directly; outages recover without routine retyping. Use native tables only for native/demo resource pools; never independently sell capacity belonging to Wix.

### Phase N: native booking foundation

Optional tenant profile, services/locations, staff/exclusive resources, single-session bookings, recurrence for published class occurrences, transactional holds, basic roster, full payment/refund flow, single-occurrence changes, reminders, admin calendar, standalone shell.

**Gate:** native capacity/payment/security tests pass under concurrency; rollback and support runbooks exist. This release is not yet a full MakeMusic replacement.

### Phase E: early parties and camps

Move these ahead of general expansion. Start with party requests, group size, resource needs, and clearly distinguished shared-session versus exclusive-room bookings. Native paid parties also require verified deposits, remaining balance, cancellation/refund rules and recorded offline settlement through the shared payment contract. Capturing a request or displaying a deposit is not collecting payment.

For camps, start with a fixed cohort spanning specified sessions, one purchaser and participant list, and all-occurrence capacity/resource validation. If any required session cannot be reserved, do not leave a partial registration or charge. Flexible drop-ins, pro-rating and advanced camp operations follow demonstrated need. Confirm Plushumi's actual camp offering and policies before a customer commitment.

**Gate:** party exclusivity and deposit/balance tests, or camp all-session booking/cancellation tests, pass for the enabled capability. Do not defer these requirements past a migration that depends on them.

### Phase L: music-school readiness

Standing lessons, household access, series exceptions, substitute teachers, attendance policies, tested credit ledger, required tuition/package/per-lesson billing, waitlists, required calendar integrations, optional enrollment bridge.

Implementation checkpoint, October 4: M7c1 private family access is accepted. M7c2 has a
local-only public-trial/family-single-lesson booking slice using the existing conflict planner,
tenant lock and shared payment ledger. Family write permission and offer make-up eligibility
are independently opt-in. Notification previews are saved, not delivered. Verified re-entry,
delivery/retry and online lesson settlement are still engineering work; recurring tuition is M8.
The local-preview guard is a deliberate temporary rollout restriction, not the eventual
per-customer capability packaging. See the tracker and M7c2 verification for actual status.

**Gate:** SBMC's signed workflow checklist passes on a separate sandbox copy. Reconcile counts, recurring dates, balances/credits, guardians, permissions, and notifications before any cutover. Never infer a recurring lesson schedule from text preferences.

### Phase X: demand-led expansion

Memberships, promotions/gift cards, pooled resources/material stock, flexible course attendance, deeper reporting, additional connectors. Pull a feature forward if it is necessary for an agreed migration; otherwise do not build it speculatively.

### Business packaging

Offer Bookings standalone, or Bookings as an enrollment add-on. Charge separately for complex integration/onboarding and ongoing operations; do not silently include unlimited integration work in low-priced founder enrollment plans. Review any existing customer commitments before pricing new modules.

Use one published feature catalog with module, capability, maturity (prototype/pilot/production), dependencies, customer configuration, tests, and owner. Ops should show enabled capabilities and connection health per tenant. This is how we avoid a codebase whose available features only live in chat history.

## 12. Testing, Rollout, and Self-Review

### Every push is a verification gate

The user's October 1 requirement applies to every push: meaningful unit tests, database-backed
integration tests, the full regression suite, and agent-performed browser/manual checks, including
existing-school safety. Verify the complete outgoing change at the actual revision being pushed;
record results and shared-code impact. Failed or unavailable required checks block the push.
The [implementation tracker](scheduling-implementation-tracker.md) holds the concrete checklist
and verification record. User acceptance is an additional gate, not outsourced QA. Normal tests
make no paid calls or real messages; provider readiness additionally requires approved sandbox
proof. No such implementation test results are claimed by this design document.

### Required tests before paid native use

| Scenario | Required result |
| --- | --- |
| Twenty simultaneous buyers for one seat | Exactly one succeeds; no negative availability or double charge. |
| Two services request the same teacher/room | One valid non-overlapping claim; no race when no prior reservation exists. |
| Two-person party, one seat remaining | Entire request rejected or explicit alternative offered. |
| Course with a full occurrence (when courses launch) | No partial course reservation or partial charge. |
| Duplicate submission / timeout retry | Same outcome/booking for same key; changed payload rejected. |
| Hold expires while payment finishes | Reconcile without overselling, orphan payment, or silent data loss. |
| Payment webhook duplicate/out of order | One financial effect; verify current state and account/mode. |
| Paid intent from another draft/order or wrong total | Rejected; cannot mark the new application/booking paid. |
| Tuition renewal or cancellation event | Updates only that family's agreement; never changes the school's Pontora plan or active flag. |
| Monthly renewal fails or requires authentication | Existing agreement retained, explicit customer action/grace policy, no duplicate subscription or credits. |
| End tuition versus cancel one lesson | Distinct effective dates and previewed effects; neither silently changes the other. |
| Two merchants or test/live requests interleave | Correct isolated clients, customer IDs, price IDs and signed events; no cross-account use. |
| Client forges price, capacity, school, or staff ID | Request rejected; no cross-tenant access. |
| Canceled event has a queued reminder | Stale reminder suppressed; correction delivered once. |
| Daylight saving, month end, holiday, leap day | Correct local time or explicit exception; no silent disappearance. |
| Move this / future / entire series | Correct scope, preserved past/audit, conflicts previewed. |
| Reschedule into a full slot | Original booking remains valid. |
| Concurrent credit redemption | Credit consumed once; reversal is traceable. |
| Notion 429, schema change, revoked access, ambiguous create | Bounded retries or actionable error; booking stays valid; duplicate detection/recovery. |
| Wix direct booking/cancellation outside Pontora | Mirror and prep update; missing selections visible. |
| Selection changes after preparation is complete | New requirements clearly flagged, prior work/notes retained. |
| Two admins edit the same booking | Version conflict, no silent lost update. |
| Required calendar sync stale | New affected availability withheld, existing commitments preserved. |
| Worker crash/redeploy/laptop closes | Durable work resumes without replaying charges/messages. |
| Scheduling disabled for existing tenant | Existing UI, redirects, data, mail, permissions, billing remain unchanged. |
| Standalone booking tenant with expired enrollment trial | Booking entitlement controls booking access; no irrelevant enrollment banner/block. |
| Teacher/prep staff signs in | Only authorized schedule/location/data visible, no enrollment-admin escalation. |

Use real PostgreSQL transaction/concurrency tests, not SQLite or mocks alone. Provider contract tests need realistic SDK objects and recorded/sanitized payload shapes, not dictionaries that hide SDK incompatibilities. Ordinary CI blocks outbound network by default; paid APIs and real messages are never part of normal tests.

Separate explicit sandbox integration tests validate provider authorization, field mapping, checkout, cancellations/refunds, and webhook retries. Browser tests cover customer/admin journeys on desktop/mobile, keyboard access, visual choices without color-only meaning, and loading/error states.

### Protect existing schools

1. Capture SBMC baseline tests for trial redirects, preferences, lead/application handling, parent portal, permissions, notifications, exports, and billing. Include Kid Works and non-opted-in schools.
2. Add new tables/indexes and optional links without backfilling or changing existing records. Measure migration lock/runtime on a realistic copy before deployment.
3. Add shared hooks only behind deny-by-default checks. Confirm query/performance impact when scheduling is off. A flag does not protect against a bad migration or shared import error.
4. Deploy with all existing tenants disabled; verify old flows in production without sending test mail or taking payments.
5. Enable approved sandbox/pilot tenants only, then explicitly agreed production services. Do not run two live booking engines over the same capacity.
6. Separate rollback of new sales from fulfillment. Keep read access, cancellation/refund processing, event receipts, and exports for existing commitments. Stop new sales first if correctness is uncertain.
7. Any SBMC migration freezes provider changes in an agreed window, exports and reconciles, cuts over once, and retains an audited rollback plan. Payment methods/billing agreements require a supported migration; they cannot be assumed portable.

### Self-review: weaknesses this design deliberately does not hide

- The connected Wix flow is plausible from current APIs but still unproven for Plushumi's exact service/form/payment setup. That is the first technical gate.
- A second scheduling authority is a serious operational risk. Native mode must not be sold as a harmless visual refresh.
- Using School as the tenant avoids a large refactor, but standalone navigation, trial checks, roles, and module licensing need explicit integration work. Independence is not automatic.
- More tables and a worker increase maintenance. Deliver them by phase; do not build a general workflow engine, generic plugin loader, or distributed event platform first.
- Sync creates operational obligations. A successful first Notion write is not a reliable integration; cancellations, duplicates, outages, permission changes, and reconciliation are part of the deliverable.
- Resource exclusion does not solve pooled capacity, recurring future commitments, external calendar races, or payment timing by itself.
- Full music-school billing is a substantial additional product. SBMC cannot be safely moved after only the workshop milestone.
- We cannot certify SBMC behavior remains unchanged from this document alone. That requires implementation-specific regression evidence. This design task itself changes no application behavior.

## 13. Decisions Needed Before Implementation

1. Optional module and incremental, user-tested delivery are the requested direction as of October 1. Confirm the actual paid pilot boundary separately; the demonstration is not a commitment to full competitor parity or a native migration.
2. Confirm which Plushumi location/service starts the pilot and obtain authorized sandbox/provider access. Confirm its Notion schema and field ownership.
3. Confirm whether material selections affect stock, price, or preparation only; obtain the approved image/option catalog.
4. Confirm SBMC's actual booking, teacher, room, cancellation, recurring billing, and credit workflows. The supplied public trial screenshot has been reviewed; the remaining checkout and administrative behavior still needs validation.
5. Confirm merchant ownership across locations, supported payment methods, deposit/refund requirements, and whether current billing is in migration scope.
6. Approve future migrations, worker infrastructure, module entitlements, and a paid pilot/support budget before production implementation.

## Sources

Payment references rechecked October 1, 2026:

- [T8] Stripe Checkout creation and payment/subscription modes: https://docs.stripe.com/api/checkout/sessions/create
- [T9] Stripe recurring invoice and subscription events: https://docs.stripe.com/billing/subscriptions/webhooks

Primary vendor documentation reviewed September 30, 2026. Vendor behavior and API versions must be rechecked during the implementation spike. Proposals and phase assignments above are our engineering recommendations, not vendor claims.

- [P1] [The Plushie Workshop website and locations](https://www.theplushieworkshop.com/)
- [P2] [The Plushie Workshop booking and cancellation policies](https://www.theplushieworkshop.com/booking-and-cancellation-policy)
- [W1] [Wix single-service booking flow](https://dev.wix.com/docs/api-reference/business-solutions/bookings/flow-single-service-booking)
- [W2] [Wix booking sample flows for appointments, classes, and courses](https://dev.wix.com/docs/sdk/backend-modules/bookings/bookings/sample-flows)
- [W3] [Wix Forms integration](https://dev.wix.com/docs/sdk/backend-modules/bookings/wix-forms-integration)
- [W4] [Wix booking policies](https://support.wix.com/en/article/wix-bookings-setting-up-your-booking-policies)
- [W5] [Wix resources and rooms](https://support.wix.com/en/article/wix-bookings-managing-your-resources)
- [W6] [Wix creating a class](https://support.wix.com/en/article/setting-availability-for-classes-in-wix-bookings)
- [M1] [MakeMusic Studio features](https://www.studio.makemusic.com/features/)
- [M2] [MakeMusic Studio appointment creation and recurrence](https://help.studio.makemusic.com/en/articles/2059799-appointment-schedule-create)
- [M3] [MakeMusic Studio appointment changes and absence handling](https://help.studio.makemusic.com/en/articles/2059793-change-appointment-lesson-cancel-reschedule-price-change-absent-how-to-for-providers)
- [M4] [MakeMusic Studio flat-rate credits and make-up limitation](https://help.studio.makemusic.com/en/articles/9998607-apply-a-credit-to-a-flat-rate-subscription)
- [MS1] [My Music Staff calendar and attendance](https://www.mymusicstaff.com/calendar-attendance/)
- [S1] [Sawyer semester and activity guide](https://help.hisawyer.com/en/articles/14827297-semester-activity-guide)
- [A1] [Acuity resource limits](https://help.acuityscheduling.com/hc/en-us/articles/16676949567757-Use-resources-to-limit-bookings)
- [N1] [Notion databases and data sources](https://developers.notion.com/guides/data-apis/working-with-databases)
- [N2] [Notion request limits and retry behavior](https://developers.notion.com/reference/request-limits)
- [T1] [PostgreSQL range types and exclusion constraints](https://www.postgresql.org/docs/current/rangetypes.html)
- [T2] [python-dateutil recurrence rules](https://dateutil.readthedocs.io/en/stable/rrule.html)
- [T3] [Stripe webhook handling](https://docs.stripe.com/webhooks)
- [T4] [Stripe Checkout limited inventory and session expiry](https://docs.stripe.com/payments/checkout/managing-limited-inventory)
- [T5] [Google Calendar incremental synchronization](https://developers.google.com/workspace/calendar/api/guides/sync)
- [T6] [FullCalendar licensing](https://fullcalendar.io/license)
- [T7] [Celery task design and retries](https://docs.celeryq.dev/en/stable/userguide/tasks.html)
