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

## Approved Tenant Assignment

Production: South Bay Music only.

Demo: Enrollment Request Demo, Multi-form Demo, Young Minds LA, DUC Learning Center, Beverly Hills Gymnastics, World Famed Masters, Kidworks Children's Center and Plushumi Demo.

The first four require classification correction from non-demo to demo. This is an operator migration decision, not a customer-name branch in application code.

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
3. Deploy that exact commit to demo. Migrations run against the demo database only. Verify the relevant user journeys there.
4. Promote the same commit to production explicitly. Run production migrations against production only. Never restore the demo database over production.
5. Verify production health, school access, enrollment and payment/webhook behavior. Roll back application code only if its migrations are backward-compatible; database restore requires a separate recovery decision.

School onboarding is separate from releasing code. Create the customer in production and apply its reviewed YAML and database-backed configuration. Do not copy sample students, bookings, test payments, demo passwords or access tokens. The old in-place Convert to Customer action is disabled on a dedicated demo deployment because it cannot move data between databases.

## Remaining Live-Payment Gate

Production booking maintenance still needs `--include-live-recovery` when real Square payments are enabled for a studio. Keep this explicit launch gate: verify the live account, webhooks and a small real payment/refund before enabling it. Demo maintenance must never use this flag.

## Verification Status

Provisioning, transfer and hosted cutover evidence are recorded separately. This document describes the contract and checklist; it is not evidence that every cutover step has completed.
