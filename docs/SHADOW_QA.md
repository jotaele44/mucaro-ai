# MUCARO Shadow QA / Hardening Contract

Status: ACTIVE CANDIDATE
Initial frozen checkpoint: `6c0831b0cf3a52b99998e6433fb4187316aa5534`

## Purpose

The Shadow lane is an independent adversarial verifier that trails primary mucaro-ai development by one frozen checkpoint. It is not a second implementation branch.

Primary development demonstrates that a capability can work. Shadow QA attempts to falsify the scope of that claim, identifies residue, applies only deterministic narrow repairs, and converts confirmed defects into regressions when feasible.

## Immutable target rule

Every Shadow run MUST bind to:
- exact repository
- exact base commit
- exact checkpoint/head commit
- exact changed-file denominator
- exact test/certification commands executed
- exact receipt artifact

Never audit mutable `main` without first resolving and recording a commit SHA.

## Cumulative baseline rule

Automatic main-push Shadow runs do **not** use the immediately previous commit
as their baseline. They resolve the newest successful **main-push** Shadow QA
run whose head is a strict ancestor of the requested checkpoint. Failed,
blocked, or provisional checkpoints therefore cannot silently become a future
baseline and cannot make unresolved changed surfaces disappear from the next
denominator.

The resolver writes a separate machine-readable baseline receipt containing
the selected run ID, run URL, exact base SHA, exact checkpoint SHA, source, and
retrieval time. Resolution fails closed if the GitHub Actions query fails, no
successful ancestor can be established, an identifier is malformed, or the
selected base is not an ancestor of the checkpoint.

A workflow-dispatch operator may supply an explicit base SHA for replay or
adjudication. The override must be a full 40-character SHA, must differ from
the checkpoint, and must be a verified ancestor. An override is recorded as
such and is never presented as automatic latest-success selection.

## Finding classes

- **DEFECT**: clear violation of an existing requirement, invariant, schema, contract, or expected behavior.
- **REGRESSION**: previously demonstrated passing behavior that now fails.
- **GAP**: required capability, state, test, evidence, or proof is absent or incomplete.
- **DEDUST**: low-risk polish defect such as overlap, clipping, stale copy, dead controls, transition roughness, or non-semantic visual residue.

## Certification states

Use only: `PASS | FAIL | OPEN | BLOCKED | PROVISIONAL | AUDIT_ONLY | UNRESOLVED | SUPERSEDED`.

Script success is never equivalent to certification.

## Surface accounting

For every run:

```
CHANGED_SURFACES = VERIFIED_SURFACES + UNRESOLVED_SURFACES
```

No changed surface may disappear from the ledger.

The receipt must preserve:
- changed paths
- adjacent paths selected for verification
- commands and return codes
- confirmed findings
- repaired findings
- unresolved findings
- blocked rendered-interaction work
- exact checkpoint SHA

## Required adversarial vectors

Where relevant to the changed surfaces, Shadow QA MUST attempt to falsify:
- null / empty states
- duplicate input and duplicate callback
- stale version or optimistic-concurrency token
- M:N multiplication and cardinality drift
- race / concurrent mutation
- replay and idempotency
- cancellation
- restart and reconstruction from persisted history
- resume after interruption
- malformed tool call or tool result
- partial / delayed tool result
- orphan state transitions
- persistence loss
- schema drift
- unexpected codes
- UI overflow, overlap, focus, keyboard, touch, and responsive layout residue

A vector may be marked NOT_APPLICABLE only with an explicit reason in the receipt.

## Change-to-adjacency rules

Minimum adjacency expansion:
- `backend/app/**` -> corresponding `backend/tests/**`, schemas, API/CLI consumers, persistence/state boundaries.
- `backend/tests/**` -> implementation under test plus any shared fixture/helper contract.
- `frontend/**` -> API contract, responsive layouts, keyboard/focus/touch behavior, rendered desktop/mobile evidence.
- `schemas/**` -> validators, producers, consumers, fixtures, migrations, compatibility tests.
- `scripts/**` -> bootstrap/release/certification callers, platform variants, failure/restart behavior.
- `.github/workflows/**` -> scripts invoked, artifact/receipt semantics, failure propagation, exact-head binding.
- `docs/**` -> only runtime/code surfaces explicitly claimed by the changed documentation; documentation-only edits remain AUDIT_ONLY unless they change an operational contract.

## Repair authority

Shadow QA MAY automatically patch only when:
1. the expected behavior is unambiguous,
2. the repair is narrow,
3. no architecture/schema/public contract is redefined,
4. a regression test can be added or an existing invariant directly proves the fix.

Architecture, schema, product-contract, or ambiguous behavior changes MUST return upstream as GAP/OPEN or BLOCKED.

## Two-pass execution

### Pass A — structural / semantic
Run exact-head tests and certification plus change-aware adversarial checks across state, persistence, cardinality, replay, failure, malformed input, and adjacent consumers.

### Pass B — rendered / interaction dedust
When a runnable UI is available, inspect desktop and phone widths with real interaction. Record overflow, overlap, focus, keyboard, touch, transition, and navigation defects.

If browser/device evidence cannot be executed, classify Pass B as BLOCKED. Never infer rendered correctness from static source inspection alone.

## Defect closure

For confirmed findings:

```
CONFIRMED = FIXED + OPEN + BLOCKED + INTENTIONALLY_DEFERRED
```

Arithmetic must close.

Every deterministic defect repair SHOULD add a permanent regression gate. If that is not feasible, the receipt must explain why.

## Receipt identity

The canonical Shadow receipt is JSON and must contain:
- schema version
- repository
- base SHA
- checkpoint SHA
- timestamp UTC
- changed and adjacent path arrays
- gate results
- finding ledger
- surface arithmetic
- defect arithmetic
- rendered pass state
- overall state

The receipt certifies only the declared checkpoint and declared vectors. It never claims universal defect freedom.

## Promotion rule

A primary checkpoint is Shadow-closed only when:
- exact checkpoint identity is frozen,
- all changed surfaces are VERIFIED or explicitly UNRESOLVED,
- defect arithmetic closes,
- all executed gates pass,
- no confirmed unclassified residue remains,
- rendered verification is PASS or explicitly BLOCKED with preserved reason.

## Training gate 🚩

Do not promote this workflow into a reusable skill/agent after a single successful run. Train it only after multiple checkpoint -> audit -> repair -> rerun cycles reproduce the same contract and receipt semantics without hidden manual assumptions.


## Checkpoint-specific adversarial evidence

Generic release or pytest success never implies that every adversarial vector passed.
A real application checkpoint may close a vector only through a
`mucaro-shadow-vector-evidence/1.0` manifest bound to the exact `base_sha`
and `checkpoint_sha`.

The manifest contains exactly one row for every required vector. Allowed terminal
states are `PASS`, `NOT_APPLICABLE`, `BLOCKED`, and `UNRESOLVED`.

- `PASS` requires at least one exact-checkpoint execution receipt plus any
  supporting checkpoint file/test evidence. The execution receipt must itself bind
  the checkpoint SHA and report PASS. Test presence alone never proves execution.
  Referenced checkpoint paths must exist at the checkpoint; test evidence must name
  an exact test definition present in that checkpoint file.
- `NOT_APPLICABLE` requires an explicit scope rationale and carries no positive
  evidence. Vocabulary absence, nearest-name matching, or generic test success is
  not a valid rationale.
- `BLOCKED` and `UNRESOLVED` require explicit reasons and remain non-green.
- Duplicate vector dispositions, duplicate evidence IDs, unknown vectors, missing
  vectors, malformed SHAs, stale base/head bindings, PASS without evidence, and
  evidence absent from the checkpoint fail closed.

Historical replay is evidence adjudication, not execution equivalence. The verifier
may run from a later tooling checkout against an older exact checkpoint, but that
mode records its execution gate as BLOCKED and must not claim the older code was
rerun. A later exact-checkpoint execution can supersede that blocked execution
observation.

Checkpoint manifests live under `_architecture/shadow/evidence/<checkpoint_sha>.json`.
They are append-only provenance records; a later correction supersedes rather than
silently rewrites an earlier receipt.
