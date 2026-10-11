# M9b: Application Release and Real Playdates

Date: October 4, 2026. Ready for local user testing. No deployment approval.

## Delivered Scope

- An optional school admissions policy: require a completed tour or not, automatic or staff
  application release, automatic or staff playdate release, eligible application statuses,
  and the scheduled/completed status labels from the school's existing configuration.
- One exact case links a tour's lead, saved draft, submitted application and playdate.
  Siblings sharing an email remain separate; reopening a lead does not overwrite family answers.
- All existing public application, resume and application-payment entry points enforce the
  policy before proceeding. Staff application links enforce it too. Unlinked manual application
  creation is blocked for enabled schools instead of bypassing the required sequence.
- Families choose a published playdate with a real room, host, date and capacity. The existing
  reservation engine and zero-dollar payment ledger are reused; no second payment module.
- Staff approval is visible in an admissions progress page. Parent and staff application pages
  show the actual booking. Booking sets Playdate Scheduled; attendance sets Playdate Completed.
  Cancellation releases places and restores the prior status only when staff has not already
  advanced that application. Retry and concurrent submissions cannot create duplicate work.

## Impact Review

This is not a templates-only or school-files-only change. New policy/case services, forms,
views, templates and migration `0017_admissions_workflow` live in scheduling. The additive
migration does not activate a school, change customer records or collect money. Applied only
to the isolated Bookings preview database; production and the primary localhost database
were not migrated.

Shared enrollment changes are bounded policy checks around application/resume/payment routes,
an atomic exact-case submission hook, guarded lead enrollment actions, and case-specific
parent/admin panels. Missing or disabled policy passes through to the existing behavior.
The legacy duplicate-email/fingerprint shortcuts are bypassed only for a policy-bound case;
that case's one-to-one link and database locking provide duplicate protection instead.
Legacy manual playdate entry is replaced only on case-linked applications.

The student name/search helper now falls back to `student_name` after existing first/last and
`applicant_name` fields. Tests preserve the existing precedence. This fixes the new demo's
single-name YAML field without changing existing school field mappings.

SBMC and Kid Works have not been enabled or reconfigured. Their existing regression tests
remain part of the full suite. Their YAML and payment configurations were not edited.
Standalone workshop, party, camp, music and tour use does not require this enrollment policy.
Only the fictional Garden Preschool Demo has been configured for this preview.

## Automated Verification

Final full suite: **3,007 passed, 2 existing warnings, 183.81 seconds**.
The five new admissions modules have **99.03% combined statement/branch coverage** (393
statements, 122 branches; 2 statements and 3 branches uncovered). Forms, models and entry-point
gates are 100%; the policy service is 98.48% and views are 98.75%. Coverage across scheduling,
shared payments and test safeguards is **99.86%**. Every measured module passes the 90% gate.
Migration drift: no changes detected. Whitespace check passed. Existing Django check warning:
duplicate `jazzmin` template tag library; not introduced by M9b.

Reproducible command, using a disposable database only:

```sh
PONTORA_TEST_DATABASE_URL=postgresql://njulka@127.0.0.1:5432/test_pontora_bookings_m1a COVERAGE_FILE=/tmp/pontora-m9b-final.coverage venv/bin/python -m coverage run --source=scheduling,core.payments,testing -m pytest --tb=short
COVERAGE_FILE=/tmp/pontora-m9b-final.coverage venv/bin/python -m coverage json -o /tmp/pontora-m9b-final-coverage.json
venv/bin/python -m testing.coverage_gate /tmp/pontora-m9b-final-coverage.json
```

Tests use disposable local PostgreSQL, local email capture and blocked external sockets.
Python coverage excludes migrations, templates and tests; it is not a claim about every line
of legacy enrollment code. Shared enrollment behavior is covered by targeted integration
checks and the existing full regression suite.

Coverage artifacts: `/tmp/pontora-m9b-final.coverage`,
`/tmp/pontora-m9b-final-coverage.json`, `/tmp/pontora-m9b-final.log`.

Targeted checks cover release requirements at every entry point, tenant/role boundaries,
browser binding, bad/expired/mismatched links, school/feature pause, immutable child identity,
siblings, draft preservation, single/multi-form submission, two simultaneous applications,
two simultaneous playdates, rollback, capacity, cancellation/no-show/attendance, preservation
of later staff decisions, policy edits, stale settings and default-off pass-through.
Closed/unpublished/inactive-host slots are filtered before the catalogue limit; saved policy
alone prevents removing its feature and stranding admissions behind an inaccessible gate.
Legacy application fee entry points are mocked in tests; real paid enrollment is not certified.

## Browser Verification

Verified the actual local parent/staff journey: an attended tour released a prefilled
application, the submitted application waited for staff approval, approval revealed the
playdate selector, an over-capacity request was rejected, and a valid booking appeared on
both parent and staff pages with Playdate Scheduled. Canceling released the places, restored
New, and allowed rebooking. Saved state survived a server restart.

Desktop (1280px) and mobile (390px) checks passed. Parent booking, staff progress and settings
stayed within their viewport; the settings checkboxes reflowed into one mobile column. Progress
shows the actual booked date/room and an attendance link, not an ambiguous booked/completed label.
The browser tracker expands M9b's manual steps and local credentials and links this report.
Screenshots: `/tmp/pontora-m9b-desktop.png`, `/tmp/pontora-m9b-mobile.png`, and
`/tmp/pontora-m9b-progress-mobile.png`. Existing SBMC and Kid Works public application pages
were opened in the isolated preview and retained their normal forms without the new gate.
Database inspection confirmed that `preschool-demo` is the only enabled admissions policy.

## Self-Review Corrections

- Preserved saved family answers when staff reopens an enrollment link; filled only missing
  lead-prefill fields in old drafts.
- Checked token expiry and case/draft identity before redirecting to an existing application.
- Added same-browser application-status re-entry on the original tour receipt.
- Paused application/playdate access when the family portal is disabled.
- Rejected disabling an admissions policy with saved cases, and removing Visits with saved
  policy/setup/history, so existing families are not left in an unmanageable state.
- Filtered unavailable playdates before the 50-row display bound.
- Corrected playdate-specific confirmation labels, settings checkbox layout and contradictory
  generic parent-status instructions for case-linked applications.

## Remaining Rollout Limits

M9b provides a local functional slice, not production readiness. No push or deployment.

1. Only free visits. No new tuition, payment checkout or recurring billing. Existing application
   fee processing is reused but its legacy verification/order binding still needs a separate
   paid-flow review before activation; mocked tests do not certify Stripe behavior.
2. No new outgoing notifications or verified cross-browser family access. The tour receipt
   is browser-bound; the application uses the existing private status link. Delivery, lost-link
   recovery and recipient verification must be completed before real families rely on it.
3. Explicit dated slots, not recurring availability. Cancellation/rebooking is the current
   change path. Attendance correction, self-service cancellation/rescheduling, shared host/music
   teacher identity mapping and closure rules remain rollout work.
4. One application per prospective child. Additional attendees consume capacity but do not
   become separate applications. Existing converted leads are not silently imported; real
   migrations need explicit record mapping and school-specific field/status review.
5. Policy applies to the school's admissions entry points, not separate program funnels.
   Confirm the school's release rules, statuses, sibling handling and communication policy
   before enabling. SBMC and Kid Works require explicit approval and their own acceptance tests.
6. Connected M4 is still deferred pending Plushumi input. The local implementation does not
   establish Wix/Notion compatibility or approve a paid customer rollout.
