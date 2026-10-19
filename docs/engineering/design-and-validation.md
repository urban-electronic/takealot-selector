# Design and validation used in this project

These are project-specific working rules, synthesized from the sources below.
They are not an installation of Claude skills or OpenSkills. Do not add a loader,
new UI framework or runtime dependency to apply them.

The user subsequently requested Vercel's `web-design-guidelines`, installed locally
with `npx skills add https://github.com/vercel-labs/agent-skills --skill web-design-guidelines --agent codex --yes`.
The unchanged skill lives in `.agents/skills/web-design-guidelines/SKILL.md` and its
installer hash in `skills-lock.json`. Read it for UI reviews and fetch its live
rules before reviewing; user requirements and this application's context prevail.
For this Chinese operator UI, do not mechanically apply English title case,
decorative animation, new loaders or dependencies. Native buttons already provide
Enter/Space behavior; do not duplicate those key handlers.

This review used [agent-skills 063bee9](https://github.com/vercel-labs/agent-skills/tree/063bee94c3f4df8453406c830b0a7df0f2860278/skills/web-design-guidelines)
and [interface rules 434b7f9](https://github.com/vercel-labs/web-interface-guidelines/blob/434b7f91364665f2f733b310ec54809bf8f37937/command.md),
retrieved on 2026-10-17. The skill repository declares MIT in its README; the copied
skill retains its original author metadata and text. No upstream scripts copied.

## Define the operator's task

Before a change, specify the user action, correct outcome, current evidence and
constraints in a few sentences. For example: after pausing a SKU, a delayed list
response must not restore its old running badge. Success is correct state after
that action, not a prettier screenshot or an HTTP 200.

Read only the code and references relevant to that task. Reuse existing issue IDs,
PRs and evidence. Fix the highest-priority executable problem before cosmetic work.

## Skill discovery and updates

The user's [vercel-labs/skills](https://github.com/vercel-labs/skills/tree/13e4063a1cf913f5606d57d42ab83a86f5001e04)
reference is the CLI/skill manager, distinct from `vercel-labs/agent-skills`.
Its `find-skills` instruction is installed unchanged at project scope for Codex.
Its README, discovery logic and local-lock implementation informed this workflow:

1. Identify a concrete task and capability gap. Check the installed skills first.
   If the selected source already meets the request, do not perform a redundant
   popularity search. Use the leaderboard/search only when discovery is needed.
2. Inspect the matching SKILL.md, scripts/references, source and license before
   adoption. Install counts, stars and publisher reputation are candidate signals,
   not evidence that a skill is correct or safe for this task. Verify any reported
   counts live rather than repeating example numbers from the skill text.
3. Load only the selected instructions. Use `skills use` or direct reading for a
   one-off task; install only useful repeatable skills, to this project and the
   intended agent. Do not install all skills/agents or change global configuration.
4. Commit the original skill and `skills-lock.json` together. Retain source,
   skillPath and content hash. Verify existing entries remain intact; the hash
   tracks local file contents, not trustworthiness or model capability.
5. Review updates on an explicit request or a concrete compatibility/fix need.
   Compare the selected skill's differences before acceptance, preserve local
   changes and validate affected behavior. No unconditional periodic reinstall,
   automatic bulk update or arbitrary external agent launch.
6. Apply the useful method to the actual task and verify its outcome. Reading or
   installing a skill is tooling preparation, not a product fix or business pass.

Use the already-authorized tools/sessions; acquiring skills does not grant new
permissions. This workflow never requests or transfers credentials.

## Design for clear actions and truthful state

- Reuse existing colors, spacing, type, rounded controls and focus styles. Avoid
  per-page token forks, new decorative cards, font downloads and forced animation.
- Give each action one consistent name. Busy actions expose `aria-busy` and prevent
  repeated submission. Preserve usable keyboard focus and accessible labels.
- Use real buttons for inline editing and real links for navigation, preserving
  modified clicks/new tabs. Modal editors have a programmatic name, linked labels,
  initial focus, Tab containment, Escape cancellation and return focus. During a
  save, lock its fields/actions and block duplicate requests synchronously.
- Name transitioned CSS properties explicitly; honor reduced-motion preferences.
  Align comparable quantities with tabular numerals without adding visual clutter.
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
| Contrast / control-size in a real browser | `scripts/test-contrast-audit.py` |
| Layout and overflow in a real browser | `scripts/test-responsive-layout.py` |
| Frontend compilation | `npm run build` |

These commands have different scopes; none proves production business correctness.
Browser checks must use an isolated fixture server, intercept writes and external
requests, wait for actual ready UI, and fail if pages cannot be measured. An
undeclared `/api` path must fail the run rather than 404 into an empty response:
a page that silently degrades to defaults still renders, so a green layout result
would certify a screen that was never actually measured. For
geometry/focus checks capture the rendered page at the affected width and inspect
console errors. JSDOM cannot certify layout, contrast or real browser focus.

A harness that is never executed is worth nothing: `scripts/test-ui-audit-safety.py`
drives the contrast audit with a fake browser, which proves the audit fails loudly
but measures stub numbers. `scripts/test-contrast-audit.py` is the real run; keep
it in the gate. Collapsed UI must be opened before measuring it — `details`
elements such as the per-row decision card render nothing while closed, so a
fixture without the data behind them (here `rule.plan`) measures an empty box and
still reports a clean result. This applies to **both** browser suites: the layout
audit opens the "more" panel and the per-row details, and the contrast audit does
the same before it samples computed styles (AUDIT-CONTRAST-02). Route coverage is
part of the same rule: a routed page that is missing from an audit's route list is
not "clean", it is unmeasured — the product detail page was absent from the
contrast run until it was added, while the layout and text-overlap audits already
covered it.

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
