# Demo and Production Separation

## Deployment Contract

- One repository, separate Render web services, PostgreSQL databases and scheduled workers.
- `PORTAL_DEPLOYMENT_ENV=demo` or `production` pins web and worker tenant scope independently of hostname. Local development remains `shared`.
- Both hosted services use `DJANGO_ENV=production` for HTTPS and secure cookies. Demo is not a development-security mode.
- Demo uses sandbox payment credentials only. Staff-triggered demo email remains enabled without a recipient allowlist; automatic customer reminders stay disabled.
- Database credentials, Django signing keys and integration encryption keys are separate. Existing integration credentials must be re-encrypted when moved, not copied with a production encryption key.
- The demo service's `BASE_URL`, `APP_BASE_URL` and `DEMO_BASE_URL` all point to its own canonical domain. Production keeps its app URL and the separate demo URL.
- Run `python manage.py check_portal_deployment` after migrations and before starting either web or scheduled workers. Demo refuses non-demo schools, live payment evidence and live application-fee credentials.
- Public enrollment URLs on either dedicated deployment require an existing school in that database. YAML alone cannot recreate a moved or absent tenant. Operator-created schools inherit the deployment classification.
- Disable automatic production deployment. Release an explicitly tested Git commit, not whichever commit happens to be newest on main.
- Demo web and demo maintenance follow `main` with Render **After CI Checks Pass**.
  A push requires no separate demo-deploy action. Both production services stay
  **Off** for automatic deployment. Verified in Render on November 10, 2026.
- Maintain [the release tracker](release-tracker.md) for code revisions, approval,
  pending customer decisions and configuration activation. Demo approval does not
  automatically authorize production activation.

## Approved Tenant Assignment

Production: South Bay Music only.

Demo: Enrollment Request Demo, Multi-form Demo, Young Minds LA, DUC Learning Center, Beverly Hills Gymnastics, World Famed Masters, Kidworks Children's Center and Plushumi Demo.

The first four were corrected from non-demo to demo during the November 9, 2026 cutover. This was an operator migration decision, not a customer-name branch in application code.

## Cutover Checklist

1. Confirm the final full regression revision/fingerprint and GitHub checks before release.
2. Provision the separate demo database and web service without moving public domains.
3. Inventory tenant ownership, payment environments, files, user memberships and foreign keys. Reject cross-tenant relationships; never use shared actor IDs to infer ownership.
4. Pause demo writes and demo jobs during the final snapshot. Keep production enrollment available.
5. Transfer only approved demo tenants and their owned records into an empty destination. Preserve IDs, balances, payment evidence and template ownership. Do not copy sessions, OAuth handshakes or unrelated production users.
6. Re-encrypt integration credentials with the demo key. Remove live billing references/credentials from demo schools; preserve sandbox financial history.
7. Verify foreign keys, sequences, record counts and financial totals. Confirm no South Bay Music school or owned data exists in demo.
8. Correct the source classification of the four approved schools, and enforce production-only web and worker scope. Retain the old demo records as a rollback snapshot until the separate database has been verified; do not run jobs against both copies.
9. Move both demo domain aliases, check TLS/DNS, canonical links, login, passwordless access, file downloads and sandbox callbacks. Keep both app aliases on production.
10. Start the demo-only maintenance job. Verify production subscription and enrollment behavior using read-only checks.
11. Restrict external database access again after transfer. Record resource IDs, deployed revisions, costs and any remaining shared storage credentials or operational gaps.

## Local to Demo to Production

1. Develop and test locally using the existing local server and disposable test database.
2. Commit reviewed code and pass the required complete test suite and CI checks.
3. Push to `main`. After GitHub checks pass, Render automatically deploys demo web
   and maintenance. Verify both deploys reached that exact commit and check the
   relevant hosted user journeys. Migrations affect demo only.
4. Promote an approved, tested commit to production explicitly, including its
   maintenance job. Review every intervening change in the release tracker, not
   just the latest commit. Run production migrations against production only.
   Never restore the demo database over production.
5. Verify production health, school access, enrollment and payment/webhook behavior. Roll back application code only if its migrations are backward-compatible; database restore requires a separate recovery decision.

School onboarding is separate from releasing code. Create the customer in production and apply its reviewed YAML and database-backed configuration. Do not copy sample students, bookings, test payments, demo passwords or access tokens. The old in-place Convert to Customer action is disabled on a dedicated demo deployment because it cannot move data between databases.

### Features Awaiting Customer Approval

Prefer the existing scoped capabilities and workflow settings: enable a proposed
workflow in the demo school's configuration, keeping the production school's
defaults unchanged. Gate server-side actions and jobs as well as navigation.
Deploying disabled code is not the same as activating a feature, and shared
changes and migrations still require regression verification.

A later commit includes earlier merged changes. If an unapproved change cannot
be safely disabled, do not deploy the latest main and pretend it excludes that
change. Build a short-lived release branch from the current production commit,
select only approved changes with their dependencies, and test that exact branch
and migration sequence before release. Record excluded changes and merge any
release-only fixes back into main. Avoid permanent customer branches.

Render's [CI integration](https://render.com/docs/deploys#integrating-with-ci)
requires passing checks; failed or missing checks do not trigger deployment.
Deploying a specific demo commit can turn automatic deployment off, so recheck
the demo web and maintenance settings after emergency rollbacks.

## Customer Go-Live Process

Use **Super admin > Schools > the customer > Go-live checklist** as the
customer-specific record. The checklist stores completion time and operator,
preserves existing completion history, and groups work into workspace/access,
customer experience, payments/verification, and release/launch.

1. **Keep practice and production separate.** Leave the demo school in demo.
   Create a separate real school on app with its own owner and staff access.
   Approve the plan, contract and any trial start conditions before activation.
2. **Apply approved configuration.** Review operator-managed YAML and editable
   database settings, enabled features, branding, programs, prices and policies.
   Re-enter production integration credentials securely. Import only approved
   real customer records using a reviewed, scoped import with counts/totals;
   never copy the entire demo database or its sample records and access tokens.
3. **Verify the enabled customer journeys.** Exercise enrollment, leads and/or
   bookings as applicable, including validation, consent, capacity, changes,
   cancellation and staff review. Verify scoped staff permissions. Check desktop,
   phone and tablet layouts, plus the actual staff device before acceptance.
4. **Connect production communications and payments.** Approve templates and
   triggers, choose school SMTP or Pontora email, and verify delivery to an
   approved inbox with production links. For online payments, verify account and
   location, signed webhooks, receipts, totals, refunds and recovery. Obtain the
   owner's approval before a small live charge/refund. A physical terminal needs
   an on-site live test; sandbox does not prove hardware connectivity. Confirm
   free/offline-only workflows do not expose online checkout.
5. **Release the tested revision.** Follow Local to Demo to Production above.
   Verify backups, migration compatibility, health checks, scheduled jobs,
   monitoring and rollback before opening customer traffic. Do not use a passing
   test result from a different revision or working tree.
6. **Agree the cutover.** Record customer approval, launch time and the
   authoritative system for bookings/enrollment. Update the customer's website
   to the production links and avoid taking duplicate bookings in both systems.
   Confirm staff access, any required written go-live notice and trial activation,
   then monitor the first real activity and alerts.

### Checklist Applicability

- Enrollment forms, lead capture, reports, scheduling and trial checks follow
  the school's existing configuration. Irrelevant steps appear under **Not
  required for this setup**, outside the progress total, rather than being
  deleted. Missing enrollment configuration remains a review requirement.
- Online payment checks follow configured enrollment fees or reservation-payment
  capability. That capability can also support offline collection, so the
  guidance explicitly includes checking an offline-only launch.
- A demo can record preparation, but production-only checks are disabled and
  cannot count as complete there. Complete those checks on the real school in app.
- These are manual operator attestations, not automated readiness results.
  Checking **School marked live** does not activate a school, start a trial,
  change billing or deploy code. Use the existing explicit activation controls.
- Completion history survives configuration changes. Review retained completions
  and reopen relevant checks when settings, integrations or launch scope change.

## Remaining Live-Payment Gate

Production booking maintenance still needs `--include-live-recovery` when real Square payments are enabled for a studio. Keep this explicit launch gate: verify the live account, webhooks and a small real payment/refund before enabling it. Demo maintenance must never use this flag.

## Verification Status

The database/service split and hosted cutover completed November 9, 2026.
Production has South Bay Music; demo has the eight schools listed above. The
services, databases, signing/encryption keys and scheduled jobs are separate.
Provisioning, transfer and hosted verification evidence are recorded separately;
this does not certify a future customer's go-live checks or live Square setup.

## Legacy Domain Review (November 9, 2026)

| Alias | Current destination | Canonical domain |
| --- | --- | --- |
| `app.enrollifyapp.com` | Production service/database (South Bay Music) | `app.mypontora.com` |
| `demo.enrollifyapp.com` | Demo service/database (eight demo schools) | `demo.mypontora.com` |

These are aliases, not additional web services. The current SBMC trial page links
to `app.mypontora.com`; generated-link environment settings use Pontora domains.
The platform Stripe webhook uses the existing Render service hostname, not either
legacy alias. Remaining environment references to the aliases are host/origin
allowlists. These checks do not establish that historical emails or bookmarks
are unused, so neither alias has been removed.

Before retirement, review host-specific traffic and external integrations, offer
a redirect period for ordinary browser links, and test old signed/public links.
Do not rely on redirects for webhooks or form submissions. Then remove the
custom-domain bindings, DNS records and corresponding host/origin allowances
together, leaving both canonical domains untouched.

[Render custom-domain pricing](https://render.com/docs/custom-domains) includes
an allowance per workspace plan and charges $0.25/month per additional domain.
Removing both aliases can save at most $0.50/month in domain overage, depending on
the plan and other domains. It does not remove web-service charges or cancel the
separate annual registration of `enrollifyapp.com`.
