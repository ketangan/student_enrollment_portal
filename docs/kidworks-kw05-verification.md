# KW-05: Contract Features and Go-Live Trial

Date: 2026-10-08
Scope: implementation and isolated local verification only. Not deployed, not
applied to the real Kid Works school, and not authorization to convert or launch it.

Release packaging: KW-05 was reconstructed without the unrelated local scheduling work.
The isolated first commit passed 218 billing/trial tests, including activation concurrency,
migration rollback, checkout restrictions and ordinary-trial regressions. Scheduling-specific
integration checks remain in the pending scheduling module; core tests do not require it.
Migration 0061 must be applied before serving this code. The prepared customer terms still
require an explicit operator action; committing them does not activate Dinuka's trial.

## Behavior

- Demo access and the customer trial are separate. Demo token expiration still
  controls its login link; old demo trial timestamps no longer produce an expired
  customer-trial banner or block public demo forms. Inactive schools stay inactive.
- Ordinary, non-demo schools retain the default 30-day auto-start behavior.
- Explicitly opted-in customers can be prepared with an unstarted trial. Kid Works'
  reviewed configuration is 60 days, with no selected billing interval.
- An active superuser must record the sent written go-live notice, its timestamp
  and confirmation that the agreed workflow is live. The trial starts at that
  timestamp, not the time of configuration, conversion or clicking the button.
- Start is transactional, audited and locked against simultaneous/repeated
  requests. A repeat cannot extend it. Invalid/future notices are rejected.
- Demo conversion clears historical demo trial dates only for opted-in contracts.
  Ordinary settings edits cannot bypass the explicit conversion/start workflow.
- Subscription checkout and billing-portal creation are blocked during demo/setup
  and the opted-in free trial, including direct POSTs and service calls. At expiry,
  existing explicit plan selection is available. There is no automatic charge,
  subscription creation, notice email or scheduled billing change.
- The existing welcome email claims the school is live. It is therefore unavailable
  during demo/pending setup, and displays the actual trial end after activation.

## Contract Entitlements

`configs/customer_terms/kidworks-childrens-center.json` adds only four existing
per-school overrides: custom branding, custom statuses, lead conversion and
save/resume. Starter already includes leads, CSV export, application status,
family access, reports and the other base enrollment features.

The defaults for Starter and all other schools are unchanged. The legacy admin
form preserves explicit contract overrides when saving during a full-featured
trial, rather than discarding them as redundant trial defaults. A simulated paid
Starter webhook transition verifies those overrides survive billing updates.

Scheduling uses its existing enabled profile, capabilities and membership checks,
not a new paid-plan gate. Tests verify visits and CSV export remain accessible
under Starter when explicitly enabled. **This does not activate KW-02 or implement
the missing Kid Works workflow/Brightwheel mapping.** Those have separate go-live
gates and remain pending; an entitlement test is not acceptance of those features.

## Impact Review

Shared edits are confined to trial properties, billing entry points, onboarding,
operator forms/routes, contract-override retention and related trial UI. The
60-day/deferred behavior defaults off. Demo behavior changes intentionally for
all demos, not for paid SBMC or ordinary live trials.

No scheduling runtime, payment ledger, Stripe webhooks, enrollment state machine,
school YAML, exports, customer forms, SBMC configuration or business records were
changed by this task. Existing dirty-tree changes in those areas were preserved.
The tracked diff was compared against the pre-task snapshot, not just Git HEAD.

One additive migration (`core.0061_school_trial_activation`) adds trial duration
and deferred-activation fields. Python/database defaults are 30/false, preserving
old rows and inserts from older workers/historical migration models. The fields
are non-editable in legacy model forms; contract preparation is explicit.

Critical review found and corrected:

1. New required model fields leaking into an older admin form.
2. Historical migration models inserting rows without the new columns.
3. Contract feature overrides being stripped while on the Trial plan.
4. Settings edits bypassing deferred demo conversion.
5. Immediate checkout potentially truncating the contracted free period.
6. Unstyled controls on the new operator page during browser verification.

## Verification

All automated checks use disposable loopback PostgreSQL databases and
`testing.settings`, with external network requests blocked and provider secrets
removed. Browser checks use separate synthetic records on port 8010; outbound
network access is blocked and mail is in-memory. No real email or Stripe operation.

- Focused unit/integration checks cover exact trial boundaries, authorization,
  CSRF, concurrent activation, audit rollback, repeated preparation/start,
  conversion, direct checkout, public forms, token expiry, inactive schools,
  Starter entitlements, admin editing and migration upgrade/rollback.
- Full regression: **3,547 passed**, 2 warnings, 299.37 seconds. After adding the
  paid-Starter transition test and legacy billing redirect assertions, a final
  affected-suite run passed **375 tests**, 2 warnings, 6.87 seconds. No runtime
  changes were made after the full regression; the final additions were tests.
- New KW-05 test file: 78 cases. New runtime modules: **100% line/branch coverage**
  in the final affected-suite run. Exact task-diff executable-line coverage:
  **192/192 (100%)**, including the modified shared files. This is not a claim of
  100% whole-repository coverage or a substitute for live deployment acceptance.
- `makemigrations --check --dry-run`: no changes detected.
- `git diff --check`: passed.
- Django system check: no errors; existing duplicate `jazzmin` template-tag warning.

Reproduction (local disposable database only):

```sh
PONTORA_TEST_DATABASE_URL=postgresql://njulka@127.0.0.1:5432/test_pontora_kw05 venv/bin/python -m pytest --reuse-db -o 'addopts=--disable-warnings --maxfail=1 -p testing.network' -q
```

The affected-suite run selects `test_customer_trial_activation.py`,
`test_trial_expiry.py`, `test_billing.py`, `test_onboarding.py`, `test_admin.py` and
`test_ops_phase1.py` under `core/tests`, with `--cov=core` and a JSON coverage report.
Local evidence: `/tmp/kw05-final-coverage.json`, `/tmp/kw05-before.patch`,
`/tmp/kw05_scope_review.py` and `/tmp/kw05-billing-verified.jpg`.
The isolated preview server was stopped after testing; no existing server was
reconfigured or restarted.

Manual browser checks (1280px desktop and 390px mobile where applicable):

1. Sign in as a synthetic demo owner with a historically expired trial date.
   Dashboard has no false countdown; Settings > Billing says no customer trial
   has started; checkout controls are absent.
2. Open the public tour and application forms. Old trial dates do not block them.
3. As a synthetic operator, open the pending customer's trial-start page.
   Submission without the confirmation checkbox is blocked.
4. Enter the synthetic sent-notice timestamp and confirm. The result shows a
   60-day trial (October 8 to December 7 in this fixture) and a start audit entry.
5. Reopen activation: no restart form, existing dates unchanged.
6. Open customer billing after activation: correct end date, no payment due,
   no checkout. Mobile activation form has no horizontal overflow.

## Authorized Rollout, Later

These are instructions for a separately authorized deployment, not actions already
taken on production. Preserve the existing demo; KW-06 defines the clean launch
environment and any data transfer. The conversion tool can remove demo records
when its existing cleanup options are selected; do not use it as a shortcut for
production setup without the reviewed backup/data plan.

1. Review the scoped change alongside the pre-existing workspace work. Back up
   the target database and apply the additive migration before loading new code.
2. Preview contract settings with:
   `python manage.py prepare_customer_terms kidworks-childrens-center`
3. Apply only to the intended demo or explicitly unstarted customer with an active
   operator account:
   `python manage.py prepare_customer_terms kidworks-childrens-center --apply --actor <operator>`
   This does not convert the school, change its plan, start a trial or send email.
   For a newly created clean customer, initialize deferred activation before its
   first save; the ordinary school-create screen intentionally retains auto-start.
4. Keep setup on Trial with deferred activation. Do not select Starter prematurely.
   Do not enable scheduling or configure final customer data merely to pass KW-05.
5. Only after customer acceptance and the actual written go-live notice: Ops >
   school > Record go-live & start trial. Enter the real sent timestamp/reference.
6. Verify the displayed end date, audit, public/admin access and billing hold.
   Dinuka chooses monthly/annual billing explicitly when paid service begins.

Rollback: before any contract is activated, the additive migration can be reversed
with a backup and matched application rollback; this was tested on disposable data.
After activation, **do not blindly roll back to code that assumes 30 days** or drop
the trial fields. Preserve the contracted end dates and overrides through a reviewed
rollback/data plan. Retaining columns alone does not make old 30-day logic correct.
