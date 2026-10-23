# Pontora Bookings Implementation Tracker

Updated: October 23, 2026.

Browser view (local only): [Status overview](http://127.0.0.1:8003/) ·
[Readable tracker](http://127.0.0.1:8003/tracker). Start with
`venv/bin/python scripts/project_status.py`; refresh to read the latest saved documents.
This Markdown file remains the source of truth; the viewer does not edit milestone status.

## Current Status

### October 23 Release

PR #7 merged to main (`d2674df`), and the web service and existing five-minute
maintenance job are deployed. This supersedes local-only labels below for the
calendar notes, location-based team access, shared team email fields and Square
connection/payment work included in that release. Policy/waiver placeholders
remain deferred, not complete.

Final CI: 4,569 Python tests passed, three skipped; 98.56% combined coverage;
83 JavaScript tests passed. Real hosted Sandbox payment/refund webhooks and
duplicate/signature isolation checks passed. Live charging remains off; no
school's selected provider changed. Hosted Sandbox authorization, Pasadena
location mapping and actual renewal by the existing scheduled worker are verified.
Other demo locations remain unmapped; Jasmine's live connection and live payment
acceptance remain pending. Test slowness was profiled, not optimized.
See `square-booking-payments.md` for deployment and diagnostic evidence.

### Location-Based Staff Access (Local Review)

October 22: extend existing SchedulingMembership users/Owner/Scheduler/Viewer
roles with All locations or Selected locations. Configure existing members in
Bookings > Settings > Team Access using the shared enrollment team panel. Role permissions
are unchanged; location scope further limits them. Existing memberships retain
all-location access through additive migration 0036.

- Calendar, dashboard totals/activity, workshops, parties, camps, reservations,
  preparation, guest lists, payment actions and material counts enforce assigned
  locations on the server, including direct URLs and forged submissions.
- Only school-wide Owners and superadmins manage membership access. The last active
  school-wide Owner cannot be removed, demoted or location-restricted. Saves use the existing stale-edit checks,
  profile transaction lock and audit log; changes apply on the next request.
- Location Owners manage local setup and bookings, not school-wide billing,
  integrations, shared catalog definitions or communication settings. Scheduler
  and Viewer retain their existing role permissions. Enrollment access is separate.
- Scope for this release is workshops/parties/camps. Lesson/tour administration and
  the school-wide message log remain unavailable to restricted memberships rather
  than exposing unscoped records. Existing all-location access is unchanged.
- Original location-scope verification covered restricted calendar/settings,
  immediate Owner-to-Viewer downgrade and direct Team Access denial.
  The team-flow correction uses the shared SBMC panel, not a dialog: desktop and
  mobile layout, inline name/role saves and selected-location saves were checked
  in the browser. The browser stalled on the Remove confirmation, so removal is
  covered by the automated workflow check, not claimed as manually verified.
  Temporary local test users were removed; existing accounts/passwords untouched.
- No production deployment, real payment, email or external service changes.
- Before the team-flow correction: full regression 4,381 passed / 3 skipped; focused access,
  checkout, calendar and view regression 163 passed; JavaScript 82 passed. Final
  permission-only rerun: 47 passed, with 100% line/branch coverage of access and
  membership-save helpers, including no-op audit suppression. Migration
  drift and diff-whitespace checks passed. Existing duplicate Jazzmin template-tag
  warning remains unrelated to this change.

Manual check: open `/bookings/plushumi-demo/access/` as superadmin or school-wide
Owner, edit an existing colleague, choose their role and selected locations, then
save. In a separate browser session, sign in as that colleague and check calendar,
reservations and setup. Other locations must not appear; direct links must fail.
Use All locations for school-wide staff. The shared SBMC team panel now supports
Create & Add, inline names and roles, and Remove for booking memberships too.
Location access is the booking-only extension; no enrollment membership or Django
staff privileges are granted. Migration 0037 records who added new booking members.
No invitation or separate modal workflow is introduced.

Team-flow correction verification: 2,125 scheduling, team, login, environment and
enrollment regression tests passed; 82 JavaScript tests passed. Tests cover account
creation/login, inline names/roles/location scope, removal and immediate access
revocation, invalid/duplicate users, last-owner protection, stale edits, unchanged
enrollment controls and constant query counts as the team grows. Empty-team and
disabled-login display regressions found during testing were corrected. Migration
drift, compile and whitespace checks passed. Local only; not pushed or deployed.

Visual follow-up: compared against the rendered production SBMC Team screen
(read-only). Shared component styles now prevent booking-wide overrides from
changing its compact controls, row alignment and primary button. Location choices
use a compact dropdown rather than expanding each row. Verified desktop/mobile
layout, dropdown selection/dismissal and an unchanged-access save locally.
Focused Python regression: 102 passed; JavaScript: 83 passed. No production writes.

Team email follow-up: the shared panel requires a valid email for new enrollment
and booking accounts, with a separate inline email save for existing members.
Existing blank-email accounts retain login, name/role edits and permissions.
Shared addresses are allowed; their users sign in by username and the existing
password-reset flow sends a separate reset link for each matching account.
Email changes are owner/superadmin-only and audited; school owners cannot edit a
superadmin's recovery address. No migration, account backfill or live email sent.
Verification: 160 focused Python regressions and 83 JavaScript tests passed,
including reset completion/token invalidation, shared addresses, invalid input,
CSRF, cross-school denial, superadmin protection and legacy account behavior.
Both local team screens inspected; mobile booking form and invalid-email browser
validation checked. Local only, not pushed or deployed.

### Booking Policies and Waivers (Deferred; Placeholders Only)

October 22: Ketan directed that no policy summaries, acknowledgment wording or
waivers be authored by us. The existing policy page was located and reviewed:
https://www.theplushieworkshop.com/booking-and-cancellation-policy.
School YAML now holds its source URL and empty text/document placeholders only.
No new policy UI, required checkbox, enforcement or acceptance records are added.
Existing booking behavior is unchanged; this feature is not complete.

Before activation, obtain the exact approved workshop/party text, acknowledgment
wording and waiver document/collection process. Reconcile the website's general
food restriction with Jasmine's emailed party exceptions. Organizer acknowledgment
must not be treated as a signed parent/legal-guardian waiver for each minor.
The existing workshop demo acknowledgment remains legacy placeholder behavior,
not evidence that the final customer policy/waiver requirement is implemented.

### Calendar Reservation Details and Notes (Local Review)

October 22 follow-up on `codex/reservation-notes-calendar`, not deployed:

- Calendar events already opened the existing reservation detail screen. Those
  links now retain the calendar date/range/location URL, with a same-school-only
  Back to calendar link that survives notes saves and the Workshop Prep tab.
- The existing detail summary now displays saved party name, occasion, space and
  observer allowance. Payment totals and guest selections reuse existing displays.
- Owners and Schedulers can edit internal notes; Viewers can read but not edit.
  Parties retain PartyRequest.preparation_notes as the single source, shared with
  Workshop Prep. Workshops/camps use Reservation.internal_notes (migration 0035).
  Existing tenant locks and signed edit revisions protect simultaneous/stale edits.
  Updates record actor/time in the existing reservation audit without copying notes
  into customer snapshots, payment pages or a separate notes timeline.
- Verification: 215 focused Python regressions passed; 100% line/branch coverage
  for the new notes service; 12 calendar JavaScript tests passed. Checks cover
  cross-tenant access, Viewer/inactive membership, CSRF, invalid/oversized text,
  concurrent edits, no-op saves, clearing, customer privacy and frozen party terms.
  Manual local checks as plushie_admin: calendar -> party -> save -> Workshop Prep
  -> same calendar week; desktop/mobile screenshots inspected, mobile document
  width equals viewport (433px). Existing localhost:8002 and preview DB reused.
  Full release-wide suite/deployment is not claimed for this local follow-up.

### October 22 Release

PR #5 merged to main as `8193b9740665f99fe0fe0fb7b630258d2803ad39`.
Render web deployment completed and scheduling migrations 0029-0034 applied.
Final GitHub CI: 4,336 passed, 3 skipped; 98.64% overall coverage and every
measured module above the existing 90% gate. JavaScript: 80 passed with the
existing coverage gates. Additional error-path tests were added before merging.

Jasmine's supplied Design guide.pdf is installed for the hosted Plushie demo
through the existing validated upload service. Its public current-guide download
matches the supplied PDF's SHA-256 exactly. Anonymous historical-version access
returns 404; the app-domain demo route redirects to the demo domain. The private
R2 bucket retains uploaded versions; only the temporary staging object was removed.
No production database was copied locally. SBMC's hosted billing DOM matched its
pre-deployment baseline exactly. Calendar month/week switching and location filters
were checked on the hosted demo; calendar enablement/default changed only there.

Final configuration deployments are live: web `dep-dbd89s4ipgvc73ee1fj0` and
cron `dep-dbd89sbncjis73bfjj00`. Guide storage readiness and the global party-email
switch are enabled. The shared job is active every five minutes with commit-based
auto-deployment. A delivery-disabled run passed first; the activated run at
21:51 UTC completed successfully with zero messages processed. No additional test
emails were sent. Per-school notifications remain opt-in and demo/test delivery
remains blocked. Hosted guide replacement controls were visually verified after
the final restart. The existing duplicate `jazzmin` template-tag warning remains;
it did not prevent checks or successful execution.

This releases slices 1-4 and the current calendar implementation, not a declaration
that Plushie's complete launch/cutover scope is finished. Location-restricted
staff access, remaining policy/waiver work and integrated customer acceptance
remain separate launch work.

The following implementation notes retain pre-release test history; the release
status here supersedes their earlier local-only and pending-activation statements.

### October 20 Requirements Baseline and Delivery Order

Source: Jasmine's calendar/party follow-up email supplied by Ketan on October 20,
and Ketan's subsequent configuration and rollout instructions. This section
supersedes conflicting October 17-19 requirements below. Historical implementation
and verification notes are retained; a requirement here does not mean it is built,
deployed or accepted. Update this tracker rather than creating a parallel plan.

Key corrections:

- Pontora replaces Wix's booking calendar at launch. Notion sync/export is an
  optional proposal, not committed launch scope; recommend location-restricted
  Pontora access first and confirm that choice with Jasmine.
- Standard parties book directly when capacity is available and pay a deposit.
  New York full-studio and custom arrangements require inquiry/review first.
  Existing inquiry-only implementation is not the finished standard-party flow.
- Standard party deposits are $150; private full-studio deposits are $300.
  This replaces the earlier "$150 per six participants" assumption, including
  the old $300 standard 12-person deposit. Do not alter existing reservations.
- Individual participant names/ages/designs/colors belong after confirmation,
  not as an initial booking requirement. Most hosts supply them later, including
  by email; staff need to enter emailed details on the same reservation.
- Observer allowance must be stored/displayed as package information. Removing
  the old guest-count input did not remove the policy requirement.

#### Confirmed Package Values

All packages reserve 180 minutes. Prices exclude tax; tax treatment is unresolved.
These are configuration requirements, not authorization to publish live offers.

| Locations | Party | Included participants | Observers | Price USD | Deposit USD | Approval |
| --- | --- | --- | --- | --- | --- | --- |
| Pasadena, Houston, Dallas, Charlotte | Party Package A | 6 | 2 | 495 | 150 | Direct booking |
| Pasadena, Houston, Dallas, Charlotte | Party Package B | 12 | 4 | 895 | 150 | Direct booking |
| New York | Party Package A (Reserved Table) | 8 | 2 | 595 | 150 | Direct booking |
| New York | Party Package B (Private Full Studio) | 16 | 4 | 1190 | 300 | Staff approval |

Additional participants cost $65 plus tax; up to two may be allowed depending on
package/studio capacity. Do not infer eligible packages, observer rounding or
capacity exceptions. Larger/custom groups remain inquiries.

#### Ordered Implementation Slices

| Order | Scope and existing foundation | Acceptance boundary |
| --- | --- | --- |
| 0 | Inspect existing membership roles, room/session capacity, package snapshots, approval/payment handoff, notes and email delivery. Record gaps before code changes. | One existing owner for each rule; no duplicate scheduling/payment engine or customer-specific branches. |
| 1 | Extend Setup > Parties with per-location package name, duration, price, deposit, participant/observer allowances, extra-person limit/rate, approval mode and references to existing bookable spaces. | Authorized edits preserve historical reservation terms. Full-studio exclusivity and table/shared-capacity conflicts are enforced, including concurrent requests. Unknown tax/availability rules are not published as defaults. |
| 2 | Separate initial booking from later preparation. Reuse customer selection, reservation and payment services for direct packages; retain review for approval-required/custom parties. | Initial form collects name/email/phone, occasion, conditional birthday name/age, general age range, expected count/range, location/date/time and package. Age-range requiredness is not newly imposed without agreement. One reserved participant makes one plushie. Approved inquiries have a secure payment handoff and defined hold expiry; payment failure/retry cannot double-book or confirm unpaid reservations. |
| 3 | Private host follow-up on the existing reservation, reusing numbered participant controls and visual selections; staff edit the same data. | Names/ages/designs/colors can be supplied later. Deadline is school YAML (7 days), not a new settings screen. Short-notice bookings request selections immediately; late changes depend on availability. Missing selections remain visible to preparation and inventory. |
| 4 | School-managed design-guide upload/replacement, confirmation delivery and seven-day reminders using existing email infrastructure. | Recommend one current guide per school with retained file versions and a stable download link during booking, in confirmation and on the reservation. Whether to attach a PDF remains open; sent attachments cannot change. Validate file access/type/size, actual delivery, duplicate-reminder prevention, cancellation/rescheduling and short-notice cases. No general document or messaging platform. |
| 5 | Calendar reservation details/internal notes, policy acknowledgment and waiver process. | Reuse reservation details/notes; confirm which notes behavior exists before adding it. |
| 6 | Location-scoped staff access using existing Owner, Scheduler and Viewer roles, followed by integrated acceptance/cutover. Moved last at Ketan's request October 20, not removed from launch scope. | Management sees all permitted locations; employees see only assigned locations. Server checks cover lists, calendar feeds/details, reservations, prep, payments and applicable exports/direct URLs. Then test customer desktop/mobile, money/capacity races, reminders, environment isolation and SBMC regressions. Deployment requires separate approval. |

Calendar status: Day/Week/Month, filters and reservation links implemented locally
October 20, with superadmin school-level enable/default controls. Not deployed.
Full regression recorded 4,197 passed/3 skipped; final focused calendar suite 16
passed and JavaScript suite 76 passed. This does not establish location-restricted
staff access, new internal-note functionality or complete Wix replacement.

October 22 guest-communication slice (local implementation, not deployed):

- Settings > Guest communication lets existing Owners upload/replace a school-wide
  PDF. Immutable upload records retain earlier versions; customers use a stable
  current-guide link on inquiry, guest-list and reservation pages. Historical
  files require Owner access. Files use Django storage, not database blobs or a
  customer-specific path. Upload validation checks size, PDF structure, encryption
  and active/interactive content. Stale replacements are rejected; identical uploads
  are idempotent. Jasmine's supplied one-page guide was loaded unchanged locally.
- Confirmations and one guest-deadline reminder reuse BookingMessage and its fenced
  delivery attempts. The existing school mail factory supplies transport. Capture
  is idempotent, canceled/rescheduled/stale messages are checked before sending,
  completed guest selections suppress reminders, and short-notice confirmations
  ask for selections immediately without a second immediate email. Unknown provider
  acceptance is held for review, not automatically retried.
- School notification settings default off. External delivery additionally requires
  DEBUG=False, BOOKINGS_PARTY_EMAIL_ENABLED=True, a non-demo school and a live
  payment-order environment. Existing lesson/tour preview messages cannot enter
  the live party worker. Payment providers and enrollment email behavior are unchanged.
- Operational commands: `manage.py process_party_notifications --school SLUG --limit 20`
  for scoped checks; `manage.py process_party_notifications --all-schools --limit 20`
  for the shared live worker. The shared command excludes demo schools and test
  orders, honors global/per-school switches and capabilities, and continues other
  schools after an individual school failure (then exits nonzero).
  Ketan approved the paid Render job on October 22. Created
  `pontora-party-notifications` (`crn-dbd7ahu7bikc73fc2h80`) in the existing Production
  environment/Oregon region, Starter compute, every five minutes. It is SUSPENDED,
  auto-deploy OFF and BOOKINGS_PARTY_EMAIL_ENABLED=false until the application
  release/migrations are verified. Build: `pip install -r requirements.txt`.
  Start: `python manage.py process_party_notifications --all-schools --limit 20`.
  Verified required database/signing/email/URL values match the web service, without
  copying payment, R2, operator or AI credentials. Rotating shared secrets or URL
  settings requires updating this job as well as the web service.
  Activation: deploy the tested release to web first; build the same commit on the
  cron service; verify migrations and a delivery-disabled run; then enable live
  delivery on the job and resume its schedule. Keep demo/test delivery blocked and
  per-school opt-in required. No new customer emails have been sent by this job.
  Capture covers confirmed future parties, including existing
  bookings when the school first enables notifications. Draft messages are not
  silently backfilled into live delivery; Owners can explicitly queue eligible drafts.
- Hosted storage remains a release gate: Render API inspection found no persistent
  disks. Current uploads remain disabled outside DEBUG unless
  BOOKINGS_GUIDE_STORAGE_READY=True. Do not enable this until durable storage and
  retained-version downloads are verified. Ketan selected Cloudflare R2. Inspection
  of the Pontora Cloudflare account initially found R2 not activated. Ketan activated
  the subscription ($0 base fee, usage charges above the free allowance). Created
  `pontora-booking-guides` with Standard storage and verified Public Access Disabled.
  Ketan approved a bucket-limited Object Read & Write credential, now created and
  saved in Render using Save only (no deployment). The four BOOKINGS_GUIDE_R2_*
  settings apply only to DesignGuide.file through the standard django-storages
  adapter; enrollment and static storage remain unchanged. Migration 0034 records
  the storage callable, with no file copying. Synthetic R2 upload/read/replacement
  retention/fresh-client reads passed; only those synthetic objects were removed.
  Partial credentials and invalid endpoints fail closed; SDK signed-request debug
  logs are suppressed. Hosted upload readiness and email delivery remain off until
  deployment verification. No credentials were saved in repository files.
- Exactly two explicitly approved synthetic test emails were accepted by Resend
  for ketan@mypontora.com (confirmation and reminder). No other recipient was sent
  email, no live reservation was created, and credentials were neither printed nor
  saved. Inbox receipt remains for Ketan to confirm. The isolated preview server's
  outbound network protections were left unchanged.
- Browser checks exercised the settings save and notification preview, plus desktop
  and mobile layout. Browser file selection was blocked by the extension's disabled
  file-URL access; the supplied PDF was loaded through the same validated service,
  with upload HTTP/permission/replacement behavior covered by automated tests.
  Full regression before R2 integration: 4,314 passed, 3 skipped (511.14 seconds);
  three deprecation warnings. After R2 integration: 33 focused storage, guide and
  notification tests passed; no migration drift. Browser guide download succeeded;
  mobile document width matched viewport width (433px). Recurring hosted job is
  provisioned but activation and final release verification remain pending; do not
  mark slice 4 shipped.
- Final release verification on October 22: 4,326 tests passed, 3 skipped and three
  deprecation warnings (516.30 seconds); 80 browser-control tests passed with the
  existing coverage thresholds. Follow-up error-path tests raised focused coverage
  to 100% for the shared command and guide views, and 98.47% for party notifications;
  all 159 focused tests passed. Generated private guest-list navigation and guide
  download were verified against the running preview. Release PR #5 awaits CI and
  hosted verification; deployment/activation are not implied by these local results.

October 20 package-configuration implementation (local only):

- Extended the existing PartyPackage model, setup service and inline table;
  pricing stays inline and each party opens its booking rules in a compact modal
  over the same table (October 20 UX follow-up).
  Added observer allowance, extra-person limit/rate, assigned room/table, full-studio
  space and intended booking method. No location-specific code branches.
- Optional package sales-tax percentage supports an exclusive flat-rate quote,
  rounded once to cents on base plus extra participants. Blank means unconfigured,
  not an approved zero rate. This is not Square automatic-tax integration or a
  resolution of Jasmine's tax/deposit questions. Real rates remain unset locally.
- Inquiry quotes now use configured extras and preserve the complete price/tax,
  deposit, space and allowance snapshot. Later settings edits do not rewrite it.
  Direct booking is configuration only in this slice: current submissions still
  require staff review, and live party checkout remains the next slice.
- Full-studio claims reuse the existing profile transaction lock and active-hold
  rules; they conflict with other rooms, published sessions, camp occurrences
  and scheduled lessons at the location. Private-room/table bookings retain
  room-level exclusivity. Existing Room capacity means participants, not fire-code
  occupancy including observers; physical occupancy rules still need confirmation.
- Confirmed customer values are retained in the school YAML, with a New York
  override. They are not automatically applied to live records. Only the existing
  Pasadena A/B draft fixtures were updated during manual testing; they remain
  unpublished, tax unset, extra-person limit zero, with the confirmed $65 rate
  stored for future activation. Other saved packages were not overwritten.
- Still pending: party slot/buffer policy, approved space mapping, extra-person
  eligibility and observer changes, tax/deposit treatment, and direct customer
  booking/payment handoff. No deployment or live provider changes in this slice.
- Verification: full local regression passed before final form/validation cleanup;
  final focused setup/party/reservation/lesson regression passed 351 tests, including
  concurrent studio/table claims and both lesson-conflict directions. All 76 JS
  tests passed with the existing coverage gate. No migration drift; diff check clean.
  Manually exercised the existing Plushie login on port 8002: pricing/rules save,
  preserved values, invalid tax errors, cancel, and mobile table containment.
  Browser testing caught and fixed missing hidden-field form ownership; a rendered
  HTML regression now asserts every package field belongs to the submitted form.

October 20 direct-booking slice (local only): Ketan selected explicitly published
party times, not recurring opening hours. Booking rules now lets authorized staff
publish or withdraw start times in the location's timezone. Direct parties require
an assigned room, duration, price and explicit tax configuration. Customers choose
a published time; custom/review requests retain preferred date/time. Reservation
capacity, order creation, deposit/full-payment checkout and verified settlement
reuse the existing services. Duplicate submissions and competing room claims are
checked transactionally; expired holds cannot displace a newer reservation.

Guest designs may be deferred for direct bookings: staff see a pending-materials
notice, no stock is falsely allocated, and preparation/completion remain blocked.
The private host/staff guest-list editing workflow is still required to resolve
that pending state. Initial phone/birthday fields, the full initial-form/preparation
split and approved-inquiry payment handoff also remain unfinished. Published times
are not a live availability calendar: conflicting choices are rejected on submission
and capacity is checked again at payment. This slice is not launch-ready on its own.

Manual verification on the existing port 8002 and preview DB covered publishing,
withdrawing, unsaved-rule protection, customer location/package switching and the
mobile no-times state. A real Square Sandbox payment confirmed a $495 test party
with a $150 deposit and $345 balance; no live charge or production changes were made.
The temporary test package/time is unpublished after verification. Final serial
regression: 4,239 Python tests passed, 3 skipped, two existing datetime deprecation
warnings; all 78 JavaScript tests passed. Migration drift and diff checks passed.
The earlier overlapping test runs were invalidated and are not counted as evidence.

#### Policies and Continuing Launch Dependencies

October 21 booking/guest-list completion (local only): slices 1-3 now have their
planned local implementation, subject to acceptance and the launch gates below.
This does not mark the remaining three slices complete or authorize deployment.

- Package-based initial intake and staff review defer participant preparation to
  the confirmed reservation. Historical selections are preserved; legacy intake
  without party packages retains its established behavior. Removed the obsolete
  upfront-selection toggle rather than keeping a second package intake path.
- Approved inquiries reuse the existing review, quote, reservation and checkout
  services. Staff can copy the private payment link from the reservation. Links
  expire after 24 hours; reopening the reservation generates a fresh link. The
  existing 30-minute hold is displayed, and expired holds must pass capacity checks
  again before checkout. No email is sent by this action.
- Confirmed customer payment pages link to guest follow-up. Hosts and authorized
  Owner/Scheduler staff edit the same guest data and revision through one save
  service. Viewers cannot edit. Staff changes identify the actor in the audit log.
  Individual ages are optional; names/ages survive drafts and submission. Staff
  can enter emailed details without impersonating the host. Existing stock,
  prepared/closed-state, stale-edit and concurrent-submission protections remain.
- Short-notice follow-up displays selections as due now; the YAML deadline remains
  advisory, with no automatic rejection merely because the requested deadline passed.
- Manual checks used the existing port 8002 and preview database: custom inquiry
  through staff review and reservation creation, copied payment link and customer
  payment page, staff draft/submission with age, audit attribution, host follow-up,
  and desktop/mobile layout. The temporary unpaid handoff reservation was canceled
  afterward. The existing Local party verification reservation remains available.
- No live payment, email, new database, port, push or deployment. Current booking
  account selection still requires the existing connected test account; live
  merchant activation and verified production delivery remain launch work.
- Verification: full regression 4,286 passed, 3 skipped (two existing datetime
  deprecation warnings); final affected-area regression 273 passed after the
  staff-review/deadline refinements. All 80 JavaScript tests passed; the removed
  test covered the intentionally removed upfront-selection toggle. Migration
  drift and diff checks passed. Payment coverage includes approved-quote handoff,
  signed-link expiry/tenant isolation, expired-hold conflicts and duplicate retries.

Next: slice 4 (design-guide management and notifications), then slice 5 (calendar
details/notes/policies), then slice 6 (location access and integrated cutover).
Real package/tax/space configuration still needs approval before publication.

October 21 guest-list follow-up (local only): confirmed party reservations with a
catalog have a signed, school/reservation-scoped host link, available from the
existing reservation screen. Hosts reuse the visual participant editor to save
progress and submit all selections. Drafts do not allocate inventory; submitted
changes reuse the tenant lock and stock allocator, with stale revision protection,
stock validation and audit events. Unchanged selections retain frozen recipes.
Both reviewed and direct parties can reserve before guest selections are supplied;
preparation remains blocked until the selections/material allocations are complete.
The requested deadline is configured in the normal school YAML under
`scheduling.guest_list_deadline_days` and frozen on new reservation snapshots.
Plushie's value is seven days. It is advisory, not an automatic late-submission ban;
editing closes at the event start or once prepared, and unconfirmed/canceled/completed
reservations cannot be edited. Staff see submitted selections and draft counts.
Manual browser checks covered saving/reopening progress, six-guest submission,
participant limit, staff visibility, copying the link and narrow-screen layout.
Verification: full regression 4,272 passed, 3 skipped (two existing datetime
deprecation warnings). After the final review refinements, 59 focused guest-list,
inventory and reservation-workspace tests passed, including simultaneous submission,
expired links, CSRF, stock rollback, frozen recipes and closed reservation states.
All 81 JavaScript tests passed. Migration drift and diff checks passed; this
increment requires no new migration. Browser testing caught and corrected an
over-restrictive referrer policy that blocked same-origin form posts.
The existing sandbox booking named Local party verification retains these test
selections for user review. No live charge, email, push or deployment occurred.
Automatic email delivery/reminders and PDF management remain separate work. Initial
intake still supports entering selections upfront; it has not been removed in this
increment. The earlier statement below that secure follow-up was incomplete is now
superseded by this entry.

October 21 party timing UX (local only): the shared inquiry URL starts without a
selected location. Space preference precedes preferred time for custom requests;
package space is shown in the summary. Direct packages use the existing calendar
library with published dates and location-timezone time buttons, stacked on mobile.
Changing date, package or location clears incompatible time selection. Public labels
use required asterisks without redundant optional wording. Verification: 131 focused
Python tests and 80 JavaScript tests passed, plus desktop/mobile browser checks of
blank location, direct time selection, custom requests and location switching.
Temporary test package/time unpublished afterward. Existing port 8002 and preview
database reused; no push, deployment or payment logic changes.

October 21 initial-contact increment (local only): extended the existing party
form/model with optional phone and conditional birthday name/age. Free-text
occasions remain supported; birthday details are discarded for other occasions.
Staff review displays the fields and reservation creation preserves them in the
existing snapshot. Existing records remain valid with blank values. Age range
remains optional. One additive migration (0032) is applied to the existing preview
DB. The initial/preparation split and secure host guest-list follow-up are NOT
complete; existing optional design entry stays available until that flow replaces it.
Verification: 193 party/package/reservation tests and all 79 JavaScript tests passed;
manual desktop/narrow-screen conditional fields, location preservation, customer
submission and staff visibility passed on port 8002. No push/deployment or live
payment changes. Browser review also observed an existing navigation issue: changing
location from Party inquiries opens Party packages; track for the next navigation fix.

- Participants must be at least 7. Each reserves one spot and makes one plushie;
  no sharing spots or multiple plushies per participant during one workshop.
- Party observers are limited to the selected package allowance. Regular
  workshops continue to prohibit non-participating observers.
- Party deposits are non-refundable and applicable only to other parties;
  precise credit/rescheduling interpretation remains to be confirmed.
- Rescheduling requires at least seven days and is subject to availability.
  Under-18 participants require a parent/legal-guardian waiver. Organizer policy
  acknowledgment alone is not an individual guardian waiver.
- No outside meals/snacks/beverages; party exceptions are cake/cupcakes and water.
- Policy URL supplied by Jasmine: https://www.theplushieworkshop.com/booking-and-cancellation-policy
  Contents have not been reviewed in this requirements update; reconcile them
  with her email before publishing final wording.
- Accessories and remaining balances stay in-person Square Terminal payments,
  manually recorded using existing functionality. Workshop deposit configuration
  remains available and currently zero; preserve full-payment choice pending
  clarification rather than silently removing it.
- L01 live Square authorization/location mapping/webhooks remains a launch blocker.
  L08 real materials/preparation validation and L10 existing-booking migration,
  cutover, acceptance, rollback and agreed Stripe subscription/trial setup remain.
  Demo/prod DB separation, superadmin-only location creation and combined-product
  testing retain their previously agreed deferred status.

#### Questions for the Next Customer Update (Do Not Send Yet)

Ketan wants to demonstrate significant progress before asking another batch.
Proceed with configurable foundations; leave unresolved live behavior gated.

1. Availability: party slots/buffers, table/studio capacity and permitted overlap;
   which packages allow extra participants and whether observer allowances change.
2. Money: tax calculation/deposit tax treatment, confirmation of the revised
   standard $150 deposit, optional full payment and deposit-credit/rescheduling rules.
3. Operations: staff/location assignments, notification recipients, preferred
   waiver collection, guest-list late edits and approval payment-link hold expiry.
4. Assets/cutover: final guide and attachment versus current-guide link preference;
   acceptance of restricted Pontora staff access versus essential Notion needs,
   existing bookings/launch timing and Square merchant/location authorization.

### October 19 Release Preflight

- Scope: manual Square Terminal payment recording/corrections and extra purchases;
  per-location party packages, inquiry guest previews and navigation/UI fixes.
- Full local regression: 4,183 passed, 3 skipped. Measured Python combined
  statement/branch coverage: 98.93%; every measured module exceeds 90%.
  JavaScript: 64 tests passed with the existing coverage gate. Migration drift
  and whitespace checks passed. Desktop/mobile inquiry checks are recorded below.
- Six pending schema migrations were reviewed. Render's existing startup runs
  migrations before Gunicorn. A fresh Render-only database export completed on
  October 19 at 9:27 PM Pacific; no production database was downloaded.
- SBMC's hosted billing baseline remains its active Custom/Founder plan. This
  release does not change its provider, subscription, credentials or pricing.
- Local party configuration is not copied into hosted data by deployment.
  Private host guest-list follow-up and final launch policy/configuration remain
  pending; this release is not a declaration that Plushie is ready for live payments.
- Released through PR #4 after GitHub CI passed (run 39370081682). Merge commit
  `6bb0c0e` deployed successfully on Render October 19 at 9:54 PM Pacific;
  deploy `dep-dbbf78ks728c73bcdutg`. All six migrations reported OK and Gunicorn
  became live. The existing duplicate Jazzmin tag-library warning remains.
- Hosted smoke checks: party inquiry and Setup pages load; Add next stops at
  the participant count, deletion renumbers guests and enables adding again.
  SBMC billing content exactly matches the pre-release baseline. Hosted party
  packages are unconfigured; local fixtures were not copied or published.
- Release branch removed after merge. The unrelated nested worktree was left
  untouched. Next proposed work is the private host guest-list follow-up (L04).

### Plushie Launch Work Summary (October 17)

Status here distinguishes implementation, customer decisions, and deployment. Local
completion is not production acceptance. Earlier milestone notes remain historical.

| Work item | Current status | Remaining work |
| --- | --- | --- |
| Configurable deposits, full-payment choice and later online balance collection | **Deployed October 16; not equivalent to Terminal payment recording.** Customers can pay a deposit or full amount; staff can initiate online balance collection. Jasmine confirms party deposits are typically $150 for 6 guests and $300 for 12. | Configure approved party packages; clarify custom sizes and workshop payment rules. For launch, Jasmine chooses existing Square Terminals plus manual recording in Pontora, tracked separately below. Existing hosted workshops retain full payment until configured. |
| Customer payment copy and workshop settings presentation | **Deployed October 16.** Removed the redundant Deposit option row and repetitive demo/test checkout wording; preserved deposit/full-payment behavior and sandbox safeguards. | Jasmine's requested customer-journey changes, if any. |
| Configurable Stripe/Square booking provider | **Code deployed October 16; real Square sandbox flows tested locally.** Superadmin-controlled; existing orders and refunds retain their original provider. No hosted provider was switched. | Hosted sandbox credentials/webhook activation and delivery/retry verification; production merchant connection and launch acceptance. |
| Live Square merchant connection | **Not complete; launch blocker for real Square payments.** One Square seller account across all locations is now confirmed. | OAuth/token lifecycle, Jasmine's account authorization, exact Square location mapping, reviewed live-order support, and live payment/refund/reconciliation checks. Do not treat sandbox credentials as production setup. |
| Negotiated Pontora subscription pricing | **Deployed October 14 (P03).** Supports agreed base/location prices and next-renewal price changes without proration. | Configure the client's agreed live prices and location quantities; notify them of later billing changes. Pontora subscriptions remain on Stripe. |
| Final workshop/customer journey and studio policies | **Partially confirmed October 17.** Party details at booking, deferred guest selections, observer rules and shared/private-space distinction are specified below. | Obtain actual policy text, package details and design guide; settle remaining decisions and get acceptance. |
| Superadmin-only creation of additional locations | **Deferred; not a go-live blocker.** | Restrict operational location creation separately from the already implemented subscription billing quantities. |
| Separate production and demo databases | **Deferred; not a go-live blocker.** | Plan and test the split. Until then retain existing demo/production isolation safeguards; shared storage is not physical separation. |
| One school using both enrollment and scheduling | **Deferred; not a go-live blocker.** | Configure a combined local test school and verify navigation, permissions, billing and workflows together. |

### Jasmine Response and Launch Checklist (October 17)

Source: Jasmine's email pasted by Ketan on October 17, plus the attached calendar
and Square Terminal images. This update supersedes conflicting open assumptions
in the October 13 launch-plan document and older notes below; it does not change
the signed contract or authorize new implementation. The contract is **reported
signed by Jasmine**; its executed contents have not been verified in this update.

Confirmed decisions to retain for future work:

- All locations use **one Square account**. Separate location IDs/mappings still
  need verification; the account confirmation does not establish those IDs.
- In-person devices are **Square Terminals**. Launch balance workflow: staff take
  payment on the existing device, then manually record it against the Pontora
  reservation. Device integration and automatic matching are deferred.
- Party deposits are fixed by package: typically **$150 for six participants,
  $300 for twelve**, described as $150 per six people. This is not a percentage,
  not a regular-workshop rule, and not permission to invent rounding for custom
  sizes. Store explicit package deposits until the exceptions are confirmed.
- Collect estimated participants/package, occasion and general age range upfront.
  Final guest names, plushie designs and colors can follow approximately one week
  before the event. Attach a shareable design guide; the actual guide is pending.
- Regular workshops do not allow non-participating observers. Parties must state
  the observers included in their package. A reserved party area is not exclusive
  use of the whole studio unless full-studio booking was selected.
- Each location currently reports material counts manually to central management.
  Retain the customer-design catalog versus raw-material inventory distinction.
  This is not a requirement to synchronize Square's inventory or accessory sales.
- The calendar screenshot is a reference for a weekly view, location/service
  filters and booked/capacity counts. Overlapping sessions are visible: do not
  infer an overlap is an error, or hardcode a six-person capacity. Event/custom
  locations in the filter are not automatically permanent or billable locations.
- Pontora subscriptions remain Stripe and follow the agreed 30-day trial and
  school-specific pricing. The Square decision concerns customer booking money.

| ID | Launch work | Status and completion boundary |
| --- | --- | --- |
| L01 | Connect live Square for online booking payments | Sandbox foundation deployed. October 23 local work: real OAuth connection/mapping and full/deposit/balance payments verified; lost-response recovery prevented duplicate charges; both refunds reconciled (one needed an operator retry with its original key). Environment-bound live mapping/checkout/refund/webhook code is implemented locally behind a default-off activation gate; 791 regression tests passed. No live configuration or deployment changed. Hosted webhook delivery, renewal scheduling/monitoring, Jasmine's authorization/location verification, live acceptance and the uncertain-refund operator runbook remain launch gates. SBMC and Pontora subscription Stripe behavior remain unchanged. See `square-booking-payments.md` for evidence and limitations. |
| L02 | Record a payment taken on a Square Terminal | **Implemented locally October 17; simplified and extended October 19; not deployed.** Record payment already received opens a dialog for amount, payment date and optional receipt number; staff identity and studio are automatic. Notes and payment-area expanders are removed. Take online payment separately opens checkout for a new charge. Supports partial/final balance and explicitly confirmed extra purchases, including after full payment; corrections reverse both the receipt and its extra charge. Submission-retry/stale-total protection and append-only history remain. Duplicate receipt detection applies when a number is supplied. Reuses existing owner/superadmin permissions; no new location permission or inventory-deduction system. See the local walkthrough in `docs/square-booking-payments.md`. Staff acceptance is still required. |
| L03 | Party packages and initial form | **Package/inquiry foundation implemented locally October 19; not deployed.** Setup > Parties edits package values in their table cells, with save/cancel actions. The customer link follows the request host and Preview opens a new tab. The complete inquiry opens immediately; changing location refreshes packages/catalog without discarding contact details. The existing workshop image/color picker is shared with party selections. Customer-requested duration and the non-participating guest field are removed. Package duration comes from the configured offer; custom duration is set during staff review before reservation approval. Inquiries snapshot the offer and collect occasion/participants' ages. Existing staff review is reused; no automatic booking/payment. Approval/payment handoff and deferred-selection reservations remain launch dependencies; observer policy wording is still required separately under L05. |
| L04 | Deferred party guest selections and preparation | **Not complete.** Provide a private host follow-up flow linked to the same reservation, with guest names, separate design/color choices, design guide and staff completion visibility. Define the final deadline, late bookings, changes and reminders. Missing selections must remain visible to preparation and must not imply materials are ready or fully allocated. |
| L05 | Policies and customer journey | Carry approved booking/cancellation, observer and shared/private-space terms into the relevant selection, review and confirmation steps without repetitive copy. Get the current text, package rules and guide; review the final desktop/mobile flow with Jasmine. |
| L06 | Staff bookings and custom parties | Remains from the October 13 call. Validate phone/manual booking and custom groups such as 24 guests. Obtain a sample invoice and confirm minimum launch support. Online payable custom invoices/emailed links are not implied by accepting manual Terminal collection; agree any deferral explicitly. |
| L07 | Calendar and analytics | Use the screenshot to finalize weekly calendar scope, location/service filters, participant/capacity counts and opening a reservation. Confirm legitimate overlapping sessions/resources and the small set of launch reports. Screenshot alone does not define analytics. |
| L08 | Materials inventory and Workshop Prep | Existing foundation; validate location stock entry, central visibility, shortages and consumption against real recipes/counts. Determine how delayed party selections affect material reservations and preparation. No automatic Square inventory integration. |
| L09 | Customer confirmations and host follow-up | Verify actual delivery of agreed booking/payment confirmations and the host selection link. Do not treat local message previews as sent emails. Agree whether one-week reminders are manual or automatic; avoid adding a general messaging engine. |
| L10 | Launch configuration, acceptance and cutover | Confirm Wix entry point, authoritative booking/calendar system, Notion's remaining role, launch locations and existing-booking handling. Load approved real data, verify staff roles and training, protect demo/live boundaries, run SBMC/payment regressions and desktop/mobile acceptance, then agree go-live and rollback. Verify signed contract and configure agreed subscription prices/trial without inventing terms. |

Items already delivered: configurable deposit/full-payment choice and online
balance collection, booking payment presentation cleanup, and negotiated Pontora
subscription pricing. These are foundations, not a claim that L01-L10 are complete.

October 19 individual-guest inquiry update (local only): the initial customer form
now uses numbered guest rows, optional names, the shared design/color picker,
Add next, and trash removal with renumbering. The entered participant count limits
rows in the browser and is validated on submission; quantity is fixed at one per
guest server-side. Existing formset safety maximum remains 50 rows. Names are
retained in the material snapshot and staff review. This does not complete L04's
private follow-up link, deadline, reminders, or deferred-selection preparation.

October 19 party UX verification: 4,177 Python tests passed, 3 skipped (full suite,
existing test database, `-p testing.network` enabled); 34 focused JavaScript tests
passed. Migration drift and whitespace checks passed. Browser checks on the existing
8002 server covered cell editing/save/cancel, rejected invalid prices, new-tab preview,
full inquiry rendering, location switching without losing contact details, shared image
selection, added rows, visible missing-selection errors, successful local inquiry
submission, regular workshop picker regression, and 390px/desktop layouts. Host-aware
share links are tested for localhost, demo and app. Local migration 0028 removes the
unused guest allowance field and permits an unset custom inquiry duration; staff must
set duration before approval. Nothing was pushed or deployed.

Post-launch/deferred: automatic Square Terminal initiation/matching, automated
emailed balance-payment links, superadmin-only location creation (P02), physical
demo/production database separation (P04), and combined enrollment/scheduling
pilot (P05). Custom-party invoice scope remains open, not silently deferred.

Targeted clarification list for the working session:

1. Package sheet: total prices, participants, observers, duration and shared-area
   versus full-studio options. How should custom sizes and added/removed guests
   change price/deposit? Do regular workshops remain full payment, or offer a deposit?
2. Current booking/cancellation/refund/rescheduling/no-show terms, including when
   the balance is due and how refunds of Terminal payments are recorded.
3. Actual design guide and desired host workflow: exactly seven days or flexible,
   who submits the guest list, changes after cutoff, late bookings, and reminders.
4. Party confirmation and custom orders: staff approval before collecting a
   deposit or immediate booking? Sample custom invoice; which invoice/private-link
   functionality is needed on day one?
5. During account setup, map each launch studio to its Square location and walk
   through a sample Terminal receipt/reference. Authorize the connection securely;
   do not request passwords or tokens by email. Confirm tax treatment and that
   unrelated accessory sales stay outside the reservation balance.
6. In the working session, confirm calendar overlap/resources, essential reports,
   inventory update examples, Wix entry point/Notion role and existing-booking
   cutover. Reuse existing supplied data rather than asking for it twice.

### Pontora Decisions After Jasmine Response (October 17)

#### October 19 Package Implementation and Remaining Decisions

- Local entry: `http://localhost:8002/bookings/plushumi-demo/parties/setup/`.
  Reuses existing Setup forms, permission checks, profile locking, stale-edit
  tokens, audit and party review. No production deployment or payment-provider
  changes in this package work. Migration `scheduling.0026` is additive;
  `0027` removes the three unnecessary location switches introduced in `0026`.
- October 19 UI correction: inline package editing, a shared customer inquiry
  link with location selection, an authorized preview return link, optional
  deposit defaulting to zero, and optional guest selections now/later replace
  the separate party settings page. Calendar time/footer overlap and placeholder
  corrected. A published package on an unpublished/party-disabled workshop is
  explicitly shown as hidden; it does not bypass the workshop's visibility rules.
- UI correction verification: 214 focused Python tests and 19 JavaScript checks
  passed; the earlier 189-test run included inventory and migration checks.
  Desktop and 390px phone checks covered inline edit/save, date-picker spacing,
  now/later selections, inquiry submission, preview return and demo entry.
  Removed only the synthetic inquiry made during this check. The existing local
  preview harness no longer shadows the maintained integration-options route.
  No full-suite rerun or deployment is claimed for this UI correction.
- Package settings live in the database through the offering's location.
  Changing defaults does not rewrite submitted inquiry or reservation snapshots.
  Server-side selection supplies prices/duration/space, never browser-supplied
  monetary values. Incomplete packages cannot be published.
- The local Plushie database has two unpublished drafts at each existing
  party-enabled studio: six guests/$150 deposit and twelve guests/$300 deposit.
  Seed amounts are in `configs/booking_demos/plushumi-demo.yaml`; live runtime
  reads database records, not the demo loader. Names are editable placeholders.
  Totals, durations and observer allowances are intentionally unset, not invented.
- Local future workshop deposits are zero; accepted reservation amounts remain
  unchanged. School admins retain workshop deposit controls.
- Custom requests receive an explicit staff quote. No rounding up to six-person
  blocks, automatic extra-person charge or automatic payment/room hold is assumed.
- Deferred initial selections are supported, but L04 is NOT implemented by this:
  there is no host guest-list link, seven-day enforcement or reminder yet. Existing
  stock checks still require complete selections before creating an inventory-backed
  reservation. Do not remove these checks to make an incomplete flow appear ready.
- Observer allowances are descriptive package terms, not new occupancy accounting.
  Resolve total physical occupancy and full-studio exclusivity before paid party
  launch. The current private-room option is not a full-studio buyout.

Questions still requiring Jasmine's information or decisions:

- Provide the current package sheet per location: final names, total prices,
  duration, participant limits, observer allowances and shared/private/full-studio
  terms. These values are editable; they do not require customer-specific code.
- Must staff approve every party before payment? After approval, how long should
  a customer have to pay, and should the date/space be held during that period?
- For custom sizes (for example 7, 13 or 24), is pricing by package, extra person
  or individual quote? Are extra observers allowed, charged, and counted in each
  room's maximum occupancy? Provide one representative custom invoice.
- Send approved cancellation, refund, rescheduling, no-show and balance-due terms.
  Are displayed prices tax-inclusive or should tax be added, and which extras are
  taxable? No tax behavior should be inferred from a sample deposit.
- Send the design guide. Who submits the final guest list, what happens with
  bookings inside seven days, and can hosts amend selections after the due date?
  Seven days remains the agreed configurable school default, not an automatic lock.
  Confirm whether a free-text "Participants' ages" field suits staff or whether
  Jasmine prefers specific age categories; do not invent categories.
- Confirm which customer emails and reminders must be automatic at launch and
  provide any preferred wording. Local previews do not establish actual delivery.
- During secure Square setup, authorize the seller account and map the launch
  studios to its Square locations. No account password/token should be emailed.
- In the working session, confirm launch studios/date, existing-booking cutover,
  Wix entry point and Notion's remaining role, simultaneous room/session rules,
  essential calendar/reports, and a real inventory update/recipe example.

Local test walkthrough:

1. Open Setup > Parties and select a location. Edit either draft; attempting to
   publish with missing price/duration/observers must show field errors.
2. Fill confirmed values, then publish. Open inquiry form at the same location;
   select the package and verify its total, deposit and allowance.
3. Submit a future date, estimated participants, occasion and age range. An
   oversized group must be rejected; Custom group permits a staff-quoted request.
4. Open Party inquiries and verify the submitted package and proposal. Editing
   package defaults afterward must leave this inquiry unchanged.
5. Keep client packages unpublished until confirmed. The temporary QA package and
   inquiry used during browser verification were removed; no booking/payment was
   created. Do not test a real charge as part of this inquiry-only slice.

Verification October 19: full regression 4,166 passed / 3 skipped / 2 existing
warnings; after final self-review adjustments, 195 focused party/setup/reservation/
migration tests passed. All 59 JavaScript tests passed. Checked desktop and 390px
mobile editing, incomplete-publication errors, package selection, inquiry submission
and saved staff details. Query-count regression verifies additional locations/packages
do not introduce per-row queries. Migration drift and diff whitespace checks passed.

#### Earlier Decisions (Still Applicable Unless Superseded Above)

These decisions supersede the workshop-deposit and deadline questions above.
They are implementation requirements, not a claim that configuration was applied.

- Keep workshop deposit controls available to authorized school admins. Set
  Plushie's current workshop deposits to zero (full payment), without rewriting
  existing reservations or changing other schools. Jasmine may enable/tune them
  later. Do not interpret zero as a free booking.
- Add editable party-package defaults of $150 for six guests and $300 for twelve;
  preserve the existing per-reservation quote/deposit controls. Later package edits
  affect new quotes, not accepted reservation amounts. Keep the customer option
  to pay in full. Custom group sizes need an explicit quote, not guessed rounding.
- Set the guest-list lead time to seven days as a school-specific YAML policy,
  with no new admin-settings field. Plushie currently has only
  `configs/booking_demos/plushumi-demo.yaml`; its loader is demo-specific. Integrate
  the policy with the school configuration used by the real guest-list workflow,
  rather than making production reservations depend on a demo-only adapter.
  Do not add an unused YAML key and claim the deadline is enforced. Preserve each
  reservation's agreed deadline when later defaults change; use its studio timezone.
  Treat seven days as the due date, not an automatic cancellation or editing lock.
  Late bookings and post-deadline amendments remain workflow decisions.
- Next implementation is manual recording of Terminal payments (L02). No Square
  device credentials or hardware are needed to build/test that recording action.
  Reuse reservation financial totals and audit. Default to existing authorized
  payment-management roles and location scope, not every staff member.
- Proposed minimum form: amount (defaults to outstanding balance), payment date,
  required Square payment/receipt reference and optional note. Derive the studio
  from the reservation; save who recorded it and when. Label as staff-recorded,
  never provider-verified. Correction reverses the recorded entry with a reason;
  it does not issue a Square refund. Actual Terminal refunds stay in Square and
  need explicit recording. No second charge, subscription change or automatic
  receipt claim is implied by saving this form.
- Tests: verified online deposit plus partial/final manually recorded balances;
  duplicate click/reference, stale totals, overpayment, invalid amounts, active
  checkout and provider-webhook races; correction/refund accounting; canceled and
  completed reservations; owner/location permissions and cross-school denial;
  Stripe/Square separation and SBMC regressions. Use fictional local records and
  sandbox online deposits, never live charges. Manually inspect desktop/mobile.
- Before operational acceptance, obtain one redacted Terminal receipt example to
  confirm which reference staff can reliably retrieve. A staff walkthrough using
  an existing transaction can validate the real workflow without a new charge.

Code review supporting L02: `scheduling.reservations.settle_offline` still exists,
but `core.payments.service.record_offline` accepts only test orders with an
`offline:<school>` merchant binding. The reservation UI intentionally removed
offline settlement earlier. It cannot record a Terminal balance against an online
Square deposit as-is. Reuse the financial ledger and audit, add a narrowly scoped
external-payment action, and explicitly test duplicates, online-checkout races,
cross-school/location access, corrections and refund routing. The requirements
update itself made no code changes; the local L02 implementation is recorded below.

### Terminal Payment Recording: Local Implementation (October 17)

October 19 update supersedes the required-reference/optional-note UI below:
receipt number is optional, Notes is removed, and the redundant
Payment & refund status section is removed. Recovery actions remain only for
unresolved payment/refund operations. Migration `payments.0007` is local only;
201 focused tests plus 144 overlapping Terminal/reservation/migration tests passed.
Desktop and 390px mobile checks passed. Existing notes and correction reasons are preserved.
The subsequent October 19 UI pass replaces all payment-area expanders with dialogs:
**Record payment already received** for Terminal money, **Take online payment** for
a new online charge. Receipt number is visible inside the dialog. Correction uses
a dialog too. 245 payment/reservation and 56 JavaScript tests passed; manual save,
correction, validation, date picker, keyboard and desktop/mobile checks passed.

October 19 extra-purchase update supersedes the balance-only restriction below:
receipts may exceed the outstanding balance, with the excess previewed as extra
purchases, including on fully paid confirmed/completed bookings. The extra-purchase
checkbox was removed at the user's request; Save payment record commits the amount.
The original booking quote stays immutable; active extra charges are included in
all displayed totals. Correct entry reverses both the receipt and its extra charge.
Migration `payments.0008` is local only. No inventory deductions or tax calculation
were introduced. Desktop/mobile checks verified $15 extras plus a further $5 after
full payment, customer totals and corrections, then restored the test booking.

- Added **Record Terminal payment** under the existing reservation Payment summary.
  Available to existing scheduling owners and superadmins for confirmed/completed
  workshop or party reservations with a balance. Scheduler/viewer roles cannot write.
  The school and studio are derived from the reservation; no new client-specific
  branch, provider setting, port or database was added.
- Uses the existing append-only payment ledger. Partial/full receipts update
  Received, Balance, customer payment status and the reservation grid. Each receipt
  retains date, reference, note, actor and recording time. Correct entry appends a
  reversal with a required reason; it does not increase Refunded or call Square.
- Active/uncertain online checkouts and pending refunds block recording/correction.
  Duplicate submissions, reused receipt references, stale balances, invalid amounts,
  future dates and overpayments are rejected. Incorrect receipts can be voided and
  re-entered while preserving the original audit trail. The old local demo reset
  refuses to delete Terminal receipts.
- One additive ledger migration, `payments.0006`, is applied to the existing local
  preview database only. No Render or production changes; no commit or deployment.
  Existing online payment evidence and school subscription settings are preserved.
- No Terminal API/device connection, provider verification, automatic receipt email,
  or recording of actual Terminal refunds is claimed. A correction is NOT a refund.
  Staff still take payments/refunds in Square. Actual refund recording requires a
  separate agreed workflow before handling that case operationally.
- Other October 17 requirements remain separate: zero Plushie workshop defaults,
  editable party-package defaults, and YAML-backed guest-list deadlines have not
  been implemented/configured by this Terminal-recording change.
- Local manual walkthrough and exact test evidence: see
  [Terminal payment recording](square-booking-payments.md#manual-terminal-payment-recording-october-17).

Deposit/payment verification: 2,008 backend and 53 JavaScript tests passed before
the copy-only cleanup; real Square sandbox deposit, balance decline/retry and
full-payment journeys were checked. For the copy cleanup, 249 of 250 focused tests
passed, then the remaining obsolete test-card-copy assertion was corrected and all
24 Stripe experience tests passed on rerun. Desktop and 390px mobile review layouts
were checked. No payment behavior was changed by the copy cleanup.

Release verification: **4,067 backend tests and 53 JavaScript tests passed in
pre-merge CI**, with three optional browser tests skipped. All three migrations
completed automatically on Render. SBMC billing and public enrollment matched
their pre-deployment content; hosted Plushie billing navigation, deposit controls,
booking page and modal passed smoke checks. No customer settings or credentials
were changed. [Release evidence](square-booking-payments.md#october-16-production-release-verification).

Next implementation recommendation: finish L02 local acceptance, then L03/L04
using the confirmed party-package and deferred guest-list requirements.
Hosted Square webhook activation still needs configuration approval. Automatic
Terminal matching and emailed balance links remain deferred; manual recording is
now a launch requirement and the completed online balance feature does not replace it.

- **Historical pre-release Square verification (superseded by October 16 deployment above).**
  Superadmin provider selection lives on the existing Ops school page. New booking
  orders select Stripe or Square; existing orders/refunds retain their account.
  Real Square sandbox card success, decline, retry and refund after switching
  providers verified. Enrollment fees and Pontora subscriptions stay Stripe;
  no SBMC or Render configuration changed. Full regression: 4,010 passed;
  follow-up payment regression: 236 passed; JavaScript: 52 passed. All three new
  Square payment/settings modules exceed 90% measured statement/branch coverage.
  Three migrations (`payments.0005`, `scheduling.0024`, `scheduling.0025`) remain local;
  the last adds the workshop deposit setting with full payment as the default.
  Hosted webhook delivery and Jasmine-owned live OAuth/location setup are launch
  gates, not completed features. [Scope, setup and release gates](square-booking-payments.md).

- **P03 / school-specific subscription pricing: deployed October 14, 2026.**
  Release `0302149` passed pre-merge CI and is live on the existing Render service. Additive
  migrations 0065/0066 applied automatically, both verified in startup logs. Fresh Render backup
  completed before merge; no production data downloaded. Full local suite: **3,945 passed**;
  JavaScript: **48 passed**. Pre-merge CI: **3,942 passed, 3 local-only browser tests skipped**;
  all measured modules meet the 90% coverage gate. The main-branch CI rerun also passed.
  SBMC's active Founder billing, admin and
  public enrollment pages passed live checks. Plushie demo and shared Billing navigation passed;
  demo subscription checkout remains paused. No customer prices or credentials changed.
  Historical local verification details follow; the release status here supersedes earlier
  undeployed notes. [Release evidence](school-subscription-pricing.md#production-release-verification).
  Superadmin-only validated Stripe prices, explicit additional-location billing quantities,
  agreed-rate school checkout, historical offers and retry-safe checkout identities are implemented.
  Standard pricing remains unchanged for unconfigured schools. Existing payment emails,
  seven-day grace and recovery are reused; feature access is not inferred from the negotiated amount.
  Base-price changes and added locations on an existing negotiated subscription can now be
  explicitly applied by the superadmin for the next renewal, without proration, an immediate invoice
  or a billing-date change. Saving an offer alone still does not reprice it. Billing-frequency
  changes and location removals remain outside this action.
  Full regression: 3,867 passed. Final payment/isolation/browser run: 388 passed; 48 JavaScript
  tests passed, with 99.38% measured
  combined coverage in the new service/form/view modules. Desktop/mobile walkthrough and critical
  self-review completed. Real Stripe sandbox Checkout decline/success, signed webhooks,
  test-clock monthly renewal failure/recovery, and local notification capture verified October 14.
  Follow-up regression: 373 passed. User acceptance is ready at http://localhost:8015/login/;
  separate local database, existing .env test key, no Render or SBMC changes.
  Additive core migration 0065 applied only to localhost preview; no commit, push or hosted changes.
  [Setup guide, impact review and manual checks](school-subscription-pricing.md).

  **Location renewals (October 14, local):** implemented and verified with real Stripe sandbox
  invoice previews and a test-clock renewal. First extra location and later quantity increases
  produced no immediate invoice; renewal charged the exact new amount. Desktop/mobile browser
  checks passed. 299 focused regressions passed; pricing service/view combined statement/branch
  coverage 97.61%. Additive migration 0066 applied only to the isolated local billing sandbox.
  No Render changes, live subscription changes, commit or push.

  **Base-price renewals (October 14, local):** the same action now replaces the base price,
  optionally alongside added locations. Signed current/new-rate reviews reject stale offers
  and changed renewal dates. Real Stripe sandbox verified increases/decreases, no immediate
  invoice, unchanged subscription/base-item/renewal, and a paid $119 test-clock renewal.
  Manual browser test: $129 to $159 next renewal, preserving four extra locations. No new
  migration beyond pending 0065/0066. Customer notification remains the operator's responsibility.
  Final verification: **323 focused backend/browser tests + 271 payment/trial/booking regressions
  passed (594 total)**; pricing service/view combined statement/branch coverage **98.16%**.
  Migration drift and whitespace checks passed. Two existing Jazzmin warnings remain unrelated.

<details>
<summary>P03: Manual review and Stripe setup</summary>

1. Use the local superadmin account and open Ops > Schools > school > Subscription pricing.
2. Check the Test/Live indicator. Validate a Price ID, inspect the total, then save the agreement.
3. Confirm school owners cannot edit prices or quantities, and see only their agreed subscription.
4. Confirm legacy schools still show their existing billing flow. Saving future pricing must not
   reprice an existing subscription or change its features, trial or payment credentials.
5. Read the [beginner Stripe instructions](school-subscription-pricing.md#stripe-setup-for-ketan).
   Test Price IDs supplied and verified. Needed from Ketan: local acceptance review; later the separate
   live Price IDs and final billable location counts. No secret keys in chat.
6. Keep Render's live Stripe mode unchanged. Finish real sandbox checkout, renewals and recovery
   before approving deployment. No live settings or subscriptions were changed during local testing.
7. For a paying negotiated school, change the base Price ID and/or increase Additional locations
   to bill, validate and save. Review current/new rates and the renewal date; notify the customer.
   Click Apply billing changes at next renewal. Verify the confirmation, unchanged renewal date and
   the school's Billing page showing the new total from that date. Repeated submissions must not
   add duplicate items; an uncertain update shows Retry billing update and blocks replacing its offer.

</details>

- **Superadmin directory correction: deployed; live directory verified.**
  The prior release incorrectly hid demo clients from the production Schools directory.
  The superadmin-only directory now lists all schools by default, labels their environment,
  and offers an All environments / Production / Demo filter. Edit and Open use the school's
  canonical host; scheduling-only clients open their bookings workspace. Audit logs,
  submissions, leads, permissions, and wrong-host write protection remain scoped.
  Manual checks: open Schools on the production host, find Plushie, filter each environment,
  open Plushie settings on the demo host, confirm the scheduling magic-link panel, and open
  the bookings workspace. Confirm SBMC still opens enrollment on the production host.
  Separate sign-in on each hostname remains intentional; no shared cookies were introduced.
  No migrations, payment changes, or customer configuration changes are needed.
  Full local regression suite and 48 JavaScript tests passed; the final 205-test focused
  run includes the added status-filter cases and documentation checks. Directory view
  and destination helper have 100% measured statement/branch coverage. Desktop and
  mobile browser checks covered search, environment filters, canonical navigation,
  scheduling magic-link controls, SBMC enrollment access, and production audit isolation.
  Release commit `8d42914` passed CI run `38299918041`: 3,804 Python tests,
  48 JavaScript tests, 99.45% measured combined coverage, and every measured module
  above 90%. Render deployment `dep-db6jqa59fdbs73fnkp5g` went live October 12 at
  20:05 UTC. Hosted checks confirmed all nine schools appear on the production directory,
  Plushie's Edit/Open URLs use the demo host, environment filters work, the SBMC dashboard
  opens normally, and production audit history excludes demo activity. Read-only checks
  confirmed SBMC and Render configuration fingerprints unchanged. After the user signed
  into the demo domain as superadmin, hosted verification also confirmed Plushie settings,
  scheduling magic-link controls, and Open School Admin reaching bookings as the operator.
  No cross-domain session bypass was added.

- **Demo access and portal separation: deployed and verified.**
  Canonical rule: `app.mypontora.com` is production, `demo.mypontora.com` is demo.
  Enrollment/bookings share one account menu. Except for the cross-environment superadmin
  school directory described above, operator/Django admin lists, form choices,
  reports, audit history and login/workspace selection are environment-scoped. Global
  operator accounts and infrastructure incidents remain shared; storage is not split.
  The demo account is `plushie_admin`; Customer demo opens the existing comparison
  hub. Superadmin can generate a scheduling-only passwordless link using the existing panel.
  Core migrations 0063 and 0064 label historical audit events and bind magic-link recipients.
  Exact-account maintenance deleted 65 old hosted audit entries after a verified Render
  backup; no reservations or payment records were deleted. Checkout/subscription processing
  and SBMC settings are unchanged.
  Desktop/390px checks passed for login, magic links, menus, themes, navigation and isolation.
  Final verification: **3,792 Python tests and 48 JavaScript tests passed**;
  every measured module exceeds 90% combined statement/branch coverage (99.45% overall).
  New magic-link, account maintenance, admin scoping and menu modules are 100% covered;
  portal helper 95.52%, audit attribution 98.33%. Migration drift check passed (existing
  Jazzmin warning). SBMC dashboard, submissions, leads, reports, settings, billing and
  public forms passed browser checks without data/payment changes. Critical review fixed
  missing branding-file fallback and removed the hardcoded business name from the shared
  demo banner. Final review also scoped the operator's add-member email lookup and safely
  rejects ambiguous emails; all 121 affected tests and the real browser form check passed.
  Render confirmed a completed backup dated October 12, 18:23 UTC; no production DB was
  copied locally. Revision `357a4b5` passed GitHub CI run `38291587182` before Render
  deployment `dep-db6inllg1s2s739fdh40` went live at October 12, 18:51 UTC.
  Rolling-deploy review: migrations retain database defaults for audit environment and
  magic-link destination, so pre-release workers can still insert records during rollout
  or a code rollback. The historical-model compatibility test and 29 access tests passed
  against a freshly migrated disposable database; the full regression also passed.
  Hosted checks verified normal and passwordless login, the superadmin Demo Access Link
  panel, shared menus, Customer demo navigation, real booking-page/popup participant
  navigation, phone layout/focus restoration, reciprocal operator list/audit separation,
  production Django admin scoping, and SBMC admin/billing/public forms. No new payment was
  submitted. Chrome extension message-channel warnings were present before and after
  deployment; no booking application console error was observed. Final read-only comparison
  confirmed unchanged SBMC school/subscription fields, submission count and existing Render
  settings. Core migrations 0063/0064 and their rolling-deploy defaults are verified live.
  [Scope and review URLs](scheduling-plushumi-demo.md#portal-access-and-environment-separation).

  <details>
  <summary>Manual review: shared menu, demo login and environment separation</summary>

  1. Locally use `booking-sandbox-reviewer` / `admin` at port 8006. Use `localhost`
     for demo and `127.0.0.1` for production-style routing; these use only the local preview DB.
  2. Compare the account menu on Plushumi bookings and SBMC enrollment. Verify matching
     controls, Clean/Midnight/Mint persistence, password link, Escape and outside-click close.
     Repeat at laptop and phone widths. Operator links should appear only for a superuser.
  3. Sign in as `plushie_admin` / `admin` on the demo host. It should open its scheduling
     workspace, not enrollment or the operator portal. A demo-only login must not select a
     production workspace. Do not change the shared demo password while rehearsing.
  4. Open a demo workspace through the production host and vice versa. Reads should switch
     to the matching host. A wrong-host write must fail without changing any record.
  5. As operator, compare schools, users, submissions, leads, reports, audit and Django admin
     on both hostnames. Searches, filters, dropdowns and Recent Actions must not reintroduce
     another environment. Background booking events follow their business's environment.
  6. In demo Ops > Schools > Plushumi, generate a Demo Access Link. Open it in a private
     window: scheduling opens without a password, with no enrollment/operator access.
     Generate a replacement: the previous link must stop working. A link tied to a disabled
     member/workspace must not log in. Never share the operator's own session.
  7. In the account menu, Customer demo must open `/demo/plushumi/integrations/`.
     Test both the full booking page and popup, including mobile navigation.
  8. Before release, back up the shared DB. Render startup applies migrations 0063/0064.
     Run exact-account maintenance only after dry-run review. Verify renamed normal login,
     new magic link, audit separation and SBMC pages on the hosted domains after release.

  </details>

- **Hosted booking sandbox: deployed and verified for the demo.** User approved Pontora's shared
  sandbox for demo businesses because Connect is not enabled. Superadmin payment-account settings
  replace the single-school environment switch; shared payment code has no customer-name branches.
  Dedicated booking test credentials and a separate signed test webhook are configured. Revision
  `398f887` is live after green CI; payment migration 0004 ran automatically. Hosted desktop/mobile,
  decline/success, unpaid retry, duplicate-event and webhook-only confirmation checks passed.
  3,748 Python and 49 JavaScript tests passed; measured scheduling/payment coverage is 99.48%.
  Baseline comparison confirms unchanged existing Render variables, SBMC school/subscription and
  working enrollment pages. User declined a paid recovery job; manual recovery is the accepted
  demo fallback. This does not complete broader B1 subscription/email work or a live paid pilot.

- **P2 preview cleanup: Ready for your test.** Comparison options now navigate to
  a full booking page or the popup's own page; the old inline URL redirects rather than nesting
  another complete page/header. Redundant preview/integration copy is removed. Real booking
  forms and concise simulated-payment notices remain. Desktop, laptop, mobile and local
  participant-to-confirmation browser checks passed. 3,705 Python and 49 JavaScript tests
  passed, with no migration drift. The changed redirect and popup JavaScript have 100%
  statement/branch coverage. No shared payment or Enrollment runtime changes.
  [Walkthrough](scheduling-plushumi-demo.md#preview-navigation-and-copy-cleanup).

- **B1 / Stripe activation follow-up: Planned, not operational yet.** The live platform
  webhook is missing invoice events needed for payment emails and seven-day recovery.
  Verify sender/recipient configuration, retry delivery, school-owned payment endpoints,
  and sandbox success/failure/card-update/recovery before enabling the live behavior.
  This is shared billing work for all schools, not Plushumi-only. No live billing settings
  are being changed as part of the demo presentation cleanup. Expand B1 below for the checklist.

- October 10: **Shared production release deployed and verified.** Revision `31220c5` is live on the existing Render service/database; all 28 migrations ran automatically. Scheduling remains disabled for existing schools. SBMC live admin/public pages and its active subscription passed checks. Plushumi has 15 fictional sample reservations plus one live-browser test booking. Inline/modal customer selection, simulated decline/success, staff preparation/audit, mobile sizing and access protections passed. 3,697 Python and 49 JavaScript tests passed; measured Python coverage is 99.48%, including the CI gate. Real Stripe/Wix/Notion connections remain gated. [Live comparison hub](https://demo.mypontora.com/demo/plushumi/integrations/) · [Release evidence and limitations](scheduling-shared-production-release.md).

- October 6: **Pasadena booking rehearsal is Ready for your test.** Cozy Creations has three October 11 sessions and four confirmed sample bookings / seven participants. Compact calendar/time slots, closer color swatches, larger selection preview and per-participant review/checkout thumbnails are verified at desktop/390px. **140 affected Python tests**, **38 JavaScript tests**, **98.84% measured Python coverage**; new additive seed is 100% covered. Existing demo data preserved, normal material holds applied, no migration/shared Enrollment/payment edit or push. [Manual walkthrough](scheduling-plushumi-demo.md#pasadena-booking-rehearsal).

- October 6: **Compact catalog settings and required color picker are Ready for your test.** Muted edit icons sit beside values. Color Add/Edit uses a visual picker with editable required hex; the global image field is removed without clearing saved images or changing design-specific photos. Age choices remain unchanged. **183 affected Python tests passed**, inventory forms/views **97.08% combined coverage**, plus **38 JavaScript checks** with 100% new-picker coverage. Desktop/390px layout and picker/validation browser checks passed. No migration, shared Enrollment/payment edit, saved demo-data change or push. [Verification and manual steps](scheduling-plushumi-demo.md#catalog-color-settings).

- October 6: **Compact design editor is Ready for your test.** The previous large, blue-outlined color cards were too noisy. Add/Edit design now uses aligned checkbox/swatch/name/photo rows with shared column labels, compact spacing and side-by-side main fields on laptops. Phone layouts keep colors and URLs aligned with no page overflow; theme colors and accessible input labels are preserved. **177 affected regression tests passed**, including add/edit validation and the empty-color state. Desktop/390px, dark theme, selection and invalid-URL browser checks passed. Presentation-only: no backend rules, migration, saved-data change, shared enrollment edit or push. [Review and steps](scheduling-plushumi-demo.md#compact-design-editor).

- October 6: **Catalog usability is Ready for your test.** All nine requested changes are implemented: scoped View Catalog links, color swatches and per-color design photos, recipe remove-row controls, Catalog settings, stock alerts and reference-style date/time entry. **3,454 full regression tests**, **174 focused Python checks**, **35 JavaScript checks** and **64 tracker checks** passed. Changed inventory modules have **97.19% combined coverage**; each exceeds 95%. Desktop/390px browser checks passed. Full regression completed in 301.73 seconds with two existing warnings. Migration 0023 is isolated-preview-only; existing demo records were retained and shared Enrollment/payment runtime is unchanged. No push or deployment. [Impact and manual steps](scheduling-plushumi-demo.md#catalog-and-scheduling-usability).

- October 6: **Customer mock alignment is Ready for your test.** The Plushumi customer flow follows Jasmine's shared mock: centered brand header, compact real-session calendar, three-step progress, visual participant choices, group summary, review and confirmation. Workshop photos no longer use wide crops; collage and uploaded-photo framing preserve complete available images in stable previews. **3,436 full tests**, **70 focused experience checks**, **22 JavaScript checks** passed; customer JavaScript is **98.33% line / 92.19% branch covered**. Desktop/390px phone, group editing, saved selections and simulated payment failure/success verified. Admin retains Enrollment styling. No backend rule/schema change, demo reset, provider call, push or deployment. [Impact and manual walkthrough](scheduling-plushumi-demo.md#customer-mock-alignment).

- October 6: **Catalog / Materials Inventory / Workshop Prep replacement is Ready for your test.** Catalog designs/colors no longer carry finished-product stock. Per-color recipes expand into shared, per-studio raw-material holds; saved reservations preserve their original recipe. Preparing does not consume stock; completion consumes once and cancellation releases unused holds. **3,434 full tests**, **141 final focused checks**, **16 JavaScript checks** and **64 tracker checks** passed. Changed inventory modules have **96.27-100% combined coverage**. Desktop/390px mobile and customer-to-prep/completion browser checks passed. Local Plushumi data is backed up and reset to 15 fresh reservations; other-school fingerprints are unchanged. Scheduling migration 0022 is isolated-preview-only. Shared payments/enrollment are unchanged; no push or deployment. [Current walkthrough and verification](scheduling-plushumi-demo.md#catalog-materials-inventory-and-workshop-prep).

- October 6: **New workshop bookability is Ready for your test.** Customer booking no longer depends on the removed unpaid-request checkbox. The studio/workshop picker separates named workshops and their dates, with actual session duration. Young Horizons' two Charlotte sessions are available without changing their saved setup. **3,397 regression tests passed**, 12 JavaScript checks and 64 tracker checks. Every measured module exceeds 90% combined statement/branch coverage (99.51% overall). New coverage creates/publishes through admin, completes simulated booking and verifies preparation. Unpaid intake stays disabled; camps stay on their own registration path. Desktop/mobile checks passed. No enrollment/SBMC edits, new migration, provider call or push. P3 step 40 has the walkthrough; [verification](scheduling-p3-verification.md#october-6-new-workshop-bookability).

- October 6: **Workshop start + duration is Ready for your test.** Add/Edit uses one local start datetime and a duration in minutes; the server derives the end. Fields and Save/Cancel fit one row at 1024/1280px and stack on phones. Existing booked timestamps, room conflicts, capacity, stale-edit and tenant protections remain. Preschool tour/playdate forms retain their original start/end fields. **3,387 regression tests passed**, seven JavaScript checks; forms are **100% statement/branch covered**, overall measured coverage **99.51%**, with every measured module above 90%. No new migration, provider call, push or production change. P3 step 39 has the walkthrough. A separate interactive inventory concept was researched from Plushumi's public site, not the existing inventory UI; it is a proposal, not an implemented replacement. [Verification](scheduling-p3-verification.md#october-6-workshop-duration-and-inventory-concept).

- October 6: **Workshop setup cleanup is Ready for your test.** Workshop pages now have focused headings, Enrollment-style session grids, inline Add/Edit, and inventory links scoped to the workshop's catalog and location. Removed public-schedule/preparation/catalog-edit links, workshop submenus, participant heading and request/inquiry setup switches. Materials Catalog is required for catalog-using businesses; existing non-material and historical bookings are preserved. **3,370 full tests passed**, seven JavaScript checks; every measured module exceeds 90% coverage (99.53% overall). Desktop, tablet and phone browser checks passed, including an inline validation/save and mobile anchor correction. No enrollment/SBMC edits, new migration, live provider call or push in this change. Expand P3 steps 35-38; [verification](scheduling-p3-verification.md#october-6-workshop-setup-cleanup).

- October 6: **Seven-day sample trial and Enrollment grid refinement are Ready for your test.** Location's visible caption is hidden accessibly. Side-by-side inspection found narrower content, taller rows/buttons and heavier dividers/text in scheduling. Those are aligned with Enrollment, preserving read-only reservation statuses and scroll containment. **3,340 full tests passed** (281.83 seconds), 134 focused checks, seven JavaScript checks and 64 tracker checks. Every measured Python module remains above 90%. Live browser checks covered five viewport sizes, sorting, keyboard scrolling, location persistence, Inventory and dark theme. No shared Enrollment/SBMC edits, new migration, integration call or push. Expand P3 steps 32-34; [comparison evidence](scheduling-p3-verification.md#october-6-enrollment-grid-comparison).

- October 6: **Reservation layout and demo branding are Ready for your test.** Prep's participant selections now collapse inside Reservation details. The reservations grid has explicit column sizing and wrapping. Plushumi's local demo alone gets its logo and sample/trial strips; the trial is hardcoded text, not functionality. Navigation matches Enrollment's 60px row plus border. **3,340 regression tests passed**, seven JavaScript checks and 64 final tracker checks. Python coverage is **99.53%**, with every measured module above 90%; the changed context module is 100%. Live browser checks at 390/901/1024/1280/1440px found no cell or page overflow. No migration, shared Enrollment/SBMC edits, live calls, push or deployment. Expand P3 steps 29, 32-33; [verification](scheduling-p3-verification.md#october-6-grid-and-demo-header-polish).

- October 6: **Participants and workshop completion are Ready for your test.** Both tabs show saved choices in separate columns. Payment summary replaces Money; Prep's shared reservation summary collapses; materials omit Studio but remain location-scoped. Workshop completed replaces the stock-use checkbox/button and atomically records Completed, stock use and audit history. Retries do not consume twice or reopen payment. **3,327 full regression tests passed**, then **412 final focused checks** and seven JavaScript tests. Completion/participant/preparation/checkout modules have **100% statement/branch coverage**; every measured module exceeds 90%. Desktop/mobile, a new fictional booking, stock/payment invariance and restart persistence verified. Scheduling migration 0021 is test/isolated-preview-only; no shared Enrollment/SBMC changes, live calls, push or deployment. Port 8002 refreshed. Expand P3 steps 28-31; [verification](scheduling-p3-verification.md#october-6-completion-and-participants).

- October 6: **Reservation workspace changes are Ready for your test.** Workshop Prep is inside each booking only; list rows show readiness. Details use compact times, a separate ordered Money section, explicit cancel with/without-refund choices and a paginated Audit log. Room, saved choices, notes and materials are in the booking's Prep tab. Removed the manual offline-settlement form/handler, preserving shared ledger/history and internal demo simulation. **3,304 full regression tests passed** (two existing warnings, 259.03 seconds), followed by **262 final focused checks** after the contact/accessibility cleanup. **Seven JavaScript tests passed** at 100% coverage. New cancellation, audit and workspace modules are **100% statement/branch covered**; all measured modules exceed 90% (99.54% overall). Desktop/mobile, dark theme, prep/reopen and local simulated cancellation/refund were exercised. No new migration, shared Enrollment/SBMC edit, provider call, push or deployment. Port 8002 refreshed. See P3 steps 25-27 and [verification](scheduling-p3-verification.md#october-6-reservation-workspace).

- October 6: **Visual parity correction is Ready for your test.** The previous pass did not achieve the claimed Enrollment parity: header alignment varied, location did not persist across sections, grids used different structure, and Recent Activity was missing. The correction reuses Enrollment's greeting, KPI, avatar and table conventions, stabilizes header alignment, persists location per workspace/session and adds real reservation activity. **3,272 regression tests passed**, two existing warnings, 251.80 seconds; **seven JavaScript tests passed** with 100% line/branch/function coverage. New activity, location, workspace and reservation-query code has **100% statement/branch coverage**; overall measured Python coverage is **99.54%**, with every module above 90%. Compared actual Enrollment and Bookings at desktop 1280px and checked the native 362px mobile panel, filters, keyboard scrolling, themes, location persistence and prep-count drilldowns. No shared Enrollment asset/runtime edits, schema change, provider calls, push or deployment in this correction. Port 8002 refreshed. Expand P3 steps 19-24 and read the [corrective evidence](scheduling-p3-verification.md#october-6-visual-parity-correction). Earlier "uniform" claims below are superseded by this correction.

- October 6: **Admin components and Dashboard location filtering are Ready for your test.** Tables, badges, forms, buttons, numeric columns, empty states and pagination follow enrollment styling. Dashboard location is beside date/avatar, moves to a separate header row on phones, and scopes attention counts and upcoming events. Drilldowns/review tabs/pagination preserve it; invalid and foreign locations fail closed. Setup checklist stays explicitly All locations. Fixed local-day end-date formatting and mobile inventory overflow. **3,251 regression tests passed**, two existing warnings, 249.57 seconds; final template checks and six JavaScript tests also pass. Seven changed Python modules and admin JavaScript are **100% statement/branch covered**; every measured Python module exceeds 90% (99.53% overall). Desktop/mobile/901px boundary, keyboard scrolling, filters, form errors and Midnight theme verified. No enrollment asset changes, migration, provider calls, push or deployment. Port 8002 refreshed; expand P3 steps 21-23.

- October 6: **Enrollment-style Bookings navigation is Ready for your test.** Dashboard; Reservations (Reservations, Workshop Prep); Setup (Workshop, Parties, Camps, Inventory); Settings (Location & rooms, Team Access, Integrations). Requests is removed from menus, with existing records/URLs preserved. Header, section tabs and theme preference match enrollment without editing its assets. **3,217 regression tests passed**, two existing warnings, 249.17 seconds; changed Python navigation/context modules are **100% statement/branch covered**, and all measured modules exceed 90% (99.53% overall). Five JavaScript tests pass with **100% line/branch/function coverage**. Desktop/mobile destinations, permissions, keyboard focus, theme persistence and overflow checks passed. No migration, enrollment behavior change, provider call, push or deployment. Port 8002 refreshed; expand P3 for navigation steps.

- October 6: **Booking copy cleanup verified.** Removed repeated test-workspace/no-real-money notices from workshop and reservation screens; simplified reservation/refund action labels. Kept action-specific payment, unconfirmed-inquiry, unsent-email and disconnected-provider warnings. **290 focused tests passed**; desktop/mobile reservation layout checked. Copy only: no runtime behavior, migration, enrollment changes, push or deployment.

- October 6: **P3 workshop polish is Ready for your test.** Reversible Prepared/Reopen actions are separate from stock consumption and money; staff navigation is grouped with a compact mobile menu; Reservations supports contact/workshop, studio, status and local-date filters; catalog stock items have search/pagination. **3,161 tests passed**, two existing warnings, 256.35 seconds. New preparation and filter modules are **100% Python statement/branch covered**; every measured module exceeds 90% (99.52% overall). Desktop/mobile, keyboard, stale/concurrent edits, stock/payment invariance and restart persistence were checked. Migration 0020 is isolated-preview-only. No enrollment changes, provider calls, push or deployment. Expand P3 for repeatable steps.

- October 5: **P2/P3 workshop audit fixes verified locally.** Paused intake preserves existing checkout/prep; group stock and exact-stock retries are handled; missing selections and ordinary workshop bookings appear correctly in prep; image identity survives catalog edits; prep is readable on phones. **3,149 tests passed**, two existing warnings, 243.77 seconds; experience Python coverage **99.07%**, every measured module above 90%. Desktop/mobile end-to-end rehearsal passed. Native print output remains unverified. No new migration, enrollment changes or push. Read the [audit, remaining product gaps and call sequence](scheduling-plushumi-demo.md).

- October 5: **P3 admin usability cleanup verified.** Reservation availability is separated from filters; inventory uses one Manage catalog action and expandable studio assignments; option editing is aligned; stock saves preserve the studio. Latest regression: **3,145 passed**, two existing warnings, 243.95 seconds. Inventory coverage remains **98.64%**, every measured module above 90%. Desktop/mobile and keyboard checks passed. No new migration, enrollment changes or push. Expand P3 for updated manual steps and navigation/filter follow-ups.

- October 5: **P3 inventory/settings is Ready for your test.** Shared catalogs and per-studio physical stock are connected to workshops, parties, camps and preparation. Staff/settings styling reuses the enrollment design tokens without editing enrollment assets. Final application regression: **3,138 passed**, two existing warnings, 237.18 seconds; five new inventory modules have **98.64% combined statement/branch coverage**, every measured module above 90%. Additional tracker checks passed. Browser checks covered sold-out blocking, reserved-count protection, saved party choices, material consumption, mobile layouts and restart persistence. Migration 0019 is local-preview-only; no provider integration, push or deployment. Expand P3 below and read [verification](scheduling-p3-verification.md).

- October 5: **P2 Plushumi working demo is Ready for your test.** The six shared concept screens now connect to saved local bookings, shared test-payment settlement and staff preparation. Guided/group entry share the same fields. Final regression: **3,119 passed**, two existing warnings, 207.94 seconds; new Python code **99.58% combined statement/branch coverage**, every measured module above 90%. Desktop/mobile, failed/canceled/successful checkout, saved preparation/mapping and restart persistence passed. Wix checkout, email delivery and Notion sync remain explicitly simulated, awaiting customer input; M4 is not complete. Enrollment Request Demo remains unchanged. [Open the walkthrough](scheduling-plushumi-demo.md).

- October 5: **M9d integrated school demo is Ready for your test**. Enrollment Request Demo / Riverside Learning Academy has all implemented local capabilities, an ordinary school-owner login, permission-aware navigation and a Scheduling settings tab. See [the school walkthrough](scheduling-integrated-demo.md). Final regression: **3,076 passed**, two existing warnings, 198.94 seconds. Every measured module meets 90% combined statement/branch coverage; new navigation/tag modules are 100% and the guarded seed command is 94.57%. Manual admissions and simulated deposit journeys, lesson reschedule preview, public assets, desktop/mobile settings and restart persistence passed. Port 8002 only; original enrollment database, SBMC and Kid Works unchanged. No new migration or push.

- October 4: **M9c is Ready for your test**. Complete local preschool walkthrough, private family return links, local notification recovery, atomic rescheduling, attendance corrections, weekly/biweekly schedules, editable closures and optional host/music-teacher conflict mapping. **3,055 tests passed**, two existing warnings, 202.43 seconds. Six new runtime modules have **98.92% combined statement/branch coverage**; every measured module exceeds 90% (99.75% combined). Desktop/mobile, restart and existing-school form checks passed. Expand M9c for steps/login; two untouched practice families are prepared. Read [M9c impact and verification](scheduling-m9c-verification.md). Real email/provider verification and paid rollout remain open gates. Existing schools remain off. No push/deployment.

- October 4: **M9b is Ready for your test**. Configurable tour/application release and eligibility-protected playdates are connected to the same child/application. **3,007 tests passed**, two existing warnings, 183.81 seconds. Five new admissions modules have **99.03% combined statement/branch coverage**; every measured module passes 90% (99.86% combined across scheduling/shared payments/test safeguards). Desktop/mobile parent and staff journeys, cancellation/rebooking, restart persistence and existing-school public forms were checked. Only the fictional preschool preview is enabled. Expand M9b for steps/login and read [M9b impact and verification](scheduling-m9b-verification.md). No push or deployment; M9 production release gates remain open.
- M9a is **Accepted for progression** by the M9b request. Its original verification was 2,960 tests and 100% coverage across the four tour modules; see [M9a verification](scheduling-m9a-verification.md) for that historical slice. The application gates and playdates are delivered by M9b, not silently added to existing customer flows.
- Connected **M4 is deferred pending Plushumi's workflow, field ownership and authorized provider access**. M4a remains ready for testing; no vendor integration is claimed to work. This supersedes the earlier next-sequence note below.

- October 4: **M4a configurable integration foundation is Ready for your test**. Separate booking sources and destinations, per-business settings, encrypted credentials and offline field-mapping previews. Wix/Notion are initial providers, not a required pair. **2,901 tests passed**; new integration Python code has **100% statement/branch coverage**, and every measured module passes the 90% gate (99.92% combined). Desktop/mobile source selection, invalid mapping rejection and restart persistence passed. See [M4a verification](scheduling-m4a-verification.md). No provider calls, activation or push; P1 remains available for review.
- October 4: **P1 usable-pilot foundation is Ready for your test**. Operator-granted features, a focused staff home/setup path and repository-owned local-only test/coverage gates are implemented. This is the first reviewable slice toward a usable Jasmine pilot, not the finished connected pilot. No customer connections or deployment are authorized.
- P1 final verification: **2,861 tests passed**, two existing warnings, 135.94 seconds. Scheduling/shared-payments/test-safety code has **99.91% combined statement/branch coverage**; every measured module passes the 90% gate. The four new product modules are **100%**. Desktop/mobile customer-to-staff review, feature-removal protection and restart persistence passed. See [P1 verification](scheduling-p1-verification.md); expand P1 below for manual steps and local credentials. No push.
- Current next sequence: user tests M9d's integrated school demo, then verify real delivery and agree customer policy/onboarding before any client pilot. Connected M4 resumes after Plushumi input and authorized test access; its connection health/recovery work is not waived. P1/M4a remain available for testing. Existing music demos stay available and unfinished M7c2/M8 remain deferred.
- October 3 audit corrections: A1/A2 fixed and verified locally. Pausing new bookings no longer blocks a confirmed booking's balance checkout. Signed edit snapshots prevent stale overwrites across all 13 setup form types; a reload link restores the saved settings. Read the [requirements and rollout review](scheduling-alignment-review.md).
- Final corrective verification: **2,557 tests passed**, 2 warnings, 94.33 seconds; scheduling/shared-payments combined line/branch coverage **99.95%**. The edit-protection helper and affected checkout modules are **100%**. Real two-tab conflict/reload and paused-intake balance checkout were verified in the browser. No new migrations, paid APIs, production changes or push. Preview refreshed on port 8002; original demonstration text/intake setting restored.
- Client rollout remains NOT ready: mixed online-deposit/offline-balance settlement, scoped staff access, provider authority, Wix/Notion delivery and onboarding remain unfinished. P1 now implements selective native feature grants and portable coverage gates; remote CI and real provider verification are not claimed. No local milestone acceptance authorizes deployment.
- Design and 19-screen walkthrough: available for review; not implemented functionality.
- Functional Bookings implementation: M1a, M1b, M2 and M3 accepted; no production enablement or deployment.
- Deferred milestone: **M7c2; local booking slice implemented**. M7c1 is accepted for progression. Published offers, bounded teacher/student/room availability, atomic single-lesson booking, separate guardian booking permission, saved confirmations and shared-ledger pay-at-studio test quotes are implemented. Existing grants stay read-only. Trials only receive make-up credits when the offer explicitly opted in at booking time and the studio policy permits it. See [M7c2 verification](scheduling-m7c2-verification.md).
- Previous M7c2 step: **local confirmation queue and recovery verified; retained for later review**. Local simulation captures messages transactionally, retries definitive failures, blocks uncertain outcomes and replaces stale pending confirmations. **2,793 tests passed in 133.74 seconds**; five new message modules have **100% Python statement/branch coverage** (310 statements, 60 branches). Browser checks covered restart persistence, recovery, duplicate prevention and desktop/mobile layouts. See [message verification](scheduling-m7c2-messages-verification.md).
- M7c2 remaining engineering: real provider delivery/monitoring, verified guest re-entry, and online trial checkout/settlement. This is not merely waiting for credentials. Family cancellation/rescheduling/credit redemption still go through staff. All new customer booking routes are local-preview-only, not approved for rollout. No SBMC enablement, enrollment/payment runtime changes, external calls or push.
- M7c2 local verification: **2,747 tests passed**, two existing warnings, 124.86 seconds. Four new booking modules: **100% Python statement/branch coverage** (402 statements, 80 branches). Scheduling/shared-payments runtime: **99.94% combined**, excluding migrations. Desktop and 390px mobile public/family journeys, room availability, payment/refund, cancellation policy, saved confirmations and restart persistence checked. The overall milestone is now deferred with remaining engineering tracked, not accepted as complete.
- Final M7c1 verification: **2,677 tests passed**, two existing warnings, 111.46 seconds. New access Python code **100% line/branch covered** (279 statements, 40 branches); scheduling/shared-payments runtime code **99.96%** combined (excluding migrations). Real desktop/mobile checks covered scope, non-consuming preview, opening, past attendance, filtering, revocation and restart persistence. Browser CSRF compatibility and form/email length defects were fixed before handoff. See [M7c1 verification](scheduling-m7c1-verification.md).
- Final M7b2 verification: **2,623 tests passed**, two existing warnings, 107.78 seconds. New credit Python code: **100% line/branch covered** (336 statements, 72 branches); scheduling/shared-payments combined coverage **99.95%**. Includes added race/rollback checks, cancellation-form cleanup and the new tracker route. See [M7b2 verification](scheduling-m7b2-verification.md). Desktop cancellation/credit/redemption/rescheduling/restoration, conflict and expiry rejection, mobile layout and restart persistence verified. No provider or customer-system calls were made.
- Preview: [Open local Bookings](http://127.0.0.1:8002/bookings/) (isolated database, no external API calls).
- Initial working demonstration target: Tuesday, October 6, 2026, not a production commitment.
- Scope and architecture: [scheduling-module-design.md](scheduling-module-design.md).

## Working Agreement

Status sequence: **Planned -> In progress -> Ready for your test -> Accepted**.
Use **Blocked** only with a stated dependency and a concrete next action.

Keep one user-facing milestone active. Update this file when starting, finishing engineering
verification, finding a blocker, or receiving user acceptance. User acceptance is not a
substitute for automated tests, browser checks, or self-review. Do not silently progress
through several milestones while waiting for the user's review.

Each handoff includes the local URL, access instructions, a short test checklist, test results,
known omissions, and any shared-code impact. Mark Accepted only after the user confirms the
feature works for them. Track corrective work against the same milestone. A prototype's
acceptance does not confer production readiness.

Fix defects found during a task before handoff, with regression tests and impact review.
Do not leave a known defect for the user to rediscover. If a fix requires a product decision,
new external access or risks unrelated live behavior, report the concrete limitation instead
of guessing. Distinguish defects in delivered behavior from planned, unimplemented features.

Repository architecture: one repo, a separate Bookings module, shared services with explicit
interfaces, and an optional enrollment bridge. No second tenant/payment implementation, no
mandatory enrollment record, and no broad shared-code rewrite before the first useful feature.
Read this tracker and PROJECT.md before resuming implementation; chat memory is not the record.

## Audit Fixes: Manual Checks

A1/A2: balance checkout during intake pause and stale-edit protection.

Use [Workshop Demo](http://127.0.0.1:8002/bookings/workshop-demo/) with
`bookings-owner` / `admin` in the isolated local preview only.

1. Open a workshop's Edit page in two tabs. Save a description change in the first tab.
2. Change the name in the older tab and save. Expect a warning, no overwrite, and a
   **Reload saved settings** link. Follow it: the newer description and original name remain.
3. Edit and save after reloading. Expect success. Restore any demonstration text you changed.
4. In Reservations, find a confirmed test booking with an online deposit and unpaid balance
   (Jordan Camp Demo is an example). Open its customer payment page.
5. On the Reservations list, choose **Stop new reservations**. Refresh the customer page:
   its balance payment button still works and opens the loopback-only payment simulator.
6. Choose **Simulate checkout expiry** to close the test attempt without changing the paid
   amount. The booking remains confirmed and its balance unchanged. Re-enable test reservations.

Automated checks additionally cover real simultaneous database saves, all 13 setup types,
teacher service/camp session changes, missing/forged/expired edit tokens, checkout safety
gates, repeated payment requests and duplicate verified events. No external Stripe call is used.

## Milestones

| ID | Deliverable | Acceptance check | Dependencies | Status |
| --- | --- | --- | --- | --- |
| B1 | Activate and verify shared Stripe alerts and subscription recovery | Verified payment/failure emails, recoverable seven-day grace, card updates and school-owned payment isolation | Authorized Stripe configuration; verified email delivery/retries; sandbox rehearsal before live activation | Planned |
| P3 | Shared catalog, raw-material inventory and enrollment-aligned staff settings | Workshops, parties and camps share designs, color recipes and per-studio material holds; preparation precedes atomic completion; customer photos, stock alerts and consistent date/time controls | Additive local migrations 0019-0023; no Wix/Notion stock authority or production activation | Ready for your test |
| P2 | Plushumi branded working demo matching the six shared concept screens | Studio/session selection, visual per-person choices, group layout, review, simulated checkout, saved confirmation/calendar and staff preparation/proposed Notion projection | Existing native booking/payment services; isolated fictional data; connected M4 deferred | Ready for your test |
| M9d | Integrated school demo, navigation and setup access | Ordinary school admin can configure and exercise every implemented capability; disabled schools and standalone bookings remain unchanged | Existing local capabilities; isolated fictional data; no new migration or provider access | Ready for your test |
| M9c | Usable preschool demo and operational recovery | Family return access, change booking, correct attendance, preview repeat times/closures, inspect notification queue and complete the admissions journey | M9b; isolated additive schema and local-only delivery; real provider rollout excluded | Ready for your test |
| P1 | Usable-pilot foundation: selective features, daily workspace and portable test safeguards | Workshop and music workspaces show only granted features; direct requests cannot bypass grants; saved records stay accessible; clear setup and upcoming work; safe tests run without laptop-only helpers | Existing local baseline; isolated additive migration; no provider access | Ready for your test |
| M4a | Configurable connection settings and offline adapter foundation | Independent source/destination choice; per-business credentials protected; field mappings and participant pairings preserved; no accidental provider activation | P1 access controls; isolated migration only; no credentials needed for local tests | Ready for your test |
| M1a | Isolated module, default-off access, booking-specific roles and standalone workspace | Booking-only staff can sign in without enrollment access/trial banners; existing SBMC login, permissions and navigation stay unchanged | Baseline tests; reviewed local/test additive schema where needed | Accepted |
| M1b | Minimal location, room, workshop, session/capacity setup and draft/publish controls | Save, reopen and edit setup; invalid values rejected; drafts remain private; new configuration does not change existing enrollment data | M1a accepted; tenant-scoped database integration tests | Accepted |
| M2 | Public workshop request, participants, per-attendee design/color selections, saved preparation roster | Complete a request, reload/restart, see the same attendees and accurate preparation totals; duplicate submit does not duplicate work | M1b; supplied or explicitly illustrative option assets | Accepted |
| M3 | Early party request and staff review; shared versus exclusive space, group size, proposed deposit/balance | Request and prep persist; pending request is not falsely confirmed/paid; exclusive reservations cannot overlap when reservation support is enabled | M2; exact paid/confirmed transitions depend on M5 or provider authority | Accepted |
| M4 | Wix/Notion feasibility and connected pilot workflow | Two attendees with different selections in one journey; correct provider checkout, same-record Notion delivery, cancellation and retry reconciliation | M4a foundation; authorized test access and field ownership; use shared delivery components, no routine second customer form | Planned |
| M5a | Shared orders/ledger, native capacity holds and offline settlement | Immutable quotes, deposits/balances, no overbooking, explicit offline receipts, safe cancellation and concurrent writes; no online charge claims | M3; isolated local migrations and full regression | Accepted |
| M5b | Shared Stripe checkout and verified settlement | Bound order/amount/account/mode; persistent attempts, provider idempotency, signed events and account isolation; local simulation first | M5a accepted; merchant ownership agreed; external sandbox verification deferred by user, not claimed complete | Accepted |
| M5c | Payment recovery, refunds and paid-pilot gate | Provider expiry/payment races, late money, retries, refunds and cancellations reconciled without losing money or overselling | Local build verified; paid-pilot gate still blocked on real sandbox evidence, operational checks and production approval | Ready for your test |
| M6 | Early fixed-session camp/cohort registration | Reserve every required occurrence or none; correct participant roster, cancellations and payment allocation | Validated multi-occurrence capacity/resources; M5 for native paid use or verified provider capability | Accepted |
| M7a | Teacher/service setup, availability and finite standing lessons | Preview and reserve every available weekly date atomically; respect teacher, student, room and closure conflicts; staff household schedule | M6 accepted; isolated preview verified; no production migration or money collection | Accepted |
| M7b1 | Staff rescheduling, substitutes and attendance | Preview one or later scheduled dates; atomic conflict-checked replacement; linked history; attendance corrections without financial effects | Accepted for progression by the M7b2 request | Accepted |
| M7b2 | Make-up policy and lesson credits | Explicit eligibility, expiry, redemption/reversal and cancellation outcomes; no confusion with money refunds | Accepted for progression by the request to continue | Accepted |
| M7c1 | Private family schedules and access management | Owner explicitly verifies recipient/student scope; expiring single-use access links; revocation; read-only schedules and credits with no sibling/tenant leakage | Accepted for progression by the M7c2 request | Accepted |
| M7c2 | Public trials and optional family lesson booking (remaining work deferred for workshop pilot) | Accurate availability, atomic booking, explicit self-service policy and notification delivery; reuse shared payment module where needed | M7c1 accepted; confirmed business policies and delivery before rollout | Planned |
| M8 | Recurring tuition/packages through the same payment foundation | Successful/failed renewals, credits and cancellation affect the right household only; schedule and billing recurrence remain distinct | M5; agreed SBMC billing policy and sandbox validation | Planned |
| M9a | Native tours and optional enrollment leads | Staff publishes slots; families reserve real capacity; attendance; protected optional lead connection | Accepted for progression by the M9b request | Accepted |
| M9b | Application release and real playdates | Configurable eligibility enforced on the server; same family and application linked | M9a accepted for progression; shared enrollment impact review | Ready for your test |
| M9 | Optional enrollment integration and production pilot readiness | Preschool flow, standalone mode, disabled-school regressions and release gates | M9a/M9b; explicit deployment approval | In progress |

These are acceptance-sized outcomes, not permission to implement every item at once. Split a
milestone further before coding if it cannot be reviewed in one short session. M4 is the first
customer-specific integration proof and can be pulled forward when authorized access arrives.
M5/M6 are early native capabilities, not prerequisites for demonstrating a non-paid request.
Do not put parties/camps behind speculative advanced features. Neither the music milestones
nor an enrollment dependency is required to sell a standalone workshop pilot.

## Open Questions

October 6 inventory concept: Jasmine should confirm whether the studio counts raw yarn/stuffing,
prepacked kits, or both; the actual quantity consumed per participant/design; when materials
become committed; and whether camps reuse one kit across dates. Public site information supports
yarn, stuffing, color choices and accessories, but not internal quantities or consumption rules.
The concept uses illustrative values and supports materials/accessories/kits. It proposes recording
real physical losses even when stock falls below reservations, showing a shortage rather than
refusing an accurate count. This is not a change to the current inventory implementation.

October 6 workshop setup: asked whether Materials Catalog should be mandatory for every
business or only businesses using inventory. Pending answer; implemented the latter so
music/non-material workshops do not acquire a mandatory physical inventory dependency.
Plushumi requires it. Existing uncatalogued workshops with bookings retain their original
selection source, because forcing a catalog replacement would invalidate saved selections.

M9c has no unanswered customer question blocking the local demonstration. Before connecting a real
preschool, confirm who can release the application, when a playdate is allowed, how siblings
should be represented, and the school's cancellation/reschedule policy. Release rules and
eligible statuses are explicit settings; family changes and cross-browser return access are now
implemented and locally tested. Actual message delivery, provider monitoring and customer policy
acceptance remain release gates. Do not infer policies from Kid Works or apply them to SBMC.
An optional explicit visit-host/music-teacher mapping now checks both calendars, buffer/travel
time and closures. It is not enabled for any existing customer. No real activation is authorized.

M4a: no customer input is needed for configuration and local payload testing. Before live connectivity,
confirm authorized Wix site/service access, Notion data source and property schema, and which system
owns bookings, capacity, payments and cancellations. The platform needs one protected encryption
key ring; customer credentials belong in settings, not a growing set of customer-specific environment variables.

October 4 pilot: no new answer is needed for P1. The connected Jasmine pilot will need authorized Wix and Notion test access, a sample reservation/preparation database schema, and agreement about which system owns booking availability, payment and cancellation. Existing instruction remains local simulation only. Do not promise a working connection on the strength of the mock screens.

October 3 audit follow-up: the user prioritized M7b2 after A1/A2 corrections. That local slice
is now ready for testing; it does not replace the documented rollout corrections or Jasmine's
deferred M4 workflow. P1 now supplies selective native capability controls and portable test gates; verify the
Wix/Notion customer journey when authorized access is available. No new customer input is
needed for these completed defect corrections. Mixed-method settlement is required before a native
Plushumi party pilot. Details and remaining usability gaps are in the
[requirements and rollout review](scheduling-alignment-review.md).

October 2, M7: asked for Emily's cancellation/make-up settings and whether families choose
recurring slots or staff assign them. Unanswered; does not block M7a's staff-assigned,
finite schedule. It gates M7b2/M7c policy defaults and any SBMC migration. M7a has no automatic
charges, refunds, make-up credits or customer notifications. Lesson closures affect lessons
only, not workshop/camp publication. Existing enrollment preferences/MakeMusic links stay intact.

The user will obtain these details later. A ready-to-send note is in M7a's expandable
manual testing section below. No guessed policy has been applied to SBMC.

M6 uses a fictional fixed-session camp for local verification. Jasmine's exact camp dates,
prices and cancellation policy are not verified; this does not block the generic implementation.
This slice covers whole-camp registration/cancellation, not drop-ins, late joins, individual-date
cancellation or prorating. One order covers every date; per-date amounts are allocated evenly
in cents for reconciliation, not separate charges. No enrollment models or production data change.

M5b questions answered October 1: each school/studio receives money in its own Stripe account,
separate from Pontora's subscription. Keep verification local with simulated Stripe responses;
use a real sandbox later. No unanswered M5b product question blocks local testing. Merchant
onboarding and a real connected test account still require a later authorized verification step.
No live charges, customer account connections or credential changes are authorized.

Party inquiries and internal USD pricing proposals
are local-only; no approved customer price or deposit policy is required to test them.
M3 captures organizer contact, preferred time/duration, group size and shared/private-room
preference. Staff assign a proposed room, see overlapping sessions/inquiries, and record
preparation notes and proposed total/deposit/balance. Reviewed does not mean reserved or paid.
Attendee-specific party rosters, customer quote delivery, live room reservations and deposit
collection are not claimed in M3. M5 must enforce exclusive-resource reservations before any
confirmed/paid party launch; M3 cannot enable that capability.

When a decision is needed, record the question, affected milestone, date asked and whether it
blocks progress here. Keep unanswered questions visible until answered; record the answer and
its consequence in the decision log. Authorized Wix/Notion sandbox access remains a future M4
dependency, not a blocker for M5a. Nothing will connect to customer systems without that approval.

Before enabling external checkout: provision and verify a merchant-owned Stripe Connect test
account and signed test webhook, then run the provider checks in the M5b report. Demo-only exception
approved October 11: use Pontora's shared sandbox with per-business account bindings. This does not
change the merchant-owned account requirement for real customer payments. No secrets
belong in this tracker. Existing application-fee fail-open/order-binding issues remain a separate
impact-reviewed enrollment fix; M5b does not migrate that live flow to the new order ledger.

## Manual Testing

### B1

TODO: finish Stripe configuration and end-to-end verification for **all schools**.
The code is deployed, but this feature is not fully operational. Current SBMC subscription
is active at USD 24.99/month; this task must not change its price or trigger a charge.

1. Keep existing platform checkout/subscription events. Add and verify signed delivery of
   `invoice.paid`, `invoice.payment_failed`, `invoice.payment_action_required`,
   `invoice.finalization_failed`, `invoice.voided`, and `invoice.marked_uncollectible`.
2. Verify sender and `PAYMENT_ALERT_RECIPIENTS` (Ketan or his approved alias), actual email
   receipt, and a scheduled `send_payment_notifications` retry job with failure monitoring.
3. Verify each school-owned application-fee endpoint/signing secret and the booking test
   endpoint independently. Parent payment failures must never suspend a school's subscription.
   Also verify current Stripe API compatibility for new subscription/application checkout:
   the booking sandbox rejected the legacy `payment_method_types` request field. That correction
   is booking-only; live checkout was deliberately not changed or exercised in this demo release.
4. In Stripe sandbox, test initial payment, recurring renewal, decline, authentication needed,
   duplicate/delayed webhooks, and delivery retries. Confirm the correct tenant and amount.
5. Test card replacement in the existing Stripe portal; changing a card alone must not clear
   unpaid debt. Verify the seven-day banner, enrollment AND scheduling lockout, billing-owner
   recovery access, successful payment restoration, and multiple unpaid invoices.
6. Review existing overdue invoices and retry/cancellation settings before live activation.
   Do not replay historical failures blindly: their original dates can cause immediate lockout.
7. Record sandbox/email evidence, then obtain live-activation approval and verify SBMC's
   subscription, price, owner access and normal enrollment pages remain unchanged.

Hosted booking sandbox follow-up (October 11): deployed and verified. The dedicated
test key/account identity and separate booking webhook are configured. Only Plushumi's
demo `PaymentAccount` is enabled after migration 0004 and green CI. Other demo businesses can use the same
superadmin settings without code or per-school environment variables. Shared `STRIPE_MODE` stays
live; booking credentials never fall back to subscription credentials.
Hosted testing caught Stripe's rejection of the legacy payment-method request field. The
booking-only correction uses a separate card-only test configuration and checks resolved
methods during creation and recovery. The corrected release passed local regression, CI and real
hosted card checks before final acceptance. SBMC's live subscription checkout and Stripe default
configurations are unchanged. [Provider/browser evidence](scheduling-plushumi-demo.md#hosted-acceptance-evidence).

Manual checks for this slice:
1. As superadmin, open **Payments > Payment accounts**. Add a demo business, choose
   **Pontora shared sandbox (demos only)**, leave the connected account ID blank, and enable checkout.
   Saved business/account/scope are read-only. Disabling blocks new payments, not pending settlement.
2. From both full-page and popup previews, complete participant selection and open real test Checkout.
   A decline must leave the reservation unpaid; a successful test payment must confirm it once.
3. Verify the signed webhook confirms even without the customer returning. Check duplicate events
   do not create duplicate receipts, and wrong/live/account-mismatched events are rejected.
4. User declined a paid Render job. For missed notifications run
   `python manage.py reconcile_booking_payments --school plushumi-demo --limit 20` manually.
   This is an accepted demo limitation, not an unattended production recovery guarantee.
5. Verify desktop/mobile, SBMC application/inquiry/status pages, its unchanged active subscription,
   unchanged existing Render variables, and the separate migration/release evidence.

[Detailed setup and scope](payment-alerts-and-recovery.md). No live charge is needed.

### P3

Status: Ready for your test: Catalog / Materials Inventory usability and date/time controls. Local preview
only; production and connected M4 remain separate gates. Use the new walkthrough below.

Latest targeted verification: 174 Python checks and 35 JavaScript checks. Changed inventory
modules have 97.19% combined statement/branch coverage; each exceeds 95%. Final regression
totals are recorded in Current Status. [Impact and detailed steps](scheduling-plushumi-demo.md#catalog-and-scheduling-usability).

Local login: `plushie_admin` / `admin`.

**Current manual test: Catalog, Materials Inventory and Workshop Prep**

1. Open [Catalog](http://localhost:8002/bookings/plushumi-demo/catalog/), open Plushie designs,
   and expand Bear. Its colors have recipes, not quantities of finished bears.
2. Add a design, select its available colors, then edit each color's recipe. Add materials
   with amounts in meters, grams or pieces. A missing/invalid recipe cannot be booked.
3. Open [Materials Inventory](http://localhost:8002/bookings/plushumi-demo/settings/), choose
   Pasadena and inspect Blue fleece, Polyester stuffing and Safety eyes. Use Stock & activity
   to receive 2.5 m of fabric and record a reason. Only Pasadena stock increases.
4. Start [customer booking](http://localhost:8002/bookings/plushumi-demo/experience/). Choose
   two participants with different designs/colors, review and complete simulated checkout.
5. Open that reservation's Workshop Prep. Shared stuffing/eyes combine into one row each;
   separate fabric colors remain separate rows. Participant choices are in Reservation details.
6. Mark prepared: on-hand stock does not change. Mark Workshop completed: on-hand stock
   falls by the saved recipe amounts, status becomes Completed and the audit shows material use.
7. Cancel another uncompleted booking: reserved materials return to availability, without a
   physical-stock deduction. Changing a catalog recipe does not rewrite an existing booking.
8. Change studios, then inspect workshops, materials and reservations. Other studios and
   other schools remain isolated. Narrow the browser to phone size and check contained grid scrolling.
9. Open a workshop's **View Catalog**, then Add/Edit design. Select color swatches and assign
   color-specific photo URLs. Customer preview and group summary must use the matching photo.
   Open **Catalog settings** from the catalog list to manage shared colors and other choices.
   Check that muted edit icons sit beside values. Add/Edit color must show a picker and editable
   required hex, not an image URL. Verify both directions of synchronization and blank rejection.
   Cancel to discard edits; Age choices and design-specific photo URLs remain unchanged.
   Add/Edit uses compact aligned rows, not outlined cards; each photo field retains a color-specific
   accessible label. Invalid URLs keep the selected colors and show the error beside that field.
10. Edit a recipe: trash removes a row immediately; Add material row adds an empty row with focus.
    Cancel discards edits. In Stock & activity, inspect blue On hand / amber Reserved and set a
    low-stock alert using Receive delivery = 0. Zero threshold disables the alert; quantity stays intact.
11. Edit a session: the calendar has month arrows, AM/PM, Today, Clear and Close. Test keyboard
    month navigation and Escape; Cancel preserves the saved time. Reservation date filters use
    the same calendar without time controls. Repeat at phone width; the footer must stay visible.

Fresh fixture counts: 9 designs / 9 colors / 81 recipes / 11 materials / 5 studios / 15 reservations.
Amounts are demonstration assumptions. The old Plushumi practice data was intentionally replaced.
See [implementation and reset details](scheduling-plushumi-demo.md#catalog-materials-inventory-and-workshop-prep).

**Historical checks below:** finished-item stock quantities, old menu names and named QA
records are superseded by the new raw-material model and clean fixture dataset above.

- Open [Setup > Inventory](http://localhost:8002/bookings/plushumi-demo/settings/).
- Local login: `plushie_admin` / `admin`.
- Catalogs are optional, reusable booking settings, not a Plushumi-specific inventory engine.
- Studio stock is fictional: initially 40 of every configured design/color combination per studio.
  These are not verified Plushumi stock counts or supported design/color combinations.

1. In Inventory, filter Studio to Pasadena and search for `Cat / Blue`. Check On hand,
   Reserved and Available. Counts in other studios are separate.
2. Select **Manage catalog** for Workshop materials. Expand a design/color section and
   use its labeled pencil control to edit a label, image URL or swatch; save and
   reopen it. Restore the value after testing. Shared changes apply to linked workshops
   and the camp, while previous bookings retain their original selection snapshots.
3. Add a new option and its stock-item combination before trying to book it. A new item
   starts at zero stock at each studio; adjust the desired studio's count with a reason.
4. In a workshop's Edit screen, see the shared catalog assignment. Existing catalog-linked
   requests prevent switching catalogs, so promised materials cannot disappear silently.
5. Adjust an unused item to zero, then open Customer demo at that studio. The sold-out
   color must be disabled. Restore the fictional count after testing. Counts cannot be
   reduced below already-reserved materials, and stale count forms must not overwrite a change.
6. Make a fictional workshop booking. During its valid checkout hold, stock is reserved;
   successful local payment keeps it reserved. Expiry/cancellation releases unused stock.
   Unresolved payment attempts remain protected until reconciled, not prematurely released.
7. In **Reservations**, open the booking's **Prep** indicator, or open its detail and choose
   **Workshop Prep**. Only that reservation's participants, room and materials appear.
   Select **Mark prepared**: its list indicator becomes Complete and
   it leaves the Preparation Pending queue, but on-hand/reserved counts and payments stay unchanged.
   **Reopen preparation** restores the queue. Refresh/restart preserves readiness; a stale
   form must ask for a reload. The Reservation tab's Audit log records both actions.
8. Select **Workshop completed** in the reservation header and confirm on the next screen.
   Status becomes **Completed** and on-hand stock decreases once. Repeated submissions do
   not deduct again. Used items are not restocked. Completed bookings cannot be canceled or
   reopened through preparation; payment history and any outstanding balance stay unchanged.
9. From Dashboard's **View customer schedule**, open **Plan a party** for an enabled offering. Select items and quantities totaling the
   participant count. In Parties, verify the same choices appear in staff review. An inquiry
   alone reserves nothing; staff must select complete materials before creating a reservation.
10. Register two people in the demo camp using the shared catalog. It reserves two kits
    for the whole camp, not two kits for every camp date. Reservation cancellation releases
    unused kits through the same stock rules as workshops and parties.
11. Check Settings' **Location & rooms**, **Team Access** and **Integrations** tabs.
    Only owners can edit inventory/settings. Staff with session-edit access can complete reservations.
12. Check desktop and narrow/mobile views. Staff setup and preparation use Pontora's
    enrollment colors and controls; the customer booking experience keeps Plushumi branding.
13. In Inventory, expand **Assigned workshops & camps**. Confirm each workshop/camp
    is paired with its studio, and that Manage catalog remains the primary catalog action.
14. Filter to one studio, adjust an item's count with a reason and save. The studio
    stays selected. Search for an unknown item and check the empty-search message;
    **Clear search** keeps your studio filter.
15. In Reservations, switch **Accept new reservations** off and select **Save**.
    The state becomes Paused and existing reservations remain visible. Switch it back
    on and Save, then verify Open. Payment follow-up is a separate active-state tab.
16. For a dedicated fictional stock combination, leave only one unit available and select it for
    the first of two workshop participants. The second participant cannot choose that unit;
    Apply to all rejects insufficient stock without overwriting either person's choices.
    Restore the physical count with a reason after the check.
17. Open Workshop prep at phone width. The page stays within the viewport and the participant
    table scrolls horizontally, with readable columns. The selected studio/date/time is explicit.
    Print formatting is implemented; native print/PDF output still needs a visual check outside
    the in-app browser test tool.

18. Use the enrollment-style **Dashboard**, **Reservations**, **Setup** and **Settings** header.
    The main Reservations page has no Workshop Prep subtab. Each booking has its own
    **Reservation** and **Workshop Prep** tabs. Setup has **Workshop**,
    **Parties**, **Camps** and **Inventory**. Settings has **Location & rooms**, **Team Access**
    and **Integrations**. Requests is no longer a menu item; existing detail links still work.
    Open an edit/detail page and verify the correct parent and subtab remain selected.
    At phone width, open the navigation button with touch or keyboard; Escape closes it and
    returns focus to the button. Subtabs scroll within their row if needed. The avatar menu
    contains Workspaces, Customer demo, Sign out and the same theme preference as enrollment.
    Test Clean/Midnight/Mint and refresh; restore Clean. Integrated-school links and
    capability/permission restrictions remain intact. Music and tour tools remain available
    under Reservations only for enabled workspaces.
19. In Reservations, search by contact name/email or workshop, then select location, status
    and date range. Dates use each studio's local day, including daylight-saving boundaries.
    Payment follow-up and pagination retain the filters. **Clear filters** removes search,
    status and date filters but keeps the header's selected location;
    an end date before the start date shows a field error instead of misleading results.
20. Under **Manage catalog**, search the Stock items section. Its 25-item pages preserve
    the query; an unknown item has a clear empty result, and **Clear search** restores the list.
    At phone width, only the table scrolls horizontally, not the entire page.
21. On **Dashboard**, use **Location** beside the date/avatar. Select Pasadena, then
    New York and **All locations**. Upcoming events and all attention counts must match
    the selected location. Visit Reservations, Workshop Prep, Setup and Settings; the
    selection remains visible and location-specific lists retain it after refresh.
    This preference is per workspace and browser session, not a new access permission.
    **All locations** resets it. Changing location on a detail/edit page returns to its
    list, including when viewing one booking's Workshop Prep. Shared catalogs, Team Access,
    Integrations and the Setup checklist remain workspace-wide.
22. Check Reservations, Workshop Prep, Workshop, Parties, Camps, Inventory, rooms,
    Team Access and Integrations. Compare Enrollment Submissions with Reservations:
    padded white table frame, header/row spacing, customer initials, bold names, secondary
    values, Open actions and search/count layout. Customer and Scheduled headers sort
    without losing filters. Reservation status is not an unrestricted inline edit, because
    its transitions affect capacity and payments. Forms use enrollment labels, borders,
    colors and focus treatment. An empty Add workshop submission shows required errors.
23. At phone width, the location selector moves below the header on every staff page. Change it
    and open/close the menu; neither covers the page content when closed. Focus a wide
    table and use arrow keys: only the table scrolls, not the whole page. Check Clean
    and Midnight, then restore Clean. A session ending on the same local day does not
    repeat its end date even if it crosses midnight in UTC.
24. Dashboard uses Enrollment's greeting/KPI layout. **Recent Activity** shows actual
    reservation creation, payment/cancellation and preparation audit events, newest first,
    for the selected location; each opens its reservation. **Preparation Pending** opens upcoming,
    confirmed, unprepared bookings and excludes consumed materials (including Inventory QA).
    **All reservations** exits that queue. Search result counts change without changing
    the selected studio's Total/Needs payment review cards. Empty locations show zero/empty
    states, not another location's activity.
25. On the main Reservations grid, check Preparation Pending/Complete indicators. Held/review
    bookings show On hold; canceled bookings show Not required. Open a booking. Scheduled
    uses one compact local date/time range; Room appears only in Workshop Prep. Payment summary is
    separate and ordered Total, Deposit, Received, Balance, Refunded. Received is highlighted
    green; an outstanding Balance is amber. After cancellation, Balance is Not due.
26. On a new fictional local-demo booking, select **Cancel reservation**. Neither payment
    choice is preselected. **Keep reservation** leaves it unchanged. Choose **Cancel and
    refund remaining payment**, enter a reason and confirm. The demo receipt is refunded
    locally, capacity/unused stock are released, and no external transaction runs. Refresh
    and check the Audit log. A second submission must not create a second refund.
    On another fictional booking, **Cancel without refund** preserves Received/Refunded
    and clearly shows retained money. Real provider refunds still require verified payment;
    arbitrary offline receipts are not eligible for automatic refunds.
27. The Audit log replaces Payment History and shows creation, money, prep completion/reopening,
    materials used, cancellations, refund requests and recorded outcomes with actor/time.
    Check its pagination and horizontal keyboard scroll on a phone. There is no Record
    offline settlement form or handler. Internal demo settlement/shared historical ledger
    support remain because the local simulator and other scheduling modules need them.
28. Both Reservation and Workshop Prep show **Participants & selections** with one column
    per saved choice (Design, Color, Age in Plushumi). Historical labels/values survive catalog
    renames. A choice not supplied by a participant shows Not selected; parties that did not
    collect individual names say so rather than inventing participants.
29. In Workshop Prep, **Reservation details** starts collapsed, including **Participants & selections**.
    Expand it to see the same summary and participant choices as the Reservation tab; collapse it
    again to hide both. Room remains in Prep. Materials has Material, Quantity and Stock status,
    with no redundant studio column; stock is still scoped to this reservation's location.
30. Use a new fictional confirmed booking to rehearse completion. Mark prepared first if desired;
    stock remains Reserved. **Workshop completed** then shows a confirmation screen. Go back
    leaves it unchanged; confirming deducts unused allocations and records actor/time in Audit log.
    Reload: status remains Completed, materials Used, payments unchanged, and no completion/cancel
    controls remain. Staff/viewer permissions and other schools' records stay isolated.
31. The saved **Workshop Completion QA** record demonstrates the completed journey. Its one
    Blue Cat kit changed Pasadena stock from 38 to 37, with one stock movement and one completion
    audit. The local $65 receipt was unchanged. On mobile, tables scroll within their container;
    the page itself must not scroll sideways. The customer receipt remains completed, not a new checkout.
32. The local Plushumi admin header uses the workshop logo, the blue **Built specifically for
    The Plushie Workshop · Sample data loaded** strip, and a yellow **Trial ends in 7 days** strip.
    The trial text is fixed demonstration copy: no countdown, billing link, trial state or booking
    restriction. Other workspaces and Enrollment must not acquire these banners or this logo.
    The navigation row matches Enrollment's 60px height plus its border; on phones, Location
    occupies an additional row beneath navigation.
33. In Reservations, check long customer/workshop names and the Participants header. Text must
    stay within its own column. At desktop widths the whole grid fits; on smaller screens focus
    the grid and use the arrow keys or swipe horizontally. Only the grid should scroll, not the
    whole page. Open a reservation or its Prep link and verify it still opens the correct record.
34. Compare Reservations with the demo school's Submissions page at the same browser width.
    The grid now uses the same outer card/inner scroll structure and content width, compact
    line height, lighter row dividers, muted workshop/date text and Open button dimensions.
    Reservation statuses are read-only labels, not Enrollment's inline status editor.
    The location dropdown remains in the header without a visible Location label; its accessible
    label and filtering still work. Select Pasadena, switch to Setup, then restore All locations.
35. Open **Setup > Workshop**, then a Plushumi workshop. The list has no Public schedule button.
    Inside the workshop, the Parties/Camps/Inventory submenus, promotional description,
    Participant choices heading, Preparation links and Workshop materials editor button are absent.
    Check the compact Date, Time, Room, Capacity and Status grid; its pencil opens an editor
    directly below that row. Back returns to the workshop list.
36. Select **Edit details**. Materials Catalog is required and contains only this business's
    catalogs. There are no Accept unpaid workshop requests or Accept party inquiries checkboxes.
    Saving without a catalog is rejected for Plushumi. Cancel preserves the workshop.
    Existing non-inventory/historical offerings remain editable without rewriting old selections.
37. Select a session's pencil, enter an invalid capacity (for example, 13 in a room of 12),
    and save. Expect field errors in the same editor, with the old session unchanged. Correct
    the value and save: return to that row with Session saved. Add session uses the same page;
    new sessions are Draft. Concurrent/stale edits, room conflicts and booked-session changes
    remain protected. On a phone the editor fits the viewport and opens below the sticky header;
    the wider grid scrolls inside its container only.
38. Select **View inventory**. Expect only this workshop's Materials Catalog at its location,
    with no catalog-editing page or catalog-management section. Search/clear and pagination keep
    both filters. Back returns to the workshop. Choosing a different header location deliberately
    exits the workshop-specific filter and opens inventory for that location.
39. Open a session's pencil. Expect **Start time**, **Duration (min)**, Room and Capacity,
    with Save/Cancel on the same line on a laptop. A two-hour session shows 120; there is no
    second datetime field. For an unbooked draft, change duration to 90 and save: its displayed
    end is 90 elapsed minutes after the start. Reopen and verify 90 remains. Zero/blank/negative
    durations are rejected without changing the record. Existing booked session times remain
    protected; capacity-only changes do not alter their timestamps. On a phone, the editor stacks
    without page overflow. Preschool tour/playdate Add time forms still use start and end.
40. Open **Customer demo**, select **Charlotte, NC**, then **Young Horizons** in Workshop.
    Expect October 12 and October 30, with each date's saved time and capacity. Select October 30
    and three participants: the two-seat session is disabled and the booking link is cleared.
    Return to one participant, select the time, and continue: the participant page identifies
    Young Horizons and the correct Charlotte session. Changing Workshop or Studio must clear
    the previous session and show only that selection's dates. For a fresh rehearsal, create a
    workshop with a catalog, add future sessions and publish both the sessions and workshop;
    it must appear without any unpaid-request setting. Draft, past and camp-only dates stay out.

The local `Inventory QA` workshop booking is retained as a consumed-material example.
`Reservation QA October 6` is a canceled, fully simulated-refunded browser-test record;
its history demonstrates preparation completion/reopening and cancellation without consuming stock.
`Inventory Party QA` is withdrawn and holds no stock or capacity. Existing walkthrough
bookings and Enrollment Request Demo data are preserved. Real stock transfers, purchasing,
automatic supplier replenishment and provider stock synchronization are not in this slice.
See [P3 impact and verification](scheduling-p3-verification.md) for evidence and limitations.

### P2

Status: Ready for your test. Local demo only. Connected M4 remains deferred.

- Customer: [Plushumi working demo](http://localhost:8002/bookings/plushumi-demo/experience/).
- Staff: [Studio preparation](http://localhost:8002/bookings/plushumi-demo/experience/preparation/).
- Local username: `plushie_admin`. Password: `admin`. No enrollment or operator access.
- Use `localhost` for this demo while keeping Enrollment Request Demo on `127.0.0.1`;
  the separate host cookies avoid replacing the school-admin login you are currently testing.

1. Choose Pasadena, two participants, an available date and start time. Continue.
2. Name each participant, select age, design and color. Check the visual selection preview.
3. Switch between One at a time and Whole group. Selections must stay attached to the correct person.
4. Add/remove a participant and try Apply to all for a shared color. Check the total.
5. Fill in a fictional booking contact, review the example policy notice, and review the booking.
6. Edit a choice from review, then return. Continue to the clearly marked Wix handoff simulation.
7. Simulate payment failure first: no confirmed booking or preparation row. Then simulate success.
8. Confirm the saved selections, calendar download and unsent email preview. Refresh: no duplicate payment.
9. Sign in as the studio admin, select that session in Workshop prep, and check participant rows and material totals.
10. Open Preview Notion mapping: actual saved choices should appear with the stable booking reference;
    the page must say Notion is not connected. View the configurable integration settings separately.
11. In a second booking, abandon checkout. Its places should be released and it should not appear in preparation.
12. Admin workspace includes existing locations/rooms, workshops/choices, parties, camps and reservations.
    These are native local features, not a claim of connected Wix inventory. Do not enter live credentials.
13. Start a checkout, then pause new reservations in another admin tab. Existing checkout can still
    complete; receipt/calendar and staff prep remain accessible. New bookings show a paused message.
    Restore Open afterward. Each new participant starts with explicit blank choices.
14. In prep, confirmed ordinary workshop requests must appear alongside branded-demo bookings.
    Any historical missing design/color is flagged, not included as a made-up materials total.

Prices, availability, ages/design choices and policies are discussion assumptions. Real delivery,
Wix provider authority, Notion mapping/access and launch onboarding still require agreement.

Latest audit: 3,149 tests passed; 99.07% experience Python statement/branch coverage, with all measured
modules above 90%. Browser checks included desktop and 390px layouts, guided/group preservation,
add/remove, shared color, review/edit, decline/retry, cancellation, staff authentication,
actual-record projection and restart persistence. Jordan Walkthrough's two-person Pasadena
booking is available in preparation; Jamie Mobile's canceled booking is deliberately excluded.
No new migration, provider calls, real money, emails, deployment or push.
The audit's `Plushumi Demo Rehearsal` booking is saved in Pasadena on October 10 at 1 PM.
Pricing/policy admin settings, branded parties/camps, customer
change/communication flows and connected M4 remain unfinished product work. See the
[workshop audit](scheduling-plushumi-demo.md) before making commitments to Jasmine.

### M9d

**Ready for your test.** Final verification: 3,076 passing tests; every measured module meets
the 90% statement/branch gate. New navigation/tag code is 100%; local seeding is 94.57%.
The corrected parent-label assertion also passed in the final full run. Configuration checks
found only the existing duplicate Jazzmin template-tag warning; no model changes were detected.

Local school: [Enrollment Request Demo](http://127.0.0.1:8002/schools/enrollment-request-demo/admin/),
displayed as Riverside Learning Academy. Login **demo_admin / admin**.
Use port **8002**, not the original school database on port 8001.

1. Open **Settings > Scheduling**. Follow **Open scheduling** and return through **Enrollment admin**.
2. Open **Tours > Taylor Walkthrough** and record attendance. Follow its enrollment lead,
   complete Casey's application, release its playdate from Submissions and book from the parent page.
3. Submit a workshop request with participant choices. Review it in Requests, quote it and
   complete the local deposit simulation; inspect the reservation and balance.
4. Review Jordan Party, the three-day camp, Riley Park's recurring lessons and Park Family access.
5. Edit rooms/availability, try a conflicting booking, reschedule a lesson, cancel under the
   make-up policy and redeem the credit. Use future sessions shown on screen.
6. Inspect Messages and Integrations. They are local previews, not real email/Wix/Notion delivery.

The [full walkthrough](scheduling-integrated-demo.md) provides separate steps for every area,
prepared records, expected results and boundaries. Taylor is untouched for your first test;
Robin Practice was used for the verified admissions journey. Morgan Workshop has a $60
reservation with a $20 simulated deposit and $40 balance for inspection.

No current question blocks this local demo. Connected providers, recurring tuition, online
lesson checkout and production rollout remain unfinished; enabling a demo is not acceptance of those features.

### M9c

**Ready for your test.** Full regression: 3,055 passing tests; six new modules 98.92% combined
statement/branch coverage. Browser desktop/mobile and restart checks passed.

Start at [Visit setup](http://127.0.0.1:8002/bookings/preschool-demo/visits/setup/).
Local username: `preschool-owner`; password: `admin`. This is the isolated local preview,
not a customer account. Use fictional names and `example.invalid` email addresses.
The setup page shows contact details, the family-change deadline and current readiness.

1. Open **All visits**. Under **Meet our preschool**, choose **Repeat times**. Pick a future
   weekday, last date, time, room, Alex Morgan and total places. Preview first: conflicts block
   publication and closures are skipped. Publish only the reviewed open dates. Dates are finite,
   not an indefinitely repeating rule; use a new batch when you need later availability.
2. Open the **Family tour page** in another tab, choose a time, and book one child with a parent.
   The receipt includes room/time, rescheduling and cancellation until the configured deadline.
   Move it to another available date and confirm the old places are released.
3. Choose **Close visit access**, then request a return link using the same booking email.
   In the staff tab open **Messages**, run the local message queue, open that recipient's private
   link and use **Open my visit**. The link works once; another family/tenant cannot use its URL
   without the secret. This is a local email simulation, not a message sent to an actual inbox.
4. For a fresh application without waiting for a future tour, use **Taylor Walkthrough / Finley**
   or **Blake Walkthrough / Harper**. These fictional past tours await attendance. Open the tour,
   choose **Attended**, then **View enrollment lead** and its application link. Fill and submit the
   prefilled application as the family. **Robin Practice / Quinn** and **Avery Demo / Casey** are
   saved completed examples. Existing submitted applications reopen instead of duplicating.
   A future tour cannot be marked attended early.
5. On the admissions progress page, approve the playdate when the application is eligible.
   The family status page offers **Choose a playdate**. Booking or moving it keeps the same
   application and updates its status; no retyping the child's identity. Robin's current example
   is scheduled for October 15 at 1:30 PM. Dates may be changed during your own testing.
6. Open a historical visit and **Correct a status** with a reason. An attended tour that already
   released a submitted application cannot be undone casually. An old playdate cannot be restored
   over a replacement. Staff decisions such as Enrolled are not overwritten by booking corrections.
7. In **Visit setup**, add or edit a closure. October 15 already has demo bookings, so closing
   that date must be rejected. November 2 is a sample closure; a repeat preview spanning it skips
   that date. Moving a closure does not automatically republish previously closed slots: review
   and publish the desired times explicitly. Closures here cover visits/lessons, not workshops/camps.
8. Try a temporary failure in the local queue, then retry after its displayed retry time. It must
   not duplicate the booking. Used/revoked/expired return links and superseded notices are suppressed.
9. Refresh or restart the preview. Bookings, applications, repeat schedules, closures and access
   records remain saved. Check a phone-width view; controls should fit without page-wide overflow.

**Demo limits:** Free visits only. No real email, Stripe, Wix or Notion calls. One application per
prospective child; accompanying attendees consume places, not extra applications. Policy is per
school. Closing visit access leaves existing enrollment status/draft links unchanged. Recurring
tuition, real merchant/email onboarding and customer migration are not part of M9c.
Read [M9c impact and verification](scheduling-m9c-verification.md). No push or deployment.

### M4

**Not complete; connected implementation remains planned.** M4a supplies saved configuration
and an offline example preview only, and is separately ready for user testing.

Remaining acceptance checkpoints:

1. Connect to authorized Wix/Notion test resources, verify identity, permissions and actual
   destination fields. Saving an API key or seeing an example payload does not satisfy this.
2. Prove the single customer journey: current Wix availability, two attendees with different
   selections, the correct booking/checkout and reliable provider-confirmed status.
3. Deliver and update the same Notion records on changes/cancellations, including bookings
   originating in Wix. Handle duplicate/out-of-order events, bounded retries and ambiguous writes.
4. Provide connection testing, sync health and safe recovery, then verify the full journey,
   failures and restarts with real authorized test services as well as local automated tests.

Revised planning allowance: **24-48 focused engineering hours remaining**, low confidence until
the first authorized provider proof. This excludes waiting for access and production rollout;
it is not an elapsed-time promise. M4a's local coverage does not establish vendor compatibility.
Local adapter/delivery work can continue without customer access, but M4 cannot pass final
acceptance using only simulations. Existing local-only restrictions remain in force.

### M9

**Started before connected M4 at the user's request.** Reuse scheduling availability,
capacity and reservations; do not build a second calendar or route preschool visits through
music-specific screens. Add an opt-in enrollment connector and school-configurable rules for
tour requirements, staff versus automatic application release, and playdate eligibility.

Target acceptance journey: book tour -> staff records attendance -> application becomes
available -> family applies -> playdate booking becomes available under the school's review
policy. Link the same family/child, lead, application and bookings without duplicate entry.
Enforce eligibility on the server, not merely by hiding links. Check rooms/staff/capacity,
rescheduling/cancellation, duplicate actions and cross-school isolation. Standalone bookings
must continue without enrollment; SBMC's current enrollment and MakeMusic flow must not change.

M9a is accepted for progression; M9b adds the gated application and playdate sequence. The earlier estimate for simple enrollment links did not cover the full staged
preschool workflow. M9's production release gates are not satisfied by a successful demo.

### M9a

**Accepted for progression by the M9b request.**

Open [Preschool tours](http://127.0.0.1:8002/bookings/preschool-demo/visits/).
Local username: `preschool-owner`; password: `admin`. This account has both booking-owner
and enrollment-owner access in the isolated preview only. Use fictional contact details.

1. Review **Meet our preschool**: location, host, room, date/time and booked places are visible.
   Slots count adults plus children. A full slot says Full rather than Open.
2. Choose **Add time**. Use a future weekday, start/end, room, host and capacity; check
   **Open this time for bookings** and save. The time appears in the family booking page.
   Overlapping a published time in its room is rejected; moving it to another room while
   retaining the overlapping host is also rejected. Tour capacity cannot exceed room capacity.
3. Open [Family booking](http://127.0.0.1:8002/bookings/preschool-demo/tours/), select a time,
   and enter a fictional guardian, email, child and attending counts. Book it. Expect a saved
   confirmation with the correct time and room, no payment and no actual email.
4. Return to Tours and open that family's booking. Counts and contact details must match.
   **View enrollment lead** shows the child, tour time and Tour Scheduled status. A booking
   creates its own lead; it never overwrites an existing child's record just because emails match.
5. Open **Robin Practice**, the past-tour example left for you. Choose **Attended**, then view
   its enrollment lead: expect Tour Completed. **Avery Demo** is an already-completed example.
   Future bookings deliberately cannot be marked attended before the tour starts.
6. On a future fictional booking, expand **Cancel this booking** and confirm. Its places become
   available again; its still-pending linked lead becomes New and its tour time clears.
   **Chris Capacity** is the completed cancellation example. Already advanced leads are preserved.
7. Try booking more people than a time's remaining places. Expect rejection with no extra booking.
   Refresh/retry the same successful form: it must not create a second booking or lead.
8. Optional standalone check: under the tour's **Settings**, clear **Connect tour bookings to
   enrollment leads**, save, then create another fictional booking. It appears in Tours without
   creating a lead. Existing lead history remains; re-enable the setting afterward for this demo.
9. Refresh/reopen the pages. All saved state persists. Check the family form on a narrow window;
   staff tables scroll horizontally inside their section instead of forcing the whole page wide.

Verified: 2,960 tests passed, 2 existing warnings, 167.47 seconds. Four new tour modules have
100% statement/branch coverage (367 statements, 64 branches). Shared measured modules meet
the 90% gate individually; combined coverage is 99.92%. Database schema check is clean.
See [M9a impact and verification](scheduling-m9a-verification.md).

**M9a-only limitations (application gates/playdates are now delivered by M9b):** paid tours, outgoing email,
verified cross-browser family re-entry, recurring availability, self-service rescheduling or
correction of terminal attendance outcomes. Only one prospective child is identified per booking;
other attending children count toward capacity. Hosts are conflict-checked against other tours,
not automatically against music-teacher calendars. Standalone tours need no enrollment membership;
enabling the lead connection requires both booking-owner and enrollment-owner access.
No existing school changed, no live APIs, no deployment or push. Application links are now
gated only for schools explicitly configured with the M9b admissions policy below.

### M9b

**Ready for your test.** Final verification: 3,007 tests passed, 2 existing warnings,
183.81 seconds. Five new admissions modules: 99.03% combined statement/branch coverage.
All measured modules pass the 90% gate. Desktop/mobile and existing-school checks passed.

Start at [Preschool tours and visits](http://127.0.0.1:8002/bookings/preschool-demo/visits/).
Local username: `preschool-owner`; password: `admin`. Fictional preview only; no real messages
or charges. This account has booking-owner and enrollment-owner access.

1. Open **Admissions workflow**, or [go directly to settings](http://127.0.0.1:8002/bookings/preschool-demo/admissions/settings/).
   The saved example requires a completed tour, releases the application automatically, and
   requires staff approval before a playdate. Eligible statuses are New/In Review. Scheduled
   and completed labels come from this school's configured application statuses.
2. Open [Avery Demo's progress](http://127.0.0.1:8002/bookings/preschool-demo/admissions/leads/1/).
   This completed example shows tour attendance, a submitted application and Casey's playdate
   on Wednesday October 7 at 11:30 AM in Garden room. Follow **View application** and then
   **Parent status page**: both show the same Playdate Scheduled status and booking.
3. For a fresh sequence, return to Tours and open **Robin Practice**, the seeded past-tour
   example. Before recording attendance, its enrollment lead cannot start an application.
   Mark the tour **Attended**, then open the lead's **Admissions progress**. Choose
   **Open application**: the child/guardian details should already be filled in.
4. Submit the fictional application. The parent status page shows that the school must
   release playdate booking. In another tab, open that lead's Admissions progress and choose
   **Release playdate booking**. Refresh the parent page: **Choose a playdate** appears.
5. Choose a future Classroom playdate. The child's identity comes from the application, not
   an editable name field. Adults plus children consume places. Try too many attendees, then
   one adult and one child. The oversized request is rejected; a valid booking confirms the
   actual time/room and changes the application to **Playdate Scheduled**.
6. Return to the staff progress page and follow **Playdate booking & attendance**. Future
   attendance stays unavailable until the start time. **Cancel this booking** releases the
   places and restores the prior application status, unless staff already advanced it. The
   parent can choose another time. Completed/no-show transitions also have automated tests.
7. Reopen the saved lead while the family has a draft: their edited answers remain intact.
   Retry a successful submit: it must show the same application/booking, not duplicate it.
8. Open [the generic application URL](http://127.0.0.1:8002/schools/preschool-demo/apply/)
   in a separate private session without a released draft: it is blocked and asks for the
   school's application link. An existing valid draft in the same session is deliberately
   resumable. Release checks also cover payment/resume URLs, not just visible buttons.
9. In settings, try making scheduled and completed statuses identical: expect rejection.
   Saving from an older tab after another save is rejected. Cancel returns to Tours without
   changing policy. Saved workflow history prevents disabling/removing its supporting feature.
10. Refresh after reopening the browser and check a narrow/mobile window. School records,
    approvals, booked places and status remain saved. The tour receipt itself is browser-bound;
    cross-browser delivery/recovery is not implemented, so retain the private application link.

Only the fictional preschool preview is configured. SBMC and Kid Works are not enabled.
Shared enrollment hooks are default-off, not absent: see the
[M9b impact and verification](scheduling-m9b-verification.md) for changed boundaries and evidence.
No production migration, deployment or push. Free visits only; notifications, paid enrollment
certification, recurrence/rescheduling and client onboarding remain M9 release work.

### M4a

**Ready for your test.**

Open [Workshop Demo integrations](http://127.0.0.1:8002/bookings/workshop-demo/integrations/)
with `bookings-owner` / `admin`. Local demonstration records only; do not enter real tokens yet.

1. Two saved examples appear: **Workshop Demo - Wix** and **Workshop preparation - Notion**.
   Both say **Not connected**. Only this fictional workshop has the new capability enabled.
2. Open the Wix settings. The site ID persists; its placeholder credential is saved but hidden.
   Leaving the password field blank and saving keeps the existing credential. No request is sent.
3. Open Notion settings. Under **Bookings come from**, select **Pontora Bookings**, save, then
   **Preview saved mapping**. The preview shows Pontora as source without requiring Wix.
4. Back in settings, choose **Workshop Demo - Wix**, save and preview again. This links saved
   configuration only; it does not change actual booking or payment authority.
5. The preview shows Avery's Cat/Blue and Casey's Bunny/Pink under **Preparation**, with separate
   title, booking reference, contact, schedule and status fields. **Request preview** is optional
   technical detail. No customer data is read, and no Notion page is created.
6. Change a destination field name and save; preview uses that property name. Try mapping both
   title and reference to **Name**: saving is rejected. **Reload saved settings** restores the
   previous valid mapping. Restore any demonstration field names you changed.
7. Open the same settings in two tabs. Save one, then attempt a change from the older tab.
   The older save must be rejected; reloading shows the current saved version.
8. Narrow the window: fields stack and labels/buttons remain usable without horizontal page
   scrolling. Reloading or restarting the isolated preview retains the saved connections.

Owners/operators may edit connections. Viewer/scheduler access and businesses without the
capability are denied even through direct URLs (automated tests cover these boundaries).
New providers require an adapter and contract tests, not just another URL. Provider authorization,
remote schema discovery, webhook processing and actual delivery are not yet implemented.
The [M4a verification report](scheduling-m4a-verification.md) records research, security, impact,
automated results, agent-performed checks and the remaining connected-pilot work.

### P1

**Ready for your test.** Open [Workshop Demo](http://127.0.0.1:8002/bookings/workshop-demo/)
with `bookings-owner` / `admin`. These credentials and records are isolated local examples only.

1. Overview shows **Needs attention**, **Next 7 days** and a collapsible **Setup checklist**.
   Workshops, Parties, Camps and Reservations are available; Lessons are absent. The agenda
   distinguishes published sessions from confirmed parties and shows their location/room.
2. Click **Workshop requests awaiting review**. Find **Pilot Review Demo**, created during
   browser testing. Open it: Avery Demo chose Cat / Blue; Casey Demo chose Bunny / Pink.
   It remains pending, with no reserved seats, charge or sent email. **All requests** restores
   the full history rather than only the pending subset.
3. Return to Overview. **Reservations needing payment review** opens the matching filtered
   list, including Casey Recovery Demo. **New party inquiries** opens the pending-inquiry
   filter; zero pending inquiries does not mean historical inquiries have disappeared.
4. Expand **Setup checklist**. Its links lead to locations, workshops, published sessions and
   reservation settings. Configured means that setup step exists, not that payments or a
   customer integration are approved for launch.
5. Follow **View customer schedule**, open Design Your Plushie, and request places with
   fictional details and two different participant choices. Check the saved receipt, return
   to Overview, and find the new request through the updated count. This is unpaid intake.
6. Open [Music Demo](http://127.0.0.1:8002/bookings/music-demo/). Its lesson schedule and
   lesson controls remain available; workshop/party/camp/payment-reservation menus are absent.
   Shrink the browser to phone width: navigation wraps and tables scroll inside the page.
7. Optional operator check: sign out, then sign in as `bookings-operator` / `admin`.
   Open Workshop Demo > **Manage available features**. Clear Camps and save: it must reject
   removing access because camp records already exist. **Reload saved settings** restores
   the checked value. Sign back in as `bookings-owner` / `admin`; this account has no feature-grant control.

Verified: **2,861 tests**, **99.91%** combined scoped coverage, every measured module above
90%, four new product modules at 100%, real browser request/review, mobile layout, protected
feature removal and restart persistence. [Full verification](scheduling-p1-verification.md).
Only the isolated preview database received the additive migration. SBMC/enrollment runtime
was not changed. No external API calls, charges, emails, deployment or push.

### M7c2

**Verified slice: local confirmation queue.** Open [Messages](http://127.0.0.1:8002/bookings/music-demo/messages/)
with `bookings-owner` / `admin`. `bookings-viewer` / `admin` may read history only.

1. Under Processing, enable automatically queuing new confirmations and save. This does not
   queue historical messages. Disabling it pauses processing; it does not undo a completed attempt.
2. Create a fictional trial booking or reschedule/cancel a test lesson. The latest confirmation
   appears as Queued. Older pending confirmations for that booking become Replaced by newer update.
3. Choose Successful local delivery and Process local queue. Expect Simulated, not sent,
   with one attempt. Repeat processing: the message must not be processed a second time.
4. Open a message to see its saved body and attempt history. For a new message, choose Temporary
   rejection before processing. Expect Retry scheduled. Wait until its displayed retry time, then
   process successfully. Batches do not ignore backoff. No background worker is running locally.
5. On another new message, simulate Unknown outcome. Expect Needs review and no automatic resend.
   Later messages for that same booking show Waiting for earlier message with a link. Open the earlier message, confirm review and Suppress message;
   processing may now continue with the newer update. Other bookings are not blocked.
6. To test a historical confirmation, open its booking's Confirmation history and explicitly
   queue the latest confirmation. Old updates cannot be queued. No real email is ever sent.

See [message verification](scheduling-m7c2-messages-verification.md) for exact limits and remaining
real-delivery work. These controls are local preview only; no customer SMTP settings are used.

**Local booking slice is available; remaining milestone work is deferred for P1/M4.** Use
`bookings-owner` / `admin` at [Booking offers](http://127.0.0.1:8002/bookings/music-demo/lessons/booking-offers/).
`bookings-viewer` / `admin` can inspect schedules but cannot change offers, book on behalf of
staff or record money. These are isolated local credentials, not real customer credentials.

1. Open **Piano trial lesson**. Review service, teacher, location/room, price, advance notice,
   date horizon and public/family audience settings. Make-up eligibility is off. Changing it
   affects new bookings only; the studio credit policy must also permit the cancellation.
2. Open [Public trials](http://127.0.0.1:8002/bookings/music-demo/trials/). Select the offer,
   choose October 6 or 7, 2026 and **Show times**. Teacher availability is Tuesday/Wednesday,
   2-6 PM; occupied slots and teacher gaps are absent. October 13 is a closure.
3. Pick a free slot, use fictional contact/student details and confirm. Expect a receipt,
   with teacher, date/time, room and $30.00 collected by the studio. No card is charged and
   no email is sent. Refreshing the receipt does not duplicate the booking.
4. Reload availability in a second tab. The booked time and required teacher buffer should
   disappear. Submitting an older selection for that time must fail without adding a booking.
5. Open [Lesson bookings](http://127.0.0.1:8002/bookings/music-demo/lessons/bookings/). The booking
   has a shared **Test payment ledger** and a saved, explicitly unsent confirmation. Record
   a fictional $30 payment with a unique test receipt reference. Repeated submits must not
   duplicate it; excess payments/refunds must be rejected.
6. Follow **Schedule, attendance & changes**, reschedule a future lesson, then preview its
   cancellation. The default offer must say no make-up credit. Confirm; the slot is released,
   a cancellation preview is saved, and received money stays recorded for explicit refund review.
   Taylor Trial Demo is an existing booked/rescheduled/canceled example with $30 received
   and subsequently refunded in the test ledger. No real funds were moved.
7. At [Family access](http://127.0.0.1:8002/bookings/music-demo/lessons/family-access/), ordinary
   grants remain **Read-only**. Issue a new fictional Riley-only grant with **Allow booking**
   selected and confirm your authority to share the selected student's schedule. Open the
   private link using the existing M7c1 steps. It gains **Book a lesson**; Jamie is not offered.
   The staff **Preview** remains non-actionable even for this grant.
8. The existing in-app family session is **Park Booking Demo**, Riley-only with booking allowed.
   Riley's October 6, 5 PM booking is a working family example. Booking adds no new household,
   and does not consume the existing make-up credit. Payment and lesson credits are separate.
9. Check the booking page on a narrow mobile window: dates, times, contact fields and submit
   button must fit without horizontal page scrolling. Keyboard focus must remain visible.

Do not enable this on SBMC or send these local links to real families. Guest receipts are
restricted to their originating browser; durable verified access/email delivery is still to build.
See [M7c2 verification](scheduling-m7c2-verification.md) for impact, tests and remaining work.

### M7c1

Private family schedules. Use [Family access](http://127.0.0.1:8002/bookings/music-demo/lessons/family-access/)
with `bookings-owner` / `admin`, only in the isolated local preview. No email is sent.

An Active Park Guardian Demo grant for Riley and Jamie is available. Its **Preview** action
opens the same read-only layout without using a private invitation. The in-app browser also
has an already-open family session, valid until November 2, 2026. The earlier Riley-only
grant is Revoked, demonstrating access history. No real family data was used.

1. Choose **Choose guardian & students** for Park Family. Enter a fictional guardian name
   and keep `park-family@example.invalid`. Select Riley only, choose access duration, and
   check the authorization confirmation. No students are selected automatically.
2. Choose **Create access link**. Use **Preview family view** to inspect the result without
   consuming the guardian invitation. Riley's dates, teachers, rooms and credit appear;
   Jamie, private staff notes and staff billing/actions do not.
3. For the actual local family experience, open the displayed private link, then **Open my
   schedule**. The secret disappears from the address bar. The schedule remains available
   in that browser at `/bookings/music-demo/family/schedule/`; no enrollment/admin login is needed.
   A second use of the original invitation is rejected. It must first be opened within 24 hours.
4. Choose **Past lessons** and **Apply**. Riley's October 1 absence appears without its private
   staff note. Upcoming includes the October 21 lesson in Strings Room and clearly marked
   canceled/closed dates. The existing credit's expiry has not changed.
5. In the owner's **Family access** history, revoke the test grant with a reason and confirmation.
   Refresh the family page: access is unavailable immediately. It does not cancel lessons,
   remove credits, change enrollment or change billing.
6. Create a fresh grant for both Riley and Jamie to test the student selector. Adding another
   sibling later does not expand an existing grant. Owners must explicitly issue access for
   that child. Never send the local demonstration link to a real customer.
7. **Close private access** ends that grant. A different browser, cleared cookies, closed or
   expired access needs a new invitation. Automatic delivery and recurring re-entry remain
   rollout work. Self-booking and online lesson payments are NOT enabled by this step.

Automated checks also cover tenant/role/email isolation, real simultaneous opens/revocation,
expiry boundaries, forged form keys, CSRF, migration preservation, pagination and rollback.
Emily's unresolved policy questions remain in M7a's notes; they do not block this read-only
fictional demo, but they do block enabling self-service for SBMC.
See [M7c1 verification](scheduling-m7c1-verification.md).

### M7b2

Open [Make-up credits](http://127.0.0.1:8002/bookings/music-demo/lessons/credits/).
Local login: `bookings-owner` / `admin`. These credentials are only for the isolated preview.

1. Open Riley Park's credit. It is Available after the browser demonstration. Its history
   shows the original October 6 lesson, redemption, restoration, a second redemption,
   rescheduling and studio cancellation. The expiry remains January 4, 2027 at 2:30 PM.
2. Choose **Book make-up**. Try Alex Demo, Main Studio / Piano Room, October 6 at 4 PM:
   **Preview make-up** must report the teacher conflict with Jamie's lesson, with no booking button.
   Try January 6, 2027: it must reject the date beyond the credit's expiry.
3. Try October 14, 2026 at 3 PM, with a short reason. Preview and **Book using credit**.
   Confirm one 30-minute lesson appears and the credit becomes Booked. Refreshing/retrying
   the same confirmation must not book another lesson.
4. From the lesson, **Reschedule** to October 14 at 4 PM. Preview and confirm. The same
   credit points to the replacement; the original date and change remain in its history.
5. **Cancel** the make-up. Choose Family, enter a reason, preview, then confirm. With at
   least 24 hours' notice the same credit returns to Available, with no expiry extension.
   Studio cancellation also restores it. Late family cancellation forfeits the credit;
   no-shows/attendance do not automatically issue another credit.
6. To test a new cancellation, use another future Riley/Jamie lesson, not an already canceled
   or closed date. Preview shows the outcome for each selected scheduled date. Nothing changes
   until confirmation. Closure-only dates never create credits by themselves.
7. As owner, open **Make-up policy**. The fictional demo allows family cancellations with
   24 hours' notice, studio cancellations, and 90-day expiry. Change settings in two tabs:
   saving the older tab must be rejected with a reload link. Turning issuance off must not
   strand already issued credits. Restore demo settings afterward.
8. An Available/Expired unused credit can be revoked by its owner with a reason. A Booked
   or Used credit cannot be revoked; first resolve any future make-up. Revocation does not
   refund money. To keep Riley's example available, test revocation on a newly issued credit.

These dates are local demonstration data, not Emily's actual availability. Later testing may
require a new future lesson. Expiry is an exact elapsed-day deadline measured from the original
lesson's end; the displayed local hour can shift across daylight saving. The make-up must finish
by the deadline, not merely be booked before it.

**Still needed from Emily:** confirmed cancellation notice, make-up expiry/limits, exceptions,
holidays and self-booking policy. The ready-to-send question is in M7a's expanded notes below.
Do not enable SBMC or promise policy parity yet. Owner exception grants for past absences,
term caps, transferable credits and family self-booking are not included in this slice.
See [M7b2 verification](scheduling-m7b2-verification.md) for impact, migration and test evidence.

### M7b1

[Open Music Studio Demo](http://127.0.0.1:8002/bookings/music-demo/lessons/).
Owner: **`bookings-owner` / `admin`**. Read-only: **`bookings-viewer` / `admin`**.
Fictional tenant only; these are local credentials, not customer credentials.

1. Open Riley's original Tuesday piano schedule. Choose Reschedule on a future scheduled
   date. Choose Only this lesson, a Wednesday date, Alex, Main Studio, Strings Room and a
   start time between 2 and 5:30 PM. Add a reason, then Preview changes.
2. Verify the old and new date/time. Confirm. The replacement detail links to Original
   schedule & change history. The old date remains Rescheduled with the reason and a link
   to its new date. Reloading does not create duplicates or charge anyone.
3. Preview This and later scheduled lessons in a series. Check the exact dates listed:
   canceled/closed/past dates and previously separated changes must not move. Only this
   saved series is included, not all previously created exceptions.
4. Try moving into another student's occupied teacher time, outside teacher availability,
   or onto the October 13 closure. A conflict must hide Confirm changes; nothing moves.
   Teacher substitutions require an eligible teacher with availability at that location.
5. Open a completed lesson's detail and choose Attendance. Save Present with a reason.
   Reopen and correct it to Absent with another reason. The current outcome appears on
   the lesson; internal audit retains the correction. Future/canceled lessons cannot be marked.
6. Sign in as bookings-viewer: schedule/history is readable, but Reschedule, Cancel and
   Attendance controls must be absent. Existing enrollment menus and SBMC stay unchanged.

Browser checks passed. Riley's October 20 lesson was moved to October 21 at 3 PM in Strings
Room, and its original schedule retains the link/reason. Future-date preview correctly excludes
that exception and the closed date. A fictional October 1 lesson was added for attendance:
[Open attendance example](http://127.0.0.1:8002/bookings/music-demo/lessons/series/82f0ae0f-6591-41e4-8e13-8d235ccb8409/).
It is marked Absent after a tested correction from Present. Desktop and 390-pixel mobile
layout checks passed; read-only staff cannot change dates or attendance. No paid API calls.
Make-up credits, automatic family notices and tuition are not implemented here.
See [M7b1 verification](scheduling-m7b1-verification.md).

### M7a

[Open Music Studio Demo](http://127.0.0.1:8002/bookings/music-demo/lessons/).
Owner: **`bookings-owner` / `admin`**. Read-only: **`bookings-viewer` / `admin`**.
This is a separate fictional tenant in the isolated local database, not SBMC.
Desktop workflow, mobile layout and restart persistence checks passed.
Accepted for progression by the October 3 request; push authorization remains outstanding.

1. Open Setup. Inspect services, eligible teachers, availability, lesson closures, families
   and students. Main Studio has Piano Room and Strings Room. Alex teaches piano; Morgan
   teaches violin. Prices/payments are not part of this scheduling slice.
2. Riley's piano schedule runs Tuesdays at 3 PM, October 6 through November 17. October 13
   is closed for a fictional training day. The local time stays 3 PM across the November clock change.
   Jamie also has a 4 PM Tuesday schedule; October 20 is canceled with its reason retained.
3. Choose Schedule lessons. Select Jamie, Piano lesson, Alex, Main Studio and Piano Room.
   Use Riley's dates/time and Preview dates. Conflicts must prevent confirmation. For an
   available test, change to Wednesdays, October 7 through November 18 at 4 PM. Available
   dates should be listed before Confirm schedule appears. The Tuesday closure does not apply.
4. Confirm a conflict-free schedule. Reload: dates remain saved, no extra schedule is created,
   and no charge, enrollment status change or email is triggered.
5. Filter Lessons to Park Family. Inspect both siblings' schedules. Open a series and Cancel
   a future occurrence. Choose Only this lesson or This and future lessons, enter a reason,
   and confirm. Canceled rows remain in history and stop holding teacher/student/room time.
6. Attempt to add a lesson closure over an already scheduled lesson. It is rejected until
   staff explicitly cancel or move affected lessons. Room/teacher/time changes are M7b1;
   make-up policies remain M7b2. Availability windows used by a series
   and teacher gap settings lock; adding additional availability remains possible.
7. Sign in as bookings-viewer: schedules/setup are readable, but editing, creation and
   cancellation are unavailable. No guardian access is implemented yet; M7c covers that.

**Note to send Emily:**

Hi Emily! We're working on an optional scheduling feature for Pontora, and I'd like to
understand how you have MakeMusic set up so we support the way SBMC already works.
Do you assign students their regular lesson times, or can families choose/change them?
What happens when a family cancels, a teacher cancels, or a lesson falls on a holiday:
is it charged, skipped, rescheduled, or eligible for a make-up? If you offer make-ups,
is there a notice deadline or expiry date? A screenshot of those settings, with any
student/payment details hidden, would be perfect. No changes to your current setup are needed.
Thanks! Ketan

Engineering evidence: [M7a verification](scheduling-m7a-verification.md).

### M6

Local only: [Camp administration](http://127.0.0.1:8002/bookings/workshop-demo/camps/1/)
and [customer registration](http://127.0.0.1:8002/bookings/workshop-demo/camps/1/register/).
Owner username **`bookings-owner`**, password **`admin`**. Real Stripe is not connected.

1. Open Camps, then Autumn Makers Camp. See three October 12-14 dates, Camp studio, $150
   full price/$50 deposit per participant. Jordan Camp Demo is confirmed for two attendees;
   Jamie Cancel Demo is canceled. There are six places left on every date.
2. Edit camp. Prices, payment method and selected dates are locked because registrations
   exist. Uncheck Open registration and save: new registration closes, existing reservations
   remain. Reopen it before continuing. To create another camp, choose a workshop under
   Create a camp on the Camps list, then select 2-31 prepared sessions.
3. Open customer registration. Enter fictional contact details and two participants with
   different choices. Submit once. All three dates should be held under one $300 order and
   $100 deposit. Reload the receipt: it should not create a second registration.
4. Open View payment & balance. Pay in the **local simulator**, then click Simulate verified
   payment. Expect Confirmed, $100 verified and $200 remaining. No real money moves.
5. Return to Camps and inspect the new row. Open View roster for the middle date. Both
   participants and their choices should appear in the confirmed preparation totals.
6. Open the reservation and cancel it using the confirmation checkbox. Every date's space
   is released. The recorded money is not erased or silently refunded. Use the existing
   Approve refund flow to exercise a refund; pending is not the same as completed.
7. Owner can edit/setup/cancel; `bookings-viewer` / `admin` can view the roster without these
   controls. Enrollment-only school accounts do not gain Bookings access.

Engineering evidence and remaining paid-pilot gates: [M6 verification](scheduling-m6-verification.md).

### M1a

Local only: [Open Bookings](http://127.0.0.1:8002/bookings/).
All accounts below use password **`admin`**. These credentials are for the isolated preview,
not demo.mypontora.com or production. Sign out before switching accounts.

| Account | Username | Expected access |
| --- | --- | --- |
| Bookings owner | `bookings-owner` | Workshop Demo, including Team access and editing |
| Read-only staff | `bookings-viewer` | Workshop Demo without editing or Team access |
| Multi-business staff | `bookings-multi` | Workspace chooser; permissions vary by business |
| SBMC smoke account | `sbmc-smoke` | Existing enrollment dashboard, not Bookings |
| Kid Works smoke account | `kidworks-smoke` | Existing enrollment dashboard, not Bookings |

1. Sign in as `bookings-owner`. Expect Workshop Demo with Bookings navigation and no enrollment trial banner.
2. Open Team access. Only this workspace's team should appear.
3. Sign out and sign in as `bookings-viewer`. View setup; editing and Team access should be absent.
4. While signed in as the viewer, open [Team access directly](http://127.0.0.1:8002/bookings/workshop-demo/access/). Expect a 404, not team data.
5. Sign in as `bookings-multi`. Expect a workspace chooser; switching workspaces must use the role for the selected business.
6. Sign in separately as `sbmc-smoke` and `kidworks-smoke`. Expect their existing enrollment dashboard and Leads/Submissions navigation, without Bookings added.
7. Return to `bookings-owner` for the remaining milestones.

### M1b

[Open Workshop Demo](http://127.0.0.1:8002/bookings/workshop-demo/).
Username **`bookings-owner`**, password **`admin`**. Read-only comparison: **`bookings-viewer` / `admin`**.

1. Open Locations & rooms. Main Studio and Room 1 should be present. Add a room with capacity 12; save and reopen it.
2. Open Workshops, then Design Your Plushie. Add a session on a future date with a start time and duration in minutes, the new room and capacity 10.
3. Try capacity 15 for that room, or an end time before the start. Expect a validation error and no invalid save.
4. Save valid values. Expect Draft. Reload and reopen Edit; the saved date, local times, room and capacity must remain.
5. Publish the session, then publish its workshop if needed. Open View customer schedule from Dashboard; the published session should appear.
6. Create another session in the same room overlapping the first. Saving a draft is allowed; publishing must show a room conflict. Change its time to after the first session, then publish successfully.
7. Unpublish the second session. Refresh Public schedule; that session should disappear while the first remains.
8. Sign in as `bookings-viewer`. Existing sessions are readable but Add/Edit/Publish controls are absent.
9. Open the same edit form in two tabs; save the first, then save the older tab. The old save
   must be rejected. Use **Reload saved settings** to recover the current version. This also
   applies to choices, camps and lesson setup. See [audit fix checks](#audit-fixes-manual-checks).

M1b is setup and publishing, not paid reservations. Existing M2 request-enabled demo fixtures
retain their stored opt-in; the October 6 cleanup removed request switches from Workshop setup.
Publishing alone does not start collecting unpaid requests.

### M2

Staff: [Workshop Demo](http://127.0.0.1:8002/bookings/workshop-demo/),
**`bookings-owner` / `admin`**. Read-only staff: **`bookings-viewer` / `admin`**.
Customer: [Public schedule](http://127.0.0.1:8002/bookings/workshop-demo/schedule/) (no login needed).
These are local preview accounts only.

1. Open Workshops, then Design Your Plushie. On this uncatalogued legacy demo, expand Booking options for Design and Color. The fixture retains its unpaid-request opt-in; its checkbox is no longer in Workshop setup, and publishing by itself does not enable requests. This is the historical M2 flow, not Plushumi's confirmed-booking flow.
2. Open Public schedule, choose a future session and click Request places. If the October 6 sample is in the past, add and publish a new future session first. Expect the correct date, time and room, plus a clear request-only notice.
3. Enter a fictional contact name/email and one participant. Leave a required field or choice empty and submit; expect validation, not a saved incomplete request.
4. Select Cat and Blue for the first participant. Add a second participant and select Bunny and Pink. Add then remove a third participant; the first two names and choices must remain.
5. Submit. Expect Request received and an explicit notice that no place is reserved and no payment collected. Refresh the receipt; it must not create another request.
6. Back in staff Requests, open that contact. Expect both participants and their separate selections. Open Session roster & preparation: this request adds two participants, one of each selected design/color, to any existing pending totals.
7. Reload the staff page; saved details and totals remain. Editing option labels later must not rewrite what existing customers requested.
8. In request details, check Remove this request from pending preparation, then Withdraw request. Expect Withdrawn; the historical details stay, but this request's participants and quantities disappear from the pending preparation totals.
9. Sign in as bookings-viewer. Requests and preparation are readable; choice editing and withdrawal controls are absent. Return to bookings-owner afterward.
10. On a phone-sized window, check participant controls, images and choices. Public pages must not expose the staff roster or customer contact list.

This milestone supports up to 20 participants per request, also bounded by the session's
capacity. Separate pending requests do not reserve capacity and may together exceed it;
staff see a warning, not a false claim of confirmed availability. Payments, confirmations,
emails, inventory availability, Wix and Notion connections are not implemented in M2.

### M3

Staff: [Party inquiries](http://127.0.0.1:8002/bookings/workshop-demo/parties/),
**`bookings-owner` / `admin`**. Read-only staff: **`bookings-viewer` / `admin`**.
Customer: [Party inquiry](http://127.0.0.1:8002/bookings/workshop-demo/workshops/1/party/).
These credentials and records are local preview only.

1. The historical Design Your Plushie preview retains its stored party-inquiry opt-in; newly created workshops default off. The October 6 cleanup removed this checkbox from Workshop setup. Use the enabled fixture below to exercise the historical M3 flow.
2. Open Public schedule and Plan a party. Enter a fictional organizer name/email, a future date/time, duration, group size and shared/private-room preference. No attendee roster or payment is required for an inquiry.
3. Try a past date or zero participants. Expect validation, not a saved inquiry. Submit valid details; expect Party inquiry received and a clear notice that nothing is booked or paid.
4. Open staff Parties and your organizer's inquiry. The contact, time, group size, preference and notes must match. Initially it should say Needs review, with no room or quote.
5. Choose a room, enter total 400 and deposit 500, then save. Expect an error because the deposit exceeds the total. No invalid proposal should be saved.
6. Change the deposit to 100, add preparation notes and choose Reviewed (not booked). Save and reopen. Expect $400 total, $100 deposit, $300 balance, the notes and the proposed room, but no confirmed booking.
7. For a private-room inquiry, try Reviewed without assigning a room. Expect an error. Choose a room smaller than the group and expect rejection. Overlap an existing published session and expect a room warning, not a promise of availability.
8. Open the same review in two tabs. Save a change in one, then save the older tab. Expect a stale-edit error. Click Open saved review to return to the current saved version; the first change must remain.
9. Sign in as bookings-viewer. Party details and warnings remain readable; the staff editing form must be absent. Return to bookings-owner afterward.
10. Reload/reopen the saved review. Check the inquiry form and staff page on a narrow phone-sized window. Existing Requests and their participant preparation remain separate and unchanged.

The seeded Alex Party Demo intentionally overlaps Room 1's October 6 workshop, demonstrating
the warning. Use a new future date if those sample dates have passed. No room is held and no
money is collected in M3. Wix/Notion, external availability, customer quote delivery and
per-attendee party rosters are not implemented here. See [M3 verification](scheduling-m3-verification.md).

### M5a

Staff: [Reservations](http://127.0.0.1:8002/bookings/workshop-demo/reservations/),
**`bookings-owner` / `admin`**. Read-only comparison: **`bookings-viewer` / `admin`**.
These are fictional local test records. No money, emails or external API calls are sent.

1. Open Reservations. New workspaces start disabled. Enable test reservations for Workshop Demo if needed; other schools remain unchanged.
2. Inspect Taylor Party Demo: eight people in Room 1, October 7, 2-4 PM; $400 total, $100 test deposit received, $300 remaining, Confirmed. Morgan Demo demonstrates a canceled workshop reservation with its full $100 test refund retained in history.
3. To test a fresh workflow, submit a new fictional request from Public schedule, then open it under staff Requests. Choose Create reservation. Enter total 100 and deposit 125; expect rejection. Change the deposit to 25 and approve the quote; expect Held - awaiting payment and a 30-minute deadline.
4. Record an Offline payment received of 25 with a new reference such as YOURTEST-DEPOSIT-01 and the acknowledgment checked. Expect Confirmed, received $25, remaining $75. This records a fictional offline receipt; it does not charge a card.
5. Try to record 1000 against the same order. Expect rejection with the saved balance unchanged. Record 75 with a different reference; expect zero balance. Reload: both entries remain. Reusing a receipt reference must not create another payment.
6. Check the cancellation acknowledgment and cancel. Expect Canceled and released places, but the received money remains recorded. Choose Offline refund already issued, enter 100 with a new reference and acknowledge it. Expect refunded $100 and net received $0; no external refund is initiated.
7. For a party, submit a new inquiry at a future non-conflicting time. Staff must first assign a room, set a valid quote and mark Reviewed. Create reservation uses that saved quote; the quote becomes fixed. A $100 deposit on a $400 quote confirms it with $300 remaining. The original proposal is no longer editable.
8. Try creating a reservation for Alex Party Demo. It overlaps Room 1's published October 6 workshop and must be rejected. Two groups cannot reserve the same exclusive room; workshop capacity includes current holds and confirmed groups.
9. Stop new reservations from the list. Existing records and authorized settlement remain available; new creation is blocked. Re-enable when done. An expired hold no longer reserves capacity; late money is recorded, but a booking is confirmed only if capacity can still be secured.
10. Sign in as bookings-viewer. Details/history remain readable; creating, canceling, enabling and recording money controls are absent. Return to bookings-owner afterward.
11. Check the details/form on desktop and a narrow phone. Refresh to confirm persistence. The existing SBMC/Kid Works enrollment screens should have no Bookings links or changed behavior.

Use future dates if the October samples have passed. Quotes are USD only. The 30-minute hold
is a local offline prototype policy, not yet configurable and not a Stripe payment expiry rule.
Cancellation and refund are separate actions. No online checkout, recurring charges, Notion,
Wix, automatic messages, customer quote acceptance or automatic enrollment is included.
See [M5a verification](scheduling-m5a-verification.md) for concurrency/expiry tests and limitations.

### M5b

Staff: [Reservations](http://127.0.0.1:8002/bookings/workshop-demo/reservations/),
**`bookings-owner` / `admin`**. Read-only staff: **`bookings-viewer` / `admin`**.
Only the isolated local preview has the simulation. It cannot contact Stripe or collect money.

October 3 correction: confirmed balance checkout must remain available while new intake is
paused. Follow [audit fix checks](#audit-fixes-manual-checks) to verify without changing the
fictional received amount. Disabled merchant/workspace and the checkout cutoff still apply.

1. Open Sam Checkout Demo. It is the completed browser-test example: $100 total, $25 deposit and $75 balance verified locally. The history also includes an expired balance checkout. This is fictional money.
2. To test from the beginning, submit a new one-person request from Public schedule. Open it under staff Requests, then Create reservation. Enter total 100, deposit 25, check Use Stripe sandbox checkout and approve. Expect a held reservation and an Open customer payment page link.
3. Open the customer payment page. It must show $100 total, $0 verified and Pay $25.00 in test checkout. No staff navigation or other families' data appears on this page.
4. Click Pay. The next page is explicitly labelled Local payment simulation, not Stripe. Choose Return without payment. The reservation must remain unpaid. Reopening checkout reuses the same attempt.
5. Choose Simulate verified payment. Expect Confirmed, $25 verified, $75 remaining. Reload the staff reservation: the same amounts and a verified test entry must appear.
6. Start the $75 balance checkout and choose Simulate checkout expiry. The already-confirmed reservation remains confirmed; the deposit is unchanged. Start again, simulate payment, and expect $100 verified, zero balance and no further Pay button.
7. On a different unpaid reservation, open checkout, then try canceling from the staff page. Cancellation must be blocked while checkout may still accept payment. Simulate checkout expiry first, then cancel. No automatic refund is claimed.
8. For a party, use a future non-conflicting room/time, save its quote and mark Reviewed. Create a test reservation with sandbox checkout selected. The same checkout uses the party's saved deposit and balance, not an amount supplied by the browser.
9. Sign in as bookings-viewer: histories remain readable but quote creation, payment-link creation and financial edits are not available. Return to bookings-owner afterward.
10. Check customer and staff screens on desktop/mobile, then reload. Balances persist. Unrelated SBMC/Kid Works enrollment screens do not gain booking links or changed application statuses.

Use future dates if sample sessions have passed. Customer payment links expire after 24 hours;
open the staff page for a fresh link. Online checkout closes 40 minutes before a session.
The provider expiry is 35 minutes, but an open/unknown attempt retains capacity until verified
expiry, not just a local timer. Retry/reconciliation and Stripe refunds are covered by M5c;
do not manually clear an uncertain payment to free a place. Recurring billing remains M8.
No actual Stripe cards, account connection, payout, webhook delivery or refund has been tested.
The simulation harness is temporary local-only under `/tmp/pontora_m5b_*`; it is not a shipped
payment backend and no fake-payment endpoint is added to the application's normal URL config.
See [M5b verification](scheduling-m5b-verification.md) for impact and external verification gates.

### M5c

Local only: [Reservations](http://127.0.0.1:8002/bookings/workshop-demo/reservations/).
Username **`bookings-owner`**, password **`admin`**. Read-only check: **`bookings-viewer` / `admin`**.
All provider responses are simulated. No Stripe connection, real refund or charge is made.

1. Open **Payment follow-up**. It includes open/uncertain payments, pending refunds, payment reviews and canceled bookings with money still received. Opening/refreshing pages makes no provider requests.
2. Open **Casey Recovery Demo**. The manual check recovered a missing $25 deposit notification, then canceled the reservation and verified a $15 partial refund. Expect Received $25, Refunded $15, Net received $10 and separate payment/refund history entries.
3. On Casey, choose **Approve refund**. Try $11: it must be rejected because only $10 remains. Approve up to $10 with a reason and confirmation instead. The initial state is **Refund processing**; the refunded total must not change yet.
4. After at least 30 seconds, use **Check refund status**. The local simulator returns success on this check; expect one new verified refund entry and the correct balance. Repeated clicks/reloads must not create more entries. A real provider may take longer and is not assumed successful.
5. Open **Avery Close Demo**. Its open checkout was verified as closed, so it shows **Checkout expired** and no money received. You can cancel the reservation. For a new open checkout, cancellation must be blocked until you tick **Stop this checkout from accepting payment** and use **Close checkout**, then cancel. If verification fails, capacity stays held and a warning appears.
6. A payment-review booking has **Recheck and confirm**. It succeeds only with adequate verified payment, no extra/open checkout and capacity still available. Otherwise it remains unconfirmed; cancel/refund instead of promising a place.
7. Sign in as `bookings-viewer`: history is readable, but recovery, refund, cancellation and confirmation actions are unavailable. Direct requests to these actions must be denied.
8. Refresh or restart the isolated preview: approvals, provider identities, verification history and balances persist. The agent verified a pending refund across a restart before completing it.

The recovery command processes bounded batches with leases/backoff; it is not running
automatically in production. Six failed checks pause that record for operator investigation.
Unknown creates are looked up, not blindly recreated after Stripe's idempotency retention window.
Normal refunds require cancellation. A confirmed booking can retain its place while staff refund
only the verified excess above its quote, less pending refunds. Automatic refund policies,
disputes and dashboard-originated adjustments need later review. Real sandbox verification,
reliable scheduler/monitoring and deployment approval are
still required before a paid pilot. See [M5c verification](scheduling-m5c-verification.md).

## Effort and Major Checkpoints

Estimates include implementation, meaningful tests, review and browser checks. They exclude
waiting for customer access/answers, user acceptance and production migration. One focused
engineering day is roughly seven hours. These are planning ranges, not promises of elapsed
time or autonomous background work. Re-estimate after each accepted milestone.

| Milestone | Difficulty | Focused effort | Major checkpoint |
| --- | --- | --- | --- |
| B1 | High: shared billing and customer-access risk | 4-8 focused hours; provider access and email setup may extend this | Verified provider events, emails and sandbox recovery; reviewed live activation |
| P1 | High: product access, data preservation and usability | 12-20 hours | Focused workshop/music workspaces and repeatable safe release checks |
| M4a | High: credentials, tenant boundaries and adapter contracts | 8-14 hours | Reusable configuration and locally verified payload preview; part of M4, not additional scope |
| M1a | Medium-high: security boundary | 6-10 hours | Standalone access works; enrollment remains isolated |
| M1b | Medium: validated setup | 8-12 hours | Staff can configure and publish a basic offering |
| M2 | High: complete saved workflow | 12-20 hours | First useful customer-to-preparation demonstration |
| M3 | High: group/exclusive resources | 8-16 hours | Party requests work; paid confirmation still gated |
| M4 | Very high: provider uncertainty | 24-48 hours remaining; provisional | Authorized provider proof, then connected journey, delivery/recovery and end-to-end verification |
| M5a | Very high: capacity and financial records | 8-14 hours | Native holds and explicit offline settlement verified |
| M5b | Very high: provider identity and settlement | 12-20 hours | Local checkout/settlement first; external sandbox evidence still required |
| M5c | Very high: recovery and paid-release gate | 8-14 hours | Expiry, late payments and refunds reconciled |
| M6 | High: all-session consistency | 16-28 hours | Basic fixed-cohort camps work |
| M7a | High: recurrence and shared resource safety | 12-20 hours | Staff can set up and reserve a finite lesson schedule |
| M7b1 | High: atomic changes and preserved history | 10-16 hours | Staff can reschedule lessons and record attendance |
| M7b2 | High: entitlement safety | 8-14 hours | Staff can apply agreed make-up rules and track credits |
| M7c1 | High: guardian isolation and secure access | 4-7 hours | Families privately view authorized schedules and credits |
| M7c2 | High: availability, access, delivery and settlement | 18-30 hours | Booking, secure return access and recoverable confirmations/payments |
| M8 | Very high: recurring money lifecycle | 24-40 hours | Recurring billing passes renewal/failure/cancellation checks |
| M9a | High: inventory concurrency and opt-in enrollment boundary | 12-20 focused engineering hours | Native tours and optional leads; unit, integration, concurrency and manual checks included |
| M9b | High: shared enrollment entry points | Original planning allowance: 12-20 focused engineering hours | Local application gates and playdates; customer rollout work is separate |
| M9c | High: family identity, recovery and shared resources | Planning allowance: 3-5 focused engineering days including verification | Complete local demo; live delivery and paid release remain gated |
| M9d | Medium: opt-in shared navigation and realistic integrated fixtures | Planning allowance: 4-8 focused engineering hours including verification | One ordinary school-admin account exercises all implemented local features; no customer activation |
| P3 | High: shared stock consistency, reservation/payment races and cross-flow reuse | Planning allowance: 2-3 focused engineering days including automated/browser verification | Catalog and per-studio physical stock work across workshops, parties, camps and preparation; staff theme matches enrollment |
| P2 | Medium-high: branded forms, durable booking flow and honest provider boundaries | Planning allowance: 1-2 focused engineering days including automated/browser verification | All six shared concepts become a coherent local journey; only provider steps simulated |
| M9 | High: shared-code and release risk | M9a/M9b above; release gates separate | A local demo is not production readiness |

The earlier full-scope estimate of 178-314 focused hours is historical, not a remaining-work total.
M4 now shows a revised remaining allowance; M9 needs re-estimation for the concrete preschool
workflow. Do not sum completed milestone estimates as remaining effort. Later estimates remain
low confidence until customer workflows/provider capabilities are verified. No entire milestone
is done until its acceptance checks pass; no paid launch merely because a prototype works.

## Tuesday Demonstration Boundary

Revised estimate for M1a/M1b plus a narrow M2: 26-42 focused engineering hours, approximately
4-6 working days. The earlier 2-4 day estimate was optimistic before accounting for access
isolation, regression checks and acceptance gates. Tuesday remains a target for a bounded
demonstration, not a guarantee; re-estimate from measured progress after M1a. These are effort
estimates, not unattended work or elapsed calendar promises.

Minimum useful demonstration:

- One isolated test business/location with a saved workshop, room, date/time and capacity.
- Customer selects a session and participant-specific design/color options in one journey.
- Persisted request and accurate staff preparation roster; refresh/restart does not lose it.
- Clearly unpaid/pending state. No fake payment success or guarantee of a reserved seat.
- Basic party request is an early stretch, not grounds to delay a correct workshop journey.

Out of the Tuesday baseline: live charges, production availability, a working Wix/Notion
connection without its sandbox proof, automatic SBMC imports/cutover, complete camp
operations, recurring billing, waitlists and full competitor parity. Demonstrating Pontora's
native data flow does not validate a provider integration. Tell Jasmine exactly which exists.

## Customer Combinations

| Package / authority | Daily workspace | Enrollment required? |
| --- | --- | --- |
| Bookings only / Wix | Wix and optional Notion; minimal Pontora setup/recovery | No |
| Bookings only / native | Pontora Bookings; optional downstream destination | No |
| Enrollment + Bookings / native | Existing school admin with gated booking sections | Yes, only for enrollment features |
| Enrollment + connected provider | Linked provider-backed bookings; existing enrollment unchanged | Only if the customer buys enrollment |

Every customer has a tenant and authorized account owner. Reuse identity, not blanket
enrollment permissions. Someone must own connection failures and account settings even if
staff do all daily work in Notion. Operator-assisted setup is possible by explicit agreement.
Connectors are independently optional. Do not expose unsupported combinations as working
settings, and never let two authorities sell the same teacher/room/seat pool.

## Setup Acceptance Checklist

1. Choose product access and booking authority; choose optional destinations separately.
2. Set business branding and locations, then rooms/resources and their availability.
3. Define offerings: instrument/lesson duration or workshop/session and allowed visual choices.
4. Assign qualified staff, hours, closures, buffers, lead times and capacity.
5. Set prices/payment policy and cancellation rules; keep payment disabled until validated.
6. Preview as a customer, run a test request, inspect roster/preparation, then explicitly publish.

Only published offerings appear to customers. Existing SBMC bookings must be imported and
reconciled before offering conflicting times, not inferred from enrollment preferences.

## Engineering Gates

- No default enablement or record changes for SBMC, Kid Works or any existing school.
- No duplicate payment implementation: shared processor/account boundary; separate business handlers.
- Additive migrations reviewed and tested locally; never auto-apply production changes for a demo.
- Per-milestone tests for authorization, cross-tenant access, disabled modules and invalid input.
- Network blocked in ordinary tests: no paid APIs, real mail, provider bookings or charges.
- PostgreSQL concurrency tests before promising seat/resource reservation or confirmed native bookings.
- Shared login, billing or navigation edits require written impact review and regression evidence.
- Browser checks for the actual delivered screens, including mobile, errors and saved-state reload.
- Follow repository coverage/full-suite requirements; report exact results and unverified cases.
- Production release needs explicit approval, supported migration, rollback and reconciliation checks.

## Required Before Every Push

These are blocking gates, not an aspirational checklist. Apply them to the exact revision and
complete diff being pushed, including all outgoing commits. Do not include another window's
unreviewed work. Push authorization and deployment approval are separate from passing tests.

1. Self-review the complete change, including migrations and configuration. Record which shared
   surfaces are touched, who can be affected, default-off behavior and the rollback approach.
2. Run meaningful unit tests: normal behavior, boundaries, invalid input and failure handling.
   Assert outcomes, not just mock interactions; meet the repository's 90% new-code coverage floor.
3. Run integration tests through real application boundaries and the test database: routing,
   authentication, tenant/role permissions, persistence, configuration and relevant shared services.
   Check booking-only access and enrollment-disabled behavior without granting enrollment roles.
4. Run the full regression suite on the committed candidate; SBMC, Kid Works and other existing
   flows must stay green. Use PostgreSQL for reservation/concurrency semantics when in scope.
5. Perform browser/manual testing personally: exercise the delivered workflow, an invalid/error
   path, refresh/persistence, and mobile/desktop layout when UI changes. Smoke-test SBMC and a
   non-opted-in school for unchanged login/navigation and the shared workflows affected by the diff.
6. Record evidence below and summarize it to the user before push. Relevant changes after
   verification invalidate affected results. Failures or unavailable required checks stop the push;
   report them rather than quietly reducing coverage or transferring QA responsibility to the user.

No paid APIs, real emails, real bookings or charges in routine tests/manual smoke checks. Use
local test data and network-blocked fixtures that match actual SDK types and provider payloads.
Provider-backed releases additionally need authorized sandbox end-to-end evidence, including
retries/cancellations and correct account/environment routing. Mocks do not establish feasibility.

### Verification Record Template

Complete for each push candidate; do not mark an unrun check as passed.

- Milestone and tested commit/revision:
- Reviewed files, shared impact and rollback:
- Unit tests and coverage (command, result and duration):
- Integration tests (command, database/backend, result and duration):
- Full regression suite (command, result and duration):
- Manual/browser scenarios (environment, viewport/role, actions and observed outcomes):
- SBMC/disabled-school regression evidence:
- Provider fixture/sandbox boundaries and any unverified claims:
- Remaining failures or blockers; ready to push yes/no:
- User acceptance and push authorization, recorded separately:

Current record: [M4a impact and verification](scheduling-m4a-verification.md).
Historical records: [P1 impact and verification](scheduling-p1-verification.md),
[M7c2 impact and verification](scheduling-m7c2-verification.md),
[M7c1 impact and verification](scheduling-m7c1-verification.md),
[M7b2 impact and verification](scheduling-m7b2-verification.md),
[M7b1 impact and verification](scheduling-m7b1-verification.md),
[M7a impact and verification](scheduling-m7a-verification.md),
[M6 impact and verification](scheduling-m6-verification.md),
[M5c impact and verification](scheduling-m5c-verification.md),
[M5b impact and verification](scheduling-m5b-verification.md),
[M5a impact and verification](scheduling-m5a-verification.md),
[M3 impact and verification](scheduling-m3-verification.md),
[M2 impact and verification](scheduling-m2-verification.md),
[M1b impact and verification](scheduling-m1b-verification.md),
[M1a impact and verification](scheduling-m1a-verification.md).
M1a, M1b, M2, M3, M5a and M5b accepted by the user's instructions to proceed.
M5c and M6 have verified local builds. M6, M7a, M7b1, M7b2 and M7c1 are accepted. M7c2's local booking slice and message queue/recovery slice are verified;
real paid-pilot gates remain uncleared.
Push authorization remains outstanding. Nothing pushed.

## Decision / Acceptance Log

- October 4: user prioritized the preschool tour -> application -> playdate integration after
  M4. Treat it as the first opt-in M9 slice, not a second calendar or implicit change to existing
  schools. M4a remains ready for user testing; connected M4 is not done and needs substantive
  implementation plus authorized provider verification. No new external-access approval.

- October 4: user requested the next step. Continue M7c2 with reusable local message queuing
  and recovery before guest re-entry/online trial settlement. This slice is high difficulty,
  estimated 3-5 engineering hours. The original 6-11 hour M7c2 estimate covered only the booking
  slice and understated delivery/access/payment work; revised full-milestone estimate is 18-30
  hours, not a production deadline. Keep existing provider and enrollment behavior untouched.
  Local queue/recovery implementation is now verified (2,793 tests and desktop/mobile journeys).
  Critical review fixed unclear waiting states and cramped mobile message controls. Next planned
  slice is verified guest return access, followed by online trial settlement and real delivery work;
  do not mark M7c2 complete or enable live customers yet.

- October 3-4: user requested M7c2, accepting M7c1 for progression. Implemented the local
  booking slice without expanding existing family access. Staff-assigned and customer-booked
  lessons share inventory checks/locks; money reuses core.payments. Trial make-up eligibility
  is separately opt-in, not silently inherited. Actual delivery and online lesson settlement
  remain incomplete, with all public/family booking routes gated to local preview. No rollout,
  SBMC enablement, real provider calls, commit or push.

- October 3: user requested the next part, accepting M7b2 for progression. Split M7c into
  M7c1 read-only family access and M7c2 booking, retaining the combined 10-18 hour planning
  estimate. Exact student scope, owner attestation and revocable invitations are verified
  before granting family write/payment permissions. No SBMC, enrollment or payment changes.
  Manual invitation sharing is a local demonstration limitation, not a completed delivery system.

- October 3: user requested M7b2, accepting M7b1 for progression. Implemented a default-off,
  configurable staff make-up workflow in the Bookings module. No second money ledger or
  payment provider implementation. Cancellation policy snapshots survive later setting changes;
  credit redemption shares the existing conflict checks and tenant lock. No production/main-local
  migration, SBMC enablement, external API calls, commit or push. Emily's unknown policies still
  gate customer enablement, not this fictional demonstration. M7c remains planned.

- October 3: user requested pending M7 work, accepting M7a for progression. Split M7b into
  M7b1 staff changes/attendance and M7b2 policy/credits so unknown rules are not invented.
  M7b1 is ready for user testing: 2,521 full-suite tests in 96.03 seconds, 99.95% combined
  scheduling/payments coverage, lesson code 100%. Self-review fixed stale cancellation scope;
  browser testing found and removed a hidden-field label. Isolated migration preservation,
  concurrent room claims, mobile layout, attendance correction and read-only access passed.
  M7b2 and M7c remain planned; Emily's note remains visible. No production migration, SBMC
  enablement, external provider calls, commit or push.

- October 2: M7a implemented locally. 2,461 regression tests passed in 87.41 seconds;
  99.94% scheduling/payments line/branch coverage; new lesson models/forms/services/views 100%.
  Desktop workflow, mobile layout and restart persistence checks passed. An initial browser
  resize timeout resolved before handoff. Self-review added migration preservation and competing lesson/workshop
  room-claim tests, plus a closure-date overflow guard. Emily's policy note is in M7a's notes.
  No SBMC enablement, money collection, production migration, commit or push. M7b/M7c not started.

- October 2: M6 ready for user testing. 2,402 regression tests passed in 74.35 seconds;
  99.93% combined scheduling/payments line/branch coverage; new camp forms/services/views 100%.
  Browser checks covered owner setup, per-attendee choices, one simulated deposit for all dates,
  middle-date preparation, cancellation, intake closure/reopening, mobile layout and persistence.
  Self-review corrected the online intake cutoff and clarified confirmed preparation totals.
  Isolated additive migration preserves existing reservations, ledger records and school plans.
  No enrollment flow change, real provider call, production migration, commit or push.

- October 1: M5c local build ready for user testing. 2,334 full regression tests passed;
  99.92% combined scheduling/payments coverage; new recovery/refund paths 100%. Browser checks
  verified recovery, pending/refunded distinction, cancellation guard, restart persistence and
  desktop/mobile layouts. Self-review added excess refunds without losing confirmed capacity.
  No live enablement, main-local/production migration, commit or push. Paid-pilot gate remains open.

- October 1: user accepted M5b locally and requested M5c. Keep real Stripe verification deferred;
  implement shared recovery/refunds and owner controls. No customer refund policy is invented:
  staff approve refunds after cancellation, or return only excess payment on a confirmed booking.
  Unknown outcomes retain allocations and capacity
  until verified. Production paid-pilot gates remain open.

- October 1: M5b local verification complete: 2,292 regression tests passed; 99.90%
  scheduling/shared-payments coverage. Customer deposit/balance/expiry, staff history and
  desktop/mobile/restart checks passed. Ready for user testing, not accepted or deployed.
  User confirmed merchant-owned Stripe accounts and explicitly deferred external sandbox
  verification. Local simulation is isolated; existing application-fee behavior is preserved
  with its known hardening work still tracked separately. M5c remains planned.
- October 1: user accepted M5a and requested M5b. Begin shared Stripe transport/account isolation,
  checkout attempts and event verification, with local network-blocked tests first. Merchant
  ownership and sandbox authorization questions are visible above. Do not change legacy
  application-fee policy as an incidental part of the booking feature.

- October 1: M5a engineering verification complete; ready for user testing. 281 focused tests,
  2,237 full regression tests, 99.88% scheduling/shared-ledger coverage. Browser checks covered
  room conflict rejection, deposit/balance/cancel/refund, role restrictions, saved data after
  restart and 1280/390/320px layouts. Only isolated Workshop Demo enabled; no real money moved.
  Expand M5a for test steps and credentials. M5b/M5c are not implemented; merchant/sandbox and
  legacy application-fee hardening gates remain explicit. No commit, push or deployment.

- October 1: user accepted M3 and authorized moving to M5 while deferring M4. Jasmine remains
  the commercial priority; no promise of Wix compatibility is made. Split M5 into reviewable
  M5a/M5b/M5c without changing its total effort range. Start with generic shared orders and an
  offline-only reservation workflow; do not use the legacy application-fee confirmation path.
  No real payment/provider calls or existing-school enablement are authorized by local tests.

- October 1: M3 implemented and engineering verification complete. Party intake defaults off;
  internal room/price proposals remain unreserved/unpaid. 221 scheduling tests and 2,176 full
  regression tests passed with scheduling Python coverage at 100%. Browser checks verified
  mobile intake, invalid quotes, overlap warnings, stale-edit recovery, read-only access,
  saved data after restart and unchanged SBMC/Kid Works navigation. Expand M3 for local manual
  steps and credentials. No current blocker; M4 not started. No push/deployment performed.

- October 1: user authorized M3 after M2 verification. M2 accepted; M3 active. Reuse tenant,
  roles, offerings, rooms and audit services; add only an opt-in party inquiry/review workflow.
  No shared enrollment/payment edits and no confirmed bookings. Unanswered decisions must
  remain visible on this status page. Run full regression and local browser QA before handoff.

- October 1: M2 engineering verification complete; ready for user testing. No current blocker.
  Added expandable manual steps for M1a/M1b/M2 with local-only credentials and a visible
  unanswered-questions section. No M3 work or git push; wait for the user's M2 acceptance.

- October 1: incremental, reusable standalone/integrated module requested; parties/camps moved
  earlier; Wix and Notion independently optional; setup and user acceptance gates made explicit.
- October 1: tracker created. No coded milestone accepted yet. Existing production flows untouched.
- October 1: one-repo/separate-module boundary reaffirmed. M1 split into access/workspace and
  saved setup. Every push requires unit, integration, full regression and agent-performed manual
  testing with recorded evidence; no automatic waiver when a check is blocked.
- October 1: difficulty/effort and major completion checkpoints added for all milestones.
  M1a implemented and verified on isolated PostgreSQL data; ready for user review. No information
  from Emily needed for access foundation. M1b is not started; M1a is not yet Accepted.
- October 1: local, read-only project browser added on port 8003. It reads the canonical
  documents on refresh and does not alter application routes, data or milestone acceptance.
  Viewer checks: 34 tests and 99.04% combined line/branch coverage; full suite 2,010 passed
  in 41.42 seconds with external network calls blocked. Desktop/mobile navigation, milestone
  expansion and document links checked. No push or production deployment.
- October 1: user instructed proceeding to M1b; M1a recorded as accepted. M1b is now active.
  Scope: owner-managed locations/rooms/workshops, owner/scheduler-managed dated sessions,
  validated capacity/time/location relationships, draft/publish and read-only public listings.
  No reservation, payment, provider integration or changes to existing enrollment workflows.
- October 1: M1b implemented and ready for user testing. Added location/room/workshop/session
  persistence, capacity and time validation, owner/scheduler/viewer permissions, explicit
  publishing and a read-only public schedule. Reviewed additive migration applied only to the
  isolated preview database. Browser tests covered owner setup, rejected capacity/overlap,
  public draft filtering, viewer denial, reload/restart and 1280/390/320px layouts. Existing
  SBMC and Kid Works enrollment navigation smoke-tested. No reservation/payment claims.
- October 1: at the user's explicit request, reset all 31 application-user passwords across
  the two verified loopback local databases. No remote passwords, authentication policy or
  production hashers changed. Login instructions supplied separately; do not deploy local
  fixture credentials. M2 starts only after M1b acceptance.
- October 1: user authorized M2. M1b recorded as accepted; expandable manual checks and local
  credentials added to the browser tracker. Open questions/blockers must also be recorded there.
  M2 stays an unpaid request workflow, with generic per-attendee options and no live integrations.
