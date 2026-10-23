# Square Booking Payments

## Hosted Release (October 23, 2026)

PR #7 is merged to main as `d2674df729f846099d950f06ac924330515a403c`.
GitHub CI passed: 4,569 Python tests, three skipped, 98.56% combined coverage;
83 JavaScript tests passed. Earlier local-only notes below are historical.

- Hosted Sandbox payment and full refund settled through real Square webhooks.
  Duplicate delivery did not duplicate the ledger; invalid signatures and
  test signatures sent to the live endpoint were rejected. The synthetic
  reservation was canceled and fully refunded; no real funds were involved.
- Existing five-minute Render job now runs
  `process_booking_maintenance --limit 20`: party email, Square renewal and
  Square-only Sandbox payment recovery. A hosted run completed successfully.
  Actual hosted token renewal was subsequently verified on the connected Default
  Test Account: only that demo connection was made due, the scheduled worker
  reported one checked / zero needing attention, and its revision, checked time,
  expiry and next-check time confirmed successful renewal.
- Sandbox callback now targets
  `https://demo.mypontora.com/bookings/square/oauth/callback/`.
  Hosted authorization completed after opening the Sandbox seller dashboard and
  the user approving access. Pasadena is mapped to Default Test Account; other
  demo locations remain unmapped. Refresh and save were verified through the UI.
  The earlier blank Square authorization page cleared with a seller test session.
- Web startup changed from `bash -lc` to `bash -c`, preserving migration,
  ensure-superuser and Gunicorn commands. The login shell caused credential-vault
  validation to fail; the hosted Connect screen passed after this correction.
- Live charging remains disabled. School payment-provider selection was not
  changed; SBMC subscription settings matched their pre-release checksum.
- Sentry's October 23 18:31:59 UTC `Location.timezone` AttributeError came from
  the temporary verification script, not a customer request. It occurred before
  payment submission, inside a rolled-back transaction. The script was corrected
  to `timezone_name`, then the hosted acceptance check passed.
- Test-performance work is diagnosis only, not an implemented speedup. See the
  timing investigation below; migration coverage must not be bypassed.

## Live Connection Work (October 22, 2026)

Local implementation is in progress and live charging remains disabled. The latest
increment below adds environment-bound production transport, ledger and webhooks
behind an explicit activation gate. Historical increments below describe their
state at the time; they are not evidence of production acceptance.

First local increment: `core/payments/square_oauth.py` implements explicit
test/live authorization URLs, code exchange, token introspection, merchant-bound
refresh and revocation. It reuses the existing no-redirect policy, bounds response
size, hides credentials from object representations/debug pages, and does not retry
uncertain requests. Live checkout remains disabled. Verification of the client: 45
client tests, 100% statement/branch coverage, and 219 combined Square/checkout
regressions passed. Provider responses were mocked; this is not a real OAuth
acceptance test. This work is separate from release PR #6.

Second local increment: the existing booking Settings > Integrations page now
includes Square connection status and Connect, Check connection, Reconnect and
Disconnect actions. Only school-wide owners and superadmins can use them. The
selected payment provider is not changed by connecting an account. Credentials
use the existing authenticated vault; authorization state is single-use and bound
to the school, environment, app, user and browser session. Revision checks and
short operation leases prevent stale updates from overwriting newer actions.

The additive migration 0038 creates seller authorization records without modifying
payment accounts or existing orders. The callback is
`/bookings/square/oauth/callback/`. Configure the exact site callback and application
secret separately with `PAYMENTS_SQUARE_TEST_OAUTH_REDIRECT_URI` and
`PAYMENTS_SQUARE_TEST_APPLICATION_SECRET` (and corresponding `LIVE` settings).
`BOOKINGS_INTEGRATION_KEYS` must be configured before connection is offered.
Never put these secrets in school YAML, source control or browser fields.

`refresh_square_connections --limit 20` renews due authorizations and retries
pending disconnects, with a nonzero exit status when attention is needed. Successful
renewals are due again the following day; failed operations retry after an hour.
The job is implemented but **not scheduled on Render yet**. Schedule frequent,
bounded runs and monitor failures before enabling live payments. Expired or overdue
connections are visibly distinguished from current connections.

Real sandbox OAuth acceptance is recorded below. Hosted scheduling and live
payment transport remain pending. Automated lifecycle tests mock
provider responses and are separate from the real-provider walkthrough.

Verification (October 23): 4,502 Python tests passed and three skipped across an
interrupted run (3,791 passed) and an overlapping continuation (721 passed).
The run was split after the transaction-test group slowed down; this was not one
uninterrupted full-suite pass. All 83 JavaScript tests passed. New lifecycle module
coverage is 98.35% combined, with every new module above 90%. The concurrent callback
test verifies only one code exchange while the first provider response is pending.
Migration drift and whitespace checks passed. The existing localhost:8002 database
was reused. The unconfigured owner settings page was visually checked at desktop
and mobile widths; connected/error controls have automated rendering coverage,
with the real-provider walkthrough subsequently completed below.

### Real Sandbox OAuth Verification (October 23)

Used the existing Pontora Square application and Default Test Account. Configured
the Sandbox callback as `http://localhost:8002/bookings/square/oauth/callback/`.
The Sandbox application secret and vault configuration use the existing ignored
local `.env`; no production credentials or Render settings were changed. The
existing preview settings load only these Sandbox values and retain the network
guard. No new server port, database or seller account was created.

Verified in the browser: denial returns without connecting, successful consent
connects the expected seller, Check connection renews the real token, and Reconnect
returns to the same seller. Marked only this local connection due and ran the
renewal command: one connection checked, zero failures, next check advanced. A stale
browser submission was rejected after that renewal. Disconnect succeeded against
Square and cleared the local credential ciphertext and expiry. The original
Sandbox payment credential passed a read-only location check before and after
revocation. No payment, refund, provider switch or production action was performed.

The revocation test initially left OAuth disconnected. The subsequent mapping
verification below reconnected the same Sandbox seller with explicit approval.
Real production acceptance is still outstanding.

### Location Mapping and Connected Sandbox Payments (October 23)

Settings > Integrations now contains a compact account-status section and a shared
location-mapping table. Refresh reads eligible active US/USD card-processing
locations using the seller's existing OAuth permissions. Save revalidates against
Square outside database locks, then checks permissions, environment and revision
under the profile lock. Unknown, foreign, omitted and stale location submissions
cannot partially change mappings. No remote request runs on the settings GET.

Mappings select immutable PaymentAccount bindings for new workshops, parties and
camps. Remapping does not rebind old orders; balances, refunds and recovery use
the original account/location. Connected accounts use encrypted seller tokens,
never a fallback platform token. Disconnected, expired and unmapped locations fail
closed. The existing superadmin provider selector supports these bindings;
connecting or mapping does not itself change the provider. Stripe's uniqueness
and existing shared-sandbox behavior remain intact. Payment constraints still
reject live accounts; this increment does not enable production charges.

Local migrations payments 0009 and scheduling 0039 were applied to the existing
preview database. Real Sandbox consent, location refresh and mapping save were
verified. All five local Plushie locations map to the one available Default Test
Account location for testing only. Mobile unmap/save/remap was verified; desktop
and mobile screenshots were inspected. No real or Sandbox payment was submitted
in this walkthrough. Payment/refund acceptance in this increment uses mocked
provider responses, including deposits, balances after remapping, decline/retry,
uncertain-payment recovery, webhook replay and provider-switch refunds.

Verification: 207 focused tests and 657 broader regressions passed before the
final additional permission/balance cases. The expanded mapping suite then passed
all 32 tests (overlapping the focused run). Production mapping/transport/webhooks, hosted renewal
scheduling and Jasmine-owned live acceptance remain launch gates.

### Payment Acceptance and Live Environment Support (October 23)

Local, uncommitted work. The existing shared checkout, ledger, refund, recovery and
mapping paths now support connected Square live accounts. Live Stripe booking
accounts are still unsupported by this control; existing Stripe enrollment,
subscription and Sandbox booking behavior is unchanged. No live account was
connected, no live charge made, no deployment or Render configuration changed.

Production configuration is explicit and separate:

- Existing `PAYMENTS_SQUARE_LIVE_APPLICATION_ID`, `..._APPLICATION_SECRET` and
  `..._OAUTH_REDIRECT_URI` configure seller authorization.
- `PAYMENTS_SQUARE_LIVE_WEBHOOK_SECRET` and `..._WEBHOOK_URL` configure the exact
  registered HTTPS endpoint `/bookings/square/live/webhook/`. Subscribe to payment
  and refund created/updated events. Sandbox retains `/bookings/square/test/webhook/`
  and its own signature key and URL. Event payloads cannot choose the environment.
- `PAYMENTS_SQUARE_LIVE_ENABLED` defaults to false. It gates new charges even under
  DEBUG. Live checkout also requires the live webhook configuration, a current
  encrypted seller connection and an enabled mapped account. No live platform-token
  fallback exists. This setting was **not enabled** during this work.
- Owners may prepare location mappings before activation. A superadmin must still
  choose Square in the school's existing Booking Payments control. Mapping does
  not switch providers. The section now labels its actual Sandbox/Live environment.
- Disabling new charges does not block reading existing payments, processing
  verified webhooks or refunding existing payments. Original order bindings remain
  authoritative after location/provider changes.

Migration payments 0010 permits only connected Square accounts in live mode;
live Stripe/shared-demo accounts remain rejected by database constraints. Existing
records are not rewritten. Applied locally to the existing preview database only.
Do not reverse this constraint migration after live records exist; use a reviewed
forward fix and disable new charges instead.

Real provider verification used only the existing Default Test Account, encrypted
OAuth credentials, localhost:8002, the existing preview DB and synthetic contacts.
Email stayed in memory. Results:

- A fresh browser-created workshop booking collected $65 through Square's Sandbox
  card SDK and returned to the confirmed customer page, with one ledger payment.
- A separate synthetic reservation collected a $25 deposit and $40 balance through
  the actual Sandbox API. Deliberately discarding the successful balance response
  left the attempt unknown. A repeat submission was blocked before transport.
  The first immediate lookup did not yet find the payment; a later read recovered
  it. Exactly two payment entries totaled $65.
- Both payments were refunded after cancellation. The $25 refund progressed from
  pending to succeeded. The $40 request initially remained unknown with no refund
  found; an operator reissued **the original idempotency key and amount**, then
  verified completion. This was manual intervention, not automatic recovery.
  Final ledger: $65 paid, $65 refunded, $0 retained. Unknown refunds without a
  discoverable provider object still require operator investigation; never create
  a replacement refund with a new key. Do not infer why the original request
  failed: its provider error code was not retained.
- A real SDK decline using Square's documented declined card produced the existing
  clean retry screen. Try another card reopened the secure form. The unused retry
  checkout was closed through the admin screen, releasing its hold.
- Desktop confirmation/admin totals and mobile card/retry screens were inspected.
  The mobile viewport had no horizontal overflow. Back navigation was checked on
  the browser-created flow and the standard signed reservation-payment page.

Final automated regression pass: **791 passed in 49.06 seconds**, including 15 live
environment tests. Production responses are mocked, not actual live transactions.
Coverage includes live deposit/balance/refund routing, SDK selection, default-off
activation, mapping before activation, cross-environment signatures/evidence,
duplicate events, recovery while intake is disabled, original account bindings,
legacy shared-Sandbox webhook routing, and Stripe/subscription regressions.

Still required before production activation: hosted webhook delivery acceptance,
scheduled OAuth renewal with failure monitoring, Jasmine's live authorization and
location verification, explicitly approved small live payment/refund acceptance,
and the recovery deployment checks below. Nothing here marks L01 launched.

### Refund Recovery Follow-Up (October 23)

- Reservation recovery now offers **Retry refund** only for an unknown Square
  refund without a saved provider ID. It replays the original approved amount,
  payment, reason and refund UUID as the idempotency key. It does not approve a
  replacement refund. Known refunds remain status-check only, and Stripe is
  unchanged. Existing permission checks, operation leases and the 30-second
  cooldown apply. Automatic recovery does not resubmit uncertain refunds.
- Operator flow: open the reservation, use Retry refund once, then Check refund
  status after Square starts processing. If verification continues to fail,
  inspect the original payment in Square; do not create a replacement request or
  change its key. A pending refund is not recorded as refunded until verified.
- Real Sandbox browser verification recovered an intentionally interrupted $65
  refund on the existing synthetic booking. Square returned pending, then
  completed; the ledger contains exactly one $65 refund and $0 retained. Email
  stayed in memory. The existing server and local preview database were reused.
- Follow-up regression: **465 tests passed in 32.37 seconds**, including lost
  responses before/after acceptance, identical retry payloads, repeat clicks,
  known-refund read-only recovery, access/CSRF checks, OAuth/location mapping,
  live environment selection and Stripe/subscription regressions. Live responses
  remain mocked; no real-money transaction was performed.
- `reconcile_booking_payments --environment test --limit 20` retains the previous
  default. `--environment live` explicitly selects connected Square booking
  records; it does not enable checkout and continues recovery when intake is
  disabled. Keep test and live jobs separate. Neither command was scheduled on
  Render in this follow-up. Schedule `refresh_square_connections --limit 20` and
  the appropriate recovery jobs after deployment, with failure monitoring, then
  verify hosted webhook delivery before enabling live charging.
- Retry behavior follows Square's [idempotency contract](https://developer.squareup.com/docs/build-basics/common-api-patterns/idempotency)
  and [Refund Payment API](https://developer.squareup.com/reference/square/refunds-api/refund-payment).

### Test Timing Investigation (October 23)

Profiled migration, pilot and reservation tests: 125 passed in 459.88 seconds under
cProfile (profiling adds overhead). Of 461.45 seconds profiled, 445.28 seconds were
inside 58 migration executions; historical model reload/render work dominated,
including 613,273 model renders. Individual historical migration tests took
approximately 20-42 seconds under profiling. This reproduces the slowdown outside
the full suite and identifies repeated schema/model-state rebuilding, rather than
Square network waits, as the main cost in this measured group.

Comparison without migration tests or profiler: 189 booking/Square tests passed in
8.85 seconds; the slowest individual call was 0.37 seconds.

Do not disable migration assertions or raise timeouts to hide this. Follow-up:
reduce redundant migration transitions with shared migration-test setup where
isolation can be preserved, and report migration timings separately from regular
behavior tests. Retain full migration coverage in release checks. No performance
refactor or change to test assertions was made during this verification.

Implementation sequence:

1. Add Square's server-side OAuth connection lifecycle: separate test/live app
   configuration, single-use school/user/environment-bound authorization state,
   encrypted seller credentials, refresh, revocation and visible connection status.
   Reuse the existing authenticated credential vault rather than introduce custom
   encryption. Do not expose tokens to browsers, audit logs or exception pages.
2. Map each Pontora studio to an active card-capable Square location belonging to
   the connected seller. Keep merchant, location and environment immutable on each
   payment order so later setup changes cannot redirect balances or refunds.
3. Extend the existing booking payment transport, ledger, signed webhook and
   recovery paths for live Square. Keep the Stripe enrollment/subscription paths
   unchanged; preserve the superadmin-only provider switch. Demo orders must never
   use live credentials, even when the service contains both configurations.
4. Verify sandbox authorization/denial/replay, refresh/revocation, cross-school and
   cross-environment rejection, multi-location routing, deposit/full/balance flows,
   duplicate events, uncertain payments, refunds and provider-switch recovery.
   Only then configure production and perform explicitly approved live acceptance.

No extra Terminal API integration is required for launch. Terminal collection and
staff recording remain separate from online payment verification. Actual Terminal
refunds must not be confused with correcting an incorrectly recorded receipt.

Workshop visit checklist:

- Confirm the Square account owner who can authorize Pontora and the exact Square
  location corresponding to every launch studio. No passwords or tokens by email.
- Observe staff collecting a remaining balance and an extra purchase, finding its
  receipt in Square, and recording it against the correct Pontora reservation.
- Check how staff apply tax, tips (if any) and refunds, and distinguish the existing
  online deposit from money taken on the Terminal to avoid collecting it twice.
- Obtain redacted example receipts if useful; omit card details and customer data.
- Reserve an explicitly approved live payment/refund walkthrough for after the
  connection is ready. Do not take a real payment merely to test the local form.

References: [Square OAuth](https://developer.squareup.com/docs/oauth-api/overview),
[permissions](https://developer.squareup.com/docs/oauth-api/square-permissions),
[authorization state and redirects](https://developer.squareup.com/docs/oauth-api/create-urls-for-square-authorization),
[token lifecycle](https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope).

## Status

October 16, 2026: code deployed to the shared Render service. Hosted Square sandbox
credentials/webhook activation remain pending; no hosted school was switched to
Square. Live Square merchant connection and production acceptance remain required
before Plushie's go-live. Historical local test notes below retain their dates.

**October 17 customer decision:** Jasmine confirms one Square seller account for
all locations and existing Square Terminal devices. For launch, staff will take
remaining balances on those devices and manually record them against the Pontora
reservation. This manual-recording workflow is now **implemented locally, not
deployed**; it is separate from the online Collect balance action below. Automatic
device integration remains deferred.
Party deposits are typically $150 for six guests and $300 for twelve. These are
party-package rules, not new workshop defaults. See the [updated launch checklist](scheduling-implementation-tracker.md#jasmine-response-and-launch-checklist-october-17).

This adds Square to booking payments, not enrollment application fees or Pontora's
subscription billing. Those Stripe integrations remain unchanged. There are no
client-name checks in the provider selection or payment code.

## Superadmin Flow

1. Open Ops > Schools > the demo school > Booking Payments.
2. Select Stripe or Square and click **Verify & save**.
3. New bookings use that provider. Existing reservations, balance payments and
   refunds keep the provider/account originally attached to their order.

Only an active superadmin can change this setting. An unavailable account, changed
account binding, stale selection or live-school request is rejected. Switching
does not change subscription prices, features, trial dates or enrollment keys.
Stripe remains the migration default for every existing scheduling profile.

For local review, use http://localhost:8002/ops/schools/plushumi-demo/#booking-payments
and the existing local administrator account. Reuse the established Plushie preview
database, `pontora_bookings_m1a_preview`, and its existing local settings harness.
Do not create another server or manual-review database for payment testing.
The temporary Square server on 8016 and database `test_pontora_square_sandbox`
were removed on October 15. Stale review servers on 8005 and 8015 were stopped;
the older billing sandbox database is preserved, not deleted. Automated tests
continue to use their separate existing `test_pontora_local` database.

### Local Plushie Test Steps

Square is selected for new bookings in the local Plushie demo. **Settings >
Billing** manages the school's subscription to Pontora; it is not where customer
Square payments are configured. No subscription payment or trial change is needed
to test customer payments.

1. Open http://localhost:8002/bookings/plushumi-demo/experience/.
2. Choose Pasadena, an available session, and the participant's design and color.
3. Review the booking, select **Continue to checkout**, then **Continue to
   Square Checkout**. Use the sandbox cards listed below, never a real card.
4. After success, confirm the booking in **Reservations** and check its received
   amount and audit log. For a decline, no payment should be recorded; retry with
   the success card.

Changing the school provider does not convert existing Stripe reservations to
Square. Start a new booking when testing a newly selected provider.

## Manual Terminal Payment Recording (October 17)

Local only. Reuses localhost:8002 and `pontora_bookings_m1a_preview`. The test
reservation **Square payment retest** retains its real sandbox $25 online deposit
and has $40 outstanding on a $65 total. The local QA Terminal entries were
voided with reasons, leaving the original balance and transparent audit history.

1. Log in as the existing local `plushie_admin` account. Open
   http://localhost:8002/bookings/plushumi-demo/reservations/96ecfb0d-e68f-4535-86a0-8a33bc4cf73d/
   or find **Square payment retest** under Reservations.
2. Under Payment summary, click **Record payment already received**. In the dialog,
   enter 10.00 and today's payment date. Receipt number is optional; Notes has been
   removed. Check the collected-payment confirmation and click **Save payment record**.
   This is local test data: do not take a real card payment to test this form.
3. Expect Received $35, Balance $30, Refunded $0, a Staff-recorded row, and an audit
   entry with the user/date and receipt number if supplied. The reservation remains confirmed.
4. Enter $45 against the $30 balance: the form previews $15 in extra purchases
   automatically, without an extra checkbox. Cancel this preview to continue the
   balance-only walkthrough. A future payment date or duplicate receipt number
   must still fail without changing the balance.
5. Enter the remaining $30. Expect Received $65 and zero
   Balance. Recording remains available for later extra purchases; the customer
   payment page shows Paid in full without offering another online payment.
6. Click **Correct entry** beside the $30 receipt, enter a reason in the dialog and confirm it is
   an incorrect record, not a refund. Void it. Expect Received $35, Balance $30 and
   Refunded $0. Both the original receipt and correction stay visible. Void the $10
   test receipt too to restore the starting $40 balance for another walkthrough.

Permissions: existing scheduling **Owner** and superadmin can record/correct.
Scheduler and Viewer remain read-only for money. Membership is currently scoped to
the school, not individually assigned studios; no new location permission system
was added. This supports an online Stripe OR Square deposit followed by a manually
recorded Square Terminal balance. It never switches the order's online provider.

Online checkout already open or uncertain? Use the contextual check/close controls
above the form first. The redundant Payment & refund status section is removed;
recovery controls appear only for unresolved operations. An active provider refund blocks recording
and corrections. Do not take a second payment while an online charge is unresolved.

Operational limits: receipts are **staff-recorded, not API-verified**. The form
neither charges a Terminal nor sends a refund or automatic customer receipt. Real
Terminal refunds stay in Square and their Pontora recording workflow is still open;
do not use Correct entry to represent a real refund. Obtain one redacted receipt
example from Jasmine before staff acceptance to confirm the optional number they can use.
Without a receipt number, a separately entered duplicate of the same real-world
payment cannot be identified automatically. Submission retry and stale-total
safeguards still apply. The receipt settles the outstanding balance first; only
the excess is recorded as extra purchases. This does not track individual items,
deduct inventory, calculate tax, or support buying extras while deliberately leaving
an existing balance unpaid.

Migration: `payments.0006` adds receipt details and append-only correction links;
applied locally only. Provider charging/webhook code, enrollment and subscription
billing are unchanged. The shared totals function subtracts corrections separately
from actual refunds. The guarded local reset now preserves Terminal receipts.

October 19 simplification: `payments.0007` makes the receipt number optional without
rewriting existing records. Applied only to the existing local preview database.
Notes is removed from new payments; historical notes and correction reasons remain.
Focused payment/workspace/recovery/checkout regression: **201 passed**. Additional
Terminal/reservation/migration run: **144 passed** (overlaps the focused run).
Desktop and 390px mobile form checks passed; Terminal history scrolls without
crushing its columns. Browser
verification recorded and corrected a $10 payment without a receipt, restoring the
original Received $25 / Balance $40 / Refunded $0. Earlier test totals below refer
to the October 17 implementation, not this follow-up.

October 19 action clarification: **Record payment already received** opens the
Square Terminal recording dialog; **Take online payment** opens the existing
customer checkout for a new payment. No expandable rows remain in this payment area,
including receipt number and correction controls. Calendars inside a dialog stay
within it; other calendars retain their existing positioning. No payment service,
permission, total calculation or migration change in this UI follow-up.
Verification: **245 payment/reservation tests and 56 JavaScript tests passed**.
Manual desktop/mobile checks covered date selection, Cancel/Escape/focus return,
overpayment validation, online checkout navigation and a $1 local record followed
by correction. Original $25 received / $40 balance / $0 refunded restored.

October 19 extra purchases: `payments.0008` adds an extra-charge amount to the
Terminal ledger entry, defaulting to zero for existing records. The original booking
price remains unchanged; totals include active extra charges. Saving an amount above
the balance records the excess as extra purchases. The preview updates as the amount
changes; no extra-purchase checkbox is required. Fully paid confirmed/completed
reservations accept additional receipts.
Correcting an entry reverses its payment and extra charge together, without a refund.
Legitimate extras do not trigger excess-payment refund prompts; genuinely unexpected
overpayments still do. Applied only to the existing local database, not Render.
Manual desktop/mobile verification covered $55 received against a $40 balance,
a further $5 after full payment, customer totals, confirmation validation and both
corrections. The original $65 total / $25 received / $40 balance was restored.
Verification for this update: full regression **4,149 passed, 3 skipped, 2 warnings**;
after adding the party extra-purchase case, the final Terminal suite passed **83 tests**.
All **57 JavaScript tests** passed their coverage gates. Migration drift and
whitespace checks passed. No Stripe provider, enrollment or subscription code was
changed by the extra-purchase update. Existing Stripe/Square booking flows are
covered by the regression run; no live payment was taken.

Manual browser verification: real existing sandbox deposit plus simulated $10/$30
Terminal receipts; duplicate-reference rejection, full settlement, both corrections,
audit trail, and 1280px desktop/390px mobile form layout without page overflow.
No Square Terminal hardware or live transaction was used. The balance and correction
history also survived restarting the same localhost:8002 server.

Verification: full regression **4,129 passed, 3 optional browser tests skipped, 2
existing warnings**. After the final voided-retry guard and additional concurrency/
environment tests, the focused final run passed **266 tests**. All **53 JavaScript
tests** passed. The new Terminal module has **100% statement/branch coverage**;
every measured module passes the 90% coverage gate. Migration drift and whitespace
checks are clean. Tests cover workshop/party balances, Stripe/Square deposits,
receipt/submission duplicates, competing writes and online checkout, invalid/stale
data, permissions/CSRF/demo-host isolation, pending payments/refunds, corrections,
customer balances and preservation of existing Stripe records through migration.

## Checkout and Recovery

### Deposits and Balance Collection (October 16)

**Status: deployed to the agreed scope, pending Jasmine's final inputs.**
Ketan accepted the customer success/decline behavior. Remaining
customer decisions are the actual deposit/policy settings and final customer
journey. Hosted/live Square release gates below remain open; this does not mark
Square device reconciliation or automated emailed payment links complete.

- Workshop setup now offers **Payment at booking: Full payment / Deposit**.
  A workshop deposit is a fixed USD amount **per participant**. Full payment
  remains the default for existing workshops.
- Party review uses the same choice with its existing quote fields. Its deposit
  is **per reservation**, not per guest. Zero means full payment, not free booking.
- Customer review shows the total and a concise deposit/full-payment explanation
  with the balance due date. Confirming a deposit does not mark the reservation
  fully paid.
- When a deposit is available, online customers choose **Pay deposit** or
  **Pay in full** before entering card details. This uses the same choice control
  in the workshop experience and the shared reservation payment page (including
  parties). The server calculates both amounts from the saved order, never from
  a submitted price. Full payment leaves no balance; the configured deposit stays
  unchanged for reporting and later bookings.
- An active checkout retains its amount and is resumed, not replaced by a
  competing checkout. Confirmed declines let the customer retry, preserving a
  full-payment selection, or return to the reservation to choose again.
- Staff use **Collect balance** on the existing reservation. The customer enters
  their card for the outstanding amount; no automatic charge or device payment
  is initiated. Confirmed reservations can pay on the event day, while new
  bookings retain the existing checkout cutoff.
- Changing workshop settings does not change existing reservation amounts.
  The original payment provider/account remains attached to each reservation.
  Failed balance attempts leave the booking confirmed and its balance unchanged.
- Local Pasadena workshop 23 is configured for a $25-per-participant deposit
  against the existing $65 price. Other workshops were not reconfigured.
  Start at `http://localhost:8002/bookings/plushumi-demo/workshops/23/`, edit the
  payment setting, then use **Customer demo** to make a new Pasadena booking.
  Pay $25, open the reservation as staff, and collect the remaining $40.
- Manual Square SDK verification covered the $25 deposit, declined $40 balance,
  successful $40 retry and final $65 received/$0 balance. Desktop and 390px mobile
  review layouts were inspected. Automated coverage includes both Stripe and
  Square, workshop and party balances, event-day payment, duplicate protection,
  stale quotes, immutable amounts and existing cancellation/refund regression.
- October 16 regression: 1,993 backend tests and 53 JavaScript tests passed.
  Migration `scheduling.0025` adds the workshop deposit with a zero default and
  was applied only to the existing local preview database. No deployment occurred.
- Full-payment choice follow-up: 2,008 backend tests and 53 JavaScript tests passed.
  Manually chose $65 in full for the $25-deposit Pasadena workshop, verified a real
  Square sandbox decline, retried at $65 and confirmed Paid in full. Desktop and
  390px mobile payment choices were checked. This follow-up adds no migration.
- Customer-copy cleanup removed the redundant Deposit option row, repeated
  demo/test payment wording, test-card instructions and unsent email preview.
  The action is **Continue to checkout**. Payment choices, verification and
  sandbox safeguards are unchanged. Desktop/390px review layouts were checked;
  249 of 250 focused tests passed, followed by all 24 Stripe experience tests
  passing after correcting the final obsolete test-card-copy expectation.

- Square's Web Payments SDK owns the card fields. Pontora receives a one-use token,
  never card numbers or CVVs. Amount, currency, location and reservation identity
  come from the server, not posted browser values.
- A submitted marker and provider idempotency key prevent duplicate payment
  submissions. Confirmed declines allow a new attempt; uncertain responses do not.
- Customer **Check payment status** and the existing admin recovery controls read
  Square before changing financial records. An unknown result retains the hold;
  absence in a bounded provider search is not proof that no charge occurred.
- Signed payment/refund webhooks fetch the current provider object and validate
  account, reference, amount and status. Replays cannot create duplicate ledger
  entries. Existing payment-notification outbox and reservation audit are reused.
- Cancellation/refund uses the existing reservation controls. A pending refund
  is not displayed as completed until Square verifies completion.

## Sandbox Configuration

Use the existing `.env`, not a second project credentials file:

| Setting | Square source |
| --- | --- |
| `PAYMENTS_SQUARE_TEST_APPLICATION_ID` | Sandbox application ID |
| `PAYMENTS_SQUARE_TEST_ACCESS_TOKEN` | Sandbox access token; server only |
| `PAYMENTS_SQUARE_TEST_LOCATION_ID` | Active US/USD sandbox location |
| `PAYMENTS_SQUARE_TEST_WEBHOOK_SECRET` | Sandbox webhook signature key |
| `PAYMENTS_SQUARE_TEST_WEBHOOK_URL` | Exact HTTPS URL registered with Square |

The first three values are configured locally. Hosted webhook setup is still
pending. Before enabling a hosted demo, register the following endpoint on the
demo host and subscribe to `payment.created`, `payment.updated`, `refund.created`
and `refund.updated`:

`https://demo.mypontora.com/bookings/square/test/webhook/`

Hosted checkout requires webhook configuration. Local DEBUG preview allows manual
status verification without exposing localhost publicly. Real webhook delivery
has not been tested against Render; automated tests verify signatures, replay,
account isolation and settlement through the endpoint.

Square calls use only `connect.squareupsandbox.com`; no live-key fallback exists.
The existing Stripe booking sandbox and `STRIPE_MODE` are independent. No Render
settings, hosted tenant data or SBMC credentials were modified for these checks.

Successful sandbox card: `4111 1111 1111 1111`.
Declined sandbox card: `4000 0000 0000 0002`.
For either card use a future expiration (such as 12/30), CVV `111`, ZIP `94103`.
Do not enter a real card. The decline number was verified through the real Square
Web Payments SDK, not simulated by Pontora.

## Verification

- Full regression rerun: 4,010 passed. Follow-up payment regression: 236 passed,
  including the customer experience and immutable account controls added during
  self-review. Square transport, checkout and settings modules each exceed 90%
  measured statement/branch coverage. Two existing framework warnings remain.
- JavaScript suite: 52 passed, including SDK initialization failure, invalid card,
  duplicate submit, decline, pending and uncertain responses.
- Real browser SDK payment: $25 deposit on a $65 fictional reservation; confirmed,
  $25 received and $40 balance. Real sandbox decline and retry verified.
- Switched new bookings to Stripe, canceled the prior Square reservation and
  refunded its $25 through Square. Existing UI showed $25 refunded and $0 retained.
- Desktop and mobile checkout and provider controls inspected. The school audit
  table now scrolls within its container on narrow screens.
- Payment migrations also applied to the existing local Plushie preview database
  when consolidating back onto port 8002. No hosted migration was run.
- Payment return page now distinguishes the initial deposit, optional remaining
  balance, paid-in-full, canceled and review states. Staff see a permission-checked
  reservation link; anonymous customers get a public workshops link.
- October 15 UX follow-up: 350 backend checks and 4 checkout-script tests passed.
  Retested the actual SDK on port 8002: `4000...0002` declined, retrying with
  `4111...1111` received $25 and confirmed the reservation with a $40 balance.
  Staff return navigation and desktop/mobile layouts were checked; at 390px there
  was no horizontal overflow. Existing Stripe billing/notification tests passed.
- October 15 billing navigation follow-up: 471 billing/access regression checks
  and 89 Square/shared-sandbox checks passed. The existing billing views are reused
  under Bookings routes, preserving the booking menu and return destinations for
  owners and superadmins. Tests cover cross-school denial, enrollment-only access,
  overdue-subscription recovery and demo-host returns.
  Manually followed Settings > Billing as both account types, and completed the
  public Plushie booking flow with a real $65 Square sandbox payment. The customer
  confirmation and admin Recent Activity both showed the verified payment.

## Release and Live Launch Gates

1. **Complete October 16:** applied `payments.0005`, `scheduling.0024` and `scheduling.0025` during the approved deployment.
   They add provider/location bindings and submission state, preserve existing
   Stripe records, and change account uniqueness to school/environment/provider.
2. Configure the hosted sandbox webhook, prove delivery and retry with real Square
   events, and repeat demo/prod isolation checks before enabling hosted checkout.
3. Implement Jasmine-owned live Square OAuth, encrypted token storage/refresh and
   verified Square location mappings. The shared sandbox account is not a live
   merchant solution and must never be used to collect customers' real money.
4. Verify agreed deposit rules, live checkout, declines, refunds, reconciliation
   and notifications. Square enrollment application-fee support is not included
   in this booking-only slice.
5. **Updated October 17:** manual recording of independently collected Terminal
   balances is a launch requirement. Automatic device collection/matching and
   automated emailed balance links remain deferred. Staff-initiated online balance
   collection uses the existing reservation payment page, but does not satisfy the
   new manual-recording requirement. Never claim a Square device transaction
   automatically reconciles in Pontora.

No new background worker, subscription engine or separate refund workflow was
introduced. API failures deliberately require verification rather than guessing
that money did or did not move.

### October 16 Release Preflight

- Scope: configurable workshop/party deposits, customer full-payment choice,
  reservation balance collection, the previously tested Square sandbox provider,
  booking Billing navigation and requested customer-copy cleanup.
- Existing enrollment application-fee and Pontora subscription Stripe transport
  is unchanged. No SBMC prices, account settings, credentials or Stripe mode are
  changed by deployment. No hosted provider is automatically switched to Square.
- Render's existing main-branch deployment runs `migrate --noinput` before
  starting the web process. Required migrations are `payments.0005`,
  `scheduling.0024` and `scheduling.0025`; their defaults preserve Stripe and full
  payment. There are no data-rewrite operations or new runtime dependencies.
- A fresh Render-managed database export completed October 16 at 2:15 PM Pacific.
  The database has a three-day point-in-time recovery window and approximately
  10% storage usage. No production database was downloaded locally.
- Rollback precaution: retain the forward schema for a code rollback. Do not
  reverse `payments.0005` after creating Square accounts or ledger entries:
  the old uniqueness/source constraints cannot represent those records. Before
  reverting provider-aware code, assess any Square accounts/orders created since
  deployment. Hosted Square activation remains a separate gated step.
- Local manual checks reconfirmed booking Settings > Billing keeps the Bookings
  navigation and returns to its own Settings page. Workshop payment controls
  toggle correctly without modifying saved settings. SBMC's hosted dashboard and
  active Founder/Custom billing were captured as the pre-deployment baseline.
- The full release run caught an obsolete `Back to demo options` assertion after
  the approved wording change. It now asserts `Back to booking options` while
  retaining the original destination and single-header checks.
- Final local regression: 4,066 passed, three optional browser tests skipped;
  the additional upgrade-preservation test passed separately. All 53 JavaScript
  tests passed. Combined measured statement/branch coverage is 99.12%; every
  measured module exceeds 90%. Migration drift and whitespace checks passed.
  Two pre-existing framework warnings remain. Sandbox card journeys and manual
  browser checks are recorded above; automated tests do not contact live providers.

### October 16 Production Release Verification

- [PR #3](https://github.com/ketangan/student_enrollment_portal/pull/3) merged as
  `0280eb8f34827ec7b885d93fc03fe704f04beb60` after the full pre-merge
  [CI run](https://github.com/ketangan/student_enrollment_portal/actions/runs/38968602165)
  passed: **4,067 backend tests passed, three optional browser tests skipped,
  53 JavaScript tests passed**. Coverage gates passed. The added migration test
  proves historical Stripe accounts, paid ledger entries, checkout amounts and
  school plans survive the upgrade; existing workshop deposits default to zero.
- Render deployment `dep-db99jg3rjlhs73bgjlr0` became live at **2:42 PM Pacific**.
  Startup logs confirmed all three migrations completed successfully. No manual
  migration command is needed. Render settings, credentials and Stripe mode were
  not changed.
- Post-deployment browser snapshots of SBMC's active subscription page and public
  enrollment form matched their pre-deployment snapshots exactly. No real
  enrollment or payment was submitted during hosted smoke checks.
- Hosted Plushie Settings > Billing retains Bookings navigation. Workshop edit
  exposes Full payment / Deposit and the deposit amount control. The control was
  inspected without saving, preserving the hosted workshop's full-payment setup.
  Customer demo opens the two-option hub; both direct booking and the responsive
  modal load the real booking experience.
- Hosted Square checkout has **not** been activated or end-to-end tested. The
  real Square sandbox deposit, full-payment, decline/retry, balance and refund
  checks above were local. Hosted webhook configuration and live merchant OAuth
  remain explicit launch gates, not implied by this deployment.
- Existing CI checkout-cleanup warning about the unrelated nested Claude worktree
  is non-blocking. That worktree and its changes were left untouched.

## Square References

- [Web Payments SDK](https://developer.squareup.com/docs/web-payments/quickstart/add-sdk-to-web-client)
- [Sandbox payment testing](https://developer.squareup.com/docs/devtools/sandbox/payments)
- [Create Payment](https://developer.squareup.com/reference/square/payments-api/create-payment)
- [Refund Payment](https://developer.squareup.com/reference/square/refunds-api/refund-payment)
