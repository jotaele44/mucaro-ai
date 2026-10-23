# MAX Self-Propulsion V9 — Sealed Evaluation Contract

Date: 2026-10-22
Branch: feature/agent-evaluator-v9
Pinned exercised source: af9aa231fd2a335024dab25dffb4e64151ad3e70
Pinned codeload ZIP bytes: 5,502,108
Pinned ZIP SHA-256: 31628c9c40f1c375ebff0486ad84b1c715f88f0033c2b2fad44424414710d9f3

## Implemented
- SealedHoldoutAuthority: benchmark revision, truth digest, complete declared case IDs, HMAC-SHA256 benchmark sealing with curator key.
- VerifiedEvaluationGate: separate configured evaluator ID/key; validates trusted source seal and all exact-answer cases; prohibits curator/proposer self-evaluation; uses previous fixed holdout comparison and regression gate; returns signed evaluation receipts that cannot promote skills.
- Separate signer/verifier secrets are supplied by trusted caller, not embedded in production source.
- Tests reject signature/truth tampering, training/holdout leakage, missing candidate predictions, positive delta with regressions, same-key identity and unsigned promotion.

## Evidence
- Extracted pinned complete GitHub source archive into isolated disposable runtime.
- Executed test_sealed_evaluation_gate, test_outbox_budgeted_agent, test_aggregate_agent_budget, test_durable_dispatch_outbox.
- 34/34 selected unittest cases PASS.
- This is evidence of functional and fixture-level cryptographic gates, not model learning effectiveness.

## Critical open gates
- 'Independent' currently means distinct configured signer IDs and key bytes, not verified service identity or independent key custody. Key provisioning, role attestation, cross-process isolation, audit and rotation OPEN.
- Holdout fixture itself is synthetic, not a real-task sealed benchmark proven acquired and withheld from candidate training. Operational contamination controls OPEN.
- Model weights, training, reinforcement learning, improvement experiments and production promotion NOT executed.
- V8 full CI, V9 full CI, updated main reconciliation, TheHub, device and sustained autonomous operation are independent OPEN gates.
- HMAC signer can attest inputs/receipts but cannot prove truthful labels or actual task outcomes without verified source evidence.
- V8 external-effect exactly-once guarantees and aggregate cross-database transactions remain OPEN.

## Next gate
Trusted evaluator runtime identity -> sealed independent real-task baseline and holdout provenance -> assess candidate on previously unexposed tasks -> quantitative improvement + adverse-regression corpus -> independently signed measure -> operator reviewed versioned reversible skill promotion only after PASS -> retained canary/rollback -> sustained monitoring -> TheHub verification -> LOCKSTEP -> zero material residue.
