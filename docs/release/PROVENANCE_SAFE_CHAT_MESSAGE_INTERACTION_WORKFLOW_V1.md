# Provenance-Safe Chat Message Interaction Workflow V1

Status: REUSABLE WORKFLOW — CLOSED SOURCE VECTOR  
Training source: PR #176 closed selection/copy/edit/revision/cross-engine path only  
Repository: `jotaele44/mucaro-ai`

## Purpose

Define the reusable implementation and verification contract for chat messages
that are selectable, copyable, display-editable, provenance-safe, and
cross-engine verified without rewriting canonical conversation evidence.

This workflow is promoted only from the closed behavior demonstrated by PR
#176. Later UX refinements, including completed-run hierarchy, workspace empty
states, Directory Access consolidation, and any future semantic branch/retry
feature, are not training evidence for this workflow unless independently
closed.

## Frozen evidence identity

| Evidence | Identity | State |
|---|---|---|
| Tested PR head | `5ea93586ee7b45dfb16e481a020b99e3b53f5a86` | PASS |
| Squash-merged main | `5643c3c9ff655f3d1db6b70929f98362e84cc132` | PASS |
| Tested tree | `d0bc29fe54b156adee43a3a5b79fdec298b471a2` | BINDING |
| Merged tree | `d0bc29fe54b156adee43a3a5b79fdec298b471a2` | BINDING |
| Rendered UI QA | workflow run `36826950048` | PASS |
| Chromium rendered job | `110254641905` — 93/93 | PASS |
| WebKit rendered job | `110254642220` — 93/93 | PASS |
| General CI | workflow run `36826950188` | PASS |
| macOS app | workflow run `36826950212` | PASS |
| macOS build-and-verify job | `110254642776` | PASS |
| Intermediate WebKit assertion | commit `d37b340be6da95e75635fa3dec821a878d29a18f`, job `110253451646`, 91/93 | SUPERSEDED TEST_ASSERTION |
| Same intermediate Chromium | job `110253451854`, 93/93 | HISTORICAL PASS |

The tested and merged Git tree identities are byte-for-byte equal. This binds the
final PR verification evidence to the squash-merged source tree even though the
commit SHA changed.

The superseded WebKit result is retained as negative test-design evidence. Its
two failed selection assertions logged non-empty selected text on desktop and
iPhone; the defect was that the assertion considered only the unprefixed
computed `userSelect` serialization. The replacement gate accepts either the
standards or WebKit CSS property while still requiring a real, non-empty DOM
selection. The final WebKit run then passed 93/93. The 91/93 result must never be
reported as application PASS evidence.

## Inputs

- A stable conversation/message identity.
- Canonical immutable message or run evidence.
- A durable conversation record capable of storing display revisions.
- A frontend surface that renders user and assistant messages.
- A rendered-test harness with Chromium and WebKit coverage.
- A packaged macOS lane when the application ships a native wrapper.

## Preconditions

1. Freeze the repository base commit and source-tree identity.
2. Enumerate every user and assistant message manifestation in the declared
   scope.
3. Identify one canonical user-facing action surface per message.
4. Keep canonical model/run evidence separate from mutable presentation state.
5. Define negative cases before exposing mutation controls.

## Invariants

1. **Selectable text:** rendered message text is natively selectable. Verification
   requires an actual non-empty selection, not CSS declaration inspection alone.
2. **Canonical action ownership:** every sent and received message exposes exactly
   one Copy/Edit action surface.
3. **Assistant speech:** Read aloud may appear for assistant messages only and
   remains part of the same canonical action surface.
4. **Immutable originals:** ordinary Edit never mutates the canonical message,
   model answer, run event, citation record, or historical prompt.
5. **Display-only revision:** edited text is stored as a revision-controlled
   `DISPLAY_OVERRIDE` with `original_preserved=true`.
6. **Optimistic concurrency:** each mutation supplies the expected revision and
   stale revisions fail closed.
7. **Stable ownership:** a message key must resolve to exactly one message owned
   by the target conversation.
8. **Fail-closed corruption:** malformed edit state or revision state must not be
   silently normalized into a successful edit.
9. **Clipboard resilience:** standards clipboard is preferred; a deterministic
   fallback is permitted when the browser exposes the API but rejects the write.
10. **Semantic separation:** changing historical conversational meaning,
    rerunning a prompt, branching, retrying, or regenerating is a different
    operation. Do not overload ordinary Edit with those semantics.

## Procedure

1. Freeze base commit, changed-file denominator, and expected message identities.
2. Add or confirm stable message keys for legacy turns and run-backed user and
   assistant messages.
3. Implement one reusable message-action component and route every message
   manifestation through it. Remove obsolete assistant-only or duplicated action
   rows.
4. Enable standards text selection plus WebKit-compatible selection/touch
   behavior without disabling selection on message descendants.
5. Persist edits in a separate display-revision map. Preserve canonical turns and
   run evidence unchanged.
6. Require `expected_revision` for edits and increment only after a valid
   compare-and-write.
7. Validate message-key syntax, target existence, run ownership, non-empty
   content, stored-state integrity, and authorization.
8. Render an explicit edited/provenance marker so the user can distinguish a
   display revision from the original.
9. Test copy success, edit/save/cancel, reload persistence, one action surface,
   assistant Read aloud, and actual DOM text selection at desktop and phone
   widths.
10. Run the same rendered gate in Chromium and WebKit. Treat engine-specific test
    serialization differences as test defects only when functional evidence
    independently demonstrates the behavior and the corrected assertion is
    rerun.
11. Run backend/API negative gates and general CI.
12. Run packaged macOS build/install/LaunchServices verification when applicable.
13. Bind the tested source tree to the merged tree. If tree identity differs,
    rerun the required evidence on the merged tree before claiming closure.
14. Freeze PASS and displaced/superseded evidence append-only.

## Required positive gates

- User message text can be selected.
- Assistant message text can be selected.
- User message has exactly one Copy and one Edit action.
- Assistant message has exactly one Copy and one Edit action.
- Assistant Read aloud remains available without a duplicate action row.
- Copy reports success.
- User display edit persists and shows provenance marker.
- Assistant display edit persists and shows provenance marker.
- Display revisions survive reload.
- Canonical originals remain byte-for-byte/logically unchanged.
- A second valid revision succeeds with the current expected revision.
- Chromium desktop/phone rendered denominator closes.
- WebKit desktop/phone rendered denominator closes.

## Required negative gates

- stale revision -> rejected;
- invalid message key -> rejected;
- missing target -> rejected;
- run owned by another conversation -> rejected;
- empty/whitespace edit -> rejected;
- malformed/corrupt edit state -> rejected;
- unauthorized mutation when operator auth is enabled -> rejected;
- duplicate Copy/Edit surface -> rendered failure;
- CSS-only “selectable” claim with empty DOM selection -> rendered failure;
- ordinary Edit mutating canonical prompt/answer/citations -> failure.

## Evidence states

Use only the following interpretations inside this workflow:

- **FACT** — directly observed source, commit, tree, API, or log fact.
- **COMPUTED** — deterministic derivation from frozen facts.
- **BINDING** — identity relationship that allows evidence to attach to an exact
  source manifestation.
- **PASS** — all declared positive and negative gates for that evidence item
  passed.
- **FAIL** — expected behavior was falsified.
- **OPEN** — required verification remains unexecuted.
- **BLOCKED** — required verification cannot currently execute and the reason is
  preserved.
- **SUPERSEDED TEST_ASSERTION** — an earlier test result displaced by a proven
  assertion defect plus a corrected rerun; retained for provenance but excluded
  from positive certification.
- **UNKNOWN** — evidence is insufficient; never promote by inference.

## Terminal rule

The workflow is CLOSED only when:

```text
DECLARED_MESSAGES
= SELECTABLE_MESSAGES
= SINGLE_ACTION_SURFACE_MESSAGES
= REVISION_SAFE_MESSAGES
= OWNERSHIP_VALIDATED_MESSAGES

NEGATIVE_GATES = ALL_PASS
CHROMIUM_RENDERED = PASS
WEBKIT_RENDERED = PASS
CANONICAL_ORIGINALS = IMMUTABLE
TESTED_TREE = MERGED_TREE
MATERIAL_UNEXPLAINED_RESIDUE = 0
```

Otherwise return FAIL, OPEN, BLOCKED, or UNKNOWN for the affected scope. Never
widen a bounded PASS into a universal product claim.

## Explicit exclusions

- Semantic “edit user prompt and branch from here”.
- Retry/regenerate/replay semantics.
- Mutation of canonical historical messages.
- Rewriting historical citations or tool receipts.
- Current or future UX refinements not independently closed.
- Any engine behavior inferred only from another browser engine.

## Reuse rule 🚩

Use this workflow as the reusable provenance-safe chat message interaction
procedure only because the source vector completed implementation, adversarial
negative gates, a failed-assertion repair cycle, cross-engine rerun, packaged
macOS verification, and exact tested-to-merged tree binding.

Do not train or extend the reusable procedure from provisional behavior.
A future branch/retry capability must establish its own state model, negative
gates, rendered receipts, and evidence binding before it can be incorporated.
