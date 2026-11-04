# Research stage 2 — bounded development integration

Base main: `4ca5106e5f008cc7f0fe7cc1827332e669fb4fdd`.
Stage 1: `1e6a8f89c21ee995f06d24be4ec6ab4436cf586a`, preserved and cherry-picked
onto an isolated development branch. No main merge or production activation.

## Implemented

The research service persists a frozen plan and source packet through the existing
agent checkpoint engine. It validates a bounded question dependency graph and
source/claim bindings before starting, then performs claim inventory, evidence-bound
question synthesis and deterministic report assembly. Model calls use the existing
routing policy and provider implementation with no implicit fallback. Existing
permissions remain effective; source content cannot add tasks or authorize calls.

Run state is resumable. Successful steps are reused by idempotency key, result
hashes are verified, and source bytes are revalidated on resume. A source-code
commit change is rejected. An uncertain in-flight model operation is retained for
explicit reconciliation, never automatically retried. Completion means pipeline
completion only. Contradictions remain unresolved and block checkpoint completion;
a bounded report may still describe them. Declared FACT/BINDING classes are input
assertions, not accepted truth or canonical identities.

Limits: 20 questions, explicit model-call count, input character budget and an
absolute deadline checked between calls. Provider transport timeouts still apply;
the deadline does not kill an in-flight request. Input characters are not billed
tokens, and model-call limits are not dollar caps. New adjudication or changed
budgets require a separately preserved successor run. The development CLI requires
a clean commit and separate runtime directory. No new runtime dependency.

## Benchmark freeze and unblinding

Reviewer packets now commit to the private key using the repository's canonical
JSON hash. This prevents system identities being swapped after seeing scores.
`freeze` validates complete attributed paired scores and writes an exclusive seal
over exact reviewer-packet, score and rubric bytes. `compare` requires the recorded
seal hash, verifies all frozen inputs and key/answer bindings, then reports paired
descriptive differences. It cannot establish superiority, reviewer independence,
or a production certificate. Rubric predeclaration is recorded, not independently
time-attested. Preserve the seal hash outside the mutable review directory.

Keep the custodian mapping and original system-attributed outputs separate from
reviewers. Style and citations can still reveal identity; record leakage explicitly.
Scores must come from an actual reviewer. Do not fill empty templates with invented
scores or treat synthetic test scores as empirical results.

## Commands

From a clean checkout, using its prepared interpreter:

`python scripts/research_run.py --workspace /absolute/isolated-runtime start plan.json packet.json --evidence-root /absolute/evidence`

`python scripts/research_run.py --workspace /absolute/isolated-runtime advance CHECKPOINT_ID --steps 10`

`python scripts/benchmark_research_smoke.py /absolute/new-smoke-directory`

`python scripts/research_audit.py freeze reviewer_packet.json scores.json rubric.json --seal review_seal.json`

`python scripts/research_audit.py compare reviewer_packet.json scores.json rubric.json --private-key private_mapping.json --seal review_seal.json --expected-seal-sha256 RECORDED_SHA256`

The smoke command acquires actual outputs from the unchanged direct mock router
and the checkpoint-driven mock router for two synthetic questions. It does not
run ChatGPT or a frontier model, generate reviewer scores, or measure general
research quality. All packet inputs and output provenance are retained.

## Rollback and remaining gates

The test suite rehearses SQLite backup/restore into a separate runtime and confirms
replayed report identity. This proves bounded local-data restoration, not production
deployment rollback. Baseline main and Stage 1 remain preserved.

OPEN: adaptive web-discovery planning, acquisition scheduling, dynamic replanning,
work_runs/UI research activation, GIS-specific task adapters, semantic entailment,
authoritative identity adjudication, independent reviewer verification, real model
bakeoffs, ChatGPT product comparison, native macOS, full release/security checks,
production consumer integration and deployment rollback. Existing GIS modules were
not modified or newly certified. These are required future stages, not implied by
the synthesis pipeline's tests.

No paid frontier calls were made by this task. At execution setup, live mode was
off, no Anthropic API key was present and no local endpoint was explicitly set.
Benchmark quality comparisons and score freezing remain blocked until actual
comparator outputs and independently attributed scores are available.
