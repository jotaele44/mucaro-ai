# Mucaro MAX Self-Propulsion V11 — Attested Evaluation Replay

Date: 2026-10-22
Branch: feature/agent-replay-v11
Pinned tested SHA: 72aa26fe48f1d8c08c6427dcf8bcfece35542ee1
Pinned source archive size: 5,516,530 bytes
Pinned source archive SHA-256: a2097ef248fce12681cac46121b758218f791cce4bba2886c1759da7ff581201

## Scope of engineering
- backend/app/attested_evaluation_replay.py
- backend/tests/test_attested_evaluation_replay.py
Uses the V9 sealed evaluator and V10 three-role evidence admission contracts.
Produces candidate-only replay receipt and independently recomputes accuracy,
fixed and regressed cases, denominator, delta, signed evaluator receipt
digest, baseline/candidate output digests, and sealed truth/training exclusion
manifest digests from supplied inputs.
Requires all signed attestations to match that recomputation. Refuses claimed
improvement contradicted by the supplied candidate outputs and rejects a
candidate with any regression. This contract never grants skill promotion.

## Observed verification and historical failure
- Initial pinned candidate 4c293d0abfafe13e3f96d002e4c4944431ea287f:
  33 selected cases, 31 PASS, 2 ERROR. Underlying V9 EvaluationGateError
  for missing case and holdout leakage was not normalized by the V11 boundary.
- Patched tested source 72aa26fe48f1d8c08c6427dcf8bcfece35542ee1:
  33/33 selected tests PASS in isolated VM from exact pinned GitHub ZIP.
- Historical initial failure is SUPERSEDED but preserved in test lineage.

## Certification boundaries
- The inputs and signing secrets in the suite are synthetic fixtures.
- The trusted key configuration does NOT independently authenticate evaluator
  operator/service or prove genuine secret custody/rotation/revocation.
- Real-task holdout acquisition, data use licensing, independent retention
  and candidate exclusion, source-byte provenance and execution traces OPEN.
- Reproduced scores over caller-provided output dictionaries do not by
  themselves establish that candidate model/code produced those outputs.
- Operational measured self-improvement, independent baseline/candidate
  run attestations, cost/latency analysis and negative distribution stress OPEN.
- V11 full exact-HEAD CI and PR integration CI OPEN pending hosted result.
- Current independently advanced main requires source/authority reconciliation.
- No merge, production deployment, training, capability expansion, skill
  promotion, external runner idempotency claim or TheHub LOCKSTEP undertaken.

## Ordered next scope
1. Hosted exact-head and PR-merge-tree regression receipts (distinct trees).
2. Independent source manifest and benchmark byte acquisition with access logs.
3. Provision genuinely separate evaluator principal and key custody.
4. Execute frozen real baseline/candidate on sealed unseen holdout; capture
   full actual outputs, runtime identities, latency and declared/actual costs.
5. Replay those raw results through this admission/recompute contract and
   independent verifier. Reject zero/improper improvement or regressions.
6. Reviewed and authorized reversible skill promotion trial, rollback proof,
   sustained canary monitoring and adversarial abuse testing.
7. Reconcile main and producers, verify TheHub as cross-repository consumer,
   federation LOCKSTEP, freeze receipts and certify only at zero residue.

V11_REPLAY_CONTRACT=BOUNDED_ISOLATED_PASS
REAL_TASK_INDEPENDENCE=OPEN
GENERAL_SELF_IMPROVEMENT=OPEN
PROMOTION=DENY
FINAL_CERTIFICATION=OPEN
