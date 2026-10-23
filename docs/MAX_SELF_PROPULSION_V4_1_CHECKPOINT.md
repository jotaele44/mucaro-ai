# Mucaro MAX Self-Propulsion V4.1 — Bounded Engineering Checkpoint

Status: **BOUNDED PASS / PRODUCTION OPEN / INTELLIGENCE PLANE PROVISIONAL**.
Date: 2026-10-16. Repository: `jotaele44/mucaro-ai`.
Branch: `feature/governed-learning-loop-20261016`.
No changes to main, deployments, credentials or model weights.

## Frozen exercised manifestation
Pinned source commit before adding this checkpoint:
`2fea130bd29baca06a52cf0177aca3bc04fe3cf8`
Pinned GitHub codeload ZIP: 5,448,789 bytes.
Source ZIP SHA-256: `b3aa56f4cc33ba68120291aa2dfff9e9aad40def039b105fe3740b56bd90f183`.
Full project archive validated for bounded size, member count and nontraversal names.
This hash binds the downloaded ZIP manifestation only, not a repository tree hash.

## Newly implemented
- `goal_reactor.py`: durable, identity-checked declared goals/tasks, same-goal
  prerequisite edges, bounded resource accounting, and explainable priority ranking.
- `governed_skill_bridge.py`: real capability-policy evaluation, active/digest-bound
  skill inspection, IN_PROCESS-only read-only dispatch, worker fencing, runtime-clock
  lease expiry, and actual Mucaro skill execution receipt integration.
- `independent_evaluation_gate.py`: deterministic frozen-answer holdout comparison,
  complete-case denominator, train/holdout nonoverlap, separate claimed evaluator ID,
  regression detection, strict positive-delta requirement, no automatic promotion.
- `bounded_self_propulsion_pilot.py`: finite eligible-task loop, no generated new
  authority, task-specific run identity, completed-worker reconciliation, fixed action
  budget, stop at PASS/OPEN/BLOCKED.
- `leased_agent_queue.py`: consistent stored completion reconciliation with exact
  fence/result digest, preserving prior table identities.

## Actual verified execution
From the pinned complete source archive:
- `python3 -m unittest -v backend.tests.test_max_self_propulsion_v4
  backend.tests.test_max_self_propulsion_recovery` -> **28 tests PASS, 0 FAIL**.
- Real Mucaro `mucaro/intelligence-plane-health` skill mounted and activated by
  explicit `runtime.in_process` policy in an isolated temporary workspace.
- A two-step declared DAG `first -> second` executed **2/2** real skill invocations,
  finished PASS with a goal receipt:
  `sha256:02820f4e093a9053df0938cb1151ac4a969adf701e1d54e294288a60092e51f7`.
- Repeated driver call -> PASS with no additional skill invocation.
- Crash-gap recovery after worker commit, contradictory completion identity,
  receipt tamper, action budget, prerequisite and explicit budget gates exercised.
- Earlier one-task real skill pilot and 22 V4 tests also passed on pinned preceding
  source, but are not additive to the 28 V4.1 regression denominator.

## Critical distinctions
- The test execution is a bounded offline, read-only, credential-free *real skill* run,
  not production operation or unrestricted agent execution.
- The evaluator compares deterministic sealed-input *fixtures*. The
  `independent_evaluator_authorized` flag and distinct string ID are caller declarations,
  NOT independent cryptographic principal authentication or a separately deployed
  evaluator. Promotion is never performed.
- Tests do not establish benchmark validity, actual agent learning, genuine
  performance improvement on previously unseen real tasks, or RL weight updates.
- The goal reactor selects from a predeclared DAG; it does not spontaneously
  discover, approve or create new goals.
- Hash-linked events are not an externally immutable log absent signed or
  independently anchored checkpoints.
- The low-level V3 leased queue still accepts a caller boolean `permitted`.
  The policy-bound bridge is the allowed V4 pilot path, but direct queue access
  must be sealed before general tool execution can be certified.
- The job-result ledger and goal receipt use local SQLite. Crash recovery and
  external side-effect idempotency require further production-grade trials.
- Full repository pytest/CI, GitHub-hosted CI at current branch HEAD, mobile
  verification, sustained scheduling, model training, agent federation and
  canary/promotion rollback were NOT executed.

## Contradiction adjudication
V4.1 first multi-task attempt: FAIL, 2 tests ERROR, due to V3
`UNIQUE(run_id,phase)` collision. Cause: using the goal ID as a run ID for
multiple EXECUTE-phase tasks. Superseded by stable
`digest(["goal-task",goal_id,task_id])` per-task run identity, without schema migration.
Retest at pinned commit: 28/28 PASS and real two-task execution PASS.
Preserve previous failure manifestation as SUPERSEDED, not erased.

## NEXT VECTOR / certification gates
1. Freeze this checkpoint; preserve parent commits and every prior evidence receipt.
2. Replace the caller-supplied queue policy boolean with authority-signed or
   policy-evaluated capability tokens; enforce revocation and stale-policy fences.
3. Introduce a transactional dispatch/outbox with heartbeat, backoff, retry
   classification, watchdog, dead-letter reconciliation, and side-effect idempotency.
4. Add authenticated independent evaluator worker and frozen benchmark/holdout provenance.
5. Integrate a read-only evidence acquisition task, coding sandbox and bounded
   delegated agent workloads without expanding rights.
6. Implement persistent multi-agent goal DAG, schedule triggers, memory conflict
   policy, opportunity discovery and executable training candidate intake.
7. Recheck actual before/after real-task improvement, leakage, reward gaming, rollback.
8. Run complete repository test/CI/security, production-equivalent and device gates.
9. Integrate TheHub cross-repo consumer authority only through explicit contracts;
   keep domain identity ownership in producer projects.
10. Certify bounded component at zero material residue; do not issue
    `SELF_PROPULSION_CERTIFIED` or `FEDERATION_INTELLIGENCE_PLANE_CERTIFIED`
    until all relevant gates close.

## MAX score (planning, not production certification)
Execution strategy: 96/100.
Hardening strategy: 96/100.
Measured outcome for declared V4.1 fixtures: 28/28 PASS plus 2/2 actual
registered health skill executions, not general-agent certification.
