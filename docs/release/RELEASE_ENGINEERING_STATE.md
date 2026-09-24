# Release engineering state

This page explains the release control plane that implements the
[release engineering contract](MUCARO_RELEASE_ENGINEERING_CONTRACT_V1.md) and
the [mobile interaction brief](MOBILE_INTERACTION_EXTRACTION_V1.md). The
machine-readable files are authoritative; this page does not record states.

## Files

| Contract term | File |
|---|---|
| `RELEASE_SCOPE_MANIFEST` (KERNEL-002) | `_architecture/release/release_scope_manifest_v1.json` |
| `CURRENT_STATE_SNAPSHOT` (KERNEL-004) | `_architecture/release/current_state_snapshot_v1.json` |
| Dependency DAG (KERNEL-006) | `_architecture/release/release_dag_v1.json` |
| Acceptance ledger (MATRIX-002) | `_architecture/release/acceptance_ledger_v1.json` |
| Blocker ledger (KERNEL-020) | `_architecture/release/blocker_ledger_v1.json` |
| Contradiction ledger (KERNEL-014) | `_architecture/release/contradiction_ledger_v1.json` |
| Scope-delta ledger (KERNEL-019) | `_architecture/release/scope_delta_ledger_v1.json` |
| Frozen checkpoints | `_architecture/release/frozen/<UTC label>/` |

Schemas live in `schemas/release_*.schema.json`; the validator is
`backend/app/release_scope.py`.

## Commands

```bash
mucaro release scope validate --format text   # exit 1 on any ledger error
mucaro release scope status --format text     # terminal-certification verdict + residue
mucaro release scope snapshot --pytest-summary "N passed" --frontend-build pass
mucaro release scope freeze 20260924T210000Z-label
```

## What the validator enforces

- **Re-enumeration.** Every bound specification is re-read from its bytes. A
  changed hash is reported as `ERROR_SPEC_HASH_MISMATCH`. An enumerated ID that
  is missing from the manifest is reported as `ERROR_SILENT_SCOPE_LOSS`.
- **Classification.** Every requirement is classified exactly once as
  `IN_SCOPE`, `NOT_APPLICABLE` (which needs a justification and evidence) or
  `EXTERNALLY_BLOCKED` (which needs a real blocker).
- **Acceptance ledger rows.** There is exactly one row per requirement, and it
  must match the manifest.
- **What `PASS` requires.** A positive gate, a negative gate and evidence
  references, and every one of them must exist. A `path::test_name` reference
  must name a real test function.
- **`BLOCKED` references.** A `BLOCKED` state must point to an active blocker.
- **Dependency DAG.** The DAG must be acyclic and must reference known
  requirements and blockers.
- **Scope deltas.** A `DEFECT_REVEALED` or `REQUIRED_BY_EXISTING_SCOPE` delta
  must enter the denominator. An `ENHANCEMENT` must stay out unless it is adopted
  with a named authority.
- **Frozen checkpoints.** Compared with the latest frozen checkpoint, no
  requirement, contradiction, blocker or scope delta may disappear. A
  contradiction that was frozen as material cannot later be reclassified as
  non-material.
- **Denominator arithmetic.** `D = PASS + FAIL + OPEN + BLOCKED + PROVISIONAL +
  NOT_APPLICABLE` must hold.
- **Terminal certification rule.** The verdict stays `PROVISIONAL` unless every
  condition holds, including a bound specification, zero open or provisional
  in-scope requirements, zero blocked gates and zero material unresolved
  contradictions.

## Recorded assumptions

These are all reversible. They are recorded as ASSUMPTION rather than FACT.

- **A1: Specification binding.** The contract and the mobile brief are bound
  by SHA-256. The separate "Release-Grade Workbench Specification" was not found
  in the repository or in the connected Google Drive, so
  `SPECIFICATION_BINDING = OPEN` (blocker `BLK-SPEC-001`). Requirements it adds
  later enter through a scope delta.
- **A2: Desktop shell.** A native window (pywebview/WKWebView) hosts the
  built UI. The backend runs in-process on a loopback ephemeral port. The
  Python runtime is embedded, and app data lives outside the bundle.
  - *Refinement (M7):* the app is frozen with PyInstaller 6.22.3 (onedir
    `.app` with a native Mach-O launcher, so the Dock and Finder see a real
    application) instead of a shell-script wrapper around a downloaded Python.
    Inputs are hash-locked in `packaging/macos/requirements-macos-arm64.lock`.
  - The per-launch token reaches the window in the URL fragment, which is
    never sent to the server, and the GUI removes it from the address at once.
  - Data lives in `~/Library/Application Support/Mucaro AI`. One instance runs
    per data directory, and `instance.json` (mode 0600) tells local tooling
    where the service is.
  - pywebview's JavaScript bridge needs `eval`, which the app's CSP forbids.
    So the shell proves that the GUI is running from the first authorized API
    call carrying `X-Mucaro-Client: mucaro-gui`. It does not relax the CSP.
- **A3: Local AI.** Local AI uses an OpenAI-compatible loopback provider
  (Ollama, the llama.cpp server or LM Studio), and model weights are not
  bundled. The declared hardware baseline is Apple Silicon (arm64), 16 GB RAM,
  10 GB free storage and macOS 14 or later (raised from 13 by RC-008).
- **A4: Streaming.** Results stream over server-sent events.
- **A5: Mobile rows.** Mobile rows F01–F20 are satisfied by a responsive UI
  verified at 390×844. A native iOS client is an unadopted enhancement
  (`SD-010`).
- **A6: Launch evidence.** Launch evidence comes from LaunchServices (`open`)
  on hosted macOS runners. A literal physical double-click stays blocked on the
  user (`BLK-PHYS-001`).
- **Release class.** The release class is direct distribution with a
  Developer ID. The Mac App Store lane is an unadopted enhancement (`SD-009`).
