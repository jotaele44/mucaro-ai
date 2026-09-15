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

Resulting overlay:

- historical baseline: `61.5% raw / 62.6% weighted`
- source-backed overlay at that audit head: `64.5% raw / 66.2% weighted`

## Coding read-only provisional overlay

Audit head: `ff739b7c9a8ce7cb13fcbfeafe2ba7816c35a710` (PR #18 squash merge).

Additional events:

- `CODE-03 OPEN → PROVISIONAL`: approval-gated repository discovery, exact HEAD/branch state, bounded file reads, path boundaries and byte hashes are merged, but hosted regression execution is blocked before jobs start.
- `CODE-04 OPEN → PROVISIONAL`: staged/unstaged/untracked candidate sets, explicit dirty state and patch dirty-overlap preservation are merged, while executed regressions remain blocked by hosted Actions startup failure.
- `CODE-05 OPEN → PROVISIONAL`: bounded diff receipts and non-mutating patch SHA-256/path/preimage/applicability receipts are merged; patch mutation/application remains outside this scope and hosted tests have not executed.

The GitHub Actions blocker is preserved as `BLOCKED`, not converted to a code `FAIL` and not used as PASS evidence. The frozen denominator file remains unchanged.

Resulting overlay:

- historical baseline: `61.5% raw / 62.6% weighted`
- previous source-backed overlay: `64.5% raw / 66.2% weighted`
- current source-backed overlay: `66.0% raw / 67.7% weighted`
- CODING track: `30.0%` with `PASS=1`, `PROVISIONAL=4`, `OPEN=5`

The overlay is an audit state, not a replacement denominator version and not a release certification claim.
