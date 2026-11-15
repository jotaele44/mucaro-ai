# Contextual Next-Vector Selector — candidate contract

Status: CANDIDATE / PROVISIONAL; not a new execution authority.
Source: `ChatView` passes the most recent completed run prompt to `NextVectorPicker`.
Canonical submit: `ChatView.send(prompt, "ask")` → existing `api.sendMessage` → `work_runs`.

## Behavior
1. Offer four deterministic choices after a completed run: continue, gaps, independent verification, alternatives.
2. Radio changes do not submit, change execution mode, approve tools, or mutate the composer draft.
3. Explicit **Start selected vector** submits a visible, previewable prompt via canonical ask-first send.
4. A synchronous in-flight guard prevents fast duplicate clicks from submitting twice from one picker instance; backend idempotency and permission gates remain canonical.
5. Do not render this follow-up selector while a run is active or a queued message exists. Submission errors preserve the chooser and permit a later user retry.
6. Preserve existing Composer draft, conversation ID, queue, event trail, and text input behavior.

## Dynamic option readiness (not yet wired to any model)
- A future caller may provide `suggestions` to `NextVectorPicker` as a 1–6 item plain-data array of `{id,label,prompt}`.
- `contextualNextVectorOptions` validates unique machine IDs, nonempty labels/prompts and length limits; invalid or absent suggestions fall back to four deterministic choices.
- Extra metadata, including purported tool names, permissions, automatic approval and auto-execution, is ignored. It cannot carry authority into `work_runs`.
- Any model-generated suggestions must be tagged with provider/source identity and assessed for prompt-injection risk by the future integration. Provider provenance is **not** implemented by this PR.
- No network, backend library, agent framework, or model server is introduced.

## Acceptance
- Run `npm run test:next-vectors`, full frontend build, current-head backend CI and rendered Chromium/WebKit QA.
- Independently test keyboard radio navigation, exact preview, submit-once, rapid double click, errors, composer-draft preservation and queue handling on desktop and phone.
- Capture exact-head and browser receipts. The existing general rendered QA PASS is not in itself proof these **new specific interactions** were exercised.
- Merge or production promotion only with independent review, exact-head green gates and explicit authorization.
