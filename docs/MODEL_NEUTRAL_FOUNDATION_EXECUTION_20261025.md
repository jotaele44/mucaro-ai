# Múcaro model-neutral foundation — development gate (2026-10-25)

Status: AUDIT_ONLY. Not a production release or certification.

## Immutable starting reference
Repository: jotaele44/mucaro-ai
Base commit: 7cb23cbba879e7aa6f99f9ce52ada4ad1b29001f
Development branch: dev/model-neutral-foundation-20261025
Existing MUCARO_FLAGPOLE_V1 V00–V23 remains authoritative; this document is a supplemental execution contract, not a replacement.

## Scope and preservation
Preserve existing retrieval, SQLite state, checkpoint schemas, permission engine, mock/Anthropic routing, GIS work, GUI, certification and prior evidence. Never interpret prior test receipts as exact-head evidence without commit binding. No production deployment, federation activation, or skill promotion from this branch.

## Execution order
1. Inventory all V00–V23 domains at the exact base SHA, mapping implementation, tests, provenance, dependencies and OPEN/BLOCKED residue.
2. Reconcile CODE-B01–B06 against current code and exact-head CI. Historical blocker ledgers are evidence of historical state, not current proof.
3. Specify provider-neutral request/response/event contracts: model identity, capabilities, tool-call arguments and results, token limits, streaming, cancellation, structured output, errors, timeout and retry semantics.
4. Implement missing coding-agent read/patch/test/review actions with sandbox, least-privilege permissions, transactional patching, Git checkpoints, idempotency and auditable receipts. All writes remain confined to isolated development.
5. Execute deterministic mock-provider tests: malformed JSON/tool calls, tool denial, timeout, interruption/restart, duplicate calls, conflicting writes, context overflow, model failure, cancellation, replay and rollback.
6. Run a bounded real local-model compatibility spike in an isolated environment; never make external inference or a cloud key a prerequisite for offline baseline behavior.
7. Verify with independent runner and exact commit: unit/integration/security/GUI/platform checks; freeze manifest with raw inputs, hashes, output hashes, test results, platform and versions.
8. Rehearse rollback. Certify only if every applicable required domain passes and material residue is zero. Otherwise issue machine-readable BLOCKED/PROVISIONAL result.
9. Only after exact-release certification and explicit authorization, integrate production local-model adapters, benchmark end-to-end, deploy with restoration safeguards and promote proven reusable agents.

## Release invariants
- A model adapter never grants tool authorization.
- Planning is not execution approval.
- Model/provider failure cannot erase canonical state.
- Offline operation does not silently call cloud endpoints.
- Git/file mutations require explicit scoped authority and diff receipts.
- Every accepted artifact binds to input SHA, output SHA, model identity, tool version and runtime identity.
- Desktop PASS does not imply iOS PASS.
- Test PASS does not imply evidence or production certification.
- No promotion on unresolved required-domain residue.

## Required evidence outputs (planned, not yet produced)
foundation_inventory.json; coding_blocker_reconciliation.json; model_adapter_contract.json; test_matrix.json; security_receipts.json; rollback_receipt.json; release_manifest.json; certification.json; TERMINAL_READY.txt.

## Current evidence classification
Repository discovery: PASS.
Exact-base source sampling: PASS.
Full inventory: OPEN.
Implementation: UNATTEMPTED.
Mock tests: UNATTEMPTED.
Real local model: UNATTEMPTED.
Independent verification: OPEN.
Production certification: BLOCKED.
