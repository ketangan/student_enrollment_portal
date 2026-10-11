# M5a Impact and Verification

Date: October 1, 2026 (America/Los_Angeles).
Status: ready for user testing. Not accepted, committed or pushed.
Working tree based on `7ccd62b02ee2c4aeb0621ae4a82cd50af9034cf6`; unrelated SBMC homepage
and nested-worktree changes remain unmodified.

## Scope

M5 was split into M5a (shared financial records/native holds), M5b (verified shared Stripe
checkout) and M5c (provider recovery/refunds/paid release). This implements only M5a.
Workshop groups and reviewed parties can become native reservations when a workspace owner
explicitly enables the capability. USD quotes, deposit/full-payment thresholds, temporary
capacity holds, offline test receipts, cancellation and separately recorded refunds persist.

No real money is moved. The interface and financial records explicitly identify the test
environment. No new Stripe adapter, webhook, customer checkout, recurring collection or
external integration was added. Jasmine's Wix/Notion work remains deferred M4, not solved here.

## Impact Review

- Additive shared `core/payments` app: PaymentOrder and LedgerEntry plus authorized-caller
  service functions. Payment records do not require an enrollment application or booking FK.
  Existing tenant identity and audit logging are reused. No second Stripe client was created.
- Existing `core/services/billing_stripe.py`, core enrollment models, application-fee routes,
  platform billing webhooks and School subscription state are unchanged by M5a.
- One settings registration for the shared app; scheduling changes are contained in its
  models/services/forms/routes/views/templates. Earlier M1a shared login/routes/settings
  changes remain in the tested working tree. No customer-name conditionals.
- Payments migration 0001 and scheduling migration 0005 are additive. Existing profiles get
  native_reservations_enabled=False. Applied only to the isolated preview database. Forward
  and reverse migration tests preserve core School and prior scheduling setup/history.
- Supported inventory changes serialize on the tenant profile. Financial writes additionally
  lock the School/order and validate whole-cent amounts, tenant/environment/merchant scope,
  unique receipt references and exact idempotency-key replay. Audit failure rolls back writes.
- Quote and ledger history have no edit/delete service or UI. Their supported lifecycle is
  fixed quotes and append-only entries; these are not DB-trigger guarantees against privileged
  raw SQL/ORM writes. Production permissions and release controls remain required.
- Holds expire logically in capacity queries. Confirmed reservations keep capacity; canceled,
  review-needed and expired holds do not. Half-open intervals permit adjacent reservations.
  Shared-room parties use peak concurrent group size, not a sum over disjoint times.
- Published sessions and active reservations block conflicting private-room claims. Session
  time/room edits and capacity reductions cannot invalidate active reservations. Proposal
  versions reject stale party quotes; reserved party proposals are locked against editing.
- Money and capacity are separate. Late offline money remains in the ledger if capacity is
  gone; the reservation becomes Needs attention rather than confirmed. Cancel does not refund.
  Partial late money does not silently leave an expired hold looking reserved.
- Staff reads/writes are scoped to the business; only booking owners can perform financial
  writes. Viewer/scheduler writes, cross-tenant URLs, GET mutations and CSRF failures are tested.
  Public receipts do not expose quotes or internal decisions and cannot serve as payment proof.
- No enrollment submission/plan transitions, mail or external requests occur. Native reservations
  are off for all existing schools; only the isolated Workshop Demo was explicitly enabled.

## Verification

Final focused run: **281 scheduling tests passed in 13.63 seconds**. Scheduling plus the shared
ledger: 1,400 statements and 284 branches, **99.88% combined line/branch coverage**. The shared
payment app itself has 100%; one defensive room-capacity rejection line/branch is unexecuted
because the preceding M3 assigned-party validation already rejects that model edit.

Final full regression: **2,237 passed, 2 warnings in 54.64 seconds**. Including the local
viewer and existing login, combined Python line/branch coverage is 99.27% (viewer 99.21%,
legacy login 90.43%). No failed or skipped tests. Django checks report no errors and only
the pre-existing duplicate jazzmin template-library warning. Migration drift: no changes.
Tracked whitespace check passes. Template and visual checks are separate from Python coverage.

Tests include real PostgreSQL races for the last workshop places, exclusive room conflicts
and duplicate offline receipts; all-or-nothing group capacity; expired/late/partial payments;
free/full/deposit settlement; overpayment and excessive refund rejection; cancellation/retry
idempotency; stale quotes; migration reversibility; audit rollback; default-off and role isolation.

Commands (temporary local settings/guards prevent all external API calls):

```sh
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings COVERAGE_FILE=/tmp/pontora-m5a-focused.coverage venv/bin/python -m pytest scheduling -p pontora_local_db_tests --cov=scheduling --cov=core.payments --cov-branch --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings COVERAGE_FILE=/tmp/pontora-m5a-full.coverage venv/bin/python -m pytest -p pontora_local_only_tests --cov=scheduling --cov=core.payments --cov=scripts.project_status --cov=core.views_login --cov-branch --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_ui_settings venv/bin/python manage.py check
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_ui_settings venv/bin/python manage.py makemigrations --check --dry-run
```

Logs: `/tmp/pontora-m5a-focused.log`, `/tmp/pontora-m5a-full.log`.
Coverage numbers refer to Python execution, not visual/template coverage or proof of correctness.

## Browser Checks

Isolated preview at localhost:8002, fictional data, external traffic blocked:

1. Default-off reservations, explicit owner enablement, disable-new without hiding old records.
2. Alex Party Demo blocked by a published Room 1 workshop. No order/reservation created.
3. Morgan Demo: invalid 100/125 quote rejected; valid 100/25 creates two-person held reservation.
4. Fictional $25 offline deposit confirms with $75 remaining. $1,000 overpayment rejected;
   $75 balance accepted. Restart/reload retains the same saved financial data.
5. Cancellation releases capacity, retains $100 received and shows no-automatic-refund warning.
   Separate $100 offline refund brings net received to zero and keeps all three history entries.
6. New public Taylor Party Demo inquiry: October 7, 2-4 PM, eight participants, private room.
   Staff quote 400/100 in Room 1, native reservation, fictional deposit -> Confirmed/$300 balance.
7. Read-only account sees history without create/settle/cancel controls. SBMC and Kid Works
   smoke accounts still open their original enrollment dashboard/navigation with no Bookings.
8. Restarted the final preview and reloaded Taylor's reservation: Confirmed, $100 received,
   $300 balance and the same receipt persisted. The original party review form is absent.
9. Detail/payment/quote layouts checked at 1280px, 390px and 320px. No page horizontal overflow,
   overlapping text or controls outside the page; logo loads, labels wrap, acknowledgment
   checkboxes use the existing compact style and financial controls remain usable.
10. Refreshed the local tracker after the final documentation update: M5a shows Ready for your
    test; its row expands to the manual checklist and owner/viewer credentials. The next
    merchant/sandbox decision remains visible, and M5a verification opens this report.

Screenshots: `/tmp/pontora-bookings-m5a-reservation.jpg` and
`/tmp/pontora-bookings-m5a-reservation-mobile.jpg`. All pictured records are fictional.

Self-review corrected stale quote acceptance, late partial-payment status, misleading old
request-list/preparation wording, proposal editing after reservation, public receipt wording,
and checkbox/section layout. These fixes are covered by rendered-response or browser checks.

## Boundaries and Next Gate

This is an offline test foundation, not a production payment release. It does not provide
customer quote acceptance, confirmed customer correspondence, self-service rescheduling,
teacher calendars, stock inventory, external provider capacity or recurring billing. Owner
approval is required for each quote. USD only; fixed 30-minute holds are a prototype policy.

M5b needs an approved merchant/account model and authorized sandbox. The existing application
fee confirmation path has fail-open/order-binding risks and cannot simply be reused for native
bookings. Its hardening requires a separately reviewed compatibility/policy decision and
regression evidence; it was intentionally not changed under this offline milestone.

One shared Stripe/account boundary must extend the existing payment service for M5b, with
server-owned order/amount/currency/account/mode binding, persistent attempts, provider
idempotency and verified settlement. Before online use, a local deadline alone must not
release capacity while provider checkout can still succeed. M5c must reconcile that race,
late/duplicate/out-of-order events, canceled bookings, refund failures and retry recovery.
Official references reviewed: [Stripe charge models](https://docs.stripe.com/connect/charges),
[idempotent requests](https://docs.stripe.com/api/idempotent_requests) and
[limited-inventory Checkout](https://docs.stripe.com/payments/checkout/managing-limited-inventory).

Stop new reservations to pause intake while retaining existing records/authorized settlement.
Disabling the whole Bookings profile removes access. Do not reverse these migrations after
real use without an approved export/backup: reversal destroys financial/reservation history.
Use forward repair and reconciliation after real settlement, not a blind schema rollback.
No main-local/production migration, existing-school enablement, commit or push performed.
