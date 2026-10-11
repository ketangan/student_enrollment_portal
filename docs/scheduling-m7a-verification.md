# M7a: Staff-Assigned Lesson Scheduling

October 2, 2026. Ready for local user testing; desktop workflow and mobile layout checks passed.
This is one M7 slice, not complete
music-school readiness. No commit, push, deployment or SBMC cutover is authorized.

## Scope

- A separate default-off lessons capability inside the existing Bookings workspace.
- Generic service/subject/duration configuration; eligible teachers and weekly availability
  by location; configurable teacher gap/travel time; optional existing rooms.
- Explicit whole-date lesson closures for a location or teacher. These do not cancel
  existing bookings or close workshop/camp registration. Closures overlapping scheduled
  lessons are rejected until staff cancel those lessons explicitly.
- Private family/student records independent of enrollment records. Staff can filter the
  combined schedule by family. No guardian login, public student data or family-link sharing.
- Finite weekly/fortnightly schedules ending within 180 days. Same first/last date permits a
  single appointment. One student, teacher, service, location and optional room per series.
- Preview lists every date, including closed/conflicting dates. Available dates reserve
  atomically; any conflict prevents the entire save. Closures remain visible as skipped rows.
- Staff cancellation of one or all future dates retains history and releases resources.
  It does not change billing, enrollment, notification state or make-up entitlements.

## Design Review

Recurrence uses pinned `python-dateutil`, not a custom date generator. It generates local
wall-clock dates, explicitly rejects nonexistent/ambiguous times, then persists UTC instances.
A lesson crossing a DST transition is rejected rather than silently changing its duration.
No indefinite recurrence or background horizon extension is claimed.

Every writer locks the existing tenant profile. Teacher/student/room conflicts are rechecked
under that lock after preview. Workshop publication and party reservation use the same lock
and check lesson room claims. Lesson creation checks published sessions plus active reservations,
including camp occurrence snapshots and unresolved checkout holds. Disabling a capability does
not erase its room claims. No teacher is implicitly assigned to an existing workshop.

Confirmation tokens bind user, tenant, field payload and preview revision, expire after one
hour, and provide idempotency for an exact retry. Changing policy/catalog/availability between
preview and commit requires another preview. Authority is checked again after acquiring the lock.
Audit failures roll back setup, the complete schedule and cancellation writes.

Existing commitments use snapshotted service, student, teacher, location, room, duration and
gap values. Renaming/editing catalog values does not silently rebuild lesson dates. Teacher
gap values and used availability windows are frozen for this release. Future exception/edit
work belongs to M7b, not ad-hoc row mutations.

## Impact Analysis

New code is in `scheduling/lesson_models.py`, `lesson_forms.py`, `lessons.py`, `views_lessons.py`,
lesson templates/tests and one additive scheduling migration. Existing Bookings-only changes:

- `SchedulingProfile.lessons_enabled` defaults false; conditional Lessons navigation and
  owner settings. Existing tenants are not enabled by migration.
- Session publication and party capacity checks reject rooms claimed by lessons.
- Location timezone edits lock after lesson availability/schedules exist.
- Existing access roles are reused: owners manage setup; owners/schedulers assign/cancel;
  viewers read. Teacher records do not grant account access. Families are not staff members.

No enrollment models, YAML, SBMC stylesheet, subscription flows, payment ledger, checkout,
Stripe facade, or existing MakeMusic redirect is changed. No second payment module is added.
M8 will price/collect recurring tuition through the shared payment foundation, independently
of lesson recurrence. M7a creates no PaymentOrder and never represents these lessons as paid.

Migration `scheduling.0007` is additive and only applied to isolated test/preview databases.
Existing camp prices/open state, native reservation enablement and school plans are preserved;
lesson enablement remains false. Empty lesson tables can be rolled back. With real schedules,
stop new intake and use a forward fix; do not drop history as a rollback strategy.

## Verification

Final full run: **2,461 tests passed in 87.41 seconds**. The new lesson models, forms,
services and views have **100% Python line/branch coverage**. Combined scheduling/shared
payments coverage is **99.94%** (3,511 of 3,513 lines/branches). Coverage is not a claim of
complete behavioral coverage or production readiness. The two existing warnings remain.
Focused tests cover inclusive end dates, weekly/fortnightly cadence, DST gap/fold/duration,
closure scope, all-closed schedules, cross-tenant choices, CSRF, disabled/inactive access,
role restrictions, stale/forged previews, exact retry, concurrent lesson claims, simultaneous
lesson creation/workshop publication, additive migration preservation, shared
workshop/party/camp rooms, buffers/travel, audit rollback, immutable snapshots and cancellation.
All external network calls are blocked in test and preview environments; no paid API usage.

Manual desktop browser checks passed in Music Studio Demo with fictional people only:

- Added a violin service, qualified teacher and weekly availability; saved a lesson closure.
- Previewed and confirmed Riley's seven-week piano schedule. October 13 stays visibly closed;
  the six scheduled lessons stay at 3 PM across the November daylight-saving change.
- Attempted Jamie at the same teacher/time: each occupied date showed a conflict and no
  Confirm action was available. Changed to 4 PM and confirmed successfully.
- Canceled only Jamie's October 20 occurrence. Its reason/history remain, other dates remain
  scheduled, and the combined Park Family schedule includes both siblings.
- Reload and server-restart persistence checked. No charges or notifications were generated.
- At 390 x 844, the family schedule stays within the page with a horizontally scrollable
  table. All lesson form fields and buttons fit the viewport, with no document overflow.
- Refreshed the project viewer and expanded M7a: the manual steps and Emily note render.

Desktop evidence: `/tmp/pontora-m7a-series-desktop.png`.
Mobile evidence: `/tmp/pontora-m7a-form-mobile.png`, `/tmp/pontora-m7a-family-final.png`.
Full run and coverage artifacts:
`/tmp/pontora-m7a-final.log`, `/tmp/pontora-m7a-final-coverage.json`.
Schema drift check passed (`makemigrations --check --dry-run`: no changes detected).

The browser viewport control initially timed out, but the resize subsequently applied;
the actual 390-pixel viewport and field bounds were measured and screenshots inspected.
The explicit override was reset successfully afterward. Automated role/CSRF/privacy tests
passed; a separate manual viewer-account walkthrough is not claimed. No live-provider
verification or SBMC cutover is claimed. User acceptance and push authorization remain open.

## Limits and Next Steps

M7a is staff-only scheduling, not a replacement for SBMC's MakeMusic account. M7b adds reviewed
series changes, attendance and make-up rules. M7c adds verified guardian access/public booking.
M8 remains recurring billing. Group lessons, arbitrary recurrence rules, indefinite standing
rights, auto reminders, imports/calendar sync, external calendar busy time, make-up credits,
and rescheduling are not included here. Do not use two systems as authoritative sellers of the
same teacher/room pool. Names/emails are local fictional data, not copied from SBMC.

Teacher gaps affect sequential lessons; rooms are reserved for the lesson interval, not an
unconfigured room-cleanup interval. Availability follows location timezones. One teacher may
have availability at multiple locations, but actual lessons must respect travel gaps.
Shared teachers across separate business tenants and workshop teacher assignment are not supported.

Emily's actual self-booking, closure and cancellation/make-up rules remain unverified. The user
will obtain them later. A short ready-to-send note appears in the expandable M7a tracker row.
These questions do not block local setup tests, but do block claims of SBMC migration readiness.

## References

Reviewed the official [MakeMusic appointment/recurrence guide](https://help.studio.makemusic.com/en/articles/2059799-appointment-schedule-create)
and [appointment changes guide](https://help.studio.makemusic.com/en/articles/2059793-change-appointment-lesson-cancel-reschedule-price-change-absent-how-to-for-providers).
These support separating scheduling from billing and treating individual/series changes as
explicit actions; they are not evidence of Emily's settings. Recurrence behavior is based on
the [python-dateutil documentation](https://dateutil.readthedocs.io/en/stable/rrule.html).
