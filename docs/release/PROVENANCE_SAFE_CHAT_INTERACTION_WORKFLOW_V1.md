# Provenance-Safe Chat Interaction + Semantic Branch Workflow V1

Status: CANDIDATE until the branch implementing this workflow closes CI, Chromium/WebKit rendered QA, macOS packaging, and tested-tree == merged-tree identity.

## Purpose

Reuse the verified interaction contract for chat systems that allow message selection, copy, display editing, and semantic retry without corrupting canonical conversation provenance.

## Preserved baseline

The workflow starts from the certified message-interaction baseline:

- merged main commit `5643c3c9ff655f3d1db6b70929f98362e84cc132`
- tested/merged tree `d0bc29fe54b156adee43a3a5b79fdec298b471a2`
- selectable sent/received message text
- exactly one per-message Copy/Edit action surface
- assistant Read aloud on the same surface
- revision-controlled `DISPLAY_OVERRIDE` edits
- immutable canonical turns/runs
- stale-revision, invalid-key, ownership, empty-content, corruption, and auth gates
- Chromium 93/93 and WebKit 93/93 desktop+iPhone receipts
- macOS build/install/LaunchServices verification
- prior WebKit 91/93 classified `SUPERSEDED TEST_ASSERTION`

## Identity model

Display revision and semantic branch are not equivalent.

### Display edit

A display edit:

- changes only the rendered message manifestation;
- is stored under `message_edits`;
- increments an optimistic revision;
- records `scope=DISPLAY_OVERRIDE`;
- preserves the canonical turn/run and citations;
- does not change future semantic context by itself.

### Semantic branch and retry

A semantic branch:

- is available only from a completed canonical user run;
- creates a new conversation with a deterministic branch identity;
- snapshots only canonical turns before the selected source prompt;
- never imports DISPLAY_OVERRIDE content into inherited semantic context;
- submits the replacement prompt as a new run;
- records source conversation/run/turn lineage;
- preserves the source conversation, answer, citations, and edit manifestations;
- binds one idempotency key to one prompt/mode/options digest;
- fails closed on ownership, source-state, turn-resolution, corruption, or digest conflicts.

## UI ownership

- Message text owns selection.
- One message action row owns Copy and Edit.
- Completed user messages may add Branch & retry to that same object-level surface; Edit is never overloaded to perform semantic branching.
- Assistant messages retain Read aloud without a duplicate action location.
- Completed execution metadata lives in one collapsed run receipt below the answer.
- Active approvals, active questions, warnings, and errors remain foregrounded.
- Empty-state actions navigate to canonical controls; they do not duplicate Save, tool, or permission actions.
- Directory permission mutations live in one permission control surface. Granted-directory rows only select which grant to manage.

## Regression gates

Positive gates:

1. Native selection returns non-empty selected text in Chromium and WebKit.
2. Exactly one Copy and one Edit action exist per sent/received message.
3. Branch & retry exists once on completed user messages and nowhere on assistant messages.
4. Branch creation yields a new conversation and new run.
5. Source and branch conversations coexist after branch creation.
6. Branch lineage identifies source run and states original preservation.
7. Branch execution uses canonical pre-prompt context only.
8. Completed answers precede the collapsed run receipt.
9. Tool metadata remains accessible after expanding the receipt.
10. Memory/Provenance empty states point to canonical next surfaces.
11. Directory Access exposes one canonical permission mutation surface.
12. Desktop/iPhone layouts have no horizontal overflow and preserve touch sizing.

Negative gates:

1. Display edits never alter canonical source turns.
2. Display edits never enter inherited branch context.
3. A branch key reused with different prompt/mode/options fails.
4. A source run owned by another conversation is not branchable.
5. A non-completed run is not branchable.
6. Missing/ambiguous source canonical user turn fails.
7. Malformed branch context fails before model execution.
8. Unauthorized branch requests fail.
9. Permission UI never exposes both per-row revoke actions and a second canonical revoke action.
10. Hidden completed metadata must not prevent tool receipt access.

## Certification

Do not promote this workflow beyond CANDIDATE until:

- backend branch tests pass;
- complete release/security/test-lint-build CI passes;
- Chromium rendered QA passes desktop and iPhone;
- WebKit rendered QA passes desktop and iPhone;
- macOS app build/sign/smoke/install/LaunchServices verification passes;
- the tested tree SHA equals the merged main tree SHA;
- no material unresolved residue remains inside this declared workflow scope.

Only behavior that passes those gates may be reused as the V1 workflow.
