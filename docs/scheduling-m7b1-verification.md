# M7b1: Staff Rescheduling and Attendance

October 3, 2026. Ready for local user testing. No commit, push, deployment or
SBMC cutover is authorized. This is not all of M7: make-up policies/credits remain M7b2,
guardian access/self-booking M7c, and recurring tuition M8.

## Scope

- Staff can move one future lesson or the selected and later scheduled dates in that series.
  Changes may include date, time, eligible teacher, location and optional room.
- Preview shows original and replacement dates. Every replacement must fit availability,
  closures, teacher gaps, student conflicts and shared workshop/camp/party room occupancy.
  All selected dates move atomically or none do; closed dates are not silently dropped.
- Original occurrences remain as Rescheduled, with the staff reason and links to their
  replacements. Replacement schedules link back to original history. Previously canceled,
  closed, past and separately rescheduled dates are not included in a later-series move.
- Attendance after a lesson ends: Present, Absent, No-show or Unmarked. A short reason is
  required; corrections retain before/after values and actor in the internal audit log.
- No charges, credits, refunds, notifications or enrollment transitions occur.

## Implementation and Self-Review

Changes create a replacement series using the original service/student/duration snapshot;
they do not rewrite existing appointments or silently adopt a changed service duration.
Selected dates shift by local calendar days, not 24-hour UTC offsets. Timezone/DST validation
and conflict checks reuse M7a's planner. The preview is signed and bound to tenant, actor,
source occurrence, fields and source revision; it expires after one hour. Exact retries reuse
the saved replacement. Commit rechecks access, eligibility and every conflict under the existing
tenant-profile lock shared by booking writers. Audit failure rolls back both sides.

Attendance uses the same lock and a signed version to prevent lost corrections. A stale
cancellation page cannot cancel later lessons once its selected occurrence has moved. Retrying
a cancellation cannot expand a previously completed cancellation's scope.

Review caught and fixed that stale-cancellation case. Browser testing found a visible label for
the hidden attendance revision; hidden fields now render without labels, with a regression test.
One test initially reused a cached service
object after changing the catalog; it now reloads through the same lookup as the real preview
request and verifies that the original duration is preserved. Checks intentionally reject an
intervening setup change rather than silently accept an outdated preview.

## Impact and Migration

Production behavior changes are confined to the optional Bookings lessons module: new change
forms/services/views, existing lesson planner/cancellation logic, lesson history templates and
scheduling routes. The local read-only tracker adds this report. No enrollment, billing,
payment provider, SBMC YAML/templates, MakeMusic links or shared login edits in this slice.
Existing lessons enablement remains default-off; only the fictional Music Studio Demo is enabled.

Migration `scheduling.0008_lesson_changes` adds predecessor/replacement links, attendance/note,
optimistic version and state checks. Existing schedules, reasons, snapshots, school plans and
enablement flags are preserved. Apply only to isolated test/preview databases in this milestone.
Reversal was tested before new history existed. After real rescheduling/attendance data exists,
use a forward fix; rollback would lose attendance/history and old checks cannot accept the
Rescheduled state. No production migration or customer data update has been performed.

## Verification

Focused run: 123 tests passed in 30.70 seconds, including migration preservation. Final full
run: **2,521 tests passed in 96.03 seconds**, with two pre-existing warnings. All lesson Python
code is **100% line/branch covered** (684 lines, 160 branches). Combined scheduling/shared
payments coverage is **99.95%** (3,772 of 3,774 lines/branches); the wider measured set, including
legacy Stripe/login and the document viewer, is 98.40%. Schema drift and whitespace checks passed.
External APIs are blocked in test and preview environments; no Google, Anthropic, Stripe or
customer-system calls were made. Full regression includes existing enrollment/disabled-school,
billing/login and booking isolation tests. No separate production SBMC browser visit is claimed.

Covered cases include single/future moves, closures, source exceptions, substitution, tenant and
role isolation, CSRF, stale/forged/expired confirmation, conflict after preview, DST date shifts,
teacher/student/room conflicts, concurrent room claims, idempotency, audit rollback, attendance
corrections and additive migration defaults. Coverage does not prove all business scenarios.

Manual browser checks in the isolated fictional Music Studio Demo passed:

- Occupied teacher time produces Conflict and no Confirm changes button.
- Riley's October 20 lesson moved to October 21 at 3 PM in Strings Room. Both original and
  replacement details link correctly; reason and original time remain. History persists after
  preview-server restart and a fresh read-only sign-in.
- Future-date preview shows five remaining dates and excludes October 13's closure and
  the already moved October 20 exception. This preview was not committed in the browser;
  multi-date commit and substitute-teacher behavior are covered by automated integration tests.
- A fictional past October 1 appointment was seeded using the normal booking service with a
  test clock. Staff saved Present, corrected it to Absent with a new reason and reloaded.
  The recorded outcome/reason persisted. No revision label is visible on the form.
- Local viewer account can read history and attendance but has no change/cancel/attendance actions.
- At 390 x 844 all reschedule fields/buttons fit; document width stays 390, and the preview
  table scrolls within its container. Desktop history and attendance screens inspected at 1280px.
  Temporary mobile override was reset.

Evidence: `/tmp/pontora-m7b1-history.png`, `/tmp/pontora-m7b1-change-mobile.png`,
`/tmp/pontora-m7b1-attendance.png`. Test/coverage artifacts: `/tmp/pontora-m7b1-final.log`,
`/tmp/pontora-m7b1-final-coverage.json`. The browser tracker includes M7b1 manual steps,
local credentials, estimates and the outstanding Emily questions. User acceptance and push
authorization remain separate from engineering verification.

## Limits

"This and later scheduled lessons" means this saved series, not an entire tree of previously
separated exceptions. Use linked history to find an individually moved appointment. No indefinite
recurrence or whole-history merge is provided. Attendance is staff-only and shown in lesson
details; the audit database retains corrections, but there is no dedicated audit-history screen.
There are no automated family reminders or cancellation/make-up eligibility decisions.

Emily's cancellation/holiday/make-up and family self-booking policies remain unanswered. The note
to request them is saved in M7a's expandable tracker row. These do not block this generic local
staff workflow, but do block adopting policy defaults or claiming SBMC replacement readiness.
