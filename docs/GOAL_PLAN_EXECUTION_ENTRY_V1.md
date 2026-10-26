# Mucaro Goal/Plan Execution Entry Contract v1

Status: CANDIDATE_NOT_CERTIFIED. Scope: entry semantics and qualification requirements; this document does not claim a runnable implementation, UI proof, or successful CI.

## Source anchor

Initial contract review used GitHub default branch `main` at `7cb23cbba879e7aa6f99f9ce52ada4ad1b29001f` (2026-10-24 acquisition). A first security-fixed reconciliation used `918275ab0d26d6f6e62cbac8b18d2fce87d7d4a5`; a subsequent context-usage reconciliation used `3214540812ceb6eb866f6a725753dc4fe8ad26ee`. The current candidate is reconciled onto canonical main `701f732eb678997099dd6122b8c07bfb688ec61b` after the bounded VCP-026 terminal context-status surface merged, without changing the contract semantics. Existing distinct layers: `backend/app/execution_gate.py` (planning/approval), `backend/app/work_runs.py` (persisted conversational run bus), and `docs/EXECUTION_GATE.md`. Do not equate `work_runs` `plan|ask|auto` with the proposed product `GOAL|PLAN` mode; they are different dimensions. Preserve `work_runs` approval, idempotency, interruption, and queue controls.

## User-visible decision point

| Product mode | Primary | Secondary | Authority | Modification |
| --- | --- | --- | --- | --- |
| GOAL | Proceed | Adjust | Exact current eligible **vector** of approved objective | New vector revision with preserved predecessor |
| PLAN | Execute | Revise | Exact approved **plan revision** or explicitly eligible stage | New candidate plan revision; old approved revision retained |

Locale: English `Proceed / Adjust`, `Execute / Revise`; Spanish `Proceder / Ajustar`, `Ejecutar / Revisar`. Never use `Ajust`.

An entry decision is not a blanket approval for downstream external mutations. Existing tool-specific permission policy remains authoritative. An automation-safe exemption may be used only if individually proven by existing policy, never inferred from this interface.

## Data and invariants

Each decision records immutable `decision_id`, `mode`, `action`, `objective_id`, `vector_id` and `vector_revision` for GOAL or `plan_id` and `plan_revision` for PLAN, `expected_version`, `actor`, `policy_version`, `idempotency_key`, `created_at`, and `source_commit`. Store exact artifact digests and authorization receipt references; do not mint identities from display names or titles. Every mandatory gate record carries `evidence_ref` plus `evidence_sha256`; the entry controller binds those references immutably and fails closed when either is absent or malformed. Whether the referenced evidence itself is authoritative remains the responsibility of the upstream gate producer/governance layer and must not be inferred from the reference alone.

1. `Proceed` checks `VECTOR_READY`, exact vector revision, dependencies, scope, authorization, and no conflicting active execution. Atomically claim the vector and append entry receipt before any action; reject stale revision, duplicate conflicting key, missing source binding, and unauthorized scope.
2. `Adjust` never executes. Create `VECTOR_CANDIDATE` from the current version; validate; require explicit re-authorization when scope, side effects, or policy changes. Prior revision and receipts remain append-only.
3. `Execute` checks `PLAN_APPROVED` with exact immutable revision, dependency DAG validity, permission boundaries, and no conflicting execution. Atomically bind to that revision; stage-specific approvals may still be required.
4. `Revise` never executes and never edits the running/approved version in place. Create `PLAN_CANDIDATE`, validate, then explicitly approve before new execution. Already-running execution remains bound to its prior approved revision or is explicitly interrupted; never silently migrate.
5. `READY` cannot be inferred from a scalar readiness score. All mandatory gates must independently be PASS. `UNKNOWN/OPEN/BLOCKED/FAIL/UNRESOLVED` blocks execution within the applicable required scope.
6. Retry with the same idempotency key and identical request returns the original decision; same key with different payload fails. Serialize claims with compare-and-swap; preserve multi-process safety.
7. User-visible progress uses queued/running/awaiting approval/awaiting input/completed/failed/cancelled/interrupted, mapped from actual work-run receipts. Completion is not certification.
8. The next GOAL vector derives from verified outputs and unresolved gaps; never implicitly authorize subsequent vectors. PLAN advances only per approved dependency order, with guarded stage transitions.
9. All evidence and action records must bind to source artifact version and exact run identity. Downstream cancellation, resumption, rollback, and approval continue to use existing governed runtime.

## UI and accessibility acceptance

Render two actions only at the relevant pre-execution point, with a discernible mode name and summary of the *exact* vector/plan revision. Primary button: GOAL `Proceed`, PLAN `Execute`. Secondary button: GOAL `Adjust`, PLAN `Revise`. Buttons must have programmatic labels, keyboard focus indication, tab order, and explicit loading/disabled state with reason; disabled visual styling alone is insufficient. Confirm irreversible downstream actions through existing tool gate. After run starts replace entry actions with applicable `Inspect`, `Pause/Stop`, `Resume` controls. Test English/Spanish, widths 393/430 and desktop, zoom 200%, keyboard and screen reader announcements. No hidden automatic execution on rendering, state hydration, or locale change.

## Regression denominator (minimum 24; expand from actual implementation)

Positive:
P01 GOAL ready Proceed; P02 GOAL Adjust creates new revision/no execution; P03 PLAN approved Execute exact revision; P04 PLAN Revise creates new candidate/no execution; P05 same-key replay returns same decision; P06 resumed interrupted run binds same revision; P07 successful completed stage retains receipts; P08 localized labels preserve semantics.

Negative/adversarial:
N01 stale vector; N02 stale plan; N03 missing vector ID; N04 missing plan ID; N05 null approval; N06 failed dependency; N07 unresolved risk; N08 unauthorized actor; N09 side-effect action without tool approval; N10 same key/different payload; N11 two competing claimants; N12 modification of approved plan in place; N13 silent migration of running plan; N14 automatic next-vector authorization; N15 UI double-click; N16 restart between claim and execution; N17 corrupted receipt hash; N18 changed source commit; N19 disabled button keyboard activation; N20 locale-switch execution. Total = 8 positive + 20 negative = 28 exact cases (the 24 minimum is superseded by this explicit 28-case denominator).

Each case must have a runnable test and a receipt. Test existence, lint, or static source review alone does not count as execution PASS. Distinguish CI, local, rendered, and device evidence; reconcile exact source SHA.

## Release and certification gates

1. Acquire current `main` before code changes and reconcile this candidate with it; preserve historical artifacts.
2. Implement typed contract and durable migrations, backend transaction transitions, API and UI, without destabilizing `work_runs` existing `plan|ask|auto`.
3. Run existing full regression/typecheck/build plus all 28 entry cases, with positive and adversarial results.
4. Capture independently verified rendered desktop/393/430 receipts, keyboard/accessibility and language checks.
5. Validate rollback/restart, concurrent claims, app authorization and immutable provenance. Record excluded scope and all unresolved conditions.
6. Obtain review approval; land only passing PR; rerun on merge head; confirm deployment where release scope requires it.
7. Freeze hashes of source/test/artifact/receipts and promote reviewed reusable skill/agent with explicit training permissions and an evaluated regression corpus.
8. Issue `MUCARO_EXECUTION_ENTRY_STANDARD_CERTIFIED` only when every applicable gate passes and zero material unresolved residue remains. Until then status is OPEN/BLOCKED/PROVISIONAL as evidenced.

## Governance and reuse

Promote only a verified, review-approved operational pattern. No automatic agent-training from raw user data, secrets or unconsented sources. Mucaro's orchestration is not authority to bypass the owner project, and TheHub cross-repo governance is independent of the individual GOAL/PLAN choice. Audit identity collisions and 1:N relationships rather than name-based joins.
