# M7c1: Private Family Schedules

Updated October 3, 2026. Ready for local user testing; no commit, push or deployment.

## Scope and Impact

M7c is deliberately split into M7c1 access and M7c2 booking. M7c1 gives an explicitly
approved guardian read-only access to selected students' upcoming/past lessons and make-up
credits. Dates, duration, teachers and rooms come from existing saved lesson snapshots.
Rescheduled originals are not presented as additional appointments. Internal reasons,
attendance notes, other household contacts, staff actions and financial records are not exposed.
Attendance labels are shown for past lessons, not a misleading future "Scheduled" label.

No customer slug checks. No enrollment record, enrollment subscription, staff membership or
new parent account is required. Existing Django sessions are reused; there is no second user
or password database. The access grant is a read-only authorization capability, not a staff
role or automatic match to an email. Existing parent-status tokens give no scheduling access.
Owner verification of the recipient's authority is explicitly required for the selected students.
This is a staff attestation, NOT a claim that the application verified email ownership.

Only Bookings files, its local tracker and documentation changed in this slice. No shared
enrollment, login, Stripe, payment-ledger, global middleware, settings or school YAML edits.
Migration 0010 adds the access-grant table and its student membership table. It creates no
grants automatically and changes no existing lesson, credit, payment or school records.
Applied only to the isolated Bookings preview database. Main-local/deployed databases and
SBMC/Kid Works enablement are untouched.

## Security Review

- Owners alone issue, preview and revoke access. Scheduler/viewer roles cannot grant it.
- An invitation selects exact students from one household; it does not automatically cover
  future siblings. A student moved out of that household ceases to be authorized.
- A 256-bit random invitation secret is stored only as a SHA-256 digest. It expires after
  24 hours and is single-use. Access ends after the owner's chosen 1, 7 or 30 days from issue.
- A URL fragment carries the secret, so it does not enter HTTP request URLs/access logs.
  Local JavaScript removes the fragment immediately, then an explicit CSRF-protected POST
  exchanges it for a separate server-stored session capability. No localStorage or third-party scripts.
- Session ID rotation prevents fixation without changing an existing staff identity. Every
  family request rechecks the business gates, exact student scope, expiry and revocation.
- Database-backed tenant locking serializes issue, open and revoke. Duplicate submissions
  never create extra grants. Duplicate token opens succeed in at most one browser. Revoking
  after opening invalidates that browser on its next request.
- Owner preview is a separate authenticated route and does not consume the guardian link.
- Closing private access revokes that grant and removes only that workspace's family session,
  leaving any staff session or other workspace's family access intact.
- Secrets are redacted from Django exception variables/POST reports. Audits contain scope,
  actor, timestamps and lifecycle actions, never bearer secrets. Pages are non-cacheable,
  non-indexable and disallow framing. The exchange is rate-limited using the existing package.
- Browser testing found `no-referrer` made legitimate form POSTs send an Origin of `null`.
  Use `same-origin` instead: cross-origin referrers remain suppressed, URL secrets are already
  stripped, and Django CSRF remains fully enabled. HTTPS same-origin acceptance and foreign/null
  Origin rejection are covered by integration tests. No CSRF exemptions/trusted-null origins added.
- Self-review caught a form/model limit mismatch: Django's default email form permits 320
  characters while this database field allows 254. The form now uses the same limit and an
  overlong otherwise valid email has a regression test; no database exception reaches the owner.

This follows the separation between student/parent portal access and booking permissions
described by [My Music Staff](https://www.mymusicstaff.com/availability-based-student-portal-booking-is-here/).
Token/session controls were cross-checked against [OWASP token guidance](https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html)
and [session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).
These are reference checks, not claims of identical product functionality or a security certification.

## Verification

- Initial focused run: 45 tests passed; 279 new Python statements and 40 branches covered, 100%.
- Final full regression: **2,677 tests passed**, two existing warnings, **111.46 seconds**.
  New access Python: **279/279 statements, 40/40 branches, 100%**. Scheduling/shared-payments
  runtime code, excluding migrations: 3,678/3,679 statements and 819/820 branches, **99.96% combined**.
  The one uncovered existing reservation branch is not part of this change. Wider measured
  scope including legacy billing/login/viewer is 98.65%. These percentages do not measure
  template/JavaScript execution or replace browser checks.
- Unit/integration checks: recipient confirmation, student/tenant/role isolation, unrelated
  users with matching emails, unsigned/malformed/stale form tokens, link replay/expiry,
  revoked/copied sessions, profile pause, student movement and newly added siblings.
- Concurrent issue/open and revoke-vs-open use actual separate PostgreSQL connections.
  Audit failure rolls back invitation creation and opening. Database date/session constraints
  and migration preservation/reversal are tested. Paging remains scoped to the authorized students.
- Browser: owner creates Riley-only access, non-consuming staff preview, fragment removal,
  legitimate form POST, upcoming/past lessons, room changes, private-note omission, mobile
  width 390 with no page overflow, immediate revocation and restart persistence checked.
  Final preview refreshed on port 8002. The active fictional grant covers both Riley and Jamie;
  selecting Jamie hides Riley's lesson rows and credit. Earlier Riley-only access is revoked.
  No SBMC/Kid Works Bookings profiles exist in the preview; readback confirmed only Music Demo grants.
- No external API/provider calls. Test runner blocks external sockets. No email/SMS was sent.

Evidence (uncommitted working tree; these are local temporary artifacts):
- `/tmp/pontora-m7c1-verified-tests.log`
- `/tmp/pontora-m7c1-verified-coverage.json`
- `/tmp/pontora-m7c1-verified.coverage`
- `/tmp/pontora-m7c1-family-mobile.png`
- `/tmp/pontora-m7c1-family-desktop.png`

Final command used the existing isolated PostgreSQL test settings and external-socket blocker:

```sh
env PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings \
COVERAGE_FILE=/tmp/pontora-m7c1-verified.coverage venv/bin/python -m pytest \
-p pontora_local_only_tests --maxfail=5 --cov=scheduling --cov=core.payments \
--cov=core.services.billing_stripe --cov=core.views_login --cov=scripts.project_status \
--cov-branch --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing \
--cov-report=json:/tmp/pontora-m7c1-verified-coverage.json --tb=short --show-capture=no
```

`makemigrations scheduling payments --check --dry-run` reports no missing changes.
`git diff --check` passes. Much of the accumulated Bookings module is still untracked, so a
future commit must explicitly review/stage its files; do not stage unrelated SBMC homepage work
or the nested agent worktree. Portable CI/network/coverage gates remain a release prerequisite.

## Rollout Boundaries

This is a usable local read-only access increment, not a finished recurring guardian-login
experience. Links must be shared by staff over a verified channel. Clearing browser cookies,
using a different device, closing access or reaching expiry requires a new invitation;
automated verified re-entry and real notification delivery are not implemented here.
Because a bearer link can be forwarded, the selected scope must be deliberately narrow.
This must not be presented as strong identity verification for payments or sensitive records.

M7c2 still owns optional self-booking/trials, booking policy confirmation, cancellation/make-up
self-service and notification delivery. M8 owns recurring money billing and must extend the
shared payment module, not duplicate it. M4 Wix/Notion and the outstanding rollout audit
items remain open. Nothing here enables SBMC or constitutes paid-pilot readiness.

Before deploying outside the isolated preview: reviewed migration/backup, real delivery and
re-entry policy, HTTPS/secure session cookies, a shared rate-limit cache, operational expiry
support, tenant-specific acceptance and the wider release gates are required. Rollback before
use may remove the empty new tables; once access history exists prefer a forward fix and
revocation, not deleting the audit history.
