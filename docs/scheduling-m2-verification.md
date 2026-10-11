# M2 Impact and Verification

Date: October 1, 2026 (America/Los_Angeles).
Status: ready for user testing. Not accepted, committed or pushed. Tested working tree based on
`7ccd62b02ee2c4aeb0621ae4a82cd50af9034cf6`; unrelated SBMC homepage work remains unmodified.

## Scope

Generic unpaid workshop intake: owner-configured choice groups/options, optional image URLs
and color swatches, multiple named participants with separate choices, persisted requests,
tenant-scoped staff details and preparation totals. Owner/scheduler can withdraw a request;
history remains while its participants leave pending preparation.

Intake is explicitly opt-in per workshop and defaults off, independently of publishing.
Requests are not confirmed bookings, held seats or payments. No outbound emails, API calls,
Wix/Notion connection, school-specific conditionals or new payment implementation.

## Impact Review

- M2 changes only scheduling models/services/views/routes/templates/static assets plus local
  status-viewer documentation. No new shared login, enrollment, billing, email or navigation edits.
- Existing M1a shared settings/URL/login changes remain in the uncommitted working tree and
  are covered by their earlier impact record and the full regression run. They are not M2 changes.
- Reuses Django identity and the existing AdminAuditLog, with tenant metadata and no purchaser
  name/email copied into audit text. Bookings-only roles do not grant enrollment permissions.
- Migration 0003 adds four scheduling tables and a default-false offering field; no core table
  or data migration. Applied only to the isolated local preview database. Forward/reverse tests
  preserve existing M1b setup and verify that intake defaults off.
- Supported setup/intake/withdrawal writers serialize on the tenant profile row. Concurrent
  repeats of the same signed form create one request and one set of participants.
- Two-hour signed forms bind session, browser and current schedule/catalog. Stale selections,
  wrong-tenant options, invalid names/emails/counts and changed-payload retries are rejected.
- Saved labels and date/time snapshots preserve request history. Pending requests prevent
  staff from silently moving their session or shrinking capacity below a requested group size.
- Public receipts are browser-session-bound, non-cacheable and omit attendee/contact details.
  Staff routes require tenant membership; read-only staff cannot edit or withdraw. CSRF and
  POST rate limiting apply; this is not a complete production anti-abuse strategy.
- Image settings accept HTTPS or constrained local static image paths, not script/file URLs.
  Rendering is browser-side with no-referrer and no server-side download. Local preview uses
  an existing illustrative image. Approved catalog/assets and deployment asset handling remain
  pilot work, not a claim of connected customer inventory.

## Verification

Scheduling: 164 tests passed in 6.19 seconds. Scheduling Python code: 750 statements,
134 branches, 100% line and branch coverage. Includes real PostgreSQL concurrent duplicate
requests, rollback after audit failure, migration round trip, receipt privacy, permissions,
stale/expired forms, HTML escaping, pagination and exact preparation totals.

Full regression: **2,118 passed, 2 warnings in 45.45 seconds**. Coverage for scheduling plus the
local status viewer and existing login module: 98.96% combined line/branch; scheduling 100%,
viewer 99.21%, legacy login 90.43%. This is Python coverage, not JavaScript/template coverage.

Commands used from the repo root (temporary local test settings and network guards):

```sh
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings COVERAGE_FILE=/tmp/pontora-m2-focused.coverage venv/bin/python -m pytest scheduling -p pontora_local_db_tests --cov=scheduling --cov-branch --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings COVERAGE_FILE=/tmp/pontora-m2-full.coverage venv/bin/python -m pytest -p pontora_local_only_tests --cov=scheduling --cov=scripts.project_status --cov=core.views_login --cov-branch --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_ui_settings venv/bin/python manage.py check
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_ui_settings venv/bin/python manage.py makemigrations --check --dry-run
```

Logs: `/tmp/pontora-m2-focused.log`, `/tmp/pontora-m2-full.log`.
Django checks: no errors; existing duplicate `jazzmin` template-library warning remains.
Migration drift check: no changes detected. `git diff --check`: clean.

## Browser Checks

Actual local browser tests used the isolated PostgreSQL preview on port 8002, no paid APIs,
no real email delivery and fictional customer data:

1. Created Design/Color groups and Cat/Bunny/Blue/Pink options through the owner UI. Invalid
   hex color was rejected; valid swatches saved. Explicitly enabled intake on the workshop.
2. Submitted two participants with different choices. Add/remove-last retained existing values.
   Missing required input prevented submission; participant-name browser validation was tightened
   during review (server validation already existed). Reference photo loaded successfully.
3. Receipt clearly said unpaid/unreserved. Refresh did not add a duplicate. Staff details and
   preparation showed exactly two participants and one of each selected option.
4. Withdrew that request using its confirmation checkbox. History remained; pending roster and
   totals became empty. Created a second request for the final local demonstration.
5. Restarted the preview and reloaded: second request, both participants and totals persisted.
6. Read-only staff could view requests but had no withdrawal or choice-edit controls. Automated
   direct-route tests additionally covered unauthorized writes and cross-tenant access.
7. SBMC and Kid Works smoke users opened their existing enrollment dashboards/navigation,
   without Bookings links or new scheduling permissions. No live school/account was changed.
8. Customer form inspected at 1280px, 390px and 320px, including image rendering and no page
   horizontal overflow. Preparation inspected at 1280px and 390px; phone navigation scrolls
   within its own strip and long contact text wraps. No new console warnings/errors observed.
9. Local tracker expanded M1a, M1b and M2 with correct steps and local credentials. Open
   questions appeared above milestone rows; 390px tracker had no page horizontal overflow.

Screenshots: `/tmp/pontora-bookings-m2-customer.jpg`,
`/tmp/pontora-bookings-m2-mobile.jpg`, `/tmp/pontora-bookings-m2-preparation.jpg`.
The tests cover native request intake, not provider feasibility or production deployment.

## Boundaries and Rollback

This is a local request prototype, not production-ready reservations. Aggregate pending
requests can exceed capacity; preparation warns about that. Per-request maximum is the lower
of 20 or session capacity. No payment, automatic confirmation, waitlist, inventory decrement,
customer self-service change/cancel, emails or external connectors are claimed.

Immediate rollback: disable request intake on the affected workshop (or the Bookings profile)
and retain request history. Do not reverse migration 0003 after real intake without an approved
export/backup: reversal removes request/participant/choice tables. No production migration,
school enablement, remote credential changes, commit or push performed.

M3 remains planned until the user accepts M2. The local tracker includes per-milestone manual
steps and local credentials; the viewer listens only on loopback and must not be published.
