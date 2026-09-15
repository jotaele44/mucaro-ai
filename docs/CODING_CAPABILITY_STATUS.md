# Coding capability status

This document records the current source-level implementation boundary without changing the frozen Mucaro V1 denominator.

## Source state

- Repository discovery, bounded reads, diffs, and patch proposal validation are merged and source-backed.
- The coding container boundary is plan-only: pinned image identity, read-only repository mount, read-only root filesystem, dropped capabilities, no-new-privileges, network none, resource ceilings, exact HEAD and dirty-state binding. It does not launch arbitrary repository commands.
- Patch mutation is limited to an approved Git patch transaction. It revalidates exact HEAD, patch SHA-256, candidate path set, preimages, and dirty overlap; HEAD is conserved; unexpected dirty expansion triggers explicit rollback handling.
- Test/lint/type/build/security/benchmark checks currently produce deterministic read-only execution plans only. `PLANNED` is not `PASS`.
- Code-review findings are bound to changed/untracked paths, current source SHA-256, and exact line evidence. No findings is not proof that no defects exist.

## Certification boundary

The coding capability is a **CANDIDATE / PROVISIONAL** implementation, not certified completion.

The following remain material residue:

1. Live pinned coding-container image and engine execution evidence.
2. Executed test/lint/type/build receipts from the isolated boundary.
3. Governed branch/commit/PR mutation lifecycle.
4. Hosted CI admission to executable jobs, or an independently frozen trusted execution surface.
5. Full coding certification corpus execution with zero unresolved residue.

`backend/app/capability_contracts.py` may still contain the historical `ABSENT` implementation label until that contract artifact is explicitly adjudicated. That label is now stale relative to merged source and must not override the concrete source evidence above; it is tracked as a NAME/CLASS contradiction to close in a dedicated contract update rather than silently rewriting history.
