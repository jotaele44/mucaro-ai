# MAX Self-Propulsion V4.2 — Pinned Execution and Safety Receipt

Date: 2026-10-16
Branch: `feature/governed-learning-loop-20261016`
Disposition: **BOUNDED PASS / GENERAL AUTONOMY OPEN / FEDERATION OPEN**
No production deployment, model training, policy-authority expansion, or main merge.

## Pinned code and archive snapshot
- Exercised exact commit: `b3a1cd0e5029430073c6429a39be0d5384528d81`
- GitHub codeload ZIP: 5,452,526 bytes
- ZIP SHA-256: `1ada9ee649ded3062a7567fc0434d4cdfc69f57d4b9d56cd58df62def2d6e643`
- Complete repository archive extracted to a clean isolated workspace after
  size, membership, and path-traversal checks. ZIP hash represents archive bytes,
  not Git tree identity.

## Verification at pinned code
`python3 -m unittest -q backend.tests.test_max_self_propulsion_v4 backend.tests.test_max_self_propulsion_recovery`
=> **29/29 PASS, 0 failures**.

Actual `skill_runtime`/real `capability_policy`/real `execution_router`
under an isolated `Settings` configuration:
- Explicit in-process policy authorized the preexisting built-in
  `mucaro/intelligence-plane-health` skill.
- Two dependent, predeclared tasks `first -> second` ran through the actual
  Mucaro registered skill implementation, bridge, fenced queue and goal reactor.
- Result: 2 actual skill executions, 2/2 task PASS, second driver call PASS
  with zero additional skill invocations.
- Goal receipt:
  `sha256:8128109099da2c72d714b14ddec6068821b7eb31b2d6f67d46759d0f48a4bb5f`.
- Crash-gap tests verified recovery from a completed worker receipt, no double
  dispatch, identity conflict denial, receipt-tampering denial, bounded action
  budgets and deterministic dependency ordering.
- Zero-capability status alone is not accepted as safety evidence. The V4.2
  bridge adds an explicit reviewed health-skill allowlist; even this handler
  creates local data directories in the isolated fixture workspace.
- Scope is credential-free, explicitly authorized local health probe execution,
  not arbitrary side-effect-free general execution.

## Contradictions and supersessions
- V4.1 pre-repair multi-task run: 2 tests ERROR due to V3's
  `UNIQUE(run_id,phase)` and an incorrectly reused goal-level run ID.
- Repair: task run identity `digest(["goal-task",goal_id,task_id])`.
  Legacy queue schema preserved; V4.1 pinned 28/28 PASS.
- V4.2 adds explicit skill allowlist and negative regression. 29/29 PASS.
  Keep all three observations as sequential manifestations; earlier failure
  SUPERSEDED, not erased.

## Learning and independent evaluation gate
- Frozen exact-answer fixture comparison computes baseline 2/4 vs candidate
  4/4, positive delta with zero fixtures regressed, and returns
  `PROMOTION_CANDIDATE`, not `PROMOTED`.
- The improvement is synthetic evaluation-fixture evidence, NOT real-world
  measured general-agent improvement.
- `independent_evaluator_authorized=True` and distinct named evaluator ID are
  caller declarations. There is no independently authenticated evaluator worker,
  cryptographic attestation, sealed external holdout, or signed run transcript.
- No memory promotion, instructions update, RL, SFT, adapter training,
  autonomous code patch or deployment occurred.

## Governance boundary
- The full repository pytest/CI suite was NOT run, only the selected 29 test cases.
- GitHub combined commit status returned no statuses; commit PR-workflow query
  returned no runs. This is **NO CI EVIDENCE**, not CI PASS or CI FAIL.
- Production, arbitrary tool authority, federation worker execution,
  continuous scheduled goals, cross-repo mutation and recovery from real
  external effects remain OPEN.
- Existing raw V3 queue method accepts a caller-supplied `permitted` boolean.
  This must be replaced with verifiable authorized dispatch before broad worker
  exposure. The V4.2 bridge never accepts a user-supplied `permitted` flag.

## Next acceptance vector
1. Introduce an immutable authorization decision bound to exact task, tool,
   skill digest, workspace, source revision, worker, expiration and budget;
   remove boolean bypass at the low-level queue boundary.
2. Move external action execution behind transactional outbox/lease authority,
   add lease heartbeat, watchdog, exponential retry, dead letter, cancellation,
   drift checks and side-effect idempotency.
3. Integrate actual independent evaluator service and frozen nontraining
   holdouts, validate reward/metric falsification and source permissions.
4. Bind authorized acquisition, coding and multimodal skills one by one,
   with no implicit privileges and per-skill adversarial tests.
5. Implement autonomous goal/event discovery under explicit standing mandate,
   dependency DAG, capacity scheduler, stagnation/budget stop conditions.
6. Add separately reviewed and reversible candidate promotion, online
   monitoring, measured holdout performance and rollback/compensation.
7. Execute full repo test/CI/security checks and real-time sustained durability,
   then apply TheHub explicit federation contract and LOCKSTEP.
8. Certify only closed, bounded scopes with source/receipt hashes, zero material
   unexplained residue, and independent production validation.

## Score
Engineering workflow design 96/100; preemptive hardening design 96/100.
This score is an assessment of the plan, not autonomous production readiness.
The measured evidence is 29 selected tests PASS and 2 genuine health-skill
executions under an isolated credential-free fixture.
