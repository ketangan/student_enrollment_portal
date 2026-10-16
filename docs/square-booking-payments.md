# Square Booking Payments

## Status

October 15, 2026: local sandbox implementation. Not deployed. Live Square merchant
connection and production acceptance remain required before Plushie's go-live.

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

## Checkout and Recovery

### Deposits and Balance Collection (October 16)

**Status: done locally to the agreed scope, pending Jasmine's final inputs and
deployment.** Ketan accepted the customer success/decline behavior. Remaining
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

1. Apply `payments.0005`, `scheduling.0024` and `scheduling.0025` during an approved deployment.
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
5. Terminal balance collection and automated emailed payable-link workflows remain deferred.
   Staff-initiated online balance collection uses the existing reservation payment page.
   Do not claim a Square device transaction automatically reconciles in Pontora.

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

## Square References

- [Web Payments SDK](https://developer.squareup.com/docs/web-payments/quickstart/add-sdk-to-web-client)
- [Sandbox payment testing](https://developer.squareup.com/docs/devtools/sandbox/payments)
- [Create Payment](https://developer.squareup.com/reference/square/payments-api/create-payment)
- [Refund Payment](https://developer.squareup.com/reference/square/refunds-api/refund-payment)
