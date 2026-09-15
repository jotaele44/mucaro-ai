# Coding governance invariants

The following invariants are binding for this source increment:

1. `coding.patch_apply` is mutation-capable and must remain approval-gated.
2. Patch application must revalidate exact HEAD, patch SHA-256, full candidate path set, preimages, and target dirty overlap.
3. Patch application must not create a commit or alter HEAD.
4. Unrelated pre-existing dirty paths must be preserved.
5. Unexpected post-mutation dirty paths trigger rollback handling and cannot be silently accepted.
6. Coding check plans use pinned image identity, network none, and a read-only repository mount.
7. `PLANNED` is never equivalent to executed PASS.
8. Code-review findings require changed-path membership, current source SHA-256, and valid line evidence.
9. No-findings review state is not evidence of defect absence.
10. Hosted CI startup-before-jobs is infrastructure BLOCKED, not test FAIL and not PASS.
11. Frozen denominator membership is not mutated by source implementation progress.
12. Certification requires zero unresolved residue inside the declared coding scope.
