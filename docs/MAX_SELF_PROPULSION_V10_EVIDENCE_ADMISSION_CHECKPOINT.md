# MAX Self-Propulsion V10 — Multi-Authority Evidence Admission

Date: 2026-10-22
Branch: feature/agent-evidence-v10
PINNED TESTED COMMIT: 5c04489ea1d490b7540d1374c711f0c1be9e0f72
PINNED SOURCE ZIP BYTES: 5,509,167
PINNED SOURCE ZIP SHA256: fc332920809c8dfefbbce3584a6dbe9664d04756c92c1e400e9faf536bb6abc3

## Bounded test proof
Exact source archive acquired and extracted in isolated Floot VM.
Selected unittest suites:
- backend.tests.test_independent_evidence_admission
- backend.tests.test_sealed_evaluation_gate
- backend.tests.test_outbox_budgeted_agent
Result: 27/27 PASS, zero failed in selected denominator.

## New contract
Three separately configured signer roles:
SOURCE_AUDITOR, HOLDOUT_CUSTODIAN, EVALUATION_AUDITOR.
An authenticated envelope must contain exact manifest identity, source
commit/tree digest, baseline/candidate source digests, sealed benchmark and
training exclusion manifests, matching evaluator receipt digest and
quantitative denominator/delta/regression counts.
The gate refuses missing attestations, tampering, role/principal duplication,
reused signer key bytes, mismatched cases/source and invalid arithmetic.
A candidate is classified REVIEW_CANDIDATE only with positive reported delta
and zero reported regressions; promoted=false always.

## Claim authority limits
Role keys are configured in the test runtime. HMAC integrity protects
those configured claims but **does not prove key custody, real source bytes,
actual benchmark data provenance, authentic observations or model learning**.
Observed delta is an attested input, NOT independently recomputed from unseen
real-task results in this gate. Separate acquisition, custody, data leakage,
baseline/candidate execution and independent replay are still mandatory.

Previous V9 PR integration CI observed PASS with 1547 pytest passed,
1 skipped, 21 subtests PASS, 246 warnings, QA and macOS PASS. Exact standalone
V9 SHA CI remains unconfirmed. This evidence is not transferred to V10.

## External acquisition requirements (OPEN)
1. Establish repository source/build manifest byte provenance.
2. Provide legally permitted real-task benchmark from an independently
   controlled source, with predeclared case denominator and retained data
   access log showing candidate training exclusion.
3. Provision three role-separated identities/keys through independent
   authority, with rotation/revocation and audit trails.
4. Capture actual baseline and candidate outputs by reproducible executor
   runs on the sealed holdout, attach raw responses and verified score/latency.
5. Compare real improvement, adversarial regressions and operational costs.
6. Review candidate promotion only after signed evidence, reversible canary,
   impact analysis and rollback; no implicit instructions or model-weight changes.
7. Sustain independent operation, reconcile current main, TheHub consumer,
   producer authority and federation LOCKSTEP, then certify only at zero material
   unresolved residue.

V10_EVIDENCE_ADMISSION=BOUNDED_ISOLATED_PASS
EXTERNAL_INDEPENDENCE=OPEN
REAL_TASK_IMPROVEMENT=OPEN
PROMOTION_AUTHORITY=DENIED
FEDERATION_CERTIFICATION=OPEN
