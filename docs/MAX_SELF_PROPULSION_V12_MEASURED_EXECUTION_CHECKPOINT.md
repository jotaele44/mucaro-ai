# Mucaro MAX Self-Propulsion V12 — Measured Execution Checkpoint

Date: 2026-10-22
Branch: feature/agent-measured-runs-v12
Pinned tested source SHA: df98098f19fe16ba55181c14149798f2e7eb964d
Pinned GitHub ZIP bytes: 5,524,322
Pinned ZIP SHA256: d3e6c84e2e6a65920b82f38965ceafae179d872c5d9d138f6961078b57b584ef

## Observed executable proof
New backend/app/observed_benchmark_runs.py invokes explicitly supplied,
bounded Python handler functions against exact case inputs; stores per-case
input digests, actual returned string outputs/digests, elapsed monotonic
nanoseconds and source/implementation/runtime configured identity.
Separate comparison method computes passes, fixes, regressions and accuracy
delta from observed outputs against caller-supplied truth.
Receipts preserve UNMETERED cost and no promotion/independence certification.

New backend/tests/test_observed_benchmark_runs.py:
- 12/12 PASS, including actual baseline/candidate fixture handler calls,
  output mutation, source mismatch, runtime identity mismatch, incomplete
  denominator, handler failure, nonstring result and regression denials.
- Prior V11 attested replay tests included in selected run: 12/12 PASS.
- Exact pinned archive in independent isolated VM: 24/24 selected PASS.

Illustrative observed fixture: four input words, baseline handles two,
candidate handles four, recomputed delta +0.50 without regression;
classification FIXTURE_REVIEW_ONLY, promoted=false. This is NOT model
training or independently sourced real-task improvement.

## Certification limitations
- Handler source/implementation digest is configured by caller and must
  later be bound to independently acquired bytes and deployed process.
- Input tasks/answer keys are synthetic and accessible to test designer;
  they are not held in separate authorized custody or shown unseen.
- Timing is actual local monotonic elapsed ns per invocation but isolated
  fixture measurements do not imply performance improvement in production.
- Provider charges, model token costs, energy and hardware metrics UNMETERED.
- Actual Mucaro model candidate, baseline deployment and signed independent
  execution provenance have NOT been acquired or compared.
- This is independent of V11 PR merge CI; no source ancestry equivalence
  or output manifestation across main/federation is certified.
- V11 branch was 82 ahead / 45 behind main at last comparison.
- All code stays DRAFT/unmerged. No training, promotion, source mutation,
  production rollout, policy grant, key rotation or TheHub LOCKSTEP.

## Next gates
1. Current-head hosted CI and independent exact-HEAD receipts.
2. Acquire restricted real-task benchmark legally; freeze task bytes,
   sample selection, complete denominator and custodian provenance.
3. Authenticate independent evaluator principal and signing key lifecycle.
4. Bind baseline/candidate binaries/code revisions to exact signed
   execution manifests, run the actual Mucaro function/skill/model.
5. Record genuine independent outputs and costs, recompute gate V11/V12,
   adversarial evaluate domain-general task drift and holdout leakage.
6. Independent review + reversible canary skill promotion only after
   demonstrated no regression and validated true improvement.
7. Sustained supervised operation, current main conflict reconciliation,
   TheHub producer/consumer proofs and federation LOCKSTEP.
8. Issue global certification only after all material FAIL/OPEN/BLOCKED
   items inside declared certification scope reach zero.

V12_EXECUTABLE_FIXTURE_EVIDENCE=BOUNDED_PASS
TRUE_REAL_TASK_IMPROVEMENT=OPEN
FULL_CI=OPEN
INDEPENDENT_EVALUATOR_CUSTODY=OPEN
SKILL_PROMOTION=DENIED
FEDERATION_CERTIFICATION=OPEN
