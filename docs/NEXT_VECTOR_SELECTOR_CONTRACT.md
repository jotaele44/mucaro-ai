# Contextual Next-Vector Selector — candidate contract

Status: CANDIDATE / PROVISIONAL; not a new execution authority.
Source: `ChatView` passes the most recent completed run prompt, and that run's `next_vector` proposal if it has one, to `NextVectorPicker`.
Canonical submit: `ChatView.send(prompt, "ask")` → existing `api.sendMessage` → `work_runs`.

## Behavior
1. Offer four deterministic choices after a completed run: continue, gaps, independent verification, alternatives.
2. Radio changes do not submit, change execution mode, approve tools, or mutate the composer draft.
3. Explicit **Start selected vector** submits a visible, previewable prompt via canonical ask-first send.
4. A synchronous in-flight guard prevents fast duplicate clicks from submitting twice from one picker instance; backend idempotency and permission gates remain canonical.
5. Do not render this follow-up selector while a run is active or a queued message exists. Submission errors preserve the chooser and permit a later user retry.
6. Preserve existing Composer draft, conversation ID, queue, event trail, and text input behavior.
7. Every deterministic prompt ends with the reply-format contract (`RELAY_FORMAT_INSTRUCTION`), which asks the model to close its answer with a NEXT VECTOR block. `backend/app/next_vector.py` holds the same sentence; `backend/tests/test_next_vector.py` fails if the two copies drift.

## Model-proposed vector (wired; `docs/VIDEO_VECTOR_RELAY_V1.md`)
- When a run's answer closes with a valid NEXT VECTOR block, `work_runs` appends a `next_vector` event: a proposal record (`schemas/next_vector_proposal.schema.json`) with `authority: "NONE"`, the block text and its SHA-256, the model and run that wrote it, the gate rows as unverified claims, and a `suggestion` of `{id: "proposed", label, prompt}`.
- `relayOptions(request, proposal)` lists that suggestion first, followed by the four deterministic choices, and passes the set through `contextualNextVectorOptions`. It is preselected unless the record lists a review reason (instruction-like text in the vector or in the run's sources, or a truncated answer).
- The picker says who wrote the vector, that it is a proposal, and shows the claimed gates as not verified. **Copy proposed vector** copies the bare block.
- The frontend checks the record again before offering it: `status` must be `PROPOSED`, `authority` must be `NONE`, the prompt must contain the block text, and no line of the prompt may start with `/`. Any failure falls back to the four deterministic choices.
- The service rejects a block that contains a line starting with `/`, control characters (including every character `str.splitlines()` breaks a line at), or anything past its bounds, and reports `WARN_NEXT_VECTOR_NOT_OFFERED` on the run instead of a proposal.
- A block that only repeats the vector its own prompt relayed is an echo and yields no proposal and no warning.
- Starting a proposed vector is the same explicit Start as every other choice. Nothing submits it automatically (execution-entry case N14).

## Dynamic option readiness (caller-supplied suggestions)
- A caller may still provide `suggestions` to `NextVectorPicker` as a 1–6 item plain-data array of `{id,label,prompt}`; it then replaces the proposal path for that render.
- `contextualNextVectorOptions` validates unique machine IDs, nonempty labels/prompts and length limits; invalid or absent suggestions fall back to four deterministic choices.
- Extra metadata, including purported tool names, permissions, automatic approval and auto-execution, is ignored. It cannot carry authority into `work_runs`.
- No network, backend library, agent framework, or model server is introduced.

## Acceptance
- Run `npm run test:next-vectors`, full frontend build, current-head backend CI and rendered Chromium/WebKit QA.
- Run `node qa/rendered/relay_qa.mjs` for the model-proposed path (scripted answerer; desktop and iPhone width).
- Independently test keyboard radio navigation, exact preview, submit-once, rapid double click, errors, composer-draft preservation and queue handling on desktop and phone.
- Capture exact-head and browser receipts. The existing general rendered QA PASS is not in itself proof these **new specific interactions** were exercised.
- The model-proposed path has not been run with a live model. Until it is, treat block adherence as unverified.
- Merge or production promotion only with independent review, exact-head green gates and explicit authorization.
