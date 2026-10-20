# Square Booking Payments

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
