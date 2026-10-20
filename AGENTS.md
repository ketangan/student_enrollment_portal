# Persistent Engineering Instructions

These are the project owner's standing requirements for all work in this repository.

## Collaboration

- Be direct and evidence-based. Do not agree merely to be agreeable or claim
  verification that was not performed. Challenge weak assumptions and explain risks.
- Critically review your own implementation before presenting it as complete.

## School Configuration and Shared Behavior

- School, studio, location, workshop and client-specific values and policies belong
  in a scoped YAML configuration section or database settings/records, never in
  hardcoded application logic or reusable UI components.
- Do not branch shared behavior on a customer's name, slug or ID. Use validated,
  explicit configuration and existing capabilities/permissions instead.
- Prefer database-backed settings for values authorized users must edit. Use YAML
  for operator-managed policies where no settings UI is needed. Follow existing
  configuration conventions and keep one authoritative source per setting.
- Do not make production behavior depend on a demo-only configuration loader.
  Preserve agreed reservation prices/policies when defaults change later.

## Extend Before Adding

- Before implementing any change, inspect existing models, services, forms,
  templates, configuration, permissions and tests for that feature and its callers.
- Extend or reuse the established behavior. Do not create competing implementations,
  duplicated business rules or a separate workflow when the existing one can serve.
- Keep code generic and expandable to other schools, studios and workshops without
  speculative frameworks or unrelated refactoring.

## Quality and Stop Conditions

- Code must remain clean, readable, maintainable, reliable and performant. Check
  query counts and avoid per-row queries or unnecessary remote calls in shared paths.
- Preserve school/environment isolation and existing enrollment, SBMC, Stripe and
  subscription behavior unless the user explicitly requests a change to them.
- Scale tests to risk: exercise affected flows, authorization, invalid input,
  regression behavior and migrations. For money, also verify retries, concurrent
  writes, immutable payment evidence and accurate totals. Inspect changed UI manually.
- Reuse the existing local server and database; do not create additional ports,
  databases or customer-specific implementations as an unrequested shortcut.
- If a proposed change breaks these rules, stop and re-evaluate the design. If the
  conflict cannot be resolved cleanly, explain the tradeoff and ask the user before
  proceeding. Do not conceal uncertainty or broaden scope silently.
