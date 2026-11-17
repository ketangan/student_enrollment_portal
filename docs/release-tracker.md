# Release Tracker

Last hosted revision/configuration check: November 17, 2026 (PST).

## November 17 Booking Control Release

- Session and recurring-session live previews show Notify customers only when
  changes and affected customers exist. The shared hidden-state stylesheet also
  covers calendar dialogs; asset versions updated to avoid stale browser code.
- Reservation editing limits the seat stepper to the selected session's capacity
  available to this reservation (existing seats plus remaining open seats), not
  the maximum across other sessions. Server capacity validation remains in place.
- Studio Prep view and inline edit omit age. Existing workshop age choices and
  party ages remain stored and are preserved through hidden form fields.
- Local Chrome checks: selected-session capacity cap; notification hidden before
  changes and after reverting, visible after a duration change with a customer;
  Studio Prep participant view/edit. Session editor checked at desktop,
  768x1024, 1024x768 and 390x844. No changes saved to customer sessions during
  browser checks. Physical Android and printing not tested in this pass.
- Full regression requested after targeted checks, with durable logs/revision/
  fingerprint under `/private/tmp/pontora-regression-20261117-booking-controls/`.
  Completed successfully at 19:00 UTC: 6,307 application tests (3 skipped), 26
  migration tests, all JavaScript tests and coverage gates; Python coverage
  97.91%. Source fingerprint matched before release:
  `588dc1c121f9041f8ef585e48359866ad63a089f5f659a989986cae1e95534ff`.
  Only this release record was updated after the run. Demo deployment verification
  pending; production promotion is not authorized by this release.
- Targeted checks passed: 70 Python tests across Studio Prep, participant
  preparation and reservation list controls; 14 JavaScript tests across session
  preflight, reservation fields and session fields. `git diff --check` passed.

## November 17 Hosted Acceptance Follow-up

- Demo web and maintenance are both on
  `7c6291d5142afd74e9824604f28c886e0e8de84a`. GitHub CI `44042224617`
  passed all three jobs (12m54s); Render web deployment took 1m41s. Demo
  maintenance has successful scheduled runs, including 09:00 PST November 17.
  Production web AND maintenance remain pinned to
  `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c`. No production promotion.
- The preceding exact-source full regression passed: 6,306 application tests
  (3 skipped), 26 migration tests, 209 JavaScript tests, 97.90% combined Python
  coverage and all required gates. This follow-up changed no application code.
- New > Reservation opened the complete customer/session form, defaulted to a
  bookable workshop, saved, and returned to the list. Synthetic reservation
  `3103ec9e-0005-440d-84ea-1c14e81a6041` was edited from one to two seats:
  $130 subtotal + $14.30 tax = $144.30. The standalone edit route has an explicit
  review/Confirm changes step; it did not silently fail. Later cancellation
  succeeded with an optional note and released the reservation. No real charge.
- New York full-studio package B shows a preferred-time approval request,
  $1,295.61 proposed total and $0 due today. Public group/review/back navigation
  passed; final public waiver acceptance/submission was not performed. Staff
  request `9d686cfc-5b25-4c10-a775-beaf92044cb7` remained unbooked after review,
  then explicit staff approval created reservation
  `87796fde-a2d8-4119-a0a8-677f03f83272`.
- Recorded a synthetic $300 cash deposit on that demo party (no money moved).
  Increasing to 17 participants and adding a $10 pretax discount produced
  $1,245 subtotal + $110.49 tax = $1,355.49; balance $1,055.49. Downgrading to
  package A/eight participants preserved the existing discount and deposit:
  $585 + $51.92 = $636.92; balance $336.92. Original payment history remained
  $300. This was an offline-payment booking; online payment-request/refund
  acceptance is not established by this check.
- Synthetic recurring sessions 69/70 moved from 15:30 to 15:45; the linked
  reservation time changed too. Capacity below three booked seats showed a
  conflict and disabled Save. Editing only session 70 to 14:00 detached it;
  reopening displayed the standalone editor while session 69 stayed at 15:45.
  Moving session 70 onto session 69's occupied room/time was blocked with
  "This room already has a published session at that time." Discarded conflict.
- Fresh email delivery verified in the approved Ketan inbox: workshop
  confirmation `1a1ea795aa8603a7`, party confirmation `1a1ea7df4905251d`, and
  updated workshop time `1a1ea821dd951491`. No Jasmine/customer recipients used.
- Camps absent from hosted Setup and workshop choices; theme choices absent
  from the fresh Plushie user menu. Studio Prep displayed phone/email, persisted
  internal note and prepared selection. Hide prepared worked. Print view
  rendered the selected November 23-29 week, updated time, participant selection
  and internal note; physical printer output was not tested.
- Chrome desktop plus verified responsive viewports 768x1024, 1024x768 and
  390x844: public approval review and Studio Prep had no horizontal overflow.
  Prep switch interaction checked; viewport override reset. Do not treat this
  as a full every-screen tablet pass, physical Android or virtual-keyboard test.
  Screenshot evidence: `/private/tmp/pontora-demo-verification-20261117.png`.

### Remaining Before Blanket Completion Claim

- Two observed UI misses: all-location New Party workshop choices have identical
  names without location labels; recurring capacity-conflict rows show blank
  proposed values in the change summary. Save is correctly blocked, but the
  preview must not render an empty time/capacity. These are not fixed in 7c6291d.
- Fresh hosted online party payment-request/refund exercise, customer calendar
  provider-link check and location-limited account verification remain. Prior
  automated/local evidence is not a substitute for these hosted checks.
- Physical terminal/small real charge and refund require the connected live
  account/device. Physical Android/printing remain separate acceptance checks.
- Wix data import, real variant photos, final email copy/triggers and production
  location/capacity/tax verification still require launch inputs. Do not email
  Jasmine that *everything* is complete or promote production based only on CI.


## November 16 Hosted Audit Corrections (Main/Demo Release)

This follow-up supersedes the configuration gaps in the audit below, not its
remaining acceptance checks. Do not send an "everything is complete" email yet.

### Applied and Verified on Demo

- Removed `camps` from Plushie's saved SchedulingProfile capabilities after
  confirming it has no camp reservations. Preserved the legacy Camp/offering
  records. Hosted Setup now has no Camps tab or camp workshop entry. This was a
  saved demo configuration miss, not a failed code deployment.
- Set only Plushie's `admin_theme_switching_enabled` flag to false, preserving
  the other flags and recording an audit entry. The earlier Ops-form attempt did
  not persist; a database read and fresh Chrome user menu now confirm it is off.
- With owner approval, extended the existing scheduling demo token for the same
  recipient (14), without replacing it or widening permissions. Persisted expiry
  is December 1, 2026, 22:44:47 UTC. Do not publish its bearer URL in this file.
- Added nine missing published party packages from the supplied package matrix,
  keeping the existing Pasadena A package unchanged: A/B at Pasadena, Charlotte,
  Dallas and Houston; reserved-table A and private-full-studio B in New York.
  New York full-studio B remains `review`, not direct checkout, as explicitly
  confirmed by the owner. Its public approval workflow requires the code below.
- Added 18 synthetic direct-package time slots (IDs 3-20), two per direct package,
  on December 1 and December 8 at 4 PM local time. Existing slots and reservations
  were not rewritten. Hosted New York A showed a selectable December 1 slot,
  $595 subtotal, $52.81 configured tax and $150 deposit.
- Demo room capacities were enlarged only as needed for the synthetic package
  examples (14 outside New York, 18 in New York). These are NOT verified physical
  studio capacities. Existing configured tax rates were reused, not validated as
  live tax advice. Both require production setup verification before activation.
- No production/SBMC configuration, existing booking prices, live payments or
  customer emails were changed by these setup operations. Package/slot changes
  have a demo audit entry. Two failed operator shell commands (an incorrect model
  field in a read query and shell quoting) emitted Sentry errors; both were
  corrected, neither changed data, and they were not cron/application failures.

### Code Verified for Main; Hosted Acceptance Pending

- Reservation entry choices now use one shared bookability helper: no camps,
  draft offerings, missing price/catalog, or disabled native reservations. This
  prevents New > Reservation from opening a session-creation form. Lesson-only
  schools return no workshop choices without a query or capability error.
- Added validated operator policy `allow_approval_packages` (default false).
  Plushie opts in; other schools retain their existing defaults. `packages_only`
  still prevents custom requests, but no longer has to forbid published packages
  that require staff approval. Package booking mode remains database-backed.
- Reused the existing party request/review/reservation services, staff inbox and
  public form. Approval requests create no reservation, payment order or charge;
  direct packages retain checkout. Retry, required preferred time, approval then
  reservation creation, and package-editor behavior are covered.
- Customer review labels and totals distinguish approval requests from immediate
  checkout. No payment-reminder feature or parallel booking implementation added.
- Final focused checks: 371 Python tests passed across package-only booking,
  admin parity, calendar, staff workshops, party packages/requests, location
  dashboards and reservations; 16 party JavaScript tests passed; diff whitespace
  checks passed. These are not a full-suite result.
- Local Chrome: New > Reservation opened customer fields and saved reservation
  `fda2d29b-6efd-42be-a906-63d8f4d5d220` (New entry verification), one seat at
  $65 plus $7.15 tax, returning to the list. Synthetic `.invalid` email; preview
  email provider is local-only. Approval package preferred-date picker, group
  details, review/back navigation and zero-pay-today display checked manually.
- Visual checks: desktop 1470x797, tablet 768x1024 and 1024x768, phone 390x844.
  This time viewport dimensions were verified; no horizontal overflow in the
  checked customer review layouts. Staff review-package controls were also
  inspected. Physical Android keyboard/touch/printing checks remain pending.
- Existing no-reload preview server restarted on the same port 8002/database so
  browser checks actually used changed Python/templates. No extra database/server.
- Full regression PASSED in 22m01s, with 6,306 application tests (3 skipped),
  26 migration tests and 209 JavaScript tests. Migration drift, combined Python
  coverage (97.90%), per-module gates and JavaScript coverage gates passed.
  Logs: `/private/tmp/pontora-regression-20261116-demo-audit-fixes/`.
- Before release, source matched tested base
  `264130b0d3ee4af263d9c16e74a5bdbb781e9008` and unchanged fingerprint
  `a8d1bcfad3ff4167bf9c4b3ab5b57ed4d3c8f514849ea4a32fc68927d37e1632`.
  Only this release-status documentation was then updated.
- Owner approved pushing this batch to main and deferring hosted verification
  until the next morning. GitHub CI, automatic demo deployment and the remaining
  hosted acceptance paths below must still be checked before emailing Jasmine
  that everything is ready. No production promotion is approved; production
  remains pinned. The unrelated agent worktree is excluded from this release.

## November 16 Release to Main/Demo

- Owner approved this accumulated calendar, session/recurring editor, reservation,
  party-form, Studio Prep, pricing-display and shared-control batch for main/demo.
  Production promotion is NOT approved. Historical "Local, Unpushed" headings
  below describe iteration checkpoints, superseded by this release entry.
- Full regression `pontora-regression-20261116-save-label-IIKXFW` PASSED:
  6,288 application tests (3 skipped), 26 migration tests and 207 JavaScript tests.
  Migration drift, 97.90% combined Python coverage, every-module coverage gates
  and JavaScript coverage gates passed. Duration 23m24s; source unchanged.
- Before release, current source matched tested base `fdb687e047fe7395f7efd0ee404e54fa9adb7db5`
  and working-tree fingerprint
  `db76a7b3e07543f8cb2fb809f66bcfa91da36602aa27a16ef4d1158c8b37c06d`.
  Only this release-status documentation was then updated. Whitespace checks pass.
- Includes additive nullable `Session.series_id` migration 0061. Existing standalone
  sessions remain standalone. Shared behavior uses capabilities/settings, not
  customer-name branches; existing school defaults remain unchanged.
- Hosted Plushie theme/camp configuration must be verified after deployment;
  the local scoped settings are not proof of hosted activation. No customer data
  or local database is included in the push. Unrelated agent worktree excluded.
- Released commit: `264130b0d3ee4af263d9c16e74a5bdbb781e9008`.
  GitHub CI run `44026604325` passed all three jobs, including migration and
  coverage gates (about 12 minutes). Render demo web is Live on this revision;
  automatic web deployment took 2m34s. Demo maintenance is on the same revision
  with successful scheduled runs, including the November 16 10:30 PM PST run.
- Hosted non-destructive Chrome smoke checks passed: Calendar New menu and
  session form; workshop defaults; reservation detail and editing; $65 pretax
  pricing and live tax totals; seat-limit control; selection preserved across
  week navigation; collapsed Workshop Prep details; Setup session editor,
  automatic highlighted change preview, and notification control below review.
  Edited values were discarded. No customer booking writes, emails or payments
  were performed, so this is not a fresh hosted end-to-end save/delivery test.
- Outstanding hosted activation: Plushie still exposes Camps in Setup/calendar
  and Clean/Midnight/Mint theme choices. Its published Creative plushie camp
  also remains in workshop selectors. Apply the approved school-scoped settings
  and review that demo offering without changing other schools or deleting data.
  Code deployment alone did not apply the local database configuration.
- Demo and production database-backed health endpoints returned status ok.
  Render production web AND maintenance remain at
  `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c`; no promotion was performed.
- Hosted visual inspection was desktop Chrome at 1470x797. The browser viewport
  override did not change the observed viewport and was reset; do not count this
  as a new hosted tablet/phone verification. Prior local responsive checks remain
  recorded below; hosted responsive and physical Android checks remain pending.
  Browser logs included asynchronous extension-message-channel errors; no
  corresponding failure was observed in the exercised application controls.

## November 16 Jasmine Email and Hosted Acceptance Audit

Status: NOT ready for an "all requested changes are complete" announcement.
This follow-up audit tested the deployed revision above; no application source,
production data, live payments or existing customer bookings were changed.

### Confirmed on Demo

- Calendar session > Add booking saved a synthetic customer reservation and
  returned to the calendar. The total was $65 plus $7.15 tax for one participant.
- Automatic confirmation reached ketan@mypontora.com, not merely the outbox:
  Gmail message `1a1e841557dbb0e8`. The customer selection link saved a participant
  name, age and Bear/Brown selection successfully.
- Studio Prep showed phone/email, saved an inline internal note, and persisted
  a participant's prepared state. Hide Prepared and weekly Print were present;
  Still to Pull was absent. Physical printing was not exercised.
- Setup created two published weekly sessions. Editing the series updated both
  future occurrences. Moving the synthetic reservation to the first occurrence
  saved successfully without changing its price or prepared selection.
- A subsequent recurring edit showed the affected booking and Notify customers,
  moved both sessions from 3:00 PM to 3:30 PM, and moved the reservation with them.
  The update email reached ketan@mypontora.com with the correct old/new times:
  Gmail message `1a1e8462f8f0c4fe`.
- Synthetic records retained for review: reservation
  `81279c7a-ff36-490b-bbe9-70cbac463354` (Demo verification Nov16), workshop 1
  sessions 69 (November 24) and 70 (December 1), 3:30-5:30 PM, capacity eight.
  No email was addressed to Jasmine or an existing customer during this audit.

### Blocking Demo Gaps

1. Reservations > New > Reservation defaulted to the published legacy Creative
   plushie camp offering and opened a New session form instead of customer
   reservation fields. Calendar > existing workshop > Add booking worked.
   Investigate the shared entry routing and permitted/default offering selection;
   do not treat hiding a menu item as proof that this path is fixed.
2. Hosted Parties had only Pasadena Package A ($495, six participants, $150
   deposit). No Package B or packages for Charlotte, Dallas, Houston or New York
   were configured, and the public party page offered no future party times.
   The supplied package matrix and suitable demo times must be configured before
   claiming party checkout, paid package changes and updated payment requests
   are fully demonstrated on the hosted environment.
3. Approved school-scoped camp/theme settings are still unapplied on demo, as
   recorded above. The legacy camp offering also requires review without data
   deletion. Keep changes scoped to this school, preserving SBMC.
4. The previously shared passwordless link expires November 17. Refresh access
   before sending the final review invitation; it was not renewed in this audit.

### Requirement Coverage and Remaining Verification

Compared incoming emails dated September 30, October 17, October 20, October 29,
November 2, November 11 and November 14 with outgoing commitments on October 28,
November 3 and November 10. November 14 is the controlling calendar/party scope.

| Requirement | Evidence / remaining work |
| --- | --- |
| Calendar individual bookings and empty sessions | Creation and reservation move/save passed on demo. Fresh hosted cancellation/refund and conflict-path acceptance checks remain. |
| Recurring updates, affected-booking review and emails | Creation, multi-occurrence time update, reservation movement and actual update-email delivery passed. One-occurrence detachment and collision blocking have automated/local evidence but were not repeated end-to-end on demo in this audit. |
| Paid party package/count/price edits and updated payment request | Released implementation exists; hosted package/time setup blocks a representative acceptance exercise. Do not mark hosted acceptance complete. |
| Google/Outlook/Apple calendar choices | Shared provider-choice implementation exists; a fresh hosted confirmation-page provider-link check remains. |
| Individual animal/color photos | Catalog variant images are supported. Actual replacement photos remain Jasmine's input; no new images were supplied in this audit. |
| Confirmation email and customer selection link | Fresh hosted booking delivered and its selection link saved successfully. |
| Studio Prep notes, contact details, Hide Prepared, no Still to Pull | Hosted checks above passed; selected-week printing and physical tablet behavior need their own acceptance checks. |
| Upcoming Wix bookings imported | Not completed. Obtain/review export, map payments/balances and reconcile counts before import/cutover. Do not import card credentials. |
| Original party rules | Recheck the configured A/B prices, location-specific capacities, deposits, two extra guests, age/observer rules, estimated guest range and NY private-studio inquiry/approval flow after completing party setup. |
| Design guide, later guest selections and seven-day reminders | Guide is present (October 22 upload); reminder/selection services exist. Final guide/templates and production reminder activation/verification remain. Automatic copied-booking reminders are deliberately excluded in demo. |
| Per-location staff access and tablet support | Automated/local evidence exists; this hosted audit used an all-access admin on desktop Chrome, not a location-limited login or physical Android tablet. |

Not every earlier idea is an outstanding launch requirement: the accepted Studio
Prep workflow superseded the proposed Notion integration; credits, gift cards,
promo codes and expanded reporting were explicitly deferred in the October 28
email. Square terminal/refund code has test-environment evidence, but physical
terminal acceptance needs the connected live account/device and an agreed small
real charge/refund. Do not equate sandbox coverage with physical-device testing.

### Outstanding Inputs From Jasmine

- Upcoming Wix bookings export, including customer/participant details, selections,
  deposits, payments and outstanding balances; arrange secure transfer.
- Labeled animal/color photos and the latest design-guide PDF if revised.
- Email templates and the trigger/timing for each. As previously agreed, final
  template copy need not block the other launch preparation.
- Send customer emails from Pontora or her own domain/provider; confirm desired
  sender address. Never request email or Square passwords by email.
- Final review feedback and launch date, avoiding Thanksgiving week; connect
  Square through Settings and arrange the physical-terminal charge/refund check.

Already answered: Wix is the current platform, Wix access has been supplied,
Square uses one account across locations with standalone Terminals, and Jasmine
reported the customer flow worked on her tablet. Do not ask these again.

This audit changed documentation and synthetic demo records only. Existing CI
and regression results still apply to the unchanged application source; they do
not erase the hosted configuration/entry-path gaps above.

## November 16 Regression Corrections (Local, Unpushed)

- Resolved all seven failures from the setup-spacing regression: updated obsolete
  preparation disclosure/summary assertions and incomplete shared-template context;
  recurring tests now exercise live preflight and disabled-save conflict responses.
- Disabled participant controls now describe readiness rather than advertise a
  preparation action. Added viewer/completed-state checks for disabled controls
  and rejected direct writes. Stale series reviews still cannot write or email.
- All 133 tests in the seven affected modules passed. No scheduling/payment
  service changes. Fresh full regression logs and revision/fingerprint status:
  `/private/tmp/pontora-regression-20261116-corrections/`. Full result pending.

## November 16 Setup Spacing (Local, Unpushed)

- Removed the general Parties instruction banner and reduced header spacing;
  retained package-specific publication warnings.
- Workshop add/edit surfaces now match the 736px session editor width.
- Recurring-session Starts and Duration use equal-width grid columns.
- Verification: 87 targeted party-package and recurring-editor tests passed.
  Chrome desktop Parties and workshop checks completed; workshop responsive
  widths checked at 768x1024, 1024x768 and 390x844 with no page overflow.
  Recurring field sizing was reviewed in CSS; available local sample sessions
  were standalone, so that specific visual check remains pending. Physical
  Android verification and a full-suite check of this working tree are pending.

## November 16 Compact Prep and Reservation Entry (Local, Unpushed)

- Replaced calendar-cancel icons with trash icons in calendar session/customer
  actions and lesson cancellation controls. Accessible cancellation labels and
  confirmation/refund behavior are unchanged; these are not hard-delete actions.
- Studio Prep contact headers are compact, with staff alongside contact details
  when space permits. Party booking and party-package setup forms have tighter
  spacing while preserving touch targets and responsive field grouping.
- Workshop Prep now contains a collapsed reservation-details and participants
  disclosure instead of the aggregate selections summary. Both tabs share one
  live editor; moving it between tabs avoids duplicate form IDs and preserves
  unsaved fields. Inline saves refresh the preparation content without losing it.
- Calendar and Reservations New > Reservation load the first permitted workshop
  immediately. The top workshop selector reloads the full form for another
  workshop using the existing protected endpoints and location scope.
- Verification: 261 targeted backend tests and 45 JavaScript tests passed;
  `git diff --check` passed. Includes the corrected reservation-list test that
  failed in the prior background regression. No new full suite or push.
- Manual Chrome checks: both reservation entry points, changing workshop/session
  lists, single-copy tab switching, participant inline Save from Prep, compact
  party/package forms, staff editing and trash icons. Desktop plus 768x1024,
  1024x768 and 390x844 responsive checks; physical Android/virtual-keyboard
  verification remains pending.

## November 16 Recurring Session Editor (Local, Unpushed)

- Calendar and Setup reuse one recurring-session editor and the existing atomic
  session-change service. Scope is one occurrence (detaches its series ID) or
  this and following future occurrences with the same explicit series ID.
  Past, canceled, detached and unrelated sessions are excluded.
- Start time, duration and capacity refresh the affected-session list before Save.
  All detected conflicts are listed, with a count; Save is disabled and the server
  rejects the whole batch. No inline conflict resolution or message-preview UI.
  Signed reviews, stale-booking checks, authorization and retry protection remain.
- Existing reservation-update emails and payment evidence are retained. The
  notification toggle defaults on. Notifications still use the existing
  per-reservation outbox, not a new combined multi-date email format.
- Verification: 100 targeted backend tests across recurring editor, workshop
  schedules, session changes and calendar actions; 16 targeted JavaScript tests
  passed. `git diff --check` passed. No full regression or push in this iteration.
- Chrome manually checked Setup and Calendar, live scope switching, conflict
  blocking/recovery and notification-toggle revalidation. Desktop, 768x1024,
  1024x768 and 390x844 responsive layouts inspected; physical Android testing
  remains pending. Temporary local conflict fixture removed; no existing
  customer reservations changed during browser verification.
- Manual check: open a recurring session from Calendar > Edit or Setup >
  Workshop > session edit. Select one/following; change time, duration or seats.
  Verify the impact list updates without saving. Reduce seats below bookings or
  overlap an occupied room: Save must stay disabled. Resolve separately, reopen,
  and save. For one occurrence, reopen it and confirm it is now standalone.

## November 16 Calendar Editors (Local, Unpushed)

- Empty-calendar-slot menu now offers Session only; toolbar New retains Session
  and Reservation. Existing session Add booking remains unchanged. New Session
  and workshop Setup forms have a shared white editor surface. Desktop Chrome
  inspected all three changes; 22 calendar JavaScript and 91 setup/schedule tests
  passed. Physical tablet checks remain pending.
- Full regression requested after this batch, detached with bounded caffeinate,
  revision/fingerprint, individual logs and exit status under
  `/private/tmp/pontora-regression-20261116-final/`. Status is pending until
  explicitly checked. Previous calendar-actions-final run exited -15 (interrupted),
  not a passing release check. No push authorized by this iteration.
- Reservation session choices now disable any destination with fewer available
  seats than the entered group size, including changes to quantity and week.
  Uses the existing capacity data excluding this reservation's current claim;
  server-side capacity/concurrency checks are unchanged. Shared by Add/Edit
  Reservation in calendar and standalone screens. 32 booking-edit backend tests
  and 3 picker JavaScript tests passed. Chrome verified 3 seats disables the
  2-open session, reducing to 2 enables it, and restoring 3 disables it again.
  No booking saved during verification. No full suite or push.
- Live session impact review: editing date/time, duration or capacity now refreshes
  the affected-booking summary through the existing read-only preflight. Its signed
  review token permits one explicit Save; the server still checks the booking
  revision, conflicts and exact submitted values. No automatic writes or emails.
  Removed the "Not saved yet" message. Calendar and standalone session editors
  reuse the same summary partial and preflight script; cancellation is unchanged.
  42 targeted session backend tests and 12 JavaScript tests passed, including
  preview-to-save, retries, altered values, stale responses and conflict handling.
  Desktop Chrome verified duration 100 -> 110 updates 11:55 AM -> 12:05 PM live,
  with customer emails and no Save click. Canceled browser check without changing
  the booking. This revision's tablet/phone checks remain pending; no push.
- Session review: replaced the shaded contact table with the shared compact
  customer list (name, email, seats); phone numbers removed from this form.
  Availability checks now ignore identical input/change events so blur cannot
  restart validation during a Save click. Review explicitly says not saved yet.
  Expired login/permission responses are distinguished from uncertain saves.
  41 session-change backend tests and 11 editor/preflight JavaScript tests passed.
  Desktop Chrome review and successful unchanged-details save verified locally;
  no customer times or payments changed. Intermittent uncertain-save report was
  not reproduced; do not claim that issue fully resolved. Responsive/physical
  device checks for this specific revision remain pending. No push/full suite.
- Pricing presentation: customer workshop selection and setup prices explicitly
  say plus tax; shared staff Add/Edit Reservation summaries show per-person base
  price, subtotal, tax, adjustments/net payments when applicable, and actual balance.
  Calendar dialogs and standalone forms use the same summary. Reservation detail
  and customer confirmation show saved subtotal/tax; existing statements and
  checkout already separate them. Legacy quotes without reliable tax evidence
  retain their recorded totals, never today's tax. No price or ledger writes.
  Chrome desktop, phone 390x844, tablet 768x1024/1024x768 checked; seat controls
  update the quote and re-enable Save. Physical Android/keyboard testing pending.
  Targeted staff-workshop, booking-edit, booking-document, deposit and customer
  experience tests passed, plus 26 JavaScript tests. Explicit regressions cover
  saved tax after default-price changes, partial payments, and legacy quotes.
  Full release regression is not claimed for this unpushed iteration.
- Calendar creation menu now offers Session and Reservation only, for both New
  and empty-slot clicks. Removed the unused party-creation configuration query;
  existing party calendar events and creation from Reservations are unchanged.
  Checked Jasmine's November 11/14 emails: party changes were requested within
  reservation details, not explicitly party creation from the calendar.
- Reservation Save timing fix: ignore the duplicate change event fired when an
  already-quoted seat input loses focus. It previously restarted pricing and
  disabled Save during the click; changed quantities and failed-quote retries
  still calculate normally. Added regression assertion for input/quote/blur.
  Phone and seats now share the customer grid row (stacked on phones).
  Browser verified a four-seat save, persisted roster entry and $288.60 amount;
  canceled both temporary verification bookings afterward to restore capacity.
  Checked desktop and 390x844/768x1024/1024x768 Chrome emulation. Physical-device
  checks pending. Targeted reservation backend and 14 JavaScript tests run; no
  full suite or push. Proof: `reservation-phone-seats.jpg` in artifacts.

- Staff reservation availability no longer depends on public request intake or
  choose-designs-at-studio settings. Those settings remain unchanged; staff use
  the existing reserve-now/complete-participants workflow. Publication, native
  reservation capability, catalog, price, capacity and permissions remain gated.
  Reproduced Cozy Creations' missing actions with both public settings disabled;
  verified both Add booking buttons return and intake loads four available seats,
  the $72.15 tax-inclusive quote, and an enabled Save action. No local booking
  saved. 52 targeted staff-booking/calendar tests passed, including new coverage
  for all disabled-public-setting combinations and existing capacity concurrency,
  retry, role and past-session protections. No full suite or push.
  Proof: `cozy-booking-access.jpg` in the verification artifact folder.

- New session follow-up: date/time, Duration and Seats share the Edit session's
  three-column proportions; mobile wraps date/time above the numeric inputs.
  Calendar and Setup use the same field order and compact native Seats input.
  Publish and Save as draft now include the requested explanatory text in an
  accessible radio group. 21 targeted calendar tests and four session-control
  JavaScript tests passed. Checked Setup desktop and Calendar desktop, 390x844,
  768x1024 and 1024x768 Chrome emulation; changed seats and selected draft without
  saving test data. Physical Android checks pending. Not pushed; no full suite.
  Proof: `new-session-three-fields.jpg` in the verification artifact folder.

- New session now shares the combined date/time picker with Edit session, with
  Duration on the same row in both Calendar and Setup. Removed conflicting
  calendar-only grid rules. Recurrence still uses the existing schedule writer;
  submitted wall times are interpreted in the workshop location's timezone.
  Previously open split-date forms remain accepted. Audited other split controls:
  recurring date ranges, teacher availability, lesson series/rescheduling and
  make-up dates remain unchanged; party forms already use combined date/time.
- Reservation participant edits now explicitly say when a price review has not
  been saved and label its final action Confirm changes. The session picker opens
  on the currently selected booking's week instead of concealing its selection.
  Verified review/confirm/retry behavior for standalone and calendar requests;
  the user's local reservation was not changed during inspection.
- Verification: 81 targeted backend tests and 22 targeted JavaScript tests passed;
  diff whitespace check clean. Manually checked shared creation controls from
  Setup and Calendar, picker open/close and recurrence controls in Chrome desktop,
  390x844 phone, 768x1024 and 1024x768 tablet emulation. Physical Android/keyboard
  checks remain pending. No full-suite run, push or deployment for this iteration.
  Proof: `new-session-compact.jpg` in the verification artifact folder.

- Picker sizing correction: removed the session-only in-flow expansion. Dialog
  pickers reuse the standard 320px dropdown, attached inside the modal top layer
  with viewport-bounded positioning. Editor field sizing no longer stretches the
  picker inputs. 17 targeted picker/editor JavaScript tests passed; visually checked
  desktop, phone and tablet portrait, including the reachable Close control.
  No full regression or push. Proof: `session-standard-picker.jpg`.

- Compact single-row session controls supersede the split Date/Starts layout:
  reuse the existing date/time picker with small Duration and Capacity number
  inputs. Calendar and Setup share the three-column row; phones place the date/time
  above the two numeric fields. Removed the redundant split-date implementation.
  Existing picker now supports idempotent mounting for loaded session forms; its
  popup stays in the dialog flow so time/footer controls remain reachable.
  Verified date/time changes trigger availability checks before Save is enabled.
  41 targeted session tests and 22 related JavaScript tests passed; desktop,
  768x1024/1024x768 and 390x844 Chrome emulation checked. Physical-device checks
  pending. No full regression, data changes, push or deployment.
  Local proof: `session-single-row.jpg` in the verification artifact folder.

- Session edit alignment/customer follow-up: shared field grid now aligns Starts
  and Duration at matching 44px heights with tighter spacing. Customer rows show
  name, email, optional phone and a right-aligned seat count. Uses the existing
  authorized, preloaded customer records; no extra queries or notification changes.
  Calendar-specific duplicate layout rules removed; Setup shares the same layout.
  Targeted verification: 41 session-change tests and four field/preflight JavaScript
  tests passed. Customer rendering tests cover both standalone and dialog requests,
  including escaping. Chrome desktop, tablet 768x1024/1024x768 and phone 390x844
  visually checked; physical Android remains pending. No full regression or push.
  Local proof: `session-editor-contacts.jpg` in the verification artifact folder.

- Compact typography follow-up: shared booking-admin/editor text is 13px, calendar
  headings and metrics reduced, and reservation-row View/Edit/Cancel are icon-only
  with accessible names/tooltips and 44x44 targets. Reservations list View uses the
  same icon sizing. Cancellation remains cancellation, not permanent deletion.
  Confirmed the workshop default is $65 BEFORE tax: the configured 11% produces
  $72.15 per seat and $216.45 for three. No prices or existing bookings changed.
  Setup and Reservations reuse the shared editors. Manual Setup verification caught
  and fixed an existing Flatpickr alternate field left behind by the split date/time
  editor; its picker is now destroyed before replacing the control.
  Targeted checks: 181 backend tests and 46 JavaScript tests passed; focused coverage
  is 97.14% for views_booking_edits and 94.12% for views_session_changes. These address
  the preceding full run's two module-floor failures (application tests and overall
  coverage had passed). No full run started for this iteration per owner instruction;
  the final batch still requires full-suite verification before release.
  Chrome desktop, 768x1024 and 1024x768 tablet emulation, and 390x844 phone reviewed;
  row actions and reservation edit exercised, standalone Setup/Reservations editors
  checked. Physical Android, virtual keyboard, and printing remain unverified.
  Screenshot: `compact-booking-actions.jpg` in the local verification artifact folder.
  No push or deployment.

- Follow-up correction supersedes the overflow menus below: no three-dot menus in
  the session popup. Print is a direct header icon; reservation View/Edit/Cancel
  are visible buttons and session cancellation is a direct footer action.
  The owner confirmed past sessions must remain closed to new reservations.
  Their booking controls now remain visible, disabled, with a started/canceled
  explanation. Noneditable reservations retain disabled Edit and the domain reason.
  Verified the exact November 14 session (four Edit controls, zero menus), plus
  active Edit on an upcoming booking. Chrome desktop, phone 390x844, tablets
  768x1024 and 1024x768 checked; physical Android remains unverified.
  64 backend and 22 calendar JavaScript tests passed. Prior full regression stopped
  deliberately with -15 before editing; replacement full run is pending under
  `calendar-direct-actions-20261116/status.json`. No push or deployment.
- Latest owner-requested layout restores the wide blue Add booking beside Edit,
  and the dashed available-seats action beneath the roster. Both reuse the same
  reservation editor; full-session and permission guards remain. Print is now a
  direct header icon; the session menu retains cancellation only. Payment reminders
  remain excluded. Chrome checks: desktop, 390x844, 768x1024, 1024x768; both booking
  controls exercised. 29 targeted JavaScript tests passed. Actual-device printing
  and Android remain unverified. Prior full run deliberately stopped with -15
  before these edits; replacement: `calendar-actions-final-20261116/status.json`.
- New menu separates Session, Reservation and Party. Empty timed-cell selection
  uses the same choices and temporarily highlights the selected slot. This
  supersedes the earlier restriction on calendar recurring-session creation.
- Setup and Calendar use the same single/repeating session editor. Setup locks
  the workshop; Calendar permits selecting an authorized workshop. The existing
  schedule writer remains authoritative. New session creation uses a signed,
  actor/workshop-bound retry token for one-step creation; conflict checks and
  transaction locks remain in the shared writer.
- Single-session edits have date/time fields and default-on customer email
  notification. Read-only availability checks disable Save until resolved. The
  transactional save repeats validation. Same-workshop overlaps with an unassigned
  room are also rejected; distinct explicit rooms remain independent.
- Reservation intake/edit uses weekly session choices and server-calculated
  balances. No card collection, customer search or SMS consent was added.
  Existing payment evidence, confirmation delivery and booking writers are reused.
- Compact session roster has per-customer action menus and the available-seat
  reservation action below the roster. Party creation reuses the existing form.
- Focused verification: the final repeat passed all 143 Python tests after title
  and date-input hardening. 33 JavaScript tests passed, including
  availability failures/stale responses and unresolved balance blocking.
- Chrome manual checks: desktop, 768x1024 portrait, 1024x768 landscape and
  390x844 phone. Verified creation choices, party entry, roster, conflict error,
  disabled Save and re-enabling after correction. No live email/payment was sent.
  Physical Android and virtual-keyboard behavior remain unverified.
- The first `calendar-editors-20261116` run failed five application tests. Updated
  shared-form expectations and the Setup return URL; the affected regression
  files now pass. That earlier run does not verify the current source.
- Follow-up browser review reproduced and fixed the empty-cell popover being
  dismissed by the opening click and action-menu labels wrapping vertically.
  Added seat steppers and notification switch; moved print/cancel into session
  actions and payment-review information into Payments instead of a third card.
  Workshop payment reminders are explicitly excluded by the owner. Existing party
  payment requests are unchanged.
- Chrome follow-up: desktop 1470x741, portrait 768x1024, landscape 1024x768,
  phone 390x844. Checked open action menus, seat/balance recalculation, conflicting
  session blocking and correction, repeat fields, and one-click local draft
  creation returning to the calendar. A phone footer gap was found and corrected.
  Synthetic draft added to local workshop 32 on January 25, 2027; no hosted data,
  real payment or external email was used. Physical Android/keyboard is pending.
- Fresh full regression is tracked in `calendar-mock-review-20261116/status.json`
  with revision/fingerprint and sequential database stages. It remains pending
  until checked. No push or production promotion. Recurring-edit redesign remains
  intentionally outside this batch, awaiting the next design.

## November 15 Workshop Schedule Creation (Local, Unpushed)

- Supersedes the calendar scope UI described in the two entries below. Add workshop
  now includes optional One session / Repeating sessions scheduling. The first date
  determines the weekday; repeat every N weeks (1-26) through an explicit end date,
  within the existing 180-day creation window. No daily/monthly/perpetual scheduler.
- Workshop and initial sessions save atomically using the existing recurrence and
  availability validation. Existing workshop publication remains a deliberate step.
- Migration 0061 adds a nullable series UUID to sessions. No existing sessions are
  automatically grouped. Recurring creation in Setup assigns one UUID to its batch.
- Calendar edit/cancel reuses This session / This and following sessions for linked
  sessions only. No date/day/current-time filter reconstruction and no recurrence
  creation link in calendar intake. Booking creation and single cancellation remain.
- Signed review binds the seed and series identity; stale membership/booking/payment
  evidence cannot partially apply changes. Existing booking, refund and notification
  services remain authoritative. Cancel retries are safe after the seed is canceled.
- 144 targeted Python tests passed, including additive migration preservation,
  invalid parent forms, atomic conflicts, permission checks, refunds, unchanged
  prices and automatic rescheduling outbox messages. 14 admin JavaScript tests passed.
- Chrome manual checks: desktop 1470x797, phone 390x844, tablet 768x1024 and 1024x768.
  Created local draft workshop 32, sessions Jan 4/11/18 2027 at 10 AM; moved only
  Jan 11/18 to 11 AM from the calendar review. Verified return to calendar, first
  session unchanged, mode-dependent fields, 44px controls and no sideways overflow.
  Physical Android, virtual keyboard and real inbox delivery remain unverified.
- Previous shared-selects run passed application tests; migration stage intentionally
  stopped with exit -15 before implementation. Replacement full suite is pending in
  artifact `workshop-series-20261115/status.json`. No push or production promotion.

## November 15 Shared Form Alignment (Local, Unpushed)

- One shared select stylesheet loaded by dashboard, booking, customer enrollment,
  public experience and Django admin base styles; embedded lead forms and the
  standalone demo also include it. Native single-select arrows are inset 12px,
  with 36px text clearance. Multi-select/listbox behavior remains native, with a
  forced-colors fallback. Enhanced admin Select2 arrow spacing is also normalized.
- Fixed workshop checkbox height/alignment and full-width label layout. Asset
  versions refreshed to prevent old cached styles masking these changes.
- Add recurring sessions is on the saved workshop detail page next to Add session;
  the existing successful Add workshop redirect already leads there.
- 51 focused style-loading/calendar tests passed. Chrome manual checks covered
  Add workshop controls at desktop, phone 390x844 and tablets 768x1024/1024x768,
  including dropdown selection, checkbox label and the recurring Setup entry.
  Not every application page or enhanced admin control was manually inspected.
- Shared styling affects SBMC/enrollment visually; no workflow/data changes.
  Full combined regression pending in `shared-selects-20261115/status.json`.

## November 15 Calendar Scope Cleanup (Local, Unpushed)

- Recurring creation now starts in Setup > workshop > Add recurring sessions,
  reusing the existing form and service. Calendar intake no longer links to it.
- Edit and cancellation dialogs reuse the existing segmented-control styling for
  This session / Multiple sessions. Switching scope retains the selected session
  and action; cancellation never links to editing or recurring creation.
- No change to recurrence frequencies, selection rules, signed review, pricing,
  refunds, notifications or school permissions. Setup entry is capability-gated;
  existing enrollment/SBMC configurations retain their defaults.
- Focused calendar/session/bulk tests: 81 passed. Chrome checked at desktop
  1470x797, tablet 768x1024 and 1024x768, phone 390x844; scope targets are 44px.
  No customer data changed during UI checks. Physical Android remains unverified.
- Full regression in `calendar-scope-20261115/status.json` was intentionally
  stopped for the follow-up shared-style request (exit -15, no reported assertion
  failures). The combined replacement run is recorded above.
  Do not attribute the preceding release's passing full run to this new change.
- Prior release fdb687e passed GitHub CI 43686606628 and deployed successfully to
  demo web and maintenance. Production was not promoted.

## November 14 Recurring Schedule Batch (Demo Release)

- Re-read Jasmine's November 11 and 14 emails and the October 28 meeting recap.
  Requirements audit is saved in visualization artifact `jasmine-requirements-audit-20261114.json`.
  The agreed behavior is reviewed whole-session/bulk moves with automatic update
  emails; cancellation communication is staff-controlled, with a personal message.
- Repeat weekly or every two weeks within a finite 180-day date window. New sessions
  publish automatically. Existing room, lesson, private-party and capacity checks
  are reused. No perpetual recurrence worker or competing reservation writer.
- Change/cancel matching future sessions by workshop/location, day, current start
  time and date window. Preview lists each session, affected bookings and phones.
  A moved individual exception at another time is not silently included. Duration,
  room, agreed prices and payment evidence are preserved by bulk time changes.
- Reuses the single-session writer, signed reviews, profile/school/order locks and
  outbox. Conflicts, stale booking/payment evidence, pending activity and retries
  cannot partially apply a batch. Cancellations offer both refund choices; refunds
  remain approved requests until the existing provider/recovery path confirms them.
- Cancellation emails now require staff to choose Email customers. Optional personal
  message is separate from the internal note. Move notices remain automatic when
  delivery is configured/eligible; no test-recipient allowlist was introduced.
- Party confirmations now reuse workshop Google/Outlook/ICS controls and export code.
  Exports use current reservation times and exclude private links, contacts and notes.
- Catalog photo policy now independently controls variant photos and tint fallback.
  Plushie's config allows an exact uploaded color photo, otherwise the default image,
  without synthetic tinting. Other schools retain their existing defaults. Actual
  replacement photos remain an input from Jasmine; none were invented or uploaded.
- Self-review/manual fixes: empty-session editors also expose recurring/bulk links;
  repeat-from-session starts the following week; contextual cancellation retains the
  selected session; radio/checkbox labels reuse existing aligned controls; dialog
  focus no longer scrolls the heading out of view on initial load.
- Focused verification: 127 backend tests and 53 JS tests passed; all 25 recurring
  schedule tests then passed after the final date-specific conflict messages/retry
  guard (these runs overlap). New shared module combined statement/branch coverage
  before that final guard was 95.06%; full-batch coverage remains pending below.
- Chrome manual verification: desktop 1470x797, tablet 768x1024 and 1024x768,
  phone 390x844. Created two local weekly sessions, added a synthetic customer,
  moved both together, verified calendar stayed open, unchanged $144.30 balance and
  revised-time outbox content. Cancellation preview/Cancel preserved the bookings.
  No sideways dialog overflow; phone buttons and checkbox/radio labels are 44px.
  Party calendar menu checked on landscape tablet and phone (44px links).
  Physical Android/virtual keyboard, real inbox delivery and live Square remain
  separate checks; browser emulation is not actual-device evidence.
- Local review data: Pasadena, Nov 30 and Dec 7 at 11 AM, eight places each.
  Nov 30 contains "Recurring schedule review", two participants, synthetic phone,
  example.invalid email. These are local-only and no customer email was sent.
- Full-batch regression passed November 14 at 10:40 PM Pacific: 6,221 application
  tests (three skipped), 26 migration tests, 185 JavaScript tests, migration drift,
  97.95% combined statement/branch coverage and every measured module above 90%.
  Artifact `bulk-recurring-20261114/status.json` records unchanged fingerprint
  `389cfe67f7c13ce688882d32875e0f8fcd7c08c48f7c88e7b574abf037134214`.
  Only release documentation changed after that verified snapshot. User approved
  the main/demo release; GitHub CI and automatic demo web/maintenance deployment
  verification follow the push. Production approval/promotion remains separate.
- Remaining launch inputs/checks: Jasmine's optional replacement photos/templates,
  Wix placement/theme review and approved upcoming-booking import, launch decisions,
  real Square connection/small-charge verification. Hosted demo inbox/link checks
  and new admin-flow acceptance follow deployment; do not claim them completed locally.

## November 14 Whole-Session Changes (Included In Demo Release Above)

- Calendar session Edit now reviews all affected customers before changing time,
  duration or room. Confirmation updates the session and its reservations together;
  agreed quotes, payments, participant selections and preparation evidence remain.
- Cancel session offers refund/no-refund when money was collected, with an optional
  note. Unpaid sessions do not show irrelevant payment choices. Cancellation hides
  the session, releases reservations and keeps historical booking/payment records.
  Refunds use the existing approval/recovery worker, never a new payment service.
  External payments cannot silently be marked refunded. Approved means requested,
  not provider-confirmed completion.
- Signed impact review detects changed customers, counts, payments, capacity and
  reservations. Existing profile/school/order locks serialize writes. Duplicate
  confirmation is idempotent; failure on any reservation rolls back the operation.
  Conflicting rooms, insufficient capacity, pending checkout/refund, consumed
  materials, completed reservations, in-flight email and stale review are blocked.
- Schedule/cancellation email uses the existing outbox, environment restrictions and
  delivery worker. Superseded pending notices are replaced; the confirmation sweep
  does not send a duplicate after a move. Local simulated-email notices are hidden
  in the review; hosted bookings without delivery eligibility retain a concise notice.
- Review cleanup: affected customer name, phone and participant count; no repeated
  status/email/payment detail for moves. Paid cancellations retain payment amounts.
  Only changed times/rooms are summarized. Cancellation reuses the established red
  confirmation button, and unpaid cancellation omits irrelevant refund wording.
  No email transport or eligibility policy was changed by this UI cleanup.
- Scoped to native workshop reservations, excluding camps. Existing enrollment,
  lesson and SBMC defaults remain unchanged. No customer-name branches. Migration
  0060 adds nullable Session.canceled_at; old sessions remain unchanged.
- Verification: 193 targeted backend tests passed, followed by 99 final session,
  calendar and school-workflow tests; these runs overlap. New migration upgrade/
  rollback test passed; migration drift check passed. All 184 JavaScript tests
  passed (coverage gates passed). New module statement/branch coverage: 97.38%.
- Manual Chrome: desktop 1440x900, phone 390x844, tablet 768x1024 and 1024x768.
  Created two synthetic local bookings, moved them together, checked unchanged
  amounts, canceled the session, verified it disappeared and retained canceled
  reservation history. Phone review overflow was found and fixed; final dialog
  scrollWidth equals clientWidth, and input/button targets are at least 44px.
  Physical Android, virtual keyboard and printing remain unverified.
- Follow-up verification: 53 session/calendar backend tests and 29 calendar JS tests
  passed. Seven new cases cover contact rendering, payment-column relevance,
  real versus local email notices and duration/room-only changes. Chrome desktop
  1440x900, phone 390x844, tablets 768x1024 and 1024x768 inspected; populated and
  missing phone values checked, Cancel/Keep session left bookings unchanged.
  Phone dialog had no horizontal overflow; action targets were at least 44px high.
- Prior full run `session-changes-20261114` was intentionally interrupted before
  these edits: 4,279 tests passed, no test failures, exit 2 from KeyboardInterrupt.
  It is not a completed regression and is not release evidence for this version.
- Replacement full regression `session-review-compact-20261114` PASSED: 6,190
  application tests (3 skipped), 26 migration tests, and JavaScript/coverage gates.
  Exit 0; source unchanged, fingerprint
  `19f0269d35fd80b9c1a9dea207fc55aa29e8136c2f0c6ab1064c66c2b03071d9`.
  This baseline does not verify the newer recurring batch above.

## November 14 Calendar And Paid Party Release

- Approved for main/demo; production promotion is NOT approved. Includes the
  calendar intake/individual booking work, paid-party amendments, compact review,
  cancellation/UI consistency and Chrome checkout fixes recorded below.
- Final local regression `party-review-clarity-20261114` PASSED: 6,154 application
  tests (3 skipped), 25 migration tests and 183 JavaScript tests. Combined Python
  statement/branch coverage 97.99%; aggregate and every-module gates passed.
  No migration drift. Runner exit 0, source unchanged throughout.
- Tested base revision `133f1ac63e259bfd9d91e195b10ae325b4be31ac`, working-tree
  fingerprint `dd6659cccb713132def0a38af4c8401ad18cb994cd7a92e6381b7a450a4c26c0`.
  This release-status documentation is the only subsequent change before commit.
- Pushed commit `a87f820941131d35549af5bdb600937c71def9cf` to main. GitHub run
  `43675825169` PASSED. Render demo web and maintenance automatically deployed that
  exact revision after CI; web deploy took 1m19s. Production remains manual at
  `6ebf7fe`; no production deployment was performed.
- Hosted Chrome checks: booking picker/party intake, calendar customer roster,
  refund/no-refund cancellation form without submission, empty session creation,
  staff booking into that session, same-calendar return and correct balance.
  Tablet 768x1024 and 1024x768, phone 390x844 and desktop were inspected.
- Found and corrected an existing Pasadena demo configuration mismatch:
  `allow_deferred_selections` was false, preventing calendar customer intake.
  Enabled its existing "Allow participants to choose designs at the studio"
  setting without changing price/tax/payment terms or production. Other location
  configurations were not changed; review those before activating their intake.
- Left a synthetic demo session on Nov 22, 10 AM-noon, Pasadena, 12 seats, with
  one "Release verification Nov 14" booking (one participant, $72.15 due), email
  ketan@mypontora.com, reservation `758e840f-f368-42b0-bad6-7f245bcb0e54`.
  No card charge or refund was made. Inbox receipt was not independently verified.
  Existing upcoming demo slots had expired; this new session provides review data.
- Next: whole workshop-session move/cancel, affected-customer review and automatic
  notifications. Whole-session cancellation must offer both refund and no-refund
  choices; external payments still require a separate provider refund. Recurring
  schedule changes follow this shared operation, not a competing implementation.

## November 14 Party Review Clarity (Released To Demo)

- Paid-party review now shows only Total and Balance due by default. The optional
  Price breakdown retains tax, payments, refunds and existing ledger adjustments;
  unchanged totals are not repeated. Changed time/room and excess-payment warnings
  remain visible. No pricing, payment, refund or school-policy rules were changed.
- Calendar parties no longer label every hold as Reserved. Unconfirmed bookings
  show their actual state and a disabled Edit icon with a visible explanation;
  the existing View action leads to payment verification. Confirmed party edits
  remain available. No per-reservation provider call or new permission was added.
- The reported 7-person Nov 24 local party is an expired hold with an unresolved
  Oct 29 test checkout, not a confirmed booking. Its payment state was not forced,
  its quote was not changed, and editing safeguards remain in place.
- Verified the reported quote: 885.00 subtotal + 97.35 saved/configured tax =
  982.35 total; less 150.00 received = 832.35 due. Tests cover tax rounding, package
  changes, included/excess participants, credits/refund reductions, retry and
  concurrent-write protection, and immutable payment evidence.
- 301 focused backend tests passed (including SBMC workflows); 29 calendar/editor
  JavaScript tests passed. Manual desktop Chrome checks: 1440x900, 390x844,
  768x1024 and 1024x768, including disclosure interaction, excess warning, pending
  state, action sizing, and a synthetic calendar save from 3 to 4 participants.
  No horizontal overflow; tablet/phone actions measured 44px. Physical Android,
  virtual keyboard and printing remain unverified. Browser inspection caught stale
  assets; existing stylesheet/script versioning was updated and rechecked.
- Prior run `paid-party-amendments-20261114` passed 6,144 application tests (3
  skipped) but failed migrations after repeated reuse exhausted PostgreSQL's
  dropped-column slots in the disposable test DB. Rebuilding that same test DB
  resolved the problem: all 25 migration tests passed in 530.83s. Neither hosted
  data nor the local demo DB was rebuilt.
- New full regression PASSED in `party-review-clarity-20261114/status.json`
  under the task visualization workspace. The runner refreshes the existing
  disposable test database before migrations, records revision/fingerprint and
  exit status, and uses bounded idle-sleep protection. See the release entry above.

## November 14 Paid Party Amendments (Released To Demo)

- The existing Edit booking form now supports package changes, participant count
  and an optional signed price adjustment. It works both on the reservation and
  in the existing calendar dialog; no separate billing/editor flow was introduced.
- Same-package edits use the saved agreed package terms. Changing packages uses
  the selected published package's current terms, retaining any explicitly saved
  price adjustment. New adjustments are incremental and applied before package
  tax; custom parties without a package retain their agreed flat total plus the
  adjustment, with no guessed tax or per-person rate.
- Commercial changes require review even when the price stays unchanged. Room,
  time, package capacity, materials, permissions, stale previews and unsettled
  payments/refunds remain guarded. Guest removal preserves the chosen survivors,
  their frozen material recipes and preparation marks. Existing payment/refund
  entries are never rewritten; an excess payment is displayed, not auto-refunded.
- Existing statement/receipt and Email actions use the revised quote and balance.
  Payment-request templates reject stale amounts. Sending an updated request is
  explicit, not an automatic email on every edit. Account-less offline orders
  cannot offer an online payment button; statement attachments remain available.
- No migration, school configuration change, production activation or push. Shared
  enrollment/SBMC/Stripe/subscription logic was not changed. The package quote
  helper's optional adjustment defaults to zero for existing callers.
- Previous full run `calendar-intake-simplification-20261114` was deliberately
  interrupted before edits: 5,035 passed and one failed, exit 2, source unchanged.
  The failure was the old staff-estimate persistence expectation, corrected to
  match the approved removal (customer estimates remain supported). It is not a
  passing release run.
- Final focused verification: **508 backend tests passed**, including 27 new
  paid-party cases and the SBMC workflow suite; **28 calendar/editor JavaScript
  tests passed**. Paid-party tests cover immutable payments, retries, concurrent
  writers, quote drift, credits/refunds, excess payments, guest/material retention,
  stale payment emails and school/location/role scoping. Browser checks covered
  1440x900, 390x844, 768x1024 and 1024x768 in desktop Chrome, including real local
  save/cancel, capacity rejection, calendar return and statement-email capture.
  Tablet editor actions/inputs measured 44px high; no horizontal overflow.
  Physical Android, virtual keyboard, hardware payments and printing remain pending.
- Self-review retained the existing transaction/lock and retry protection, kept
  contact-only corrections independent of capacity revalidation, and verified
  price adjustments do not absorb prior ledger credits or refund reductions.
  Focused combined statement/branch coverage: shared booking edits **96.91%**,
  party amendment helper **90.76%**. `git diff --check` passed.
- Full regression `paid-party-amendments-20261114/status.json` passed application
  tests but failed on the repeatedly reused migration-test database. See the
  Party Review Clarity entry above for the successful fresh-DB migration rerun
  and the superseding full regression. This is not a passing release run.
- Review steps: [Paid party amendments](calendar-checkout-testing.md#paid-party-amendments).
  Next implementation priority remains whole-session move/cancel with affected-
  customer review and notifications, followed by recurring/bulk schedule changes.

## November 14 Staff Intake Simplification (Released To Demo)

- Staff party intake has one numeric participant count, no estimate field and no
  summary sidebar. The unused staff-summary script and its test were removed.
  Customer inquiry estimates and customer package summaries remain unchanged.
- New sessions created through calendar intake publish automatically, with or
  without a customer. The publication checkbox is removed. Existing draft records,
  Setup's draft workflow, public intake eligibility and Close to new bookings are
  unchanged. No migration or school-specific conditional was added.
- The prior `calendar-ux-20261114` full run FAILED: 6,109 application tests passed,
  three failed, three skipped. Unpaid-form normalization overwrote an explicit
  refund choice, breaking retries and unpaid-refund rejection. Fixed the form to
  default only an omitted choice; explicit choices reach the existing audited
  cancellation service. All three regressions now pass, including concurrent
  cancellation and duplicate-refund protection.
- Focused backend run: 351 passed, including staff parties/workshops, booking
  edits, customer package flows, calendar permissions, cancellation and SBMC.
  A final 76-test rerun also passed after adding the omitted-choice regression
  (overlapping coverage; 352 distinct focused tests across these runs).
  All 182 JavaScript tests and aggregate coverage gates passed. Chrome responsive
  checks covered 1440x900, 768x1024, 1024x768 and 390x844.
  Verified capacity blocking/recovery, single participant field, party submission
  staying on Calendar, and a customer-free session saving as Published. No live
  charge or outbound email was sent. Physical Android/keyboard/printing unverified.
- Full regression INTERRUPTED in `calendar-intake-simplification-20261114/status.json`
  under the task visualization workspace. Exact revision/fingerprint and durable
  logs accompany the detached run. See the newer paid-party record for its failure
  and replacement run. Nothing pushed or deployed.
- Current scenario audit and remaining launch work:
  [Staff intake and scenario audit](calendar-checkout-testing.md#staff-intake-simplification-and-current-scenario-audit).

## November 14 Calendar UX Corrections (Released To Demo)

- One Add booking entry opens workshop or party intake in the same calendar dialog.
  Session, reservation and party saves refresh the calendar without navigating away.
  Custom-party requests retain their existing review requirement and a review link.
- Fixed checkbox/radio alignment, consistent 44px roster actions and compact metrics.
  Customer booking counts are visible at the beginning of calendar event labels.
  Jasmine's November 11 Wix screenshot shows participant occupancy, not a separate
  customer booking count; the count is an added operational improvement.
- Full workshop times warn before customer entry; party times display remaining
  shared-room capacity with a two-query batch read using the existing peak-capacity
  calculation. Server-side locked capacity validation remains authoritative.
- Participant removal choices appear only when reducing the count. Cancellation
  keeps a short confirmation, optional note and refund choices only when paid.
  Payment evidence, refund safeguards and permissions remain unchanged. No new
  customer-specific branches, schema changes or SBMC configuration changes.
- 310 targeted backend checks passed, including SBMC, calendar permissions, party
  booking retries, capacity/concurrency, cancellation and refunds. All 182 JavaScript
  tests passed with aggregate coverage gates. Chrome responsive checks covered
  1440x900, 768x1024, 1024x768 and 390x844 with actual create/edit/cancel actions.
  Physical Android, virtual keyboard and printing remain explicitly pending.
- The preceding `calendar-individual-bookings-20261114` full run finished with
  6,107 application and 25 migration tests passing, but FAILED its module coverage
  gate (booking-edit view 88.68%). Added stale-submit/JSON-save regression coverage;
  focused coverage is now 96.23%. That earlier run is not a release pass.
- That full regression FAILED in `calendar-ux-20261114/status.json`; the three
  cancellation regressions and their fixes are recorded above. Logs, revision,
  fingerprint and bounded sleep prevention accompany it. Do not push until the exact source passes. Demo and
  production remain untouched. Review instructions: [Calendar UX checks](calendar-checkout-testing.md#november-14-calendar-ux-follow-up).

## November 14 Calendar Intake And Individual Edits (Released To Demo)

- Implemented priorities 1 and 2 below. Fixed the lesson-settings Cancel destination
  rather than weakening the navigation test; 85 focused navigation tests passed.
- Empty calendar times create a session with optional customer details. Existing
  session intake fixes its time. Shared session forms, reservation creation,
  participant choices, inventory, payment ledger and notification services remain
  authoritative. No customer-name branches or new school configuration flags.
- Individual workshop bookings support contact/time/count edits; party bookings
  support contact/time edits while preserving their paid quote and duration. Party
  package/count/price amendments remain priority 3, not implicitly completed here.
  Existing individual cancellation is accessible from the calendar roster.
- Count changes require a financial preview using agreed prices, explicit selection
  of removed participants, and confirmation. Surviving participants/materials and
  immutable payment evidence remain intact. No automatic charge/refund or credit.
  Missing legacy price evidence blocks count changes only, not contact/time edits.
- Role/location restrictions, stale booking and destination-session checks,
  payment/refund-in-progress blocks, capacity/room validation, retry protection and
  audit before/after snapshots are covered. Self-review also refreshed the locked
  order before evaluating its quote and removed duplicate standalone edit headings.
- Final targeted run: 376 backend tests passed (including SBMC, customer edits,
  participant selections, payment requests and documents). All 179 JavaScript tests
  passed with coverage gates (99.24% lines, 95.66% branches, 98.37% functions).
  `git diff --check` passed. No new migration.
- Manual Chrome: 1440x900 desktop, 768x1024 tablet portrait, 1024x768 landscape and
  390x844 phone. Created empty and customer-filled sessions, added to an existing
  time, previewed/confirmed count increase, moved an individual booking and canceled
  it without altering the other customer or deleting the session. Checked reachable
  actions and no horizontal dialog overflow. Physical Android/keyboard remain pending.
- That implementation's full-suite verification completed with a coverage failure in
  `calendar-individual-bookings-20261114/status.json` under this task's visualization
  workspace. The detached runner records HEAD, working-tree fingerprint, logs and
  exit status with bounded sleep prevention. See the follow-up correction above;
  this earlier run does not establish release readiness.
- [Local test steps](calendar-checkout-testing.md#individual-booking-edits-november-14).
  Individual reschedule emails remain staff-triggered through the existing Email
  action. Whole-session/bulk updates and their automatic notices remain priorities
  4 and 5. Production remains pinned; no push or hosted change in this pass.

## November 14 Jasmine Requirements Reconciliation (Planning, Not Implemented)

Evidence re-read from the Gmail recap thread: Jasmine's November 11 message
`1a1cbd6e1492f3fc`, November 14 reply `1a1db0c83ce2d572`, and the October 28
meeting recap `1a18573ab29b3bb4`. November 14 supersedes the existing-times-only
calendar restriction below. Paid-party amendments are normal operational scope
we missed, not merely an optional enhancement. No product changes in this audit.

Priority order and acceptance boundaries:

1. Repair the known navigation regression and establish a passing baseline.
   The November 12 cleanup full run FAILED: 6,084 passed, one failed, three
   skipped; migration/coverage/JS stages after application tests did not run.
   Failure: `test_cancel_returns_to_related_section[lesson_settings-lessons]`.
   Inspect intended navigation before changing the test. Targeted passing checks
   do not make this revision releasable.
2. Calendar intake and individual booking management: create sessions with or
   without customers from a selected empty time; add customers to existing times
   without asking them to reselect that time; create/edit/move/cancel individual
   workshop and party bookings. Use contextual dialogs and existing reservation
   details, no added toolbar clutter or hover/right-click-only controls. Include
   customer contact/count edits, participant data and capacity/material handling.
3. Paid-party amendments in reservation details: package upgrade/downgrade,
   participant count, other details and explicit price adjustments; preview tax,
   revised total, payments/refunds and balance. Preserve reservation identity and
   immutable payment evidence; audit revisions and refresh existing payment
   requests/documents. Do not silently refund or create credit for an overpayment.
4. Whole-session edits/moves/cancellation: list affected customers and counts
   before any change affecting reservations; validate rooms/capacity/exclusive
   parties, apply consistent changes, preserve audit/financial records, and remove
   canceled availability from public intake. Closing intake is not cancellation.
5. Recurring creation and bulk changes: explicit location/date range/series scope,
   single-occurrence exceptions, affected sessions/customers/bookings and conflict
   preview; revalidate on confirmation. Confirmed time changes move all selected
   affected bookings and automatically queue updated reservation emails. This is
   not a manual customer-by-customer reschedule. Preserve past/completed records.
6. Finish/verify November 11 requests: actual-recipient confirmation delivery and
   working links; inline Studio Prep staff notes and phone across workshops and
   parties; Google/Outlook/ICS choices including party parity; supplied design/color
   photos with default fallback. Existing implemented features need regression and
   hosted checks, not duplicate implementations. Photo files and final wording
   remain external inputs. Customer-authored notes remain distinct from staff notes.
7. Launch data/site preparation: inspect Wix access and export, rehearse upcoming
   workshop/party import with participant details, payments/refunds/balances and
   source identity; reconcile totals and avoid duplicate reservations/emails.
   Imported bookings must support the same amendments without fabricating provider
   payment evidence. Verify website styling and workshop/party booking links;
   coordinate final data delta and stop duplicate intake at cutover.
8. Release gates: exact-revision full suite/CI and migration checks; SBMC and
   enrollment regressions, role/location isolation; desktop/phone/tablet portrait
   and landscape actions plus Jasmine's actual-device acceptance for new flows.
   Verify demo before separately approved production promotion; configure approved
   sender/templates, live account/location/terminal mapping, webhooks, scheduled
   live-payment recovery and reminders; approved small live charge/refund, rollback
   and monitoring. No production activation inferred from a demo pass.

Required supporting behavior (engineering work, not extra customer questionnaires):
- Retain participant selections, staff notes/assignments, usable customer links and
  correct Studio Prep/print/document/calendar dates after edits. Recompute reminders
  and affected preparation/material requirements without needlessly resetting work.
- Prevent races between public bookings, staff changes and payments. Stale impact
  previews, active/uncertain payments and obsolete checkout amounts must be handled
  before committing; retry safely and expose failed notification delivery.
- Ordinary time moves must not silently adopt today's prices/policies. Explicit
  package/count/price edits show their financial effect, retain collected money and
  flag excess payment. Package changes may alter duration, observers and exclusive
  room use as well as price; check all of them.
- Automatic email is explicitly required for confirmed recurring schedule moves.
  Staff-controlled cancellation messages from October 28 still stand; do not infer
  automatic refunds or automatic cancellation emails from November 14.
- Customer calendar links are add-to-calendar actions, not a promised background
  Google/Outlook synchronization. Updating Pontora does not prove an already saved
  personal calendar event has changed.

Keep previously deferred promo/gift-card/non-expiring-credit systems and customer
self-service rescheduling separate. Do not introduce mandatory drag-and-drop,
calendar synchronization or a new Square invoicing subsystem to satisfy this scope.
Do not promise a launch date until amendment tests, data reconciliation and live
payment checks pass. Track any deliberate deferral with owner/Jasmine agreement.

## November 12 Booking Popup Simplification (Local, Unpushed)

- Checked the preceding full run before editing: 6,079 application tests, 25
  migration tests, three skips, 178 JavaScript tests and coverage gates passed,
  unchanged fingerprint `76fcb392b365b05519a1da7ef7b7ba42ba19e277da0865642825ef21b0abc4a8`.
- Removed Schedule time only, price/tax copy and session creation from Add booking.
  The existing toolbar button and empty-slot clicks book into existing times;
  exact times are preselected, otherwise the user must choose. Empty availability
  has an explicit Setup link, not a silent new-session fallback. Session creation
  remains in Setup and editing remains available on calendar events. No extra
  toolbar buttons or changes to school configuration.
- Visible Cancel action in each eligible customer roster row, usable by tap.
  Cancellation uses the existing confirmation/refund flow, releases only that
  customer's places and keeps the session and financial history. Completed
  reservations no longer advertise an action that the service rejects.
- Cancel controls standardized across 17 templates, with a regression guard for
  unstyled Cancel links. Calendar-dialog Cancel/Keep reservation are real buttons;
  navigation links use the established button style. Shared SBMC forms receive
  only the Cancel appearance change, no enrollment/status/payment behavior changes.
- Manual Chrome: added a second booking to an existing session, canceled only the
  new synthetic booking and verified the first remained and capacity was released.
  Desktop 1440x900, portrait 768x1024, landscape 1024x768 and phone 390x844 verified.
  Real Android/virtual keyboard remain untested; no live payment or email sent.
- The detailed [use-case audit](calendar-checkout-testing.md#november-12-simplification-and-practical-use-cases)
  records both supported paths and genuine gaps: workshop reservation moves,
  group-size/contact changes, whole-session changes, recurring/bulk scheduling,
  cancellation/reschedule notices and removal of accidental empty sessions.
  Do not mark Jasmine's entire appointment-management request complete yet.
- Final full regression failed (see November 14 reconciliation above), recorded in
  `calendar-booking-cleanup-20261112/status.json` under the task visualization workspace.
  No push, demo deployment or production
  promotion in this task. Previous full passes do not verify these later edits.
- Final targeted checks passed: 211 backend tests (staff workshop bookings,
  calendar actions/read models, refunds and SBMC), all 178 JavaScript tests with
  coverage gates, and `git diff --check`.

## November 11 Calendar Actions And Checkout Recovery (Local, Unpushed)

- Approved scope: create/edit workshop sessions from calendar dialogs, publish or
  close sessions to new bookings, cancel individual reservations from the roster,
  and enter the existing party-booking flow from the calendar. Party saves and
  cancellation return to the selected calendar context. No bulk/recurring changes,
  drag-and-drop, or moving already-booked sessions in this release.
- Existing forms, services, CSRF, role/location restrictions, stale-edit protection,
  capacity checks, cancellation/refund handling and audit records are reused.
  No migration, school configuration, enrollment/lead or Stripe writer changed.
  Calendar controls require the existing calendar capability and writer permission.
  SBMC's local school has no scheduling profile; this work does not enable one.
- Square card initialization now has a 20-second deadline and reload recovery;
  initialization failure cannot submit a payment or enable Pay after a late result.
  The SDK loads asynchronously so a blocked script cannot indefinitely block the
  recovery code. Existing charge/tokenization/verification behavior is unchanged.
  Regular Chrome and private Chrome both loaded the sandbox card fields. Empty-card
  validation was exercised; no live card was charged. The original profile-specific
  cause was not conclusively identified, so do not claim a specific extension caused it.
- Manual verification: local in-app browser at desktop 1440x900, phone 390x844,
  tablet portrait 768x1024 and landscape 1024x768. Created a draft, published it,
  edited its time, closed new bookings, rejected a booked-session move, created a
  synthetic party and canceled it from the calendar. Active-checkout cancellation
  was correctly rejected. Dialog actions, Escape/Cancel and horizontal calendar
  scrolling remain usable. Turned off vendor event overlap after mobile taps could
  select the neighboring event. Actual Android hardware/keyboard and printing are
  still unverified.
- Targeted results: 198 calendar/party/SBMC/lead/submission tests, 180 Square/setup/
  inventory tests, and 121 calendar/reservation/payment-request tests passed (some
  overlap). All 174 JavaScript tests passed the aggregate coverage gates. The last
  JS-only change reveals a newly created session when Hide empty sessions was set.
- November 12 follow-up: empty Day/Week slots open the same session editor with
  venue date/time prefilled at half-hour precision. Location rows and filters
  constrain workshop choices. Month clicks still open Day. Chooser spacing fixed.
  Browser inspection also caught an existing callback-plus-promise event-loader
  double completion; the loader now uses only callbacks, with regression coverage.
- Follow-up verification: 182 backend tests (calendar, refund and SBMC), 23
  calendar/editor JavaScript tests; all 176 JavaScript tests and aggregate coverage
  gates passed (99.23% lines, 95.79% branches, 98.35% functions). Local desktop,
  phone and both tablet viewports checked.
  Verified click/prefill, location choices, save/refresh, navigation and cancellation
  of the editor. No new browser console errors after the event-loader fix.
- Previous `calendar-checkout-20261112` full run failed: 6,055 passed, one refund
  fixture failed, three skipped. macOS sleep overlapped the run; the fixture's
  simulated payment could expire before settlement. Its setup clock is now frozen
  and paid evidence asserted; the refund module rerun passed all 54 tests.
  Live expiry behavior is unchanged.
- Full regression passed on November 12: 6,056 application tests, 25 migration
  tests, three skips, 176 JavaScript tests, migration drift and coverage gates.
  `calendar-click-create-20261112/status.json` records unchanged source fingerprint
  `a2e2910b50f56637c3f9cd69daafa85d645457dffd861b16dfc0679a0d23a191`.
  Only documentation was updated after that run in the phone-workflow audit.
  No hosted deployment or production promotion performed in this task.
- Review instructions: [Calendar and checkout checks](calendar-checkout-testing.md).

### Calendar Customer Bookings (November 12 Initial Pass, Superseded Above)

- Owner approved customer contact and participant count in the same calendar
  dialog. Add booking selects an existing workshop time or creates a draft time
  and reservation atomically. Empty-slot location/time is preselected; existing
  roster Add booking preselects its session. Schedule time only remains available
  for new availability. Customer contact belongs to the request, not the session.
- Confirmed reservations reserve capacity, retain the quoted price/tax/policy and
  leave payment outstanding. Shared reservation, stock, ledger, audit and outbox
  services are reused. Signed submissions prevent duplicate bookings and reject
  changed details/prices/times. Room and capacity checks remain enforced. No
  customer consent is asserted; the existing workshop policy is an informational
  notice, stored with the reservation and shown on its customer details page.
- Participant placeholders can be completed inline by staff or through the existing
  private confirmation link, including configured non-stock choices such as age.
  Emails respect existing settings and go to the entered customer address. No new
  notification switch, allowlist, cron or recipient routing was added.
- Booking availability uses existing capabilities, price, catalog and deferred-
  selection settings. Real studios require their configured payment account;
  missing configuration cannot create a test ledger. No migration, school-specific
  branch or SBMC/enrollment configuration change. Public bookings still cannot
  reserve unpublished sessions; the draft exception is explicit staff-booking data.
- Manual local end-to-end: calendar intake, staff/customer participant editing,
  synthetic offline payment, preparation and completion. Tested existing/new
  times and over-capacity rejection. Chrome verified at 1440x900, 768x1024,
  1024x768 and 390x844, including scrollable inline grid controls. In-app viewport
  overrides did not actually resize in this follow-up; those screenshots establish
  desktop behavior only. Physical Android/keyboard and live email remain untested.
- Backend targeted batches: 144 and 335 passed (overlapping), including preparation,
  inventory and SBMC regressions. Final staff/email/calendar batch: 44 passed;
  includes actual-recipient single delivery using the existing worker with mocked
  transport. All 178 JavaScript tests and aggregate coverage gates passed
  (99.25% lines, 95.57% branches, 98.37% functions). Its full regression passed in
  `calendar-customer-booking-20261112/status.json` under the task visualization
  workspace, with revision/fingerprint/logs: 6,079 application tests, 25 migration
  tests and three skips. This pass does not verify the later simplification above.
  No push, hosted demo activation or production promotion yet.
- Review steps: [Calendar and checkout checks](calendar-checkout-testing.md).

## November 11 Jasmine Demo Release

- Owner approved pushing all pending work to main after the full suite passes,
  then verifying demo. Release commit title: **Improve booking confirmations and
  Studio Prep contact workflow**. Production promotion is NOT approved.
- Full regression passed: **6,045 application tests, 25 migration tests, three
  skips**, JavaScript tests and aggregate/module coverage gates; measured Python
  coverage **98.1229%**. Source remained unchanged. Evidence:
  `studio-contact-cleanup-20261111/status.json`, fingerprint
  `a57c43ab69458671470c6947e321205c3cc6963ef8bbf19b0b56abb4749b96dc`.
  The saved `verified-manifest.json` records the tested files; only this release
  documentation was updated afterward. CI reruns the final committed revision.
- Includes the cron connection retry, additive notification cutover migration
  `scheduling.0059`, actual-recipient demo confirmations, scoped inline staff notes,
  contact display, workshop calendar choices and honest online-payment status.
- Render rechecked before push: demo web and maintenance follow main after CI;
  production web and maintenance remain on `6ebf7fe` with auto-deploy off. Hosted
  deployment, notification activation and smoke-test results will be recorded in
  the release commit's verification comment. Do not infer those checks from the
  local pass. Existing customer records must not be used for test emails.
- Remaining Jasmine work: direct calendar create/edit/cancel; scoped recurring
  schedule and bulk changes with booked-session safeguards; supplied design/color
  photos with default-image fallback; validated upcoming-booking import from Wix;
  website booking links/theme integration and final launch configuration. Her
  final email wording/triggers remain pending but need not block other work.
  Physical Terminal verification requires the live account and an approved small
  real charge/refund. Google/Outlook choices currently cover workshop confirmation;
  party confirmation calendar parity remains a separate follow-up.

## November 11 Studio Contact And Payment Status Follow-Up (Local, Unpushed)

- Studio Prep places the plain-text contact phone beside the booking name and
  inline staff notes below participants. Click-to-call was not requested and was
  removed. Editing a note hides its duplicate read-only copy. Add/edit/save/cancel
  controls retain 44px targets and existing permissions, audit and conflict checks.
- Current contact phone is also shown in reservation lists/details, individual
  preparation, workshop request details and day/week preparation printouts.
  Clearing a source phone does not resurrect an old snapshot value. Party inquiry
  already displays its contact phone. No enrollment/lead behavior was changed.
- Online checkouts now distinguish awaiting customer payment, expired unused
  checkout and a payment needing verification without an animated fake-progress
  indicator. Rendering does not call providers or alter money. Real terminal and
  refund polling remains unchanged, including duplicate-collection safeguards.
- The reported local $399.45 spinner was an expired, unsubmitted Square test
  checkout from October 30 (attempt 42). Existing recovery expired that attempt
  locally without a provider call or ledger change. The confirmed reservation's
  $150 received and $399.45 balance due were unchanged. No hosted data was changed.
- Final targeted regression: **345 passed**, including Square/Stripe payment
  controls, recovery, concurrency, notes, contact consistency and roster query
  counts. Two obsolete assertions expecting fake verification copy were corrected;
  submitted unknown payments still require verification. Studio Prep JS: **10/10**.
- In-app desktop browser: 1440x900, phone 390x844, tablet portrait 768x1024 and
  landscape 1024x768. Exercised inline add/edit/save/cancel, contact placement,
  reservation details/preparation/list and mobile grid scrolling. No page overflow;
  essential editing controls remain reachable. Physical Android, virtual keyboard
  and physical printing remain unverified.
- Full regression for this follow-up passed in the task artifact directory
  `studio-contact-cleanup-20261111`, with logs, revision, source fingerprint and
  completion status. See the release entry above for the approved demo release.

## November 11 Jasmine Follow-Up (Local, Unpushed)

- Demo workshop and party confirmations use the existing notification outbox and
  the booking's entered recipient. There is no email-address allowlist or recipient
  redirection. Demo subjects are labeled `[Demo]`; production content is unchanged.
  Migration `scheduling.0059_demo_notifications_since` establishes a cutover on
  existing profiles so older copied bookings cannot trigger automatic confirmations.
  Automatic demo reminders remain excluded. Explicit staff-composed emails retain
  their existing behavior and permissions.
- Activation after demo deployment: verify both existing confirmation switches in
  Guest communication, create a new synthetic booking addressed to
  `ketan@mypontora.com`, and check delivery, contents, links and outbox completion.
  No new configuration screen or tester-address setup is required. Local workshop
  confirmations were enabled through the existing settings screen; local external
  delivery remains disabled. Hosted settings and real inbox delivery have NOT been
  changed/tested by this local pass.
- Studio Prep displays the booking contact phone and supports inline add/edit of
  existing staff notes. This reuses reservation/inquiry notes and permissions,
  records before/after values in the audit log, detects conflicting edits and
  preserves customer-authored notes as read-only. Enrollment/lead notes, SBMC's
  enrollment UI, Stripe/subscription behavior and school configuration are unchanged.
  Existing roster query-count tests remain passing.
- The existing customer workshop confirmation now offers Google Calendar,
  Outlook and the Apple/other-calendar ICS download. Links and ICS use current
  reservation times, including after rescheduling, without exposing contact details,
  internal notes or private booking links to calendar providers. Calendar choices
  are not a background calendar sync; customers still save the event themselves.
  Party-calendar expansion is not part of this change. Amounts consistently show
  two decimal places and the displayed balance-due date follows the reservation.
- Verification: final focused run passed **339 tests** across cron connection,
  notifications, inline notes, permissions, concurrency, customer calendars,
  preparation, email/settings and hosted/local booking regressions. Studio Prep
  JavaScript tests passed **10/10**; migration drift and diff whitespace checks pass.
  Browser checks used the in-app desktop browser at 1440x900, 390x844, 768x1024 and
  1024x768. Notes save/cancel/blank validation, 44px editor controls, keyboard calendar
  menu use, overflow checks and actual ICS download were exercised. Google handoff
  reached its signed-out destination; saving an event in an authenticated Google or
  Outlook account was not tested. Physical Android, virtual keyboard and printing
  remain unverified. No real payment or external email was sent during these checks.
- This batch's full regression passed, with unchanged source and durable evidence
  in `jasmine-first-batch-20261111/status.json`: application, migration, aggregate
  and module coverage, JavaScript and migration drift gates all passed. Fingerprint:
  `2342b25c5aa51792893a44c0efb060c39a3578ead588a7b12d74c8c37182f573`.
  This predates the Studio Contact follow-up above, which also passed afterward.
  The preceding `demo-cron-connection-20261111` application pass had 6,007 passing
  tests but its remaining migration pass was canceled before this work; it is not
  a completed full-suite pass and does not verify this new batch.
- Consolidated unpushed scope: the demo connection retry below; demo confirmation
  delivery/cutover migration; Studio Prep phone/inline notes; workshop calendar and
  display fixes; associated tests and this release record. No production promotion
  is approved. Calendar management, bulk/recurring changes, customer-supplied
  animal/color photos and booking-data import remain separate follow-up work.

## November 11 Demo Connection Incident

- Sentry issue `7843398544` came from demo maintenance's initial deployment
  check, before booking maintenance started. The 20:40 UTC run timed out at
  20:42:41 and exited unsuccessfully. The 20:45 run passed the check at 20:45:23
  and completed successfully at 20:45:43. All 126 web health requests in the
  inspected 20:39-20:48 window returned 200; PostgreSQL continued accepting
  connections. Evidence supports a transient cron-to-database connectivity
  failure; the underlying Render network cause is not established.
- Local fix, NOT deployed: the demo boundary command bounds each PostgreSQL
  connection attempt to 10 seconds per host and retries connection errors up to
  three times, with five seconds between attempts. It never retries safety
  queries or maintenance/payment work. Recognized authentication/database
  configuration SQLSTATE errors fail immediately; final connection failures
  remain uncaught for monitoring. Existing production/shared command behavior
  and private database access remain unchanged.
- Targeted connection/isolation tests cover recovery, exhausted retries,
  option restoration, non-PostgreSQL connections, fail-closed isolation after
  recovery and no replay after a query fails. Full regression is required before
  push; no production promotion is authorized.
- Hosted demo web and maintenance are on `665a919518bdec165823d83ce2746685578f9451`.
  Production remains on `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c`.
  The previous release passed CI and hosted smoke checks; its final evidence is
  [recorded on the commit](https://github.com/ketangan/student_enrollment_portal/commit/665a919518bdec165823d83ce2746685578f9451#commitcomment-209443130).

## Hosted Baseline Before November 11 Release

| Service | Live revision | Deployment policy |
| --- | --- | --- |
| Demo web (`pontora-demo`) | `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c` | main, automatically after CI passes |
| Demo maintenance | `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c` | main, automatically after CI passes |
| Production web | `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c` | manual, approved exact revision |
| Production maintenance | `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c` | manual, approved exact revision |

These are observations from Render, reconfirmed before the November 11 push.
They are not a claim that the release candidate below is already deployed.

## Pending Work And Activation

| Change | Code/PR | Demo status | Production decision / gate |
| --- | --- | --- | --- |
| Contextual school go-live checklist and onboarding tests | November 11 readiness release, based on `6ebf7fe` | Full local regression passed; demo release authorized | Production promotion NOT approved; includes additive checklist-choice migration 0069 |
| Restore Leads/Submissions layout and shared status buttons; preserve custom answers on partial lead edits; allow explicitly clearing a submission follow-up | November 11 readiness release | Full regression and local responsive checks passed; verify hosted delivery through release evidence below | Production promotion NOT approved; retain existing school capabilities and policies |
| Automatic demo deployment and expanded CI coverage gates | Render configuration applied; November 11 readiness release | Demo web/job set to `checksPass` | Production auto-deploy remains off; no production release authorized by this configuration change |
| Database-aware health check and readiness regression repair | November 11 readiness release | Full regression passed; health module 100% coverage | Both hosted web services already use `/healthz/`; production code promotion remains separate |
| Production database network restriction | Owner approved November 10; applied November 11 07:20:33 UTC | Demo was already private-only | Production external allowlist cleared after confirming web/job internal URLs; later maintenance and production school list checks passed |
| Plushie production onboarding and physical Square Terminal | Implementation in hosted baseline | Sandbox only | Customer account/website decisions, production setup and approved small live charge/refund still required; enable production `--include-live-recovery` only at that gate |

## Maintenance Rules

- Update this table in each release-related PR: exact commit/PR, affected shared
  paths, demo verification evidence, approval source/date, production settings to
  enable or keep disabled, migrations/dependencies and remaining blockers.
- Use explicit states: local, demo review, production approved, production live,
  deferred. A passing CI run is not customer approval. Never infer approval.
- Before production promotion, compare its deployed commit with the proposed
  revision and account for **every** change, including earlier merged PRs.
- Record the full-suite revision and working-tree fingerprint. Verify both web
  and maintenance deploys, then update hosted revisions and smoke-test evidence.
- If code must be excluded rather than merely disabled, use the temporary release
  branch procedure in [the deployment guide](demo-production-separation.md).
- Customer setup decisions belong in the school's Go-live checklist. This tracker
  records software releases and activation gates, not passwords or credentials.

## November 10 Verification

- Prior full run passed application/migration tests but failed the onboarding
  module's 90% coverage gate. Added welcome-email recipient/token/link/audit,
  unready-school rejection and delivery-failure tests; targeted onboarding
  coverage is now 98.08%, and go-live applicability coverage is 100%.
- Added regressions reproducing custom-answer loss on partial lead updates and
  rendered-form tests for setting, rejecting invalid dates and clearing follow-ups
  in both normal and simplified workflows. Retested SBMC enrollment/lead behavior,
  school isolation, permissions and tour-field handling.
- Manually exercised local Leads and Enrollment details, contact/application
  editing, lead status, follow-ups, converted-lead navigation, invalid email input
  and responsive navigation. Browser emulation: desktop 1440x900, phone 390x844,
  tablet portrait 768x1024 and landscape 1024x768. Corrected small enrollment
  inputs; verified no horizontal page overflow. Real Android hardware, virtual
  keyboard and physical printing remain unverified.
- The preceding full run (`leads-release-regression-20261110`) passed application,
  migration, JavaScript and per-module coverage checks with 98.11% aggregate
  coverage. Its source fingerprint is recorded in `status.json`; it does not
  verify the subsequent header changes below.
- Header follow-up: removed the simplified status dropdowns in favor of the
  existing configured status buttons on both detail pages. Restored the compact
  desktop back/previous/next row and renamed the simplified navigation destination
  and list heading to Submissions. Status policy and school capabilities are unchanged.
- Final targeted regression: 192 passed across workflow simplicity, session
  workspace, school navigation policy, shared record details and SBMC workflows.
- Exercised actual status changes and restored the synthetic records' statuses;
  verified read-only/system-managed statuses, navigation, and responsive layouts
  at 1440x900, 390x844, 768x1024 and 1024x768 in the in-app desktop browser.
  Phone/tablet header actions measure at least 44x44 CSS pixels with no horizontal
  overflow. This is responsive emulation, not physical Android verification.
- Final full-suite evidence: local artifact directory
  `record-header-regression-20261110` under this task's visualization workspace.
  Check `status.json` for completion, revision and working-tree fingerprint before
  any push. That run failed nine variants of one obsolete exact-markup assertion
  in `test_admissions_preview.py` (5,972 other application tests passed). The fix
  checks the active status, `aria-current` and read-only behavior; all 37 tests in
  that module pass. This is not a passing full run.

## November 10 Launch Readiness Follow-Up

- Production database external access is disabled; subsequent maintenance and
  read-only admin checks passed. The isolated PITR rehearsal matched all 102 public
  tables by row count/content checksum and its temporary private database was
  deleted after about nine minutes. No service was pointed at the restored copy.
- The approved planned Sentry event `62f571b3e3d14ebf9cdfb6f8a4fdc4c4`
  reached the operator's Gmail inbox. Render cron-failure notification configuration
  was verified, but its delivery was not tested by failing a production job.
- 116 focused health, admissions, checklist and onboarding tests passed. A further
  504 focused Plushie booking, Terminal, refund, recovery, consent and preparation
  tests passed. Health and CI guard checks: 14 passed, health coverage 100%.
- Local browser checks covered the launch checklist at 1440x900, 390x844,
  768x1024 and 1024x768, disclosure keyboard interaction, mobile navigation,
  disabled production attestations and 44px checklist actions. Payment/refund
  dialog entry and cancellation were checked without submitting transactions;
  sandbox over-limit validation remained visible and unobscured. Studio Prep
  filtering and a workshop-scoped staff save were exercised; the original sample
  value was restored and the other booking stayed unchanged. Physical Android,
  virtual keyboard and printing remain pending.
- Both hosted environments remain on `6ebf7fe`; no application release or live
  payment activation occurred. The staff-consent fix was already in that revision.
- Final full regression evidence belongs to `plushie-launch-readiness-20261110`
  under this task's visualization workspace. `run.py` uses the CI coverage source
  list and records revision, working-tree fingerprint, logs and exit status.
  This run completed successfully on November 11; see the release record below.

## November 11 Demo Release

- Owner explicitly authorized completing regression and publishing to demo, not
  production. The release commit is titled **Launch readiness checklist and restore
  consistent record details**. Its GitHub Actions checks and commit comment record
  the exact released SHA, both Render deployment results and hosted smoke checks.
- Local full suite: **5,991 application tests, 24 migration tests, three skips,
  167 JavaScript tests**, migration drift and all coverage gates passed. Combined
  measured Python coverage: **98.1158%**. Source remained unchanged throughout.
  Evidence: `plushie-launch-readiness-20261110/status.json`, fingerprint
  `ea83d39daa05e23130904103e3bdbf62ccbb4d2c632c48efff3ac65030f1b920`.
- The overnight delay was macOS sleep, with a wake at 08:46 and explicit clamshell
  sleep again 08:51-09:54 Pacific. Actual test runtimes were 12m29s for
  application tests and 9m19s for migrations. No test failure/deadlock caused the
  overnight pause. Temporary idle-sleep protection was attached, and the standing
  test instructions now require it for detached macOS runs. It cannot override a
  closed lid. The run was allowed to finish without skipping tests or changing its
  tested files.
- Only documentation/instructions changed after this passing run; the release
  application, migrations, assets, CI configuration and tests match its saved file
  manifest. GitHub CI independently reruns the final pushed commit.
- Production web and maintenance must remain on `6ebf7fe` with auto-deploy off.
  No live payment activation, tenant migration, sample-data publication or new
  customer workflow enablement is part of this release. The combined enrollment
  demo's local database configuration is not automatically copied to hosting.
## November 16: Session Surfaces and Participant Preparation (Unpushed)

- New/Edit Session standalone pages use the same compact white form surface;
  calendar dialogs reuse the existing forms without nesting a second surface.
- Workshop and party reservation detail rows reuse Studio Prep's prepared control
  and preparation service. Changes save inline, preserve draft notes, require a
  current revision and selected materials, and remain disabled for read-only staff
  or consumed/completed bookings. Removed the bulk Mark prepared header action
  where these participant controls are present.
- Local Plushie Camps capability removed after verifying zero camp reservations.
  Existing camp setup/data retained. Setup, calendar, dashboard and public workshop
  lists hide camp offerings when the capability is absent. Other profiles retain
  their existing capabilities. Fresh Plushie demo seeds use the YAML capability
  defaults without Camps; reseeding does not overwrite existing profile settings.
- Hosted activation pending: inspect Plushie's camp reservations before removing
  `camps` from its database-backed SchedulingProfile capabilities. Do not delete
  camp data or disable the capability for SBMC/other schools. If hosted camp
  commitments exist, review those before hiding their management screens.
- Previous full run `/private/tmp/pontora-regression-20261116-final/status.json`
  finished with 6,268 passes, one failure and three skips. Its sole failure was
  an obsolete exact CSS-class assertion in admin parity; replaced with checks for
  the icon action, accessible title and correct reservation destination. Targeted
  admin parity now passes. That failed full run did not reach subsequent stages;
  no new full regression was started, per the owner's targeted-only instruction.
- Targeted Python passes: participant preparation, admin parity, calendar, session
  changes, workshop schedule, setup and booking experience demo. JavaScript:
  reservation workspace and Studio Prep, 22 passing checks. Browser verification:
  desktop Chrome, approximately 768x1024 and 1024x768 tablet CSS viewports and
  390px phone width (accounting for existing browser zoom); form backgrounds,
  date picker and inline guest preparation toggling. Physical Android remains
  unverified. No production deployment or push in this batch.

### November 16: Reservation Entry and Prep Contact Layout

- Local, unpushed: Studio Prep shows phone and email links below the customer's
  name. Missing contact values are omitted; existing readiness/notes behavior is
  unchanged.
- Reservations uses the existing calendar editor for New > Reservation and
  New > Party. Choices respect staff permissions, capabilities and selected
  location. Calendar's own menu is unchanged. The compact toolbar stays on one
  row at desktop/tablet widths and wraps on phones.
- Package-party forms share the compact editor styling on both the standalone
  page and dialog; existing package, capacity, consent, payment and retry services
  remain authoritative. Successful dialog saves return to the reservation list.
- Targeted checks passed: admin parity, Studio Prep, staff workshops, staff parties
  and package-only bookings; 22 JavaScript checks across editor, reservation fields,
  Studio Prep and list-menu controller. Updated the old Create booking assertion
  to expect the new Save label. No full suite started for this targeted-only pass.
- Manual Chrome: desktop, 768x1024, 1024x768 and 390x844 CSS viewports; menu opening,
  shared reservation form, party package/time selection, inline validation,
  reachable customer fields/Cancel/Save, and prep contact wrapping. No real emails
  or payment transactions sent. Physical Android and hosted verification pending.

### November 16: Session Review, Seat Limits and Scoped Themes

- Local, unpushed: single-session edits highlight changed time, room and capacity
  with old/new values. Notify customers follows the live change review. Calendar
  and Setup share the template and existing preflight/save services.
- Reservation choices display genuinely open seats, while the editable quantity
  limit includes the reservation's own seats only in its current session. The
  plus control stops at that limit; moving sessions uses the destination's free
  seats. Existing server-side capacity checks remain authoritative.
- `admin_theme_switching_enabled` defaults on for all schools. Set the existing
  Plushie School's `feature_flags` entry to false during hosted activation,
  preserving every other flag; this has been applied locally only. No migration
  or demo loader is needed at runtime. The menu hides theme choices and uses
  Clean without erasing the user's saved preference for other schools.
- Prior full run `pontora-regression-20261116-corrections`: 6,284 application
  tests passed; migrations and overall coverage passed. The module gate failed
  on demo seeding (88.64%); JavaScript was not reached. Added password-guard and
  optional-camp idempotency/scoping tests instead of weakening coverage gates.
  The focused local/hosted seed and reset run passes all 25 tests and reaches
  100% statement/branch coverage for that module.
- Verification: 205 focused Python tests, 23 feature-flag checks and 15 focused
  JavaScript checks passed. Manual Chrome checks covered desktop, tablet portrait
  768x1024, landscape 1024x768 and phone 390x844. Verified live review, reachable
  controls, 2-open display with a five-seat edit limit, disabled plus at the cap,
  and absent Plushie theme choices. No booking changes or emails were submitted.
  Physical Android/virtual keyboard checks remain pending.
- Fresh full regression is tracked in
  `/private/tmp/pontora-regression-20261116-session-limits/status.json`, with logs,
  exit status, revision and working-tree fingerprint. Treat it as pending until
  checked; do not promote this batch based on the earlier run.

### November 16: Selected Session While Browsing Weeks

- The shared reservation picker now shows a compact selected-session date/time
  and seat limit whenever that session is outside the displayed week. Browsing
  does not change the selection; choosing a different session updates the limit
  and removes the redundant summary when the selected row is visible.
- Nine targeted JavaScript tests passed, including week navigation, empty weeks,
  retained selection and switching from a five-seat limit to eight. Manual Chrome
  checks covered desktop, 768x1024, 1024x768 and 390x844; physical Android remains
  pending. Existing Calendar and Reservations callers share the picker.
- The preceding full run was stopped before editing at the owner's targeted-only
  request (application exit -15, source unchanged, logs retained under
  `pontora-regression-20261116-session-limits`). It is interrupted, not a pass.
  No new regression or push for this change.

### November 16: Workshop-Wide Seat Limit and Editor Consistency

- Supersedes the selected-session stepper cap above: the shared reservation
  picker permits up to the largest available booking capacity among its workshop's
  selectable sessions, across weeks. The current reservation's own seats count
  only toward its current session's editable capacity, not its open-seat label.
  Sessions that cannot fit the requested group remain disabled, and an unsuitable
  retained selection blocks Save with a visible explanation. Backend capacity
  validation remains unchanged.
- Audited notification controls in single-session changes, recurring edits and
  bulk schedule/cancellation forms. They reuse one Notify customers partial,
  positioned after the impact review. Cancellation consent retains its existing
  explicit choice. Recurring changes now highlight old/new times and capacities.
- Participant inline fields stay inside the reservation table; tablet layouts
  move notes below the main workspace. Narrow screens scroll the table locally
  instead of overflowing the page. Shared Reservation and Workshop Prep rendering
  uses the same styles.
- Verification: 146 targeted Python tests and 13 targeted JavaScript checks
  passed. Manual Chrome checks covered desktop 1440x1000, tablet 768x1024 and
  1024x768, and phone 390x844, including editable selections, reachable Cancel,
  page bounds, workshop-wide limits and invalid/valid destination selection.
  No booking writes or emails were sent. Physical Android remains unverified.
  Full regression remains interrupted as recorded above; no new full run or push.

### November 16: Full Regression and Staff Save Label

- Full run `pontora-regression-20261116-final-84fgXs` completed in 23m12s with
  unchanged source. All 6,288 application tests and migration tests passed;
  aggregate Python coverage was 97.90%, and every-module gates passed.
  JavaScript failed one stale assertion expecting `Create booking` instead of
  the intended staff `Save` label. Updated only that expectation; all six party
  inquiry tests pass, including unchanged customer payment/request labels.
- Full replacement run logs/status:
  `/private/tmp/pontora-regression-20261116-save-label-IIKXFW/`.
  Verification is pending until this exact run completes. No push or deployment.
