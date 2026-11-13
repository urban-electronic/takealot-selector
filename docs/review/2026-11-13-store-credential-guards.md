# STORE-CRED-01 candidate acceptance

Owner Codex; P1 STORE-IDENTITY-LATE-01 / STORE-PORTAL-CLEAR-01.
Base PR236 ed16ff46403b on main63a648b. Preserve all WorkBuddy contributions in an independent branch.

- Actual Settings component with controlled Promise completion reproduces unsafe editable identity after switching store on ed16ff4. Earlier extracted actual loader also reproduced A late response replacing B's draft. Fix immediately clears and locks identity on switch, invalidates read/save generations, suppresses stale errors/finally/refresh, blocks duplicate saves synchronously, and enables saving only after the selected store's read succeeds. Read failure offers retry. Unmount invalidates pending results.
- Legacy default-store Seller/Portal credential read-through revives old credentials after clear. Four tests (each credential, with/without a scoped replacement) fail on ed16ff4. Explicit empty scoped rows now mean revoked; absent scoped rows alone permit legacy fallback. Legacy encrypted rows remain unchanged for rollback. Repeated clearing remains revoked; explicit configuration restores access. Other stores remain isolated.
- Corrected the backend late-response test to actually start A with create_task and an Event barrier, complete B before A, and assert completion order.

Validation: backend credential suite19 passed; actual Settings component covers read disorder, read failure, duplicate save, stale save success/error/finally, fresh retry and subsequent save; latestGate8 checks; API contract17 checks; TypeScript check passed. Full integrated gate and deployment are recorded in the PR when available. All DB/API calls use isolated fixtures; no real credentials, platform calls, prices, purchases or inventory changes.

Rollback: revert the follow-up commit for the UI; revocation rows are intentionally retained. Rolling the entire feature back to pre-PR236 reads legacy credentials again, so requires explicit operator review; rollback is not a revocation mechanism. Multi-store background scheduling and user-specific authorization remain outside this candidate. Actual authorized two-store Seller identity/readback remains unverified.
