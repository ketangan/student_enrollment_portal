# M9a: Native Tours and Optional Enrollment Leads

Date: October 4, 2026. Ready for local user testing; no deployment approval.

## Delivered Scope

- Default-off Tours & visits capability. A business can use tours without enrollment,
  workshops or paid reservations being enabled in its workspace.
- Staff creates a tour, chooses its location, and publishes explicit dated times with
  a room, host and capacity counting both adults and children.
- Families book a real slot and receive an on-screen confirmation. The shared reservation
  engine protects capacity; a zero-dollar order uses the existing ledger without Stripe.
- Staff sees occupancy, family details and attendance, and can cancel a booking. Future
  attendance is blocked; cancellation releases places. Staff actions have audit history.
- Optional lead creation requires both scheduling-owner and enrollment-owner access.
  Each booking has its own lead, matching enrollment's existing multi-child intake policy.
  A signed retry returns the same booking/lead/order instead of creating another.
- Attendance advances only that booking's still-pending lead to Tour Completed; cancellation
  or a no-show resets only its own pending lead. Manually advanced or converted leads are
  preserved. Turning off the connection stops future synchronization, not historical records.

## Impact Review

New visit models, services, views and templates live in scheduling. The new migration
`0016_visits` adds three tables, an optional Reservation link and extends its source constraint.
It contains no school activation or data migration. Applied only to the disposable Bookings
preview database. Existing school YAML, enrollment views, billing and payment runtime were
not edited for M9a. `preschool-demo.yaml` describes a new fictional preview school only.

Shared scheduling changes are deliberate: feature gates, navigation, workspace summaries,
reservation source constraints and exclusion of visit-backed records from workshop/payment
actions. Room conflict and capacity queries still include visits. The shared capacity error
now uses wording suitable for single sessions as well as camps; its validation is unchanged.
No second payment implementation was introduced.

Enrollment is connected through its existing intake service and data contract. The bridge
checks enrollment staff/membership access without weakening enrollment's own permission gate.
The ordinary enrollment pages are not newly gated by tour attendance in M9a.

SBMC and Kid Works have not been enabled, configured or migrated to this new flow.
Their current enrollment behavior is protected by the full regression suite, but this is
local evidence, not a production deployment certification.

## Automated Verification

Final full suite: **2,960 passed, 2 existing warnings, 167.47 seconds**.
Four new runtime modules (`visits`, `visit_forms`, `visit_models`, `views_visits`):
**100% statement and branch coverage**, 367 statements and 64 branches.
Scheduling/shared-payments/test-safety combined coverage: **99.92%**. Every measured module
meets the 90% minimum individually. Migrations, templates and tests are not included in this
Python coverage figure; template behavior is covered by integration and browser checks.
Migration drift check: no changes detected. Whitespace check passed.

Artifacts: `/tmp/pontora-m9a-final.log`, `/tmp/pontora-m9a-final.coverage`,
`/tmp/pontora-m9a-final-coverage.json`. These are local verification artifacts, not CI results.

Tour-specific checks include standalone booking, optional lead creation, child/guardian
prefill, replay/changed payloads, last-seat concurrent submissions, rollback on ledger failure,
room/host conflicts, stale edits, publication changes, cross-tenant/role checks, CSRF, escaping,
bounded query counts, attendance timing, lead preservation and cancellation capacity release.
Tests use a disposable PostgreSQL database, local email capture and blocked external sockets.

## Manual Verification

Desktop (1280px) and mobile (390px) checks passed: parent booking/receipt, over-capacity
rejection followed by a valid booking, staff occupancy/Full state, room and host conflict
rejection, publication of a new Friday time, attendance-to-lead transition, correct child/tour
fields, and cancellation restoring places and clearing only the linked pending lead's tour.
Pages remained within their viewport; dense staff tables scroll internally on mobile.
Saved records persisted through the refreshed server. A past Robin Practice example remains
available for the user's attendance test. Screenshots: `/tmp/pontora-m9a-desktop.png` and
`/tmp/pontora-m9a-mobile.png`. The tracker contains repeatable steps and local-only credentials.

## Self-Review Corrections

- Replaced email-only lead matching: one parent may have several children or prior records.
- Matched enrollment's form_fields structure so child and tour details render and prefill.
- Canonicalized signed timestamps to UTC so in-memory local times match reloaded rows.
- Matched the existing enrollment staff/active-membership boundary before offering a lead link
  or enabling the connection; booking-only staff do not gain enrollment access.
- Added real occupancy and Full/Closed states, readable horizontally scrolling staff tables,
  correct child/children wording and a non-camp-specific capacity error.

## Remaining Scope and Rollout Limits

1. M9b: application release, eligibility rules and real playdate booking, linked to the same
   child/application. All public/resume/admin entry points must enforce the chosen policy.
2. Explicit slots only. Recurring availability, closures across visit schedules and staff
   rescheduling are not delivered. Cancellation followed by a new booking is the current path.
3. Hosts are scheduling members. Tour-to-tour host conflicts are checked, but they are not
   automatically mapped to music teacher identities/availability. A mixed-role staff rollout
   needs a shared staff-resource mapping and regression tests first.
4. Attendance/cancellation outcomes are terminal in this slice; correction/reopen tooling and
   a parent reschedule/cancel journey remain follow-up work, not a production-ready claim.
5. No outbound confirmations or durable family re-entry. Receipts are browser-bound; using
   another browser or losing that session requires contacting the school. Add verified family
   access and reliable delivery before real use; do not send sensitive details to an unverified email.
6. Free visits only. Existing shared payments support other modules, but paid tour checkout
   and recurring tuition are not added by this work. No live API calls, email or charges occurred.
7. One prospective child's identity per booking; additional attending children count toward
   capacity, not separate applications. Rescheduling reconciliation and school-specific field
   mapping/lead-status policy need definition before connecting a real customer.

M4a configuration remains available; connected M4 is deferred pending Plushumi input and
authorized provider verification. M9a acceptance does not complete M9 or authorize a push.
