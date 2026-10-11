# M7c2: Confirmation Queue and Recovery

Updated October 4, 2026. Local-only delivery/recovery slice; no production activation or push.

## Scope and Impact

A reusable Bookings outbox captures confirmation snapshots in the same database transaction as
booking/rescheduling/cancellation. A unique workspace/event key makes capture idempotent;
different contents cannot silently replace the same event. Later updates replace pending older
messages for the same booking, but never pretend to recall in-flight or accepted messages.
Existing lesson confirmation history is retained. Historical notices are not automatically queued;
an owner may explicitly queue only the latest old notice.

Owner-only controls enable local processing, process bounded batches, queue eligible drafts/retries,
and suppress messages. Schedulers/viewers have read-only history. Settings use existing stale-edit
protection. Views use existing tenant/role checks, Django CSRF, non-cacheable/no-index responses,
and pagination. Family/student access is checked again before a family confirmation is processed.

No new email provider implementation: transport reuses `get_school_email_connection` from the
existing notification service and Django EmailMessage. This slice deliberately calls the global
factory without school SMTP credentials and insists on the exact in-memory mail backend.
DEBUG, `BOOKINGS_MESSAGE_PREVIEW` and that backend must all be configured. Workspace processing
is also default-off. No SMTP/Resend, real recipients, payments, Stripe, Wix or Notion calls.
Messages reaching the local backend are labelled **Simulated, not sent**, not Delivered.

Runtime changes are confined to Bookings and its templates/styles. No shared notification,
enrollment, payment, billing, login or school YAML changes. The tracker adds this report.
Migration 0013 adds outbox/attempt tables, nullable notice linkage and default-off workspace
processing. Applied only to the isolated Bookings preview. No main-local or deployed migration,
no school-profile backfill and no automatic replay of old confirmations.

## Failure Semantics

- Claims serialize per workspace and have a unique token and two-minute lease. Transport runs
  outside database locks; writes require the same token, so stale workers cannot overwrite review.
- Definitive rejection schedules exponential backoff (2 then 4 minutes), at most three automatic
  attempts. An owner may explicitly queue up to two additional attempts: five lifetime maximum.
- Unknown acceptance and interrupted workers become Needs review, never automatic retry.
  An unresolved earlier message blocks later messages for that booking, not other bookings.
  Suppressing the reviewed message releases the newer update. A local pause stops new claims;
  it cannot revoke a claim already being processed.
- If transport accepts a message but saving the result fails, recovery does not resend it. The
  durable attempt becomes uncertain. This is conservative ambiguity handling, not a false
  promise of exactly-once SMTP delivery or verified arrival in an inbox.
- Raw exceptions, credentials and provider error text are not stored. History records fixed
  result codes and human-readable outcomes. Message content remains scoped private staff data.
- No background worker is installed. Retry times persist, but processing resumes only when
  the owner runs a batch or the local command is invoked. Restart does not reset retry budgets.

Local command: `manage.py process_booking_messages --school music-demo --limit 20`, using the
isolated preview settings. It fails closed with non-local email configuration.

## Verification

Final full regression: **2,793 tests passed**, two existing warnings, **133.74 seconds**.
The five new message modules have **100% Python statement and branch coverage**: 310 statements
and 60 branches. The wider measured set (including existing notification/billing/login code and
the tracker) is 97.54%; that is not a claim of 100% coverage across the repository.
External sockets were blocked; PostgreSQL tests used the disposable local test database.
No real provider, paid API or email calls were made.

Evidence: `/tmp/pontora-m7c2-messages-final-tests.log` and
`/tmp/pontora-m7c2-messages-final-coverage.json`. `git diff --check` passed.

Tests cover atomic capture/rollback, event collision, concurrent workers, acknowledgement failure,
stalled leases, stale workers, backoff and attempt caps, uncertain outcomes, supersession,
recipient scope changes, role/tenant/CSRF gates, stale settings, migration preservation,
legacy-notice opt-in, backend safety, pagination and the processing command.

## Remaining Work

This completes a local queue/transport simulation, not production notification delivery. Real
sender identity, school SMTP/Resend adapter acceptance semantics, verified recipient re-entry,
bounce/complaint handling, production scheduling/monitoring and provider verification are still
required. A stable Message-ID is not provider idempotency. Family-access changes after transport
has started cannot recall a message. Real integration needs explicit policy and operational review.

Guest re-entry and online paid-trial settlement remain subsequent M7c2 slices. Existing staff
cancellation/rescheduling stays in place; no guessed Emily policies or SBMC activation.

## Browser Checks

Use [Messages](http://127.0.0.1:8002/bookings/music-demo/messages/) and the expanded M7c2 row
in [the tracker](http://127.0.0.1:8003/). Local owner `bookings-owner` / `admin`.

Completed against the isolated preview on October 4:

1. Enabled local auto-queue for Music Studio Demo. Existing confirmation history stayed untouched.
2. Explicitly queued only Taylor Trial Demo's latest historical cancellation. Simulated rejection
   produced Retry scheduled; processing after the two-minute backoff succeeded. The detail shows
   both attempts and labels the final state Simulated, not sent.
3. Booked fictional Robin Message Demo through the public trial form for October 7, 2 PM.
   Booking and confirmation appeared together. Simulated an unknown confirmation outcome.
4. Staff rescheduled Robin to 4 PM. The newer update stayed queued behind the unresolved attempt;
   unrelated Taylor processing succeeded. The new waiting explanation links to the blocking message.
5. Restarted the preview. Message states, attempt counts and waiting explanation persisted.
6. Reviewed and suppressed Robin's ambiguous older message. The latest reschedule then processed
   once. Running another batch processed zero messages; no duplicate attempt was created.
7. Checked 1280px desktop and 390px mobile. Forms use the existing workspace field styles;
   narrow tables scroll within their own region, not the page. Native horizontal scrolling exposed
   the outcome columns. There were no browser console errors on the final workspace.

Manual review found and fixed two issues before handoff: a blocked update was labelled only
Queued with no explanation, and default form/table markup was cramped on narrow screens.
Regression tests cover the waiting links and tenant isolation; the complete suite was rerun after
the fixes. Mobile table scrolling was verified with native browser scrolling, not merely CSS inspection.

Screenshots: `/tmp/pontora-m7c2-messages-desktop.png`,
`/tmp/pontora-m7c2-messages-mobile.png`, `/tmp/pontora-m7c2-messages-detail-mobile.png`,
and `/tmp/pontora-m7c2-messages-mobile-outcomes.png`.

Fixture retained: Robin Message Demo, booking `af981856-b34b-4a93-a9af-894205ffaa08`,
October 7 at 4 PM, $30 pay-at-studio test quote with no payment recorded. Contact uses
`message-demo@example.invalid`. This is not an actual school customer or charge.
