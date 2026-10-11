# M9c: Usable Preschool Demonstration

Date: October 4, 2026. Ready for local user testing. No deployment approval.

## Delivered Scope

- Complete optional tour -> application -> approved playdate flow with the existing enrollment
  connector. One family/child/application identity survives return visits and schedule changes.
- Private email-possession return links: one use, 30-minute expiry, hashed token, fragment-based
  exchange, seven-day revocable session, generic request response and persistent request throttle.
- Booking and admissions notifications reuse the existing transactional message queue. The local
  inbox demonstrates success, retry, supersession, revocation and uncertain-delivery handling.
- Family/staff rescheduling, family cancellation with a configurable notice deadline, optimistic
  revision checks and audited corrections. Last-place races use the shared inventory lock.
- Finite weekly/biweekly schedules with preview, signed actor-bound confirmation, closure skips,
  conflict rejection and idempotent atomic publication. Maximum planning horizon is 180 days.
- Editable location closures; booked visits and lessons must be moved or canceled explicitly first.
  Optional explicit mapping connects a visit host to a music teacher and checks both calendars,
  room use, buffer/travel time and closures without automatically linking existing staff.
- Owner setup/readiness, contact details, family return links on parent status, local example records
  and browser-readable manual steps under the tracker M9c row.

## Impact Review

This is not a school-template-only change. New visit services/views/models are in scheduling.
Migration 0018 adds operational settings, return grants, schedule-batch idempotency, booking revision
and nullable teacher/host mapping. It activates no school. Only the isolated preview database was
migrated. No production or primary localhost database change, credentials change, push or deployment.

Existing visit booking/attendance now invokes an opt-in notification hook and increments a revision.
The shared lesson availability engine checks visit calendars only when a teacher is explicitly mapped.
Location closures prevent booked visits from being silently closed. Workshop/camp closure semantics
are not changed. Shared message preview protects private return URLs from viewers and from booking
owners lacking enrollment-owner access when a linked application exists.

The admissions guard additionally fails closed on payment routes/fee-enabled configurations only
for an enabled admissions policy with the new pilot settings. This free demonstration does not
certify or rewrite legacy application-fee processing. Reusing the zero-dollar reservation/order
foundation does not create a second payment system. Existing SBMC and Kid Works configurations,
feature activation, public form field precedence and payment flows are unchanged.

## Automated Verification

Final full regression: **3,055 passed, 2 existing warnings, 202.43 seconds**.
The six new runtime modules have **98.92% combined statement/branch coverage** (516 statements,
134 branches; 2 statements and 5 branches uncovered). Each new module is at least 97.10%.
Views, models and repeat scheduling are 100%. Across scheduling, shared payments and test
safeguards the combined result is **99.75%**, and every measured module passes the 90% gate.
No migration drift or whitespace errors. The existing duplicate `jazzmin` template-library
warning remains; this milestone did not introduce it.

Reproducible local-only commands:

```sh
PONTORA_TEST_DATABASE_URL=postgresql://njulka@127.0.0.1:5432/test_pontora_bookings_m1a COVERAGE_FILE=/tmp/pontora-m9c-final.coverage venv/bin/python -m coverage run --source=scheduling,core.payments,testing -m pytest --tb=short
COVERAGE_FILE=/tmp/pontora-m9c-final.coverage venv/bin/python -m coverage json -o /tmp/pontora-m9c-final-coverage.json
venv/bin/python -m testing.coverage_gate /tmp/pontora-m9c-final-coverage.json
```

Disposable local PostgreSQL, local email and blocked external sockets. Coverage scope is scheduling,
shared payments and test safeguards, excluding migrations/templates/tests; it is not whole-repository
coverage. Full regression includes existing enrollment, SBMC and Kid Works tests.

Cases include expired/reused/revoked/cross-browser access, generic recovery responses, no default
activation, private inbox role restrictions, request throttling, stale writes, moved-booking retries,
duplicate publication, simultaneous last-place contention, rollback, closures, buffer/travel conflicts,
teacher mapping validation, application-status preservation, replacement-playdate correction, and
fail-closed local payment entry points. No real provider compatibility is claimed.

## Browser Verification

Desktop parent/staff walkthrough completed: Dana's tour was booked, moved and recovered through
a one-use return link. Robin's historical tour attendance was corrected, then restored; a private
return link opened a prefilled application, submission appeared on the same case, staff released
a playdate, the family booked and moved it, and the same application became Playdate Scheduled.

Weekly tour dates were previewed and published. Existing room/time conflicts removed the publish
action. A closure on October 15 was rejected because families were booked. A November 2 closure
was saved and appeared as Skipped in a weekly preview; its reason was edited and persisted.
Phone-width receipts/status/setup had no document overflow or broken images. Saved setup,
closure, private application status and playdate survived a server restart. The tracker expands
M9c's credentials and manual steps. Existing SBMC and Kid Works public forms opened without
the new gate. Read-only database inspection confirmed only preschool-demo has enabled admissions
and visit-pilot settings; no existing teacher was mapped.

Local retry processing suppressed the already-used private return link and safely delivered
the latest pending notice to local capture. Two additional fictional historical tours, Taylor
Walkthrough / Finley and Blake Walkthrough / Harper, remain untouched for user demonstrations
of attendance, a fresh application and staff approval. Existing examples were preserved.

Screenshots: `/tmp/pontora-m9c-setup.png`, `/tmp/pontora-m9c-family-mobile.png`,
`/tmp/pontora-m9c-status-mobile.png`, `/tmp/pontora-m9c-repeat.png`.

## Self-Review Corrections

- Prevent restoring an old missed playdate when a replacement is already booked or attended.
- Preserve manually advanced lead/application statuses; no correction may silently reset Enrolled.
- Keep original submission-key retries bound to the same booking after a move or access revocation.
- Suppress consumed/revoked return emails; hide private URLs from insufficiently privileged staff.
- Supersede pending application/playdate invitations when the same case books or changes its playdate.
- Make closure editing available with stale-edit protection and tenant scoping.
- Keep private visit access terminology precise: it does not revoke enrollment status/draft links.
- Fail closed on paid enrollment for this free pilot; do not rely on the known legacy payment gap.
- Update the tracker's matching estimate row so adding M9c cannot break the browser status overview.

## Before a Real Pilot

1. Implement and verify actual email delivery, worker scheduling, sender identity, monitoring,
   bounce handling and recovery with an authorized provider. Current messages stay local.
2. Agree each customer's release/eligibility/notice rules, application fields, sibling handling,
   staff permissions and room capacity. No SBMC/Kid Works migration is implicit.
3. Certify paid admissions separately if required, using the shared payment foundation and an
   authorized test merchant. Real checkout and recurring tuition are not part of this free demo.
4. Review deployment/security/retention, additive migration rollback/backup, real onboarding and
   customer acceptance; obtain explicit rollout approval. No public deployment occurred.
5. Connected Wix/Notion remains M4 and needs Plushumi input plus authorized provider tests.

This milestone is a functioning local demonstration, not a claim that every M9 production gate
or every future scheduling/payment milestone is complete.
