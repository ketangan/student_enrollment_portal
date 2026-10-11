# P1 Usable Pilot Foundation

October 4, 2026. Local verification only. No push or production deployment.

## Delivered

- Operator-granted workshops, private parties, reservations/payments, camps and lessons.
  Business owners cannot grant themselves products. Dependencies are explicit; malformed grants
  fail closed. New profiles start empty. Routes and services enforce grants, not just navigation.
- Removal with saved setup or records is rejected so balances, refunds and history remain
  manageable. Intake switches remain separate. This is not a subscription-downgrade/archive flow.
- Staff home: pending requests, new party inquiries, payment exceptions and seven days of
  published sessions, confirmed parties and scheduled lessons. Count links open filtered lists.
  Agenda is capped at 20 events with a continuation message; event times use their location.
- Setup checklist for granted features only. It reports configuration milestones, not commercial
  launch readiness. Rooms remain optional. Mobile navigation wraps; request tables scroll within
  a labeled, keyboard-focusable region. Filtered empty states no longer misrepresent history.
- Repository-owned safe test settings and network blocking; CI enforces 90% combined statement/
  branch coverage for every measured module, not only a package average.

## Impact

Application runtime edits are confined to Bookings. No enrollment models, school YAML,
payment service logic or SBMC navigation changes. Payment-exception queries were extracted
into one shared read helper with the same predicate, not a new ledger. Cross-module changes
are limited to CI/testing infrastructure and the local read-only project viewer.

Additive migration `0014_profile_capabilities` preserves existing profiles' prior feature access
without enabling a disabled profile or changing intake switches. New profiles default empty.
Migration/rollback tests preserve School records. Only `pontora_bookings_m1a_preview` was migrated;
the main database was not. Fictional Workshop Demo has workshop/party/reservation/camp grants;
Music Demo has lessons. SBMC and Kid Works have no SchedulingProfile in this preview.

The workspace enabled switch remains a whole-workspace access control, not an intake pause.
Native/provider inventory and payment authority must be explicit before connecting Wix.
Wix/Notion, real message delivery, mixed online-deposit/in-person-balance settlement, scoped
teacher/location roles and client onboarding remain unfinished. No charges, real emails,
customer-system calls or production settings changes were made.

## Automated Verification

Final consolidated run: **2,861 passed**, two existing warnings, 135.94 seconds.
Scheduling, shared payments and test infrastructure: **99.91% combined statement/branch
coverage**, excluding tests and migrations. All measured modules pass the 90% per-module
gate. The four new product modules (`capabilities`, `features`, `workspace`,
`reservation_queries`) have **100%** statement/branch coverage.
Tests cover default-deny, all roles, foreign/disabled workspaces, CSRF, forged grants, dependency
validation, stale edit tokens, atomic audit rollback, simultaneous operator saves, and a real
feature-removal versus setup-write race. Either the saved record remains accessible or creation
is denied. Direct service calls and already-open customer forms cannot bypass missing grants.

Workspace tests cover tenant scoping, pending versus reserved requests, declined inquiries,
payment review, paused intake, canceled lessons, unpublished sessions, date bounds, ordering,
event limits and bounded query count. Existing enrollment, payment, migration, concurrency and
family-access regression tests still run. Migration drift and whitespace checks pass.

Portable local commands (the named database is disposable):

```sh
PONTORA_TEST_DATABASE_URL=postgresql://njulka@127.0.0.1:5432/test_pontora_bookings_m1a venv/bin/python -m coverage run --source=scheduling,core.payments,testing -m pytest
venv/bin/python -m coverage report --fail-under=90
venv/bin/python -m coverage json -o /tmp/pontora-coverage.json
venv/bin/python -m testing.coverage_gate /tmp/pontora-coverage.json
```

Default pytest settings skip `.env`, clear known external credentials, use local-memory email/
cache, and require a loopback PostgreSQL `test_pontora_*` database without connection options.
The plugin rejects external Python DNS/connect/connect_ex and datagrams. Mock providers.
This is not an OS sandbox against malicious subprocesses. Do not override the safe settings
for routine tests. CI configuration is exercised locally; no remote CI run is claimed.

## Browser Verification

Preview: http://127.0.0.1:8002/bookings/workshop-demo/.
Isolated owner: `bookings-owner` / `admin`. Operator: `bookings-operator` / `admin`.

1. Workshop and Music workspaces show different, relevant menus. Records persist after restart.
2. Payment review opens the one local canceled reservation needing follow-up. Pending counts
   open Awaiting review; All requests restores full history.
3. Submitted Pilot Review Demo with two participants and different design/color choices.
   Home count changed from zero to one; its link opened the correct request and saved choices.
   No email or payment was triggered. Request deliberately remains pending for user review:
   `3afbaf5e-5927-4276-bb9e-90a4cddd7a1f`.
4. Operator tried removing Camps. Save was rejected because camp records exist; reload retained
   the grant. Ordinary owners have no feature-grant control; direct denial is also automated.
5. Desktop and 390px mobile checked: wrapped navigation, fitted text, loaded logo, document
   width equal to viewport, table-local scrolling. Owner session restored after operator testing.

Screenshots are local under `/tmp/pontora-p1-*`. Exact user steps are in the browser tracker.

## Self Review

Grants are separate from roles, enrollment entitlements and intake preferences. Checked supported
setup/intake writers and preserved conflict queries over all existing inventory. Reused profile
locks, signed edits and audit records. Checked query growth, customer selections and mobile
layout. Fixed misleading filtered-empty copy and made count links open matching filters.

P1 finishes the foundation slice, not the connected pilot. Next prove two-participant selection,
same-record Notion delivery, changes/cancellations, retries and reconciliation in authorized
test systems. Mixed settlement is still required before a native paid-party pilot.
