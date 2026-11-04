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

No schema migration or existing-school activation is included. If code rollback is
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
