# VIDEO_VECTOR_RELAY_V1

Status: FROZEN BOUNDED CAPABILITY DENOMINATOR / IMPLEMENTATION CANDIDATE, NOT CERTIFIED  
Frozen: 2026-11-16  
Baseline main SHA: `301297065526047fde7ae2b9858e687631a3f8f2`

## Goal

`VIDEO_VECTOR_RELAY_V1` is the bounded implementation and certification target derived from a user-supplied 26.775-second phone recording of a relay across five conversations in another product. Each finished answer closes with a block that names the next bounded operation, and the operator sends that block back as the next message, then moves to the next conversation while the first one works.

The source recording is frozen by SHA-256:

`0e47d28633b8615b8b1bba657ac4004f8e308488ea03a4e72b761197f6036058`

Source manifestation metadata: HEVC, 1170x2390, variable frame rate (average 4120/51 fps), duration 26.775 seconds, audio track silent.

## Scope rule

This denominator covers capabilities observably demonstrated in that recording. It does **not** claim to enumerate or reproduce undisclosed capabilities of the source product, including whatever standing instructions made its answers close with the block.

The recording shows the operator's conversation list and live investigation text. None of it is recorded here: the denominator describes interface behavior only, and the regression fixtures use synthetic blocks of the same shape.

## The block

```text
NEXT VECTOR:
<anchor>
→ <step>
→ <step>

CROSS ALL T'S:
<verification chain>
→ <check>

MAXIMIZE YIELD:
<reuse anchor>
→ <constraint>

FINAL GATE:
<gate name> = <claimed state>
```

`NEXT VECTOR` opens the block and needs an anchor or a step. The other sections are optional, headers are upper case, a step starts with an arrow, and `FINAL GATE` / `FINAL RELEASE` rows are either `NAME = STATE` or a bare token such as `PRODUCTION_DEPLOYMENT_WITHHELD`.

## Binding invariants

1. The denominator is immutable after freeze. New observations require a new version.
2. Source video identity is byte-bound by SHA-256; a transcoded copy cannot prove prior byte identity.
3. Every required capability maps to one or more MUCARO_FLAGPOLE_V1 domains.
4. A proposed vector is data. It never submits a message, approves a tool call, or changes a gate. Starting it is one explicit operator action through the ask-first `work_runs` path (`docs/GOAL_PLAN_EXECUTION_ENTRY_V1.md`, rule 8 and case N14).
5. A relayed vector is a user message, and `work_runs` reads a user-message line that starts with `/` as a slash command. A block with such a line is rejected whole, and so is a block containing any character `str.splitlines()` breaks a line at other than a newline, so model output cannot create a tool request by being relayed (INV-014).
6. Gate rows in a block are the model's claims. They are stored with `verified: false` and feed no execution gate.
7. Injection-detector verdicts stay advisory: a flagged proposal is shown with its reason and is never the default choice; nothing is executed or hidden because of a verdict.
8. A block that only repeats the vector its own prompt relayed is an echo, not a next vector, and proposes nothing. The built-in mock answerer quotes its query, so this is what keeps it from "proposing" what it was just sent.
9. A scripted or mock answer that contains a block is fixture text. It proves the mechanism, not that any model writes the block.
10. UI presence never proves backend capability; a successful command is not a certification PASS.
11. Existing passed Mucaro artifacts are reused unless a concrete falsifier reopens them.
12. No aggregate readiness percentage can hide required OPEN, PARTIAL, ABSENT or unresolved residue.

## Machine-readable artifacts

- `_architecture/video_vector_relay_v1.json` — frozen 14-capability denominator.
- `schemas/video_vector_relay.schema.json` — denominator schema.
- `_architecture/video_vector_relay_gap_matrix_v1.json` — per-row state at the baseline and in the tree that contains it.
- `backend/app/video_vector_relay.py` — fail-closed denominator/gap validator and report builder; every evidence entry must exist in the tree.
- `backend/tests/test_video_vector_relay.py` — positive and negative denominator regression gates.
- `schemas/next_vector_proposal.schema.json` — the proposal record.

## What was implemented against the baseline

| Piece | Where |
|---|---|
| Block grammar, bounded parser, proposal record, relay prompt | `backend/app/next_vector.py` |
| `next_vector` run event after an answer that closes with a valid block; a visible warning for a rejected one | `backend/app/work_runs.py` (`_next_vector_events`) |
| Thread status per conversation (working, waiting on the operator, newest result, vector ready) | `backend/app/work_runs.py` (`conversation_activity`), `GET /conversations`, `GET /conversations/activity` |
| Picker offers the proposed vector first, with provenance, claimed gates, exact prompt preview and copy | `frontend/src/nextVectorOptions.js`, `frontend/src/components/NextVectorPicker.jsx` |
| Reply-format contract on every relayed prompt, one sentence shared by both languages | `RELAY_FORMAT_INSTRUCTION` in `next_vector.py` and `nextVectorOptions.js` |
| Copy control on every fenced block | `frontend/src/components/RichText.jsx` |
| Conversation-list markers, menu marker on phones, per-browser record of seen results | `frontend/src/threadActivity.js`, `frontend/src/App.jsx` |

How a relay runs: a run completes; if its answer closes with a valid block the service appends a `next_vector` event; the picker lists that vector first and preselects it unless it is flagged; **Start selected vector** sends the previewed prompt as the next message in ask-first mode; the answer to that message closes with the next block. A first typed message carries no reply-format contract, so the first block appears after the first relayed choice.

The relay lives on the conversation run bus (`POST /conversations/{id}/messages`), which the browser interface uses. `mucaro conversations ask` and `POST /conversations/{id}/ask` answer through the older direct path and do not produce proposals.

## State

| State | Baseline `3012970` | This tree |
|---|---:|---:|
| IMPLEMENTED | 4 | 9 |
| PARTIAL | 6 | 4 |
| ABSENT | 4 | 1 |
| UNVERIFIED | 0 | 0 |
| **TOTAL** | **14** | **14** |

All 14 certification rows are `OPEN`. Nothing in this change was exercised with a live model.

Open rows, in dependency order:

1. **VRL-002** — the reply-format contract reaches a model only inside relayed prompts, and no local, remote or Anthropic model has been run against it. Closing it needs a live-model evaluation of block adherence and a decision on a conversation-level contract in the system prompt.
2. **VRL-010** — every network action in chat waits for an approval, so an unattended conversation parks at `AWAITING_APPROVAL`. Run-scoped permission leases, Autopilot budgets and the supervisor-queue primitives exist but are not connected to conversation runs.
3. **VRL-013** — no per-conversation ledger of frozen baselines, no link from a relayed run back to the proposal it started from, and the model sees a compacted summary plus at most 8 recent turns of 320 characters.
4. **VRL-012** — claimed gates are displayed, not compared with execution-entry gate evidence or receipts.
5. **VRL-009** — conversations cannot be pinned.

Not in this denominator, by contract: any relay that starts without the operator. Execution-entry case N14 forbids automatic next-vector authorization; a budgeted automatic relay would need its own authorization contract first.

## Verification commands

```bash
python -m pytest backend/tests/test_next_vector.py backend/tests/test_vector_relay.py backend/tests/test_video_vector_relay.py -q
(cd frontend && npm run build)             # runs the next-vector and thread-activity unit tests first
(cd qa/rendered && node relay_qa.mjs)      # rendered relay at desktop and iPhone width; needs the built interface
```

`relay_qa.mjs` starts the real service through `qa/rendered/relay_backend.py`, whose answerer is scripted to close each cited answer with a block. Its receipt (`relay_qa_receipt.json`) is rendered evidence for the interface with a fixture answerer. It is not hosted-CI evidence and not live-model evidence.

## Closure rule

`VIDEO_VECTOR_RELAY_V1` is **PASS** only when every one of the 14 required rows is:

`IMPLEMENTED + certification PASS`

for the same exact Mucaro source SHA, with positive and negative execution evidence, at least one live model, and zero unresolved residue inside this declared scope.

Until then the correct aggregate state is `VIDEO_VECTOR_RELAY_OPEN` or `VIDEO_VECTOR_RELAY_INVALID`, never an inferred percentage-based PASS.
