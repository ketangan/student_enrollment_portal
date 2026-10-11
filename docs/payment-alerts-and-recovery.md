# Payment alerts and subscription recovery

Status: implemented locally; deployment and real Stripe/email verification are still required.
No production data, live Stripe configuration, cards, or customer emails were changed.

## Isolated release verification

The enrollment/billing release is packaged independently of the still-uncommitted scheduling module.
It includes platform subscription/invoice alerts, school-owned application-payment alerts, the shared
seven-day recovery gate, and the existing Billing & Plan UI. Scheduling membership lookup is optional;
enrollment billing works when scheduling is not installed. Booking settlement/failure hooks and the
scheduling banner remain with the pending scheduling release, not this enrollment-only package.
The booking-specific failure adapter lives in `core/payments/notifications.py`; its integration tests
live with scheduling. Do not enable the booking webhook until that module is released.

Final release-tree verification, without the local scheduling files:

- **2,079 tests passed**, including existing enrollment and SBMC regressions.
- **218 billing/trial tests passed** on KW-05 alone, before the payment change.
- **179 focused integration tests passed** in the full local workspace after separating the modules.
- New runtime modules: **98-100% combined line-and-branch coverage** in the enrollment release.
- Browser-verified grace, overdue, expired-trial, and demo states; desktop and 390px mobile layouts.
  No horizontal overflow or browser script errors. The existing Billing & Plan screen is reused.
- Migration drift: none. All databases/accounts were disposable local fixtures; providers were blocked.

This is a Git release, not a production activation. Apply migrations 0061 and 0062 before serving the
new code, and complete the webhook/email/retry-job setup below. The checked-in Render configuration
does not establish a migration-before-start step; deployment ordering must be verified before merging
the release branch into an auto-deployed branch. Existing customer data/settings remain untouched.

## Shared billing UI correction

Payment recovery now uses the existing `school_admin/billing.html` (Billing & Plan) page and expiry
redirect, rather than a separate recovery template. The extra recovery template has been removed.
This behavior is shared across all schools and standalone scheduling workspaces, not scoped to
Kid Works. The earlier Kid Works screenshot was an isolated test fixture, not an implementation gate.

The existing plan summary shows the payment deadline and owner-only billing action. Overdue accounts
do not see a new-subscription purchase prompt. Nonowners see the account-owner contact instruction;
accounts missing a Stripe customer reference get a support path instead of an unusable action.
Payment expiry has one persistent warning. Billing uses the shared base's message rendering so portal
errors are not duplicated. Existing trial pricing, subscription cancellation states, and portal routes
are retained. No payment processing, school configuration or database migration changed in this correction.

Verification for this correction:

- Complete repository regression: **3,637 passed**.
- After the final warning-rendering cleanup: **309 passed** across payment, billing, trial-expiry and
  customer-trial activation tests. Tests explicitly assert the shared billing template across different
  school slugs/plans, grace and suspended states, and standalone scheduling owner/nonowner roles.
- Browser checks: synthetic Northside Music School (grace/expired), Lakeside Studio (scheduling-only),
  and Trial School (existing expired-trial plan screen). Desktop and 390px phone layouts were inspected;
  no horizontal overflow or clipped billing button. Final-page browser error log was empty.
- Only isolated preview/test databases were used. No real payments, messages or school data were changed.

## Behavior

- `PAYMENT_ALERT_RECIPIENTS` defaults to `ketan@mypontora.com`; comma-separated aliases are supported.
- Platform invoice payments, including first payments, monthly/annual renewals and one-time invoices,
  generate a durable operator notification. Failed attempts and authentication/finalization problems
  also generate notifications. Retries of the same delivery do not generate duplicate notices.
- School-owned application payments have a separate signed webhook. Alerts do not depend on a parent
  returning to the browser after paying. Unrelated payments in a school's Stripe account are ignored.
- Verified booking payments and failed booking payment attempts use the same notification outbox.
  The existing booking Stripe transport is still **test-only**; this task does not enable live booking
  charges, recurring booking charges, or change its settlement/refund behavior.
- Test-payment email is off by default. Opt in with `PAYMENT_ALERT_TEST_EVENTS=true`; subjects say TEST.
  Demo/test events never create a real school suspension. Development payment bypasses are not payments.
- Notification payloads contain the school, payment/invoice reference, amount, event and current invoice
  state. No card numbers, client secrets, draft tokens, participant names or raw provider payloads are emailed.

## Seven-day rule

The earliest unresolved failed Pontora subscription invoice starts a seven-day deadline. A retry or
card update does not restart it. Invoice status is re-read from Stripe under the school lock so delayed
failure deliveries cannot replace a newer paid state. Paying one invoice cannot clear another tracked
overdue invoice. Voiding a debt clears that invoice; marking it uncollectible does not.

At the deadline, the existing school-admin expiry gate also enforces payment expiry. Public enrollment,
family status and scheduling routes are blocked, as are direct legacy admin and download links.
Both enrollment and scheduling are suspended for the affected school, as explicitly requested.
Operator superusers retain access. Records are retained. No timer or scheduled job is needed to enforce
the deadline: it is checked on each request.

Billing remains available to the account owner. Scheduling-only owners can use the same billing
recovery page without receiving enrollment access. Other team members see a request to contact their
owner, not controls to change billing. Provider webhooks continue working while the school is suspended.

Verified invoice resolution removes the payment suspension. It does not reactivate a manually inactive
or canceled school. Existing independent trial activation, demo behavior, cancellation rules, feature
flags, programs, customer forms and booking entitlements are otherwise preserved.

## Card changes

The existing Stripe Customer Portal remains the sole card-management UI. Card data is not stored in
Pontora. An owner may update a card while active, within the grace period or after payment suspension.
The owner must also pay/resolve the overdue invoices. Merely changing a card or returning from Stripe
does not restore access. Existing Stripe-backed custom plans may manage payment methods; manually
managed plans without a Stripe subscription remain manually managed.

Stripe retry rules and payment-method defaults still matter. Before rollout, test that changing the
card changes the effective payment method for the actual subscription, including any subscription-level
override. Do not automatically cancel subscriptions before the seven-day grace expires. To retain the
pay-and-resume flow after the deadline, keep delinquent subscriptions recoverable (`past_due`/`unpaid`)
rather than canceling them. A canceled subscription needs a new subscription, not just a card update.

## Required deployment setup

1. Back up the database and apply `core.0062_payment_notifications` after the existing KW-05 migration.
   These are additive tables and a nullable deadline. There is no automatic backfill or change to existing
   school plans, passwords, feature flags, activation dates or subscriptions.
2. Set a verified `DEFAULT_FROM_EMAIL` and production email backend. Console/dummy delivery remains queued
   instead of being falsely recorded as sent. Set `PAYMENT_ALERT_RECIPIENTS` if another alias is preferred.
3. On the **platform** Stripe endpoint `/stripe/webhook/`, retain the existing checkout/subscription events
   and enable `invoice.paid`, `invoice.payment_failed`, `invoice.payment_action_required`,
   `invoice.finalization_failed`, `invoice.voided`, and `invoice.marked_uncollectible`.
4. For each school collecting application fees in its own Stripe account, register
   `/stripe/application-fees/<school-slug>/webhook/` on the production app domain for
   `payment_intent.succeeded` and `payment_intent.payment_failed`.
   Set `APP_FEE_WEBHOOK_SECRETS` to a JSON object mapping school slugs to their endpoint signing secrets.
   Store this in deployment secrets, never in Git or school YAML. Existing API keys are not webhook secrets.
5. For the existing booking sandbox endpoint `/bookings/stripe/test/webhook/`, include
   `payment_intent.payment_failed` along with the existing checkout events. Account, order, attempt,
   amount, currency and environment must match. Production booking rollout remains a separate milestone.
6. Run `python manage.py send_payment_notifications` every minute in the deployed environment.
   Monitor nonzero exit codes, webhook failures, and the age of unsent `PaymentNotification` rows.
   Successful delivery is attempted after commit; this job retries failures and recovers interrupted work.
   Invalid email configuration must never roll back a verified payment or the invoice deadline.
7. Review existing delinquent subscriptions before enabling enforcement. Historical failures are not
   inferred from status alone. Replay their actual signed invoice events deliberately; original event
   timestamps determine the deadline, so replaying an old unresolved failure can immediately suspend it.
8. Complete a Stripe sandbox rehearsal and a verified-email delivery test before production rollout.
   No live test charge is required or authorized by this change.

Email uses at-least-once delivery. A crash after the provider accepts an email but before `sent_at` is
committed can produce a duplicate on retry; SMTP/provider delivery cannot honestly promise exactly-once.
No notification service can guarantee inbox placement. Pending/failed delivery is retained for recovery.

## Manual verification performed locally

- Used a disposable PostgreSQL database with synthetic schools and owners; external services blocked.
- Logged in as an enrollment owner and verified the deadline banner and billing link during grace.
- Inspected desktop and 390px mobile recovery layouts for wrapping, legibility and the payment button.
- Advanced the synthetic deadline; dashboard access redirected to recovery and parent forms were blocked
  without displaying the school's financial information to families.
- Logged in as a non-staff, scheduling-only owner; scheduling redirected to the same recovery screen,
  with a Scheduling navigation link and no enrollment navigation or enrollment data access.
- Applied a simulated verified paid invoice; scheduling access returned and the warning disappeared.
- Verified automated tests for owner/nonowner permissions, failed email recovery, duplicated/concurrent
  webhooks, delayed events, multiple overdue invoices, tenant isolation, school-owned parent events,
  booking events, trial regressions and checkout replay/replacement protection.
- The real Stripe portal/card update and external email delivery were **not** exercised locally.

## Impact and review

This is a shared billing change, not a Kid Works-only change. Schools without a payment deadline retain
their existing behavior. The only scheduling settlement edit queues a notice after verified settlement;
failure alerts do not cancel reservations, release inventory or mutate school billing.

The worktree already contained substantial scheduling and KW-05 work. Scope review compares against
the pre-task dirty-tree snapshot, not just HEAD. Existing enrollment forms, school YAML, programs,
inventory rules and previously completed KW-05 implementation were not rewritten.

Reviewed and corrected: blank subscription IDs, stale subscription metadata, stale checkout replay,
duplicate subscriptions during recovery, owner-only billing writes, bad email settings rolling back
payment records, test/live separation, standalone recovery permissions, and bypasses via direct links.

## Verification results - October 8, 2026

- Complete repository regression: **3,630 passed**. The initial broad run exposed one test that
  needed database access for the new public-access middleware; its fixture was corrected and rerun.
- After the final nullable subscription-details fix: **263 passed** across payment notifications,
  billing, and customer trial activation. This includes the additional one-time invoice cases.
- Payment-task changed executable lines: **338/343 (98.54%)**, measured against the pre-task snapshot.
  New service/endpoint/command combined line-and-branch coverage ranges from **92.86% to 100%**.
  This is changed-code coverage, not a claim of 98.54% for the whole repository.
- Migration drift check: no changes detected. Whitespace/diff check: clean.
- Existing duplicate `jazzmin` template-tag warning remains; no unrelated dependency change was made.
- Isolated desktop/mobile browser verification is listed above. Normal development and production
  databases were not migrated. No real provider or outbound email verification was attempted.

## Separate pre-existing application-fee risks

Before enabling live application-fee collection, harden the existing enrollment payment flow:

- `core/views_public.py::apply_payment_view` completes the application when creating a PaymentIntent
  raises an exception. This intentionally fails open today; it does not prove payment or collect a fee.
- `apply_payment_confirm_view` checks the retrieved intent's success status, but does not match its
  metadata, amount and currency to this draft's expected fee before marking the application paid.
  An intent from the same school's account is not sufficient proof that this application was paid for.

These behaviors predate this task. They were not changed while implementing alerts and subscription
access enforcement. The new signed application-payment notification endpoint validates the draft
binding, but does not repair the existing application-completion flow. Treat these as a separate
live application-fee rollout blocker, not as covered by notification tests or delivery setup.

References: [Stripe subscription events](https://docs.stripe.com/billing/subscriptions/webhooks),
[Stripe retries and payment methods](https://docs.stripe.com/billing/revenue-recovery/smart-retries),
[Stripe Customer Portal](https://docs.stripe.com/customer-management).
