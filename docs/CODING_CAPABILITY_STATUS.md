# Coding capability status

This document records the current source-level implementation boundary without changing the frozen Mucaro V1 denominator.

## Source state

- Repository discovery, bounded reads, diffs, and patch proposal validation are merged and source-backed.
- The coding container boundary now has an executable adapter: immutable pinned image identity, `--pull=never`, read-only host source mount, ephemeral writable tmpfs workspace, read-only root filesystem, dropped capabilities, no-new-privileges, network none, IPC none, fixed non-root UID/GID, bounded resources/output, and exact pre/post HEAD + dirty-state validation.
- Patch mutation is limited to an approved Git patch transaction. It revalidates exact HEAD, patch SHA-256, candidate path set, preimages, and dirty overlap; HEAD is conserved; unexpected dirty expansion triggers explicit rollback handling.
- Test/lint/type/build/security/benchmark checks now have source-level terminal `PASS | FAIL | BLOCKED` execution accounting backed by coding-container receipts. A nonzero command is `FAIL`; engine/image/isolation failure is `BLOCKED`. Source presence alone is not execution evidence.
- Code-review findings are bound to changed/untracked paths, current source SHA-256, and exact line evidence. No findings is not proof that no defects exist.
- Governed local Git lifecycle source is implemented: clean exact-HEAD work-branch creation; commit transaction requiring a non-protected branch, exact approved dirty set, no pre-existing staged state, exact parent, exact committed path set, and rollback on failed commit/postconditions; a PR request artifact binds head/base/title/body without performing a remote network mutation.

## Certification boundary

The coding capability is a **CANDIDATE / PROVISIONAL** implementation, not certified completion.

The following remain material residue:

1. Live execution with a frozen pinned coding image and a trustworthy Docker/Podman engine, including preserved receipts for representative positive and negative cases.
2. Executed test/lint/type/build/security/benchmark receipts from that live isolated boundary.
3. Executed regression evidence for the new branch/commit rollback lifecycle.
4. Authenticated remote push/PR submission tied exactly to the preserved PR request artifact; credentials remain a final-stage capability.
5. Hosted CI admission to executable jobs, or an independently frozen trusted execution surface.
6. Full coding certification corpus execution with zero unresolved residue.

`backend/app/capability_contracts.py` may still contain the historical `ABSENT` implementation label until that contract artifact is explicitly adjudicated. That label is stale relative to merged source and must not override the concrete source evidence above; it remains a NAME/CLASS contradiction to close explicitly rather than silently rewriting history.
