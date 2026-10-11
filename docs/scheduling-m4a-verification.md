# M4a: Configurable Integration Foundation

Date: October 4, 2026. Local foundation ready for user testing; no live connectivity.

## Delivered Scope

This is configuration and an offline payload preview, not a connected Wix/Notion integration.
No HTTP client, webhook endpoint, background sync or activation switch is included in M4a.
The remaining M4 work is engineering work as well as a customer-access dependency.

- Independently configured booking sources and destinations, scoped to a business. A Notion
  destination can receive Pontora bookings or reference a saved Wix source. Several connections
  can coexist. Neither vendor is a requirement for the rest of Bookings.
- Owner/operator settings for connection name, intended use, vendor identifiers, write-only
  credentials and destination fields. Viewer/scheduler roles cannot open or post these settings.
- Wix preparation fields: site ID, optional visitor OAuth client ID, server API key. Notion:
  data source ID, internal connection token, property mappings and status-property type.
- A small provider registry, provider forms and a destination adapter contract. New providers
  require their own adapter and tests; entering an arbitrary API URL cannot create an integration.
- Provider-neutral immutable booking/participant snapshots and a deterministic Notion payload
  projection. Participant selections stay paired: Avery's design/color cannot become Casey's.
- Type-aware title, text, email, date and status/select properties. Only mapped properties are
  included, leaving unrelated staff-owned properties untouched. Oversized data is rejected,
  never silently truncated. Preview uses fictional data, not a real reservation or remote schema.
- Encrypted credentials, stale-edit protection, audit records without secrets, CSRF and
  tenant/role checks. Saving, reloading and previewing do not contact a vendor or change who
  controls booking capacity or payment collection.

## Configuration Versus Platform Secrets

Customer-specific IDs, tokens and mappings belong in the authorized settings screen. One
operator-managed key ring, `BOOKINGS_INTEGRATION_KEYS`, protects stored credentials; do not
create one environment variable per business. It accepts comma-separated Fernet keys, newest
first. Alternatively, application settings may supply a list of keys.

There is deliberately no fallback to Django's secret key. Missing/invalid keys prevent a new
credential from being saved, but permit non-secret draft settings. Back up the encryption keys
separately from the database before deployment. Losing every valid key loses credential access.
Rotation retains older decryption keys while replacements are written with the first key;
old credentials must be re-encrypted or re-entered before removing their old key. No bulk
re-encryption command is implemented here.

Fernet authenticates ciphertext; the encrypted envelope binds it to business, connection UUID,
provider and intended-use environment. Copying ciphertext into another connection fails closed.
Credentials are not returned in HTML, mapping previews or audit changes. Sensitive request/frame
annotations protect Django error reports; external monitoring redaction must still be verified
before any real credentials are introduced. Test startup clears inherited integration keys.

Testing/Production labels separate configurations; they do **not** create a Wix/Notion sandbox
or prove that a credential belongs to a test account. The labels are immutable after creation.
Use separate authorized test sites/data sources when live verification is approved.

## Research and Consequences

Reviewed the official documentation on October 4, 2026:

1. [Wix REST authentication](https://dev.wix.com/docs/api-reference/articles/rest-authentication/rest-api-authentication):
   site-scoped server calls require the site's identity and the applicable authorization.
   An account API key is not a substitute for visitor identity in customer booking/checkout.
   M4a stores the bootstrap server credential but does not claim it proves checkout authorization.
2. [Wix OAuth](https://dev.wix.com/docs/build-apps/develop-your-app/auth/authenticate-using-oauth):
   installed-app authentication needs app credentials and installation context. Scalable app
   installation, OAuth/token lifecycle and permission checks remain part of the connected work.
   Platform app secrets must not be duplicated into each customer's ordinary settings form.
3. [Wix booking flow](https://dev.wix.com/docs/api-reference/business-solutions/bookings/flow-single-service-booking)
   and [webhooks](https://dev.wix.com/docs/build-apps/develop-your-app/api-integrations/events-and-webhooks/about-webhooks):
   verify the specific workshop/service, forms, identity and checkout lifecycle, including direct
   Wix bookings. Do not infer this from a successful catalog read or use deprecated extra-field examples.
4. [Notion authorization](https://developers.notion.com/guides/get-started/authorization):
   internal tokens require explicitly shared content; public multi-customer connections need
   a proper authorization lifecycle. M4a's draft token is not a verified Notion installation.
5. [Notion data sources](https://developers.notion.com/guides/data-apis/working-with-databases)
   and [property values](https://developers.notion.com/reference/page-property-values):
   target the actual data source, not just its database container. Verify property IDs/types and
   allowed status values before activation. The offline adapter pins API version `2026-03-11`.
   It presently supports one row per booking with participant summaries, not relational attendee rows.
6. [Notion request limits](https://developers.notion.com/reference/request-limits):
   transport must respect provider limits and retry instructions, and reconcile ambiguous writes.
   The local projection bounds text chunks/arrays and conservatively caps its serialized payload.
   This validates request shape/size, not the destination's permissions or schema.

## Extension Contract and Next M4 Work

1. Register a provider's role and supported operations; supply typed validation and secret
   handling. Add a provider-specific source or destination adapter behind the shared workflow.
   Do not add client-name branches to booking logic. Unsupported operations fail explicitly.
2. For sources, normalize identity, participants and lifecycle events. Persist origin account/site,
   environment, booking/attendee IDs and revision. M4a's small projection snapshot is not yet a
   complete source-event contract; location, service, resource and payment summaries need typed
   additions when their source mappings are implemented.
3. Before activation, verify permissions, source authority, actual destination schema and selected
   field ownership. Choose one scheduling/payment authority per inventory pool; drafts cannot
   switch it. Connected Wix checkout must not also invoke Pontora's Stripe checkout.
4. Implement durable receipts/delivery records and stable local-to-provider IDs. Deduplicate events,
   handle out-of-order updates, serialize each booking's delivery, and update its existing destination
   row on edits/cancellation. Reconcile unknown write outcomes before retrying creation. Reuse the
   existing outbox reliability principles, not a message table with incompatible lifecycle semantics.
5. Pin provider hosts, use bounded timeouts/backoff and respect workspace rate limits. Coalesce
   unchanged updates and avoid full database reads for every event. No LLM is needed for mapping.
6. Add connection testing, revocation/disconnect behavior, sync health and recoverable errors.
   A destination outage cannot invalidate a confirmed booking. Staff need access to local prep
   information while recovery is pending. Disconnect must preserve history.
7. Prove an authorized two-participant journey with different selections, checkout, same-row
   delivery, edits/cancellation, duplicate events, revoked credentials, outages and restart recovery.
   Live activation and production deployment require separate approval after these pass.

## Impact Review

- New scheduling-only table: migration `0015_integration_connections`, additive and applied
  only to the isolated local preview. It does not update schools or enrollment records.
- New `integrations` capability is default-off. The old capability migration keeps its original
  literal grants, so future capabilities are not silently enabled for existing profiles.
- Local Workshop Demo alone is opted in. SBMC, Kid Works and Music Demo are not enabled by this work.
- Bookings navigation/CSS and local documentation routes change. Existing booking/payment runtime
  services, enrollment views and shared payment APIs are not changed by this slice.
- Capability removal and settings writes share the profile lock, preventing inaccessible saved
  connections during concurrent changes. Saved history blocks feature removal.
- Added explicit dependency `cryptography==46.0.4` (already present in the local environment).
  Clean-environment installation and remote CI still require verification before release.
- No external API calls, live credentials, customer messages, production migration or push.

## Verification

Final full regression: **2,901 passed**, two existing warnings, **143.28 seconds**. The suite ran
against disposable local PostgreSQL with external network blocked. No paid API calls were made.
The eight new integration Python modules (including the empty package initializer) have **100%
statement/branch coverage**, covering 298 statements and 50 branches. All measured scheduling,
shared-payment and test-safety modules meet the per-module 90% gate; their combined coverage
is **99.92%**. Migrations are excluded from the numerical gate and exercised by database tests.
Coverage is execution evidence, not proof of real vendor compatibility.

Migration drift check: no changes detected. Existing duplicate Jazzmin template-tag warning
remains unrelated. Whitespace checks passed. Remote CI and fresh dependency installation were
not run; no commit/push or deployment was requested for this step.

Tests cover mapping types/limits, multi-day display, participant pairing, alternate adapter
projection, tenant/role/CSRF boundaries, forged settings, encrypted credential lifecycle/key
rotation, missing keys, tampering, audit rollback, stale edits, real simultaneous writes,
capability-removal races, default-off migration behavior and legacy enrollment/payment regressions.

Agent-performed browser checks passed at normal desktop size and 390px mobile:

- Created fictional Wix and Notion connections, saved, reopened and restarted the server.
  IDs, mappings and the Wix placeholder credential persisted; credential input remained blank.
- Changed the destination source from Wix to Pontora, saved and previewed; restored Wix and
  previewed again. The selection is independent, but neither route performs real delivery.
- Selected Notion's Status property type; both request structure and human preview match it.
- Confirmed Avery/Cat/Blue and Casey/Bunny/Pink stay paired, with stable field order and dated
  start/end times. Preview contains no real booking or customer data.
- Submitted duplicate destination property names: clear rejection. Reloading retained the
  saved mapping and source instead of the invalid edits. Restored the demonstration settings.
- Checked desktop/mobile wrapping, no horizontal page overflow, and keyboard navigation to
  the preview. Restored the browser's normal viewport after testing.
- Local database read verified that only `workshop-demo` has the capability and two draft
  connections exist. No customer workspace or existing payment authority was enabled.

Local screenshots: `/tmp/pontora-m4a-settings-desktop.png`,
`/tmp/pontora-m4a-settings-mobile.png`, `/tmp/pontora-m4a-preview.png`.
Full test log: `/tmp/pontora-m4a-final.log`; coverage: `/tmp/pontora-m4a-final-coverage.json`.

Critical review corrected the preview's unstable database-JSON field order and missing end date
for multi-day events. Regression tests cover both. This step intentionally stops before external
activation; outstanding provider implementation is not disguised as a credential-only blocker.

Manual steps and local-only credentials are in the M4a row of the
[implementation tracker](scheduling-implementation-tracker.md). The real Wix/Notion contract,
network delivery, OAuth lifecycle, customer data schema, production monitoring/redaction and
deployment are explicitly unverified and unimplemented here, not waived by high local coverage.
