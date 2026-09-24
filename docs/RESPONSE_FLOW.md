# Response flow: typed envelope and pending-interaction ledger

Status: IMPLEMENTED (backend primitives only) / UI INTEGRATION OPEN.

This document tracks the implementation named in the Mucaro AI mobile
interaction-extraction brief. At the brief's inspected baseline commit
(`a3be73c00535ebb211776e7c97812ff5423cd2a9`), `backend/app/response_flow.py`
and this file both returned 404 — no prior "response flow" design was ever
merged, so nothing here supersedes earlier work; it is new.

## What this covers

`backend/app/response_flow.py` implements two things, deliberately kept
separate per the brief's implementation order (step 1):

1. **Typed response envelope** — a read-only UI projection of a
   conversation turn into ordered parts: `text`, `inert_code`, `question`,
   `tool_call`, `tool_result`, `artifact`, `task_receipt`. Building an
   envelope (`envelope_for_conversation`) never mutates the stored turn or
   its `answer_trace`; the answer trace stays the canonical evidence
   record (`backend/app/conversations.py`), and the envelope is a
   derived rendering surface. `validate_response_envelope` rejects unknown
   part kinds and parts missing required fields.

2. **Pending-interaction (question) state machine** — server-owned state
   for "the assistant is waiting on a choice", persisted in a new SQLite
   table (`interactions`, see `backend/app/db.py`). States are
   `PENDING → RESOLVED | DISMISSED`. Enforced invariants:
   - At most one `PENDING` interaction per conversation
     (`ERROR_INTERACTION_ALREADY_PENDING`); creating a new one while a
     prior interaction is pending requires the caller to pass
     `dismiss_pending=True`, which supersedes the old interaction with an
     explicit `SUPERSEDED_BY_NEW_INTERACTION` resolution rather than
     silently discarding it.
   - Resolution is always by `option_id`, never by label text
     (`ERROR_INTERACTION_OPTION_UNKNOWN` if the id isn't one of the
     interaction's own options).
   - Optimistic concurrency: every mutation carries `expected_version`.
     A stale submission (built against a version the store has since
     moved past) is rejected as `ERROR_INTERACTION_VERSION_MISMATCH`,
     distinct from `ERROR_INTERACTION_NOT_PENDING` (a fresh, correct
     resubmission against an interaction that has already left
     `PENDING` — e.g. a genuine double-click race).
   - Freeform responses are only accepted when the interaction allows
     them (`ERROR_INTERACTION_FREEFORM_NOT_ALLOWED` otherwise), and a
     submission must carry exactly one of `option_id` or `freeform_text`
     (`ERROR_INTERACTION_AMBIGUOUS_RESPONSE` / `ERROR_INTERACTION_EMPTY_RESPONSE`).
   - Dismissal records a required, non-empty reason and is a distinct
     terminal state from resolution — it means the question was
     abandoned (e.g. on interruption by an unrelated new message), not
     answered. `get_pending_interaction` is the resumption path: it
     returns the same interaction record (same id, same version) rather
     than a rewritten one, so resuming after an interruption never
     silently mutates what was asked.

## API surface (`backend/app/main.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/conversations/{id}/envelope` | Typed envelope for every turn, plus the pending interaction if any. |
| GET | `/conversations/{id}/interactions` | Full interaction history for the conversation. |
| GET | `/conversations/{id}/interactions/pending` | The single pending interaction, or `INTERACTION_NONE_PENDING`. |
| POST | `/conversations/{id}/interactions` | Create a pending multiple-choice interaction. |
| POST | `/interactions/{id}/resolve` | Resolve by `option_id` or `freeform_text`, with `expected_version`. |
| POST | `/interactions/{id}/dismiss` | Dismiss with a required `reason` and `expected_version`. |

Tests: `backend/tests/test_response_flow.py` (21 cases) cover the envelope
builder/validator, the one-pending-max invariant, resolve-by-id, stale vs.
duplicate submission rejection, freeform gating, dismiss/resume, and an
interruption scenario modeled directly on the brief's 12–44s window (a
question is shown, an unrelated message interrupts it, and resuming must
surface the same interaction rather than a rewritten one).

## Feature-row mapping and what remains open

Against the brief's F01–F20 denominator, this change gives backend,
API-level support to:

- **F10** (pending multiple-choice question with descriptions) — interaction
  records carry `question`, `options[].consequence`.
- **F11** (disabled submit until valid choice; stale/duplicate rejection) —
  enforced server-side via `expected_version` and option-id validation;
  the client-side "disable submit until valid" affordance is a UI concern
  not addressed here.
- **F12** (freeform override) — `allow_freeform` + `freeform_text`
  resolution path.
- **F13** (question/choice receipt in transcript) — `resolution` is
  retained on the interaction record after resolve/dismiss; not yet
  spliced back into the conversation's turn list as a rendered receipt.
- **F14** (interruption and return to pending question) — `dismiss_pending`
  for explicit supersession, `get_pending_interaction` for resumption.

**Explicitly not done in this change** (still `DISPLAY_ONLY` / `OPEN` per
the brief's own denominator, unless noted):

- No mobile UI/rendering. There is no iPhone-width client for any of
  this; F01, F05, F06, F17, F20, and the "rendered QA" step in the brief's
  implementation order are untouched.
- No queued-message-while-a-turn-is-active semantics (F04) — this module
  only manages one pending *question*, not a message queue.
- No tool-call/tool-result parts are populated from `backend/app/tools.py`
  into an envelope yet (`make_tool_call_part` / `make_tool_result_part`
  exist as builders but nothing calls them); F07–F09, F16 keep using the
  existing `/tools/*` endpoints directly.
- No background-task persistence or drawer (F18), no GitHub PR artifact
  linking beyond the existing `backend/app/artifact_identity.py` lineage
  primitives (F19), no per-tool permission sheet wiring into this
  envelope (F07 stays on the existing `tools.py` flow, which already had
  its own approve/deny/execute idempotency).

Completion criterion from the brief (20/20 rows IMPLEMENTED_AND_VERIFIED)
is **not** met by this change. This change is scoped to the brief's
implementation-order steps 1 and 3 (typed envelope; pending-question state
machine) plus the versioned-ledger part of step 2, as a backend
foundation — not a claim of mobile parity.
