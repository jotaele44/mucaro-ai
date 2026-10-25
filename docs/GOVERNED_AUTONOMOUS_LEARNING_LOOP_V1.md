# Governed Autonomous Learning Loop — V1 Integration Contract

Status: **CANDIDATE / AUDIT_ONLY**. Branch-only implementation. Not production-authorized.

## Purpose
Mucaro is an agent runtime with a Chat ↔ Code UX, not merely a conversational model.
This module establishes a deterministic evidence-and-improvement ledger without granting
any new execution, model-training, repository mutation, credentials, or policy authority.

## Phases
PERCEIVE → UNDERSTAND → PLAN → EXECUTE → OBSERVE → EVALUATE → LEARN →
OPTIMIZE → VERIFY → PROMOTE → MONITOR → RECOVER.

The ledger records phase observations; it does **not** automatically run these phases.
Each phase may be OPEN, PASS, FAIL, or BLOCKED; phase advancement is ordered, not
implicitly certified by successful logging.

## Boundaries
- RAW evidence, derived observations, proposed improvements, and promoted skills remain separate.
- Proposals require a passed event hash and a candidate/benchmark digest.
- Supported destinations are memory, skill, workflow, agent policy, project instructions,
  evaluation dataset, training candidate, and code patch.
- Core policy, credentials, capability authority, and production are excluded.
- A positive benchmark delta is insufficient: independent evaluation, regression PASS,
  permitted use, and explicit operator approval are all necessary.
- PROMOTION_ELIGIBLE is **not** PROMOTED. The V1 module never performs promotion.
- No model weights are updated. Any future RL, fine-tuning, or reward-model pipeline
  requires isolated versioned datasets, licensing/consent checks, holdouts, human
  oversight and tested rollback.
- All identifiers must be bound through authority; name-only identity is forbidden.
- Hash chains help detect accidental changes; without signed checkpoints or a trusted
  append-only remote store, a party controlling the whole ledger can recompute hashes.

## Next integration gates
1. Persist run receipts transactionally with non-rewritable external checkpoint digests.
2. Bind the run to the existing Mucaro runtime_identity, skill_runtime, execution_router,
   capability_policy and governed web/repository tool adapters.
3. Build idempotent worker state transitions with leases, retry limits, cancellation,
   crash restart/replay, dead-letter and budget constraints.
4. Implement observer/evaluator isolation, holdout datasets, metrics and negative cases.
5. Add versioned promotion transactions and rollback with operator approval for
   instruction, skill, code and training changes.
6. Prove a real external task → independent score → candidate improvement → second
   independent score → safe promotion/rollback cycle, with source and output hashes.
7. Re-run complete repository CI/security gates at exact pinned HEAD, then request review.
8. Keep cross-repository authority at TheHub and domain authority in producer repos.

## V1 verified bounded scope
An isolated Python standard-library harness ran 19 parameter-expanded positive and
adversarial checks successfully. Repository-level pytest and full CI were not run during
the chat execution. This document and code do not certify real autonomous execution.
