# M1a Impact and Verification

Status: accepted October 1 by the user's instruction to proceed to M1b; no push authorized.
The evidence below records the original M1a handoff. See M1b verification for current checks.

## Scope and Impact

- New `scheduling` app owns its entitlement and memberships, namespace, templates and CSS.
- Existing School is the tenant anchor. No enrollment form, YAML, membership, plan or
  application is required for a booking-only account. Bookings roles do not imply staff or
  enrollment roles. New memberships default to Viewer; unknown roles fail closed.
- Only explicit, enabled SchedulingProfile grants module availability. Neither a founder
  plan nor a superuser bypasses a disabled profile. User/membership revocation is checked
  on each request; private responses are not cacheable.
- Shared changes: app/URL registration and a login fallback AFTER existing superuser and
  enrollment membership redirects. Existing enrollment accounts keep their landing page.
  Explicit `/bookings/` entry uses existing authentication with a safe next URL.
- Scheduling provisioning is superuser-only in Django admin; no existing Ops screen changes.
  Owner can view their own team access. Membership editing by business staff is not M1a.
- School.is_active currently reflects enrollment billing too; it is not reused as the
  Bookings entitlement. Disabling the profile or Django user revokes Bookings independently.
- No scheduler, Stripe, email, provider worker, public booking or enrollment bridge yet.

## Migration and Rollback

The initial migration creates only SchedulingProfile and SchedulingMembership with unique
and role constraints. No existing-school profiles are seeded or enrollment records changed.
Test forward/reverse on an isolated database, including retained core data. No production
migration or enablement authorized. Keep existing local customer data untouched during QA.

Operational rollback first disables affected profiles. Removing these routes/app code must
preserve core login compatibility; reversing the initial migration deletes Bookings access
configuration, so it is appropriate only for disposable test data or after backup/approval.
Do not reverse it on a live tenant merely to hide navigation.

## Evidence

Tested October 1, 2026 against the working-tree implementation based on
`7ccd62b02ee2c4aeb0621ae4a82cd50af9034cf6`. No commit or push was made. Unrelated changes in
`core/views_demo.py`, SBMC homepage assets/docs and the existing nested worktree were preserved.
Re-run the required checks against the eventual outgoing commit before pushing.

### Automated checks

| Check | Result |
| --- | --- |
| Pre-change baseline | 1,914 passed, 2 warnings, 39.26 seconds |
| Initial focused unit/integration/migration run | 60 passed, 2.13 seconds |
| Final full suite, including two additional self-review tests | 1,976 passed, 2 warnings, 39.71 seconds |
| New module production Python coverage, excluding test files | 100% lines and 100% branches (118 statements, 10 branches) |
| Combined new module plus existing `core/views_login.py` | 95.47% combined coverage; login file 90.43%; all three new login lines exercised |
| Migration drift check | No changes detected |
| Migration round trip | New tables removed/recreated; core tenant preserved; no automatic profiles seeded |
| Patch whitespace check | Passed |

Integration checks use real local PostgreSQL at `127.0.0.1:5432`, dedicated disposable database
`test_pontora_bookings_m1a`, real Django routing/authentication/sessions/templates, CSRF and
database constraints. Fast MD5 hashing is limited to the test/isolated preview configuration;
the application's password policy and production hashers are unchanged. Tests are not routed
to production. TCP connections other than localhost PostgreSQL are blocked during test runs.
No paid APIs or real notifications were used. The existing duplicate Jazzmin tag warning remains.

Final verification command (temporary local harness, not production configuration):

```sh
env PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings \
  COVERAGE_FILE=/tmp/pontora-m1a-final.coverage venv/bin/python -m pytest \
  -p pontora_local_db_tests --cov=scheduling --cov=core.views_login --cov-branch \
  --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing \
  --cov-report=json:/tmp/pontora-m1a-final-coverage.json --cov-fail-under=90 \
  --maxfail=0 --tb=short --durations=15
```

Logs: `/tmp/pontora-m1a-baseline.log`, `/tmp/pontora-m1a-tests.log`,
`/tmp/pontora-m1a-final.log`. Configuration and fixtures in `/tmp` are local-only verification
artifacts; they are not required to run ordinary repository tests in a configured environment.

### Agent-performed browser checks

Actual browser interaction against http://127.0.0.1:8002 using synthetic accounts in the separate
`pontora_bookings_m1a_preview` PostgreSQL database. Existing localhost/production data untouched.

- Signed-out Bookings entry redirects to existing login with the return URL.
- Wrong credentials show an error; correct owner credentials reach the standalone workspace.
- Owner can open Team access and sees only this business's accounts.
- Viewer sees only Overview; directly opening Team access produces 404.
- Multi-business user gets a chooser; switching to a Viewer membership changes available navigation.
- Sign out returns to login; refresh and a full preview server restart preserve the saved account/session.
- Desktop 1280px and mobile 390px/320px inspected; no document overflow, overlapping text or broken logo.
- SBMC synthetic account still lands on enrollment Dashboard; Leads and Submissions work with unchanged navigation.
- Kid Works synthetic account still lands on enrollment Dashboard; Leads navigation works.
- New workspace has no enrollment trial banner, Leads/Submissions tabs or enrollment billing prompt.
- Browser console had no errors at completion. The only deliberate HTTP error was the denied Team access request.

Screenshots: `/tmp/pontora-bookings-m1a-desktop.jpg`, `/tmp/pontora-bookings-m1a-mobile.jpg`.
Preview server log: `/tmp/pontora-m1a-server.log`; its settings prohibit external outbound connections.

### Critical self-review

- Added explicit tests that a role downgrade immediately removes access and that enrollment login
  never queries Bookings tables. This protects both revocation and the existing login fast path.
- Confirmed school billing state is not accidentally inherited as Bookings entitlement; tested an
  expired/inactive enrollment account with valid independent Bookings access.
- Confirmed Django staff/model permissions alone cannot grant Bookings provisioning access;
  only active superusers can change the new admin records. Operator edits create Django audit logs.
- Confirmed unknown roles fail closed and duplicate/invalid memberships fail at database level.
- No promises of zero regression risk: shared app/URL registration and login fallback are real
  shared changes. Production deployment must apply the additive migration before using new routes.
- M1a deliberately has no availability engine, services editor, reservation, payment, integrations,
  location-scoped instructor roles or self-service invitation workflow. It is an access foundation,
  not the customer-booking demonstration. M1b is the next user-facing setup milestone.

### User acceptance checklist

1. Open `/bookings/` on port 8002 and sign in with the supplied local owner credentials.
2. Inspect Overview and Team access; sign out and test the Viewer account, which has no Team access.
3. Optionally use `bookings-multi` to inspect the two-workspace chooser and per-workspace role.
4. Confirm this foundation is acceptable before starting M1b. No push or deployment authorized yet.

No Emily/MakeMusic information is required for M1a. Before the music-school milestone, request
teacher/instrument/room assignments, weekly hours/closures, lesson durations, recurring booking
rules, cancellation/make-up policies and actual billing arrangements, using redacted examples
rather than student/payment data. Do not infer those rules from the single trial-booking screenshot.
