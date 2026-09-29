# Kid Works tour demo

## Scope and impact

- Only `kidworks-childrens-center` opts into appointment capture and tour labels.
- The public `/schools/kidworks-childrens-center/lead/` form offers three sample
  Pacific-time slots (October 6, 8, and 9, 2026). Submitting a valid slot immediately
  sets the existing `trial_scheduled` status, displayed as **Tour Scheduled**.
- The Leads list shows the selected date/time. Lead Details shows it below Actions
  and allows editing the slot. **Tour Completed** uses `trial_completed`.
- Shared views/templates have opt-in branches; defaults remain unchanged.
  SBMC's YAML, trial booking redirect, model statuses, enrollment, billing, and
  notification implementation are not changed. No migrations or data backfill.
- Existing leads are not assigned invented appointments. Follow-up edits preserve
  saved slots. Historical slots remain editable after sample availability changes.
- The list's public-form link uses `/lead/` for appointment-enabled schools;
  the legacy `/interest/` endpoint and external webhook intake remain unchanged.

## Deliberate limitations

These are demo slots, not a live booking calendar: there is no capacity enforcement,
calendar synchronization, or reservation conflict checking. Update sample dates
before a later demo. Existing notification behavior is retained; no calendar invite
is added. Appointment data is kept in the lead's existing custom-field JSON.
The existing lead-intake behavior permits multiple records for the same email.

## Admin tour scheduling

The Tour date & time box on Lead Details now includes the same offered slots as
the public form. Owners/editors can Schedule Tour for an unscheduled lead or Save
Tour to reschedule. Saving automatically sets Tour Scheduled when auto-confirm
is configured, and changes no contact information, notes, or follow-ups. The
existing Edit Details tour field also advances the status when a slot is saved.

Completed, enrolled, lost, and converted leads cannot be moved backward by a tour
save. Viewer/cross-school requests are rejected. Repeated identical sidebar
submissions are idempotent and booking changes are recorded in Activity. No
calendar integrations or new notification emails are triggered by this action.

## Playdate scheduling and admissions

- Kid Works' parent status page uses a dedicated Playdate Schedule section instead
  of the shared lesson-preferences form. There is one dropdown for a one-hour,
  weekday visit. Sample dates: October 12, 14, and 16, 2026, in Pacific time.
- The server accepts only configured future weekday slots within 8am-5pm. Saved
  appointments remain visible after their slot expires. This is demo availability,
  not a real calendar or a capacity-controlled appointment system.
- Saving sets **Playdate Scheduled** and preserves other application data. Repeat
  identical submissions are idempotent. Parents can reschedule until staff mark
  Playdate Completed; later, waitlisted, and closed states reject parent changes.
- Staff can schedule or reschedule using the same offered times in the Playdate
  Schedule box below Actions in the Submission Details sidebar. Parents and staff
  share validation and status-lock rules; owners/editors can save, viewers cannot.
  Advanced statuses show the saved appointment without editing controls.
  No acknowledgment or pending scheduling alert is required;
  scheduling history is retained in the audit log. No playdate emails are sent in
  this demo (`notify_school: false`). SBMC's notification behavior is unchanged.
- Kid Works submission choices: New, In Review, Playdate Scheduled, Playdate
  Completed, Fee Pending, Enrolled, Waitlisted, Declined, Archived. Contacted,
  Needs Follow Up, and tour statuses are removed from the new choices. Historical
  records are not rewritten and remain accessible in the full submissions list.
- `admin.require_enrollment_approval` prevents Kid Works' application intake from
  auto-enrolling, even if an existing program/session has auto-enroll enabled.
  Both public submission completion paths pass this config to the shared service.
  Other schools default to their existing program/session behavior.
- **Fee Pending is a status only for now**, per the user's decision. The existing
  upfront application-fee mechanism is unchanged. Follow-up work: post-approval
  fee checkout, verified/idempotent payment completion, and enrollment only after
  approval and payment. No automatic payment-to-enrollment transition is added here.
- Parent status messages, application success instructions, and confirmation emails
  now distinguish application receipt from enrollment and describe the playdate,
  availability review, and later paperwork/payment steps. Timezone suffixes are
  hidden in playdate labels, but timezone-aware validation remains unchanged.
- Separate remaining gap: lead conversion still marks a linked Lead Enrolled when
  an application is submitted. The Submission approval guard does not change that
  Lead status. A distinct Application Submitted handoff state needs separate work,
  including filters/counts; it is not implemented by this patch.

To test: open a New/In Review Kid Works submission as school admin, follow its
family status link, choose a playdate, and return to Submission Details. Verify the
slot and Playdate Scheduled status. Advance to Playdate Completed, then Fee Pending.
The parent should still see the saved slot but should no longer be able to change it.
Also start with an unscheduled submission and schedule directly from the admin
sidebar; the parent page should show the same appointment and updated status.

Playdate tests: `core/tests/test_kidworks_playdates.py` covers validation, repeat
POSTs, tenant isolation, CSRF, parent status locks, admin transitions, demo email
suppression, approval gating, and SBMC's original lesson scheduling behavior.

## Local check

1. Open `http://127.0.0.1:8001/schools/kidworks-childrens-center/lead/`.
2. Enter sample parent details, choose a tour slot, and submit.
3. Sign in as the Kid Works school admin and open Leads. Confirm **Tour Scheduled**
   and the **Tour date & time** column.
4. Open the lead. Check the Tour date & time panel below Actions, update a note,
   and verify the appointment remains. Select **Tour Completed** in the pipeline.
5. In SBMC, confirm trial labels, trial booking, and enrollment remain unchanged.

## Verification

- Admin playdate/domain/copy follow-up: **1,865 core tests passed**, one known
  PostgreSQL-only migration test deselected on SQLite. External networking was
  blocked. The focused run passed 86 playdate/domain/application-payment tests.
  Isolated desktop/mobile browser checks verified admin booking and rescheduling,
  parent visibility and rescheduling, completion locks, Fee Pending, and layout.
  Screenshots were inspected; no horizontal overflow. The existing shared
  email-editor `hidePop` error remains unchanged. No production data, emails,
  Stripe requests, or paid API calls were used.
- Admin tour-scheduling follow-up: 320 tour, lead, SBMC, and domain-routing tests
  passed with external networking blocked. Desktop/mobile browser checks covered
  scheduling an existing lead with no slot, automatic status changes, and
  rescheduling without horizontal overflow.
- Sidebar/no-acknowledgment follow-up: 142 Kid Works, scheduling, and SBMC
  regression tests passed with external networking blocked. Browser checks
  confirmed the box below Actions, no acknowledgment button, and no desktop/mobile
  horizontal overflow. Existing legacy playdate alerts are hidden without a data
  backfill; future playdate saves do not set a pending acknowledgment flag.
- Final tour + playdate regression run: **1,809 passed**, one known
  PostgreSQL-extension migration test deselected on SQLite. Network access was
  blocked during tests; no paid API calls or production data were used.
- 25 focused playdate tests passed. Desktop/mobile browser checks verified
  scheduling, rescheduling, admin completion, Fee Pending, and parent editing
  locks. Screenshots were inspected for layout and horizontal overflow.
- 21 focused tour/tenant-isolation tests added in `core/tests/test_kidworks_tours.py`.
- 195 focused and existing lead/SBMC/config regression tests passed.
- Broader `core/tests` run: 1,784 passed; one PostgreSQL-extension migration test
  fails on SQLite. The same failure was reproduced on unchanged HEAD.
- Browser exercised public submission, Leads list, Lead Details, and completion
  on an isolated local database with email disabled. Desktop/mobile screenshots
  inspected. No paid API calls were needed.
- Existing email-editor `hidePop` JavaScript error was observed on detail-page
  clicks: its script initializes before the popover HTML. That unrelated shared
  behavior is not changed in this tour work.
