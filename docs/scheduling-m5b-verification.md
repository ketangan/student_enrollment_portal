# M5b: Local Checkout and Verified Settlement

Date: October 1, 2026 (America/Los_Angeles).

## Scope and Status

M5b's local implementation is ready for user testing. The user approved merchant-owned
Stripe accounts and explicitly chose local simulation, with actual sandbox verification later.
This is not a production release or proof that a real Stripe account has been connected.
No commit, push, deployment or live charge is included.

Implemented: an operator-provisioned test merchant binding, immutable order-bound checkout
attempts, stable provider idempotency, a signed-event receipt inbox, deposit/balance ledger
entries, public expiring payment links and booking-specific capacity/confirmation transitions.
The receiving account is not accepted from browser form data. USD/card one-time payments only;
recurring billing is M8. Real Connect onboarding and Stripe refund/recovery are not implemented.

## Impact Analysis

- Shared tables: additive `payments.0002` adds test accounts, attempts, receipts and an optional
  order account binding. Existing offline ledger rows keep their channel and actor. Only the
  isolated PostgreSQL preview/test databases were migrated; main-local and production untouched.
- Shared transport: `core/services/billing_stripe.py` remains the only Stripe SDK integration.
  Platform subscription checkout, portal and subscription retrieval now pass their existing
  selected key explicitly instead of setting a process-global key. Public function signatures,
  settings selection, return values and subscription handlers are preserved.
- School application-fee calls already pass their school's own key explicitly and retain that
  behavior. Tests verify the platform key, school fee key and booking merchant scope separately.
- Booking checkout requires its separate test key and merchant binding. There is no fallback to
  `STRIPE_MODE=live` or the platform's live key. The normal settings do not enable this checkout.
- The dedicated booking test webhook cannot change `School.plan`, subscription IDs, application
  status or enrollment entitlements. Booking-only staff do not gain enrollment access.
- Scheduling changes are inside the optional module. Existing schools remain default-off;
  offline M5a remains available independently. Shared inventory writers still use the tenant lock.
- No main-local credentials, external accounts, customer data or unrelated SBMC homepage work
  were changed. The separate window's worktree/static/demo changes remain untouched.

## Safety and Self-Review

The order fixes merchant, currency, total and deposit. The attempt fixes amount, return URLs,
provider expiry and idempotency identity before an API call. No network call runs while a
database transaction holds inventory locks. Concurrent starts reuse the same active attempt.
Responses are checked before redirect; only Stripe's HTTPS checkout host is accepted.

Signed events must match account, test mode, payment purpose, order, attempt, amount, currency,
session and payment identity. Receipts, financial entries and reservation transitions commit
atomically; retries cannot create a second ledger entry. Disabled merchants still accept valid
settlement evidence for money already collected. Success URLs never settle money.

Unknown/network-failed checkout retains its capacity claim. Local expiry cannot release a
place while Stripe may still accept payment; verified expiry can. Pending checkout also blocks
staff cancellation. Confirmed reservations keep their space when a later balance checkout
expires or an anomalous extra payment arrives. Late money after capacity loss is retained in
the ledger and flagged for review instead of falsely confirming an unavailable booking.

Review corrections made during this milestone:

- PostgreSQL nullable-join locking changed to lock only the order row.
- Browser verification caught a no-referrer/CSRF conflict; same-origin referrer policy now
  preserves normal CSRF validation while withholding the capability URL from other origins.
- Duplicate events cannot opportunistically reconfirm a prior review state.
- An overpayment no longer releases an already-confirmed reservation; the staff page warns.
- Review warnings distinguish an outstanding checkout's retained capacity from a released hold.
- Actual Stripe SDK resource objects, not just dictionaries, are covered in transport tests.

## Automated Verification

Final full regression: **2,292 passed, 2 warnings, 59.79 seconds**. Scheduling/shared-payments
Python coverage: **99.90% combined lines/branches** (1,700 statements, one missed defensive
capacity-validation line; 378 branches, one partial). The new shared checkout service,
booking checkout service and checkout views each have **100% line/branch coverage**.
The pre-existing billing service as a whole is not claimed to have 90% coverage; added
merchant checkout functions and the changed platform key call sites are directly tested.
All four added Stripe transport/verification functions have 100% line/branch coverage.
No pending schema changes (`makemigrations --check --dry-run`); only the existing duplicate
`jazzmin` template-tag warning. Whitespace check passed. Tests cover tenant/role/CSRF
boundaries, signed raw webhook payloads, wrong account/mode/amount/currency/metadata, changed
session/intent, repeated/concurrent checkout, repeated/concurrent events, deposit/balance,
unknown responses, verified expiry, late/extra money, cancellation races, atomic rollback and
unchanged platform billing/application-fee key selection. All external sockets are blocked;
PostgreSQL is the only allowed test database. Viewer tests allow loopback HTTP only.

## Browser Verification

Browser checks use a temporary loopback-only harness, not an alternative production payment
module. The harness supplies fictional SDK responses and signed test events through the real
webhook view. `/tmp/pontora_m5b_preview_urls.py` is imported only by the isolated preview settings;
its simulation screen explicitly states that it is not Stripe and transfers no real money.
Normal application URL configuration contains no simulated-payment endpoint.

Verified: staff reservation -> customer payment link -> return without paying (still unpaid)
-> simulated $25 deposit -> Confirmed/$75 balance -> expired balance checkout (still Confirmed)
-> replacement $75 payment -> $100 verified/$0 remaining. The staff history and customer view
persist after reload. Desktop and 390px mobile checked; no horizontal overflow, logo loaded.
Local test: `bookings-owner` / `admin`. Read-only comparison: `bookings-viewer` / `admin`.
Expand M5b in the browser tracker for repeatable steps and limitations.

## Remaining Gates

1. Authorized Stripe Connect sandbox: account ownership/onboarding, direct charges, capabilities,
   exact API/webhook version and signed delivery, real test checkout and payout destination.
2. M5c recovery: scheduled reconciliation of unknown attempts, provider expiry, staff recovery,
   refunds, cancellation handling and alerting. Until then an uncertain checkout intentionally
   remains held and requires review; it must not be cleared to force availability.
3. Production settings, credential storage and operational monitoring; no live account or
   route enablement until separate approval and external evidence.
4. Existing enrollment application-fee flow still has its previously identified fail-open and
   weak intent-to-draft binding behavior. This milestone does not retrofit it, because that
   requires a separate SBMC impact review and acceptance. The new booking flow does not reuse
   that unsafe policy. One SDK facade is reused, not a claim that every legacy payment flow has
   already been migrated to the new order/attempt lifecycle.

Primary references reviewed: [Stripe Checkout session creation](https://docs.stripe.com/api/checkout/sessions/create)
(30-minute minimum expiry), [signed webhooks](https://docs.stripe.com/webhooks), and
[Connect direct charges](https://docs.stripe.com/connect/direct-charges). Installed Stripe Python
SDK 14.3.0 was inspected for explicit account-scoped clients and SDK object conversion.
