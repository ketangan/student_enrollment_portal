# Enrollment link domains

## Cause

Lead Details used `app_reverse()` for its copyable enrollment/resume URL and
Start Enrollment redirect. Resume emails used the same production-only builder.
Localhost appeared correct because `APP_BASE_URL` was local there; the incoming
demo host was never consulted.

## Scoped fix

- `request_reverse()` chooses the matching configured `DEMO_BASE_URL` or
  `APP_BASE_URL`. The configured scheme is preserved even behind a reverse proxy.
- During local development, loopback hosts retain the incoming scheme and port.
- Other allowed hosts fall back to the existing app URL. Disallowed hosts are
  rejected by Django. Hostnames are matched exactly, not with suffix matching.
- Lead capture, displayed enrollment links, Start Enrollment redirects, and
  resume emails use this helper. Both public save/resume form paths pass the
  request through to the email helper.
- Submission list application links, the family-status link in Submission Details,
  emailed status links, and original/resend confirmation emails also use it.
- Existing upfront application-fee payment confirmation URLs use it on both
  initial checkout and retry. Stripe keys, payment mode, amounts, and payment
  processing are unchanged; only the return destination changes.
- Calls without a request and the explicit app/demo builders retain their old
  behavior. Production SBMC links stay on the app domain.

No new environment variables, database changes, token changes, subscription changes,
or changes to authentication are needed. Existing emailed URLs cannot be changed;
copy/resend from the corrected page after deployment.

This is not a global environment-routing change. Other explicit app links,
including subscription billing, authentication, and internal notification links,
remain outside this patch. Domain selection does not select or isolate a database
or Stripe mode.

## Verification

Latest complete regression: **1,865 passed**, one known PostgreSQL-only migration
test excluded on SQLite; external network connections blocked.

The follow-up passed 86 focused playdate, domain, and existing application-payment
tests with external network access blocked. The earlier domain-only patch passed
19 focused tests and 1,830 core regression tests, with one known PostgreSQL-only
migration test excluded on SQLite.

`core/tests/test_enrollment_link_domains.py` covers configured demo/app hosts,
localhost with ports, unknown/spoofed hosts, explicit URL-builder compatibility,
Kid Works and SBMC lead pages, enrollment redirects, resume email bodies, and
parent save/resume emails. Follow-up tests cover displayed and emailed family
status links for Kid Works and SBMC on demo/app/localhost, actual public application
confirmation, and payment confirmation/retry URLs. Email uses the in-memory
backend and Stripe is mocked; no messages or payment requests are sent.

For deployed verification, use a demo lead with a test email on
`demo.mypontora.com`. Check the displayed enrollment URL and Start Enrollment
destination both begin with `https://demo.mypontora.com/`. Email the link only to
the test mailbox and verify the same domain. Repeat on `app.mypontora.com` with
an appropriate test record to confirm the production domain remains unchanged.
