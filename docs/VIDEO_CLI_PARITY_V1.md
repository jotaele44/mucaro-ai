# VIDEO_CLI_PARITY_V1

Status: FROZEN BOUNDED CAPABILITY DENOMINATOR  
Frozen: 2026-10-02  
Baseline main SHA: `7f104bf9114c85df92c24eeaecaf2260465b4568`

## Goal

`VIDEO_CLI_PARITY_V1` is the bounded implementation and certification target derived from the user-supplied 36.866667-second terminal-agent recording. It exists to prevent visual imitation, feature-name matching, or documentation claims from being mistaken for functional equivalence.

The source recording is frozen by SHA-256:

`6d7fcce3581fd2e3c06b30b5f671c40f9f9b5654ab9251df575b70f4e69f4d0f`

Source manifestation metadata: H.264, 606x512, 30 fps, duration 36.866667 seconds.

## Scope rule

This denominator covers capabilities observably demonstrated in that recording. It does **not** claim to enumerate or reproduce undisclosed capabilities of the source product.

A source-product feature name, a visually similar control, or a deterministic result is not identity evidence. Each Mucaro capability must be bound to its own implementation and execution evidence.

## Binding invariants

1. The video denominator is immutable after freeze. New observations require a new version.
2. Source video identity is byte-bound by SHA-256; regenerated or transcoded video cannot prove prior byte identity.
3. Every required capability maps to one or more MUCARO_FLAGPOLE_V1 domains.
4. UI presence never proves backend capability.
5. Backend tool presence never proves executed capability.
6. Planning never implies execution authorization.
7. A local PR request artifact is not an authenticated remote PR action.
8. A patch transaction is not a complete branch/commit lifecycle.
9. A successful command is not a regression or certification PASS.
10. Estimated context/token usage must not be represented as provider-measured usage.
11. Existing passed Mucaro artifacts are reused unless a concrete falsifier reopens them.
12. Certification binds to one exact Mucaro source SHA and the frozen source-video manifestation.
13. No aggregate readiness percentage can hide required OPEN, FAIL, BLOCKED, UNVERIFIED, PARTIAL, ABSENT, or unresolved residue.

## Machine-readable artifacts

- `_architecture/video_cli_parity_v1.json` — frozen 27-capability denominator.
- `schemas/video_cli_parity.schema.json` — denominator schema.
- `_architecture/video_cli_parity_gap_matrix_v1.json` — baseline implementation-state audit.
- `backend/app/video_cli_parity.py` — fail-closed denominator/gap validator and report builder.
- `backend/tests/test_video_cli_parity.py` — positive and negative denominator regression gates.

## Baseline state

At the frozen baseline:

| State | Count |
|---|---:|
| IMPLEMENTED | 8 |
| PARTIAL | 11 |
| ABSENT | 7 |
| UNVERIFIED | 1 |
| **TOTAL** | **27** |

All capability certification rows remain `OPEN` until parity-specific exact-head execution evidence exists.

## Dependency order

The implementation vector is deliberately backend-first:

1. Governed command execution.
2. Governed regression execution and CI-state inspection.
3. Guarded Git branch/commit lifecycle.
4. Authenticated GitHub issues/PR/gist provider actions.
5. Canonical Todo/dependency DAG.
6. V08 Autopilot orchestration, budgets, loop detection, and stopping conditions.
7. Run-scoped permission leases.
8. Persistent terminal TUI over canonical backend state.
9. Current / Sessions / Issues / Pull Requests / Gists projections.
10. Model/mode/context instrumentation.
11. Cross-platform terminal regression corpus.
12. Exact-head parity certification.
13. V23 flagpole torture-test closure.

## Closure rule

`VIDEO_CLI_PARITY_V1` is **PASS** only when every one of the 27 required rows is:

`IMPLEMENTED + certification PASS`

for the same exact Mucaro source SHA, with positive and negative execution evidence and zero unresolved residue inside this declared scope.

Until then the correct aggregate state is `VIDEO_CLI_PARITY_OPEN` or `VIDEO_CLI_PARITY_INVALID`, never an inferred percentage-based PASS.
