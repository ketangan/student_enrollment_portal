# School-Specific Subscription Pricing

Updated October 14, 2026. Released to the shared Render service as `0302149` after explicit approval and successful pre-merge checks. Existing schools have not been assigned negotiated prices automatically. The local sandbox instructions below remain available for acceptance testing.

## Production Release Verification

- [Release PR #2](https://github.com/ketangan/student_enrollment_portal/pull/2) merged to `main`; Render deployment `dep-db871jtg1s2s73f9d370` became live at 11:22 PM Pacific on October 14.
- Fresh Render-managed database export completed at 11:02 PM Pacific; three-day point-in-time recovery was available. No production database was downloaded locally.
- Render's existing startup command automatically applied `core.0065` and `core.0066`, both logged `OK`, before starting Gunicorn. No manual migration command is needed. These are additive tables/constraints, not customer-data migrations.
- Local full suite: **3,945 passed**, including three opt-in browser tests. JavaScript: **48 passed**. [Pre-merge CI](https://github.com/ketangan/student_enrollment_portal/actions/runs/38690203699): **3,942 passed, 3 browser tests skipped**, all coverage gates passed; measured modules combined coverage **99.39%** (not a whole-application coverage claim).
- [Main-branch CI rerun](https://github.com/ketangan/student_enrollment_portal/actions/runs/38691882404) also passed all test and coverage gates after deployment. The temporary release branch was deleted; the unrelated nested worktree remains untouched.
- Live manual checks: SBMC dashboard, submissions, public registration, Settings/Billing and return navigation; active Custom/Founder plan preserved, no negotiated agreement saved. Superadmin directory still shows SBMC as production and Plushie as demo. New pricing controls render with Live mode and disabled Save until validation. No prices, credentials, features or subscriptions were changed during hosted verification.
- Plushie demo dashboard, Settings > Billing, integration comparison and actual customer booking page opened successfully. Demo subscription checkout remains paused; no live charge or hosted test subscription was created.
- Existing warnings: duplicate Jazzmin template library; GitHub action runtime deprecation; checkout cleanup encounters the pre-existing tracked nested worktree without a submodule URL. CI succeeds. The unrelated worktree was not changed or included in this release.
- The historical sections below describe local development checks at the time they were performed; this release status supersedes their earlier undeployed notes. Live Price IDs and agreed location counts still require explicit school-by-school setup. Notify the customer before applying a renewal change; this release does not add an automatic price-change email.

## Ready for Local Acceptance Testing

Use **http://localhost:8015/login/**, not the normal port 8002. The billing harness has a fresh local PostgreSQL database (`test_pontora_billing_sandbox`), separate session cookies, test-only Stripe credentials loaded from the existing repository `.env`, and locally captured email. It contains no copied production or SBMC data. Render and `.env` were not modified.

| Username (password is `admin`) | Expected subscription | Purpose |
| --- | --- | --- |
| `billing-base-test` | $49/month | Base-only checkout |
| `billing-location-test` | $89/month: $49 plus two $20 locations | Location line items and total |
| `billing-upgrade-test` | $49/month | Original fixture name retained; there is no customer-facing future-upgrade offer |
| `admin` | Superadmin, not a customer | Ops > Schools > Subscription pricing |

The three customer accounts were left without subscriptions for Ketan's acceptance tests. Their trials are deliberately expired so checkout is available immediately. An additional `billing-automated-qa` account was used for the browser payment walkthrough; a separate clock fixture was canceled after renewal verification.

1. Log in as `billing-location-test` / `admin`. The expired-trial redirect should take you to Billing.
2. Confirm $49 base + 2 x $20 additional locations = $89/month. Continue to Stripe and confirm its **Sandbox** label and matching total.
3. Use `4000 0000 0000 0002`, a future expiry and any three-digit CVC. Use fictional billing details and leave Link saving off. Stripe should reject the payment without activating Pontora.
4. Replace the card with `4242 4242 4242 4242` and complete the sandbox payment. On return, Pontora should show Custom / active after the signed webhook arrives; refresh once if confirmation is still in transit.
5. Open Manage Billing. Verify the invoice, card-management controls, and $89 subscription. There must be no option to switch to unrelated catalog prices.
6. Sign out and repeat with `billing-base-test`, expecting $49. Trials and quantities may have been changed during acceptance testing; the saved agreement is authoritative. Future prices are not advertised or activated automatically.
7. Superadmin `admin` / `admin` can inspect each school's validated prices at `/ops/schools/<slug>/pricing/`. Customer owners cannot edit these prices.

Do not use real card details. Email notifications are captured under `/tmp/pontora-billing-sandbox/emails`, not delivered to Ketan or any customer. Test invoices intentionally do not impose live payment grace/lockout. Live-mode seven-day access enforcement was exercised by isolated automated fixtures instead.

### Actual Sandbox Verification, October 14

- All three supplied prices verified using the existing key: active USD monthly test prices on the expected products.
- Actual browser flow: school-owner login, $89 agreement, real Stripe Checkout, declined card, successful card, signed checkout/invoice webhook, return to active subscription, Manage Billing portal and invoice history.
- Stripe test-clock fixture: real $49 `subscription_cycle` renewal paid, subsequent renewal failed, local state became `past_due`, replacement test card and invoice retry restored `active`. Success/failure notifications captured by the local email backend. The clock fixture uses an API-created subscription with local agreement linkage; it is separate from the actual browser Checkout test, not a claim that Checkout supports adding a test clock after purchase.
- Superadmin price-validation page confirmed $49 current and $79 future rate directly with Stripe. Mobile billing page checked at 390px; desktop at the normal browser viewport.
- Existing billing, negotiated pricing, notifications, trial activation and trial expiry regression tests: **373 passed** in this verification pass.
- No production application code changed during this sandbox setup. No live Stripe objects, existing subscriptions, Render configuration, hosted database, or SBMC records were modified. The local status viewer remains on port 8003.
- Remaining gates: user's acceptance review, approved live Price IDs and quantities, live email-provider delivery verification, deployment/migration approval. This does not certify every card/3DS/tax or live-account scenario.

Local-only harness files are under `/tmp/pontora-billing-sandbox/`; they are not production modules. `start.py` uses the official Stripe CLI to forward signed sandbox events to localhost without creating a hosted webhook endpoint. Keep the server and listener running during tests. After a restart, run the repository virtualenv Python against that script only when port 8015 is free; do not start duplicate listeners. Temporary files may need recreation after machine cleanup/reboot.

## What Is Implemented

| Capability | Status |
| --- | --- |
| Superadmin-only pricing page under Ops > Schools > school > Subscription pricing | Implemented locally |
| Validate a recurring Stripe base price against the configured Stripe account | Implemented locally |
| Additional-location price and explicit billable quantity | Implemented locally |
| Change an existing negotiated subscription's base price | Explicit superadmin action at next renewal; same billing frequency, no proration |
| USD monthly or annual flat-rate subscriptions | Implemented locally |
| School sees its agreed price instead of the standard plan picker | Implemented locally |
| Retry-safe Checkout with server-controlled amounts and quantities | Implemented locally |
| Existing invoice emails, seven-day grace and access recovery | Reused; integration tested |
| Existing subscriptions and school-owned Stripe payment collection | Unchanged by saving an agreement |
| Real Stripe sandbox checkout and renewal success/failure/recovery | Verified October 14; live credentials not used |

Only schools with a saved agreement use this path. Other schools retain their environment-variable prices and existing checkout behavior. Presence of an agreement in either Stripe mode prevents silently falling back to a standard plan when the current mode has no valid agreement.

Pricing does not grant features. Existing school feature settings remain authoritative. On first paid activation from Trial, effective flags are preserved and the plan becomes Custom. Existing paid plans retain their plan and overrides. Scheduling enablement is not modified.

Saving a new price does not edit a running Stripe subscription. A paying customer continues to see the accepted offer until the superadmin explicitly applies the saved change. A new offer on a legacy subscription is explicitly labeled as a future agreement. No automatic price increase, proration or location-count synchronization is introduced.

## Stripe Setup for Ketan

These products are for **schools paying Pontora**. They are not workshop tickets, deposits, or school application fees. Do not change SBMC's products, customer, subscription, payment keys, or webhooks.

### Supplied Sandbox Prices

Provided by Ketan on October 14, 2026 for **Product sandbox**. These are test-only IDs, not live prices. Amounts, currency, interval, active state and test mode were verified directly with Stripe using the existing local test key.

| Price | Reported monthly amount | Test Price ID |
| --- | --- | --- |
| Base subscription | $49 | `price_1UQZvQRyOgZNJrOZBSUyuwsO` |
| Alternative base subscription | $79 | `price_1UQZwuRyOgZNJrOZp9HsIPTW` |
| Additional location | $20 per additional location | `price_1UQZyoRyOgZNJrOZTcIEXSsq` |

The $79 price is not an additional charge or an agreed Plushie upgrade. These IDs are configured only for synthetic local acceptance-test schools. No existing customer subscription was changed; do not change Render's live Stripe mode.

### 1. Create the Test Products First

1. Sign in to the Pontora Stripe account. Use the account picker to enter the intended **sandbox** (or the existing test environment used by Pontora). Verify that the dashboard clearly identifies it as a testing environment. Different sandboxes have different keys and objects: the prices must be created in the same sandbox as the test API key used by the local app.
2. Open **More > Product catalog**, then **+ Add product**.
3. Name the product **Pontora Subscription**. A suitable description is "Monthly Pontora software subscription." Do not promise a specific feature bundle in the Stripe description.
4. Add a **flat-rate, recurring, USD price of $49.00**, billed **monthly**. Do not select one-time, usage-based, tiered, customer-entered, or package pricing. Save it.
5. Open that product and add another recurring monthly price of **$79.00** as an alternative base rate. Assign it only when agreed with the customer. You do not need a separate product for every amount. If you later agree to $39, add a $39 monthly price under the same product.
6. Create a second product called **Pontora Additional Location**. Add a **flat-rate, recurring, USD price of $20.00**, billed **monthly**. This price is per extra location; Pontora supplies the agreed quantity.
7. Open each price's details and copy its **Price ID**, starting with `price_`. Do not copy the Product ID (`prod_`) or a payment-link URL.

| Send me this test Price ID | Amount | Use |
| --- | --- | --- |
| Base subscription | $49 / month | Initial agreed subscription |
| Additional location | $20 / month | One unit for each additional billable location |
| Alternative base rate | $79 / month | Available when agreed; not automatically charged |

Also tell me which sandbox/test environment you used. Price IDs are not secret; API secret keys are. Do not paste secret keys into chat. No new Render price environment variables are needed for these agreements.

Stripe's current Dashboard instructions and support for multiple prices per product are documented in [Manage products and prices](https://docs.stripe.com/products-prices/manage-prices). Prices cannot have their amounts edited in place; create a new price when the agreed amount changes.

### 2. Connect and Verify the Test Flow Together

After the test IDs are available, we will configure the **isolated local platform-billing test setup**, with the matching test secret, publishable key and webhook signing secret. The existing localhost integration-preview harness disables platform billing credentials by default. Creating prices alone does not enable it.

**Do not change Render's `STRIPE_MODE=live` to test.** The shared hosted service still serves SBMC. A demo hostname does not independently switch the platform subscription account. Plushie demo schools also intentionally cannot start real Pontora subscriptions. The new subscription flow is tested using a synthetic, non-demo school in the isolated test database, not by converting the hosted Plushie prospect to production.

1. As a superadmin, open the synthetic school's **Subscription pricing** page.
2. Enter an agreement name, the test base Price ID, and the number of locations included in the base rate.
3. Enter the additional-location Price ID and **only the additional locations to bill**. Example: one included location plus two additional locations gives three total locations and $49 + (2 x $20) = **$89/month**.
4. Enter only the currently agreed base price. There is no future-upgrade field. Later price changes use the same base Price ID field and a separate explicit next-renewal action, described below.
5. Click **Validate prices**. Confirm the amounts and recurring total. Click **Save agreement**. If fields change, validate again before saving.
6. Use the school's owner account to open **Settings > Billing > View billing**. Verify it shows only the agreed subscription. The existing deferred-trial rules still apply: checkout is available at the end of the trial, not a new second 30-day trial in Stripe.
7. Continue to Stripe Checkout. Confirm its base amount, additional-location quantity and recurring total match Pontora.
8. For a successful test payment, use **4242 4242 4242 4242**, a future expiration date and any three-digit CVC. For a declined payment, use **4000 0000 0000 0002**. These are sandbox-only cards; never use them in live checkout. See [Stripe testing cards](https://docs.stripe.com/testing).
9. We will verify webhook delivery, repeat clicks, failed payments, renewal alerts, card changes and recovery before release. Returning to a success URL alone does not prove payment or activate access. Existing test invoices intentionally do not lock real schools out; the seven-day lockout is additionally verified with simulated live events against isolated data.

Renewal and payment-recovery testing must cover a subsequent invoice, not merely the first successful card charge. Stripe provides [Billing test tools and test clocks](https://docs.stripe.com/billing/testing) for those checks.

### 3. Create the Live Products Only After Test Approval

1. Return to the **live Pontora account** in the Stripe account picker. Double-check the business/account name. Do not modify any existing products or subscriptions.
2. Create **Pontora Subscription** with the same agreed recurring USD monthly prices ($49 and, if needed, $79). Create **Pontora Additional Location** with the $20 recurring monthly price. Stripe may offer **Copy to live mode** on a test product; if used, open the resulting live product and verify every price yourself.
3. Copy the **live** `price_...` IDs. They are different from the test IDs. Keep test and live IDs separately labeled.
4. Send me the live IDs and the final included/additional-location counts for each customer. Do not activate a customer subscription manually in Stripe; the school must accept the agreed checkout to avoid duplicate subscriptions.
5. After code review, sandbox verification and deployment approval, apply the new migration before the updated workers serve billing. Configure each intended customer's live agreement through the superadmin page. Leave SBMC unconfigured here so its existing path stays unchanged.
6. We will inspect the existing platform webhook at `/stripe/webhook/`, confirm the required Checkout, subscription and invoice event deliveries, and perform the release checks. No replacement of SBMC's application-fee webhook is needed.
7. Do not run test charges in live mode. The first live checkout should be the customer's genuine authorized purchase at the agreed time.

### Important Limits

- This release supports USD fixed monthly or annual rates. Base, location and future upgrade must have the same interval. Metered prices, tiered rates, currency conversion, coupons, tax automation, setup fees and scheduled annual increases are not introduced.
- There is no automatic contractual increase or customer-facing future-upgrade offer. A superadmin can apply a new base Price ID at the next renewal using the review action below. Notify the school in advance; this action does not send a price-change email or establish the school's consent. Do not change negotiated subscription items directly in Stripe: reconciliation matches them to the accepted agreement.
- Existing customers' regular billing portal remains unchanged unless they are explicitly opted into school-specific pricing. Negotiated subscriptions use a separate portal configuration that allows card updates, invoice history and end-of-period cancellation, but prevents switching to arbitrary catalog prices or quantities.
- Additional locations are an explicit billing quantity, not a count silently taken from the scheduling database. Location-creation permissions are a separate deferred task.

## Engineering and Release Record

### Impact

- New tables: `SchoolBillingOffer` (versioned validated prices) and `SchoolBillingCheckout` (durable idempotent attempt). Migration: `core.0065_schoolbillingoffer_schoolbillingcheckout_and_more`.
- Existing school records are not migrated or enabled. No new dependencies, background jobs, hosting services or production configuration changes.
- Common billing changes are limited to negotiated-school routing, a dedicated portal configuration, and subscription reconciliation. Existing invoice/notification/grace services are reused.
- No changes to Square, parent application-fee collection, the booking payment ledger, SBMC configuration, or its environment-variable prices.
- Offers are checked against their school, Stripe account, mode, currency, interval and line-item quantities. Client-posted amounts/quantities and standard-price fallback are not trusted.
- Simultaneous checkout requests share a stored request and Stripe idempotency key. Provider timeouts retain that identity. An unknown expired attempt requires operator reconciliation; it is not silently retried with a fresh charging identity.
- Open checkouts block offer replacement. A provider-confirmed expired checkout can be superseded. A completed checkout awaiting a webhook cannot create a second subscription.
- Out-of-order custom subscription updates read current Stripe state. Historical offers remain available to reconcile accepted checkouts after a new offer is published.
- The new settings page is superadmin-only with CSRF and signed validation confirmation. Billing initiation remains owner-only, including scheduling-only owners.

### Verification

- Baseline before edits: 320 existing billing, notifications, trial and scheduling-access tests passed.
- Full regression: 3,867 tests passed. Final combined payment/isolation/browser run: 388 passed, including simultaneous requests and renewal failure/recovery. All 48 JavaScript regressions passed.
- New service/form/view coverage: **99.38% combined statement/branch coverage**. This is not a claim of 99% coverage across the entire application.
- Browser verification: 1440px and 1280px desktop, 390px and 320px mobile; login, invalid Price ID, verified totals, save/reload, customer billing, external checkout redirect, legacy billing and school-owner denial of operator access. Screenshots reviewed; action sizes corrected.
- Stripe calls were mocked and external network access blocked. No real charge, hosted checkout, Stripe test clock or live webhook was exercised in this pass.
- Migration drift check and whitespace check passed. Existing duplicate Jazzmin-template-library warning remains unrelated.
- Critical self-review corrected: existing-subscription webhook compatibility after future-offer setup, current-vs-future price labeling, stale form confirmation, timeout identity retention, delayed active-event reactivation, expired-checkout replacement and mobile action sizes.
- Local preview database migration applied and localhost:8002 restarted with its prior test-only booking settings. Its new pricing page, SBMC dashboard/settings/billing and public registration form were also opened manually. No hosted database, Render settings, Stripe products, credentials or customer subscriptions were changed. Nothing committed or pushed.

### Manual Review Checklist

1. Open `/ops/schools/<school-slug>/pricing/` as a superadmin. Verify the displayed Stripe mode. A school owner must not access this page.
2. Submit a Product ID, inactive price, wrong-mode price, mismatched interval, or a location count without a location price. Verify rejection without saving or substituting a standard price.
3. Validate $49 base, $20 location price and two extra locations. Verify $89 total. Change a value before saving and verify revalidation is required.
4. Save, reload and inspect agreement history. Confirm feature flags, trial dates, existing subscriptions and school merchant credentials did not change.
5. Open the school's billing screen as an owner. Verify the agreed rate and absence of unrelated standard packages. A viewer must not start checkout.
6. Repeat Checkout clicks; verify one Stripe session/subscription identity. Try another school's offer ID and confirm denial.
7. For an existing paying school, save a future offer and verify that the current accepted price remains displayed. For a legacy subscription, verify the future offer is labeled accordingly.
8. Confirm the untouched school's dashboard, submissions, Settings/Billing and public enrollment form still open. Compare existing subscription and school merchant IDs without changing them.
9. Complete the real sandbox acceptance checks above before publishing live rates. Do not treat locally simulated success as Stripe certification.

Repeat automated local browser checks with `PONTORA_BROWSER_QA=1` and `core/tests/test_school_pricing_browser.py`; the test creates its own disposable data and never uses the running development server.

### Basic Checkout Recovery (Local, October 14)

- In the existing operator school page, Subscription pricing shows pending checkout references with **Check status** and **Expire unpaid checkout**. Expiration requires confirmation and only attempts to expire an open, unpaid Stripe session.
- A completed checkout uses the existing subscription reconciliation handler. Payment finishing during expiration is reconciled instead of cleared. Unknown results retain the pending record; no replacement payment is created.
- Customers retrying Billing resume the open Stripe checkout. A completed checkout is reconciled and returns to Billing without creating a second subscription.
- Account, school, billing mode, superadmin permission and CSRF checks apply. No new migration, background job, deployment or legacy-payment changes are required for this recovery addition.
- An attempt with no Stripe session reference still requires support investigation. It is deliberately not cleared blindly after a timeout.
- Manual verification: checked the existing $89 sandbox checkout in the browser against real Stripe; confirmed it had expired and removed its stale local pending state. The agreement stayed unchanged. Automated browser checks cover status/expiration controls on desktop and mobile; mocked payment-race tests cover completed payments and provider failures.

Manual retest: start an unpaid sandbox checkout, return to the operator school page, check its status, then expire it. Verify the old link no longer accepts payment and Billing can start a new checkout. Never use a live customer's payment for this test.

### Additional Locations on a Paid Subscription (Local, October 14)

1. Superadmin increases **Additional locations to bill**, then validates and saves the offer.
2. Select **Apply billing changes at next renewal** in the same school settings section. Saving the offer by itself remains non-charging.
3. Pontora updates the existing Stripe location item (or adds the first location item) with `proration_behavior=none`. It does not change the billing anchor, base price, trial or payment method, and does not create another subscription or invoice.
4. The school sees the confirmed total and renewal date in the existing Billing screen. The extra locations can be used before that date without a partial-period charge. Physical location setup remains separate from this billing quantity.

Scope: active, automatically charged negotiated subscriptions only. Added locations retain the existing location rate and included-location count. The same action now supports base-price changes, described below. Legacy subscriptions such as SBMC are not eligible. Subscription schedules, cancellation, paused collection, mismatched prices/quantities and unresolved payments require review rather than an automatic rewrite. Location removals remain outside this action.

Implementation: additive `core.0066_subscription_location_changes` stores the original/new offers, renewal date and durable Stripe request identity. This lets a timeout retry the same request; webhooks validate the new quantities against that saved change. There is no background scheduler. Pending updates block replacement of their offer. If renewal passes during an unconfirmed update, it requires review rather than silently billing a different cycle.

Verification: 299 focused backend/browser tests passed; 97.61% combined statement/branch coverage for the pricing service/view. Real Stripe Product sandbox tests verified $49 to $69 to $89, no immediate invoice or proration, the same renewal date, and an actual test-clock renewal collecting $89. The synthetic clock subscription was then canceled. Manual operator verification on `billing-automated-qa` applied $89 to $109 from November 14, with the customer screen showing no charge today. This is sandbox data only.

Manual review: [Operator settings](http://localhost:8015/ops/schools/billing-automated-qa/#subscription-pricing) and [school Billing](http://localhost:8015/schools/billing-automated-qa/admin/billing/). Check another increase using the operator action; verify Stripe's upcoming invoice and unchanged renewal date, then check the school page. Owners must not be able to apply these changes. Production remains undeployed; migration 0066 must accompany the eventual release along with the pending 0065 migration.

### Base-Price Changes at Renewal (Local, October 14)

1. In the existing operator school page, replace **Base subscription Price ID** with the agreed new price. Keep the same billing frequency. Additional locations can be increased in the same change.
2. **Validate prices**, review the total, then **Save agreement**. This alone does not update Stripe or change the customer's accepted rate.
3. Review the current subscription rate, new rate and renewal date. After notifying the school, select **Apply billing changes at next renewal**. A signed review expires after ten minutes; stale accepted offers or changed renewal dates require a fresh review.
4. Stripe replaces the existing base subscription item by its ID and retains quantity one. No second base item, immediate invoice, proration, subscription, billing-anchor change or customer checkout is created. Monthly-to-monthly and annual-to-annual changes are supported; frequency changes are rejected.
5. The existing customer Billing page displays the updated rate and effective renewal date. Payment methods, feature flags and trial settings remain unchanged. Existing invoice notifications and failure handling continue to use the same paths.

Implementation reuses the location-change record and its original metadata/idempotency format so existing pending updates remain retryable. There is no new migration for this extension. Retry safeguards reject changed collection settings, renewal dates, pending Stripe updates, billing thresholds and unrelated price/quantity changes. Confirmed updates reconcile without another write; uncertain updates retain their original identity.

Actual Product sandbox verification: a synthetic $69 subscription changed to $119 (base and location increase), then $89 (base decrease), then $119 (base increase). Each update kept the original base item and renewal date, created no immediate invoice and produced no prorations. Advancing the Stripe test clock produced a paid $119 renewal invoice. The synthetic clock subscription was canceled afterward. Browser verification changed the existing `billing-automated-qa` base from $49 to $79, retaining its four added locations: $129 to $159 from November 14. No live subscription was changed.

Automated verification includes base-only and combined changes, annual pricing, frequency rejection, signed-review tampering/expiry, stale reviews, permissions/CSRF, timeout retries before/after Stripe applies the change, legacy billing and unchanged parent-payment paths. Desktop and 390px/320px mobile views were checked. See the tracker for the final run totals. Local only; no commit, push or Render changes.
