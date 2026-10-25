# Autonomous Agent Loop — V2 Bounded Execution Checkpoint

## Snapshot and scope
Feature branch: `feature/governed-learning-loop-20261016`.
V1 evidence-candidate ledger is preserved. V2 adds `backend/app/durable_agent_loop.py`
and an isolated regression suite.

## What is implemented
- SQLite WAL/full-synchronous run and phase records.
- Idempotent run creation by immutable objective/input/policy/executor identity.
- Sequential, single-process phase progression with explicit policy callbacks.
- Crash/reopen preservation and no re-execution of already completed phases.
- Terminal blocked/failed outcomes, output hashes, and chained event observations.
- Promotion, monitoring and recovery are not automatically executed.
- Execution is injected and must return a positive receipt; it is not given
  authority to invoke production tools, change instructions or train weights.

## Verification evidence
The isolated standard-library Python verification runner executed eight test
functions in `test_durable_agent_loop.py` with 8 PASS and 0 FAIL.
The earlier learning ledger has 19 positive/adversarial parameter-expanded cases
reported PASS. This is NOT the full repository pytest/CI gate.

## Known limitations
- At-least-once side-effect hazard: an external executor may complete work then
  crash before the database commit; exactly-once external effects are not proven.
- Coordinator currently holds a local SQLite write transaction while invoking
  the injected executor: acceptable only for bounded proof-of-concept operations,
  not a distributed or long-running deployment.
- No lease timeout, cancellation, budget governor, dead-letter queue, retry policy,
  parallel dependency-graph scheduler, or independent process worker yet.
- The evaluator/benchmark proposal ledger is not wired transactionally to the
  durable coordinator, and evaluation independence is currently a caller claim.
- The immutable source-evidence store and external checkpoint signature remain OPEN.
- Live autonomous instructions, skills, code deployment, fine-tuning, RL updates,
  cross-repo federation and unattended promotion are NOT implemented or certified.

## Next gates
1. Split reservation/dispatch/commit transactions; add leases, heartbeats,
   idempotency receipts, retries, cancellation, time budgets and recovery.
2. Bind actual Mucaro tool runtime via capability policy and runtime identity.
3. Require an independently owned evaluator principal, independent holdout corpus,
   and audited baseline-vs-candidate score.
4. Implement versioned promotion transaction (approval required), continuous monitor
   with regression thresholds, and rollback without data loss.
5. Run full repository tests/security checks at exact immutable commit.
6. Execute a genuine authorized task end-to-end and freeze reproducible receipts.

Do not label the system `GENERAL_AGENT_CERTIFIED`, `SELF_IMPROVEMENT_CERTIFIED`
or `FEDERATION_INTELLIGENCE_PLANE_CERTIFIED` before zero material residue.
