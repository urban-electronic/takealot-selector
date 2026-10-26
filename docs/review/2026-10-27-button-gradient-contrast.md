# PR183 button gradient contrast review (2026-10-27)

Issue: UI-GRADIENT-CONTRAST-01. Priority P1, R3. Owner: Codex.
Candidate reviewed: ee594e49b1c8903f7e6648ca670f4befd7e7959f (PR183).

G2 / H4: the browser contrast audit reads computed backgroundColor, ignoring backgroundImage. The new opaque gradients paint over that fallback. Small white text on the primary gradient's top stop is 4.11:1; success gradient top is 3.52:1 (hover 3.17:1). The PR's green audit therefore does not certify painted button contrast.

Fix: darken only the blue/green button gradient stops and matching fallback colors, retaining PR183's layout, tokens, focus fixes and all original commits. A focused test reads the actual six normal/hover gradients from CSS, checks their expected opaque two-stop shape, and samples each at 101 positions against white text (4.5:1 minimum). It is automatically included by selfcheck's test-*.cjs discovery.

Same-condition verification:
- Original CSS + new test: exit 1, .btn-primary 0% = 4.11:1.
- Corrected CSS + same test: exit 0, 6 gradients / 606 paint samples >=4.5:1.
- Reverse probe uses original candidate CSS; no production writes.
- Full latest-head CI pending at submission; isolated arithmetic checks do not certify rendered geometry, real browser interactions, deployment or business data.
- Review conclusion: PR183 requires this correction before merge (G2 P1, H4 P1); full CI and final head review remain required.

Rollback: revert the correction commit; this would restore the demonstrated contrast defect. No data/schema/price behavior changes. No live collection, credentials or production data used.
