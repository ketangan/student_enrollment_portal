# M7b2: Make-up Policies and Lesson Credits

October 3, 2026. Ready for local user testing, not customer rollout. M7b1 is accepted for
progression. No commit, push, production migration or SBMC enablement is authorized.

## Scope and Rules

- Optional, default-off per-business policy: family/studio cancellation eligibility, minimum
  family notice from 0 to 720 hours, and expiry from 1 to 365 days after the original lesson.
- Cancellation is previewed before confirmation. Each affected future scheduled lesson shows
  its make-up outcome. Closed/skipped dates, past attendance and separately moved exceptions
  are not silently credited. Original schedules and cancellation reasons remain intact.
- One credit belongs to one original occurrence, student, service and snapshotted duration.
  It is not a currency balance, transferable household fund or financial refund.
- A make-up is a single lesson using existing teacher qualification, availability, closures,
  travel gaps, student conflicts and shared workshop/camp/party room checks. Preview and commit
  both validate it. The lesson must finish by the exact expiry deadline; expiry is not merely
  a booking deadline. Dates remain bounded by the existing 180-day scheduling horizon.
- On-time family cancellation of a make-up restores the same credit; late cancellation
  forfeits it. Studio cancellation restores it. These rules use the issued policy snapshot,
  not subsequently edited defaults. Restoration never extends expiry or creates a second credit.
- A rescheduled make-up retains its original credit, deadline and linked date history.
  Completed/no-show lessons consume the credit; attendance corrections do not issue new ones.
- Owners can revoke unused credits with a reason. Booked/used credits cannot be invalidated;
  resolve a future booking first. Disabling new issuance does not block existing entitlements.
  Disabling lesson scheduling is blocked while future lessons or usable credits remain.
- Staff see per-family credits, Available/Booked/Used/Expired/Forfeited/Revoked status, source
  and current lesson links, and dated history with actor/reason. Viewers cannot mutate;
  schedulers can cancel/redeem; owners control policy and revocation.

The 24-hour/90-day Music Studio Demo settings are fictional, not Emily's accepted policy.
Expiry uses elapsed days from the original lesson's end, displayed in that lesson's timezone;
its local hour can change over a daylight-saving transition.

## Architecture and Self-Review

All mutations lock the existing SchedulingProfile row, then recheck access and current state.
This is the same lock used by native booking and lesson writers. Signed cancellation previews
bind staff, tenant, selected lesson, scope/origin/reason, policy version and credit outcomes.
Signed redemption previews bind the credit/version, fields, setup and availability. Commit
rejects stale setup, policy, notice-threshold or credit changes. Exact redemption retries reuse
the saved event/lesson, even after that make-up was later canceled; they never spend it again.

The credit table's unique source protects against duplicate issuance. A one-to-one current
redemption and explicit database state checks protect the entitlement. Credit events are
append-only through the application services. Audit/event failure rolls back cancellation,
credit changes and lesson creation together. State changes increment the credit version.
Policy editing also uses signed optimistic checks, including two simultaneous first saves.

Critical review added cross-credit competing-slot and revoke-versus-redeem concurrency tests,
and complete rollback tests. Browser review removed the redundant confirmation checkbox and
kept credit-history access on replacement schedules after restoration. Initial test corrections
reloaded a deliberately changed catalog before preview and renewed the test login after jumping
the clock beyond credit expiry; neither was bypassed in application code.

Competitor cross-check: [My Music Staff's policy-based make-up flow](https://www.mymusicstaff.com/availability-based-student-portal-booking-is-here/)
couples eligible cancellation to a credit and availability-based replacement. Its
[category-specific credit history](https://www.mymusicstaff.com/make-up-credits-by-category/)
reinforces keeping entitlements scoped and auditable. Pontora uses the same broad concepts;
this milestone is staff-only, not a claim of complete competitor parity or family self-service.

## Impact and Migration

New implementation lives in scheduling credit models/forms/services/views and templates.
Existing Bookings lesson cancellation, rescheduling, history, settings and routes are extended.
No enrollment view/template/YAML, authentication, Stripe/provider or core payments code is changed
in this milestone. Credits do not create PaymentOrder, monetary ledger or refund entries.
The source tree already contains earlier accumulated shared changes; this report does not
authorize pushing that whole branch without its separate review and release gates.

Migration `scheduling.0009_lesson_credits` adds four isolated tables and constraints. It does
not backfill credits from old cancellations or enable any policy. Migration tests preserve
existing lesson states, versions/reasons and school plans. Reversal is tested with empty new
tables; once real credit history exists, use a forward fix rather than a rollback that loses it.
Applied only to the disposable test DB and `pontora_bookings_m1a_preview`. Main local, demo
deployment and production databases were not migrated.

## Verification

Final full run: **2,623 passed**, two existing warnings, **107.78 seconds**. New credit
models/forms/services/views: **100% statement and branch coverage**, 336 statements and
72 branches. Scheduling/shared-payments combined coverage is **99.95%** (3,486/3,487 statements,
779/780 branches). The wider measured set including legacy billing/login and the viewer is
98.56%. The initial 2,619-test pass and subsequent 73-test focused pass were followed by this
final complete run. Schema drift and whitespace checks passed. External APIs are blocked in both test and preview settings.
No real Stripe/Google/Anthropic calls or messages were sent.

Tests exercise eligibility on both sides of the notice boundary, policy-off and saved-policy
behavior, stale/forged/expired previews, catalog changes, expiry and service-duration preservation,
repeat cancellation/redemption/restoration, database constraints, rescheduling transfer,
attendance/no-shows, revocation guards, atomic rollback, concurrent duplicate and competing
credits, role/tenant isolation, CSRF, disabled workspaces and migration preservation.
Full regression includes existing enrollment, billing, login, room-sharing and disabled-school
tests. This is not a production SBMC browser visit or a real payment-provider certification.

Manual browser checks on isolated port 8002:

1. Enabled the fictional Music Studio Demo policy through the owner screen.
2. Canceled Riley's October 6 lesson after reviewing its credit outcome. The credit appeared
   once with its source lesson, 30-minute duration, original expiry and staff reason.
3. Tried Jamie's occupied October 6 at 4 PM: rejected with no confirmation button. Tried
   January 6 beyond expiry: rejected. October 7 at 3 PM previewed and booked one make-up.
4. Canceled that make-up and restored the same credit. Rebooked October 14 at 3 PM, moved it
   to 4 PM using M7b1, then canceled as the studio. One Available credit remains, with six
   linked events and unchanged expiry. No extra credit or payment appeared.
5. Desktop history inspected at 1280px; mobile booking form inspected at 390 x 844. All
   controls stay inside the viewport and document width remains 390. Tables scroll inside
   their container rather than expanding the page. Temporary viewport override reset.
6. Preview-server refresh retained the policy, credit and history. New cancellation form has
   one preview/confirmation sequence, without the old redundant checkbox.

Revocation, late cancellation and read-only/cross-tenant behavior are verified by automated
service/HTTP integration tests; they are not claimed as separate manual browser actions here.

Evidence: `/tmp/pontora-m7b2-credit-history.png`, `/tmp/pontora-m7b2-mobile.png`.
Initial full run: `/tmp/pontora-m7b2-full-tests.log`, `/tmp/pontora-m7b2-full-coverage.json`.
Additional tests: `/tmp/pontora-m7b2-final-focused-tests.log`.
Final run: `/tmp/pontora-m7b2-final-tests.log`, `/tmp/pontora-m7b2-final-coverage.json`.
The browser tracker was verified with M7b2 expanded, credentials, manual steps, effort estimate
and the still-unanswered Emily questions visible. Isolated DB readback confirmed only music-demo
has an enabled credit policy; SBMC and Kid Works have no Bookings profile in this preview.

## Remaining Decisions and Release Gates

Emily still needs to confirm notice, expiry, term limits, exception/holiday policy and whether
families choose their own make-up times. Her draft question remains in M7a's tracker notes.
No staff override grants for old absences, no-show forgiveness, credit transfers, splitting,
term caps or monetary equivalence are assumed. These need explicit product/policy decisions,
not inferred behavior. M7c family access and M8 recurring payments are separate milestones.

The earlier audit's capability packaging, staff scoping, mixed settlement, portable CI and
Wix/Notion work remain open release requirements. Local M7b2 completion does not clear the
paid-pilot gate or make this a drop-in MakeMusic replacement.
