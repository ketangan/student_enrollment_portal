# M7c2: Public Trials and Family Booking

Updated October 4, 2026. Local booking slice implemented; M7c2 remains in progress overall.
No commit, push, production activation or external provider calls.

## Implemented and Impact

- Owner-managed, separately published public-trial and existing-family offers. Each binds a
  service, teacher, location, optional room, price, booking notice, horizon and start-time grid.
- Public contact/student form and explicit guardian authorization. A matching email never
  joins an existing household or grants access to its children. Guests have a browser-bound receipt.
- Family booking requires a new explicit permission on an existing scoped access grant.
  Old grants remain read-only; no additional child or future sibling is automatically authorized.
  Staff previews are read-only and do not acquire family write access.
- A bounded day's availability reuses the staff planner, buffers/travel rules, closures and
  teacher/student/room conflicts, including workshop/camp/party room claims. Candidates are
  calculated from a single bounded conflict snapshot (test budget: at most eight queries).
- Signed 30-minute selections bind tenant, offer, setup revision, student, grant, browser and
  idempotency key. POST rechecks permissions, publication, active staff/service, calendar and
  notice under the same tenant lock used by staff scheduling. Competing requests cannot book
  the same teacher/student/room; retries return the original booking.
- Free lessons create no money records. Priced lessons create an offline test quote in the
  existing `core.payments` ledger. Authorized staff record fictional received payments/refunds.
  Cancellation retains the money history, hides new collection, and prompts refund review.
  Existing Stripe/merchant/ledger implementation is reused, not duplicated or edited here.
- Booking, staff rescheduling and cancellation save durable confirmation previews in the
  same transaction. The UI explicitly says **Saved, not sent**. Internal staff notes and
  cancellation reasons are excluded from customer-facing previews.
- Make-up eligibility is explicit per offer, default false and snapshotted at booking.
  Later offer changes do not rewrite existing entitlements. The shared studio cancellation
  policy still applies to opted-in bookings. Existing staff-assigned lessons and restoration
  of a previously redeemed make-up credit retain their existing behavior.

Changed runtime: new Bookings offer/form/service/view modules and templates; existing Bookings
planner, access grant, cancellation/rescheduling and family views. No shared enrollment,
school YAML, SaaS billing, login, payment service or global settings changes in this slice.
The local tracker gains this report. Earlier unrelated working-tree edits are preserved.

Migrations 0011 and 0012 add offer/booking/confirmation tables, default-off family permission
and default-off offer credit eligibility. No automatic school/profile/offer/access backfills.
Applied only to `pontora_bookings_m1a_preview`. Main-local and deployed databases are untouched.
New customer booking routes require both DEBUG and `BOOKINGS_LESSON_BOOKING_PREVIEW=True`;
the latter is set only in `/tmp/pontora_m5b_ui_settings.py`, not production configuration.

## Critical Review and Corrections

1. A day of slots must not query the database for every candidate. Added a batched calendar
   shared with the staff conflict planner, with bounded date span and historical buffer checks.
2. A public email is not proof of household ownership. Guest records stay separate. Family
   writes require explicit permission and revalidate exact student/grant scope on every POST.
3. Mobile testing found a fieldset intrinsic minimum width overflowing the page. Reset its
   minimum inline size and verified the date/slot/contact controls at 390 pixels.
4. Canceled bookings must not appear payable or silently refunded. Retain the shared ledger,
   hide outstanding collection, permit explicit refunds only and preserve idempotent retries.
5. Manual trial cancellation exposed implicit make-up eligibility. Added explicit default-off
   per-offer eligibility and snapshot preservation. No change to ordinary staff lesson policies.
6. Currency formatting, repeated room/location labels and misleading generic lesson billing
   copy were corrected. Booking history uses saved offer/price snapshots, not later edits.

## Verification

Focused booking, policy, credit, view and migration regression: **143 passed**, 47.81 seconds.
Final full regression: **2,747 passed**, two existing warnings, **124.86 seconds**. The four
new booking runtime modules cover **402/402 Python statements and 80/80 branches (100%)**.
Scheduling/shared-payments runtime excluding migrations: 4,112/4,113 statements and
908/910 branches, **99.94% combined**. Wider measured scope including legacy billing/login
and the tracker: **98.76%**. This does not certify the unfinished rollout work.
Tests block non-local sockets and use a disposable PostgreSQL database, separate from the
preview. No paid APIs, email/SMS, Stripe, Wix or Notion calls.

Coverage is measured on Python statements and branches, not CSS/templates. Tests cover
teacher/student/room races, staff-vs-public contention, idempotency, audit/order/notice failure
rollback, signed quote tampering/staleness, permission changes, sibling/tenant isolation,
matching email separation, closures, DST gaps/folds, query budget, money/refund limits,
unpublished/inactive/gate checks, CSRF/rate limiting and additive migration preservation/reversal.

Browser checks on the isolated preview:

- Owner created the public/family $30 piano offer; public fictional Taylor Trial Demo booked.
  Existing-family Riley booked without re-entering contact details or duplicating the household.
  Reloading availability excluded occupied slots and teacher buffers.
- Recorded $30 received, rescheduled Taylor from October 6 to October 7, then canceled.
  Cancellation preview correctly denied a make-up credit; history showed all three unsent
  confirmations and retained the $30. Explicit test refund then showed received $30/refunded
  $30/no money held. No financial provider was contacted.
- Family access remained Riley-only, without Jamie; original make-up credit remained available.
  Default/read-only grants are unchanged. Receipt and family session survived server refresh.
- Desktop public page (1280px) and both public/family forms (390px mobile) had matching
  viewport/document widths, loaded logo and legible controls. Currency and room labels fixed.
- Preview readback: database `pontora_bookings_m1a_preview`; zero SBMC/Kid Works Bookings
  profiles. Main-local/deployed databases untouched. Migration check reported no changes.
- Manual SBMC public enrollment entry still rendered its existing registration/preferences
  and Studio billing copy; the new SBMC public-trial route returned access unavailable.
  This is an entry-point smoke check, not a claim of a new end-to-end production enrollment.
- Browser tracker opens the M7c2 row with manual steps/credentials and the new verification
  route, including completed local work and unfinished engineering. Mobile override reset.

Local evidence (temporary, uncommitted files):

- `/tmp/pontora-m7c2-final-tests.log`
- `/tmp/pontora-m7c2-final-coverage.json`
- `/tmp/pontora-m7c2-final.coverage`
- `/tmp/pontora-m7c2-booking-desktop.png`
- `/tmp/pontora-m7c2-booking-mobile.png`
- `/tmp/pontora-m7c2-family-mobile.png`
- `/tmp/pontora-m7c2-cancellation.png`
- `/tmp/pontora-m7c2-refund.png`

Final regression command (local settings/network blocker are temporary, not a portable CI gate):

```sh
env PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings \
  COVERAGE_FILE=/tmp/pontora-m7c2-final.coverage venv/bin/python -m pytest \
  -p pontora_local_only_tests --maxfail=5 --cov=scheduling --cov=core.payments \
  --cov=core.services.billing_stripe --cov=core.views_login --cov=scripts.project_status \
  --cov-branch --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing \
  --cov-report=json:/tmp/pontora-m7c2-final-coverage.json --tb=short --show-capture=no
```

## Remaining M7c2 Work

These are unfinished engineering requirements, not merely missing configuration:

1. Verified guest/family re-entry and notification delivery with outbox retries, failure
   visibility, suppression and no duplicates. Use the existing notification provider layer.
2. Online paid-trial checkout/settlement through the shared payment engine, with explicit
   inventory hold expiry, late payment and refund handling. No new Stripe implementation.
3. Confirm customer self-service policies before exposing family cancel/reschedule or credit
   redemption. Staff handling works now; family self-service does not.
4. Replace the deliberately local-only launch guard with reviewed tenant capability controls,
   then validate actual sandbox delivery/payment and operational deployment gates.

Recurring tuition remains M8. Emily's policies, production merchant access, Wix/Notion and
the broader A3-A6 rollout findings remain unapproved/unimplemented as tracked separately.
This build is useful to demonstrate and test, but is not ready to give real families.

## Local Manual Testing

Use [the expandable M7c2 tracker row](http://127.0.0.1:8003/) for current walkthrough steps.
Owner: `bookings-owner` / `admin`; viewer: `bookings-viewer` / `admin`.
[Offers](http://127.0.0.1:8002/bookings/music-demo/lessons/booking-offers/),
[Public trials](http://127.0.0.1:8002/bookings/music-demo/trials/),
[Bookings](http://127.0.0.1:8002/bookings/music-demo/lessons/bookings/).
Demo records are fictional and isolated; do not reuse these passwords for deployed users.
