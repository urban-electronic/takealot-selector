# Design and validation used in this project

These are project-specific working rules, synthesized from the sources below.
They are not an installation of Claude skills or OpenSkills. Do not add a loader,
new UI framework or runtime dependency to apply them.

## Define the operator's task

Before a change, specify the user action, correct outcome, current evidence and
constraints in a few sentences. For example: after pausing a SKU, a delayed list
response must not restore its old running badge. Success is correct state after
that action, not a prettier screenshot or an HTTP 200.

Read only the code and references relevant to that task. Reuse existing issue IDs,
PRs and evidence. Fix the highest-priority executable problem before cosmetic work.

## Design for clear actions and truthful state

- Reuse existing colors, spacing, type, rounded controls and focus styles. Avoid
  per-page token forks, new decorative cards, font downloads and forced animation.
- Give each action one consistent name. Busy actions expose `aria-busy` and prevent
  repeated submission. Preserve usable keyboard focus and accessible labels.
- Keep status and next action visible; put diagnostics in expandable details.
  Long names must fit their column at the same window width, with full text
  available on demand. Do not solve overlap by shrinking text beyond readability.
- Treat loading, empty data, failure, success and stale data as distinct states.
  A refresh failure preserves loaded data and an actionable error; retry must work.
- Coalesce overlapping read requests. After a successful mutation, fetch a fresh
  snapshot; do not reuse a request started before the mutation. Ignore responses
  and errors superseded by a newer request or an unmounted page.
- Chinese and English names are separate fields. Pure English incorrectly placed
  in the Chinese field is not a translation. A model, brand or variant label such
  as S/M/64GB needs contextual review, not blanket replacement. Keep SKU identity.
- Queueing, a heartbeat and an attempt timestamp do not prove fresh collection.
  Show last valid quote separately. Preserve source, quote time and variant
  evidence; official own price is not a Buybox/competitor quote.
- Inventory and overseas transit must agree after an authorized correction. Read
  existing records first, guard against duplicates/concurrent changes and read
  back the result. Do not create purchases or arrivals to make totals look right.

## Validate only what the change affects

For each defect record version/environment, preconditions, action/input, expected
and actual behavior, evidence and conclusion: resolved, still failing or unverified.
Reproduce before the fix and repeat the same trigger after it. For async code,
control completion order and failures rather than relying on timing sleeps.

Use existing focused commands as applicable:

| Change | Relevant check |
| --- | --- |
| Buybox display / refresh lifecycle | `npm run test:buybox` |
| Product pagination | `npm run test:products` |
| Inventory interactions | `npm run test:inventory` |
| Shared visual styles | `npm run check:visual` |
| Browser audit harness | `npm run test:audit-safety` |
| Frontend compilation | `npm run build` |

These commands have different scopes; none proves production business correctness.
Browser checks must use an isolated fixture server, intercept writes and external
requests, wait for actual ready UI, and fail if pages cannot be measured. For
geometry/focus checks capture the rendered page at the affected width and inspect
console errors. JSDOM cannot certify layout, contrast or real browser focus.

Separate four results: code committed, relevant tests passed, intended version
deployed, real business acceptance. For session-only acceptance, hand off the
specific steps once to WorkBuddy and review its redacted evidence. No evidence
means unverified. No new input means no repeated report, full suite or installation.

## Sources and scope

Reviewed on 2026-10-17; the repeated anthropics link is one source. Versions below
pin the material reviewed, rather than following future instructions automatically.

| Source | Applied method | License / limits |
| --- | --- | --- |
| [Anthropic frontend-design](https://github.com/anthropics/skills/blob/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/frontend-design/SKILL.md) | Task-led hierarchy, restrained copy, deliberate existing design tokens | Selected skill Apache-2.0; do not import unrelated document skill implementations |
| [Anthropic webapp-testing](https://github.com/anthropics/skills/blob/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/webapp-testing/SKILL.md) | Inspect rendered state, meaningful selectors, bounded ready checks and browser evidence | Selected skill Apache-2.0; examples do not authorize production writes |
| [James Rochabrun frontend-designer](https://github.com/jamesrochabrun/skills/blob/2482c176372299c92af01f8414a67172f324e8db/skills/frontend-designer/SKILL.md) and [qa-test-planner](https://github.com/jamesrochabrun/skills/blob/2482c176372299c92af01f8414a67172f324e8db/skills/qa-test-planner/SKILL.md) | Accessible pending/error controls; preconditions, expected/actual results and targeted regression | Repository MIT; individual content must be checked before copying |
| [OpenSkills](https://github.com/numman-ali/openskills/blob/57d933a4f0d5c8659bd8b285f50fb9554360f0b3/README.md) | Small entry index, load relevant detail on demand, versioned local guidance | Apache-2.0; a skill loader, not a product design or quality guarantee |

This document and the implementation are original project adaptations, not copied
skill templates/scripts. We do not adopt marketing hero layouts, mandatory new
fonts, blanket UI restyling, unrelated finance/Apple workflows or automatic CLI
installation. Review licenses again before importing substantial upstream code.
