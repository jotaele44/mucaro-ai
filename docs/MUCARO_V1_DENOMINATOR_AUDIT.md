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
- current source-backed overlay: `64.5% raw / 66.2% weighted`

The overlay is an audit state, not a replacement denominator version.
