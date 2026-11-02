# Mucaro V13 — Actual Mock-Provider Source-Bound Benchmark

Date: 2026-10-22
Branch: feature/agent-runtime-benchmark-v13
Pinned tested SHA: b4dcf6f202e92ccd6699b4d7439d80c65dbf2a05
Pinned source ZIP size: 5,530,504 bytes
Pinned source ZIP SHA-256: 7220975a0017c1ccf69a06a8f4d7cc7e07aecc0e49f0411293bdd75d4a4bfa16

## Bounded execution
- Invokes the actual backend/app/model_router.py generate_answer function through the local deterministic MOCK provider on four complete prompts.
- Checks on-disk source-file SHA-256 against expected source digest before accepting an implementation binding.
- Retains observed mock outputs, per-case digests, elapsed monotonic durations, case counts, input/output manifestations, and explicit UNMETERED cost.
- 7 new mock-adapter tests plus 12 inherited observed-run tests: 19/19 PASS on a pinned GitHub source ZIP in an isolated VM.

## Fail-closed boundaries
- The source_head is configured; source-file byte checks do not independently establish actual checked-out Git identity.
- Original fixture prompts are known; no independently acquired withheld real-task benchmark or model learning.
- The observed mock provider is not a real live inference model and has no independently proven accuracy improvement, training or generalization.
- Model token charges and external-effect costs are UNMETERED.
- V12 branch CI and this branch current-head tests remain independent hosted gates.
- No model training, skill promotion, repository merge, production credentials, deployment or federation mutation.

## Next
Hosted exact-head and merge-tree CI -> independently retained real-task source bytes and sealed holdout -> authenticated evaluator custody -> actual Mucaro model baseline and candidate with verified hashes and costs -> V11/V12 recomputation -> zero materially adverse regressions -> approved reversible canary and sustained monitoring -> current main reconciliation -> TheHub LOCKSTEP -> zero residue certification.

V13_MOCK_CALIBRATION=BOUNDED_PASS
REAL_TASK_IMPROVEMENT=OPEN
SKILL_PROMOTION=DENIED
FINAL_CERTIFICATION=OPEN
