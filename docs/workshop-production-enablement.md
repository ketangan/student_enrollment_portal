# Customer Workshop Production Enablement

November 3, 2026. Opt-in code; deploying it does not enable any existing school.

## Configuration

Use a separate, active, non-demo school with an enabled scheduling profile and
workshops/reservations capabilities. Never convert the hosted demo school into a
live school. In its ordinary `configs/schools/<school-slug>.yaml`, configure:

```yaml
scheduling:
  customer_experience:
    enabled: true
    # Optional, school-owned branding/copy:
    # logo: /static/path/to/logo.png
    # photo: /static/path/to/workshop.jpg
    # start_title: Book a workshop
    # start_description: Choose a studio, workshop and time.
    # choose_title: Your participants
    # participant_heading: Your participants
    # confirmation_title: Your workshop is confirmed.
```

Existing customer copy, age restrictions, selection deadlines and booking policies
continue to use their existing school configuration sections. No demo YAML or demo
owner account is needed. Prices, deposits, tax rates, locations, published sessions,
choices, photographs, swatches and stock remain database-owned and editable through
their existing setup screens. Inventory Setup's first two stock dimensions remain
design and color; age follows the existing participant-age policy. Non-inventory
workshops retain their ordinary choice fields.

The customer entry remains `/bookings/<school-slug>/experience/`. An opted-in
non-demo school's ordinary public schedule redirects there. Existing demo routes,
host restrictions, simulations, configuration and saved links remain supported.
Legacy `experience_demo` booking snapshots are still recognized; new real bookings
use `customer_experience`. The shared services remain in their current package to
avoid unrelated import/route churn; that package name does not enable simulations.

## Payment and Activation Checks

1. Authorize the real Square seller account(s) in existing Integrations settings and
   map each studio to its correct Square location. Do not copy demo credentials.
2. Verify the live application/webhook configuration and activation switch, signed
   webhook delivery and scheduled recovery. Production workshop intake requires a
   ready live account for that school/location. Sandbox/offline fallback is rejected.
   Live Stripe booking activation is not added by this change; enrollment and
   subscription Stripe behavior is unchanged.
3. Confirm published sessions, prices/deposits/tax, capacity, room exclusivity,
   choices, material recipes/on-hand stock, policies and waiver process.
4. Enable native reservation intake, then run the read-only preflight:

   `python manage.py check_workshop_launch <school-slug>`

   It checks opt-in/capabilities, upcoming priced sessions, available choices,
   deposit validity and local payment-account readiness. It does not call Square,
   charge cards, modify data, certify inventory counts, or prove webhook delivery.
5. Complete approved live payment/refund, email-link/inbox, staff prep, different
   browser/studio timezone and physical Android Chrome acceptance. Resolve existing
   Wix booking/balance cutover before changing Wix booking links.

Missing payment configuration blocks new selection/intake before payment; affected
session availability is disabled. The final transaction rechecks availability and
payment configuration, holds the existing school lock, reserves stock and saves
the shared payment order. Duplicate submissions return the same reservation.
Provider return URLs are never payment evidence. Customer-created bookings are
audited without impersonating a staff owner.

## Pause and Rollback

To pause new bookings, set `native_reservations_enabled=False` using the existing
operator controls. Keep the customer-experience opt-in, school and profile active:
existing confirmations, signed links and provider recovery must remain available.
Do not disable the whole school or delete payment mappings to pause intake.

No schema migration or existing-school activation is included. If code rollback is
needed after real bookings exist, use a compatible fix-forward/release preserving
`customer_experience` snapshot support, rather than blindly rolling back to a
demo-only version that cannot display those bookings. Do not reverse payment or
inventory records. Keep webhook/recovery processing available for in-flight money.

## Verification Boundaries

Tests create a non-demo school with local-only mocked Square responses, assert no
demo loader or demo actor is called, and exercise booking, duplicate/concurrent
requests, inventory/capacity rollback, verified deposits, saved review/editing,
calendar, opt-in/tenant/environment guards, paused intake and read-only preflight.
Those tests prove application behavior, not real merchant/device acceptance.

Jasmine's hosted demo, real schools, accounts, customer records and credentials are
not changed by this implementation. Release requires the final full regression,
coverage gates and GitHub checks. Physical Android acceptance remains separate
from desktop responsive checks at 768x1024 and 1024x768.
