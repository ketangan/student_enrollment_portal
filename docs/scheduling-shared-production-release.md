# Shared Production Release: Plushumi Demo

October 10, 2026 (Pacific). Released and verified on the existing shared service.

October 12 access update: the hosted demo-only account now has the
user-requested password and has been verified through https://demo.mypontora.com/login/.
The follow-up release is deployed and verified. It includes the shared menu,
operator/Django portal scoping, account-bound scheduling magic links, configured Customer
demo navigation, and the rename to `plushie_admin`. Core migrations 0063 and 0064 are
additive and applied automatically. Revision `357a4b5` passed CI `38291587182`, then
Render deployment `dep-db6inllg1s2s739fdh40` went live October 12, 18:51 UTC. The
verified Render backup completed at 18:23 UTC; no production DB was copied locally
for this release. Hosted maintenance renamed the account and deleted 65 old audit
entries, preserving memberships, permissions, password and business/payment records.
Normal login, the 14-day scheduling-only magic link and its superadmin panel passed
hosted browser checks. Demo/production operator lists and audits are separated; shared
physical storage and global operator accounts remain intentional. SBMC admin/public
pages and active Founder billing passed checks, and its school/subscription fields,
submission count and existing Render variables matched the pre-release fingerprints.
Final regression: 3,792 Python tests, 48 JavaScript tests, 99.45% measured combined
coverage, every measured module over 90%, no migration drift. See
[current portal access notes](scheduling-plushumi-demo.md#portal-access-and-environment-separation).
The initial-release evidence below is historical, not the current password/payment status.

## Deployed Result

Presentation follow-up: [booking-page/popup cleanup](scheduling-plushumi-demo.md#preview-navigation-and-copy-cleanup)
replaces the earlier inline wrapper. Historical initial-deployment evidence below describes
revision `31220c5`; the later cleanup does not change payment logic, data or schema.

- GitHub PR #1 merged after CI passed: application revision `31220c5`.
- Render deployment `dep-db5i4b7avr4c73f494h0` is live. Startup automatically applied
  all 28 migrations; production has core 0062, payments 0003 and scheduling 0023.
- Completed Render export: October 11, 2026 at 05:22 UTC. A separate private local
  logical backup was restored successfully for the PostgreSQL 18 rehearsal.
- The hosted seed committed 15 fictional Plushumi reservations. It verified all
  pre-existing School rows and the 261 enrollment submissions were unchanged.
  No scheduling profile was enabled for another school. A sixteenth fictional
  reservation, `Pontora Release Check`, records the live browser verification.
- SBMC live dashboard, submissions, leads, settings and billing were checked in
  the browser. Its active custom subscription and existing navigation remained
  unchanged. Public application, inquiry and parent-status pages returned 200.
- Both hosted embeds load the actual booking workflow. Live browser checks covered
  participant selection, review, simulated failure without confirmation, simulated
  success, staff preparation and its reservation audit entry. The 390px modal had
  no horizontal overflow; close, reopen, Escape and focus restoration passed.
- Anonymous staff preparation redirects to login and remains frame-denied.
  Customer demo pages allow same-origin frames only; wrapper pages remain noindex.
- Admin access uses the existing Pontora operator account. The newly seeded
  `plushie_admin` has no usable password; no local `admin` password was deployed.
- No real Stripe charge, Wix checkout, Notion sync or confirmation email was sent.
  Payment controls explicitly simulate success/failure. Provider connections and
  cross-origin Wix certification remain separate release gates, not completed work.

### Live Links

- Comparison hub: https://demo.mypontora.com/demo/plushumi/integrations/
- Inline: https://demo.mypontora.com/demo/plushumi/integrations/inline.html
- Modal: https://demo.mypontora.com/demo/plushumi/integrations/modal.html
- Admin: https://demo.mypontora.com/bookings/plushumi-demo/
- Original storyboard: https://demo.mypontora.com/demo/plushumi/

## Scope and Architecture

- Reuse the existing Render service/database for both app.mypontora.com and
  demo.mypontora.com. No new paid service, database, or domain change.
- This is a production code/schema release, not a separate staging environment.
- Include the pending scheduling module, its shared payment foundation and opt-in
  enrollment hooks, plus the previously committed trial/payment-recovery changes.
- Scheduling remains disabled for all existing schools. SBMC's live website,
  enrollment configuration and active subscription are not changed.
- Remove obsolete SBMC homepage concepts only; retain existing SBMC enrollment demos.
- Existing Plushumi storyboard stays at `/demo/plushumi/`. New comparison hub:
  `/demo/plushumi/integrations/`, with `inline.html` and `modal.html` beneath it.
  Both wrappers embed the actual customer workflow, never a copied form.

## Post-release CI Follow-up

- The deployed merge's run `38115747467` passed. Documentation-only follow-up
  `9f24e41` failed in run `38116354511`; application code was identical and that
  documentation commit did not trigger another Render deployment.
- Failure: `test_hosted_get_and_post_abuse_limits` expected request 61 to be blocked,
  but its real-clock loop could cross django-ratelimit's IP-offset minute window.
  A new window legitimately accepts that request. This was a nondeterministic test,
  not a payment failure or evidence of a production payment regression.
- Correction is confined to tests: an opt-in controlled clock for the limiter and
  cache, explicit allowed/blocked limits, independent-IP behavior and recovery after
  the window boundary. Four other scheduling rate-limit tests use the same fixture.
  No production rate, payment code, migration or provider configuration is changed.
- A read-only provider audit confirmed SBMC active at USD 24.99/month without a
  pending cancellation or local payment deadline. It also found invoice webhook
  subscriptions still missing: [billing activation status](payment-alerts-and-recovery.md).
  Shared billing code was deployed; billing notification activation is not complete.
- Local correction verification: 3,700 Python tests and 49 JavaScript tests passed;
  measured scheduling/payment/testing code has 99.48% combined statement/branch
  coverage, with every measured module above 90%. Migration drift check passed.
  The billing/settings page was also checked read-only for SBMC: active subscription,
  no overdue banner, no scheduling menu. No live payment or customer email was tested.
  The corrective commit uses `[skip render]`; GitHub CI must be checked independently.

## Hosted Demo Controls

- Defaults are off. Set `BOOKING_HOSTED_DEMO_SLUGS=plushumi-demo` and
  `BOOKING_HOSTED_DEMO_HOSTS=demo.mypontora.com,app.mypontora.com` only for rollout.
- HTTPS, an enabled scheduling profile, active `is_demo` school, explicit
  `booking_experience_demo` flag and required capabilities must all agree.
- Run `seed_booking_experience_demo plushumi-demo --hosted` once after migration.
  It creates fictional catalog/material/session data and 15 sample reservations
  for a new profile. Reruns preserve existing bookings and setup changes.
- Hosted seeding refuses real/externally connected schools, unrelated existing
  accounts, password assignment and destructive reset. New demo owner has an
  unusable password. Local `admin` credentials are NOT deployed.
- Only customer demo responses allow same-origin framing. Staff pages retain
  frame denial. Wrappers are noindex/no-store and prohibit being framed themselves.
- Customer booking writes remain CSRF-protected and browser-bound, with IP rate
  limits. Existing cache configuration determines rate-limit sharing across workers.
- No global Stripe mode change, live key fallback, real booking charge or email.
  This release uses explicitly labeled checkout simulation. Real Stripe test
  checkout remains off until account-bound webhooks and scheduled reconciliation
  are configured and verified on the host. Wix/Notion are not connected.

## Verified Before Release

- Render live revision: `7ccd62b`; source branch `main`; automatic deployment on commit.
- Existing startup runs migrations before Gunicorn. No separate manual migration
  should race that startup. Build collects static assets.
- Database: PostgreSQL 18, Basic-256mb, 8.84% of 1 GB used at inspection.
- Web: Starter, 512 MB / 0.5 CPU; observed recent memory around 35% and low traffic.
  This is headroom evidence, not a load-test guarantee.
- Point-in-time recovery available for the prior three days. A Render export and
  a separate private local PostgreSQL 18 logical backup completed.
- Restored that backup into an isolated local PostgreSQL 18 cluster. All 28 pending
  migrations applied: core 0061-0062, payments 0001-0003, scheduling 0001-0023.
- Compared hashes/counts of original columns in all 18 existing core tables before
  and after migration: identical. SBMC stayed active with its active subscription
  and no scheduling profile. Hosted demo seed succeeded separately.
- Full regression: 3,697 passed, two existing warnings, 330.89 seconds.
- Measured scheduling/shared-payment/test-safety code: 99.48% combined statement/
  branch coverage. Every measured module exceeds 90%. Not a whole-repository claim.
- JavaScript: 49 passed; 99.23% lines / 95.26% branches / 95.38% functions.
- Migration drift and static collection dry-run passed. Secret-pattern scan clean.
- Browser: real embedded start/selections/review, simulated decline/success,
  desktop and 390px modal, close/reopen/Escape/focus, no horizontal overflow.
- CI uses PostgreSQL 18 to match Render. Run `38115062278` passed before merge:
  3,697 tests, two warnings, 653.08 seconds; coverage gate 99.48%.

## Deploy and Verify

1. Review staged files; exclude unrelated nested worktree, local harness, backups,
   credentials, logs and local databases. Push release branch and wait for CI.
2. Set only the two explicit hosted-demo allowlists. Keep real Stripe demo disabled.
3. Merge the verified release to main; watch Render migration/startup completion.
4. Seed only the new hosted Plushumi demo. Never restore local data into production.
5. Verify live health, login, SBMC lead/enrollment/status/admin pages and no new
   scheduling navigation for SBMC. Compare existing-school access/data checks.
6. Verify original storyboard, comparison hub, both embeds and staff authorization.
   Walk a fictional booking through decline/success and verify preparation appears.
7. Record deployed revision, migration state and any unfinished external integration.

## Rollback

Disable the two hosted-demo allowlists to hide the previews without deleting data.
If enrollment regresses, roll the Render code deployment back to the prior revision
and prevent an automatic re-deploy of the bad revision. Leave additive schema in place;
do not blindly reverse migrations or restore a backup over newly submitted families.
Core 0061 uses database defaults for old-code compatibility. New scheduling/payment
tables do not modify old enrollment tables. Scheduling 0022 converts new-module stock
data and is not a generally safe reverse operation.

## Remaining Boundaries

Cross-origin Wix embedding still requires allowed-origin messaging, cookie/CSRF checks
in Safari, and tested top-level checkout return behavior. Same-origin hosted previews
are not evidence that the final Wix connector is delivered. Real provider payments,
message delivery and scheduling activation for other schools require separate rollout
verification; deploying their inactive code does not declare those milestones complete.
