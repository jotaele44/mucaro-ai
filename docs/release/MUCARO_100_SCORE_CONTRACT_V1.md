# MUCARO_100_V1 — Strict Score and Certification Contract

Status: BINDING GOVERNANCE PROJECTION  
Adopted by project authority: 2026-10-20  
Artifact execution: NOT_APPLICABLE

## Purpose

MUCARO_100_V1 defines the terminal meaning of "100/100" without creating a third denominator and without changing historical scoring.

It composes two already-canonical ledgers:

1. The frozen 100-row capability denominator at `_architecture/mucaro_v1_denominator.json`.
2. The release-engineering denominator at `_architecture/release/`, currently 139 rows: 138 applicable requirements plus one formally justified `NOT_APPLICABLE` row.

## Fixed-denominator rule

The capability denominator remains exactly 100 stable IDs. The release denominator remains governed by the existing frozen checkpoint and scope-delta rules. Rows may not be deleted, merged, weakened, reclassified as N/A, or hidden merely to improve a score.

A legitimate future denominator migration must be explicit and versioned. It must preserve and report:

- INTERSECTION
- A_ONLY
- B_ONLY
- UNION
- SYMMETRIC_DIFFERENCE
- adoption authority
- provenance
- regression impact
- arithmetic closure

The current MUCARO_100_V1 terminal release target is 138/138 applicable release requirements PASS.

## Strict certification score

Only `PASS` receives certification credit.

`PROVISIONAL`, `OPEN`, `BLOCKED`, `FAIL`, `UNVERIFIED`, `AUDIT_ONLY`, `NONCANONICAL`, `CANDIDATE_NOT_IDENTITY`, and `UNRESOLVED` receive zero strict credit.

Historical weighted or partial scores remain valid as historical engineering-progress metrics. They cannot satisfy MUCARO_100_V1.

## Terminal rule

MUCARO_100_V1 is certified only when all of the following are simultaneously true:

- capability denominator identity is exactly 100/100 frozen rows;
- all 100 capability rows are PASS;
- release denominator arithmetic closes at 139 total rows;
- exactly one release row is justified NOT_APPLICABLE;
- all 138 applicable release rows are PASS;
- the existing release terminal rule reports `MUCARO_AI_RELEASE_GRADE_CERTIFIED`;
- no required release FAIL, OPEN, PROVISIONAL, or BLOCKED residue remains;
- no active release blocker remains;
- no material contradiction remains unresolved;
- evidence is bound to the exact source and artifact identities required by the underlying ledgers.

## Non-equivalence invariants

GUI PASS is not backend PASS. Backend PASS is not product PASS. Desktop PASS is not mobile PASS. Chromium PASS is not WebKit PASS. Emulation PASS is not physical-device PASS. Build PASS is not package PASS. Package PASS is not install PASS. Install PASS is not upgrade PASS. Upgrade PASS is not rollback PASS. Local PASS is not hosted PASS. PR-head PASS is not main-head PASS. Source PASS is not distributed-artifact PASS. Historical PASS is not current-head PASS. Script success is not certification.

## Executable boundary

`python3 scripts/certify_mucaro_100.py --format text` reports the current strict projection without changing repository state.

`python3 scripts/certify_mucaro_100.py --strict --format text` exits nonzero until the complete terminal rule is satisfied.

The executable implementation must use the existing denominator and release-control modules. It must not maintain a parallel mutable requirement list.
