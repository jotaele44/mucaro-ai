# Mucaro V14 — Local Git Source Attestation

Date: 2026-10-22
Branch: feature/agent-source-attestation-v14
V13 predecessor verified 19/19 bounded mock-provider cases at pinned source b4dcf6f202e92ccd6699b4d7439d80c65dbf2a05.
Preliminary V14 Git-dependent isolated VM attempt at de6b9c911ed2ec72d1d0de9ca39e21e3e883947e yielded 7 passing V13 tests and nine V14 setup errors because the Floot VM has no git binary. Do NOT classify as a code pass; preserve as a harness-blocked attempt.

## New logic
- backend/app/local_source_integrity.py independently reads actual local Git HEAD, tree, clean worktree, tracked source blob and module bytes; checks expected head and SHA256.
- backend/app/model_benchmark_adapter.py invokes execute_git_attested_mock_calibration with imported Python generate_answer file identity equaling the verified checkout source file; rejects copied alternative modules.
- Tests: 9 local source integrity adversarial cases, 4 integrated module-binding cases, plus all prior tests.

## Acceptance
- GitHub CI Git-capable runner must independently run exact branch HEAD tests, including new Git-dependent suites. Verify both standalone HEAD and PR merge tree; they are not interchangeable.
- Source HEAD/tree/blob and path integrity prove only local checkout conservation, not external signed source supply chain or actual independent evaluator identity.
- V13 mock provider remains deterministic mock; no real-trained baseline/candidate delta, independent task acquisition, provider costs, source production release or user promotion.

## Next
Git-capable full test suite; independent real-task source and holdout custodian; source/provisioned runner identity attestation; execute real Mucaro model baseline/candidate with measured costs; independent score replay; reviewed reversible promotion and sustained monitoring; reconcile main and TheHub; federation LOCKSTEP; zero material residue certification.

V14_GIT_SOURCE_INTEGRITY=UNVERIFIED_PENDING_HOSTED_CI
MOCK_MODEL_IMPROVEMENT=NOT_DEMONSTRATED
PROMOTION=DENY
FINAL_CERTIFICATION=OPEN

## V14 hosted verification follow-up (2026-10-22)
Standalone exact HEAD `038c9d4f76fe572660050ae73afc77c1eff3943b`,
GitHub Actions run `39887684693`: **PASS**, 1453 passed,
1 skipped, 21 subtests passed, 246 warnings. Targeted Git identity and
source tests passed, source receipt archived as artifact `12473288048`.
This is HEAD-only verification, NOT PR merge-tree CI.

PR integration CI run `39887690367`: **FAIL**, 1637 passed,
1 failed, 1 skipped, 21 subtests passed, 405 warnings.
The failure was `test_permission_coverage.py` from the independently
advanced main: unclassified `local_source_integrity.py` read-only Git
subprocess. Hosted rendered QA and macOS tests at the original head PASS.

A test-only transplant of the complete newer-main policy suite
(`4679a0d563d70b302698c3f542fd3d63eb560ce3`) was rejected:
it caused 11 errors/failures on the older feature-source branch because its
`permission_engine` and migrated call-site dependencies are absent.
Its exact-source run `39891944026` is FAIL (1454 passed,
11 failed, 1 skipped), while source-integrity targeted tests PASS.
The transplant was **reverted** at `4e3f601ecc95abe7f30e413f55af1388270f9933`.
Do not count reverted transplant as PASS or weaken the main permission policy.

A true current-main reconciliation must preserve main's complete
`permission_engine` migration, explicitly adjudicate whether this narrow
read-only Git introspection is an auditable exception or needs delegated
permission, and rerun exact source/merge-tree denominators independently.
No merge or deployment is eligible while PR security-gate residue remains.

V14_EXACT_HEAD_PREDECESSOR=PASS
V14_CURRENT_HEAD=OPEN
V14_MAIN_MERGE_TREE=FAIL
V14_SECURITY_GATE=OPEN
