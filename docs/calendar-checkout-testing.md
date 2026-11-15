# Calendar And Checkout Review

Status: local review only. Demo and production have not received this change.

## Paid Party Amendments

Use an upcoming confirmed party with a recorded payment. The local synthetic
**Calendar party UX check** is available on November 25 at 4 PM in Pasadena:
`http://localhost:8002/bookings/plushumi-demo/reservations/c2f90fea-96f0-45c2-afb7-ec5774ad9c09/`.
It currently has Package B, four participants, a $982.35 total, a recorded
$150 cash test payment and an $832.35 balance. It has a saved $10 pre-tax reduction.
No real card was charged. No local test email was delivered.

1. Open **Edit booking**. Change Package B to A, leave Price adjustment blank,
   and save. Review Total and Balance due; expand Price breakdown for tax and
   retained payment. A changed time or room remains visible outside the breakdown.
   Cancel once: nothing should change. Repeat and confirm.
   The existing $10 reduction carries forward; it is not silently discarded.
2. Change the participant count within the package allowance. Review still
   appears even if the total stays unchanged. Going above the included count
   charges the saved additional-participant price; exceeding the package/room
   maximum is rejected. Existing guest selections remain attached to survivors.
3. For a party with saved guest names/designs, reduce below the number of saved
   guests. Select exactly who is no longer attending. The remaining guests keep
   their names, selections and preparation marks. Increasing again adds pending
   guests rather than duplicating previous selections.
4. Enter a price adjustment: positive adds, negative subtracts. It is an
   **additional** change to the agreed quote, before package tax. Leave blank for
   no new adjustment. The note is optional and audit-only. Review and confirm;
   payment history and already-collected deposits must not change.
5. Reduce the total below payments received. Review must show **Excess payment**
   and zero balance. Saving does not issue a refund. Use the existing Refund
   action separately for supported online/Terminal payments; return recorded cash
   or externally collected money through its original method.
6. Open **Statement** and **Receipt**. Check current package/count, total, payment
   history and balance. Choose **Email**, the payment reminder template, and
   **Booking statement (PDF)** to send the updated request. The payment button
   requires an eligible connected payment account; it is deliberately unavailable
   on this account-less offline sample. Local emails are captured as drafts in
   the audit log. Automated tests cover an eligible account's revised payment link
   and rejection of a stale pre-edit amount.
7. From Calendar, open the party, then its row's Edit action. Make and confirm a
   count change. The dialog closes, the same calendar week stays selected, and
   the event reflects the revised count. Check the same on tablet portrait and
   landscape. No extra toolbar or separate party billing screen is introduced.
8. Open Edit in two tabs. Save one, then attempt the second: it must reject stale
   details. A payment/refund still processing also blocks edits until reconciled.
9. Open the 7-person Nov 24 party in Calendar. It is an old unconfirmed local test
   hold with an unresolved checkout. The badge must say checkout needs verification,
   not Reserved. Edit is disabled with a visible reason; View opens the reservation
   for payment review. Do not treat it as a paid/confirmed test booking or force
   its state to make it editable.

Statement means the current booking total, payments/refunds and remaining balance.
Receipt is available after payment is recorded and documents money received; a
deposit receipt does not imply the whole booking is paid. Both reuse the saved
quote and payment ledger, not today's package defaults.

Implementation boundaries: same offering/location only; future confirmed parties
with unused materials. Missing/inconsistent legacy package quotes require price
reconciliation before commercial edits, but contact/time editing remains available.
Package swaps adopt their configured room, space and duration, subject to capacity.
No automatic refund, additional charge, or bespoke email pipeline is introduced.
Current automatic messages are not a substitute for the pending dedicated
reschedule/cancellation notifications.

Verified locally in desktop Chrome at 1440x900, 768x1024, 1024x768 and 390x844:
package upgrade after payment, capacity rejection, downgrade review/cancel,
calendar inline save/refresh and statement-email capture. Physical Android,
virtual-keyboard behavior and printing remain unverified. Full regression status
is recorded in `party-review-clarity-20261114/status.json` in the task visualization
workspace; it is not a release pass until that exact run completes successfully.

## Staff Intake Simplification And Current Scenario Audit

November 14 follow-up; this section supersedes the older November 12 scenario
table below. Those entries document historical verification, not current scope.

### Quick Review

1. Calendar > Add booking > Party: select package and offered time. There is one
   Number of participants field, no Estimated participants and no package summary.
   Try exceeding available capacity: the warning appears before submission and Save
   is disabled. Reduce the count and complete customer details; saving stays on
   Calendar. Package selection still names the package and price; this is not a
   second quote or an automatic charge.
2. Calendar > Add booking > Workshop > New session: enter a future time, duration,
   room and capacity. There is no publication checkbox. Leave customer fields blank,
   save, navigate to the chosen week and open the event: it is Published with zero
   reservations. Repeat with customer details to create both together.
3. Existing sessions can still be closed to new online bookings without canceling
   their customers. Pre-existing drafts remain drafts; this is not a bulk publish.
4. Cancellation without payment needs only confirmation and an optional note.
   Paid cancellations still require an explicit refund decision. Repeated submission
   must not issue another refund or reinterpret the original decision.

### Current Practical Scenarios

| Scenario | Result / limitation |
| --- | --- |
| Phone booking into an existing workshop | Supported; fixed time from roster, contact/count intake, shared capacity and pricing. |
| New workshop with no customers yet | Supported; automatically published, no reservation/order created. |
| New workshop plus first customer | Supported in one transaction; invalid customer or capacity rolls back both. |
| Two groups in one workshop | Separate customer bookings and a shared seat limit; concurrent last-seat and retry tests pass. |
| Package party booked by staff | Supported; one count, shared package validation, no consent recorded for the customer and no payment invented. |
| General custom-party configuration | Existing request/quote review retained; staff summary removed, not the financial approval step. |
| Customer supplies names/ages/designs later | Existing private participant link and staff editor retained; material allocation remains in shared services. |
| Collect payment, prepare and complete | Existing payment ledger, Studio Prep and completion flow; automated local journey tests cover this, not real hardware. |
| Correct one customer's contact, move workshop booking or change count | Supported before start; same-offering destination, capacity checks, preserved payment evidence and count-price review. |
| Cancel one customer, paid/unpaid/repeated submission | Supported with existing refund safeguards; other customers/session remain. No reason required. |
| Package/participant/price changes to a paid party | Supported locally through Edit booking; review, agreed-price handling, retained payments and revised statement/payment email. See Paid Party Amendments above. |
| Change or cancel an entire booked session | **Pending:** impact preview, atomic updates, conflict checks, explicit refund handling and automatic customer notices. Existing protection still blocks unsafe time/room changes. |
| Move all upcoming Sundays, with exceptions | **Pending:** bounded recurrence/bulk scope, review affected reservations, stale-preview protection and retry-safe notifications. |
| Notify customers of individual reschedules/cancellations | **Pending:** current automatic message is confirmation; use the existing manual Email action until change notices are implemented. Already imported personal calendars do not update automatically. |
| Remove an accidental empty session | **Pending:** calendar removal action. Closing online intake is not removal. Preserve audit/history when introducing this. |
| Walk-in after a workshop has started | **Gap:** intake/editing currently requires a future session. Decide the permitted late-arrival window before expanding this rule; no bypass added. |
| Move to another workshop type or location | **Gap:** individual workshop moves currently stay within the same offering; cross-offering repricing/material changes need an explicit policy. |
| Unassigned room | Allowed today; room-specific clash checking requires an assigned room. Studio-wide exclusive claims are still checked. Do not claim an unassigned session reserves a specific room. |

Verification: 351 focused backend tests passed, including SBMC and customer forms.
Chrome responsive emulation: desktop 1440x900, tablet portrait 768x1024, landscape
1024x768 and phone 390x844; form controls/actions remain reachable with no dialog
horizontal overflow and 44px buttons. Entered/selected values and tested capacity
recovery, then saved a party from phone layout without leaving Calendar. Created
an empty session from tablet layout and observed Published in its roster. No console
errors observed. Physical Android, virtual keyboard and printing remain pending.

Local synthetic examples: `Intake layout review`, Package A, November 24 at 4 PM,
one participant; and an empty Plushie-making workshop, November 20 at 2:30 PM,
four places. No real payment/email was used. Do not change real customer records
for testing. The detached full run is recorded in the release tracker and is not
a release pass until its status and exact-source fingerprint have been checked.

## Calendar

1. Open the Plushie admin Dashboard, then Calendar. Choose a location and a week.
2. Click/tap an empty half-hour slot in Day or Week. Choose the workshop and check
   the prefilled date/time. Set duration, room and capacity. Leave customer details
   blank to create only a session, or enter name, email, phone and participant count
   to create the session and customer booking together. A booking is unpaid, not a
   collected payment. If that workshop already has a session at the exact time,
   the dialog books into it instead. Opening an existing session and choosing
   **Add booking** fixes the time; it does not ask you to select it again.
   In Month, open a day first. Toolbar Add booking offers existing times and an
   in-dialog **New session** link, without another calendar toolbar button.
   Booking follows existing native-reservation, catalog, price and deferred-selection
   settings; session creation still works when customer intake is disabled.
3. Create a draft time in Setup. Open it on the calendar, select **Edit session**,
   then **Publish session**. Reopen
   it and change its time or capacity. Save and check the calendar updates.
4. In Edit session, select **Close to new bookings**. This unpublishes the session;
   it does not cancel existing reservations or issue refunds.
5. Open a session with reservations. Change its time and try saving: the existing
   protection must reject it without moving customers. Capacity cannot go below
   reserved places either. Moving booked sessions is a separate follow-up.
6. Open a disposable test reservation's session/party. Use its cancel icon, review
   the customer name and payment handling, optionally enter a note and confirm. The roster
   and counts update. An active checkout can block cancellation until verified.
   Do not test refunding real payments. Cancellation uses the existing refund rules.
7. Select **Add booking**, then a **Party** option. Complete the existing package/time/customer form in the popup.
   Saving returns to the same calendar week; navigate to the booked week if different.
   The Cancel/back links also return to Calendar. Custom-party configurations still
   require their existing review step before they become reservations.
8. In the new reservation, edit a participant inline: name, design, color and age.
   Confirm the total does not change. The existing confirmation email includes the
   customer's private participant-details link; complete another participant there.
   Automatic delivery still follows the school's existing notification settings.
   Local preview captures a draft; it does not send a real email. Hosted demo
   confirmations go to the entered address, not a fixed test inbox.
9. Try a participant count exceeding remaining capacity. The dialog must retain
   the entered details and show an error, with no extra reservation or new time.
   Duplicate submission must not create another booking or confirmation.
10. Record a synthetic offline payment, prepare the participants in Studio Prep,
   mark prepared and complete the workshop through the existing review. Do not
   record cash against a real booking merely to test the UI.
11. Repeat opening, editing and canceling on a tablet in both orientations. Check
   the correct appointment opens when sessions overlap, scroll the calendar where
   needed, and confirm all dialog actions remain reachable with the keyboard open.

## Individual Booking Edits (November 14)

1. Open a session, then the pencil action beside one customer. Change the contact
   name/phone and save. Open reservation details and Studio Prep to check the result.
   The same **Edit booking** action is available in reservation details.
2. Choose another upcoming time for the same workshop. Save. Only this reservation
   moves; its existing participant selections, notes, links and payments remain.
   The destination cannot exceed capacity or conflict with other room bookings.
3. Increase Participants. Saving first shows the revised total and balance. Confirm
   to apply it; new people are awaiting selections. Cancel leaves the price/count
   unchanged. Pricing uses the booking's agreed per-person price, not today's defaults.
4. Reduce Participants. Only then does **Who is no longer attending?** appear; choose exactly who
   is leaving. Review and confirm. Retained people keep their choices; removed
   people's reserved materials are released. An overpayment is shown, not refunded
   automatically. Legacy bookings lacking reliable per-person pricing allow contact
   and time edits but reject count changes rather than invent a price.
5. For a party, Edit booking changes contact details/date/time while retaining its
   duration, package, price and payments. Package/count/price amendments are the next
   work item, not included here. Room conflicts must reject the move.
6. Open two edit windows, save one, then save the older window. It must ask you to
   reopen rather than overwrite newer information. Processing/uncertain payments
   or refunds block changes until reconciled. Repeated submission is idempotent.
7. Cancel a disposable individual booking from its roster action. Only that
   booking is canceled; other customers and the session remain. Existing refund
   choices apply. No automatic refund is implied by a move or count change.

Individual edits do not automatically email a reschedule notice in this pass;
use the existing Email action. Automatic customer notices for confirmed bulk
changes, whole-session changes/cancellation and recurring scheduling remain later
items. Add-to-calendar links do not update an already imported personal calendar.

Local review examples: Pasadena, November 17, 2026 at 10 AM has the synthetic
`Calendar review Nov 14` booking (three participants). The November 18 10 AM
session remains empty after moving and canceling a second synthetic booking.
Use another available time when testing creation; do not modify real bookings.

## November 14 Calendar UX Follow-Up

1. Use **Add booking** for workshops and parties. Workshop times are selected
   before entering customer details; **New session** stays inside the popup.
   Saving a session, customer booking, party or edit keeps the calendar/date/location.
2. Pasadena, November 19 at 2 PM contains the synthetic `Calendar UX check`
   booking in a full one-place session. Opening its roster disables Add booking.
   Selecting this time from toolbar Add booking warns before showing customer
   fields. **Choose another time** returns to the chooser inside the popup.
3. Booking counts appear first in calendar events, separately from occupied places.
   The roster has aligned View/Edit/Cancel icons with labels and 44px targets.
4. Increase a disposable booking's participant count: no removal checklist appears.
   Decrease it: select precisely who is leaving, then review the revised price.
   No payment, refund, or customer selection is silently overwritten.
5. Cancel an unpaid synthetic booking with a blank note: a short confirmation
   releases only its places. Paid bookings still require an explicit refund choice.
   **Keep reservation** does not change anything. Never refund live funds for testing.
6. In the party popup, choose a package and time. Remaining shared-room places appear
   immediately; an oversized group disables Create booking. Capacity is rechecked
   on save, since another staff member can book while the form is open.
   The synthetic `Calendar party UX check` booking is on November 24 at 4 PM.
7. Repeat with phone and tablet layouts, including scrolling the long party form,
   selecting options, saving and canceling. Actual Android Chrome and its virtual
   keyboard remain an owner/device acceptance check, not proven by emulation.

Verified locally in Chrome at 1440x900, 768x1024, 1024x768 and 390x844: creation
stays on calendar, two customers in one session, count increase/reduction with
financial review, optional-note cancellation, full-session blocking, party form
validation/save, paid cancellation layout without submission, and no horizontal
dialog overflow. No real payment or outbound customer email was sent.

## Chrome Checkout

1. In regular Chrome, make a new synthetic workshop booking locally and proceed
   to payment. Use only Square sandbox configuration and approved test card data.
2. Card fields should load and Pay should become available. Submit blank details
   first: a validation error should appear without a charge.
3. A blocked or stalled card initialization now stops after 20 seconds and offers
   **Reload secure form**. Reload must not submit a charge. Submitted payments with
   an uncertain result still require status verification, not another blind charge.
4. Report the URL, browser version and visible error if loading fails. Do not
   disable browser security protections. Private Chrome is a diagnostic comparison,
   not a required customer workaround.

## Isolation And Release Gates

- No customer-specific branches or new flags. Calendar actions use existing school
  capabilities, roles and location restrictions. Viewer roles cannot write; a
  scheduler can edit sessions but cannot invoke owner-only cancellation.
- No enrollment, Leads, submission, Stripe, subscription or school configuration
  changes. SBMC-focused regression tests passed; that is evidence, not a guarantee
  of zero shared-code risk. Full regression must pass on the final source before push.
- Production remains manually pinned. Review all intervening commits before a
  production promotion, not just this batch. Pushing main deploys demo after CI.
- Real Android Chrome, virtual-keyboard behavior, physical printing and live Square
  card/Terminal verification are not established by desktop responsive emulation.

## November 12 Click-To-Create Verification

- 182 targeted backend tests passed, including calendar actions, authorization,
  refunds and SBMC regressions; 23 calendar/editor JavaScript tests passed.
- In-app browser: 1440x900 desktop, 768x1024 tablet portrait, 1024x768 tablet
  landscape and 390x844 phone. Verified blank-slot click, preserved half-hour time,
  location-scoped choices, draft save at 2:30 PM, month-to-day navigation,
  Escape/Cancel, and visible form actions. No physical Android/keyboard claim.
- Fixed an existing event-loader double-completion error found during inspection:
  callback-based event loading no longer also returns a promise to the vendor.
  Reload and day/week/month switching produced no new console errors.
- Full regression passed in `calendar-click-create-20261112/status.json` in
  the task's visualization workspace: 6,056 application tests, 25 migration tests,
  three skips, 176 JavaScript tests and all coverage gates. Source was unchanged.
  The earlier `calendar-checkout-20261112`
  run failed one refund fixture after laptop sleep; its 6,055 passes do not prove
  this revision. The fixture now freezes only its simulated payment setup clock
  and requires paid evidence. Production payment expiration is unchanged.

## November 12 Customer-Booking Verification

- Existing and new-time customer bookings were exercised through the calendar.
  A full synthetic journey covered staff participant editing, customer-link editing,
  offline payment recording, preparation and workshop completion. No real charge
  or email was sent. The delivery worker is separately tested with mocked transport.
- Chrome viewport dimensions were verified from the rendered page: 1440x900,
  768x1024, 1024x768 and 390x844. Checked populated dialogs, half-hour/location
  prefill, capacity errors, horizontal grid scrolling and inline save. The in-app
  browser did not honor its requested viewport override in this follow-up; its
  screenshots establish desktop behavior only. Chrome provides the responsive
  evidence. Actual Android hardware and virtual-keyboard checks remain pending.
- Targeted backend checks cover retries, concurrent last-seat booking, invalid
  customer data, stale prices/times, permissions, location isolation, unchanged
  public draft restrictions, real-studio payment-account requirements and email
  delivery. Final staff/email/calendar batch: 44 passed. Earlier relevant backend
  batches passed 144 and 335 tests (overlap; not a unique aggregate count).
- That full regression passed: 6,079 application tests, 25 migration tests,
  three skips, 178 JavaScript tests and all coverage gates. Source fingerprint:
  `76fcb392b365b05519a1da7ef7b7ba42ba19e277da0865642825ef21b0abc4a8`.
  It predates the simplification below; nothing has been pushed or deployed.

## November 12 Simplification And Practical Use Cases

Jasmine's email explicitly asks to add/remove/edit appointments on the calendar
and separately asks about recurring Sunday schedule changes. Interpreting
appointments as customer reservations is reasonable, but she has not explicitly
defined every scenario below. Do not equate creating supply with booking a customer.
No right-click/long-press requirement or additional toolbar buttons were introduced.

| Scenario | Current behavior / remaining work |
| --- | --- |
| Customer calls to join an existing workshop | Tap its calendar event, Add booking, enter contact and count; time preselected. |
| Another group joins the same workshop | Separate reservation on the same session; shared capacity checked atomically. |
| Staff starts from Add booking | Choose workshop/location and a future existing time; no extra session created. |
| Staff taps a blank half-hour cell | Same popup; exact existing time preselected, otherwise available times and clear no-match message. |
| No workshop times exist | Explicit empty state and existing Setup link; never silently create availability. |
| Full session, invalid input, duplicate click, stale price/time | Validation/retry guards; no partial reservation, duplicate email or overbooking. |
| Customer has not chosen names/ages/designs/colors | Deferred placeholders; staff inline editor or private customer link. |
| Payment now or later | Confirmed unpaid reservation; existing payment, Terminal and recorded-payment flows. |
| Cancel one customer's reservation | Visible Cancel beside their roster entry; review and confirm; other groups and the session remain. |
| Paid or partially paid cancellation | Existing keep/refund choice and refund safeguards. No irreversible delete or invented refund. |
| Payment still processing | Cancellation blocked until payment outcome is verified. |
| Completed booking | No calendar Cancel action; separate payment adjustment, never erase attendance/history. |
| Cancel last customer | Workshop remains available; Hide empty sessions can hide it. This is not cancellation of the workshop itself. |
| New party booking | Existing separate package booking flow from the calendar; no ordinary-workshop shortcut. |
| Change empty session time/capacity/publication | Existing calendar Edit session and Setup. Closing to new bookings does not cancel customers. |
| Move one workshop reservation to another time | **Pending**: atomic capacity/material transfer, retained payment evidence and explicit price policy. Do not pretend cancel/rebook is equivalent. |
| Change an existing workshop group's participant count | **Pending**: capacity, material allocations and quoted-price adjustments with audit. |
| Correct workshop purchaser contact after saving | **Pending**: scoped contact editing, audit and confirmation-recipient handling. Participant name editing is not purchaser editing. |
| Move/cancel a whole booked workshop | **Pending**: affected-customer preview, payment handling and notifications. Current time/room edit remains blocked. |
| All future Sundays change, with exceptions | **Pending**: recurring/bulk scheduling scope and conflict preview; not implemented by this popup. |
| Automatic cancellation/reschedule email | **Pending** for workshops; current automatic notice is confirmation. Staff can compose existing manual emails. |
| Delete an accidentally created empty session | **Pending** as a calendar removal action. Unpublish closes public intake but leaves the record visible; it is not deletion. |
| Different locations, restricted staff, SBMC | Existing permissions/capabilities/location scope; no school configuration change. |

Verification for this simplification:
- Actual Chrome booking into an existing session, then cancellation of only the new
  synthetic customer. Calendar returned from three seats to two and retained the
  original reservation. No real payment or email sent.
- Rendered Chrome viewports verified at 1440x900, 768x1024, 1024x768 and 390x844.
  Popup actions are 44px tall; Cancel closes without saving; no horizontal page
  overflow. Roster cancellation is visible on tablet without hover/right-click.
- Standardized Cancel controls across 17 templates (including multiline links
  missed by the first search). Calendar dialogs use actual non-submit buttons;
  full-page navigation retains links with the shared button appearance. SBMC's
  only additional visible change is consistent Cancel styling on shared forms.
- Full regression for the final source is pending in
  `calendar-booking-cleanup-20261112/status.json` in the task visualization workspace.
  Physical Android, virtual-keyboard and printing remain unverified.
- Final targeted verification passed: 211 backend tests covering staff workshop
  bookings, calendar actions/read models, refunds and SBMC workflows; 178 JavaScript
  tests passed with the existing coverage gates. `git diff --check` passed.
