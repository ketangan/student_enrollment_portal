# M3 Impact and Verification

Date: October 1, 2026 (America/Los_Angeles).
Status: ready for user testing. Not accepted, committed or pushed. Tested working tree based on
`7ccd62b02ee2c4aeb0621ae4a82cd50af9034cf6`; unrelated SBMC homepage work remains unmodified.

## Scope

Generic, explicitly opt-in party inquiries: organizer contact, preferred time/duration,
group size, shared/private-room preference and customer notes. Tenant-scoped staff can
review or close an inquiry, propose a room, see overlapping internal activity, record
preparation notes and a USD total/deposit with an exact calculated balance.

Reviewed means reviewed, not confirmed. There are no reservations, charges, customer quote
deliveries, emails or external connectors. Internal pricing proposals are not a second
payment module. M5 must reuse the shared payment foundation for any real collection.

## Impact Review

- M3 changes only scheduling models/services/forms/views/routes/templates/styles and project
  documentation/viewer links. No new shared login, enrollment, payment or email edits.
- Earlier M1a shared settings/URL/login changes remain uncommitted and are included in the
  regression run; see earlier impact records. Other-window SBMC homepage work is not edited.
- Reuses tenant profiles, Django identity, booking-specific roles, offerings, locations,
  rooms, request digest/browser binding and the existing tenant-scoped audit service.
- Migration 0004 adds a default-false offering field and one scheduling table. No core model
  or data migration. Applied only to the isolated preview, not the main local or production DB.
- Supported writes serialize on the tenant profile. Signed, browser-bound, two-hour intake
  tokens make identical retries idempotent; changed payloads or stale configuration fail.
- Staff saves require the current revision. Competing edits cannot overwrite each other.
  Audit failure rolls back the whole write; audit records omit contact and preparation text.
- Public receipts show only a reference and request-only notice, are browser-bound and
  non-cacheable, and never expose internal prices, notes or staff decisions. Staff reads and
  writes are tenant/role scoped. CSRF and POST rate limiting remain in force.
- Location timezone freezes once party history exists. Proposed rooms must match location
  and fit the group; reducing capacity below an active assigned party is rejected.
- Party-only offerings may publish without a fictional dated session only when party intake
  is explicitly enabled. Existing ordinary workshop publication rules are unchanged.
- Dates are interpreted in the location timezone; ambiguous/nonexistent local times fail.
  Duration uses elapsed minutes across daylight-saving transitions. Money uses Decimal and
  database constraints reject incomplete/negative quotes or a deposit above the total.

## Verification

Final focused run: **221 scheduling tests passed in 8.77 seconds**. Scheduling Python:
1,000 statements and 184 branches, **100% line and branch coverage**. Tests include real
PostgreSQL concurrent duplicate inquiries and competing staff edits, audit-failure rollback,
forward/reverse migration preservation, default-off intake, tenant/role isolation, receipts,
CSRF/rate limits, stale/expired forms, DST boundaries, invalid quotes and overlap boundaries.

Full regression: **2,176 passed, 2 warnings in 48.03 seconds**. Scheduling plus local viewer
and legacy login coverage: 99.16% combined line/branch. Scheduling is 100%, viewer 99.21%,
legacy login 90.43%. These are Python measurements, not template/browser coverage claims.

Commands from the repository root using temporary, isolated local settings/network guards:

```sh
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings COVERAGE_FILE=/tmp/pontora-m3-focused.coverage venv/bin/python -m pytest scheduling -p pontora_local_db_tests --cov=scheduling --cov-branch --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_test_settings COVERAGE_FILE=/tmp/pontora-m3-full.coverage venv/bin/python -m pytest -p pontora_local_only_tests --cov=scheduling --cov=scripts.project_status --cov=core.views_login --cov-branch --cov-config=/tmp/pontora_m1a.coveragerc --cov-report=term-missing
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_ui_settings venv/bin/python manage.py check
PYTHONPATH=/tmp:$PWD DJANGO_SETTINGS_MODULE=pontora_m1a_ui_settings venv/bin/python manage.py makemigrations --check --dry-run
```

Logs: `/tmp/pontora-m3-focused.log` and `/tmp/pontora-m3-full.log`.
Django checks have no errors; the pre-existing duplicate `jazzmin` template-library warning
remains. Migration drift check found no changes. External calls were blocked throughout tests.
The final documentation/viewer check passed 41 tests in 2.36 seconds. `git diff --check`
reported no whitespace errors in tracked changes.

## Browser Checks

Isolated preview on port 8002 with fictional data and external API/email calls blocked:

1. Owner explicitly enabled party intake without changing ordinary workshop request settings.
   Public schedule retained Request places alongside the new Plan a party entry point.
2. Submitted Alex Party Demo for October 6, 10:30 AM, two hours, eight participants and a
   private room with preparation notes. Mobile form and receipt rendered correctly; receipt
   clearly said the request was neither reserved nor paid.
3. Staff review initially showed Needs review with no room or quote. Deposit 500 against
   total 400 was rejected without changing saved state. Valid 400/100 saved with balance 300.
4. Room 1 produced the expected warning for its overlapping published 10:00-11:30 workshop.
   Reviewed (not booked) and no-reservation/no-payment wording remained visible.
5. Two browser tabs editing the same review rejected the older save and preserved the newer
   preparation notes and original quote. Review found that browser refresh could resubmit an
   invalid POST; an explicit Open saved review link was added and verified to recover saved data.
6. Read-only staff could inspect the party, notes and warning but had no editing form. Direct
   unauthorized/cross-tenant writes are additionally covered by automated route tests.
7. Existing M2 preparation still showed two participants and one each of Cat, Bunny, Blue,
   and Pink. Its withdrawn request remained historical, excluded from preparation totals.
8. Restarted the preview, then reloaded the party: room, notes, Reviewed status and 400/100/300
   proposal persisted. Error recovery also worked after the restart.
9. SBMC and Kid Works smoke accounts still opened their enrollment dashboard with original
   Leads/Submissions navigation and no Bookings links. No live school account was changed.
10. Inquiry/review layouts checked at 1280px, 390px and 320px; no page horizontal overflow,
    controls remained usable and text wrapped. No new JavaScript errors in the inquiry page.
    Intentional invalid-form POST responses returned HTTP 400 as expected.
11. Refreshed the local browser tracker: M3 shows Ready for your test, its row expands to the
    manual checklist and local credentials, open questions remain visible, and the new M3
    verification link opens this report. The viewer remains loopback-only.

Screenshots: `/tmp/pontora-bookings-m3-inquiry.jpg`,
`/tmp/pontora-bookings-m3-inquiry-mobile.jpg`, `/tmp/pontora-bookings-m3-review.jpg`,
`/tmp/pontora-bookings-m3-review-mobile.jpg`. These local files contain fictional preview data.

Self-review also corrected the public badge for party-only offerings: it now says Party
inquiries rather than Registration not open. An automated rendered-response assertion covers it.

## Boundaries and Rollback

Room warnings cover internal published sessions and active party inquiries, not external
calendars, teacher availability, buffers or inventory. Even a warning-free review does not
reserve anything. M5 must enforce exclusive resources atomically before confirming/charging.

No participant-by-participant party roster, public quote acceptance, date-change tool,
automatic correspondence, payment collection or provider integration is claimed. Staff can
decline/withdraw an inquiry without deleting history. No production anti-abuse guarantee.

Disable party intake on the workshop or disable the Bookings profile to stop new inquiries
while retaining history. Do not reverse migration 0004 after real use without an approved
backup/export: reversal drops party inquiries. No production migration/enablement, commit
or push. M4 waits for user acceptance and its authorized sandbox/access dependencies.
