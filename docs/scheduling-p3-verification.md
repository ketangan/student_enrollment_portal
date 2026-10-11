# P3: Shared Inventory and Staff Settings

Date: October 5, 2026. Status: **Ready for your test**. No push or deployment.

## Scope and Impact

- Optional tenant-scoped catalogs reuse the existing choice groups/options. Offerings opt in
  through a catalog assignment; no separate customer-specific stock engine or payment module.
- Physical stock is tracked by item and studio. Allocation follows the existing reservation
  lifecycle, including unresolved online payment attempts. Camp registration allocates one kit
  per participant for the whole camp; parties require quantities covering their group before reservation.
- Tenant transaction locks serialize stock adjustments, reservation allocation and consumption.
  Confirmations recheck stock. Stale counts, negative counts, cross-tenant items and adjustments
  below reserved quantities are rejected. Consuming twice is idempotent; cancellation does not
  restock already-consumed materials. All physical changes have an actor/reason audit record.
- Catalog changes cannot rewrite old selection snapshots. Switching a catalog after requests
  exist is rejected. The local seed preserves edits and reconciles historical workshop/camp
  allocations; unresolvable selections or pre-existing party reservations fail closed and roll back.
- Scheduling staff pages load enrollment design tokens and a scheduling-scoped CSS adapter.
  Settings navigation groups Inventory, Locations & rooms, Team access and Integrations.
  Customer Plushumi branding remains separate. Existing enrollment templates/CSS are untouched.
- Additive migration `scheduling/0019_shared_inventory` applied only to
  `pontora_bookings_m1a_preview`. Original local enrollment DB, production, SBMC and Kid Works
  are not migrated or reconfigured. Enrollment Request Demo test data is not reset.

## Automated Verification

Final command, using the repository's external-network/mail blocking fixtures:

```sh
PONTORA_TEST_DATABASE_URL=postgresql://njulka@127.0.0.1:5432/test_pontora_bookings_m1a \
COVERAGE_FILE=/tmp/pontora-inventory-final.coverage \
venv/bin/python -m coverage run --source=scheduling,core.payments,testing \
  -m pytest --reuse-db --maxfail=3
venv/bin/python -m coverage json --data-file=/tmp/pontora-inventory-final.coverage \
  -o /tmp/pontora-inventory-final.json
venv/bin/python -m testing.coverage_gate /tmp/pontora-inventory-final.json
```

Final application regression: **3,138 passed**, two existing warnings, **237.18 seconds**.
This run includes the final display-label assertions. The new tracker route/tests were added
after collection; all **64 tracker tests** were then run separately and passed. Model/migration
consistency and `git diff --check` passed; the existing duplicate Jazzmin template-tag warning remains.
The subsequent mobile-only history-table width/title adjustment passed all **17 inventory tests**
and a fresh browser check (361px page, 680px table scrolling inside its container, no page overflow).
No application Python behavior changed after the full run.

| New module | Combined statement/branch coverage |
| --- | --- |
| Inventory services | 98.75% |
| Inventory forms | 100% |
| Inventory models | 98.53% |
| Inventory views | 98.62% |
| Local inventory fixture/reconciliation | 97.59% |

Combined new inventory code: **98.64%** (506/513 statements and branches).
Overall measured scheduling/shared-payment/test-safety code: **99.49%**. Every measured
module passes the 90% gate. Coverage reports are `/tmp/pontora-inventory-final.json` and
`/tmp/pontora-inventory-final.coverage`; test output is `/tmp/pontora-inventory-final.log`.

Behavioral tests include shared studio stock, exhausted stock, expired holds, late payments,
unresolved payment attempts, consumed/canceled items, stock adjustment races, a real concurrent
last-item race on PostgreSQL, owner/staff access, tenant isolation, catalog CRUD/stale edits,
party quantities, camp kits, catalog reassignment guards, demo rerun preservation and migration
consistency. Settings navigation GET targets are checked; the new Payments shortcut was removed
because its target accepted only POST. Payments remain in Reservations.

## Browser Checks

- Local admin login, inventory filters/search and physical stock adjustments.
- Attempt to reduce Pasadena Blue Cat below reserved quantity: rejected with a clear error.
- Set available Blue Cat to zero: customer Blue color disabled for Cat; restored after test.
- Actual customer booking with fictional contact `Inventory QA`, Blue Cat, Pasadena October 10
  at 4 PM. Local successful payment simulation; saved reservation and preparation row verified.
- Mark materials used from reservation: physical count falls once, action disappears, preparation
  shows Materials used and excludes it from outstanding material totals.
- Party inquiry with two Blue Cats: staff review preserved the quantity and shared item.
  Marked Withdrawn after verification; it has no reservation, stock hold or payment.
- Mobile 390px inventory/party screens: no page overflow; wide inventory table scrolls internally.
- Desktop inventory and staff preparation: Pontora styling; customer picker retains Plushumi branding.
- Restart passed: saved counts, existing reserved materials and catalog edits remained.
  The fictional Blue Cat count was restored to 40 with an explicit QA adjustment reason;
  its consumed booking stays as a demonstration, and the original walkthrough hold is unchanged.
- SBMC and Kid Works public enrollment forms rendered separately with their original fields;
  no new form submissions or enrollment configuration edits were made.
- Browser tracker: P3 expands to manual steps, credentials, estimate and limitations, and
  its verification link opens the correct report. Final desktop/mobile screenshots saved locally.
- Mobile evidence: `/tmp/pontora-inventory-stock-final.png`. Stock history keeps readable
  columns instead of compressing names/reasons into fragments on small screens.

## Boundaries and Follow-Up

### October 5 Admin Usability Follow-Up

The first visual pass missed the reservation action/filter spacing and the catalog
link hierarchy. Python coverage did not test those visual qualities.

- Reservations now separates booking availability from record filters. An explicit
  checkbox switch and Save use the existing owner-only POST endpoint; Open/Paused
  reflects the saved state. The reservations table scrolls inside its container on mobile.
- Catalogs expose a single Manage catalog action and an initially collapsed assignment
  table, with separate workshop/camp and studio columns. Choice groups expand into
  aligned options with labeled Lucide edit icons; item-enabled status no longer claims
  that the physical stock is available.
- Catalog counts use an aggregate rather than a per-row count query. Saving stock
  returns to that item's studio. Clear search preserves the selected studio.
- Tests cover the actual checked-switch form payload, unchanged existing reservations,
  active filters, empty results, owner/viewer/scheduler controls, shared catalog counts,
  tenant isolation and the studio redirect (including an ignored external next URL).
- Browser checks: saved Paused then Open; three existing reservations remained;
  Payment follow-up showed its specific empty state; assignments expanded correctly;
  Edit Cat opened the expected option; icons loaded. Stock count saved unchanged at 40,
  returning to Pasadena. No real payment, mail, external integration or inventory
  quantity change occurred during this cleanup.
- Layout checks at 1280px, 390px and the native 361px panel found no page overflow
  or overlapping controls. Wide tables scroll internally. Locations & rooms and
  Integrations were also inspected; no enrollment styles or templates were edited.
- Evidence: `/tmp/pontora-reservations-usability.png` and `/tmp/pontora-inventory-usability.png`.
- No database migration, school configuration change, push or deployment in this follow-up.

Follow-up verification: **3,145 passed**, two existing warnings, **243.95 seconds**.
Ran the same full-suite coverage command above, replacing the output prefix with
`/tmp/pontora-admin-usability`. All measured modules pass the 90% gate; overall coverage
is **99.49%**, inventory modules **98.64%**, and the edited inventory views **98.64%**.
`makemigrations --check --dry-run` reports no changes; `git diff --check` is clean.
The existing duplicate Jazzmin template-tag system-check warning remains.

This is local demo readiness, not certification for real inventory or paid providers. The
fictional seed starts all design/color combinations at 40 per studio; Jasmine must validate
actual combinations/counts. Wix/Notion stock ownership and delivery remain M4 work.

Current scope has no purchasing, stock transfer workflow, low-stock notifications, automatic
consumption by clock, or multi-item recipes. Staff explicitly mark materials used; corrections
use an audited count adjustment. Image configuration accepts URLs, not a new upload pipeline.
Catalog setup is owner-only; session-edit staff may record consumption. Stock list is paginated
for usability but assembles tenant stock in memory; load testing and database-side pagination
are needed before claiming large-catalog scale. Coverage measures Python statements/branches,
not visual correctness, JavaScript execution coverage or real provider behavior.

See P3's expandable [manual steps](scheduling-implementation-tracker.md) and
[working demo guide](scheduling-plushumi-demo.md).

## October 6 Visual Parity Correction

The previous pass did not justify its visual-completion claim. Backend tests missed the
cross-page menu shift, Dashboard-only location selector, differing grid structure and absent
Recent Activity. This pass compared actual Enrollment Dashboard/Submissions templates and
browser-rendered styles, not just palette values.

### Changes and Impact

- Bookings' shared header uses stable center tracks across Dashboard, Reservations, Setup
  and Settings. Every staff page receives the location selector from one context helper.
- Valid selection persists in the session under the workspace ID. Each list applies its own
  tenant-scoped relation; invalid/foreign/stale selections never silently broaden the data.
  The selector on detail/edit pages returns to the appropriate list. Prep clears its old
  session when changing studios. Search/date/status filters remain separate from location.
- Shared catalogs remain shared; inventory quantities, offerings, camps, rooms, reservations
  and prep lists use the selected studio. Team Access/Integrations explicitly remain
  workspace-wide. Location selection is a display preference, not a new permission boundary.
- Dashboard uses Enrollment's greeting/KPI/icon structure and shows actual reservation/audit
  activity. Activity is bounded to eight displayed events with at most three extra queries;
  live scoped reservation IDs determine access, not potentially misleading audit metadata.
- Reservations reuses Enrollment's table frame, spacing, typography, initials, name hierarchy,
  Open actions, search and count conventions. Customer/Scheduled sorting is functional.
  It intentionally does not expose free-form status edits or nonfunctional bulk controls.
- Prep counts and their destination share a predicate. Consumed materials are excluded.
  Search results do not change the location-wide Total/Needs payment review cards.
- Styling remains inside the booking-admin scope. Shared Enrollment styles/templates,
  production school configuration, billing runtime and database schema were not edited in
  this correction. Existing unrelated worktree changes were preserved.

### Automated Evidence

Final isolated PostgreSQL run: **3,272 passed, two existing warnings, 251.80 seconds**.
No runtime edits were made during this final coverage run.

```sh
PONTORA_TEST_DATABASE_URL=postgresql://njulka@127.0.0.1:5432/test_pontora_bookings_m1a \
COVERAGE_FILE=/tmp/pontora-parity-final.coverage \
venv/bin/python -m coverage run --source=scheduling,core.payments,testing \
  -m pytest --reuse-db --maxfail=0
```

Coverage: **99.54%** combined Python statement/branch coverage; every measured module meets
the 90% gate. Activity, location preference, workspace, reservation queries and base context
are **100%**; reservation views are **99.47%**. Seven admin JavaScript tests pass at **100%**
line/branch/function coverage. Tests cover session persistence/reset, tenant isolation, stale
locations, nine staff destinations, counts/drilldowns, camps/stock/prep, bounded activity,
spoofed audit metadata, escaped customer names, sorting and existing feature/permission gates.

Evidence: `/tmp/pontora-parity-final-full.log`, `/tmp/pontora-parity-final.coverage`,
`/tmp/pontora-parity-final-coverage.json`. The per-module coverage gate and `git diff --check`
pass. Python coverage excludes test and migration files; it is not visual or provider proof.

### Manual Evidence and Limits

- Desktop 1280px: compared live Enrollment and Bookings typography, KPI geometry, table
  headers/cells, initials, action sizes and spacing. Header center/selector position remain
  stable across Dashboard, Reservations, Setup and Settings.
- Pasadena -> Reservations -> Setup -> Settings retains the selection; New York shows
  its own sessions and empty reservation activity; All locations restores the full list.
  Prep location changes reset the selected session. Search, clear, date-range errors,
  sorting and the To prepare drilldown were exercised without changing money or stock.
- Native 362px panel: tested Dashboard, Reservations and Inventory, mobile menu/keyboard,
  theme switching (restored Clean), visible location selector, date errors and clear filters.
  Wide grids scroll inside their frame; page width remains 362px. No browser console errors
  were captured on the desktop verification tab.
- Responsive viewport overrides did not apply in this browser session. This correction
  verifies the actual 1280px desktop and native 362px panel, not a fresh 901px breakpoint run.
- Dashboard activity currently covers reservations and their listed audit events, not every
  possible lesson/tour or integration event. Team/integrations and catalogs remain shared.
  Public customer booking pages were not restyled. No provider integration is certified here.
- Screenshots: `/tmp/pontora-parity-dashboard.png`, `/tmp/pontora-parity-reservations.png`,
  `/tmp/pontora-parity-mobile-dashboard.png`, `/tmp/pontora-parity-mobile-reservations.png`.
  Enrollment references: `/tmp/pontora-enrollment-reference-dashboard.png` and
  `/tmp/pontora-enrollment-reference-grid.png`.

Port 8002 refreshed on the isolated preview database. No provider calls, push or deployment.
The browser tracker contains repeatable P3 manual steps 19-24.

## October 6 Reservation Workspace

### Scope and Impact

- Reservations no longer has a global Workshop Prep menu item. Each booking has
  Reservation / Workshop Prep tabs, with a list-level readiness indicator linking to it.
- Compact studio-local dates/times, booking contact and studio are separate from Money.
  Room, participant selections, party notes, readiness and material usage are in Prep.
- Money is ordered Total, Deposit, Received, Balance, Refunded. Received is gross paid;
  retained money is shown after refunds. Canceled bookings do not present a collectible balance.
- Explicit cancellation choices and reason replace the checkbox. Cancellation and refund
  authorization are atomic; provider requests run after commit through the existing shared
  payment service. Deposit/balance payments can produce separate refunds; duplicate and
  concurrent submits cannot approve the same refund twice. Failure never fabricates returned money.
- The staff offline-settlement form, form class and HTTP handler are removed. Internal
  demo settlement and the shared ledger remain for the existing simulator/other modules;
  historical payment records are preserved. No alternate payment engine was added.
- Only exact local-simulator receipts qualify for simulated refunds: loopback named preview
  database, DEBUG, enabled demo/capabilities, demo source snapshot, exact payment key/reference,
  amount and offline channel. Ordinary external receipts still fail closed.
- The paginated Audit log merges reservation changes, ledger entries, checkout/refund records
  and material consumption. Each source is bound to this reservation/order, with actor/time
  and escaped text. Existing audit data is reused; previously unrecorded events are not invented.
- Changes stay in Bookings runtime/templates/styles/tests and documentation. No shared
  Enrollment template, stylesheet, payment implementation or SBMC configuration was edited
  for this request. No model/schema change, external provider call, push or deployment.

### Critical Review

The review caught and fixed a GET/POST prep URL collision, JSON lookup behavior that could
hide prep audit events, stale tests referencing removed controls, dark-theme contrast and
handling for old records with missing email. Cancellation tests cover authorization/CSRF,
tenant isolation, active checkout/refund blocking, split payments, prior partial/full refunds,
rollback on failed authorization, concurrent duplicate submits and failed provider results.
Preparation tests cover workshop/camp/party choices, other-booking isolation, consumed stock,
stale edits, and list/audit state. List preparation indicators use a database annotation,
not one query per row. Audit queries are bounded and paginated.

### Manual Evidence

- Desktop 1280px and native 362px phone layout: details, separate money section, scoped prep,
  location control and header alignment inspected. Page scroll width equals viewport width;
  only wide tables scroll. Clean and Midnight checked; original Clean preference restored.
- Created fictional `Reservation QA October 6` through the customer demo, selected one
  Blue Cat / Adult participant and completed simulated checkout. No email/provider request.
- In its prep tab, verified only Alex QA appeared, then marked prepared and reopened.
  Material remained Reserved, money unchanged, and both actions appeared in Audit log.
- Chose cancel with refund, entered a reason and confirmed. The local $65 receipt was
  refunded once, booking canceled, balance Not due, list prep Not required. Record retained
  as a historical QA example; no physical stock was consumed. Existing bookings were not edited.
- The unchanged rehearsal booking shows two saved participants and historical material use.
  No console errors observed on the desktop verification tab.
- Screenshots: `/tmp/pontora-reservation-details-desktop.png` and
  `/tmp/pontora-reservation-details-mobile.png`.

Repeatable user steps are in P3 steps 7-8 and 25-27 of the browser tracker. Live Stripe,
Wix and Notion acceptance remain separate gates; mocked/local checks are not provider proof.

### Automated Results

- Full regression: **3,304 passed**, two existing warnings, **259.03 seconds**.
- After the final missing-contact fallback and accessible prep-link labels: **262 focused
  tests passed**, **25.67 seconds**, covering reservation/demo/navigation/tracker behavior.
- New `cancellation`, `reservation_audit` and `reservation_workspace` modules: **100% combined
  statement/branch coverage**. Reservation views: **99.50%**; recovery views: **100%**;
  experience simulator service: **99.13%**. Overall measured Python coverage: **99.54%**;
  every measured module passes the 90% gate. This is not a claim about CSS or live providers.
- Seven admin JavaScript tests passed, 100% line/branch/function coverage. Migration dry-run
  reports no changes; existing duplicate jazzmin template-tag warning remains. Whitespace checks pass.
- Full log: `/tmp/reservation-workspace-verified.log`; final focused log:
  `/tmp/reservation-workspace-final-checks.log`; merged coverage:
  `/tmp/reservation-workspace-verified.coverage` and `/tmp/reservation-workspace-verified.json`.

The final contact fallback was tested in the focused run after the full regression. No new
Python behavior was added after that run. The isolated preview was restarted and the final
screens checked again. No request to commit or push was made for this change.

## October 6 Completion and Participants

### Scope and Impact

- Shared Reservation details and Participants & selections partials keep both tabs aligned.
  Saved group IDs determine columns, with saved-name fallback for historical data. Missing
  choices display Not selected, text is escaped, and choices are not looked up from mutable catalogs.
- Prep's summary is a native collapsed disclosure. Room stays in Prep. Material / Quantity /
  Stock status replaces the redundant studio column; allocation locations still govern stock.
  Payment summary replaces Money and Preparation Pending replaces To prepare.
- The record-header Workshop completed action uses a GET confirmation then CSRF-protected POST.
  A profile-level transaction validates material completeness, consumes unused allocations,
  saves Completed and writes actor/time history together. Failures roll back all effects.
  Concurrent/repeated completion and previously consumed stock never deduct twice.
- Completed retains historical seat/room claims, leaves the preparation queue and disables
  cancellation/preparation reopening. Owner/scheduler access and tenant/capability checks apply;
  viewers cannot mutate and visits are excluded. Camps/parties reuse the same completion service.
- Completion changes no money. Remaining balances can be collected after completion, and late
  payment callbacks cannot reopen the booking. Existing excess-refund rules remain; broader
  service refunds are deliberately blocked because this quote ledger has no credit-adjustment
  model and would otherwise turn refunded money back into a payable balance. No second payment
  implementation or change to shared core payment code.
- Scheduling migration 0021 extends the state constraint/choices only. Existing rows, ledger
  entries and school plans are preserved. Applied only to disposable test and isolated bookings
  preview databases. Reversal is not a business undo: Completed rows require explicit handling
  before removing that state. No Enrollment templates/settings or SBMC configuration changed.

### Verification

- Full application regression: **3,327 passed**, two existing warnings, **272.45 seconds**.
- Final focused run after refund-safety and table polish: **412 passed**, **42.48 seconds**,
  covering completion, inventory, both reservation tabs, customer demo, payments/refunds,
  camps, preparation, dashboard parity and tracker rendering.
- Completion, participant read model, preparation, booking checkout, reservation audit and
  dashboard activity: **100% combined statement/branch coverage**. Reservation views: **99.56%**.
  Overall measured Python: **99.53%**; every measured module passes the 90% gate. CSS/browser
  rendering and actual provider compatibility are not represented by this metric.
- Seven admin JavaScript tests pass at 100% line/branch/function coverage. Migration dry-run
  finds no missing schema changes; existing duplicate jazzmin template-tag warning remains.
- Unit/integration cases include stock-location isolation, shortages/missing allocations,
  rollback on audit failure, duplicate/concurrent submissions, authorization/CSRF, foreign
  records, disabled capability, visit isolation, late balances, excess refunds, escaped/missing
  participant choices, preserved migration data, completed filters/receipt and audit history.
- Self-review found and corrected the initials component mismatch, completed receipts falling
  back into checkout, late payments potentially reopening completion, and the refund/balance
  accounting risk above. Wide tables scroll inside their frames, not the whole mobile page.

### Browser Rehearsal

Created **Workshop Completion QA** through customer selection/review and local simulated checkout.
One fictional adult participant selected Blue Cat. In staff Prep, opened/closed the shared summary,
checked the separate Design/Color/Age columns, then marked prepared. Materials remained Reserved.
Confirmed Workshop completed: Pasadena stock fell from **38 to 37**, with **one** movement and
**one** completion event; the **$65** payment was unchanged. Status became Completed in details/Prep,
and the customer receipt remained completed without a new-checkout button after server restart.
Existing user-created bookings were not edited.

Desktop **1440px** and mobile **390px** inspected; page scroll width equals viewport width.
No console errors/warnings observed. Screenshots: `/tmp/pontora-workshop-prep-desktop.png`,
`/tmp/pontora-workshop-prep-mobile.png`, `/tmp/pontora-workshop-prep-completed.png`, and
`/tmp/pontora-reservation-completed-mobile.png`.

Logs: `/tmp/reservation-completion-full.log`, `/tmp/reservation-completion-final.log`.
Merged coverage: `/tmp/reservation-completion-full.coverage`, `/tmp/reservation-completion-final.json`.
P3 steps 28-31 provide repeatable user checks in the browser tracker. Port 8002 refreshed.
No live Stripe/Wix/Notion calls, real payments/email, production changes, commit, push or deployment.

## October 6 Grid and Demo Header Polish

Scope: scheduling presentation only. Participants & selections is inside the initially closed
Reservation details disclosure on Workshop Prep; it stays visible on the Reservation tab.
Materials and room information stay outside the disclosure. The reservation grid uses eight
explicit column widths, normal header wrapping, shrinkable customer names and wrapping prep/status
labels. Names and selections are not truncated. Narrow screens scroll the table, not the page.

The navigation row follows Enrollment's 60px height plus its 1px border. The mobile Location
control retains its own additional row. Plushumi's original white wordmark is displayed on a
dark backing for contrast. The blue sample-data strip and yellow two-day trial notice appear
only when the existing isolated-demo availability guard passes for `plushumi-demo`. There is no
new trial state, countdown, billing action, write, payment restriction or migration. Shared
Enrollment templates/styles, school records and public customer screens were not edited.

### Verification

- 110 focused rendering, navigation, completion and isolation checks passed.
- **3,340 full regression tests passed**, two existing warnings, 269.89 seconds.
- Every measured Python module meets the 90% combined statement/branch gate; overall **99.53%**.
  The changed `scheduling/views.py` context module is **100%** covered.
- Seven admin JavaScript checks passed at 100% line/branch/function coverage.
- 64 tracker tests passed with localhost permission; the first restricted run could not bind
  its temporary HTTP test server. No application failure was involved.
- No pending model migrations; existing duplicate `jazzmin` template-library warning remains.
- New regression checks cover closed disclosure ancestry, visible materials, unchanged details
  tab, long/escaped names, eight semantic columns, demo branding across six routes and exclusion
  for another business, non-demo school, nonlocal database and disabled demo flag.
- Live browser checks at **390, 901, 1024, 1280 and 1440px**: zero cell/page overflow. The table
  fits at 1280/1440px; narrower sizes scroll inside the region. Arrow-key scrolling works.
- Checked logo, both strips, consistent navigation placement, mobile menu, and prep disclosure
  open/close. Materials remain visible while participant choices collapse. No browser errors.
- A separate synthetic HTML stress preview could not be opened because browser policy disallows
  `file:` URLs. Long values were verified in rendering tests, but synthetic visual stress data
  was not browser-verified. Responsive browser measurements above use the actual live demo records.

Screenshot: `/tmp/pontora-reservations-polished.png`; expanded mobile prep:
`/tmp/pontora-prep-expanded-mobile.png`. Test log: `/tmp/reservation-polish-full.log`;
coverage: `/tmp/reservation-polish-full.json`. Repeatable checks: P3 steps 29, 32-33.
Port 8002 refreshed. Existing bookings were only viewed, not modified. No push or deployment.

## October 6 Enrollment Grid Comparison

Follow-up: the fixed demo trial text is now seven days. Location's visible caption is removed;
the associated visually hidden label remains, including on tablet widths. There is no trial logic.

Compared the actual Enrollment Request Demo Submissions page with Plushumi Reservations at the
same 1440px viewport, using rendered styles rather than only source or screenshots. Differences
were measurable: scheduling had a 48px narrower content area, 64.5px rows, 28px Open buttons,
darker row dividers and darker/larger workshop/date text. Its outer scrolling card also differed
from Enrollment's outer card plus inner scroll area.

The scheduling-only adapter now matches Enrollment's 1248px outer content width, normal line
height, 24px Open buttons with 4px/10px padding, muted 13px workshop and 12px date text, and light
row dividers. Reservation rows are about 54px because workshop/location and date/time use two
lines, versus Enrollment's 52.5px single-line sample row. Sort arrows use the same subdued/active
contrast. Read-only status labels use compact sentence case, without inventing an inline editor
that bypasses payment, cancellation or completion rules. Header tracking stays at zero.

Self-review caught mobile page overflow caused by absolutely positioned accessible table labels
after adopting the new wrapper. Positioning the inner scroll container relatively corrected it.
Final live-browser checks at 390/901/1024/1280/1440px found zero cell/page overflow. Keyboard
horizontal scrolling, Customer/Scheduled sorting, location persistence into Setup, Inventory's
mobile layout, and Midnight colors were checked. Location and theme were restored to All locations
and Clean. No record data was changed. Enrollment's templates and styles were only read.

Screenshot: `/tmp/pontora-grid-enrollment-parity.png`. Repeatable checks: P3 steps 32-34.

Automated verification: **3,340 full regression tests passed**, two existing warnings, 281.83
seconds; 134 focused rendering/navigation/reservation checks, seven JavaScript checks and 64
tracker checks passed. Every measured Python module meets the 90% coverage gate. Log:
`/tmp/grid-parity-final-full.log`; coverage: `/tmp/grid-parity-final-full.json`.
No new runtime Python, migration, provider call, commit, push or deployment. Preview refreshed.

## October 6 Workshop Setup Cleanup

### Scope and Impact

Workshop list and detail use Enrollment-style grid containers, column sizing, compact status
labels and icon actions. Individual workshop pages hide the Setup submenus and promotional
description. Public schedule, Preparation and catalog-editor shortcuts are removed from these
pages. The saved public description remains intact. Materials Catalog replaces the optional
label; it is required in catalog-using businesses, including Plushumi. Non-material businesses
and historical uncatalogued workshops with saved selections retain their existing behavior.
The question about universally requiring inventory remains recorded under Open Questions.

The request/inquiry fields and their form handling are removed from Workshop setup. Stored
flags, existing inquiry routes and booking records are deliberately retained: reservations
and party workflows depend on those records. Posting the removed fields cannot enable them.
This is not a destructive removal of the shared booking model or of Parties functionality.

Inline session Add/Edit reuses the existing setup service, permission checks, tenant lock,
revision token, room/capacity/booking constraints and audit writer. It adds no second write
path or client-side source of truth. Invalid saves keep submitted values and errors inline;
valid saves return to the same workshop row. Session IDs are bounded and scoped to the chosen
workshop and tenant. Changing the header's location returns to the location-filtered list.

View inventory filters stock in the database by both catalog and location. Search, clear and
pagination preserve both. A mismatched location returns an error rather than silently showing
other stock. Foreign/malformed workshop IDs are rejected. Choosing a different location exits
the individual-workshop view explicitly. No catalog-management panel is shown in this view.

Only scheduling code/templates/styles/tests and these repository documents changed in this
pass. Enrollment/SBMC assets and flows were not edited. No schema change or provider call.

### Verification

- **3,370 full regression tests passed**, two existing warnings, 276.52 seconds.
- **166 final focused workshop/navigation/parity/tracker checks passed** after the mobile
  CSS and documentation refinements; seven admin JavaScript checks passed.
- Every measured module passes the 90% combined statement/branch gate. Overall **99.53%**;
  forms and main scheduling views **100%**, inventory views **98.70%**, inventory **97.56%**.
- Tests cover required/foreign catalogs, non-material and historical compatibility, removed
  flag tampering, owner/scheduler/viewer permissions, CSRF, invalid IDs, foreign sessions,
  stale edits, DST/nonexistent times, capacity/room/confirmed-booking guards and stock scoping.
- Browser: opened a session inline, rejected capacity 13 against a room of 12, corrected it
  to the original 12 and saved back to the same row. Existing reservation data was preserved.
  Verified catalog/location inventory scope and absence of catalog editor controls. The form's
  required catalog and removed switches were inspected; empty-catalog rejection is automated.
- Browser at **390, 900 and 1280px**: no page overflow; table scrolling is contained. New and
  existing inline editors are usable on phones. Self-review caught the sticky header covering
  the new-session heading; scroll margin now places it below the header, verified after navigation.
  The final desktop form's inputs have no content overflow. Browser viewport restored afterward.
- No pending model migration. Existing duplicate `jazzmin` template-library warning remains.

Coverage artifact: `/tmp/pontora-workshop-setup.json`. Screenshots:
`/tmp/pontora-workshop-inline.jpg` and `/tmp/pontora-workshop-mobile.jpg`.
Manual steps: P3 35-38. Preview port 8002 refreshed; no commit, push or deployment.

## October 6 Workshop Duration and Inventory Concept

### Scope and Review

Workshop sessions now use a start datetime plus duration in minutes. The server derives the
end in UTC elapsed time, so spring/fall DST crossings and overnight sessions are consistent.
Posted end times cannot override that calculation. Existing session seconds/microseconds and
durations are preserved when staff only edit capacity. Confirmed bookings, room conflicts,
tenant scope, revision checks and audit remain on the existing setup write path.

Impact review and the first full test run caught an unintended coupling: preschool SlotForm
inherits the original SessionForm. The workshop editor is now a separate subclass, leaving
the original form and visit behavior intact. A browser validation pass also found a misleading
booked-session error alongside an invalid duration; retaining the unchanged original endpoint
during failed validation fixed that. Both paths have regression coverage.

### Verification

- Final full suite: **3,387 passed**, two existing warnings, 277.07 seconds.
- Seven admin JavaScript checks passed. Every measured Python module exceeds the 90% combined
  statement/branch gate; overall **99.51%**, forms **100%**, setup **98.28%**.
- Added duration regressions for 90-minute/overnight/DST calculations, invalid and excessive
  durations, ignored forged end values, legacy precision, room conflicts, booked-time protection
  and consistent inline/legacy edit routes. Existing visit tests pass unchanged.
- Browser: invalid zero duration leaves only the duration error; correcting to the original
  120 saves successfully without changing the booked session. Field/control top edges align
  at 1024 and 1280px. At 390px they stack with no page overflow. Preview refreshed.
- No pending migration. The existing duplicate jazzmin template-library warning remains.
- Coverage: `/tmp/pontora-duration.json`; screenshot: `/tmp/pontora-session-duration-laptop.jpg`.
  Repeatable manual steps: P3 39. No production change, live provider call, commit or push.

### Separate Inventory Proposal

The interactive concept was designed from The Plushie Workshop's public homepage, product,
locations, party and FAQ pages, not from the existing inventory UI. Its coral branding and
Plus Jakarta Sans typography follow the public site. Yarn, stuffing, choices and accessories
are grounded in that site; stock levels, usage recipes and camp allocations are illustrative.

Stock, Workshop use, Sessions and Activity show physical versus reserved versus available
quantities, workshop dependencies, per-session allocations, receiving deliveries, corrected
counts, item creation with usage rules, completion consumption and cancellation releases.
Raw materials and prepacked kits are both proposed item types, pending Jasmine's confirmation.
Physical-count corrections can expose a shortage; completion is blocked until it is resolved.
This proposal does not replace or alter the current inventory module.

Browser exercised item creation/usage mapping, delivery receipt, completion without double use,
cancellation release without changing on-hand stock, accurate shortage recording and blocked
completion, search, category/restock filters, location separation and narrow layouts. A 390px
table scrolls within its own container; no page overflow. Photo upload is a preview affordance,
not a backend upload feature, and was not part of this browser verification. No integrations
or persistent inventory writes occur in the concept.

## October 6 New Workshop Bookability

### Cause and Impact

Young Horizons (local Offering 16) and its two future Charlotte sessions were published,
but `accepts_requests` remained false. The prior removal of the unpaid-intake controls left
the customer demo incorrectly gated by that flag. Seeded workshops set it true, masking
the admin-create-to-customer-booking gap in the existing tests.

Native reservation intake now checks publication, date, enabled profile, reservation
capability, intake switch and usable choices independently of unpaid intake. The shared
participant writer has a server-only reservation mode; public request POST fields cannot
activate it. Token, tenant, inventory, locking, capacity, payment and idempotency checks
remain on their existing paths. Default unpaid-request behavior is unchanged. No new
payment implementation, migration, production data update or Enrollment/SBMC edit.

Removing the old flag exposed camp dates in the first regression run. The native single-
workshop path now excludes camp sessions in both listing and submission. Camps still use
their existing all-dates registration flow. Preparation can include newly created workshops
without enabling unpaid inquiries. The picker separates workshops by IDs within a studio,
shows the chosen name and actual duration, and clears stale session links on changes.

### Verification

- Reproduced the defect with an admin-create/publish-to-booking integration test before fixing it.
- Focused suite: 142 tests passed. Twelve JavaScript tests passed, including five new tests
  for workshop/location isolation, reset behavior, capacity, duration and empty availability.
- New end-to-end coverage books an admin-created workshop, verifies one payment/booking on
  retry and confirms its preparation roster. Negative cases cover draft/past/disabled/paused,
  missing choices/capability, forged public intake mode and camp isolation.
- Browser: Charlotte lists Young Horizons with October 12 and 30. Both open the correct
  participant page; October 30 rejects three participants against capacity two. October 12
  reached review with fictional details. No preview reservation or payment was created.
- Desktop 1280px and phone 390px layouts have no page overflow. Saved screenshot:
  `/tmp/pontora-young-horizons-booking.jpg`. Existing workshop/session settings are unchanged.
- Full regression: **3,397 passed**, two existing warnings, 279.50 seconds. Overall combined
  statement/branch coverage **99.51%**; shared participant intake **98.83%**, experience service
  **99.17%**, experience views **100%**. Every measured module exceeds 90%. Coverage artifact:
  `/tmp/pontora-workshop-bookability.json`.
- 64 tracker checks passed. No pending migration; the existing duplicate jazzmin template
  library warning remains. Preview refreshed on port 8002. No commit, push or deployment.
