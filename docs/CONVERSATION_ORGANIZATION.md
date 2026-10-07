# Durable Conversation Organization

## Goal and Scope

Advance the persistent assistant goal through durable pinning, reversible
archiving, reliable navigation, and failure-preserving updates. This is not a
claim of ChatGPT/Claude product equivalence or complete release certification.

The canonical backend owns saved conversations. Floot demo state is a separate
preview surface and cannot emit canonical execution receipts. A preview action
does not prove backend persistence.

## Contract

- Conversations have a stable ID, pinned and archived flags, and a nonnegative
  revision. Existing saved rows without these fields retain their identity and
  default to unpinned, active, revision zero.
- List ordering is active before archived, pinned before unpinned, most recent
  first, with ascending stable ID breaking timestamp ties.
- The existing conversation update route accepts title, pinned and archived
  independently or together. Boolean flags must be actual booleans; null,
  unknown fields and empty updates are rejected.
- Clients can supply an expected revision. A stale revision returns a conflict
  and the current revision without applying any of the requested changes.
- Exact no-op updates preserve the revision and timestamp. The CLI uses explicit
  pin/unpin and archive/restore commands, not retry-sensitive toggles.
- Every conversation read-modify-write uses a SQLite immediate transaction,
  including answer append and message display edits. Connections close on success
  and failure. Writers cannot recreate a deleted conversation.
- Turn replay requires an identical payload for a reused turn ID. Conflicting
  content fails and rolls back the entire append; identical repeats do not
  duplicate turns or update timestamps.
- Archive preserves the transcript and identity. Permanent deletion has no
  durable undo. Floot's in-memory preview undo is not equivalent to backend undo.

## Operator Behavior

The local UI separates Chats and Archived, sorts pinned chats first, reports
failed list refreshes without discarding the last good list, and offers retry.
Organization success is announced only after the backend acknowledges it.
A revision conflict refreshes the selected chat and requires the user to retry
the intended action against the updated state. Switching chats creates a fresh
view instance so an old request cannot replace the newly selected transcript.

The Floot demo binds displayed messages to the selected conversation ID. New
chats start empty; archive and preview undo preserve only that chat's transcript.
Demo counters reflect the displayed fixture messages, not invented history.

## Validation and Boundaries

Backend regression covers strict input validation, authentication, missing
targets, legacy rows, timestamp ties, same-title creation, restart persistence,
process concurrency, stale concurrent updates, append replay/conflict/rollback,
late writes after deletion, and CLI persistence. Rendered QA drives real UI
actions against a temporary real backend at desktop and iPhone widths, including
reload, archive/restore, conflict recovery, keyboard focus, accessibility and
overflow. The default rendered suite includes these organization checks.

Exact-head acceptance receipts belong in generated exports, not static PASS
claims here. A pre-integration test result does not certify a later merge.
Browser emulation does not prove physical iOS Safari or packaged WKWebView.
Mock-provider workflows do not establish live-provider answer quality. Hosted CI,
fresh-clone/package evidence and production Floot deployment remain separate gates.

## Preparation Ledger

The initial implementation snapshot was based on
ba1d5faee6f7caae57a6d75297d11dcc1a7f9dd5. Integration must use freshly inspected
main and retain this snapshot as prior evidence. Existing baseline tags must not
be changed. Unrelated application-bundle deletions in existing worktrees are
excluded from this patch and preserved.

| Requirement | Goal Binding | Pass Criterion | Validity Boundary |
| --- | --- | --- | --- |
| Target and authority | DEPENDENCY | Verified mucaro-ai remote and authenticated repo access; only scoped branch/PR writes | Recheck immediately before publication |
| Preserve existing work | HARDENING | Explicit file staging; clean new integration checkout; no reset or bundle restoration in existing worktrees | Scope excludes unrelated deletions |
| Durable organization | DIRECT | Real API/CLI persistence, strict negative controls and restart tests | Tested tree, interpreter and temporary database |
| Concurrency and replay | HARDENING | Multi-process preservation, stale rejection, atomic rollback and no resurrection | All conversation writers use the shared transaction |
| Operator recovery | DIRECT | Browser-confirmed success, conflict recovery and stale-list retry | Tested local UI/backend pair |
| Complete regression | VALIDATION | Full backend suite, JSON checks, frontend build/audit, rendered suite | Exact integrated tree; no inherited certification |
| External acceptance | VALIDATION | Hosted checks and main integration independently verified | PR head and merge head are distinct |

Each required row remains OPEN until its current receipt passes. Publication of
a reviewable branch does not imply release, merge, product satisfaction or
certification. Stop consequential actions on input drift, unexpected diff,
failed negative controls or ambiguous external results; preserve receipts and
repair only the invalidated phase. Recovery is a corrective commit or PR closure,
not destructive restoration of the user's work.
