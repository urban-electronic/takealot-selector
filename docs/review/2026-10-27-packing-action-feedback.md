# PACK-BANNER-EXCLUSIVE-01

- Priority P2; R3; owner Codex. Current comparison base: main 22e414967a6704c0099a4635aceea78d4d28a76b. PR185 retains both its original candidate and current main as ancestors.
- Current main already fixes ordinary action banner exclusivity (BANNER-EXCLUSIVE-02). Preserve its notice/fail helpers, reasonOf, skippedNote export reminder, Blob fixture, and all concurrent changes in other pages.
- Remaining trigger: image matching returns success, then getPackingProducts rejects. Current main calls fail during reload, then notice after reload, erasing the list-read failure. Editing/deleting also lack a current success notice.
- Incremental fix: emit action success before list refresh; keep the read error independent from the confirmed mutation result. Expire previous CTA in notice/fail. Add edit/delete success notices. No payload, stock or price logic changes.
- Regression: add isolated image-sync success/failure and list-read failure branches to the existing DOM suite; retain main's export success and skippedNote guards. Add an in-memory reverse probe restoring fail in loadProducts; require exit 1 at the confirmed-success assertion, then run the fixed implementation. No production requests.
- Historical evidence: original PR185 head 1fba111 passed 74 isolated assertions and TypeScript locally and full PR Check 40421595070. Those results do not validate this reconciled head.
- Current candidate: local execution unavailable (exec-server could not create a process); new-head CI, reverse probe, typecheck and integrated checks pending. Do not claim the 79 normal assertions passed until CI logs confirm. Deployment and real-session acceptance pending.
- A1/A3/H1/H4/G2: scoped to packing feedback; require both latest-head PR Check jobs before merge. Rollback by reverting focused change. Real naming writes, quote return and 369-piece inventory correction remain separately unverified.
