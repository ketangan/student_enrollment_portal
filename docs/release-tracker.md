# Release Tracker

Last hosted revision/configuration check: November 11, 2026.

## November 11 Jasmine Demo Release

- Owner approved pushing all pending work to main after the full suite passes,
  then verifying demo. Release commit title: **Improve booking confirmations and
  Studio Prep contact workflow**. Production promotion is NOT approved.
- Full regression passed: **6,045 application tests, 25 migration tests, three
  skips**, JavaScript tests and aggregate/module coverage gates; measured Python
  coverage **98.1229%**. Source remained unchanged. Evidence:
  `studio-contact-cleanup-20261111/status.json`, fingerprint
  `a57c43ab69458671470c6947e321205c3cc6963ef8bbf19b0b56abb4749b96dc`.
  The saved `verified-manifest.json` records the tested files; only this release
  documentation was updated afterward. CI reruns the final committed revision.
- Includes the cron connection retry, additive notification cutover migration
  `scheduling.0059`, actual-recipient demo confirmations, scoped inline staff notes,
  contact display, workshop calendar choices and honest online-payment status.
- Render rechecked before push: demo web and maintenance follow main after CI;
  production web and maintenance remain on `6ebf7fe` with auto-deploy off. Hosted
  deployment, notification activation and smoke-test results will be recorded in
  the release commit's verification comment. Do not infer those checks from the
  local pass. Existing customer records must not be used for test emails.
- Remaining Jasmine work: direct calendar create/edit/cancel; scoped recurring
  schedule and bulk changes with booked-session safeguards; supplied design/color
  photos with default-image fallback; validated upcoming-booking import from Wix;
  website booking links/theme integration and final launch configuration. Her
  final email wording/triggers remain pending but need not block other work.
  Physical Terminal verification requires the live account and an approved small
  real charge/refund. Google/Outlook choices currently cover workshop confirmation;
  party confirmation calendar parity remains a separate follow-up.

## November 11 Studio Contact And Payment Status Follow-Up (Local, Unpushed)

- Studio Prep places the plain-text contact phone beside the booking name and
  inline staff notes below participants. Click-to-call was not requested and was
  removed. Editing a note hides its duplicate read-only copy. Add/edit/save/cancel
  controls retain 44px targets and existing permissions, audit and conflict checks.
- Current contact phone is also shown in reservation lists/details, individual
  preparation, workshop request details and day/week preparation printouts.
  Clearing a source phone does not resurrect an old snapshot value. Party inquiry
  already displays its contact phone. No enrollment/lead behavior was changed.
- Online checkouts now distinguish awaiting customer payment, expired unused
  checkout and a payment needing verification without an animated fake-progress
  indicator. Rendering does not call providers or alter money. Real terminal and
  refund polling remains unchanged, including duplicate-collection safeguards.
- The reported local $399.45 spinner was an expired, unsubmitted Square test
  checkout from October 30 (attempt 42). Existing recovery expired that attempt
  locally without a provider call or ledger change. The confirmed reservation's
  $150 received and $399.45 balance due were unchanged. No hosted data was changed.
- Final targeted regression: **345 passed**, including Square/Stripe payment
  controls, recovery, concurrency, notes, contact consistency and roster query
  counts. Two obsolete assertions expecting fake verification copy were corrected;
  submitted unknown payments still require verification. Studio Prep JS: **10/10**.
- In-app desktop browser: 1440x900, phone 390x844, tablet portrait 768x1024 and
  landscape 1024x768. Exercised inline add/edit/save/cancel, contact placement,
  reservation details/preparation/list and mobile grid scrolling. No page overflow;
  essential editing controls remain reachable. Physical Android, virtual keyboard
  and physical printing remain unverified.
- Full regression for this follow-up passed in the task artifact directory
  `studio-contact-cleanup-20261111`, with logs, revision, source fingerprint and
  completion status. See the release entry above for the approved demo release.

## November 11 Jasmine Follow-Up (Local, Unpushed)

- Demo workshop and party confirmations use the existing notification outbox and
  the booking's entered recipient. There is no email-address allowlist or recipient
  redirection. Demo subjects are labeled `[Demo]`; production content is unchanged.
  Migration `scheduling.0059_demo_notifications_since` establishes a cutover on
  existing profiles so older copied bookings cannot trigger automatic confirmations.
  Automatic demo reminders remain excluded. Explicit staff-composed emails retain
  their existing behavior and permissions.
- Activation after demo deployment: verify both existing confirmation switches in
  Guest communication, create a new synthetic booking addressed to
  `ketan@mypontora.com`, and check delivery, contents, links and outbox completion.
  No new configuration screen or tester-address setup is required. Local workshop
  confirmations were enabled through the existing settings screen; local external
  delivery remains disabled. Hosted settings and real inbox delivery have NOT been
  changed/tested by this local pass.
- Studio Prep displays the booking contact phone and supports inline add/edit of
  existing staff notes. This reuses reservation/inquiry notes and permissions,
  records before/after values in the audit log, detects conflicting edits and
  preserves customer-authored notes as read-only. Enrollment/lead notes, SBMC's
  enrollment UI, Stripe/subscription behavior and school configuration are unchanged.
  Existing roster query-count tests remain passing.
- The existing customer workshop confirmation now offers Google Calendar,
  Outlook and the Apple/other-calendar ICS download. Links and ICS use current
  reservation times, including after rescheduling, without exposing contact details,
  internal notes or private booking links to calendar providers. Calendar choices
  are not a background calendar sync; customers still save the event themselves.
  Party-calendar expansion is not part of this change. Amounts consistently show
  two decimal places and the displayed balance-due date follows the reservation.
- Verification: final focused run passed **339 tests** across cron connection,
  notifications, inline notes, permissions, concurrency, customer calendars,
  preparation, email/settings and hosted/local booking regressions. Studio Prep
  JavaScript tests passed **10/10**; migration drift and diff whitespace checks pass.
  Browser checks used the in-app desktop browser at 1440x900, 390x844, 768x1024 and
  1024x768. Notes save/cancel/blank validation, 44px editor controls, keyboard calendar
  menu use, overflow checks and actual ICS download were exercised. Google handoff
  reached its signed-out destination; saving an event in an authenticated Google or
  Outlook account was not tested. Physical Android, virtual keyboard and printing
  remain unverified. No real payment or external email was sent during these checks.
- This batch's full regression passed, with unchanged source and durable evidence
  in `jasmine-first-batch-20261111/status.json`: application, migration, aggregate
  and module coverage, JavaScript and migration drift gates all passed. Fingerprint:
  `2342b25c5aa51792893a44c0efb060c39a3578ead588a7b12d74c8c37182f573`.
  This predates the Studio Contact follow-up above, which also passed afterward.
  The preceding `demo-cron-connection-20261111` application pass had 6,007 passing
  tests but its remaining migration pass was canceled before this work; it is not
  a completed full-suite pass and does not verify this new batch.
- Consolidated unpushed scope: the demo connection retry below; demo confirmation
  delivery/cutover migration; Studio Prep phone/inline notes; workshop calendar and
  display fixes; associated tests and this release record. No production promotion
  is approved. Calendar management, bulk/recurring changes, customer-supplied
  animal/color photos and booking-data import remain separate follow-up work.

## November 11 Demo Connection Incident

- Sentry issue `7843398544` came from demo maintenance's initial deployment
  check, before booking maintenance started. The 20:40 UTC run timed out at
  20:42:41 and exited unsuccessfully. The 20:45 run passed the check at 20:45:23
  and completed successfully at 20:45:43. All 126 web health requests in the
  inspected 20:39-20:48 window returned 200; PostgreSQL continued accepting
  connections. Evidence supports a transient cron-to-database connectivity
  failure; the underlying Render network cause is not established.
- Local fix, NOT deployed: the demo boundary command bounds each PostgreSQL
  connection attempt to 10 seconds per host and retries connection errors up to
  three times, with five seconds between attempts. It never retries safety
  queries or maintenance/payment work. Recognized authentication/database
  configuration SQLSTATE errors fail immediately; final connection failures
  remain uncaught for monitoring. Existing production/shared command behavior
  and private database access remain unchanged.
- Targeted connection/isolation tests cover recovery, exhausted retries,
  option restoration, non-PostgreSQL connections, fail-closed isolation after
  recovery and no replay after a query fails. Full regression is required before
  push; no production promotion is authorized.
- Hosted demo web and maintenance are on `665a919518bdec165823d83ce2746685578f9451`.
  Production remains on `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c`.
  The previous release passed CI and hosted smoke checks; its final evidence is
  [recorded on the commit](https://github.com/ketangan/student_enrollment_portal/commit/665a919518bdec165823d83ce2746685578f9451#commitcomment-209443130).

## Hosted Baseline Before November 11 Release

| Service | Live revision | Deployment policy |
| --- | --- | --- |
| Demo web (`pontora-demo`) | `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c` | main, automatically after CI passes |
| Demo maintenance | `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c` | main, automatically after CI passes |
| Production web | `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c` | manual, approved exact revision |
| Production maintenance | `6ebf7fe08bb3e0ba9c459c3a34380d7e5bcc547c` | manual, approved exact revision |

These are observations from Render, reconfirmed before the November 11 push.
They are not a claim that the release candidate below is already deployed.

## Pending Work And Activation

| Change | Code/PR | Demo status | Production decision / gate |
| --- | --- | --- | --- |
| Contextual school go-live checklist and onboarding tests | November 11 readiness release, based on `6ebf7fe` | Full local regression passed; demo release authorized | Production promotion NOT approved; includes additive checklist-choice migration 0069 |
| Restore Leads/Submissions layout and shared status buttons; preserve custom answers on partial lead edits; allow explicitly clearing a submission follow-up | November 11 readiness release | Full regression and local responsive checks passed; verify hosted delivery through release evidence below | Production promotion NOT approved; retain existing school capabilities and policies |
| Automatic demo deployment and expanded CI coverage gates | Render configuration applied; November 11 readiness release | Demo web/job set to `checksPass` | Production auto-deploy remains off; no production release authorized by this configuration change |
| Database-aware health check and readiness regression repair | November 11 readiness release | Full regression passed; health module 100% coverage | Both hosted web services already use `/healthz/`; production code promotion remains separate |
| Production database network restriction | Owner approved November 10; applied November 11 07:20:33 UTC | Demo was already private-only | Production external allowlist cleared after confirming web/job internal URLs; later maintenance and production school list checks passed |
| Plushie production onboarding and physical Square Terminal | Implementation in hosted baseline | Sandbox only | Customer account/website decisions, production setup and approved small live charge/refund still required; enable production `--include-live-recovery` only at that gate |

## Maintenance Rules

- Update this table in each release-related PR: exact commit/PR, affected shared
  paths, demo verification evidence, approval source/date, production settings to
  enable or keep disabled, migrations/dependencies and remaining blockers.
- Use explicit states: local, demo review, production approved, production live,
  deferred. A passing CI run is not customer approval. Never infer approval.
- Before production promotion, compare its deployed commit with the proposed
  revision and account for **every** change, including earlier merged PRs.
- Record the full-suite revision and working-tree fingerprint. Verify both web
  and maintenance deploys, then update hosted revisions and smoke-test evidence.
- If code must be excluded rather than merely disabled, use the temporary release
  branch procedure in [the deployment guide](demo-production-separation.md).
- Customer setup decisions belong in the school's Go-live checklist. This tracker
  records software releases and activation gates, not passwords or credentials.

## November 10 Verification

- Prior full run passed application/migration tests but failed the onboarding
  module's 90% coverage gate. Added welcome-email recipient/token/link/audit,
  unready-school rejection and delivery-failure tests; targeted onboarding
  coverage is now 98.08%, and go-live applicability coverage is 100%.
- Added regressions reproducing custom-answer loss on partial lead updates and
  rendered-form tests for setting, rejecting invalid dates and clearing follow-ups
  in both normal and simplified workflows. Retested SBMC enrollment/lead behavior,
  school isolation, permissions and tour-field handling.
- Manually exercised local Leads and Enrollment details, contact/application
  editing, lead status, follow-ups, converted-lead navigation, invalid email input
  and responsive navigation. Browser emulation: desktop 1440x900, phone 390x844,
  tablet portrait 768x1024 and landscape 1024x768. Corrected small enrollment
  inputs; verified no horizontal page overflow. Real Android hardware, virtual
  keyboard and physical printing remain unverified.
- The preceding full run (`leads-release-regression-20261110`) passed application,
  migration, JavaScript and per-module coverage checks with 98.11% aggregate
  coverage. Its source fingerprint is recorded in `status.json`; it does not
  verify the subsequent header changes below.
- Header follow-up: removed the simplified status dropdowns in favor of the
  existing configured status buttons on both detail pages. Restored the compact
  desktop back/previous/next row and renamed the simplified navigation destination
  and list heading to Submissions. Status policy and school capabilities are unchanged.
- Final targeted regression: 192 passed across workflow simplicity, session
  workspace, school navigation policy, shared record details and SBMC workflows.
- Exercised actual status changes and restored the synthetic records' statuses;
  verified read-only/system-managed statuses, navigation, and responsive layouts
  at 1440x900, 390x844, 768x1024 and 1024x768 in the in-app desktop browser.
  Phone/tablet header actions measure at least 44x44 CSS pixels with no horizontal
  overflow. This is responsive emulation, not physical Android verification.
- Final full-suite evidence: local artifact directory
  `record-header-regression-20261110` under this task's visualization workspace.
  Check `status.json` for completion, revision and working-tree fingerprint before
  any push. That run failed nine variants of one obsolete exact-markup assertion
  in `test_admissions_preview.py` (5,972 other application tests passed). The fix
  checks the active status, `aria-current` and read-only behavior; all 37 tests in
  that module pass. This is not a passing full run.

## November 10 Launch Readiness Follow-Up

- Production database external access is disabled; subsequent maintenance and
  read-only admin checks passed. The isolated PITR rehearsal matched all 102 public
  tables by row count/content checksum and its temporary private database was
  deleted after about nine minutes. No service was pointed at the restored copy.
- The approved planned Sentry event `62f571b3e3d14ebf9cdfb6f8a4fdc4c4`
  reached the operator's Gmail inbox. Render cron-failure notification configuration
  was verified, but its delivery was not tested by failing a production job.
- 116 focused health, admissions, checklist and onboarding tests passed. A further
  504 focused Plushie booking, Terminal, refund, recovery, consent and preparation
  tests passed. Health and CI guard checks: 14 passed, health coverage 100%.
- Local browser checks covered the launch checklist at 1440x900, 390x844,
  768x1024 and 1024x768, disclosure keyboard interaction, mobile navigation,
  disabled production attestations and 44px checklist actions. Payment/refund
  dialog entry and cancellation were checked without submitting transactions;
  sandbox over-limit validation remained visible and unobscured. Studio Prep
  filtering and a workshop-scoped staff save were exercised; the original sample
  value was restored and the other booking stayed unchanged. Physical Android,
  virtual keyboard and printing remain pending.
- Both hosted environments remain on `6ebf7fe`; no application release or live
  payment activation occurred. The staff-consent fix was already in that revision.
- Final full regression evidence belongs to `plushie-launch-readiness-20261110`
  under this task's visualization workspace. `run.py` uses the CI coverage source
  list and records revision, working-tree fingerprint, logs and exit status.
  This run completed successfully on November 11; see the release record below.

## November 11 Demo Release

- Owner explicitly authorized completing regression and publishing to demo, not
  production. The release commit is titled **Launch readiness checklist and restore
  consistent record details**. Its GitHub Actions checks and commit comment record
  the exact released SHA, both Render deployment results and hosted smoke checks.
- Local full suite: **5,991 application tests, 24 migration tests, three skips,
  167 JavaScript tests**, migration drift and all coverage gates passed. Combined
  measured Python coverage: **98.1158%**. Source remained unchanged throughout.
  Evidence: `plushie-launch-readiness-20261110/status.json`, fingerprint
  `ea83d39daa05e23130904103e3bdbf62ccbb4d2c632c48efff3ac65030f1b920`.
- The overnight delay was macOS sleep, with a wake at 08:46 and explicit clamshell
  sleep again 08:51-09:54 Pacific. Actual test runtimes were 12m29s for
  application tests and 9m19s for migrations. No test failure/deadlock caused the
  overnight pause. Temporary idle-sleep protection was attached, and the standing
  test instructions now require it for detached macOS runs. It cannot override a
  closed lid. The run was allowed to finish without skipping tests or changing its
  tested files.
- Only documentation/instructions changed after this passing run; the release
  application, migrations, assets, CI configuration and tests match its saved file
  manifest. GitHub CI independently reruns the final pushed commit.
- Production web and maintenance must remain on `6ebf7fe` with auto-deploy off.
  No live payment activation, tenant migration, sample-data publication or new
  customer workflow enablement is part of this release. The combined enrollment
  demo's local database configuration is not automatically copied to hosting.
