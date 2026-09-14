# Mucaro V1 Denominator

Canonical denominator version: `MUCARO_V1_DENOMINATOR/1.0`.

The machine-readable source of truth is `backend/app/program_denominator.py`.

## Frozen scope

- 100 requirements.
- 10 tracks.
- 10 requirements per track.
- Stable requirement IDs from `BC-01` through `GOV-10`.
- Membership and requirement text are immutable within denominator version 1.0.
- Requirement state may move forward or backward when evidence changes.
- Any membership change requires a denominator-version change and a new validator contract.

## Scoring

- `PASS = 1.0`
- `PROVISIONAL = 0.5`
- `OPEN | BLOCKED | FAIL | UNVERIFIED = 0.0`

Frozen historical baseline:

- raw completion: `61.5%`
- weighted completion: `62.6%`
- source baseline head: `9a06357afa9e34367776b1f46223a803ab23c958`

The baseline is historical evidence, not an assertion about every later Git head. A current-source audit may supersede the score without changing denominator membership.

## Track weights

| Track | Weight |
|---|---:|
| Backend capability contracts | 12% |
| CLI workflows | 10% |
| Provenance | 12% |
| Retrieval | 10% |
| GIS | 12% |
| OCR | 8% |
| Browsing | 10% |
| Coding | 10% |
| Packaging | 8% |
| Hosted governance | 8% |

## Certification invariant

`MUCARO_V1_CERTIFIED` requires all 100 rows to be `PASS` with zero `PROVISIONAL`, `OPEN`, `BLOCKED`, `FAIL`, or `UNVERIFIED` rows inside the V1 denominator.

Historical exact-head receipts do not certify later heads. GitHub-hosted startup failures do not by themselves prove local source-code failure.

## Regression gates

`validate_v1_denominator()` fails if:

- row count is not exactly 100;
- requirement IDs are duplicated, added, or removed;
- track membership changes;
- any track has other than 10 rows;
- an unknown state appears;
- track weights do not sum to 100%;
- a dependency names a requirement outside the denominator;
- the denominator version changes without the validator being updated.

State regression is intentionally permitted when evidence fails. Deterministic output or script success alone is not certification.
