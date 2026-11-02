# MAX Self-Propulsion V7.2 — Budget Admission + Durable Outbox Checkpoint

Date: 2026-10-22
Branch: feature/agent-budget-v7
Pinned code SHA: ea22e5674a562758572577477aea09ec0f01d49e
Pinned source ZIP size: 5,488,248 bytes
Pinned ZIP SHA256: 515bed1a63b333a03cc682383a8a3489a987fb7578cd2ce82a0b98ec730b6a87

## Verified in isolated clean full-source archive
Executed Python 3 standard-library unittest:
- backend.tests.test_aggregate_agent_budget
- backend.tests.test_budget_admitted_agent
- backend.tests.test_durable_dispatch_outbox
Results: 24/24 PASS, no failed test in this selected denominator.

The initial V7 budget-admission test manifested FAIL 1/17 because its mocked
executor did not persist Goal Reactor success but asserted repeated PASS.
Test corrected to record the exact Goal Reactor completion and verify
repeated run does not redispatch. Follow-up 17/17 PASS, followed by 24/24 PASS
with the new outbox. Preserve pre-repair failure as SUPERSEDED.

## Scope
AggregateBudgetLedger: persistent budget accounts; aggregate declared-unit
reservation, settlement, exhaustion, identity reconciliation, restart.
Budget-admitted adapter: reserve before task execution; deny missing account,
budget exhaustion, incompatible source input; retain budget on ambiguous failure.
DurableDispatchOutbox: transactional intent declaration with stable idempotency
key; record externally verified receipt; restart-safe read-only reconciliation.

## Status/gates
V6 frozen exact-head PASS (historical).
V7.2 isolated budget/outbox corpus PASS 24/24.
V7.2 full repository CI, actual registered skill integration under budget adapter,
independent backend evaluator, telemetry-backed usage, authenticated principals,
immutable source admission and current main reconciliation: OPEN.
Outbox and budget ledger are separate SQLite databases. No atomic multi-database
commit between goal, queue, outbox and budget ledger; external action idempotency
must be enforced by the external executor. Unknown effects must stay reserved
and OPEN rather than retried.
The outbox does not itself run actions, certify their effects, or perform rollback.
No autonomous learning effectiveness demonstrated.
No training, skill promotion, PR merge, production deployment or federation
authority mutation performed.

## Next gates
1. Exact branch CI and PR merge-tree CI source/receipt conservation.
2. Budget and outbox admission integrated with actual real Mucaro tool runner.
3. Trusted external idempotency + bounded crash/lease recovery and effect
   reconciliation with separate negative corpus and rollback.
4. Authenticated evaluator worker/holdout source permissions and real-task
   measured improvement vs frozen baseline.
5. Reviewed promotion/rollback, sustained supervised execution, complete
   producer authority + TheHub LOCKSTEP, release source/build/data/native
   manifests and zero material residual certification only.
