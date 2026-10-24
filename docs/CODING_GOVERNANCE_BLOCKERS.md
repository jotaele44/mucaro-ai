# Coding governance blocker ledger

This ledger preserves unresolved residue for the current governed coding increment.

| ID | State | Classification | Blocker |
|---|---|---|---|
| CODE-B01 | BLOCKED | EXECUTION | No certified live pinned coding image/engine execution evidence. |
| CODE-B02 | BLOCKED | EXECUTION | The approval-gated check-suite boundary is implemented, but trusted pinned-runtime execution receipts have not yet been produced. |
| CODE-B03 | OPEN | GOVERNANCE | Branch, commit, and pull-request mutation lifecycle is not implemented as a governed coding action plane. |
| CODE-B04 | BLOCKED | INFRASTRUCTURE | Hosted GitHub Actions has repeatedly failed before jobs; zero-job startup failure is not code-test FAIL evidence. |
| CODE-B05 | SUPERSEDED | CLASS | The stale `ABSENT` label was adjudicated to `CANDIDATE`; the coding capability remains open for live runtime and certification evidence. |
| CODE-B06 | OPEN | CERTIFICATION | Coding regression corpus has not been executed to zero unresolved residue on a trusted execution surface. |

No blocker above may be converted to PASS from source presence, mergeability, deterministic planning, or absence of observed failures alone.
