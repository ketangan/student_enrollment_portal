# M1b Impact and Verification

Status: accepted October 1 by the user's instruction to proceed to M2; no push authorized.
The evidence below records the original M1b handoff. M2 is tracked separately.

## Delivered Scope

- Owner-managed locations, rooms and workshops. Owner/scheduler-managed dated sessions.
- Saved capacity, local start/end times and optional room assignment; draft/publish controls.
- Read-only public schedule: only published workshops with future published sessions appear.
- Existing role enforcement remains server-side; hiding a button is not the access check.
- Configuration survives a fresh login, page reload and preview-server restart.
- No customer request, participant choices, preparation roster, reservation or payments yet.
  Those are later milestones. Public pages explicitly say registration is not open.

## Impact and Migration Review

M1b adds four models inside `scheduling`, scoped setup forms/services/views, Bookings-only
templates/CSS and tests. No M1b edits to enrollment forms, YAML, school views, billing,
shared login, shared navigation or existing-school records. The existing `School` remains
the tenant anchor; scheduling memberships do not grant enrollment access. All existing
schools remain default-off. M1a's already-reviewed settings/URL/login additions are still
in the uncommitted working tree and must be included in any eventual push review.

Setup edits append tenant-tagged entries through the existing `AdminAuditLog` model rather
than introducing another audit system. Writes and their audit entry are one transaction.
There are no external service calls or notifications in this setup workflow.

Migration `scheduling/0002_location_offering_room_session_and_more.py` creates only Bookings
tables, constraints and an index. It does not alter core tables or migrate existing data.
Applied only to the isolated `pontora_bookings_m1a_preview` database; not the main local
database, demo or production. Forward/reverse/reapply integration testing preserves the
core school and M1a profile. Schema drift check reports no additional migration needed.

Operational rollback is to disable the affected scheduling profile. Reversing 0002 removes
all new setup data, so do not use schema reversal on non-disposable data without backup and
explicit approval. Retaining default-off tables is safer than destructive rollback.

Unrelated work in `core/views_demo.py`, SBMC homepage assets/docs and the nested worktree
was left intact. No commit or push was made. Tested working tree is based on
`7ccd62b02ee2c4aeb0621ae4a82cd50af9034cf6`; checks must be repeated on the outgoing candidate
before a future push.

## Automated Verification

Test date: October 1, 2026 (America/Los_Angeles). PostgreSQL 16 on loopback, using dedicated
disposable `test_pontora_bookings_m1a`, real Django requests/auth/forms/templates and actual
database constraints. External network access blocked; local HTTP is allowed for the
read-only documentation-viewer tests. No paid APIs, mail, provider bookings or charges.

| Check | Result |
| --- | --- |
| Scheduling unit/integration/migration cases | 118, including 56 M1b additions |
| Final full regression suite, including viewer-link check | 2,067 passed, 2 warnings, 42.85 seconds |
| Scheduling production-code coverage | 100% lines and branches; 412 statements, 74 branches |
| Combined scheduling plus legacy login coverage | 98.17%; legacy login file 90.43%, unchanged in M1b |
| PostgreSQL concurrent publication | Two simultaneous overlapping publications: one succeeds, one rejected |
| Migration drift / patch whitespace | Passed |

The final run includes the new allowlisted browser verification page and its link rendering.

Coverage is measured without test files. Assertions cover valid CRUD persistence, invalid
fields/time zones/capacities, server-owned foreign keys, tenant and role isolation, CSRF,
private drafts, publishing prerequisites, room conflicts and adjacency, DST missing/ambiguous
times, time-zone storage/display, immutable parent relationships, audit rollback, database
constraints, migration round trips and existing-school isolation. Branch coverage does not
prove completeness or provider compatibility.

Verification command (temporary local harness, not deployed configuration):

```sh
env PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings \
  COVERAGE_FILE=/tmp/pontora-m1b-final.coverage venv/bin/python -m pytest \
  -p pontora_local_only_tests --cov=scheduling --cov=core.views_login --cov-branch \
  --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing \
  --cov-report=json:/tmp/pontora-m1b-final-coverage.json --cov-fail-under=90 \
  --maxfail=0 --tb=short --durations=10
```

Logs: `/tmp/pontora-m1b-full.log`, `/tmp/pontora-m1b-final.log`. The temporary harness and
preview settings are local verification artifacts. Fast password hashing is confined to
the test/isolated preview configuration, not the application's production policy.

## Browser Verification

Actual browser interaction on port 8002, synthetic accounts and data only:

- Logged in as Bookings owner; created Main Studio, Room 1 (capacity 12), a workshop and sessions.
- Publishing before an upcoming published session shows a clear error.
- Session capacity 15 is rejected for a room holding 12; capacity 10 saves successfully.
- Published a session then its workshop; the public page showed the correct local time,
  room and capacity, with no staff controls or private draft session.
- Created an overlapping draft; publishing it was rejected. Edited it to a later time,
  saved and reopened it. It remains a draft alongside the published morning session.
- Viewer can inspect setup but has no editing/publishing controls. Direct edit URL returns 404.
- Existing SBMC account still lands on enrollment Dashboard; Leads works with unchanged navigation.
- Existing Kid Works account still lands on enrollment Dashboard; Submissions works with unchanged navigation.
- Desktop 1280px, mobile 390px and small-screen 320px checked. Forms fit, dates/actions are
  usable, no document-width overflow; navigation/table regions scroll where appropriate.
- Reload and full preview-server restart preserve the workshop and both sessions.
- No browser console errors on the final check. Deliberate unauthorized request returns 404.

Browser testing found a real issue that the first test run missed: the new-location time-zone
select used its first alphabetical option rather than the model default. Fixed the form's
initial selection and added a rendered-response regression test before the final full run.

Screenshots: `/tmp/pontora-bookings-m1b-desktop.jpg`, `/tmp/pontora-bookings-m1b-mobile.jpg`.
Preview log: `/tmp/pontora-m1a-server.log` (same isolated server as M1a).

## Critical Self-review and Limits

- All supported setup/publication writers lock the tenant profile, reload state, re-check
  permission and validate inside the transaction. A real concurrent PostgreSQL test verifies
  that overlapping room publications cannot both succeed through this boundary.
- This is setup-level conflict protection, NOT a general reservation engine. Direct ORM writes
  can bypass model validation. No new setup models are exposed through Django admin; future
  import/provider/reservation writers must use the service or stronger resource constraints.
  Resource claims, seat holds, payment confirmation and retry reconciliation remain M5 gates.
- Rooms are optional. Without a room, no room-exclusivity claim is made. Capacity is the
  configured limit, not a live count of remaining seats. Staff/instrument conflicts are not built.
- Unpublishing a workshop hides it publicly; it does not unpublish its sessions or cancel
  their internal room allocation. Draft sessions may overlap. Adjacent published sessions
  are allowed; buffers/closures and resource calendars are later work.
- Location time zone locks after any session is created. Saved room/workshop locations cannot
  be moved. Room capacity cannot shrink below any associated session's capacity, including
  past/draft sessions. This is conservative; archive/history-aware changes are not M1b.
- No deletion/archive UI, recurring sessions, learner records, prices, checkout, Wix, Notion
  or enrollment integration is represented as complete. Published records still require
  explicit user approval before any production pilot.
- The added browser status route serves only the allowlisted verification document on loopback;
  it adds no Django route or production exposure.

## Local Password Change

At the user's explicit request, all 31 application-user passwords in the two verified local
databases were reset. The reset checked the configured database name/host and the PostgreSQL
server's actual loopback address before writing, then verified every password. Remote/demo/
production passwords, database service credentials and authentication policy were untouched.
Existing sessions may require signing in again. Do not deploy these local credentials.

## User Acceptance Checklist

1. Open [Workshop Demo](http://127.0.0.1:8002/bookings/workshop-demo/). Use `bookings-owner`
   and the local test password supplied in the handoff.
2. Open Locations & rooms. Edit a name or add another room; save and reopen it.
3. Open Workshops, then Design Your Plushie. Edit the draft afternoon session or add a future
   one. A capacity above the room limit should be rejected.
4. Publish the session and view Public schedule from the workshop list. Only published sessions
   should appear; unpublish it and check that it disappears.
5. Confirm M1b is acceptable before M2 starts. M2 adds the actual customer request and staff
   preparation workflow; M1b does not yet take reservations or payments.

No additional information from Emily is needed for this milestone.
