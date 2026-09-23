# Response flow v1

The browser is the first channel. The existing `/conversations/{id}/ask` route
retains its answer trace response and stores a channel-neutral `response_envelope`
on each new assistant turn. Older turns without an envelope still render normally.
The SQLite conversation payload is the session store; no separate in-memory state
is authoritative. `/ask` and CLI answer behavior remain legacy paths.

Stages: load conversation → resolve a pending interaction → generate the cited
answer with the existing retrieval pipeline → split text/code parts and attach
at most one explicitly supplied interaction → persist envelope → render web
parts. Render reads stored parts and never calls the model again. Code is
displayed and copied, never run. `executable` and `run_button` remain false.

An authenticated producer can supply `response_interaction` in a conversation
ask body: `{ "type": "MULTIPLE_CHOICE", "prompt": "...", "options":
[{"id":"a","label":"A"},{"id":"b","label":"B"}] }` or
`{ "type": "CLARIFICATION_REQUEST", "prompt": "..." }`.
This is an explicit producer contract, not an automatic confidence detector.
The current answer generator does not emit structured uncertainty signals.
The next request resolves with a valid `choice_id` and `interaction_id`, free
text, or `dismiss_pending: true` for a new topic. Invalid or stale IDs fail.
Typing an unrelated message without the dismissal flag counts as a freeform
answer to the pending prompt. Browser users have a **New topic** action.

Limits: concurrent requests still use the existing whole-conversation JSON
upsert and may race; requests do not yet have idempotency keys. This is a
scoped first adapter, not a claim of fully restartable stages or production
session certification. The next backend increment should add transactional
per-session compare-and-swap or event rows, request idempotency, structured
producer output, and replay tests across process restarts.
