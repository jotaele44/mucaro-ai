# Mucaro V1 denominator audit events

The frozen `_architecture/mucaro_v1_denominator.json` remains historical baseline evidence for `MUCARO_V1_DENOMINATOR/1.0` and is not rewritten as implementation progresses.

Current-source promotions and regressions are recorded in `_architecture/mucaro_v1_denominator_events.json` and applied by `backend.app.denominator_audit`.

## Rules

1. Every event must identify one existing frozen requirement ID.
2. `from_state` must equal the state produced by the preceding denominator/event history.
3. Every event requires non-empty evidence and a reason.
4. Event IDs must be unique.
5. Events may promote or regress rows; evidence failure is allowed to move a row backward.
6. Events never alter denominator membership.
7. Historical exact-head evidence remains historical and does not certify later heads.
8. `PASS` is not awarded where the merged source explicitly preserves unresolved certification residue.

## First source-backed overlay

Audit head: `95996cfd1ae8baa0f8bec18548ed6812bee533cf`.

Events:

- `BC-07 OPEN → PASS`: capability policy engine merged in PR #6.
- `BC-08 OPEN → PASS`: isolated local-worker runtime merged in PR #9.
- `BC-09 OPEN → PROVISIONAL`: execution router and pinned container boundary merged in PRs #10/#11, while live image/engine execution remains uncertified.
- `PR-10 OPEN → PROVISIONAL`: runtime/toolchain identity receipts merged in PR #8, while universal binding across all advanced-capability derivations remains open.

Resulting overlay: `64.5% raw / 66.2% weighted`.

## Coding read-only provisional overlay

Audit head: `ff739b7c9a8ce7cb13fcbfeafe2ba7816c35a710` (PR #18 squash merge).

Additional events:

- `CODE-03 OPEN → PROVISIONAL`: approval-gated repository discovery, exact HEAD/branch state, bounded file reads, path boundaries and byte hashes are merged, but hosted regression execution is blocked before jobs start.
- `CODE-04 OPEN → PROVISIONAL`: staged/unstaged/untracked candidate sets, explicit dirty state and patch dirty-overlap preservation are merged, while executed regressions remain blocked by hosted Actions startup failure.
- `CODE-05 OPEN → PROVISIONAL`: bounded diff receipts and non-mutating patch SHA-256/path/preimage/applicability receipts are merged; executed tests have not closed certification.

Resulting overlay: `66.0% raw / 67.7% weighted`; CODING `30.0%`.

## Coding feature-plane provisional overlay

Audit head: `8c604dca3237b1b0b788617f839bf41f19df406c` (PR #31 squash merge).

Additional append-only events:

- `CODE-06 OPEN → PROVISIONAL`: controlled patch mutation revalidates exact HEAD, patch SHA-256, candidate set, preimages and dirty overlap with rollback; live isolated-runtime evidence remains open.
- `CODE-07 OPEN → PROVISIONAL`: PR #28 adds pinned-image, network-none, read-only-host-source, ephemeral-workspace command execution plus terminal `PASS | FAIL | BLOCKED` receipts; its Actions run startup-failed before creating jobs.
- `CODE-08 OPEN → PROVISIONAL`: review findings bind changed files to source SHA-256 and exact line evidence; complete executed regression evidence remains open.
- `CODE-09 OPEN → PROVISIONAL`: PR #29 adds exact-head work branches, approved-path commits, rollback, and deterministic PR request artifacts; authenticated remote push/PR submission remains intentionally unprovisioned.
- `CODE-10 OPEN → PROVISIONAL`: PR #31 adds the fail-closed coding certification ledger over the regression manifest, but the representative corpus has not executed on a frozen trusted runtime.

The GitHub Actions blocker remains `BLOCKED` infrastructure evidence, not a code `FAIL` and not PASS evidence. The frozen denominator file remains byte-for-byte outside this audit mutation path.

Resulting overlay arithmetic:

- historical baseline: `61.5% raw / 62.6% weighted`
- prior coding read-only overlay: `66.0% raw / 67.7% weighted`
- current source-backed overlay: `68.5% raw / 70.2% weighted`
- CODING track: `55.0%` with `PASS=1`, `PROVISIONAL=9`, `OPEN=0`
- applied append-only events: `12`

This is an evidence-state overlay, not a replacement denominator version, coding certification, or release certification claim.
