# Plushumi Working Demo

Status: the working demo is hosted on the existing shared service. Shared sandbox Stripe
activation is being verified (see below). Wix/Notion remain off. This is not a finished
self-service workshop product.

[Customer booking](http://localhost:8002/bookings/plushumi-demo/experience/) |
[Reservations and preparation](http://localhost:8002/bookings/plushumi-demo/reservations/) |
[Catalog](http://localhost:8002/bookings/plushumi-demo/catalog/) |
[Materials Inventory](http://localhost:8002/bookings/plushumi-demo/settings/) |
[Admin workspace](http://localhost:8002/bookings/plushumi-demo/)

Local studio admin: `plushumi-admin` / `admin`. This account has scheduling access only,
not enrollment or operator access. Use localhost while Enrollment Request Demo stays on
127.0.0.1 so the two school logins do not overwrite each other's browser cookies.

Dashboard's **Location** selector is beside the date/account controls (on its own row
on phones). It filters upcoming events and attention counts; count links and the workshop
list preserve the selected location. **All locations** restores the workspace overview.
The setup checklist remains workspace-wide. Selection does not change stock, reservations,
payments, staff permissions or the customer booking flow.

Staff tables, badges, filters, forms, buttons and pagination now use the enrollment visual
system, not only its header. Customer pages retain Plushumi branding. Wide tables scroll
inside their container on phones.

## Pasadena Booking Rehearsal

### Preview Navigation and Copy Cleanup

The first comparison option now opens the **actual booking page** as a full page.
Previously shared `integrations/inline.html` links redirect there; it is no longer labeled
an inline-embed demonstration. The popup option still opens the actual same-origin flow
from its own page, with honest loading/retry behavior. Neither option recreates the form.

Removed the preview ribbon, repeated Wix/Notion explanations and nested full-page wrapper.
Keep concise simulated-payment/test-card notices at checkout; no real connection is implied.
The original six-screen storyboard is untouched. No payment service, schema, feature flag,
SBMC page, production data or provider configuration changes in this cleanup.

Manual checks:
1. From the comparison hub choose **Open booking page**: the address changes to
   `/bookings/plushumi-demo/experience/`, with one brand header and no frame/container.
2. Go back and choose **View popup preview**, then **Book Your Workshop**. Check loading,
   real studio/session choices, next-step navigation, close/Escape and reopen with selections retained.
3. At desktop, laptop and phone sizes, verify no horizontal overflow or redundant preview banners.
4. Locally rehearse participant choices, review, simulated decline/success and confirmation.
   The hosted demo remains a simulation; no Stripe charge or customer email is needed.
5. Open the local tracker and expand **B1** for outstanding shared Stripe setup and recovery checks.

Browser verification: 1280px, 1024px and 390px passed. The actual local booking flow passed
review, simulated decline, success and confirmation with one fictional `Preview Cleanup QA`
reservation. Updated comparison thumbnails are screenshots of the real pages.
Automated verification: 3,705 full Python tests, 185 focused demo/tracker tests and
49 JavaScript tests passed. The changed redirect and popup script have 100% statement/branch
coverage; every measured Python module exceeds 90%. Migration drift check passed.

Release-gate follow-up: GitHub run `38119809060` crossed Pacific midnight and exposed
an existing reservation test that rejected a date anywhere on the page, including a
correct audit-log date. Reproduced locally and corrected only the test: assert the
exact scheduled date/time markup for same-day and overnight sessions, with a separate,
fixed audit date. Deployment remains gated on a passing full CI run.

October 11 footer follow-up: customer booking screens now show **Back to demo options**
instead of Parties & camps / Studio team. The link navigates the top-level page, including
from the popup, so the comparison hub is never nested inside the booking frame.
Browser checks at 1280px and 390px passed for both full-page and popup navigation.
Regression coverage checks the footer on selection, review, checkout and confirmation,
plus the hosted page. The 186 focused demo/payment/tracker tests, all 3,706 Python tests
and 49 JavaScript tests passed; every measured Python module meets the 90% coverage floor.
No payment logic, provider settings, schema or other-school pages were changed.

### October 11: Hosted Booking Sandbox

Release verification in progress. This supersedes the older local-only Stripe notes below.
User approved Pontora's existing sandbox for demos and declined an additional paid recovery job.
Real customer funds still require the merchant-owned account design; this change enables test
payments only and must never route a customer's live revenue through the shared demo sandbox.

Operator setup: **Django superadmin > Payments > Payment accounts > Add**. Select the demo
business and **Pontora shared sandbox (demos only)**. Leave Connected test account ID blank;
the configured sandbox identity is applied automatically. Enable test checkout and save.
Only superadmins can do this. Existing bindings cannot be changed or deleted through that screen;
checkout can be disabled without discarding pending payment verification.

One-time deployment settings: `PAYMENTS_STRIPE_TEST_SECRET_KEY` (test key only),
`PAYMENTS_STRIPE_TEST_ACCOUNT_ID`, `PAYMENTS_STRIPE_TEST_WEBHOOK_SECRET`, and
`PAYMENTS_STRIPE_TEST_METHOD_CONFIG` (a dedicated, non-default, card-only test payment-method
configuration). Do not edit Stripe's existing default or live method configurations. The separate
platform-scope test endpoint is `/bookings/stripe/test/webhook/`; events are
`checkout.session.completed`, `checkout.session.expired`, and `payment_intent.payment_failed`.
No per-school credentials or customer-name checks exist in the reusable payment components.
The prior `BOOKING_DEMO_STRIPE_SCHOOL` switch is removed. `STRIPE_MODE=live` continues to serve
existing Pontora subscriptions; no shared Stripe global key is mutated by demo checkout.

Apply payment migration **0004** before activation. Existing payment accounts retain connected
scope; only demo accounts explicitly configured for the platform sandbox share its account ID.
Order/attempt identity, amount, environment and signed event checks still isolate each business.

Manual verification: follow the test-card rehearsal below through the hosted comparison hub,
including decline then success, unpaid back/retry, duplicate refresh, and webhook confirmation
without browser return. Inspect both desktop and phone layouts. No real card should be entered.
Recovery fallback: `python manage.py reconcile_booking_payments --school plushumi-demo --limit 20`.
No paid job is provisioned. Automatic scheduled recovery remains a prerequisite for an unattended
paid production rollout; it is not claimed complete for this demo. Broader operator-email and
subscription recovery work remains tracked under B1.

Pre-release verification: 3,742 Python tests and 49 JavaScript tests passed. Measured Python
statement/branch coverage is 99.48%; every measured module exceeds 90%. New account-scope code
is 100% covered and the superadmin form is 98.68%. Desktop and fresh 390px mobile admin checks
passed for creating, saving and disabling the setting. Review also corrected an unrelated flaky
test assertion that matched a student's short first name inside a random CSRF token; enrollment
runtime code was not changed. Render's verified startup runs migrations before Gunicorn. A private
pre-migration database backup was taken. Hosted payment evidence is still pending deployment.
An additional 311 billing/payment tests passed with independent gateway coverage: all modified
test-checkout transport lines/branches are covered. That targeted run covers 86.34% of the entire
pre-existing billing gateway; it is not a claim of full subscription-gateway coverage.
Final review found and fixed a calendar/payment cutoff mismatch: online demo availability now
uses the existing shared 40-minute checkout cutoff, including direct links and stale reviews.
The offline simulator keeps its existing availability. All 118 affected demo/payment tests passed;
the final release must pass full CI with this follow-up before deployment.

Hosted verification caught a provider compatibility failure: Stripe rejected the old
`payment_method_types` request field. Booking checkout now uses the dedicated test method
configuration, and validates the resolved card-only methods on both checkout creation and
reconciliation. Unsupported/deferred methods cannot expose a checkout link or settle through
recovery. Stripe accepted and immediately expired an unpaid API probe using this configuration;
actual browser decline/success verification is still pending the corrected release. Plushumi's
account is temporarily disabled during this correction. Live subscription checkout is unchanged.

Correction verification: **3,748 Python tests passed** (351.69 seconds; two existing warnings),
**49 JavaScript tests passed**, and migration drift/diff checks passed. Measured scheduling/shared
payment coverage is **99.48%**, with every measured module above 90%; checkout is 100% and
operator settings 98.75%. A fresh 390px browser saved and disabled the shared sandbox setting
without overflow. These are pre-release results, not a substitute for the hosted card rehearsal.

### October 10: Real Stripe Test Checkout (Historical Local Rehearsal)

October 11 hosted verification: this is still a local-only capability. Render has no
dedicated booking test key, test webhook secret or Plushumi merchant binding, and the
demo Stripe flag is off. Hosted pages intentionally use the simulator. Track the
separate hosted-sandbox activation under B1; shared live subscription settings stay unchanged.

The local [inline preview](http://localhost:8002/integration-preview/inline.html) and
[modal preview](http://localhost:8002/integration-preview/modal.html) now offer actual
Stripe test Checkout when the isolated preview is launched with `serve.py --stripe-test`.
This is explicitly a **Pontora-managed payments alternative**, not a Wix integration.
Stripe displays Pontora's existing test account as **New business sandbox**. This describes
the earlier local-only Stripe rehearsal, not the hosted release: the shared hosted demo
uses simulated checkout and has no real Stripe connection enabled.

Manual rehearsal:
1. Choose Pasadena, Cozy Creations, a future session and participant selections.
2. Review, continue, then select **Continue to Stripe Checkout**. Stripe opens at
   the top level, outside the iframe/modal. Use fictional contact information only.
3. Try `4000 0000 0000 0002`, a future expiry and any three-digit CVC. Stripe should
   decline it without confirming the booking. Change to `4242 4242 4242 4242` and
   retry in the same checkout. Return should show the verified confirmation.
4. Alternatively use Stripe's back link before payment: selections remain saved,
   but the booking is not confirmed. Retrying reuses the same active checkout.
5. Refresh after success: no duplicate payment/confirmation should be created.

Verification: **215 affected Python tests**, **13 preview Python tests**, and
**10 wrapper JavaScript tests** passed. The new experience payment adapter has
**100% statement/branch coverage** (31 statements, 14 branches). Browser tests
covered actual Stripe decline/success, unpaid return/retry, inline/modal handoff,
desktop and 390px phone layouts, and automatic confirmation on return. Review
caught and fixed Stripe DNS rotation in the local outbound network guard. Tests
also reject forged success parameters, live keys/provider responses, wrong amounts,
cross-browser access and simulator actions against Stripe-backed orders.

Scope: small demo adapter, demo configuration/views/templates, focused tests and
the external local preview harness. The shared payment/settlement implementation,
enrollment, production settings and other schools were not edited for this change.
No new migration, push or deployment. Two fictional test bookings were confirmed;
existing sample data was not reset. Email stays in memory and Wix/Notion stay off.

Limitation: localhost has no inbound Stripe webhook or periodic recovery worker.
If a customer pays and closes Stripe before returning, the existing reconciliation
service must run to update the local booking. Signed test webhooks, account-event
binding and periodic reconciliation remain mandatory hosted-demo release checks.
This preview does not demonstrate recurring subscription billing or live payments.

### October 6: Booking And Selection Layout

October 6: **Cozy Creations** is available in Pasadena on October 11, 2026 at 10 AM,
noon and 2 PM (90 minutes, 12 places per session). Three additive seeded bookings plus
one customer-browser rehearsal give four confirmed bookings / seven participants. Taylor
Brooks is prepared; Morgan Ellis, Cameron Lee and Alex Parker are preparation pending.
Payments are simulated. Existing workshops, bookings and stock counts were not reset;
the new reservations hold their required raw materials through the normal services.
The guarded `seed_pasadena_workshop` command is repeatable without modifying existing data.

Customer calendar/time slots now use a compact bordered calendar and two-column time buttons.
Color swatches are closely grouped beside a larger selected-plushie preview. Review, checkout
handoff and confirmation show a thumbnail per participant: exact design/color photo when set,
otherwise the same illustrative design/color preview used during selection.

Verification: **140 affected Python tests**, **38 JavaScript tests**, **98.84% combined Python
statement/branch coverage** across experience service/views and the new seed (100% seed coverage).
Tests cover exact photos, fallback previews, review-to-checkout consistency, seed idempotency,
nonlocal/connected-school guards, and rollback without recipes. Desktop/390px browser checks
covered selection, review, checkout handoff, simulated success, admin visibility and no page overflow.
No full-suite rerun, migration, shared Enrollment/payment edit, external API call or push.

Manual check: choose Pasadena > Cozy Creations on Customer booking, select October 11 and a
time. Compare compact time buttons, then select Bear/Blue and check the larger preview. Review
and continue to the checkout demo; each participant should retain their own preview. On the
admin Reservations page, search Cozy Creations to inspect the four bookings and preparation.

## Catalog Color Settings

October 6: catalog option rows use smaller, muted edit icons immediately beside their values.
The layout is compact on laptops and stacks without page overflow on phones. Add/Edit color
now uses a required visual color picker plus editable six-digit hex value. The global color
image URL field is removed from this form; existing saved URLs are preserved. Design-specific
color photos remain in Add/Edit design. Age and other non-color option forms are unchanged.

Verification: **183 affected Python tests passed**, with **97.08% combined statement/branch
coverage** across inventory forms/views (each above 95%). **38 JavaScript tests passed**;
the new picker has 100% line/branch/function coverage. Browser checks at 1280px and 390px
verified layout, Add/Edit, both directions of picker/hex synchronization, and blank rejection.
Testing caught and fixed the custom-widget template location before completion. No full-suite
rerun is claimed for this change. No migration, saved demo-data change, shared Enrollment/payment
edit, provider call, push or deployment.

Manual check: open Catalog settings, inspect Color/Age rows, then edit a color. Enter a valid
hex and check the swatch; select a swatch and check the hex. Clear the value and try saving:
it must be rejected. Cancel to discard edits. Add color must also require a color, while
design editing must retain its individual color photo URLs.

## Compact Design Editor

October 6 visual correction: the earlier two-column color cards and repeated blue outlines were
unnecessarily bulky. Add/Edit design now has one compact row per color: checkbox, swatch, name,
photo URL. Main design fields sit side by side on laptops and stack on phones. Column headings
replace repeated visible photo labels; individual accessible labels and inline errors remain.
Selection changes only the checkbox, not the row's border. The editor uses Enrollment theme tokens.

Scope: only the scheduling form template, a dedicated design-fields partial, scoped materials CSS,
and its cache version changed. No JavaScript or backend behavior changes, migration or saved-data
changes. The demo preview was restarted on port 8002; shared enrollment and payments are untouched.

Verification: **177 affected Python regression tests passed**, including three new rendered-form
checks for Add/Edit labels, invalid input preservation and no-active-colors layout. Browser checks
covered 1280px desktop, 390px phone, Clean/Midnight themes, checkbox changes, invalid URL rejection,
inline errors, restored saved values, and zero page overflow/console errors. No full-suite rerun is
claimed for this presentation-only correction; the prior full-suite result below is historical.

Manual check: open Catalog > Open catalog > Edit design. Scan colors and photo fields across each
row, toggle a color, enter an invalid URL and confirm its inline error. Cancel/reopen to discard
changes. Repeat Add design and phone width. Existing photo saving/customer-preview tests still pass.

## Catalog And Scheduling Usability

October 6: all nine requested setup/usability changes are implemented. Workshop **View Catalog**
opens its assigned catalog. **Add/Edit design** uses selectable color swatches with a separate photo
URL for each design/color combination. Exact photos replace the illustrative tint in both the
customer preview and group summary; blank URLs retain the existing fallback. Global colors and
other booking choices now live under **Catalog settings** beside Open catalog on the catalog list.

Recipes have immediate remove-row icons and an aligned Add material row control. Changes are saved
only with Save recipe; Cancel leaves the saved recipe intact. Existing reservations retain their
original material snapshot. Stock pages distinguish blue On hand from amber Reserved, with
text-and-icon Available / Low stock / Fully reserved / Out of stock states. The editable
**Low-stock alert at** value is per material and studio; zero disables low-stock warnings.

Scheduling date/date-time entry uses a locally bundled Flatpickr calendar styled to the supplied
reference, including month arrows, AM/PM, Today/Clear/Close and keyboard controls. Date-only filters
omit time controls. ISO form values, local-time interpretation and server validation are unchanged.
Customer selection of published workshop slots remains availability-controlled, not arbitrary
date/time entry. Shared Enrollment and legacy Kid Works form assets are not changed.

Impact and self-review:

- Additive scheduling migration 0023 adds optional variant photo URLs and a zero-default stock
  alert threshold. Applied only to the isolated preview after backup; no production migration.
- Tenant/role checks, stale-edit detection, URL validation, unit validation, material holds and
  saved reservation recipes remain enforced. Threshold-only changes are audited without a stock delta.
- Review fixed long photo URLs overflowing the signed edit token, unnecessary variant audit entries,
  a demo-seeder test-isolation issue, and calendar footers extending beyond the viewport.
- No reset, live provider call, enrollment/shared-payment runtime edit, push or deployment.
  Browser photo/threshold trials were restored; physical stock and existing bookings were preserved.

Verification: **174 focused Python tests**, **97.19% combined statement/branch coverage** across
the four changed inventory modules (each above 95%); **35 JavaScript tests**, with all tested scripts
above 90% line/branch/function coverage. New recipe JS is 100%; date-picker JS is 100% lines/functions
and 96.15% branches. Vendor code, CSS and templates are not included in these coverage percentages.
The JavaScript 90% gate is included in CI; remote CI is not claimed to have run. Migration drift and
whitespace checks passed. Final full regression: **3,454 passed**, two existing warnings,
301.73 seconds. **64 tracker tests** also passed.

Manual repeatable checks (local admin above):

1. Workshop > open a workshop > View Catalog. Confirm its assigned catalog opens, not stock editing.
2. Catalog > Add design or Edit design. Select color swatches and enter a different HTTPS photo URL
   for each color. Save, select that design/color in customer booking, and check both preview and group
   summary. Changing another design's same color must not change its photo.
3. Catalog list > Catalog settings. Edit a color or age choice; Save/Cancel returns to settings.
   Catalog details no longer repeat the old Colors & other booking choices section.
4. Edit recipe: trash removes that row immediately; Add material row restores an empty row with focus.
   Save commits changes; Cancel discards them. A reservation booked earlier retains its saved recipe.
5. Materials Inventory > Stock & activity. Check On hand/Reserved distinction. Set an alert at the
   available quantity using Receive delivery = 0 with a reason; expect Low stock without changing
   physical inventory. Reset the alert to zero. Grey's default Pasadena fixture shows Out of stock.
6. Edit a workshop session. Check month arrows by mouse and Enter/Space, 12-hour time, AM/PM,
   Today/Clear/Close and Escape. Cancel preserves the original time. Date-only reservation filters
   show no time controls. Existing duration, room and capacity fields remain intact.
7. At 1280px laptop and 390px phone widths, inspect color controls, recipe rows and date popovers.
   No page-level horizontal overflow; wide grids scroll internally. Calendar footer stays in view.

These browser checks passed locally, including exact-photo replacement, stock-alert save/restore,
unchanged session save/cancel, recipe remove/add, settings return navigation and no JavaScript errors.

## Customer Mock Alignment

October 6: the working customer screens now follow the visual language of the
[mock shared with Jasmine](https://demo.mypontora.com/demo/plushumi/): centered logo on
the pink header, three progress steps, compact calendar, black actions, pink participant
tabs, visual design/color choices, an unframed group summary, review and confirmation.
The working catalog keeps all nine designs rather than limiting it to the mock's five.
The storyboard screen picker and Previous/Next controls are not customer navigation.

Workshop photographs retain their original proportions instead of being cropped to wide
frames. Catalog collage rows use individually fitted portrait framing, inside fixed-size
previews, so changing designs does not shift the form. Uploaded individual photos use
contain sizing. The supplied collage itself clips the very tips of some toys; this change
does not fabricate missing portions or claim the illustrative tint is a real product photo.

Impact: only customer experience templates, their scoped stylesheet/script and regression
tests changed. The admin remains Enrollment-themed as requested. No reservation, payment,
material-allocation, enrollment, permission or schema code changed. No seed/reset, external
provider calls, push or deployment. Existing local demo records were retained; one clearly
named **Visual QA Preview** simulated reservation was added during browser verification.

Verification: **3,436 full tests**, **70 focused experience tests** and **22 JavaScript tests**
passed. The changed customer JavaScript has **98.33% line / 92.19% branch / 92.68% function
coverage** in its unit checks; these percentages do not measure CSS or templates. A rendered
integration test verifies the new controls, signed review payload and confirmation content.
The full suite still reports two pre-existing warnings.

Manual repeatable checks:

1. Open Customer booking at desktop and phone widths. Logo stays centered, Studio and
   Participants align, photos retain their full proportions, and the page has no horizontal
   overflow. Only dates with actual sessions are enabled. Change studio/workshop and confirm
   that the selected time clears, with the correct location, duration and price shown.
2. Select two participants. Choose different designs/colors and ages; inspect all nine photos,
   the color preview and live group summary. Toggle Whole group, add/remove a participant,
   then return to One at a time; values and totals must remain correct. Grey has insufficient
   materials in the default Pasadena fixture; applying it to everyone must leave choices intact.
3. Review the booking, use Edit selections, and confirm names, ages, designs and colors survive.
   Continue to the clearly marked local checkout simulation. Payment failure must not confirm;
   simulated success must show the saved selections, total and calendar link.
4. Repeat the customer path at 390px width. Check the three-step header, calendar, all design
   options, group layout, review and confirmation. Color/design selection must not resize the
   preview. Desktop 1366px and phone 390px checks passed; calendar edge cases, leap years,
   cross-year month navigation, missing dates and capacity clearing are also unit-tested.

## Catalog, Materials Inventory and Workshop Prep

This replaces the finished-plushie stock model. Catalog designs and colors have **no stock
quantity**. Each color has a recipe for one participant. Raw materials, with their own units,
are stocked per studio. Several designs can use the same fabric, stuffing or eyes.

The refreshed local demo has nine designs, nine colors, 81 recipes, 11 raw materials at
five studios, and 15 fictional reservations spanning pending preparation, prepared,
completed and canceled. Recipes use illustrative amounts, not verified Plushumi specifications.
The old Plushumi practice records, including Young Horizons, are replaced by this clean dataset.
Other schools and their records are not reset.

1. Open **Setup > Catalog > Open catalog**. Expand a design; see its colors and materials
   per participant. **Edit design** changes name/photo/colors without asking for stock.
2. Select **Edit recipe** for a color. Add or remove raw materials and enter quantities in
   their listed units. At least one valid active material is required. Existing reservations
   retain the original saved recipe when you change it.
3. Open **Materials Inventory**, select a studio, and see On hand / Reserved / Available.
   **Stock & activity** receives deliveries or corrects a counted balance, records the reason,
   and shows which reservations and catalog recipes use that material.
4. Open the customer booking link. Pick a studio/session and different designs/colors for two
   participants. Unavailable combinations are blocked using their combined raw-material needs.
   Complete the local simulated checkout, then find the reservation in the admin workspace.
5. Open **Workshop Prep**. The saved participant choices are under collapsed Reservation details;
   Materials lists combined fabric/stuffing/eyes, without a redundant studio column.
6. **Mark prepared** records readiness but does not reduce physical stock. **Workshop completed**
   changes the reservation to Completed and deducts its saved material amounts exactly once.
   The reservation audit and stock activity both record consumption.
7. Cancel another uncompleted reservation. Its holds are released without consuming materials.
   Repeated completion, stale stock edits, insufficient materials and cross-school access are rejected.

Scheduling migration 0022 preserves other tenants' old stock as one-piece legacy-kit recipes,
including their allocation history. It does not assume raw-material recipes for them. After real
raw-material data exists, lossy rollback is refused; use the pre-migration backup if rollback is needed.
Only the isolated local preview database has been migrated. Enrollment and shared payments were
not edited for this replacement. Existing workshop, party and camp booking services expand recipes
through the same inventory service; a camp recipe is per participant for the whole camp.

Verification: **3,434 full regression tests passed**, followed by **141 focused checks** including
the final fractional-material party/camp test. **16 JavaScript checks** and **64 tracker checks**
passed. Changed inventory modules have 96.27-100% combined statement/branch coverage; migration
files are excluded from coverage percentages but forward/backward preservation and lossy-rollback
rejection are tested explicitly. The full measured runtime coverage is 99.35%; the pre-existing
test-network guard imports before instrumentation and reports 70.73%, so this is not a claim that
every measured file exceeds 90%. Two pre-existing warnings remain, including duplicate Jazzmin tags.

Manual browser checks: two-person mixed-design/color booking, simulated checkout, saved material
aggregation, preparation, completion/audit, studio-scoped fabric receipt, design/color creation,
three-row recipe editing, disabled missing-recipe colors and desktop/390px phone layouts. No page
overflow or browser errors were observed. Final sample data was reseeded after those QA actions.
Fingerprints of all non-Plushumi schools, leads, submissions, profiles, locations, workshops,
sessions and reservations match the pre-change values. `makemigrations --check --dry-run` reports
no model drift. Recipe amounts and provider integrations still need customer validation.

The repeatable local seed accepts `--reset`, but refuses nonlocal, non-demo, connected-payment,
integration, lesson or admissions data. Backup created before this reset:
`/tmp/pontora-before-materials-20261006.dump`. No deployment or push is part of this change.

## Promised Screens

| Shared concept | Working implementation |
| --- | --- |
| 1. Studio, participants, calendar/time | Five fictional studio fixtures, bounded available dates/times, remaining capacity and live group subtotal |
| 2. Guided visual selection | Each participant has name, age, design and color; cropped original catalog images and illustrative color preview |
| 3. Group entry alternative | Same fields and values in a group layout; shared-color shortcut; add/remove people |
| 4. Review and Wix checkout | Review/edit before creating a held reservation; signed server-price quote; explicitly simulated Wix handoff |
| 5. Confirmation | Saved booking after local test-ledger settlement; preserved selections, calendar download, unsent email preview |
| 6. Preparation/Notion concept | Authenticated confirmed-booking roster, reversible Prepared action, saved raw-material requirements, print and actual-record offline Notion mapping preview |

Screen 3 is an alternative layout, not another customer form. No second selection form
is required after checkout. The original `/demo/plushumi/` storyboard is unchanged.

## Integration Assumptions

Wix would provide authoritative availability/booking/payment status if its supported
APIs and the business's setup allow this workflow. The local demo's native availability
and test ledger stand in for that boundary; they are not a second live booking authority.
Failed/abandoned payments do not appear as confirmed preparation records. No external
payment or email request is made. Email confirmation is shown as an unsent preview at
the Wix completion boundary, not represented as delivered mail.

Notion would receive one reservation with participant selections kept paired. The stable
booking reference is the proposed upsert key. The offline adapter includes the studio
in the booking title; a dedicated location field and final database/participant structure
must be agreed with Jasmine. The displayed table is a Pontora prep view, not fabricated
Notion UI. Connected delivery, updates, retries and authorization remain M4 work.

The settings module already separates sources from destinations and supports configurable
mapping. No provider credentials or connections are enabled by this setup.

## Scope and Impact

- One generic local experience package, configured by `configs/booking_demos/plushumi-demo.yaml`.
- Reuses WorkshopRequest, participant snapshots, native reservation capacity and shared payment ledger.
- P2 needed no migration. P3 adds scheduling migrations 0019-0020, applied only to the isolated
  local preview database. No production activation, email, real charge, deployment or push.
- Demo endpoints require DEBUG, the named loopback preview database, a demo school, explicit flag,
  enabled workshop/reservation capabilities. New bookings additionally require native intake;
  pausing intake preserves existing checkout, receipts and staff preparation. Remote hosts fail closed.
- Staff preparation and mapping require scheduling membership; private receipts require the originating browser.
- Setup refuses non-demo schools, school-level billing/email configuration and shared accounts;
  practice sessions are preserved on rerun.
- P3 staff screens reuse enrollment's existing design tokens through a scheduling-only
  stylesheet. The enrollment stylesheet and templates are not edited for this work.
- The header follows Student Enrollment: Dashboard; Reservations;
  Setup (Workshop, Parties, Camps, Catalog, Materials Inventory); Settings (Location & rooms, Team Access, Integrations).
  Requests is not a menu item. Original request records and existing URLs remain intact;
  Workshop Prep is a tab inside each reservation, not a main Reservations subtab.
  ordinary customer booking still confirms automatically after successful simulated payment.
  Shared catalogs are optional per offering; existing businesses without a catalog retain their old choices.
- SBMC, Kid Works, original enrollment DB and Enrollment Request Demo are not reconfigured.
- Existing private-party and fixed-cohort camp flows are available from the workspace; fixture fees
  and dates are assumptions, not verified Plushumi offerings. Music/recurring tuition are not added.

## Manual Walkthrough

Expand P2 and P3 in the [tracker](scheduling-implementation-tracker.md) for the repeatable steps.

The admin header and section tabs now use the Student Enrollment structure and theme.
Use Setup > Catalog for designs/recipes, Setup > Materials Inventory for stock,
Reservations > open a booking > Workshop Prep for its prep, and
Settings > Location & rooms for studio configuration. Existing bookmarked URLs still work.
At phone width the header navigation is behind the menu button; section tabs remain visible.
Workspaces and Customer demo are also in the avatar menu. Clean/Midnight/Mint use the
same browser preference as Student Enrollment.

Navigation checks: Python cases cover exact tab structure, active parent/subtab, permissions,
feature combinations, legacy URLs and working tab destinations. Run browser-control tests
with `node --test --experimental-test-coverage scripts/tests/booking_admin.test.cjs`;
they cover mobile menu state, Escape/outside-click behavior, theme persistence and unavailable storage.
Browser checks covered all requested destinations, 1280px desktop and 362px mobile,
theme persistence and keyboard focus. Booking and payment behavior are unchanged.

Jordan Miller bookings are waiting for preparation; Alex Chen bookings are prepared.
Sam Rivera examples include completed and canceled records. No need to reset the demo
to start another fictional booking.

## October 5 Workshop Audit

The six-screen customer-to-preparation story is present, but earlier readiness wording was too
broad. Unit coverage did not catch the interaction between the branded demo, shared stock,
intake pause and the newer admin theme. These combinations now have regression/browser checks.

Corrected in this audit:
- Pausing intake no longer hides existing checkout, receipts, calendar or preparation.
- Stock errors are displayed above the form. Group selection accounts for the other participants;
  Apply to all leaves choices untouched when the requested color cannot serve everyone.
- New participants must choose age/design/color instead of silently receiving default selections.
- Retrying an exact-stock checkout reuses its existing hold instead of rejecting its own reserved stock.
- Ordinary staff-created workshop reservations appear in prep and mapping, not only branded-demo bookings.
- Missing design/color data is flagged and excluded from material totals instead of reported as zero missing.
- Catalog image crops retain their identity when staff rename or reorder designs. Nonvisual catalogs
  fall back to ordinary fields instead of crashing the customer picker.
- Prep uses the staff heading layout and a horizontally scrollable table on phones. Its print stylesheet
  now includes the chosen studio/date/time and excludes navigation and actions.

Recommended call sequence (about 8-10 minutes):
1. Create a Pasadena workshop booking for two fictional participants with different selections.
2. Switch to Whole group and back, then show review/edit and the clearly simulated checkout.
3. Open the booking in Reservations, then Workshop Prep, and show its saved participants/materials.
   Mark the booking Prepared, then reopen it. Neither action consumes stock or moves money.
4. Show Setup > Catalog for recipes, then Materials Inventory for studio-specific stock.
5. The legacy [session preparation preview](http://localhost:8002/bookings/plushumi-demo/experience/preparation/)
   retains Preview Notion mapping for discussing the proposed integration fields with Jasmine.
   It is not in the main Reservations navigation.

## Reservation Workspace Update

Reservation details now separate schedule/contact information, Money and Audit log. Room,
participant choices, readiness and material usage are on the record's Workshop Prep tab.
Prep indicators on the list open the exact booking; other bookings in the same session
are not included. Cancellation requires an explicit with/without-refund choice and reason.
The local demo supports simulated full refunds for its own simulator receipts. Arbitrary
offline receipts cannot be refunded automatically. Verified Stripe payments use the existing
shared payment/refund service, including deposit and balance across separate payments.
No new payment engine, schema change or live provider call is introduced.

The manual offline-settlement form and request handler are removed. Shared payment records,
historical receipts and internal demo settlement are retained; this does not remove other
modules' payment support. The audit cannot reconstruct events that were never recorded in
older data. Provider sandbox acceptance remains a separate release gate.

`Plushumi Demo Rehearsal` is retained at Pasadena, October 10, 2026, 1 PM, as a fallback saved
example: Demo One / Adult / Blue Cat and Demo Two / Adult / Pink Cat. No message or money was sent.
Temporary stock testing was restored to its original on-hand count; new intake is Open.

### Remaining Product Gaps

| Area | Current boundary | Next decision or work |
| --- | --- | --- |
| Pricing and policies | Branded demo price ($65) and policies are YAML configuration, not staff-editable workshop settings | Generic offer pricing/policy settings for native bookings; provider-owned values when Wix is authoritative |
| Parties and camps | Generic request/registration and inventory work; not the same branded visual participant flow | Extend the reusable customer experience after the workshop path is accepted |
| Customer changes and communication | Demo receipt is browser-bound; email preview is not sent; no branded self-service change journey | Decide Wix versus Pontora ownership, then implement return access, delivery and change/reconciliation paths |
| Catalog fidelity | Original catalog crops and illustrative color tint; configured combinations/counts are fictional | Approved design/color combinations, images, prices and stock policy before pilot |
| Connected systems | Offline Wix handoff and Notion payload previews only | Authorized access, schema and field ownership; delivery/update/cancel/retry proof under M4 |

For the call, show the workshop journey rather than touring every module. Do not describe
parties/camps as equally polished, or the Notion preview as evidence that manual copying has
already been eliminated in their live operation. The working demo still cannot be shared
through the public storyboard URL: its endpoints intentionally require the isolated local preview.

Verification: **3,149 application tests passed**, two existing warnings, 243.77 seconds.
The four experience Python modules have **99.07% combined statement/branch coverage**;
every measured module meets 90% (99.53% across scheduling/shared payments/test safety).
The experience regression suite passed again after the final prep markup changes. These
figures do not measure JavaScript/CSS coverage. Browser checks exercised limited group stock,
Apply to all, edit/retry, paused-intake settlement, saved prep/mapping, restart persistence,
and 390px customer/staff layouts. Print styling is implemented, but the native print preview
could not be verified through the in-app browser controls; do not claim printer/PDF output tested.
No new migration, enrollment-code change, external API call, push or deployment in this audit.

## October 6 Usability Verification

Completed the previously tracked polish work:
- **Prepared / Reopen preparation** on the roster and reservation detail, with actor/time,
  audit history and stale/concurrent-edit protection. Payment and stock values are unchanged.
- Grouped Daily work, Programs and Setup navigation. Mobile uses an accessible collapsible
  menu; desktop keeps direct links. Existing capability/permission filtering is retained.
- Reservation search by customer name/email or workshop, plus studio, status and local-date
  range. Filters persist across payment follow-up and pagination; clear/error/empty states work.
- Catalog stock-item search and 25-item pagination, with a keyboard-scrollable mobile table.

Final regression: **3,161 passed**, two existing warnings, 256.35 seconds. Preparation,
reservation forms and reservation query modules have **100% combined Python statement/branch
coverage**; every measured module meets 90%, with **99.52%** across scheduling/shared payments/
test safety. These figures exclude JavaScript/CSS, which were checked in the browser.

Manual checks covered desktop (1280px), narrow phone layout (361px actual available width),
keyboard menu activation, combined filters, clear filters, invalid dates, catalog search,
Prepared/Reopen roster changes and server-restart persistence. The rehearsal booking is
left **To prepare** so the user can repeat the action. No stock or payment changes were made.
Migration **0020** adds readiness fields only to reservations; applied to isolated preview/test
databases, not the main enrollment database or production. Migration consistency, JavaScript
syntax and whitespace checks passed. Native print/PDF remains unverified, as noted above.
The temporary local launcher/settings were restored after their files disappeared; the
preview is running on port 8002 with external traffic blocked and existing demo accounts.

No enrollment templates/configuration changed in this cleanup, no second payment module,
no SBMC/Kid Works enablement, no live provider calls, no push or deployment. The product gaps
above remain explicit scope, not cosmetic work claimed complete.

## Inventory and Stock

Use **Setup > Inventory > Manage catalog** for designs, colors, image URLs,
swatches and stock-item combinations. Use **Studio stock** for physical counts and
reasoned adjustments. Workshops and camps select a catalog in their underlying offering;
parties inherit that offering's catalog. This is a reusable model, not a second Plushumi-only system.

Individual workshops now use **View inventory** to show stock from their selected Materials
Catalog at their location, not the catalog editor. Search and pagination retain that scope.
Materials Catalog is mandatory in inventory-using businesses such as Plushumi; historical
uncatalogued bookings and non-material businesses keep their existing selection source.
Workshop setup omits the unpaid-request/party-inquiry switches and unrelated submenus. Sessions
are edited or added inline in an Enrollment-style grid. Preparation remains on each reservation.

Reservations hold available materials across dates at the same studio. Unpaid inquiries do
not hold them. Canceling or expiring a reservation releases unused items; uncertain payment
attempts remain protected pending reconciliation. **Mark prepared** is a reversible staff
readiness marker with actor/time history. It removes the booking's kits from the preparation
queue but does not alter inventory or the payment ledger. Incomplete materials, unconfirmed
bookings, consumed materials and stale edits are rejected. Workshop, party and camp reservation
detail pages reuse the same action; a viewer cannot change readiness.
**Workshop completed** in the reservation header replaces the old materials-used checkbox/button.
After confirmation it deducts unused allocations and marks the reservation Completed atomically,
with actor/time audit history. Retries and previously consumed items do not deduct twice. Payments
are unchanged, and Completed cannot be canceled or reopened through preparation. Remaining online
balances can still be collected; only excess-payment refunds are supported on completed bookings.
Broader post-service refunds need a billing-adjustment workflow so refunded money is not charged
again as a new balance. Each camp participant needs one kit for the whole camp in this slice.

Both reservation tabs show **Participants & selections**, with a separate column for each saved
choice. **Payment summary** replaces Money. Prep has a collapsible copy of Reservation details,
room information and a Material / Quantity / Stock status table, using the reservation's studio
behind the scenes. **Preparation Pending** replaces To prepare. The fictional **Workshop Completion
QA** record is a completed browser-test example; existing customer-test bookings were not altered.

Initial counts and combinations are fictional. Inventory has no Wix/Notion stock connection,
purchase orders, transfers, replenishment automation or multi-material recipe/bill-of-materials
support. Party choices are item quantities, not a visual participant-by-participant party designer.
Provider ownership and real stock validation must be agreed before launch.

## Earlier Verification (Historical)

Initial P3 result: **3,138 tests passed**, two existing warnings, 237.18 seconds. The five new
inventory modules have **98.64% combined statement/branch coverage**; every measured module
passes 90%. Additional tracker checks passed. Browser checks covered workshop consumption,
party materials, sold-out choices, protected adjustments, desktop/mobile layouts and restart
persistence. Existing SBMC/Kid Works forms were checked without submission or configuration
changes. Read [P3 verification](scheduling-p3-verification.md) for scope and boundaries.

The following figures describe the earlier P2 delivery, before inventory was added:

- Final regression: **3,119 passed**, two existing warnings, 207.94 seconds.
- New Python code: **99.58% combined statement/branch coverage**. Config, views and seed command
  are 100%; booking service is 98.97%. Every measured scheduling/shared-payment/test-safety module
  passes the 90% gate. This metric does not claim JavaScript code coverage.
- Tests cover CSRF, signed/stale/tampered reviews, server pricing, group quantity, duplicate
  booking/payment retries, overselling after a hold expires, browser/tenant isolation,
  permissions, production guards, projection failures, calendar escaping and seed safety.
- Browser checks passed at desktop and 390px mobile: asset rendering, guided/group switching,
  per-person pairing, add/remove, shared color, review/edit, failed checkout/retry, success,
  cancellation, persisted staff preparation, existing payment workspace and Notion projection.
- Restart check passed: saved confirmation/preparation remained available. No horizontal
  page overflow in the checked mobile forms/preparation; wide tables scroll within their container.
- No migrations detected. No live API, email or payment activity; tests block external network.

Defects corrected during review: browser referrer/CSRF compatibility (same-origin policy, no
CSRF exemption), removed participants reappearing on review/edit, misleading failure text on
an already-confirmed booking, destination-validation errors becoming server errors, missing
white-logo contrast, nonlocal session-time labels, and preparation defaulting to an empty studio.
Mapping links now respect the integration capability as well as staff permissions.

Open delivery boundaries are intentional: verified Wix capabilities/account configuration,
Notion database schema and field ownership, real email, webhook/retry behavior and customer
approval of catalog/prices/policies. Do not describe this local rehearsal as a connected or
production-ready integration. The original externally shared mock URL has not been replaced.
