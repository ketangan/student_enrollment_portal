# Customer Workshop Production Enablement

November 3, 2026. Opt-in code; deploying it does not enable any existing school.

## Configuration

Use a separate, active, non-demo school with an enabled scheduling profile and
workshops/reservations capabilities. Never convert the hosted demo school into a
live school. In its ordinary `configs/schools/<school-slug>.yaml`, configure:

```yaml
scheduling:
  customer_experience:
    enabled: true
    # Optional, school-owned branding/copy:
    # logo: /static/path/to/logo.png
    # photo: /static/path/to/workshop.jpg
    # start_title: Book a workshop
    # start_description: Choose a studio, workshop and time.
    # choose_title: Your participants
    # participant_heading: Your participants
    # confirmation_title: Your workshop is confirmed.
```

Existing customer copy, age restrictions, selection deadlines and booking policies
continue to use their existing school configuration sections. No demo YAML or demo
owner account is needed. Prices, deposits, tax rates, locations, published sessions,
choices, photographs, swatches and stock remain database-owned and editable through
their existing setup screens. Inventory Setup's first two stock dimensions remain
design and color; age follows the existing participant-age policy. Non-inventory
workshops retain their ordinary choice fields.

The customer entry remains `/bookings/<school-slug>/experience/`. An opted-in
non-demo school's ordinary public schedule redirects there. Existing demo routes,
host restrictions, simulations, configuration and saved links remain supported.
Legacy `experience_demo` booking snapshots are still recognized; new real bookings
use `customer_experience`. The shared services remain in their current package to
avoid unrelated import/route churn; that package name does not enable simulations.

## Payment and Activation Checks

1. Authorize the real Square seller account(s) in existing Integrations settings and
   map each studio to its correct Square location. Do not copy demo credentials.
2. Verify the live application/webhook configuration and activation switch, signed
   webhook delivery and scheduled recovery. Production workshop intake requires a
   ready live account for that school/location. Sandbox/offline fallback is rejected.
   Live Stripe booking activation is not added by this change; enrollment and
   subscription Stripe behavior is unchanged.
3. Confirm published sessions, prices/deposits/tax, capacity, room exclusivity,
   choices, material recipes/on-hand stock, policies and waiver process.
4. Keep native reservation intake paused while running the read-only preflight:

   `python manage.py check_workshop_launch <school-slug>`

   It checks opt-in/capabilities, upcoming priced sessions, available choices,
   deposit validity and local payment-account readiness. It reports whether intake
   is paused or enabled but never changes that setting. It does not call Square,
   charge cards, modify data, certify inventory counts, or prove webhook delivery.
5. Complete approved live payment/refund, email-link/inbox, staff prep, different
   browser/studio timezone and physical Android Chrome acceptance. Resolve existing
   Wix booking/balance cutover before changing Wix booking links.

Missing payment configuration blocks new selection/intake before payment; affected
session availability is disabled. The final transaction rechecks availability and
payment configuration, holds the existing school lock, reserves stock and saves
the shared payment order. Duplicate submissions return the same reservation.
Provider return URLs are never payment evidence. Customer-created bookings are
audited without impersonating a staff owner.

## Pause and Rollback

To pause new bookings, set `native_reservations_enabled=False` using the existing
operator controls. Keep the customer-experience opt-in, school and profile active:
existing confirmations, signed links and provider recovery must remain available.
Do not disable the whole school or delete payment mappings to pause intake.

The workshop-enablement change itself includes no schema migration or existing-school
activation. The optional Terminal addition below has separate additive migrations. If code rollback is
needed after real bookings exist, use a compatible fix-forward/release preserving
`customer_experience` snapshot support, rather than blindly rolling back to a
demo-only version that cannot display those bookings. Do not reverse payment or
inventory records. Keep webhook/recovery processing available for in-flight money.

## Verification Boundaries

Tests create a non-demo school with local-only mocked Square responses, assert no
demo loader or demo actor is called, and exercise booking, duplicate/concurrent
requests, inventory/capacity rollback, verified deposits, saved review/editing,
calendar, opt-in/tenant/environment guards, paused intake and read-only preflight.
Those tests prove application behavior, not real merchant/device acceptance.

Jasmine's hosted demo, real schools, accounts, customer records and credentials are
not changed by this implementation. Release requires the final full regression,
coverage gates and GitHub checks. Physical Android acceptance remains separate
from desktop responsive checks at 768x1024 and 1024x768.

## November 4 Cutover Rehearsal

This is the release checklist for a separate real studio, not permission to turn
the prospect demo into production. Preparation may run while demo testing continues.
Do not replace the demo's data, seller connection, staff passwords or access links.

### Verified Without Customer Input

On receipt release source `f6a0ad3`, 172 focused tests passed across
`test_workshop_production`, `test_square_live_payments`, `test_party_packages`,
`test_location_access`, `test_booking_maintenance` and `test_portal_environment`.
These use the existing disposable local test database and mocked provider responses.
They cover real-school intake without demo configuration/actors, deposit settlement,
editable return links, capacity races, party package/time restrictions, location
permissions, environment isolation, duplicate webhook delivery and payment recovery
after new intake is disabled. They do not prove real seller authorization or inbox
delivery. No live school, payment configuration or real charge was created.

The local paused-intake follow-up passed all 176 tests in the same focused group.
Its 27 workshop-production tests also verified SELECT-only preflight queries and
unchanged intake flags. Command coverage was 100% of lines and 98.11% combined
line/branch coverage. Full regression remains a separate release requirement.

November 4 verification update: detached `production-cutover-regression-20261104`
completed for `8b803af` with unchanged source. All 5,231 application tests and 23
migration tests passed (three skips), along with migration drift, overall/module
coverage and 126 JavaScript checks. PR #19 subsequently passed all GitHub release
checks (`41905788167`), merged as `27e7e02` and was verified Live on Render.
Post-merge main CI `41908101571` subsequently completed successfully.
No real school was activated and the live-recovery job flag was not enabled.
Hosted receipt delivery was separately verified in Ketan's inbox with its PDF.
Current actions are maintained in the
[active Plushie todo list](scheduling-implementation-tracker.md#november-10-active-plushie-todo-list).

The existing launch command is a **workshop configuration check**, not a complete
studio go-live certificate. It only inspects locations represented by upcoming
eligible workshop sessions. A location with no sessions, party-only availability,
email transport, live recovery scheduling and external website links require the
checks below. Do not interpret a green result as approval for every location.

### Configure While Intake Is Closed

| Check | Required evidence before opening |
| --- | --- |
| Separate live school | Active non-demo school, correct public host, school-scoped branding/policy configuration; no copied demo credentials or transaction history. |
| Studio inventory | Each intended location has its own timezone, address, rooms, capacity, tax, offerings, recipes and verified stock. Do not assume a missing location is intentionally closed. |
| Workshop calendar | Published offered sessions with correct dates, duration, prices/deposits, choices and exclusive-room conflict behavior. |
| Party calendar | `party_booking.packages_only: true`; published direct-booking packages, valid rooms, participant/observer limits, prices/deposits/tax and explicit offered times. New York's review-mode package must not be silently republished as direct. |
| Payment routing | Owner connects the intended live Square seller(s); every live studio is mapped to its correct Square location. Verify old orders keep their original account after remapping. |
| Application operations | Live application/callback/webhook values and credential vault present; webhook signature verification and monitored renewal/recovery jobs checked. Do not change SBMC Stripe or platform subscription settings. |
| Email | Approved sender or Pontora fallback, school-local templates and approved triggers; links use the production host. Verify a received message and PDF attachment, not merely a queued/sent audit entry. |
| Staff | Unique live credentials, least-privilege school/location membership, correct all-location versus single-location access. Demo `admin` passwords must never become the live credentials. |
| Customer policies | Correct policy URL, acknowledgments and existing guardian waiver process. A required checkbox is not a signed waiver. |

The hosted Render job was inspected read-only on November 4: it runs
`python manage.py process_booking_maintenance --limit 20` every five minutes and
reported successful recent runs. Its implementation only includes live
payment reconciliation when `--include-live-recovery` is supplied. Verify the
deployed scheduled command and failure alerts before enabling live intake; do not
change it as part of demo receipt verification. Token renewal already runs through
the same bounded maintenance command.

- [ ] **Live-payment launch gate (operator):** add `--include-live-recovery` to the
  production scheduled command at activation, verify its database/account scope,
  successful runs and failure alerts, and record idempotent uncertain-payment/refund
  recovery evidence. Keep processing in-flight payments when intake is paused. This
  is not complete merely because the command supports the flag. Leave SBMC Stripe
  processing unchanged.

### Optional Square Terminal Activation

Terminal collection is separate from online cards and is not a launch dependency.
The local implementation reuses original order account bindings, the existing
ledger/refunds/receipts, signed Square callbacks and maintenance recovery. It does
not change SBMC Stripe/enrollment settings. Manual recording remains available.

1. Deploy only after the final full regression, migrations and review pass. Apply
   additive `payments.0012-0016` and `scheduling.0054-0056`; old attempts remain online
   and have zero adjustments. Existing refunds keep zero price reductions. Never
   reverse these after recording new payment evidence.
2. Connect/reconnect each intended Square seller through Integrations to authorize
   `DEVICE_CREDENTIAL_MANAGEMENT`, alongside existing payment permissions. Existing
   card-only grants still renew without this permission; they cannot pair devices.
3. Subscribe the existing environment-specific signed Square webhook to
   `terminal.checkout.created` and `terminal.checkout.updated`, retaining the
   existing payment/refund subscriptions. Verify callback reachability/signatures.
4. In Settings > Integrations > Square Terminals, choose the mapped studio, name
   the device and get a pairing code. Sign the physical Terminal into Terminal API
   mode with that code (expires after five minutes), then Check pairing. Pairing
   may require signing out of its current Square POS mode; agree the change with
   studio staff first. Never pair a sandbox simulation with a live seller.
   Use **Check connection** separately: pairing is not proof the device is online.
   The non-charging PING request checks the original device/account binding and
   polls for its response. Verify this on the actual device before acceptance.
   Reference: [Square device information/PING](https://developer.squareup.com/docs/terminal-api/advanced-features/check-device-information).
5. Enable `PAYMENTS_SQUARE_TERMINAL_LIVE_ENABLED=true` only for an authorized live
   acceptance window, with existing `PAYMENTS_SQUARE_LIVE_ENABLED=true`. The new
   flag defaults false. Confirm the live-recovery job gate above is satisfied.
6. With owner authorization, collect a small real payment, verify the correct
   studio/account, receipt and ledger, then verify the refund. Test cancellation,
   disconnected Terminal, duplicate submit and delayed callback/recovery. A timed
   out browser request is not proof that no money was collected. Check status or
   retry the SAME uncertain request; do not manually record or create another
   payment until Square confirms its outcome.
   Also test partial payment, an additional purchase, explicit discount and manual
   credit. Verify exact charged money, receipt and list/calendar totals. Cancel an
   adjusted attempt and confirm neither money nor adjustment is recorded. Manual
   credit does not redeem a Wix balance or enforce future promo-code rules; staff
   must verify their authority separately. Zero-dollar/credit-only settlement is
   not a Terminal charge. Additional purchase amounts include any applicable tax;
   the original saved tax breakdown is not recalculated from today's settings.
7. Disable a device in settings or turn the Terminal flag off to stop new device
   collection. Keep webhook and recovery processing running for submitted attempts;
   do not reverse migrations or delete payment evidence after collecting money.

Local sandbox acceptance used the existing preview/database, synthetic $25 workshop
and party bookings, and Square's documented virtual devices. Success produced one
ledger entry, zero balance and a receipt. Customer cancellation produced no entry
and preserved the balance. No card/physical device or live account was charged.
Desktop Chromium responsive checks are not physical Android/keyboard/printer proof.

### Website and Existing Bookings

1. Use direct HTTPS buttons for Workshop booking and Party booking, pointing to the
   actual live school's `/bookings/<school-slug>/experience/` and
   `/bookings/<school-slug>/party/` routes. Retired popup previews are not integration
   targets. CSS/branding review must use her actual website once access is provided.
2. Record the old website links and the agreed switch time. Test the new URLs signed
   out, on a phone and tablet, including back navigation and payment return.
3. Reconcile future Wix bookings per studio/date/time before opening the same times
   in Pontora. Record booked count, selected designs, deposit paid and balance due.
   Decide which system owns each existing booking. Do not double-charge deposits or
   manufacture Square transaction evidence when importing an external record.
4. Until the export is available and checked, leave affected times unavailable or
   keep their bookings in the old system under an explicit staff handover. Do not
   invent an import mapping or claim historical migration is complete.
5. Gift-card and promo balances need their own reconciliation; booking imports are
   not proof that gift-card redemption is ready. These remain separate scope.

### Activation and Recovery Order

1. Record the deployed revision, migration state, managed backup and recovery window.
   Capture SBMC enrollment/billing and existing demo behavior as read-only baselines.
2. Finish the configuration checks for every intended live location. Run
   `check_workshop_launch <school-slug>` while intake remains paused. The November 4
   local follow-up removes the need to open intake just to inspect configuration;
   its output explicitly reports the unchanged intake state. Activate only in the
   controlled acceptance window and keep website links unchanged until acceptance.
3. Complete an owner-approved live transaction and refund, signed webhook delivery,
   email/attachment acceptance, staff prep and location isolation. No real charge is
   authorized by this runbook alone. Verify on the actual Android tablet.
4. Switch website buttons only after the existing-booking reconciliation and all
   required live acceptance checks pass. Monitor checkout failures, uncertain payments,
   refund recovery, email failures and staff reports during the first sessions.
5. If intake fails, disable only new native reservations and restore the recorded
   website links. Keep confirmation links, payment reconciliation and refund handling
   running. Never restore a database backup over newer payment activity as routine
   rollback; that would discard real financial evidence. Reconcile first and use a
   compatible fix-forward or code rollback with additive schema retained.

### Remaining Acceptance

The studio's live authorization and data, Wix export/access, final sender decision,
email content/triggers and physical-tablet acceptance still require the already
requested customer inputs. Managed backup availability is not a completed restore
drill. A restore rehearsal needs an approved isolated destination; do not overwrite
the shared production database or download customer records for convenience.

## November 10 Operational Readiness Follow-Up

Inspection performed November 10 Pacific / November 11 UTC against `6ebf7fe`.
This supersedes the older health-check/configuration observations below, not the
still-open customer acceptance gates.

| Area | Verified evidence | Remaining gate |
| --- | --- | --- |
| Deployed revision | Both web services and both maintenance jobs run `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c`. Demo automatically follows passing main CI; production auto-deployment is off. | Local launch-checklist and database-aware health changes still require release. |
| Isolation | Production web/job use `PORTAL_DEPLOYMENT_ENV=production`, demo web/job use `demo`. Database URLs differ between environments and match within each environment, as do signing/encryption keys. | Preserve these boundaries on every promotion. |
| Database access | Owner approved private-network-only production access. On November 11 at 07:20:33 UTC, production's external allowlist changed from `0.0.0.0/0` to `[]`, after verifying both consumers already use its internal URL. Demo already has `[]`. Production DB remains available. | Do not reopen external access for routine administration or the restore drill. |
| Scheduled jobs | Both run every five minutes and first execute `check_portal_deployment`. Six recent runs per environment succeeded. A newly started production run completed successfully at 07:25:48 UTC after the network restriction. Read-only production Schools and Audit pages also loaded successfully. | Add `--include-live-recovery` only at the approved live launch; both online and Terminal Square live flags remain false. |
| Monitoring | Workspace email alerts are enabled for failures; all four service notification overrides inherit defaults. An owner-approved, customer-data-free Sentry test event at 07:41:55 UTC reached the operator's Gmail inbox at 07:42 UTC, with matching event ID `62f571b3e3d14ebf9cdfb6f8a4fdc4c4`, issue `7841635784`. | This proves Sentry delivery, not a Render cron-failure email. Production cron has no direct Sentry DSN; its Render failure notifications are configured but were not deliberately triggered. |
| HTTP readiness | Both Render web services already target `/healthz/`. Local code now performs one read-only `SELECT 1`, returns 503 on a database error without exposing details, and disables caching. GET/HEAD and unsafe-method behavior are tested. | Deploy and verify this endpoint; the currently hosted endpoint still checks liveness only. |
| Recovery rehearsal | Owner-approved PITR copy created at 07:34:29 UTC for the 07:23:59 UTC production snapshot; observed available at 07:42:06 UTC. All 102 public tables matched row counts and content checksums; one production school, zero demos, zero unvalidated foreign keys. Copy stayed private with no app/jobs attached and was deleted at 07:43:31 UTC. | Refresh the moving recovery window before cutover. This is database recovery evidence, not live payment reconciliation or a complete application failover rehearsal. |

The restore used the same small paid tier as the source and existed for about nine
minutes, below the approved $1 ceiling at published prorated rates; the final
provider invoice was not inspected. No production connection or data was changed.
The restored database retained the source username/password but had a different
database name; validation used that name over Render's private network, in a
read-only repeatable-read transaction. No credentials or customer rows were exported.
The combined table-checksum digest was
`898ec5bb7bac8158c741555754bd2e047fb39546ceba0213b063e842c300d135`.
Local evidence is in `plushie-launch-readiness-20261110`, including `restore.json`,
the validation summary and monitoring event record. The approved Sentry test was
clearly labelled planned and did not cause an application failure.

The failed nine-case admissions test group has been repaired to inspect the active
status and accessibility attributes instead of matching a complete HTML string.
116 focused health/admissions/checklist/onboarding tests and 504 focused Plushie
booking, payment, refund, recovery, consent and preparation tests passed. These
results are not a substitute for the final full-suite release gate.

## November 6 Operational Readiness Inspection

Read-only inspection on November 6 Pacific (November 7, approximately 06:10 UTC),
against deployed `51cf29b`. No service, database, live flag, account or schedule was
changed. This is evidence gathering, not approval to activate production.

| Area | Verified evidence | Remaining gate |
| --- | --- | --- |
| Managed database | Render Postgres `student-enrollment-portal-db`, PostgreSQL 18, paid `basic_256mb`, available. Recovery API reports `AVAILABLE`, with `startsAt=2026-11-03T15:54:53Z`. | Actual isolated restore rehearsal and measured recovery time. Refresh the moving recovery window immediately before cutover. |
| Scheduled maintenance | `pontora-party-notifications` runs `python manage.py process_booking_maintenance --limit 20`, `*/5 * * * *`. Six runs ending between 05:45 and 06:10 UTC succeeded. Directly configured web/cron database URLs match; no credential values were printed or saved. | Add and verify `--include-live-recovery` only at authorized live activation. A successful Sandbox-only run does not prove live recovery. |
| Failure notifications | Workspace email notifications enabled for failures; cron override inherits the workspace default. Web service has a directly configured Sentry DSN; cron has no directly configured DSN. | Prove a failure notification reaches the responsible operator. Configuration is not inbox-delivery evidence. Environment-group inheritance was not inspected. |
| HTTP health | Hosted `/healthz/` returned HTTP 200 with `{"status": "ok"}`. Render's configured HTTP health-check path is empty. | Evaluate/configure an HTTP health check in a separately reviewed shared-service change; the current endpoint does not verify database access. No health setting was changed in this inspection. |
| Rollback | Intake-only pause, additive-schema preservation and original website-link restoration are documented above. | Rehearse against the intended live studio during the acceptance window. Do not roll the shared database back over newer payments or disturb SBMC. |

The live-recovery command filters payment orders to `purpose=booking`,
`environment=live`, connected Square accounts. Refunds use the original attempt's
order/account binding. Stripe enrollment/subscription orders are excluded; there
is no hardcoded school exception. The scheduler has no school argument, so confirm
all eligible live Square booking accounts before enabling it. Use the existing
`reconcile_booking_payments --school <slug> --environment live --provider square`
only for separately authorized, bounded manual recovery. Do not run it as a
readiness probe: it can settle pending money. The maintenance command exits nonzero
when a subtask fails and continues the other bounded tasks; this behavior is covered
by the focused maintenance tests.

### Isolated Restore Rehearsal Gate

Owner approval is required before creating a temporary paid database containing
the shared production data, including SBMC records. Until approved, this gate is
**pending**, not passed. `scripts/restore_demo_db.sh` seeds demo data; it is not a
backup restore and must not be used for this rehearsal.

1. Record the source database, recovery timestamp, deployed revision and migration
   state. Choose an available point at least ten minutes old. Agree temporary cost,
   access restrictions, retention and cleanup with the owner.
2. Restore to a new, isolated Render database. Never repoint web/cron services or
   change the source database. Review inherited IP access rules before using the
   copy. Do not attach email senders, payment workers, webhooks or public booking
   traffic to the restored data.
3. Validate schema/migration state and read-only aggregate integrity checks for
   school/location ownership, reservations, payment orders, ledger/refund totals
   and inventory allocations. Compare to the selected recovery point, not a moving
   production database. Do not export customer rows or credentials into evidence.
4. Record duration, results and restore limitations. Keep the original service/DB
   connections unchanged. Arrange owner-approved cleanup of the temporary database
   and credentials; a rehearsal is not a production switchover.

Provider references: [Render recovery and backups](https://render.com/docs/postgresql-backups)
and [Render health checks](https://render.com/docs/health-checks). Render restores
PITR into a new instance; an available backup does not prove the application can
recover. An empty HTTP health path uses TCP checks, not an application/database
readiness check.
