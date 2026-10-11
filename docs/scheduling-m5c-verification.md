# M5c: Recovery and Refund Verification

Date: October 1, 2026. Scope: local simulation, not paid-pilot approval.

## Delivered

- Account-bound recovery for missed checkout notifications and uncertain creates. Provider reads
  and expiry requests run outside inventory/database transactions. Reads never create a checkout.
- Owner controls: check payment, close checkout, recheck paid booking capacity, cancel safely,
  approve partial refunds, and check refunds. Read-only staff cannot execute them.
- Persistent refund requests bound to the original verified payment, approval reason/actor and
  stable identity. Pending/unknown refunds reserve the refundable amount; only verified success
  appends financial evidence. Concurrent approvals cannot over-refund.
- Five-minute worker leases, exponential failure backoff, a 30-second manual-check cooldown,
  and pause after six failures. Restarting a worker does not erase pending operations.
- Payment follow-up filter and payment/refund history. GET/refresh does not call Stripe.
- One shared Stripe facade and one order/ledger implementation; no new per-school credentials.

## Impact Analysis

| Surface | Change and boundary |
| --- | --- |
| Shared payments | Additive recovery fields and Refund table; no changes to School plans, subscriptions, submissions or enrollment statuses |
| Stripe facade | Account-scoped session retrieval/expiry and refund create/retrieve/list; explicit test key/merchant; bounded lookup (3 pages of 100), 10-second timeout, no SDK retries; legacy subscription/application-fee functions unchanged in M5c |
| Bookings | Owner-only POST actions, refund form, follow-up filter; existing lock order retained: profile, school, order, attempt/refund |
| Schema | payments migration 0003 applied only to isolated preview/test databases; main local and production untouched |
| UI | Bookings-only templates/CSS. SBMC enrollment forms, navigation, scheduling preferences and Kid Works demo flows not edited |
| Tracker | Local verification document and expandable M5c testing steps |

## Automated Evidence

Final full regression suite: **2,334 tests passed**, two existing warnings, 61.92 seconds.
Bookings/shared-payments combined line/branch coverage: **99.92%**.
Recovery/refund services, recovery command and recovery views: **100%**. The shared Stripe module's
overall coverage remains lower because of pre-existing unrelated paths; new recovery/refund
transport functions are covered. Existing unrelated login/Stripe gaps are not claimed resolved.
Test log: `/tmp/pontora-m5c-final.log`; coverage: `/tmp/pontora-m5c-final-coverage.json`.

Checks exercise account/mode/amount/intent binding, signed event regression, stale/duplicate
results, expiry/payment race, missed webhook, failure retaining capacity, old unknown create
without blind retry, pending/failed/canceled refunds, concurrent approvals/workers, crash/lease
recovery, disabled-new-sales recovery, permissions, CSRF, cross-order IDs, bounded command/lookup,
no provider I/O inside database transactions and existing school flows.

Test networking is blocked except local PostgreSQL and loopback tracker tests. No actual Stripe
requests, customer connections, emails or money transfers occurred.
`makemigrations --check --dry-run`: no drift; existing duplicate jazzmin warning remains.

## Browser Evidence

- Casey Recovery Demo: recovered a missing $25 payment notification, confirmed capacity,
  canceled, approved a $15 refund, observed processing with $0 refunded, restarted the server,
  verified success, then observed $15 refunded/$10 net and one refund ledger entry.
- Avery Close Demo: cancellation blocked while checkout was open; explicitly closing checkout
  verified expiry before releasing the unpaid hold. No payment was invented.
- Desktop 1280px and mobile 390px checked; form controls fit without horizontal page overflow.
- Mobile excessive-refund submission ($11 when only $10 remained) was rejected without a ledger
  change. Shortened original-payment labels so they remain readable on narrow screens.
- Evidence: `/tmp/pontora-m5c-refund-desktop.png`, `/tmp/pontora-m5c-refund-mobile.png`.

## Critical Self-Review

1. Fixed a PostgreSQL nullable-join locking error found in regression tests. Row locks now
   target the payment/refund row; inventory/order locks remain explicit.
2. Corrected a Bookings party test matching `400` inside random UUIDs instead of leaked pricing.
   It still checks for the amount after removing only the known public reference ID.
3. No timeout or missing search result means "unpaid". Ambiguous requests retain allocations and
   require investigation. A provider lookup limit never releases capacity.
4. Unknown refund creates are never blindly resent. A crash immediately before the provider call
   may require operator investigation even when no refund exists. That conservative tradeoff is
   intentional; a missing response is not permission to issue another refund.
5. Refund status comes from the account-bound provider, not a customer redirect/success query.
   Refund webhooks/dashboard-originated changes are not claimed supported.
6. Self-review caught an unnecessary cancellation requirement for excess payments. Owners can
   now refund only the verified excess, less pending refunds, without losing a confirmed place.
   Other refunds require cancellation. No automatic customer refund policy was invented.

## Paid-Pilot Gate: Not Cleared

Local simulation completion must not be described as production payment readiness.

- Authorized connected-account sandbox verification: full/deposit/balance payment, lost response,
  retry, expiry race, successful/failed/pending refund, cancellation and late payment.
- Externally schedule `reconcile_booking_payments --limit 20`, monitor paused/old operations,
  and prove outage/restart recovery. No production scheduler is installed here.
- Agree the operator workflow for unlocatable creates and external refunds/disputes, with refund
  event reconciliation and stale-operation alerts before live collection.
- Review merchant onboarding, refund policy, credential rotation, support ownership and rollback.
- Harden/migrate legacy enrollment application-fee fail-open/binding issues separately with SBMC
  impact review before claiming all legacy payments use this ledger.
- Live mode, production migrations and deployment require explicit approval.

No commit, push, deployment or customer-system change made in M5c.

## Provider References

Stripe confirms that [checkout expiry](https://docs.stripe.com/api/checkout/sessions/expire)
only applies to open sessions; recovery retrieves status when payment/expiry can race.
[Refund states](https://docs.stripe.com/api/refunds/object) distinguish processing/action-required
from success. [Refund creation](https://docs.stripe.com/api/refunds/create) supports partial refunds
against the original payment. Stripe can prune [idempotency keys](https://docs.stripe.com/api/idempotent_requests)
after at least 24 hours; old uncertain creates are looked up rather than blindly replayed.
