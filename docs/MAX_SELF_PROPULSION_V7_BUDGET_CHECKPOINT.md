# Mucaro V7 Aggregate Budget — Bounded Candidate (verification pending)

Date: 2026-10-21
Branch: feature/agent-budget-v7
Parent: feature/agent-authority-v6 (V6 exact-HEAD 1,358 passed; PR integration 1,463 passed, independently qualified at their exact snapshots).
PR: https://github.com/jotaele44/mucaro-ai/pull/259 (DRAFT; DO NOT MERGE)

## Current-head source reconciliation
Current main at observed SHA f36b77ffb5b9baf61cb27cee0faaf755b614fb12
is independently advanced by 43 commits relative to V6 at observation.
Significant families include permission_engine, capability_policy, skill_runtime,
model_failover, portable_runtime_source, performance_benchmark, release ledgers
and Mac/ARM64 packaging. Never overwrite or treat these manifestations as
semantically equivalent to V6. The branch remains isolated.

## Newly implemented
- backend/app/aggregate_agent_budget.py: WAL/FULL SQLite-account ledger,
  transactionally reserved and settled aggregate declared-cost units, separate
  reserved/spent balances, action identity, terminal states and restart recovery.
- backend/tests/test_aggregate_agent_budget.py: 11 tests for aggregation,
  budget exhaustion, idempotency, duplicate settlement, overcharge, contradictory
  receipts, replay, invalid units, restart, and terminal release.
- .github/workflows/v7-exact-head.yml: independent branch-HEAD pytest runner
  and deterministic hashes for V6 + V7 source files; no production execution.

## Critical caveats
- This is declared-unit accounting, NOT independently observed actual model
  tokens, dollars, latency, provider charges or external financial settlements.
- release_unexecuted requires *independent* proof of zero external effect,
  currently a caller responsibility. The ledger does not provide that proof.
- The ledger is not yet integrated into every execution path or claim issuer.
- No full head tests or independent V7 CI receipt claimed before the run finishes.
- Full source identity, policy principal authentication, key rotation/revocation,
  transactional outbox, exactly-once external effects, authenticated evaluator,
  measurable real-task improvement, approved skill promotion, watchdogs and
  TheHub federation remain OPEN.
- Prior V6 independent PASS status is preserved but not transferred to V7
  without an exact-HEAD V7 test manifestation.

## Next acceptance gates
1. Exact current HEAD test run + complete CI, source/receipt manifestation.
2. Review V7 accounting under adversarial concurrent claim and crash/restart.
3. Bind signed V6 grant + declared goal-task budget + trusted metering source in
   an atomic dispatch/admission path. Reject unbudgeted execution.
4. Transactional outbox and executor-enforced idempotent external effects;
   evidence-driven lease recovery before releasing reserved budget.
5. Independently authenticate evaluator principal and sealed holdouts;
   measure real baseline improvements; train only with permitted datasets.
6. Tested reversible skill promotion + canary rollback + continuous monitored run.
7. Reconcile main/source manifests; domain producer authority, TheHub contracts,
   LOCKSTEP and final zero-material-residue release certification.

V7_AGGREGATE_BUDGET=IMPLEMENTED_UNCERTIFIED
V6_FROZEN=PASS_BOUNDED
MAIN_MUTATION=NO
PRODUCTION_MUTATION=NO
