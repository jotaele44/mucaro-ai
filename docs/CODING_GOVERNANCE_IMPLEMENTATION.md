# Coding governance implementation receipt

Scope: source-level governed coding substrate layered on top of merged PR #21.

## Implemented in this branch

- `coding.patch_apply` is routed through the existing request → approve → run tool lifecycle and is declared `mutates_state=true`.
- Patch mutation accepts only a pre-approved Git patch and revalidates exact HEAD, patch SHA-256, candidate path set, preimages, and dirty overlap immediately before mutation.
- Post-mutation HEAD conservation is enforced. Unexpected dirty expansion or HEAD drift triggers explicit reverse-patch rollback handling; rollback failure remains material residue.
- Test/lint/type/build/security/benchmark checks produce deterministic, read-only, network-disabled container plans only. They do not claim execution or PASS.
- Code-review findings bind to changed/untracked source paths, source SHA-256, and exact line evidence.
- `_architecture/coding_regression_manifest.json` defines positive, negative, provisional, blocked, and certification gates for the coding plane.

## Explicitly not complete

- No arbitrary repository command executor is added.
- No writable repository container mount is added.
- No live coding image/engine execution is certified.
- No test/lint/type/build command is represented as PASS without executed evidence.
- No branch/commit/PR mutation lifecycle is added in this branch.
- No Mucaro release certification is implied.

Certification state: `PROVISIONAL`.
