# Release Tracker

Last hosted revision/configuration check: November 10, 2026.

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
