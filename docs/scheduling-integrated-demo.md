# Enrollment Request Demo: End-to-End Bookings

Updated: October 5, 2026. Local demonstration only.

## Start Here

Open [the school admin](http://127.0.0.1:8002/schools/enrollment-request-demo/admin/).
Username: **demo_admin**. Password: **admin**.
If another demo account is already signed in, sign out first and use
[the local login page](http://127.0.0.1:8002/login/).

The existing test-school slug is `enrollment-request-demo`; its display name is
**Riverside Learning Academy**. This is an isolated local copy on **port 8002**.
The original enrollment database on port 8001 and all customer databases are unchanged.

From the school navigation, choose **Scheduling**. To configure it, open
[Settings > Scheduling](http://127.0.0.1:8002/schools/enrollment-request-demo/admin/settings/?tab=scheduling).
The scheduling workspace has an **Enrollment admin** link to return to the school.
One ordinary school-owner login gives access to both; no superuser login is needed.

## What Is Prepared

| Area | Practice data |
| --- | --- |
| Locations | Main Campus, Welcome Room, Creative Studio, Piano Room, Camp Room |
| Tours and playdates | Four weekly slots each; Taylor Walkthrough is ready for attendance |
| Admissions | Completed tour releases application; staff releases playdate after reviewing the application |
| Workshops | Creative workshop, three sessions, project choices and color swatches |
| Requests and parties | Morgan Workshop request and Jordan Party's eight-person birthday inquiry |
| Camps | Three-day creative camp, $150 total, $50 deposit |
| Lessons | Alex Demo, weekday afternoon availability, piano lessons, Park Family with Riley and Jamie |
| Recurring schedule | Four weekly Riley Park lessons; room, teacher and student conflict checks |
| Make-ups | 24-hour notice policy and 90-day credit expiry |
| Trial/extra lessons | Published $30 piano offer, pay at studio, local booking preview |
| Payments | Shared payment ledger and local Stripe simulator; no real Stripe connection |

The initial preview dates run from October 12 through November 2, 2026. Choose dates shown
on screen rather than assuming today's calendar has appointments. The two attendance-ready
tours are intentionally in the past so the whole admissions flow can be demonstrated now.
These fixtures are examples, not any customer's approved operating policies.

## 1. Tour to Application to Playdate

1. Open **Scheduling > Tours** and select **Taylor Walkthrough / Casey Walkthrough**.
2. Record **Attended**. Taylor was left untouched for your walkthrough.
3. Follow **View enrollment lead**. The application is now released; choose **Start Enrollment**.
4. Complete the application as Casey Walkthrough. Use Creative Learning and Enroll now;
   the guardian/email are prefilled. Submit and keep the parent status page open.
5. Return to the school's **Submissions**, open Casey's submission, and use the
   **Admissions progress** panel to release the playdate.
6. Refresh the parent status page, choose an available playdate and confirm it.
   Expect the submission to become **Playdate Scheduled**, with the booked date and room.
7. In Scheduling, review the playdate booking and its linked application. Future attendance
   is deliberately blocked; after the visit starts, attendance completes the playdate stage.

For a new family's full booking experience, use **Tours > Customer page**. Book a future
tour with fictional contact details. This will create the connected lead, but it cannot be
marked attended before the visit starts. Robin Practice / Quinn Practice was used for the
verified tour/application/playdate walkthrough and remains available to inspect.

## 2. Change Tour and Playdate Setup

1. In **Settings > Scheduling**, open **Tours and playdates**. Edit a visit program or
   add slots, selecting the room, host, capacity and duration.
2. Use **Visit settings and closures** for family-change notice and closed dates.
3. Open **Tour-to-enrollment rules**. Change automatic versus staff application release,
   tour requirements and playdate eligibility. These rules belong to this demo school only.
4. Test an ineligible application or a conflicting slot: the form must explain why it cannot proceed.
5. For an existing family booking, use its customer/return access options to inspect the
   private booking, reschedule or cancel. Outgoing return messages are local previews only.

## 3. Workshops, Choices and Preparation

1. Open **Workshops > Creative workshop** and its customer page.
2. Choose a date and add participants with different project/color choices.
3. Submit the request, then open **Requests** as the admin.
4. Open the new request and check each participant and the preparation totals.
5. In workshop setup, edit the options, sessions, publication status or capacity.
   Unpublished offerings should not appear on the customer page.

Submitting a workshop request is not a paid, confirmed reservation. The admin must review
and create a reservation or quote. This distinction is intentional.

## 4. Reservations and Simulated Payments

1. Open a request and create a test reservation with a total and deposit. Enable test online
   collection, review the hold, and approve it.
2. Follow the customer payment link and use the local simulation page to complete the deposit.
   There is no card entry and no real charge.
3. Return to the reservation. Check confirmed status, verified amount and remaining balance.
4. Test the balance checkout, a cancellation or the refund controls on this fictional booking.
   Inspect the ledger; cancellation and refund are separate actions.

Morgan Workshop already has a verified demonstration: **$60 total, $20 simulated deposit
received, $40 remaining**. You can inspect it or use it to test the balance. A fresh workshop
request lets you start from the beginning. Stopping new reservations must not block collection
of an existing confirmed booking's balance.

## 5. Parties and Camps

1. Open **Parties > Jordan Party**. Review the group of eight, preferred time and exclusive
   space request. Quote it, choose a room and create a reservation. An inquiry alone is not confirmed.
2. Try an overlapping exclusive reservation: it must be rejected.
3. Open **Camps > Three-day creative camp**, then its customer page.
4. Register a participant and inspect the roster across all three required dates.
5. Complete the local deposit simulation and inspect the balance. Cancelling the registration
   should release every required date, not just one occurrence.

## 6. Lessons, Availability and Make-Up Credits

1. Open **Lessons**, then inspect Riley Park's four scheduled lessons.
2. Use setup to review Alex Demo, Piano lesson, weekday availability and Piano Room.
3. Preview a new recurring series before saving. Try a room or teacher conflict and confirm rejection.
4. Reschedule one lesson, then separately try changing future lessons; inspect the replacement history.
5. Cancel a sufficiently future lesson under the configured 24-hour policy. Inspect its make-up
   credit, expiry and audit history. Redeem it through the staff credit workflow.
6. Use **Family access** to select Park Family and its student scope, then inspect the local
   family preview. Confirm it does not expose another household's students.
7. Use **Trial and family booking offers** to inspect the published offer and its customer page.
   These lesson bookings are a local preview with pay-at-studio quotes, not online tuition billing.

## 7. Messages and Integrations

Open **Messages** to inspect saved confirmations and local retry/recovery behavior. Nothing
is sent to a real family. Use the preview/recovery controls, not a personal email address.

Open **Integrations** to change the independent booking source/destination and inspect field
mapping previews. Wix and Notion are optional choices, not a required pair. No external
delivery occurs and no real credentials are needed for this demonstration.

## Scope and Safety

- Enabled here: all implemented local scheduling capabilities and the optional enrollment bridge.
- Not complete: connected Wix/Notion delivery, real email, online lesson checkout, recurring tuition/packages and production onboarding.
- School feature grants remain operator-controlled. School owners configure features already granted;
  they cannot enable unpurchased modules or assign operator permissions themselves.
- SBMC and Kid Works remain opted out. Booking-only businesses still use their standalone workspace.
- No new migrations, production configuration, real payment, deployment or push is part of this setup.
- Only the isolated local `demo_admin` password was changed. Do not use this password in production.

## Engineering Verification

M9d adds permission-aware navigation and a Scheduling settings tab, not a second settings
implementation. Existing booking forms and payment services are reused. A repeatable seed
command is restricted to the named loopback preview database, refuses live billing/email
configuration, and refuses to change shared/operator passwords. Reruns preserve practice edits.

Automated verification and final browser results are recorded in the M9d tracker entry.
The seed adds no customer data. Browser checks use fictional families in the isolated preview.

Final regression: **3,076 tests passed**, two existing warnings, 198.94 seconds. All measured
scheduling/shared-payment/test-safety modules meet the 90% statement/branch coverage gate.
The new navigation and template-tag modules are 100%; the guarded seed command is 94.57%.
No new migrations were detected. Django reported only the existing duplicate Jazzmin tag warning.

Impact review: shared school navigation and Settings add links only for an enabled Bookings
profile and an authorized scheduling member. Standalone users get no enrollment link without
enrollment membership. Family program/choice labels are corrected only under the opt-in
admissions workflow; existing school rendering stays unchanged. Only this demo's YAML gains
tour/playdate labels and staff enrollment approval. Negative SBMC/Kid Works and permission
tests passed. The tours-only workspace empty-state and playdate agenda labels were corrected.

Manual verification covered school login and both navigation directions, the complete Robin
Practice tour/application/playdate journey, a simulated $20 deposit, lesson rescheduling preview,
camp dates, workshop choices and loaded assets, restart persistence, desktop settings and a
362px mobile settings view without page overflow. The final full suite includes the corrected
parent-status label regression and all tracker routes.
