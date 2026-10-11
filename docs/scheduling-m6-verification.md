# M6: Fixed-Session Camps

Date: October 2, 2026. Local preview verified; ready for user testing, not a paid pilot.

## Delivered Scope

- Owner setup selects 2-31 fixed sessions from one workshop and location. Each session can
  have its own room, time and capacity. Existing visual choice groups are reused.
- One purchaser registers 1-20 participants, with separate names and choices. All dates
  reserve together or the transaction leaves no registration, order or occurrence behind.
- Every occurrence shares the existing session seat pool with workshops and other camps.
  A camp does not reserve its room continuously between dates.
- One immutable shared PaymentOrder covers the group. The price and optional deposit are
  per participant for the whole camp. Occurrence allocations sum exactly to the order,
  including rounding cents. Allocations are not additional charges or per-date refund policies.
- Free registration confirms immediately. Paid registration starts a 30-minute hold and
  uses existing offline settlement or the local simulated Stripe checkout. An unresolved
  checkout pins every date until verification, even after the clock expires.
- Late payment either confirms every date or records money with a review-needed state;
  it never partially books a camp or discards received money.
- Owner cancellation releases all dates and retains history. Refunds remain explicit,
  owner-approved and verified through the existing shared ledger.
- Camp roster includes held, confirmed and canceled records with clear status. Session
  preparation includes only confirmed camp participants and their choice totals, separately
  from unpaid workshop inquiries.

## Impact Analysis

Changes are confined to scheduling models/services/views/templates/assets, its tests, and
the project documentation viewer. No enrollment models, YAML, school-specific conditions,
subscription logic, core payment ledger or Stripe transport implementation changed in M6.
The existing checkout cutoff became a named constant with the same value; camps reuse it.

Shared Bookings changes: capacity checks count camp occurrences alongside single-session
reservations; reservation source validation allows exactly one of workshop, party or camp;
the existing attendee form factory is reused through a capacity-based helper. Existing
workshops/parties retain their source records and payment behavior. Conditional date/roster
sections only appear for camp reservations. The new navigation is Bookings-only.

Additive migration `scheduling.0006` ran only against the dedicated test and preview databases.
No backfill, enrollment migration, production enablement, commit, push or deployment.
The migration test verifies an existing confirmed reservation, original quote, payment ledger
and School plan survive unchanged. Empty camp tables can be rolled back; after real camp
registrations exist, disable new intake and use a forward fix, not a destructive schema rollback.

## Verification

Final full regression run: 2,402 tests passed in 74.35 seconds. New camp forms/services/views
have 100% measured Python line and branch coverage. Scheduling/shared-payments combined
line/branch coverage is 99.93% across 2,371 statements and 534 branches. This is not a claim
of whole-application or template coverage. All six migration preservation tests pass;
the missing-migration check reports no changes. Django's existing duplicate `jazzmin`
template-library warning remains.

Automated coverage includes middle/first/last-date exhaustion, cross-camp and individual
workshop competition, PostgreSQL concurrent registration and exact duplicate submission,
hold expiry, late money, unknown checkout protection, signed deposit/balance events,
missed-event recovery, refunds, no partial writes on audit failure, stale prices/choices,
tenant/browser binding, CSRF, owner/viewer/scheduler permissions, invalid formsets and database
constraints. Existing enrollment/subscription and payment suites also run with external
network access blocked. No Google, Anthropic, Stripe or other paid API calls are permitted.

Manual browser checks completed:

1. Create Autumn Makers Camp through the owner form with three October 12-14 sessions,
   $150 per participant, $50 deposit and local test checkout.
2. Register Jordan Camp Demo with two attendees and different design/color choices.
   One $300 order and $100 deposit cover all dates.
3. Simulate the verified deposit through the existing local checkout. Confirm the whole
   camp, preserve a $200 balance, reload and verify after server restart.
4. Inspect the middle-day preparation roster: both participants, one of each selected
   design/color, no unpaid or canceled attendees included.
5. At a verified 390-pixel viewport, submit Jamie Cancel Demo with one attendee. Cancel
   it from the owner reservation screen. All-date availability returns from five to six;
   the canceled record remains visible. Jordan's confirmed registration is untouched.
6. Check mobile setup and registration text wrapping, controls and loaded reference imagery.
   Commercial fields and session membership are disabled after registration; closing intake
   remains available without canceling existing bookings.

Evidence: `/tmp/pontora-m6-preparation-desktop.png`,
`/tmp/pontora-m6-registration-desktop.png`, `/tmp/pontora-m6-registration-mobile.png`,
`/tmp/pontora-m6-setup-mobile.png` and `/tmp/pontora-m6-camp-desktop.png`.
Full regression evidence: `/tmp/pontora-m6-final.log` and `/tmp/pontora-m6-final-coverage.json`.

## Critical Review

- First implementation allowed paid camp intake inside the existing checkout cutoff.
  Corrected: online intake closes 40 minutes before the first session, matching checkout.
- Preparation originally had only a pending-request total. Added a distinct confirmed-camp
  roster and selection totals; an empty request list no longer obscures a camp-only roster.
- All inventory writers retain the existing tenant lock. Fixed-date claims, rather than a
  first-to-last continuous span, prevent incorrectly blocking a room overnight.
- Prices/payment method/date membership freeze after the first registration. Session time
  and room snapshots are preserved; capacity cannot shrink below active claims. A reserved
  camp session cannot be unpublished. Closing camp intake does not release reservations.
- No second payment module, provider calls on registration GET, or changes to production
  account settings. Stripe responses are simulated locally, not evidence of real sandbox readiness.

## Limits and Gates

This is fixed-cohort registration, not flexible drop-ins, late joining, individual-date
cancellation, automatic prorating, attendance tracking or recurring tuition. Changes to dates
after registration require a new camp and explicit cancellation/refund handling, not an
unreviewed bulk reschedule. A camp currently uses one workshop/location, with optional rooms
per session. USD only. Existing online balance checkout closes before the first session;
post-start balance collection remains outside this slice.

The camp policy, dates and prices are fictional; Jasmine has not approved production rules.
Real merchant sandbox verification, external recovery scheduling, reconciliation operations,
customer notifications/receipt delivery and production approval remain required before a paid
pilot. Browser receipts are tied to the submitting browser; staff can use the existing payment
link, but automatic camp emails are not implemented. M4 Wix/Notion and M8 recurring billing
remain separate milestones. No unanswered product question blocks local M6 testing.

## Try It Locally

[Camp administration](http://127.0.0.1:8002/bookings/workshop-demo/camps/1/).
[Customer registration](http://127.0.0.1:8002/bookings/workshop-demo/camps/1/register/).
Owner: `bookings-owner`, password: `admin`, isolated local preview only.
The expandable M6 row in the [status overview](http://127.0.0.1:8003/) contains the handoff steps.
