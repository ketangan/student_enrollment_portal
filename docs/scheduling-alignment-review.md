# Scheduling Requirements and Rollout Review

Reviewed October 3, 2026 against the original customer requirements, the design,
the current uncommitted implementation and official competitor documentation.

October 4 update: P1 implements native selective capabilities and portable test gates, with a
focused daily workspace. See [P1 verification](scheduling-p1-verification.md) and the tracker.
A4 is addressed for native grants; provider authority remains a pre-connection requirement.
A6 now has repository-owned gates and local evidence; remote CI execution is not claimed.
A3/A5 and the connected Jasmine journey remain open. Original findings below are retained
as the review baseline, not a replacement for the current tracker.

## Verdict

**The architectural direction is aligned. The product is not ready for a client rollout.**
Bookings has a genuinely independent staff workspace, shares the existing business identity,
does not require enrollment records or an enrollment subscription, and has reusable setup.
It is not a customer-specific fork. Keeping it in this repository remains the right choice.

However, independent access is not yet independent product packaging. Selective capabilities,
branding, scoped staff access, daily operations and customer integrations remain incomplete.
The two reproduced defects (A1/A2) have now been corrected locally with permanent regression
tests. The October 4 update above records subsequent progress. High code coverage does not
close the remaining customer workflow gaps.

The latest work followed the user-approved native scheduling milestones. It has not delivered
Jasmine's central outcome: a better booking journey alongside Wix, with automatic Notion updates.
Continuing to build music features without revisiting that outcome would be commercial drift.

## Confirmed Findings

### A1 P1 Stop New Bookings Also Stops Existing Balance Payments

**Resolved locally, October 3. Original defect:** after a verified deposit confirms a reservation, turning off
`native_reservations_enabled` prevents its remaining balance checkout. The public page also
hides the payment action. The switch therefore cannot safely mean "stop new sales but keep
servicing existing bookings," as the design requires.

Evidence: `scheduling/checkout.py:42`, `scheduling/views_checkout.py:32`.
Reproduced with a $100 reservation, $25 simulated verified deposit and $75 remaining.
The existing balance fails with "This reservation is not available for checkout."

Correction: distinguish permission to accept new bookings from permission to service existing
commitments. Preserve valid balance collection, payment events, recovery, refunds, history and
exports when intake is paused. Do not bypass an intentionally disabled merchant account.
Test held, confirmed, canceled and review states, not only the happy-path balance payment.

Implemented: one booking eligibility check drives both the payment page and checkout service.
Confirmed balance payments remain available during intake pause; held/unconfirmed checkout
still stops. A disabled workspace, disabled/invalid merchant, missing test key, canceled/review
reservation or the existing 40-minute cutoff still blocks new checkout. Incoming verified
events, recovery and refunds keep their existing behavior. The UI now also honors the cutoff.
Tests cover workshops, parties and camps, including exact amounts and duplicate settlement.

### A2 P2 Stale Setup Forms Silently Overwrite Saved Changes

**Resolved locally, October 3. Original defect:** the profile lock serializes writes, but it does not detect a form opened before
another administrator's edit. A stale workshop form can successfully replace the newly saved
description with the older value while changing an unrelated name.

Evidence: `scheduling/setup.py:61`. Reproduced with two sequential saves representing two
open tabs. Both forms validated; the second erased the first edit. The setup paths for choices
and lesson configuration use the same general pattern and need the same audit, although this
reproduction specifically exercises workshops.

Correction: bind edits to an expected version or a signed snapshot and reject stale changes
with a reload message. Locks are still needed for resource conflicts. Avoid adding a second
generic form framework; use the existing signed-preview conventions where appropriate.

Implemented: a signed, tenant/object-bound snapshot is captured with the displayed settings
and validated under the existing tenant lock before any save. All 13 setup form types are
protected, including camps and teacher/service relationships. Missing, invalid, expired and
stale tokens fail closed; a visible GET reload link avoids accidentally resubmitting the stale
POST. Submitted values remain visible so the user can review them before reloading.
Concurrent-save tests prove exactly one winner and one audit entry. No schema change was needed.

### A3 P2 Online Deposit Plus In Person Balance Is Missing

**Requirement gap, not a failed promise of the offline-preview milestone.** A merchant-backed
order cannot accept an offline receipt, even after its online deposit is verified. The current
system supports offline-only or online-only orders, not a mixed-method settlement.

Evidence: `core/payments/service.py:52`. Reproduced with a confirmed party reservation; the
in-person balance receipt is rejected. Plushumi's published policy calls for party deposits
and the remaining balance in person. That makes this a native-party launch dependency, not
an optional payment enhancement. [Plushumi booking policy](https://www.theplushieworkshop.com/booking-and-cancellation-policy).

Correction: extend the existing order ledger to accept explicitly authorized, auditable
payment channels on the same order. Guard against an outstanding online checkout collecting
the same balance. Never refund an offline receipt through Stripe. In Wix-connected mode,
Wix remains the money authority; Pontora must not collect the same balance independently.

### A4 P2 Small Customer Packages Are Not Fully Enforceable

**Rollout gap.** `SchedulingProfile` has three booleans: workspace enabled, native reservations
enabled and lessons enabled. It has no workshop-only, party-only, camp-only or connected-provider
capability grants. Workshops, Requests, Parties, Camps and Reservations appear even in the
music workspace. Owners can enable lessons/native intake without a separate operator-granted
commercial entitlement. This is acceptable in an isolated prototype, not a paid feature boundary.

Evidence: `scheduling/models.py:14`, `templates/scheduling/base.html:31`,
`scheduling/views_lessons.py:32`, `scheduling/views_reservations.py:47`.

Correction: a small typed capability registry, with operator grants separated from owner
preferences; enforce it in services and routes as well as navigation. Do not create an
arbitrary plugin loader. Add explicit native/provider authority before connecting Wix, with
one booking authority per shared inventory pool. Move this release gate earlier than M9.

### A5 P2 Staff Roles Are Business Wide

**Rollout gap.** Owner, scheduler and viewer are workspace roles. There is no teacher-to-user
binding or location-limited access. A viewer can see the workspace's families, rosters and
reservation financial summaries. That is not a cross-tenant leak, but it is not suitable as
an "own students only" teacher account or a location-limited front desk account.

Evidence: `scheduling/access.py:7`, `scheduling/models.py:27`,
`scheduling/views_lessons.py:56`, `scheduling/views_reservations.py:31`.
Until scoped permissions exist, a pilot must explicitly limit accounts to trusted whole-business
administrators. Hiding a menu alone would not fix this.

### A6 P2 The Test Quality Gate Is Not Reproducible In CI Yet

**Engineering gap.** CI runs pytest but does not enforce the requested coverage threshold or
the local external-network block. This audit and previous isolated runs use settings and a
network guard under `/tmp`, not a committed, portable test configuration. Existing mocked
tests are useful, but the repository does not enforce those safety promises for every run.

Evidence: `.github/workflows/ci.yml:42`, `pytest.ini:1`.
Correction: commit a safe, portable test configuration, deny unmocked external calls by default,
enforce at least 90% new/changed-code coverage, retain concurrency and integration cases, and
require a recorded manual checklist before release. Coverage measures executed code, not
whether the specification or assertions are complete.

## Requirements Traceability

| Original requirement | Current evidence | Assessment |
| --- | --- | --- |
| Standalone Bookings without enrollment | Separate membership and workspace; no enrollment-plan gate | Implemented locally; operator provisioning, not self-service product onboarding |
| Optional enrollment integration | Default-off profile; independent records; enrollment bridge still M9 | Isolation exists; integrated customer journey not delivered |
| Customer-specific packaging without forks | Generic locations, rooms, offerings, choices, teachers and availability | Good foundation; missing capability catalog and policy/branding controls |
| Visual choices for each participant | Image/swatch choices, multiple participants, saved option snapshots | Implemented for requests/camps; no full variant preview or arbitrary conditional rules |
| Single workshop selection and checkout journey | Public request, then owner-created quote/reservation | Not yet a self-service paid workshop flow; requires staff action |
| Wix can remain in place | Design defines provider authority | No implemented connector or verified connected journey; M4 deferred |
| No manual transfer into Notion | Local preparation data persists | No Notion connector, durable delivery queue or reconciliation; M4 deferred |
| Parties early | Inquiries, review, room exclusivity, quote, deposit/balance prototype | Partial; mixed payment channels, participant party roster and quote delivery outstanding |
| Camps early | All-date seat reservation, choices, cancellation and shared order | Implemented fixed-session prototype; no drop-ins, late joins or proration |
| Rooms and teacher availability | Location/room capacity, lesson teacher eligibility, gaps, shared-room checks | Implemented basic constraints; generic equipment pools remain later scope |
| Standing lessons and changes | Finite weekly schedules, closures, substitutions, attendance, linked history | Implemented staff-only; make-up policy and family access remain M7b2/M7c |
| One-time and recurring payments | Shared order/attempt/refund ledger and existing Stripe facade | One-time test implementation only; recurring tuition/packages remain M8 |
| Do not duplicate payments | Bookings uses core/payments and existing billing_stripe transport | Direction aligned; legacy enrollment fees are not migrated to the new ledger yet |
| Safe incremental rollout | Tenant opt-in and local test gates | Not cleared: A1-A6 and customer-specific release checks remain |

## Competitor Check

These are public product/documentation comparisons, not claims that every advertised vendor
feature has been exercised. We should adopt relevant patterns, not chase complete feature parity.

| Reference | Relevant pattern | Implication for Pontora |
| --- | --- | --- |
| [Wix booking policies](https://support.wix.com/en/article/wix-bookings-setting-up-your-booking-policies) | Service-specific booking windows, change rules and group limits | A configurable policy layer is still needed; hard-coded checkout cutoffs are not business policy |
| [MakeMusic Studio](https://www.studio.makemusic.com/features/) | Teacher availability, rooms, individual staff calendars, self-booking, reminders, flat tuition and packages | Our resource foundation fits; family access, staff scope, daily calendar and recurring billing are not ready to replace SBMC's system |
| [My Music Staff](https://www.mymusicstaff.com/calendar-attendance/) | Calendar views/filters, student booking and make-up tracking | Prioritize daily usability and explicit make-up rules before a music-school migration |
| [Acuity resources](https://help.acuityscheduling.com/hc/en-us/articles/16676949567757-Use-resources-to-limit-bookings) | Shared physical resources constrain bookings across calendars | Keep one resource authority; extend rooms to equipment only when required |
| [Sawyer activities](https://help.hisawyer.com/en/articles/14827297-semester-activity-guide) | Different term/drop-in/payment options and configurable multiday registration | Fixed all-date camps are a sensible first slice, not full camp-management parity |

The integration design is still current in the important respects: Wix uses Forms submissions
for custom booking data, not the retired additionalFields approach. Notion distinguishes data
sources from database containers. These docs make the proposal plausible; only an authorized
end-to-end provider test can prove two attendees' choices survive checkout and update the same
Notion records on edits, cancellations and retries.
[Wix Forms integration](https://dev.wix.com/docs/api-reference/business-solutions/bookings/wix-forms-integration?apiView=SDK),
[Notion data model](https://developers.notion.com/guides/data-apis/working-with-databases).

## Usability and Customization

Browser review of the fictional music/workshop workspaces and customer request form confirms
the screens are usable for demonstrating individual tasks. They are not yet an efficient daily
workbench for a multi-teacher or multi-location client.

- Music has a chronological table and family filter, but no Today/week view or teacher/room/location
  filters. Past dates precede future work. Attendance is visible in detail, not in the main list.
- The music workspace still displays workshop/party/camp navigation. A limited package should
  expose only enabled work, with a setup checklist appropriate to that package.
- The public form shows the business name but uses fixed Pontora logo, fonts and colors.
  School branding is not yet reused (`templates/scheduling/public_base.html:3`).
- Image and color choices are configurable, but image setup currently requires an asset path/URL.
  That is operator-assisted customization, not a polished customer media-management workflow.
- Setup has separate location/service/teacher/availability/family screens. It needs an ordered
  readiness checklist and a preview before publishing; customers should not need our knowledge
  of internal dependencies to discover the next step.
- Confirmations, reminders, family change notices and self-service access are not implemented
  for the native lesson workflow. A visible warning is honest, but it does not remove staff work.
- The workshop preparation page totals requests and tells staff to consult Reservations for
  confirmed bookings. A daily confirmed-workshop roster should not require that cross-check.

Branding, bounded typed settings and reusable choice schemas are configuration. A new provider,
equipment allocation or new billing model still requires code and tests. "Customizable" must
not be sold as "any workflow can be configured without engineering."

## Code Quality and SBMC Impact

The implementation has substantive safeguards: tenant-scoped access, PostgreSQL transactions,
serialized resource writes, server-validated amounts, durable attempts, signed webhook settlement,
duplicate protection, audit trails, saved quotes/choices and history-preserving lesson changes.
The reviewed scheduling service paths contain no customer-slug business forks or AI calls.

Remaining maintainability concerns are specific, not a reason to rewrite the module: compressed
templates and some dense service code make review harder; resource checks span workshops,
parties, camps and lessons; every future writer must use the same locking/conflict contract.
Define and test that contract before adding more resource types. There is not yet a universal
database exclusion constraint protecting arbitrary direct writes.

Shared impact is **not zero**. The accumulated branch adds installed apps, routes and additive
migrations, extends login fallback, and changes the existing Stripe facade to explicit keys.
Bookings-off tenants retain their current enrollment flow in the tested scenarios, but those
shared changes still require baseline regression and a staged deployment. Legacy application
fees remain on their existing path; migrating them is separate impact-reviewed work, not a
prerequisite for a narrow non-paid Bookings pilot.

No SBMC settings, production database, payment credentials or external systems were changed
by this review. This is not a production security certification or a claim that all possible
regressions are excluded.

## Rollout Decision

| Package | Can demonstrate now | Before a real client pilot |
| --- | --- | --- |
| Standalone unpaid workshop requests | Yes, fictional tenant | A4/A6, branding, confirmed customer scope, notifications or an explicitly accepted manual process |
| Native paid workshops/parties/camps | Local simulation only | A3-A6 as applicable, direct workshop checkout, policy/receipt flows, merchant onboarding, sandbox/recovery/operational proof |
| Visual booking plus Wix and Notion | Static concepts and local choice capture only | M4 end-to-end proof, provider-owned inventory, durable sync, cancellation/retry handling and agreed field ownership |
| Staff-only lesson scheduling | Yes, fictional tenant | Capability scope, daily schedule usability, setup/version safeguards and accepted operating rules |
| Family-facing SBMC scheduling | No replacement claim | M7b2/M7c, notices, scoped staff access, M8 when required, approved migration and enrollment bridge |
| Enrollment plus selected Bookings features | Architecture supports it | Small explicit opt-in bridge, integration tests, default-off rollout and preservation of existing commitments |

Standalone means independent customer access and purchase, not a separately deployable service.
All capabilities still ship in the same application. Additive schema can be deployed default-off;
client enablement must be a separate reviewed action. No rollout has been authorized here.

## Recommended Next Work

1. A1/A2 are corrected locally with permanent regression tests. Specify and test mixed-channel settlement
   before native party payments, keeping it in the existing ledger.
2. Add the small capability/authority model, safe intake pause and portable CI safety/coverage
   gates. Do not wait for the end of M9 to define how a customer gets only one feature.
3. Finish one customer outcome. For Jasmine, prefer the M4 connected feasibility test over
   more music features. While authorized access is unavailable, prepare fixtures and adapter
   contracts without claiming a working integration or copying the customer's systems.
4. Add branding, a focused daily agenda and setup readiness for the chosen package. Deliver
   these in reviewable pieces, with an explicit demonstration of what staff no longer do manually.
5. Resume make-ups, family access and recurring tuition under their existing milestones after
   business rules are confirmed. Do not migrate SBMC just because local music scheduling works.

This is a recommendation for the next implementation sequence, not approval to enable features,
connect customer accounts or change the accepted milestone history.

## Original Audit Evidence (Before Corrections)

- Three isolated requirement probes were run against disposable PostgreSQL with external sockets
  blocked. With expected-failure markers bypassed, all three failed at the predicted application
  lines: A1 checkout gate, A2 stale overwrite, A3 mixed-method receipt rejection. These are
  confirmed unmet acceptance criteria, not three passing tests.
- Reproduction source: `/tmp/pontora_scheduling_audit_probes.py`. The report above preserves the
  scenarios; corrected behavior needs permanent regression tests in the relevant modules.
- Fresh full-suite regression: **2,523 passed, 2 warnings in 94.19 seconds**, including the local
  documentation viewer tests. This excludes the three deliberately separate failing probes.
  Scheduling/shared payments covered 3,077/3,078 statements and 695/696 branches: **99.95% combined**.
  The wider selection including legacy billing/login and the viewer is 98.40%. Reports are in
  `/tmp/pontora-alignment-audit-tests.log` and `/tmp/pontora-alignment-audit-coverage.json`.
  These passing results do not invalidate A1-A3; their requirements were not asserted by the
  existing suite. Add them as permanent failing-first regression tests when correcting the code.
- Read-only browser inspection covered workspace switching, music agenda/setup, enabled menus,
  workshop public schedule and visual participant form. Prior milestone reports contain the
  detailed transaction/mobile test evidence. No production/provider sandbox test was performed.
- Review changes are confined to this report, project/tracker notes and the local documentation
  viewer. No scheduling or payment behavior was modified, migrated, committed or pushed.

## Corrective Verification

The subsequent authorized correction changes only scheduling services/forms, scheduling
templates, related tests and project documentation. It does not modify the shared payment
ledger/provider, enrollment code, schema or customer settings. The accumulated branch still
has the shared impacts listed above; this is not approval to deploy that branch.

- Permanent regressions replace the A1/A2 probes. A3 remains an explicitly tracked missing
  capability, not a claim of completed mixed-method settlement.
- Final full regression: **2,557 passed, 2 warnings in 94.33 seconds**, after the reload-link
  refinement and workshop/party/camp coverage. Scheduling/shared payments covered 3,123/3,124
  statements and 701/702 branches: **99.95% combined**. Wider selection including legacy
  billing/login and the documentation viewer: 98.42%. Evidence:
  `/tmp/pontora-audit-fixes-final-tests.log` and `/tmp/pontora-audit-fixes-final-coverage.json`.
- Edit snapshot helper, booking checkout service and payment view each achieved 100% Python
  line/branch coverage. No new migrations detected. Django configuration check has only the
  existing duplicate Jazzmin template-tag warning.
- Browser: verified two-tab stale rejection, preserved newer description, explicit reload
  recovery and successful fresh save. Restored the original fictional workshop text.
- Browser: paused new reservations, opened Jordan Camp Demo's existing $200 balance checkout
  through the local simulator, expired that attempt and verified the booking stayed confirmed
  with its $100 deposit/$200 balance unchanged. Restored the original intake setting.
- No real provider requests, charges, messages, production changes, commits or pushes.

Self-review checked that signing occurs before ModelForm validation mutates its in-memory
instance, that relation snapshots match the settings actually displayed, and that validation
runs after acquiring the tenant lock. Authorization/CSRF still apply independently of edit
tokens. Invalid forms keep their original token, so retrying cannot silently bless stale data.
The checkout change does not relax account ownership, disabled-merchant checks, payment amounts,
provider verification, refund rules or capacity checks on unconfirmed reservations. The existing
enrollment/SBMC regression suite remains part of the full run, not a separate unverified claim.
